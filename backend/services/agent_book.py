"""The agent's budget, cash, and holdings — derived, never read from the broker.

The simulated account is funded with $1,000,000. The agent is given a few
hundred. So every number the agent is allowed to reason about has to come from
this module, computed over the orders it actually placed, and never from the
account's buying power — which would tell it that it can spend a thousand times
its budget.

Cash is ``budget − filled buys + filled sells``. Only filled rows count: a
market order placed after the close sits pending until the open, and counting
it early would let the agent spend the same dollar twice in one evening.

Holdings reuse positions.compute_position, the same FIFO the real book uses, so
realized P/L and average cost mean exactly what they mean everywhere else.
"""
import datetime
import logging
import os
from dataclasses import dataclass, field

from backend.database import db
from backend.services import market_calendar, research
from backend.services.positions import compute_position, get_shown_price

log = logging.getLogger("trading-experiment.agent_book")

_BUDGET_SETTING_KEY = "agent_budget"
# The experiment's stake. $10,000 is what the agent was given on 2026-09-01,
# and it is the number every return on the dashboard is measured against.
DEFAULT_BUDGET = 10_000.0

# Cash below this can't buy a share of anything worth owning, and floating
# point makes exact-zero comparisons unreliable after a few round trips.
_CASH_EPSILON = 0.01
_QUANTITY_EPSILON = 1e-6


@dataclass
class Holding:
    ticker: str
    quantity: float
    avg_cost: float
    price: float | None = None  # current market price, None when unavailable
    # When the oldest still-open lot was bought. The trade horizon is one to
    # two weeks, so a position's age is the difference between holding it and
    # forgetting about it — nothing else in the book says a thesis has expired.
    opened: datetime.date | None = None

    @property
    def market_value(self) -> float | None:
        return None if self.price is None else self.quantity * self.price

    @property
    def cost_basis(self) -> float:
        return self.quantity * self.avg_cost

    @property
    def unrealized_pnl(self) -> float | None:
        value = self.market_value
        return None if value is None else value - self.cost_basis

    def held_days(self, today: datetime.date | None = None) -> int | None:
        if self.opened is None:
            return None
        return ((today or datetime.date.today()) - self.opened).days


@dataclass
class Book:
    budget: float
    cash: float
    realized_pnl: float
    holdings: list[Holding] = field(default_factory=list)
    # What has been spent having tickers analysed. Already deducted from cash
    # and from realized_pnl; carried separately so a page can show how much of
    # a loss was research rather than trading, which are different problems.
    research_spent: float = 0.0
    # Shares already promised to a pending, unfilled limit sell — a GTC one
    # can rest for days. Kept apart from `holdings` rather than subtracted
    # from it: the position is still real and still owned, and Holding.quantity
    # feeds market value and the holdings table, so understating it there
    # would misstate equity, not just what remains sellable. See `validate`.
    reserved_shares: dict[str, float] = field(default_factory=dict)

    @property
    def invested(self) -> float:
        return sum(h.cost_basis for h in self.holdings)

    @property
    def market_value(self) -> float:
        """Priced holdings only. An unpriced holding contributes its cost
        basis rather than zero — treating it as worthless would understate
        equity and could make the agent panic-sell what it can still see."""
        return sum(
            h.market_value if h.market_value is not None else h.cost_basis for h in self.holdings
        )

    @property
    def equity(self) -> float:
        return self.cash + self.market_value

    @property
    def return_pct(self) -> float:
        return (self.equity / self.budget - 1) * 100 if self.budget else 0.0

    def weight_pct(self, holding: Holding) -> float | None:
        """What share of the whole account one position is.

        Shown to the model rather than capped, because allocation is its call —
        but it put 98% of the budget into one stock without ever being told
        that was what it was doing.
        """
        value = holding.market_value if holding.market_value is not None else holding.cost_basis
        return (value / self.equity * 100) if self.equity else None


def _default_budget() -> float:
    """What the app starts with before anyone sets a budget.

    ``AGENT_BUDGET`` overrides it, because a fresh database has no setting yet
    and a new container should come up on the right number rather than be
    corrected by hand on its first run — the kind of step that gets forgotten
    once and then reported as a bug. Same pattern as the model: the env var is
    only the default for an unset setting, and the settings page still wins.
    """
    raw = (os.environ.get("AGENT_BUDGET") or "").strip()
    if not raw:
        return DEFAULT_BUDGET
    try:
        value = float(raw)
    except ValueError:
        log.warning("Ignoring unparseable AGENT_BUDGET %r", raw)
        return DEFAULT_BUDGET
    if value <= 0:
        log.warning("Ignoring non-positive AGENT_BUDGET %r", raw)
        return DEFAULT_BUDGET
    return value


def get_budget() -> float:
    stored = db.get_setting(_BUDGET_SETTING_KEY)
    try:
        return float(stored) if stored else _default_budget()
    except (TypeError, ValueError):
        log.warning("Ignoring unparseable agent_budget %r", stored)
        return _default_budget()


def set_budget(budget: float) -> None:
    if budget <= 0:
        raise ValueError("Budget must be positive.")
    db.set_setting(_BUDGET_SETTING_KEY, str(float(budget)))


def _as_transactions(trades) -> dict[str, list[dict]]:
    """Filled trades grouped by ticker, in the shape compute_position wants."""
    by_ticker: dict[str, list[dict]] = {}
    for trade in trades:
        if trade.status != "filled" or trade.price is None:
            continue
        by_ticker.setdefault(trade.ticker, []).append(
            {
                "side": trade.side,
                "date": (trade.filled_at or trade.placed_at).date(),
                "price": trade.price,
                "quantity": trade.quantity,
                "note": None,
            }
        )
    return by_ticker


def build_book(price_lookup=None) -> Book:
    """The agent's current book. ``price_lookup`` is a ticker -> price|None
    callable; omitted, holdings come back unpriced, which every consumer
    already handles (an unpriced holding is counted at cost)."""
    budget = get_budget()
    trades = db.get_agent_trades()
    spent = sum(t.quantity * t.price for t in trades if t.status == "filled" and t.price and t.side == "buy")
    received = sum(t.quantity * t.price for t in trades if t.status == "filled" and t.price and t.side == "sell")

    holdings: list[Holding] = []
    realized = 0.0
    for ticker, transactions in sorted(_as_transactions(trades).items()):
        position = compute_position(transactions)
        realized += position.realized_pnl
        if position.quantity > _QUANTITY_EPSILON:
            # The oldest open lot: a position topped up last week is still a
            # position opened a month ago, and the thesis is that old too.
            opened = min(
                (lot.date for lot in position.open_lots if lot.date), default=None
            )
            if isinstance(opened, str):
                opened = datetime.date.fromisoformat(opened)
            holdings.append(
                Holding(
                    ticker=ticker,
                    quantity=position.quantity,
                    avg_cost=position.avg_cost,
                    price=price_lookup(ticker) if price_lookup else None,
                    opened=opened,
                )
            )

    # Research comes out of the same pocket as the trades. Charged at zero it
    # is a no-op, which is how the live deployment behaves; the experiment
    # deployment sets a price and the agent has to choose what to look at.
    researched = research.total_spent()
    # A pending limit buy has moved no money yet — but unlike a market or
    # bracket entry, which fills within the same pass or by the next open, a
    # GTC limit buy can sit unfilled for days. Without reserving its cost, a
    # later pass would see the same dollars as free and could commit them
    # twice. A pending *market* order never carries a limit_price, so this
    # only ever touches the new limit-order case.
    reserved = sum(
        t.quantity * t.limit_price
        for t in trades
        if t.status == "pending" and t.side == "buy" and t.limit_price
    )
    # The share-side mirror: a pending, unfilled limit sell has not reduced
    # the position yet, but the shares are spoken for. Without this, a second
    # pass — after the one that placed the limit sell, before it fills —
    # would see the same shares as free to sell again.
    reserved_shares: dict[str, float] = {}
    for t in trades:
        if t.status == "pending" and t.side == "sell" and not t.is_stop and t.limit_price:
            reserved_shares[t.ticker] = reserved_shares.get(t.ticker, 0.0) + t.quantity
    return Book(
        budget=budget,
        cash=budget - spent + received - researched - reserved,
        realized_pnl=realized - researched,
        holdings=holdings,
        research_spent=researched,
        reserved_shares=reserved_shares,
    )


@dataclass
class Rejection:
    ticker: str
    side: str
    quantity: float
    why: str


def entry_limit_price(price: float) -> float:
    """What a buy at ``price`` will actually be priced at when it is sent.

    The broker refuses a MARKET master on a bracket, so a buy goes out as a
    marketable limit a little through the offer. That limit, not the quote, is
    what the account is asked to pay.

    Imported lazily to keep ``agent_book`` free of a broker dependency at
    module scope; the fallback keeps this usable if the broker is unavailable,
    and erring on the high side is the safe direction for an affordability
    check.
    """
    try:
        from backend.services.sandbox_broker import ENTRY_LIMIT_BUFFER_PCT as buffer
    except Exception:  # noqa: BLE001 — a missing broker must not block a check
        buffer = 0.5
    return round(price * (1 + buffer / 100), 2)


def buying_power_margin() -> float:
    """The multiplier for cash an order must clear, on top of its own cost.

    The sandbox account refuses a buy unless its buying power sits this much
    above the order's cost — a broker-side cushion, separate from and
    additive to the slippage buffer in ``entry_limit_price``. A live refusal
    on 2026-09-04 exposed it; this check did not include it until 2026-09-17,
    so an order that passed here could still be refused at the broker, and
    the agent — never told the real number — would recompute from the raw
    price and resend the same order. See the 2026-09-17 JOURNEY.md entry.

    Imported lazily, like ``entry_limit_price``, and for the same reason.
    """
    try:
        from backend.services.sandbox_broker import BUYING_POWER_MARGIN_PCT as margin
    except Exception:  # noqa: BLE001 — a missing broker must not block a check
        margin = 2.0
    return 1 + margin / 100


def validate(order: dict, book: Book, price: float | None) -> Rejection | None:
    """Whether an order the model asked for is *possible*. Not whether it is
    wise — allocation is the model's call (see backend/services/agent.py); this
    only refuses orders that cannot be executed as stated.

    Returns None when the order is fine, or the reason it isn't.
    """
    ticker = str(order.get("ticker", "")).upper().strip()
    side = str(order.get("side", "")).lower().strip()
    try:
        quantity = float(order.get("quantity", 0))
    except (TypeError, ValueError):
        quantity = 0.0

    def no(why: str) -> Rejection:
        return Rejection(ticker=ticker, side=side, quantity=quantity, why=why)

    if not ticker:
        return no("no ticker given")
    if side not in ("buy", "sell"):
        return no(f"side must be buy or sell, got {side!r}")
    if quantity <= 0:
        return no(f"quantity must be positive, got {quantity}")
    if quantity != int(quantity):
        return no(f"whole shares only, got {quantity}")

    order_type = str(order.get("order_type") or "market").lower().strip()
    if order_type not in ("market", "limit"):
        return no(f"order_type must be market or limit, got {order_type!r}")
    limit_price = order.get("limit_price")
    if order_type == "limit":
        try:
            limit_price = float(limit_price)
        except (TypeError, ValueError):
            limit_price = None
        if limit_price is None or limit_price <= 0:
            return no("a limit order needs a positive limit_price")
    time_in_force = str(order.get("time_in_force") or "day").lower().strip()
    if time_in_force not in ("day", "gtc"):
        return no(f"time_in_force must be day or gtc, got {time_in_force!r}")

    if side == "sell":
        held = next((h.quantity for h in book.holdings if h.ticker == ticker), 0.0)
        # Shares already promised to a pending limit sell aren't free to sell
        # again — a GTC one can rest for days across passes that each rebuild
        # `holdings` from filled trades alone, which still shows them as held.
        reserved = book.reserved_shares.get(ticker, 0.0)
        available = held - reserved
        if quantity > available + _QUANTITY_EPSILON:
            if reserved > _QUANTITY_EPSILON:
                return no(
                    f"holds {held:g} shares but {reserved:g} are already committed to a "
                    f"pending sell, leaving {max(available, 0.0):g} — cancel it first, or "
                    f"sell fewer"
                )
            return no(f"holds {held:g} shares, cannot sell {quantity:g} (no shorting)")
        return None

    # **A limit buy is screened at its own stated price, not the quote.** That
    # price is the true worst case — a limit buy never fills above it — where
    # a market buy's worst case has to be estimated from the quote instead.
    # The broker's buying-power cushion (see `buying_power_margin`) still
    # applies: it is a balance rule, not a slippage rule, so a stated limit
    # price does not excuse an order from it.
    if order_type == "limit":
        cost = quantity * limit_price * buying_power_margin()
        if cost > book.cash + _CASH_EPSILON:
            return no(f"costs ${cost:,.2f} at the limit price plus the broker's "
                      f"{(buying_power_margin() - 1) * 100:g}% buying-power cushion, but only "
                      f"${book.cash:,.2f} is uninvested")
        return None

    if price is None:
        return no("no price available, so the cost can't be checked against cash")
    # **Screened at the price the order will actually go out at, not the quote.**
    # A buy leaves as a marketable limit set ENTRY_LIMIT_BUFFER_PCT through the
    # offer, so checking the raw quote approves an order the account cannot pay
    # for. On 2026-08-28 the agent bought 260 SMCI: 260 x $38.46 is $9,999.60
    # against $9,999.70 of cash and passed, then filled at $38.49 for $10,007.40
    # and left the book at minus $7.70.
    #
    # This refuses the order rather than shrinking it, like every other check
    # here. Resizing would quietly turn the model's decision into a different
    # one, and the record would then describe a strategy nobody chose.
    #
    # **Also screened against the broker's buying-power cushion, not only the
    # entry limit.** Without it, an order could clear this check and still be
    # refused at the broker for insufficient buying power — and the agent,
    # never told the real number, would work out from the raw price that it
    # could afford the same quantity and resend the identical order. Seen
    # live on 2026-09-17: a refused 12-share AAPL buy, recomputed by the
    # model from the refusal alone, resent unchanged.
    cost = quantity * entry_limit_price(price) * buying_power_margin()
    if cost > book.cash + _CASH_EPSILON:
        return no(f"costs ${cost:,.2f} at the entry limit plus the broker's "
                  f"{(buying_power_margin() - 1) * 100:g}% buying-power cushion, but only "
                  f"${book.cash:,.2f} is uninvested")
    return None


@dataclass
class TradeRow:
    """One lot's life: bought, and sold or still held.

    Open and closed rows come from the same FIFO walk deliberately. Computing
    them separately is how a "still holding" table and a realized-P/L table end
    up disagreeing about the same shares.
    """

    ticker: str
    quantity: float
    entry: float
    entry_at: datetime.datetime
    exit: float | None = None
    exit_at: datetime.datetime | None = None
    signal_decision: str | None = None  # what the analyst said when it bought
    signal_id: int | None = None  # the signal the lot was opened on

    @property
    def is_open(self) -> bool:
        return self.exit is None

    @property
    def pnl(self) -> float | None:
        """None while open. An unrealized number here would be mistaken for a
        booked one — the holdings table above already shows unrealized."""
        return None if self.is_open else (self.exit - self.entry) * self.quantity

    @property
    def return_pct(self) -> float | None:
        if self.is_open or not self.entry:
            return None
        return (self.exit / self.entry - 1) * 100

    @property
    def held_days(self) -> int:
        """To the exit, or to today while it is still held."""
        end = self.exit_at.date() if self.exit_at else datetime.date.today()
        return (end - self.entry_at.date()).days

    @property
    def won(self) -> bool:
        return (self.pnl or 0) > 0


# The old name, kept because the prompt and its tests read closed rows.
ClosedTrade = TradeRow


def trade_history(trades=None, decisions: dict[int, str] | None = None) -> list[TradeRow]:
    """Every lot the agent has opened, closed ones and still-held ones, oldest
    first.

    Matching is FIFO to agree with compute_position, so the realized P/L summed
    here equals the realized P/L on the book rather than drifting from it.
    """
    rows = trades if trades is not None else db.get_agent_trades()
    decisions = decisions or {}
    # ticker -> [[remaining_qty, price, filled_at, decision, signal_id], ...]
    open_lots: dict[str, list[list]] = {}
    history: list[TradeRow] = []

    for trade in rows:
        if trade.status != "filled" or trade.price is None:
            continue
        # A resting stop or take-profit that filled is a real exit and must be
        # matched like any other sell.
        when = trade.filled_at or trade.placed_at
        if trade.side == "buy":
            open_lots.setdefault(trade.ticker, []).append(
                [trade.quantity, trade.price, when, decisions.get(trade.signal_id), trade.signal_id]
            )
            continue
        remaining = trade.quantity
        lots = open_lots.get(trade.ticker, [])
        while remaining > _QUANTITY_EPSILON and lots:
            lot = lots[0]
            matched = min(lot[0], remaining)
            history.append(
                TradeRow(
                    ticker=trade.ticker,
                    quantity=matched,
                    entry=lot[1],
                    entry_at=lot[2],
                    exit=trade.price,
                    exit_at=when,
                    signal_decision=lot[3],
                    signal_id=lot[4],
                )
            )
            lot[0] -= matched
            remaining -= matched
            if lot[0] <= _QUANTITY_EPSILON:
                lots.pop(0)

    for ticker, lots in open_lots.items():
        for quantity, price, when, decision, signal_id in lots:
            if quantity > _QUANTITY_EPSILON:
                history.append(
                    TradeRow(
                        ticker=ticker,
                        quantity=quantity,
                        entry=price,
                        entry_at=when,
                        signal_decision=decision,
                        signal_id=signal_id,
                    )
                )

    history.sort(key=lambda r: r.entry_at)
    return history


def closed_trades(trades=None, decisions: dict[int, str] | None = None) -> list[TradeRow]:
    """Completed round trips only — what the agent is shown about its own
    record. An open lot has no outcome to learn from yet."""
    return [row for row in trade_history(trades, decisions) if not row.is_open]


def trades_for_signal(signal_id: int) -> list[TradeRow]:
    """What the agent actually did with one signal's call.

    The signal's own grade answers whether the analysis was right over its
    horizon; this answers what the exit rules made of it. They are deliberately
    separate — resolving a signal the moment a stop fires would end every loser
    early and let every winner run to full term, which measures stop placement
    rather than the prediction. Seeing both is what makes a PASS beside a
    losing trade legible as "the call was right, the stop was too tight".

    Only lots opened on this signal count. A sell carries no signal_id, so the
    exit that closed the lot is found by the FIFO match rather than by id.
    """
    return [row for row in trade_history() if row.signal_id == signal_id]


@dataclass
class EquityPoint:
    """One day on the agent's equity curve."""

    date: datetime.date
    equity: float
    cash: float
    market_value: float


def _session_has_opened(today: datetime.date, now: datetime.datetime | None = None) -> bool:
    """True when ``today`` is a trading day and its session opened at or before ``now``."""
    from backend.services.watchdog import _MARKET_OPEN, US_MARKET_TZ

    now = now or datetime.datetime.now(US_MARKET_TZ)
    session_open = datetime.datetime.combine(today, _MARKET_OPEN, tzinfo=US_MARKET_TZ)
    return market_calendar.is_trading_day(today) and now >= session_open


def equity_curve(trades=None, today: datetime.date | None = None) -> list[EquityPoint]:
    """The agent's equity, one point per trading day since its first fill.

    Rebuilt from the ledger and the bar cache rather than recorded daily. A
    daily snapshot — which is how the paper book does it — can only ever draw
    the days since it was switched on, and the agent has been trading longer
    than this curve has existed. Both sources are already here: the filled
    trades say what was held on any past day, and a completed session's close
    never changes, which is the whole premise of the bar cache.

    It also cannot drift. A snapshot table is a second copy of a number the
    ledger already implies, and the two disagreeing would leave nothing to say
    which was right.

    The calendar comes from SPY, not from the held tickers, so the line does
    not develop gaps on a day a thinly-traded holding printed no bar.
    """
    from backend.services import research, bars

    today = today or datetime.date.today()
    filled = [
        t for t in (trades if trades is not None else db.get_agent_trades())
        if t.status == "filled" and t.price is not None
    ]
    if not filled:
        return []

    def day_of(trade) -> datetime.date:
        return (trade.filled_at or trade.placed_at).date()

    start = min(day_of(t) for t in filled)
    # Completed sessions only. Do not pass include_today=True here: it sends
    # one live vendor request for each ticker, at three seconds a call. On
    # 2026-09-14 that made this page take 12 seconds. Today's point uses the
    # price cache below.
    sessions = [
        datetime.date.fromisoformat(bar.date)
        for bar in bars.get_bars("SPY", start, today=today)
    ]
    if _session_has_opened(today) and (not sessions or sessions[-1] < today):
        sessions.append(today)
    if not sessions:
        return []

    closes: dict[str, dict[datetime.date, float]] = {}
    for ticker in {t.ticker for t in filled}:
        closes[ticker] = {
            datetime.date.fromisoformat(bar.date): bar.close
            for bar in bars.get_bars(ticker, start, today=today)
        }

    budget = get_budget()
    # Research is spent on the day it was charged, so the curve steps down
    # then rather than being smeared across the whole span.
    research_by_day = research.spent_by_day()
    by_day: dict[datetime.date, list] = {}
    for trade in filled:
        by_day.setdefault(day_of(trade), []).append(trade)

    curve: list[EquityPoint] = []
    cash = budget
    held: dict[str, float] = {}
    # Carried so a holding is never valued at nothing on a day its own ticker
    # printed no bar — a missing quote is not a position worth zero.
    last_close: dict[str, float] = {}
    for session in sessions:
        cash -= research_by_day.get(session, 0.0)
        for trade in by_day.get(session, []):
            amount = trade.quantity * trade.price
            if trade.side == "buy":
                cash -= amount
                held[trade.ticker] = held.get(trade.ticker, 0.0) + trade.quantity
            else:
                cash += amount
                held[trade.ticker] = held.get(trade.ticker, 0.0) - trade.quantity

        market_value = 0.0
        for ticker, quantity in held.items():
            if quantity <= _QUANTITY_EPSILON:
                continue
            close = closes.get(ticker, {}).get(session)
            if close is None and session == today:
                # The watchdog writes this cache for every tracked ticker every
                # fifteen minutes, and the agent cannot untrack a holding.
                close = get_shown_price(ticker)
            close = close or last_close.get(ticker)
            if close is None:
                continue
            last_close[ticker] = close
            market_value += quantity * close

        curve.append(
            EquityPoint(
                date=session,
                equity=cash + market_value,
                cash=cash,
                market_value=market_value,
            )
        )
    return curve
