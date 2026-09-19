"""The agent's clock, and the bounds on the time it may ask for.

It had no clock at all until 2026-09-03, which was survivable while it decided
once at the open. It is not survivable once it chooses its own next wakeup,
because that is a question about the time.
"""
import datetime
from zoneinfo import ZoneInfo

import pytest

from backend.services import market_clock as mc

ET = ZoneInfo("America/New_York")


def at(hour, minute=0, day=3):
    """A Thursday in September, Eastern."""
    return datetime.datetime(2026, 9, day, hour, minute, tzinfo=ET)


# --- what it tells the agent ---------------------------------------------------


def test_it_says_how_long_is_left():
    said = mc.describe(at(9, 35))
    assert "9:35 AM Eastern" in said
    assert "6h 25m" in said


def test_it_says_when_the_session_is_over():
    assert "closed for the day" in mc.describe(at(17, 10))


def test_it_says_when_the_market_has_not_opened():
    assert "has not opened yet" in mc.describe(at(7, 0))


def test_it_knows_a_weekend():
    assert "closed for the weekend" in mc.describe(at(11, 0, day=5))  # Saturday


# --- the bounds on a requested wakeup ------------------------------------------


def test_a_sensible_request_is_left_alone():
    assert mc.clamp_wakeup(at(11, 0), at(10, 0)) == at(11, 0)


def test_asking_for_nothing_stays_nothing():
    """None is not a decision. The scheduler falls back, and the agent is not
    credited with having chosen the fallback."""
    assert mc.clamp_wakeup(None, at(10, 0)) is None


def test_too_soon_is_pushed_to_the_floor():
    """Below five minutes the book has not moved enough to be worth a fresh
    opinion — the agent would read the same numbers again."""
    assert mc.clamp_wakeup(at(10, 1), at(10, 0)) == at(10, 0) + mc.MIN_WAKEUP


def test_too_far_is_pulled_to_the_ceiling():
    """Four days, which covers a weekend with a holiday on either side."""
    assert mc.clamp_wakeup(at(10, 0) + datetime.timedelta(days=30), at(10, 0)) == (
        at(10, 0) + mc.MAX_WAKEUP
    )


def test_a_time_after_the_close_is_honored():
    """**This used to snap to the next open, and that was the schedule wearing
    a different hat.** The agent may want to read after the session — the
    broker refuses an order then, and that refusal reaches the next prompt."""
    assert mc.clamp_wakeup(at(17, 0), at(15, 0)) == at(17, 0)


def test_a_time_before_the_open_is_honored():
    """The reason the gate had to go. Morning analyses take about eighteen
    minutes, so commissioning them has to happen before the day starts."""
    assert mc.clamp_wakeup(at(7, 0, day=4), at(15, 0)) == at(7, 0, day=4)


def test_a_weekend_request_is_honored():
    """A wasted pass on a Saturday costs one prompt. A rule that forbids it
    hides whatever the agent wanted to check."""
    saturday = datetime.datetime(2026, 9, 5, 11, 0, tzinfo=ET)
    friday = datetime.datetime(2026, 9, 4, 15, 30, tzinfo=ET)
    assert mc.clamp_wakeup(saturday, friday) == saturday


def test_an_overnight_gap_survives():
    """The ceiling was 6 hours, which could not span a night — so it silently
    blocked the request the agent most obviously needs to make."""
    friday = datetime.datetime(2026, 9, 4, 16, 0, tzinfo=ET)
    monday_open = datetime.datetime(2026, 9, 7, 9, 30, tzinfo=ET)
    assert mc.clamp_wakeup(monday_open, friday) == monday_open


@pytest.mark.parametrize("minutes", [5, 30, 120, 360, 1440])
def test_every_allowed_gap_survives_the_clamp(minutes):
    now = at(10, 0)
    wanted = now + datetime.timedelta(minutes=minutes)
    assert mc.clamp_wakeup(wanted, now) == wanted


# --- the final pass before the close -------------------------------------------


def test_the_final_pass_is_five_minutes_before_todays_close():
    assert mc.next_final_pass(at(9, 0)) == at(15, 55)


def test_once_it_has_come_the_next_one_is_the_next_session():
    """Strictly after now, so re-arming the moment it fires never re-arms it
    for the same session."""
    assert mc.next_final_pass(at(15, 55)) == at(15, 55, day=4)  # Friday


def test_a_holiday_weekend_points_at_the_next_real_session():
    """Labor Day 2026 is Monday the 7th."""
    friday_evening = datetime.datetime(2026, 9, 4, 16, 30, tzinfo=ET)
    tuesday = datetime.datetime(2026, 9, 8, 15, 55, tzinfo=ET)
    assert mc.next_final_pass(friday_evening) == tuesday


def test_a_half_day_gets_one_before_its_one_oclock_close():
    """The old fixed 3:55 PM fell after a 1:00 PM close, so a half-day never
    had a final pass at all."""
    christmas_eve = datetime.datetime(2026, 12, 24, 9, 0, tzinfo=ET)
    assert mc.next_final_pass(christmas_eve) == christmas_eve.replace(hour=12, minute=55)
