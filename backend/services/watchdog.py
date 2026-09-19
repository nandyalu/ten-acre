"""Intraday alert watchdog — all rule-based, no LLM involved.
``scan_for_alerts`` does one pass over every ticker on the watchlist — which
covers every holding, because the agent may not untrack a position it still
owns — alerting on big daily moves, unusual volume, stop-level breaches, and
price-target touches. ``earnings_due`` reports which tracked tickers announce
soon, for the separate pre-market task.

**Nothing here commissions an analysis (2026-09-12).** A big move and a volume
spike used to nominate the ticker for an immediate TradingAgents run, which
made this module decide what the agent should study and spend the agent's
research budget doing it. It reports what it sees; the agent decides what that
is worth.

Sent alerts are recorded in the ``alert`` table keyed by ``dedupe_key`` so a
15-minute loop never repeats itself (per ticker per day for moves/volume/
stops, once ever per signal for targets). Everything here is blocking
(yfinance + DB) — call via asyncio.to_thread.

Since 2026-09-08, ``scan_for_alerts`` also tops up the intraday bar cache
(``backend.services.intraday``) for every tracked ticker — piggybacking on
this loop's existing 15-minute cadence and tracked-ticker list rather than
adding a second schedule for the same job.
"""
import datetime

from backend.services import market_calendar
import logging
from dataclasses import dataclass
from zoneinfo import ZoneInfo

import yfinance as yf
from tradingagents.dataflows.stockstats_utils import yf_retry

from backend.database import db
from backend.database.models import Signal
from backend.services import bars, intraday, listings, ticker_book
from backend.services.ticker_book import AgentPosition
from backend.services.signals import price_crossed_target

log = logging.getLogger("ten-acre.watchdog")

US_MARKET_TZ = ZoneInfo("America/New_York")
_MARKET_OPEN = datetime.time(9, 30)
_MARKET_CLOSE = datetime.time(16, 0)

EARNINGS_LOOKAHEAD_DAYS = 2

# Calendar days of history behind the volume-spike baseline. The alert text
# says "20-day average", and ~30 calendar days is what yields 20 sessions once
# weekends are removed.
_VOLUME_BASELINE_DAYS = 30


def is_us_market_hours(now: datetime.datetime | None = None) -> bool:
    """Whether the regular NYSE session is open right now.

    **Holidays count, and they did not until 2026-09-12.** On Labor Day this
    answered True all afternoon, and every caller means "is the session
    genuinely open": one refuses a reset that needs a market order, one queues
    an exit-arm instead of attempting it, one takes a live quote rather than
    the last completed close. Each was wrong on that day in the same direction.

    The close is early on the three half-days a year, so the end of the session
    comes from the calendar rather than a constant.
    """
    now = (now or datetime.datetime.now(US_MARKET_TZ)).astimezone(US_MARKET_TZ)
    if not market_calendar.is_trading_day(now.date()):
        return False
    end = market_calendar._EARLY_CLOSE if now.date() in market_calendar.early_closes(
        now.year) else _MARKET_CLOSE
    return _MARKET_OPEN <= now.time() <= end


# --- Config (BotSetting-backed, see /alertconfig) ------------------------------


@dataclass
class AlertConfig:
    move_pct: float = 5.0  # abs daily % move that alerts + triggers an analysis
    stop_pct: float = 10.0  # % below avg cost that fires a stop alert
    volume_mult: float = 2.0  # today's volume vs 20-day average
    enabled: bool = True


_SETTING_KEYS = {"move_pct": "alert_move_pct", "stop_pct": "alert_stop_pct", "volume_mult": "alert_volume_mult"}


def load_config() -> AlertConfig:
    config = AlertConfig()
    for attr, key in _SETTING_KEYS.items():
        raw = db.get_setting(key)
        if raw:
            try:
                setattr(config, attr, float(raw))
            except ValueError:
                pass  # ignore a corrupt setting, keep the default
    config.enabled = db.get_setting("alerts_enabled") != "off"
    return config


# --- Market data ----------------------------------------------------------------


@dataclass
class DailySnapshot:
    price: float  # latest close (the running bar during market hours)
    prev_close: float
    day_change_pct: float
    last_bar_date: datetime.date
    today_volume: float
    avg_volume: float  # mean of the earlier bars in the ~1-month window


def get_daily_snapshot(ticker: str) -> DailySnapshot | None:
    """Best-effort like get_current_price — None when there aren't enough bars.
    Also writes the price through to the ticker price cache, same as
    get_current_price — this runs every 15 minutes across the whole watchlist
    during market hours, so it's the main thing that keeps the dashboard cache
    warm.

    The volume baseline and the previous close come from the daily bar cache,
    because they are completed sessions and cannot change between two ticks
    fifteen minutes apart. Only the current session is fetched live. This used
    to refetch roughly a month of bars per ticker per tick to read two closes
    and an average.

    A bar for the day in progress that carries no prices yet (pre-market)
    leaves ``last_bar_date`` on the previous session, and the caller's "no
    fresh bar" check then skips this ticker — which is intended: a NaN price
    compares false against every threshold and would silently never fire.
    """
    # The market date, passed through explicitly. Left to default, the cache
    # would use the local clock, which after about 8pm ET is already tomorrow —
    # so the session that just closed would be treated as still in progress.
    today = datetime.datetime.now(US_MARKET_TZ).date()
    history = bars.get_bars(
        ticker,
        today - datetime.timedelta(days=_VOLUME_BASELINE_DAYS),
        include_today=True,
        today=today,
    )
    if len(history) < 2:
        return None
    latest, previous = history[-1], history[-2]
    db.set_cached_price(ticker, latest.close, source="yfinance")
    earlier_volumes = [bar.volume for bar in history[:-1]]
    return DailySnapshot(
        price=latest.close,
        prev_close=previous.close,
        day_change_pct=(latest.close / previous.close - 1) * 100 if previous.close else 0.0,
        last_bar_date=datetime.date.fromisoformat(latest.date),
        today_volume=latest.volume,
        avg_volume=sum(earlier_volumes) / len(earlier_volumes) if earlier_volumes else 0.0,
    )


# --- Alert evaluation (pure) ------------------------------------------------------


@dataclass
class AlertCandidate:
    ticker: str
    alert_type: str  # "big_move" | "volume" | "stop_loss" | "signal_stop" | "target"
    dedupe_key: str
    message: str


def evaluate_ticker(
    ticker: str,
    snapshot: DailySnapshot,
    position: AgentPosition | None,
    target_signal: Signal | None,
    config: AlertConfig,
    today: datetime.date,
    stop_signal: Signal | None = None,
) -> list[AlertCandidate]:
    alerts: list[AlertCandidate] = []

    if abs(snapshot.day_change_pct) >= config.move_pct:
        alerts.append(
            AlertCandidate(
                ticker,
                "big_move",
                f"big_move:{ticker}:{today}",
                f"📊 {ticker} moved {snapshot.day_change_pct:+.1f}% today "
                f"(${snapshot.prev_close:,.2f} → ${snapshot.price:,.2f}).",
            )
        )

    if snapshot.avg_volume > 0 and snapshot.today_volume >= config.volume_mult * snapshot.avg_volume:
        alerts.append(
            AlertCandidate(
                ticker,
                "volume",
                f"volume:{ticker}:{today}",
                f"📊 {ticker} volume is {snapshot.today_volume / snapshot.avg_volume:.1f}× "
                f"its 20-day average.",
            )
        )

    held = position is not None and position.quantity > 0

    # Two stop alerts, deliberately separate. They answer different questions
    # and either can matter without the other:
    #
    # - signal_stop: price reached the level the analysis named as the point
    #   where its thesis is wrong. The decision to exit was made at entry; this
    #   is the reminder to execute it.
    # - stop_loss: price is a fixed percentage below what you actually paid.
    #   A backstop against the account, unrelated to any thesis, and the only
    #   one available for a position with no signal behind it.
    #
    # Distinct dedupe keys, so each fires at most once and neither suppresses
    # the other.
    if held and stop_signal is not None and stop_signal.stop_loss:
        if snapshot.price <= stop_signal.stop_loss:
            # Reworded 2026-09-15, on probe evidence: the old wording ("X at
            # $A reached the $B stop from the Hold signal...") carried three
            # AVGO prices in one sentence with no stated direction, and read
            # as a stop order that had already fired. Across 16 probe runs,
            # nearly every one spent most of its reasoning trying to work out
            # whether a sell had already happened instead of noticing the
            # real fact -- this is a signal-derived level, not a broker
            # order, and nothing sells unless the agent places one. Extended
            # the same day to name the research date and say plainly that
            # this is a rule firing against an old level, not new research --
            # the agent picks its own research cadence now, so a signal
            # behind a tracked ticker can be old, and the alert should not
            # read as though it carries a fresh verdict. See the
            # 2026-09-15 JOURNEY.md entry.
            alerts.append(
                AlertCandidate(
                    ticker,
                    "signal_stop",
                    f"signal_stop:{stop_signal.id}",
                    f"🛑 {ticker} fell to ${snapshot.price:,.2f}, at or below the "
                    f"${stop_signal.stop_loss:,.2f} level set by the {stop_signal.decision} "
                    f"signal researched on {stop_signal.signal_date}. This is a rule that "
                    "fired against that old level, not a new analysis — decide today what "
                    "to do about it. Nothing sells unless you place the order.",
                )
            )

    if held and position.avg_cost > 0:
        drop_pct = (1 - snapshot.price / position.avg_cost) * 100
        if drop_pct >= config.stop_pct:
            alerts.append(
                AlertCandidate(
                    ticker,
                    "stop_loss",
                    f"stop:{ticker}:{today}",
                    f"🛑 {ticker} at ${snapshot.price:,.2f} is over {config.stop_pct:g}% below "
                    f"the agent's ${position.avg_cost:,.2f} average cost (−{drop_pct:.1f}%).",
                )
            )

    if (
        held
        and target_signal is not None
        and target_signal.price_target
        and price_crossed_target(
            target_signal.price_target, target_signal.price_at_signal, high=snapshot.price, low=snapshot.price
        )
    ):
        alerts.append(
            AlertCandidate(
                ticker,
                "target",
                f"target:{target_signal.id}",
                f"🎯 {ticker} reached the ${target_signal.price_target:,.2f} target set by "
                f"the {target_signal.decision} signal researched on {target_signal.signal_date} "
                f"(now ${snapshot.price:,.2f}). This is a rule that fired against that old "
                "level, not a new analysis — decide today what to do about it.",
            )
        )

    return alerts


# --- Orchestration (blocking) ------------------------------------------------------


def _tracked_tickers() -> list[str]:
    """The watchlist, minus anything that has stopped trading.

    A held ticker is always on the watchlist — the agent cannot untrack a
    position it still owns, and Python refuses the attempt — so the watchlist
    alone covers everything the agent holds. A delisted holding is dropped
    here: it still sits in the book, there is just nothing left to watch and no
    alert it could ever produce.
    """
    return sorted(set(db.get_watchlist()) - set(listings.inactive_tickers()))


def scan_for_alerts() -> list[AlertCandidate]:
    """One watchdog pass. Returns the alerts that are new.

    Alerts are recorded before being returned, so a crash between recording
    and sending drops an alert rather than ever repeating one.

    **It reports; it does not commission (2026-09-12).** This used to also
    return a list of tickers to analyse on the spot, which meant the system
    chose what to study and charged the agent $0.05 for each one — ten of
    them, on a book the agent was never asked about first. Seeing a move and
    deciding what it is worth are two different jobs, and only the first is
    ours. See the 2026-09-12 entry in JOURNEY.md.
    """
    config = load_config()
    if not config.enabled:
        return []
    today = datetime.datetime.now(US_MARKET_TZ).date()
    fresh: list[AlertCandidate] = []

    for ticker in _tracked_tickers():
        # Independent of the daily snapshot below, and before its own
        # "continue" — a stale daily bar (fetch failed, holiday) must not
        # also block topping up the intraday cache, a completely separate
        # data source with its own failure mode. Best-effort: a bad tick here
        # costs one ticker fifteen minutes of intraday detail, not the scan.
        try:
            intraday.capture_recent(ticker)
        except Exception:
            log.exception("Intraday capture failed for %s", ticker)
        snapshot = get_daily_snapshot(ticker)
        if snapshot is None or snapshot.last_bar_date != today:
            continue  # no fresh bar (fetch failed, holiday) — never alert on stale closes
        position = ticker_book.agent_position(ticker, snapshot.price)
        target_signal = db.get_latest_signal_with_target(ticker)
        stop_signal = db.get_latest_signal_with_stop(ticker)
        for candidate in evaluate_ticker(
            ticker,
            snapshot,
            position,
            target_signal,
            config,
            today,
            stop_signal=stop_signal,
        ):
            if db.alert_already_sent(candidate.dedupe_key):
                continue
            db.record_alert(candidate.ticker, candidate.alert_type, candidate.dedupe_key, candidate.message)
            fresh.append(candidate)

    return fresh


# --- Earnings trigger ---------------------------------------------------------------


def get_next_earnings_date(ticker: str) -> datetime.date | None:
    try:
        calendar = yf_retry(lambda: yf.Ticker(ticker).calendar)
        if not isinstance(calendar, dict):
            return None
        today = datetime.date.today()
        upcoming = sorted(
            entry.date() if isinstance(entry, datetime.datetime) else entry
            for entry in (calendar.get("Earnings Date") or [])
            if isinstance(entry, datetime.date)
        )
        upcoming = [d for d in upcoming if d >= today]
        return upcoming[0] if upcoming else None
    except Exception:
        return None


def earnings_due() -> list[tuple[str, datetime.date]]:
    """Tracked tickers reporting within EARNINGS_LOOKAHEAD_DAYS.

    **A fact for the prompt, not a reason to analyse (2026-09-12).** This used
    to nominate each one for an analysis the agent had not asked for and was
    charged for. An earnings date is worth knowing; whether it is worth $0.05
    and sixteen minutes is the agent's call, and it may prefer to sell before
    the report rather than study it.

    The ``has_signal_today`` filter went with the nomination. It answered
    "have we already spent GPU on this today", which is not a question about
    whether the agent should be told a company reports on Thursday.
    """
    results = []
    today = datetime.date.today()
    for ticker in _tracked_tickers():
        next_date = get_next_earnings_date(ticker)
        if next_date is not None and (next_date - today).days <= EARNINGS_LOOKAHEAD_DAYS:
            results.append((ticker, next_date))
    return results
