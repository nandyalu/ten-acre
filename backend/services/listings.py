"""Which tickers still trade, and which have stopped.

A delisted symbol does not fail cleanly. yfinance keeps answering for the
shell: AILEQ, delisted, returned five bars across two months, every one of them
priced at $0.000001. Nothing in that looks like an error — to a read-through
cache it looks like a ticker that is merely behind, so it refetches every half
hour forever. It also used to be handed to the daily sweep, which spent
minutes of GPU analysing a company with no market; the sweep is gone
(2026-09-08) but a delisted ticker is still something the agent can pay to
research, so detecting it still matters.

The rule here is deliberately about *freshness*, not price. A live ticker
produces a bar every session; one that has not produced a bar in a week is not
trading. That catches delistings, long halts, and mistyped symbols without
hardcoding a list or guessing from price — a real penny stock at $0.0001 is
still a real penny stock, and would be wrongly excluded by a price threshold.

Everything here is DB-only and cheap; it is checked on the hot path of every
market-data fetch.
"""
import datetime
import logging

from backend.database import db

log = logging.getLogger("ten-acre.listings")

# Weekdays without a new bar before a ticker is considered to have stopped
# trading. Generous on purpose: a long holiday weekend plus a data-provider
# outage should not mark a healthy ticker inactive, and the cost of being slow
# here is a few wasted requests, while the cost of a false positive is silently
# ignoring a position the user actually holds.
STALE_AFTER_TRADING_DAYS = 7

# An inactive ticker is still re-checked this often. Delistings are usually
# permanent, but halts lift and symbols get reused, and recovering should not
# need anyone to notice and intervene.
RECHECK_INTERVAL = datetime.timedelta(days=1)


def _trading_days_between(start: datetime.date, end: datetime.date) -> int:
    """Weekdays after ``start`` up to and including ``end``. Holidays are not
    modeled — see STALE_AFTER_TRADING_DAYS for why the slack absorbs them."""
    if end <= start:
        return 0
    days = 0
    day = start + datetime.timedelta(days=1)
    while day <= end:
        if day.weekday() < 5:
            days += 1
        day += datetime.timedelta(days=1)
    return days


def is_inactive(ticker: str) -> bool:
    """True when this ticker has stopped producing market data. Cheap enough to
    call on every fetch path."""
    status = db.get_ticker_status(ticker.upper().strip())
    return bool(status and status.inactive)


def inactive_tickers() -> list[str]:
    return sorted(status.ticker for status in db.get_inactive_tickers())


def should_fetch(ticker: str, now: datetime.datetime | None = None) -> bool:
    """Whether a market-data request for this ticker is worth making.

    Always true for an active ticker. For an inactive one, true only when the
    daily recheck is due — that single request is what lets a resumed listing
    be noticed without anyone intervening.
    """
    status = db.get_ticker_status(ticker.upper().strip())
    if status is None or not status.inactive:
        return True
    if status.checked_at is None:
        return True
    now = now or datetime.datetime.now(datetime.timezone.utc)
    checked_at = status.checked_at
    if checked_at.tzinfo is None:  # SQLite hands back naive datetimes
        checked_at = checked_at.replace(tzinfo=datetime.timezone.utc)
    return now - checked_at >= RECHECK_INTERVAL


def record_fetch(
    ticker: str,
    newest_bar_date: datetime.date | None,
    today: datetime.date | None = None,
) -> bool:
    """Record what a fetch found, and return whether the ticker is now inactive.

    ``newest_bar_date`` is the most recent bar the provider returned, or None
    when it returned nothing at all. A manual override is never overwritten.
    """
    ticker = ticker.upper().strip()
    today = today or datetime.date.today()
    status = db.get_ticker_status(ticker)
    if status is not None and status.manual:
        return status.inactive

    if newest_bar_date is None:
        stale_days = STALE_AFTER_TRADING_DAYS + 1  # nothing at all is as stale as it gets
        reason = "no market data returned"
    else:
        stale_days = _trading_days_between(newest_bar_date, today)
        reason = f"no new bar since {newest_bar_date.isoformat()}"

    inactive = stale_days > STALE_AFTER_TRADING_DAYS
    was_inactive = bool(status and status.inactive)
    if inactive and not was_inactive:
        log.warning("%s looks delisted or halted — %s. Skipping future fetches.", ticker, reason)
    elif was_inactive and not inactive:
        log.info("%s is producing bars again — resuming normal fetches.", ticker)

    db.set_ticker_status(
        ticker,
        inactive=inactive,
        reason=reason if inactive else None,
        last_bar_date=newest_bar_date,
    )
    return inactive


def set_manual(ticker: str, inactive: bool, reason: str | None = None) -> None:
    """Force a ticker on or off, and stop detection from changing it back."""
    db.set_ticker_status(
        ticker.upper().strip(),
        inactive=inactive,
        reason=reason or ("manually ignored" if inactive else None),
        manual=True,
    )


def clear_manual(ticker: str) -> None:
    """Hand a ticker back to automatic detection."""
    db.set_ticker_status(ticker.upper().strip(), manual=False)
