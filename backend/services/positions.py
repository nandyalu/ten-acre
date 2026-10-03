"""FIFO position accounting derived from an append-only buy/sell transaction log.

The transaction log (the SQLite ``transaction`` table, via backend/database/db.py) is
the only persisted state — open lots, cost basis, and realized P&L are all recomputed
from it here on every read, so there's never a separately-mutated position to get out
of sync.
"""
import datetime
from dataclasses import dataclass

import yfinance as yf
from tradingagents.dataflows.vendors.yahoo.common import yf_retry

from backend.database import db

_EPSILON = 1e-9


# Marks a transaction whose date is the day it was imported, not the day the
# shares were actually bought. Anything
# date-sensitive has to leave these lots out rather than trust the date.
ESTIMATED_DATE_NOTE = "date unknown"


@dataclass
class Lot:
    date: str
    price: float
    quantity: float
    # True when ``date`` is the import date rather than the purchase date. A
    # benchmark comparison anchored on an import date measures nothing: the
    # benchmark gets a few days to move while the position is credited with
    # months of gains, which reads as enormous alpha.
    date_estimated: bool = False


@dataclass
class Position:
    open_lots: list[Lot]
    quantity: float
    avg_cost: float
    realized_pnl: float


def compute_position(transactions: list[dict]) -> Position:
    open_lots: list[Lot] = []
    realized_pnl = 0.0

    for tx in transactions:
        if tx["side"] == "buy":
            open_lots.append(
                Lot(
                    date=tx["date"],
                    price=tx["price"],
                    quantity=tx["quantity"],
                    date_estimated=ESTIMATED_DATE_NOTE in (tx.get("note") or ""),
                )
            )
        else:
            remaining = tx["quantity"]
            while remaining > _EPSILON and open_lots:
                lot = open_lots[0]
                matched = min(lot.quantity, remaining)
                realized_pnl += (tx["price"] - lot.price) * matched
                lot.quantity -= matched
                remaining -= matched
                if lot.quantity <= _EPSILON:
                    open_lots.pop(0)

    quantity = sum(lot.quantity for lot in open_lots)
    avg_cost = sum(lot.price * lot.quantity for lot in open_lots) / quantity if quantity > _EPSILON else 0.0
    return Position(open_lots=open_lots, quantity=quantity, avg_cost=avg_cost, realized_pnl=realized_pnl)


def get_shown_price(ticker: str) -> float | None:
    """The last price this app fetched, without fetching another.

    **For pages, never for decisions.** ``get_current_price`` asks the vendor,
    and Webull's market-data endpoint is paced at three seconds a call since
    2026-09-09 — it refused 524 calls in one day without it. Across ten
    tracked tickers that is half a minute, serialised, and the home page was
    spending it on every cold load: ``/api/agent`` measured 32 seconds.

    Nothing here is worth that. The watchdog already fetches every tracked
    ticker every fifteen minutes and writes each one through to this cache, so
    a page reading it is showing a price minutes old rather than seconds old —
    measured at 8 to 10 minutes across the whole watchlist. A figure on a page
    that a reader looks at for two seconds does not need to be fresher than
    the page itself, which is a static export up to half an hour old.

    **The agent keeps the live path.** It trades on these numbers, and a stale
    one becomes an order at a price that no longer exists. Every caller in
    ``agent.py``, and the traded price in ``analysis.py``, still ask the
    vendor. This is only for what the site draws.
    """
    cached = db.get_cached_price(ticker)
    return cached.price if cached is not None else None


def get_current_price(ticker: str) -> float | None:
    """Best-effort quote lookup — callers must handle None (e.g. rate-limited).
    Prefers Webull's real-time snapshot when configured (backend/services/quotes.py) and
    falls back to yfinance's delayed close. Every successful fetch writes through
    to the ticker price cache (backend/database/db.py's TickerPrice table), which
    is what the dashboard's list/detail routes read from instead of fetching live."""
    from backend.services import listings, publish  # lazy: keeps positions import-light
    from backend.services.quotes import get_realtime_price

    # A ticker that stopped trading has no current price to fetch. Asking
    # Webull and yfinance anyway returns the delisted shell's last tick —
    # $0.000001 for AILEQ — which is worse than admitting we don't know, since
    # it gets cached and shown as a real quote.
    if listings.is_inactive(ticker):
        return None

    # The published copy has no Webull key, so its own fetch could only ever
    # reach yfinance's stale daily close — worse than the live price the
    # private copy already keeps in this cache. Read the cache and stop
    # there: writing a worse price through from here would leave it for the
    # private copy's own dashboard to show next, which defeats the point of
    # calling this copy read-only.
    if publish.is_public():
        cached = db.get_cached_price(ticker)
        return cached.price if cached is not None else None

    price = get_realtime_price(ticker)
    if price is not None:
        db.set_cached_price(ticker, price, source="webull")
        return price
    try:
        # period="1d" during an open session returns only the partial bar, so
        # without the drop this fetches a NaN and caches it as the price.
        history = yf_retry(lambda: yf.Ticker(ticker).history(period="5d"))
        history = drop_incomplete_bars(history)
        if history.empty:
            return None
        price = float(history["Close"].iloc[-1])
        db.set_cached_price(ticker, price, source="yfinance")
        return price
    except Exception:
        return None


def drop_incomplete_bars(history, columns: tuple[str, ...] = ("Close",)):
    """Drop rows missing any of ``columns``.

    While a US session is open, yfinance appends a row for the day in progress
    that carries a volume but NaN prices. Nothing here checks for that, and
    every reader takes ``.iloc[-1]``, so the NaN propagates silently: a NaN
    price is cached as the current price, a NaN close is written to a graded
    signal, and NaN compares false against every threshold — so a Buy that
    actually won is recorded as a loss. It reaches JSON as ``null`` rather than
    as an error, which is why it went unnoticed.

    Call this on any yfinance frame before reading values off it. ``.max()``
    and ``.min()`` already skip NaN, so only the positional reads were wrong.
    """
    if history is None or history.empty:
        return history
    present = [column for column in columns if column in history.columns]
    return history.dropna(subset=present) if present else history


@dataclass
class PriceWindow:
    """Daily-bar summary of ``start`` → today, for grading a matured signal."""

    first_close: float
    last_close: float
    high: float
    low: float


def get_price_window(ticker: str, start: datetime.date) -> PriceWindow | None:
    """Best-effort like get_current_price — None when no bars are available. If
    ``start`` is a non-trading day the window begins at the next session.

    Served from the daily bar cache (backend/services/bars.py). Today is
    included because a signal maturing today should be graded against the
    latest price available, not against yesterday's close."""
    from backend.services import bars  # lazy: bars imports from this module

    window = bars.get_bars(ticker, start, include_today=True)
    if not window:
        return None
    return PriceWindow(
        first_close=window[0].close,
        last_close=window[-1].close,
        high=max(bar.high for bar in window),
        low=min(bar.low for bar in window),
    )


@dataclass
class OhlcBar:
    date: str  # ISO date
    open: float
    high: float
    low: float
    close: float
    volume: float


def get_price_history(ticker: str, days: int = 90) -> list[OhlcBar]:
    """Daily OHLCV bars for the last ``days`` calendar days — chart data.

    Unlike ``get_price_window``, which collapses the same yfinance frame down
    to 4 summary scalars for signal grading, this keeps every row. Best-effort
    like the other price lookups here — empty list on a failed/empty fetch.

    Served from the daily bar cache (backend/services/bars.py), with today's
    partial bar appended live so the chart's last candle is the current
    session rather than yesterday."""
    from backend.services import bars  # lazy: bars imports from this module

    start = datetime.date.today() - datetime.timedelta(days=days)
    return bars.get_bars(ticker, start, include_today=True)


def signed_dollars(amount: float) -> str:
    """+$100.00 / -$50.25 — "$+100.00" (plain ``:+`` formatting) reads wrong."""
    sign = "-" if amount < 0 else "+"
    return f"{sign}${abs(amount):,.2f}"


def describe_position(ticker: str, position: Position) -> list[str]:
    """Shared line-formatting for both the /positions command and analysis embeds."""
    current_price = get_current_price(ticker)
    lines = [
        f"Quantity: {position.quantity:g}",
        f"Avg cost basis: ${position.avg_cost:,.2f}",
    ]
    if current_price is not None:
        unrealized = (current_price - position.avg_cost) * position.quantity
        unrealized_pct = (current_price / position.avg_cost - 1) * 100 if position.avg_cost else 0.0
        lines.append(f"Current price: ${current_price:,.2f}")
        lines.append(f"Unrealized P&L: ${unrealized:,.2f} ({unrealized_pct:+.1f}%)")
    else:
        lines.append("Current price: unavailable")
    if abs(position.realized_pnl) > _EPSILON:
        lines.append(f"Realized P&L to date: ${position.realized_pnl:,.2f}")
    return lines
