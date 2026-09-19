"""Read-through cache for daily OHLCV bars.

Before this existed, each caller fetched independently: the intraday watchdog
pulled roughly a month of bars per ticker *every 15 minutes* to use two
closes and a volume average, while the chart, the ATR, and signal grading
each refetched overlapping ranges of the same bars. On an 8-ticker watchlist
that was a few hundred fetches a day to persist eight numbers.

The saving is possible because **a completed session never changes**. Only the
bar for the day in progress moves, and that one is deliberately never cached —
storing it would serve a frozen mid-session snapshot as though it were a close.
Callers that need today ask for it explicitly and get a live fetch.

**Webull first, yfinance as fallback (2026-09-08).** Every fetch used to go
through yfinance alone. Confirmed live that day: Webull's history-bar
endpoint pages back with no real depth ceiling at daily granularity either
(reached 2001 in testing, in full 1,200-bar pages) — the same endpoint
already relied on for intraday bars (backend/services/intraday.py). yfinance
stays as the fallback rather than being removed: it is what already produces
the "possibly delisted" false positives and 429s documented elsewhere in
this app, and a Webull outage must not take the daily cache down with it the
way removing the fallback would.

Everything here is blocking (network + DB) — call via asyncio.to_thread.
"""
import datetime
import logging

import yfinance as yf
from tradingagents.dataflows.stockstats_utils import yf_retry

from backend.database import db
from backend.services import intraday, listings
from backend.services.positions import OhlcBar, drop_incomplete_bars
from backend.services.watchdog import _MARKET_OPEN, US_MARKET_TZ

log = logging.getLogger("ten-acre.bars")

# How late the first stored minute of a day may be and still count as covering
# the session. A bar stamped a few seconds after the bell is the same bar; one
# stamped an hour late means the cache missed the open, and a day summed from
# it would report the wrong open, high and low.
_MINUTE_COVERAGE_SLACK = datetime.timedelta(minutes=2)

# How long to wait before asking yfinance again for a ticker whose cache
# already looks current. Only matters on days when no new session closes —
# market holidays, mostly — where the "is the cache behind?" check can never be
# satisfied and would otherwise refetch on every call. In-process and lost on
# restart, which is fine: the cost of a redundant fetch is one request.
_RECHECK_INTERVAL = datetime.timedelta(minutes=30)
_last_fetch: dict[str, datetime.datetime] = {}

# The earliest start already requested per ticker. Without this, a ticker whose
# history is shorter than the caller asks for — a recent listing, or simply a
# 365-day chart of a stock that has traded for 200 — looks permanently
# incomplete and refetches on every single call, which is the opposite of what
# a cache is for. Recording the attempt rather than the result is what makes
# "we asked and this is all there is" distinguishable from "we never asked".
_earliest_attempt: dict[str, datetime.date] = {}

# Extra history pulled beyond what the caller asked for. A fetch is one request
# whatever its span, so widening it slightly means the next caller asking for a
# little more is served from cache instead of triggering another round trip.
_FETCH_MARGIN = datetime.timedelta(days=30)


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def last_completed_session(today: datetime.date | None = None) -> datetime.date:
    """The most recent weekday strictly before ``today``.

    Deliberately ignores market holidays. Getting this wrong in the
    conservative direction only means the cache looks stale and one extra fetch
    happens, which the recheck interval then absorbs. Modeling the NYSE
    calendar to save that request would not pay for itself.
    """
    day = (today or datetime.date.today()) - datetime.timedelta(days=1)
    while day.weekday() >= 5:
        day -= datetime.timedelta(days=1)
    return day


def _to_bars(history: list[dict], cutoff: datetime.date) -> list[dict]:
    """Rows strictly before ``cutoff``. ``history`` is already the normalized
    shape both sources produce — see ``_fetch_history``."""
    return [row for row in history if row["date"] < cutoff]


def _fetch_from_webull(ticker: str, start: datetime.date, today: datetime.date) -> list[dict] | None:
    """Daily bars from ``start`` onward, or None if Webull isn't configured,
    the call failed, or it came back with nothing — any of which sends the
    caller to the yfinance fallback instead."""
    from webull.data.common.timespan import Timespan

    days_needed = max((today - start).days, 0)
    # Calendar days always overstate trading days (weekends, holidays), so
    # this rounds up rather than risk asking for too few — one request costs
    # the same whether it returns 50 bars or 1,200.
    count = min(intraday._MAX_COUNT, max(50, int(days_needed * 1.6) + 10))
    bars = intraday.fetch_bars(ticker, count=count, timespan=Timespan.D)
    if bars is None:
        return None
    return [
        {
            "date": bar["timestamp"].date(),
            "open": bar["open"], "high": bar["high"], "low": bar["low"],
            "close": bar["close"], "volume": bar["volume"],
        }
        for bar in bars
        if bar["timestamp"].date() >= start
    ]


def _fetch_from_yfinance(ticker: str, start: datetime.date) -> list[dict] | None:
    """None on a failed request. An empty list is a real answer — "asked, and
    there is nothing there" — which is how a delisted ticker is told apart
    from a connectivity problem; see ``listings.record_fetch`` in ``refresh``.
    """
    try:
        history = yf_retry(lambda: yf.Ticker(ticker).history(start=start.isoformat()))
        history = drop_incomplete_bars(history, ("Open", "High", "Low", "Close"))
    except Exception:
        log.warning("Bar fetch failed for %s from %s", ticker, start, exc_info=True)
        return None
    if history is None or history.empty:
        return []
    return [
        {
            "date": timestamp.date(),
            "open": float(row["Open"]), "high": float(row["High"]),
            "low": float(row["Low"]), "close": float(row["Close"]),
            "volume": float(row["Volume"]),
        }
        for timestamp, row in history.iterrows()
    ]


def _fetch_history(
    ticker: str, start: datetime.date, today: datetime.date | None = None
) -> list[dict] | None:
    """Bars from ``start`` onward, oldest first, as plain dicts —
    date/open/high/low/close/volume — regardless of which source answered.
    None only when neither source could: Webull unconfigured or empty, and
    yfinance's own request failing outright.
    """
    today = today or datetime.date.today()
    bars = _fetch_from_webull(ticker, start, today)
    if bars is not None:
        return bars
    return _fetch_from_yfinance(ticker, start)


def refresh(ticker: str, start: datetime.date, today: datetime.date | None = None) -> int:
    """Fetch and store completed sessions from ``start``. Returns rows written.

    Also judges whether the ticker still trades. This is the right place for
    that: it is the one function that sees what the provider actually returned,
    and a delisted symbol is invisible from anywhere else — the provider keeps
    answering, just with bars months old.
    """
    today = today or datetime.date.today()
    history = _fetch_history(ticker, start, today)
    _last_fetch[ticker] = _now()
    previous_attempt = _earliest_attempt.get(ticker)
    _earliest_attempt[ticker] = min(start, previous_attempt) if previous_attempt else start

    newest = history[-1]["date"] if history else None
    listings.record_fetch(ticker, newest, today)

    if not history:
        return 0
    bars = _to_bars(history, cutoff=today)
    return db.upsert_daily_bars(ticker, bars) if bars else 0


def _asked_recently(ticker: str) -> bool:
    last_checked = _last_fetch.get(ticker)
    return last_checked is not None and _now() - last_checked < _RECHECK_INTERVAL


def _needs_fetch(ticker: str, start: datetime.date, today: datetime.date) -> datetime.date | None:
    """The date to fetch from, or None when the cache already covers the ask."""
    # A ticker that has stopped trading is asked at most once a day, and only
    # so a resumed listing can be noticed. Whatever bars it has are already
    # cached and will not grow.
    if not listings.should_fetch(ticker):
        return None

    oldest, newest = db.get_bar_coverage(ticker)

    # Nothing cached. Throttled too, so a ticker yfinance has no data for
    # (delisted, mistyped) is not re-asked on every call.
    if oldest is None:
        return None if _asked_recently(ticker) else start - _FETCH_MARGIN

    # Reaching further back than we have ever asked for. Not throttled: a user
    # switching the chart from 90 days to 365 should get the older bars now,
    # not in half an hour.
    attempted = _earliest_attempt.get(ticker)
    if start < oldest and (attempted is None or start < attempted):
        return start - _FETCH_MARGIN

    # Waiting for a new session to close. Throttled, because on a market
    # holiday this condition can never be satisfied.
    if newest < last_completed_session(today) and not _asked_recently(ticker):
        # Only the gap, plus a day of overlap so a revised bar is picked up.
        return newest - datetime.timedelta(days=1)

    return None


def get_bars(
    ticker: str,
    start: datetime.date,
    end: datetime.date | None = None,
    include_today: bool = False,
    today: datetime.date | None = None,
) -> list[OhlcBar]:
    """Daily bars in [start, end], oldest first, served from cache.

    Fetches only what the cache is missing. ``include_today`` appends the
    session in progress with a separate live request — it is never stored,
    because it is not final.
    """
    ticker = ticker.upper().strip()
    today = today or datetime.date.today()

    fetch_from = _needs_fetch(ticker, start, today)
    if fetch_from is not None:
        refresh(ticker, fetch_from, today)

    bars = [
        OhlcBar(
            date=row.date.isoformat(),
            open=row.open,
            high=row.high,
            low=row.low,
            close=row.close,
            volume=row.volume,
        )
        for row in db.get_daily_bars(ticker, start, end)
    ]

    if include_today and (end is None or end >= today):
        current = _todays_bar(ticker, today)
        if current is not None:
            bars.append(current)
    return bars


def _todays_bar_from_minutes(ticker: str, today: datetime.date) -> OhlcBar | None:
    """Today's session summed from the 1-minute bars already on disk, or None
    when the cache does not cover it from the open.

    **The watchdog captures those minutes for every tracked ticker on the same
    tick that asks for this bar**, so the day is usually already stored when
    this is called, and a bar built from real minutes is better than a
    vendor's in-progress daily bar rather than merely cheaper. On 2026-09-09,
    seven of ten tracked tickers held the whole session this way — 186 bars
    each — while the app was separately asking Webull for the same day and
    being refused.

    **Coverage has to reach the open, or the bar lies about it.** A cache that
    starts at 11am would report 11am's price as the day's open and its range
    as the day's high and low. When that happens this returns None and the
    caller fetches, which is the honest answer rather than a cheap wrong one.
    """
    session_open = datetime.datetime.combine(today, _MARKET_OPEN, tzinfo=US_MARKET_TZ)
    minutes = db.get_intraday_bars(ticker, session_open.astimezone(datetime.timezone.utc))
    minutes = [bar for bar in minutes if bar.timestamp.date() == today]
    if not minutes:
        return None
    # The first stored minute must be at or before the first minute of the
    # session, plus a little slack for a bar stamped a few seconds late.
    first = minutes[0].timestamp
    if first.tzinfo is None:
        first = first.replace(tzinfo=datetime.timezone.utc)
    if first - session_open > _MINUTE_COVERAGE_SLACK:
        return None
    return OhlcBar(
        date=today.isoformat(),
        open=minutes[0].open,
        high=max(bar.high for bar in minutes),
        low=min(bar.low for bar in minutes),
        close=minutes[-1].close,
        volume=sum(bar.volume for bar in minutes),
    )


def _todays_bar(ticker: str, today: datetime.date) -> OhlcBar | None:
    """The session in progress. None outside a session, or when the day's bar
    carries no prices yet (pre-market).

    Built from the 1-minute cache when that covers the session, and fetched
    live otherwise — see ``_todays_bar_from_minutes``. Deriving it came from
    2026-09-09, when asking Webull for this bar once per ticker per watchdog
    tick was most of what pushed the whole app over the market-data rate
    limit, while the answer sat in a table the same tick had just written.

    Skips the request entirely at the weekend. yfinance answers a Saturday
    range with an empty frame and a "possibly delisted" warning, so asking is
    both wasted and alarming to read in the logs.
    """
    if today.weekday() >= 5:
        return None
    if not listings.should_fetch(ticker):
        return None
    derived = _todays_bar_from_minutes(ticker, today)
    if derived is not None:
        return derived
    history = _fetch_history(ticker, today, today)
    if not history:
        return None
    row = history[-1]
    if row["date"] != today:
        return None
    return OhlcBar(
        date=today.isoformat(),
        open=row["open"],
        high=row["high"],
        low=row["low"],
        close=row["close"],
        volume=row["volume"],
    )


def reset_fetch_memo() -> None:
    """Clear the in-process fetch bookkeeping. For tests, and for a caller that
    knows the cache is stale (a fresh watchlist entry, say)."""
    _last_fetch.clear()
    _earliest_attempt.clear()
