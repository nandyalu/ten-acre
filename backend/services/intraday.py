"""1-minute intraday price bars, sourced from Webull's history-bar endpoint.

Unlike the daily bar cache (backend/services/bars.py), which reads through
yfinance, this reads through Webull. yfinance enforces a hard 8-day window on
1-minute history — Yahoo's own limit, confirmed live 2026-09-08, not
something a bigger request or a different library call can work around.
Webull's history-bar endpoint has no such ceiling in practice: paging
backward with ``end_time``, a live test against the sandbox market-data
endpoint that day pulled 5-minute bars from May and 15-minute bars from
January with no sign of a real cutoff, and six rapid-fire calls showed no
rate-limiting either.

Kept separate from quotes.py rather than folded into it: that module answers
"the price right now," this one answers "what did the price do," and the two
have different enough failure modes to want their own log lines. It does
reuse quotes' lazily-built Webull client and category cache rather than
duplicating that setup.
"""
import datetime
import logging
from dataclasses import dataclass

from backend.database import db
from backend.database.models import IntradayBar
from backend.services import quotes

log = logging.getLogger("ten-acre.intraday")

def experiment_start() -> datetime.datetime:
    """This deployment's own first trading day. Nothing happened before it, so
    a backfill never needs to reach further back.

    **Read per call, not a constant.** It was
    ``datetime.datetime(2026, 9, 2)`` until 2026-09-10, which is this
    deployment's start and nobody else's: a self-hoster starting four months
    from now would have paged back four months of 1-minute bars to reach a
    date belonging to another person's experiment — thousands of requests
    against a rate-limited endpoint before the agent had decided anything.

    Naive, treated as UTC — the same convention every timestamp in this module
    and in IntradayBar.timestamp follows, matching how the rest of this app
    reads a naive datetime out of SQLite as UTC rather than local time.
    """
    from backend.services import experiment

    return datetime.datetime.combine(experiment.start_date(), datetime.time.min)

# Minutes of bars to ask for on a routine top-up (see capture_recent, called
# from the watchdog's 15-minute tick). Wider than the actual gap on purpose:
# upsert_intraday_bars replaces on conflict, so asking for more than strictly
# needed costs nothing extra and covers a missed or slow-starting tick.
_CAPTURE_MINUTES = 30

# Webull's own per-call cap.
_MAX_COUNT = 1200

# Hard stop on how many pages a single backfill will walk back, independent
# of how far `since` asks for. Guards against a paging bug spinning forever
# rather than against any real depth limit — none has been found.
_MAX_BACKFILL_PAGES = 50


def _as_utc(dt: datetime.datetime) -> datetime.datetime:
    """A naive datetime, read as UTC by this module's convention, made aware
    just long enough to compute an epoch timestamp correctly — naive
    datetime.timestamp() assumes local time, which is not what a value that
    came out of this module (or out of the database) means."""
    return dt if dt.tzinfo else dt.replace(tzinfo=datetime.timezone.utc)


def _parse_bars(raw: list[dict]) -> list[dict]:
    """Webull's bar shape -> what db.upsert_intraday_bars expects.

    Timestamps arrive as ISO 8601 with a UTC offset (e.g.
    "2026-09-08T19:55:00.000+0000"); OHLCV arrive as strings. Stored naive,
    per this module's UTC convention. Unreadable bars are skipped and logged
    rather than failing the whole page — one bad row must not cost the rest.
    """
    out = []
    for bar in raw:
        try:
            ts = datetime.datetime.fromisoformat(bar["time"]).astimezone(datetime.timezone.utc)
            out.append({
                "timestamp": ts.replace(tzinfo=None),
                "open": float(bar["open"]),
                "high": float(bar["high"]),
                "low": float(bar["low"]),
                "close": float(bar["close"]),
                "volume": float(bar["volume"]),
            })
        except (KeyError, TypeError, ValueError) as exc:
            log.warning("Skipping unreadable intraday bar %r: %s", bar, exc)
    return out


def fetch_bars(
    ticker: str,
    *,
    count: int = _MAX_COUNT,
    end_time: datetime.datetime | None = None,
    timespan=None,
) -> list[dict] | None:
    """One page of bars, oldest first. None when Webull isn't configured or
    the call failed outright — an empty list would say "asked, and there is
    nothing there," which is a different fact the caller needs to tell apart
    from "could not ask."

    ``timespan`` defaults to 1-minute (this module's own reason for being);
    ``backend.services.bars`` passes ``Timespan.D`` for the daily cache,
    reusing this same client, category fallback, and error handling rather
    than duplicating them for a different granularity. Confirmed live
    2026-09-08 against the sandbox market-data endpoint: daily bars page back
    with no real ceiling either — a plain count=1200 request already reached
    2001 without any sign of a floor.
    """
    market_data = quotes._get_market_data()
    if market_data is None:
        return None
    from webull.data.common.category import Category
    from webull.data.common.timespan import Timespan

    timespan = timespan or Timespan.M1
    known = quotes.category_for(ticker)
    categories = [known] if known else [Category.US_STOCK.name, Category.US_ETF.name]
    kwargs = {"count": str(count)}
    if end_time is not None:
        kwargs["end_time"] = int(_as_utc(end_time).timestamp() * 1000)
    for category in categories:
        try:
            response = quotes.market_data_request(
                lambda: market_data.get_history_bar(ticker, category, timespan, **kwargs),
                f"bars for {ticker}",
            )
            body = response.json() if hasattr(response, "json") else response
        except Exception as exc:
            log.warning("Webull history bar failed for %s/%s: %s", ticker, category, exc)
            # A rate limit stops the whole attempt; any other error still tries
            # the next category. See the same split in quotes.get_realtime_price.
            # "Too busy" says nothing about whether this ticker is a stock or an
            # ETF, and before 2026-09-09 treating it as if it did meant a
            # rate-limited ticker cost two requests instead of one — the failure
            # doubling the traffic that caused it. Other errors are left falling
            # through, because a wrong category can surface as one.
            if quotes.rate_limited(exc):
                return None
            continue
        raw = (
            body if isinstance(body, list)
            else (body.get("data") or body.get("result") or body.get("list") or [])
        )
        if not raw:
            continue
        quotes.remember_category(ticker, category)
        return sorted(_parse_bars(raw), key=lambda b: b["timestamp"])
    return None


def backfill(ticker: str, since: datetime.datetime | None = None) -> int:
    """Page backward from now to ``since``, upserting every bar found.

    Safe to re-run: upserting replaces on conflict, so backfilling an
    already-covered range costs only the wasted API calls, not wrong data.
    In practice this rarely pages more than once — a single 1,200-bar page
    already reaches back six trading days, and ``since`` defaults to the
    experiment's own start.
    """
    # Resolved here rather than as a default argument: a default is evaluated
    # once at import, which would freeze whatever the date was when the module
    # first loaded.
    since = _as_utc(since or experiment_start()).replace(tzinfo=None)
    total = 0
    end_time = None
    previous_oldest = None
    reached_since = False
    for _ in range(_MAX_BACKFILL_PAGES):
        page = fetch_bars(ticker, end_time=end_time)
        if not page:
            break
        oldest = page[0]["timestamp"]
        # Checked before writing: a stalled page is one we already have, and
        # upserting it again would only mask the stall as a wasted write.
        if previous_oldest is not None and oldest >= previous_oldest:
            log.warning(
                "Intraday backfill for %s stalled paging past %s — stopping", ticker, oldest
            )
            break
        total += db.upsert_intraday_bars(ticker, page)
        previous_oldest = oldest
        if oldest <= since:
            reached_since = True
            break
        end_time = oldest - datetime.timedelta(minutes=1)
    else:
        log.warning(
            "Intraday backfill for %s hit the %d-page safety cap before reaching %s",
            ticker, _MAX_BACKFILL_PAGES, since,
        )
    log.info(
        "Backfilled %d intraday bar(s) for %s%s",
        total, ticker, "" if reached_since else " (did not reach the requested start)",
    )
    return total


def capture_recent(ticker: str) -> int:
    """Top up the cache with the last ``_CAPTURE_MINUTES``. Meant to run on
    the watchdog's own 15-minute tick (backend/services/watchdog.py) rather
    than a schedule of its own — reusing an existing cadence instead of
    adding a new one for the same tracked-ticker list.
    """
    page = fetch_bars(ticker, count=_CAPTURE_MINUTES)
    if not page:
        return 0
    return db.upsert_intraday_bars(ticker, page)


# --- Serving a chart: aggregated intraday where it exists, daily beyond it ---

# How far back "fine" (aggregated intraday) resolution reaches at most,
# regardless of how much history has actually been captured. Chosen in the
# same conversation as experiment_start(): daily bars beyond this are not a
# missing-data fallback, they are the deliberate choice for a horizon this
# app already treats as 1-2 weeks — nobody needs 5-minute candles from four
# months ago to reason about a swing trade.
_MAX_FINE_DAYS = 90

# The interval a chart actually displays fine-grained data at. Captured at
# 1 minute (IntradayBar) but shown coarser — a full trading day of 1-minute
# candles is more noise than signal on a chart meant to be read at a glance.
_DISPLAY_INTERVAL_MINUTES = 5


@dataclass
class ChartBar:
    """One bar as the chart draws it — a real timestamp rather than a bare
    date, so a chart can mix daily bars for the distant past with aggregated
    intraday bars for anything recent enough to have them, positioned by
    actual elapsed time rather than by index."""

    timestamp: datetime.datetime  # UTC, naive — this module's convention
    open: float
    high: float
    low: float
    close: float
    volume: float


def aggregate_bars(bars: list[IntradayBar], interval_minutes: int) -> list[ChartBar]:
    """Combine 1-minute bars (oldest first) into ``interval_minutes``-wide
    buckets.

    A bucket's open/close come from its first/last bar, high/low from the
    extremes across it, volume summed. A bucket is labelled by its own start
    time floored to the interval — a 5-minute bucket starting at 9:31 is
    labelled 9:30, not 9:31, so buckets land on the same grid regardless of
    which minute the underlying data happens to start on.
    """
    buckets: dict[datetime.datetime, list[IntradayBar]] = {}
    for bar in bars:
        floored_minute = (bar.timestamp.minute // interval_minutes) * interval_minutes
        key = bar.timestamp.replace(minute=floored_minute, second=0, microsecond=0)
        buckets.setdefault(key, []).append(bar)
    return [
        ChartBar(
            timestamp=key,
            open=group[0].open,
            high=max(b.high for b in group),
            low=min(b.low for b in group),
            close=group[-1].close,
            volume=sum(b.volume for b in group),
        )
        for key, group in sorted(buckets.items())
    ]


def get_chart_bars(ticker: str, days: int) -> list[ChartBar]:
    """Chart-ready bars for the last ``days`` calendar days, oldest first:
    aggregated intraday for whatever recent stretch is both covered and
    within ``_MAX_FINE_DAYS``, daily bars for everything older than that.

    Reads whatever intraday coverage already exists rather than assuming
    ``experiment_start()`` — a ticker only recently added has less of it, and a
    request for history before the experiment began has none at all, in
    which case this degrades to exactly what ``bars.get_bars`` already
    returned before any of this existed.
    """
    from backend.services import bars as daily_bars  # avoids a top-level cycle with positions.py

    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    requested_start = now - datetime.timedelta(days=days)
    fine_floor_by_policy = now - datetime.timedelta(days=_MAX_FINE_DAYS)

    oldest_coverage, _newest_coverage = db.get_intraday_coverage(ticker)
    fine_from = None
    if oldest_coverage is not None:
        fine_from = max(oldest_coverage, fine_floor_by_policy, requested_start)

    fine: list[ChartBar] = []
    if fine_from is not None and fine_from < now:
        raw = db.get_intraday_bars(ticker, fine_from)
        fine = aggregate_bars(raw, _DISPLAY_INTERVAL_MINUTES)

    # Daily bars fill everything from the requested start up to (but not
    # including) whatever day the fine-grained portion takes over on — the
    # whole range when there is no fine data at all.
    daily_end = (fine[0].timestamp.date() - datetime.timedelta(days=1)) if fine else None
    daily: list[ChartBar] = []
    if daily_end is None or daily_end >= requested_start.date():
        for bar in daily_bars.get_bars(ticker, requested_start.date(), end=daily_end, include_today=daily_end is None):
            daily.append(ChartBar(
                timestamp=datetime.datetime.fromisoformat(bar.date),
                open=bar.open, high=bar.high, low=bar.low, close=bar.close, volume=bar.volume,
            ))

    return sorted(daily + fine, key=lambda b: b.timestamp)
