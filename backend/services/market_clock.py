"""What time it is, in the only frame the agent's decisions live in.

**The agent had no clock.** Nothing in its prompt said what time it was, and
it was reasoning about a trading day with no idea how much of one was left.
That was survivable while it decided once at the open and went quiet. It is
not survivable once it chooses its own next wakeup, which is a question about
the time.

Everything here is Eastern, because the session is. Showing UTC would make the
agent convert before it could reason, and a model doing arithmetic it does not
need to do is a model with one more thing to get wrong.
"""
import datetime

from backend.services.watchdog import US_MARKET_TZ, _MARKET_CLOSE, _MARKET_OPEN
# Re-exported: callers and tests reach the calendar through the clock.
from backend.services.market_calendar import (  # noqa: F401
    _EARLY_CLOSE,
    early_closes,
    holiday_name,
    is_trading_day,
    market_holidays,
)

# How long a pass may ask to sleep. The floor is not a limit on judgment: below
# it the book has not moved enough to be worth a fresh opinion, and the agent
# would be reading the same numbers again.
#
# **The ceiling was 6 hours and is now 4 days.** Six hours could not span a
# night, so it silently prevented the one request the agent most obviously
# needs to make: wake me before tomorrow's open. Four days covers a weekend
# with a holiday on either side.
MIN_WAKEUP = datetime.timedelta(minutes=5)
MAX_WAKEUP = datetime.timedelta(days=4)

# The last pass of the day, five minutes before the close. It runs whatever the
# agent asked for, so no position goes into the night unreviewed.
FINAL_PASS = datetime.time(15, 55)


def now_et(now: datetime.datetime | None = None) -> datetime.datetime:
    return (now or datetime.datetime.now(US_MARKET_TZ)).astimezone(US_MARKET_TZ)


def close_today(now: datetime.datetime | None = None) -> datetime.datetime:
    """Today's close, which is 1:00 PM on the three half-days a year."""
    here = now_et(now)
    end = _EARLY_CLOSE if here.date() in early_closes(here.year) else _MARKET_CLOSE
    return here.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)


def minutes_to_close(now: datetime.datetime | None = None) -> int:
    """Negative once the session is over, which reads correctly as "past it"."""
    return int((close_today(now) - now_et(now)).total_seconds() // 60)


def next_open(now: datetime.datetime | None = None) -> datetime.datetime:
    """The next open the market actually has, holidays skipped.

    It skipped only weekends until 2026-09-12, so on the Friday before Labor
    Day it pointed at a Monday the exchange was shut.
    """
    here = now_et(now)
    candidate = here.replace(
        hour=_MARKET_OPEN.hour, minute=_MARKET_OPEN.minute, second=0, microsecond=0
    )
    if candidate <= here:
        candidate += datetime.timedelta(days=1)
    while not is_trading_day(candidate.date()):
        candidate += datetime.timedelta(days=1)
    return candidate


def describe(now: datetime.datetime | None = None) -> str:
    """One line, first in the prompt.

    **It says why the market is shut and when it opens again (2026-09-12).**
    It had no holidays at all, so on Labor Day it read "the market closes in
    5h 28m, at 4:00 PM" and the agent spent three passes placing five orders
    the venue refused. Naming the reason and the reopen costs a clause and
    turns a wasted day into one the agent can plan around.
    """
    here = now_et(now)
    # The year is stated, not implied. The agent's own reasoning showed it
    # working out which year it was from a date that never said — a model's
    # training cut-off is the only other thing it has to go on, and guessing
    # from that is how a stale one becomes an assumption about the market.
    stamp = here.strftime("%A %-d %B %Y, %-I:%M %p").replace(" 0", " ")
    reopen = next_open(now)
    # "tomorrow at 9:30 AM" would be one word shorter and wrong across a
    # weekend or a holiday, which is exactly when this line matters.
    opens = f"It opens again {reopen.strftime('%A %-d %B')} at 9:30 AM."

    holiday = holiday_name(here.date())
    if holiday:
        return f"It is {stamp} Eastern. The market is closed all day for {holiday}. {opens}"
    if here.weekday() >= 5:
        return f"It is {stamp} Eastern. The market is closed for the weekend. {opens}"

    left = minutes_to_close(now)
    if here.time() < _MARKET_OPEN:
        return f"It is {stamp} Eastern. The market has not opened yet; it opens at 9:30 AM."
    if left < 0:
        return f"It is {stamp} Eastern. The market has closed for the day. {opens}"
    closes_at = close_today(now).strftime("%-I:%M %p")
    half = early_closes(here.year).get(here.date())
    short = f" Today is a half day for {half}, so the close is early." if half else ""
    if left == 0:
        return f"It is {stamp} Eastern. The market closes now.{short}"
    hours, minutes = divmod(left, 60)
    span = f"{hours}h {minutes}m" if hours else f"{minutes} minutes"
    return f"It is {stamp} Eastern. The market closes in {span}, at {closes_at}.{short}"


def clamp_wakeup(
    requested: datetime.datetime | None, now: datetime.datetime | None = None
) -> datetime.datetime | None:
    """Hold a requested wakeup inside the floor and the ceiling. Nothing else.

    Returns None when the agent asked for nothing, which the scheduler treats
    differently from a bad request: no answer is not a decision, so it falls
    back rather than pretending the agent chose the fallback.

    **This used to snap every request into market hours**, and that was the
    schedule wearing a different hat. A time past the close became the next
    open, a weekend became Monday, and anything before 9:30 was pushed forward
    — so the agent could not ask to look at anything before the day started,
    which is exactly when the morning analyses want commissioning.

    Waking outside the session is now the agent's call. It is told the market
    is shut, the broker refuses an order while it is, and that refusal reaches
    the next prompt. A wasted pass on a Saturday costs one prompt and teaches
    the agent something a rule would have hidden.
    """
    if requested is None:
        return None
    here = now_et(now)
    wanted = requested.astimezone(US_MARKET_TZ)

    earliest = here + MIN_WAKEUP
    if wanted < earliest:
        wanted = earliest
    latest = here + MAX_WAKEUP
    if wanted > latest:
        wanted = latest
    return wanted
