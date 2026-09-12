"""The clock line, on the days it used to get wrong.

**This is the first line of every prompt**, and until 2026-09-12 it had no
holidays in it at all. On Labor Day, Monday 2026-09-07, it told the agent "the
market closes in 5h 28m, at 4:00 PM". The agent believed it and placed five
orders across three passes, every one refused with
``OPENAPI_CAN_NOT_TRADING_FOR_NON_TRADING_HOURS`` — a whole day of the
experiment spent on refusals the prompt had promised would not happen.
"""
import datetime

from backend.services import agent  # noqa: F401  (import order; see market_clock)
from backend.services import market_clock

# **The real zone, not a fixed -4 offset.** A hardcoded offset is right for
# half the year: the first draft of this file asserted a December case in EDT
# and was an hour out, which read as a bug in the code rather than in the test.
def at(y, m, d, hh, mm=0):
    return datetime.datetime(y, m, d, hh, mm, tzinfo=market_clock.US_MARKET_TZ)


# --- the calendar itself ---------------------------------------------------------


def test_the_2026_holidays_are_the_real_ones():
    """Checked against the published NYSE calendar, not derived from it."""
    assert {str(d): n for d, n in sorted(market_clock.market_holidays(2026).items())} == {
        "2026-01-01": "New Year's Day",
        "2026-01-19": "Martin Luther King Jr. Day",
        "2026-02-16": "Presidents' Day",
        "2026-04-03": "Good Friday",
        "2026-05-25": "Memorial Day",
        "2026-06-19": "Juneteenth",
        "2026-07-03": "Independence Day",
        "2026-09-07": "Labor Day",
        "2026-11-26": "Thanksgiving",
        "2026-12-25": "Christmas Day",
    }


def test_a_saturday_holiday_is_observed_on_the_friday():
    """4 July 2026 is a Saturday, so the exchange shuts on the 3rd."""
    assert market_clock.holiday_name(datetime.date(2026, 7, 3)) == "Independence Day"
    assert market_clock.holiday_name(datetime.date(2026, 7, 4)) is None


def test_a_sunday_holiday_is_observed_on_the_monday():
    """1 January 2028 is a Saturday; 2023's was a Sunday, observed on the 2nd."""
    assert market_clock.holiday_name(datetime.date(2023, 1, 2)) == "New Year's Day"


def test_good_friday_moves_with_easter():
    """The only NYSE holiday with neither a fixed date nor an nth-weekday rule."""
    assert market_clock.holiday_name(datetime.date(2026, 4, 3)) == "Good Friday"
    assert market_clock.holiday_name(datetime.date(2027, 3, 26)) == "Good Friday"


# --- the line the agent reads ----------------------------------------------------


def test_labor_day_says_so_and_names_the_reopen():
    line = market_clock.describe(at(2026, 9, 7, 10, 32))

    assert "closed all day for Labor Day" in line
    assert "opens again Tuesday 8 September at 9:30 AM" in line
    assert "closes in" not in line, "it promised a session that did not exist"


def test_the_friday_before_a_holiday_points_past_it():
    """The subtle one: it has to skip the weekend and the Monday."""
    line = market_clock.describe(at(2026, 9, 4, 16, 30))

    assert "opens again Tuesday 8 September at 9:30 AM" in line


def test_the_weekend_names_the_reopen_too():
    """Run 46's own Saturday. It said only "closed for the weekend"."""
    line = market_clock.describe(at(2026, 9, 12, 3, 15))

    assert "opens again Monday 14 September at 9:30 AM" in line


def test_an_ordinary_day_is_unchanged():
    line = market_clock.describe(at(2026, 9, 14, 10, 0))

    assert "The market closes in 6h 0m, at 4:00 PM." in line
    assert "opens again" not in line


def test_a_half_day_closes_at_one_and_says_why():
    """Without this the prompt promises three hours that do not exist."""
    line = market_clock.describe(at(2026, 12, 24, 11, 0))

    assert "at 1:00 PM" in line
    assert "half day for Christmas Eve" in line


# --- everything downstream of the clock ------------------------------------------


def test_next_open_skips_a_holiday():
    assert market_clock.next_open(at(2026, 9, 4, 16, 30)).date() == datetime.date(2026, 9, 8)


def test_the_close_is_early_on_a_half_day():
    assert market_clock.close_today(at(2026, 12, 24, 11, 0)).hour == 13
    assert market_clock.close_today(at(2026, 12, 23, 11, 0)).hour == 16


def test_minutes_to_close_respects_the_early_close():
    assert market_clock.minutes_to_close(at(2026, 12, 24, 12, 30)) == 30


# --- what a closed session actually stops (2026-09-12) ---------------------------


def _book():
    from backend.services import agent_book
    return agent_book.Book(budget=1000.0, cash=500.0, realized_pnl=0.0, holdings=[])


def test_a_closed_market_says_so_beside_the_orders(monkeypatch):
    """**The clock line was never enough.** Three probe runs wrote "the market
    is closed" and placed a buy or a sell in the same breath, and six of the
    nine broker failures on the live book are exactly that."""
    monkeypatch.setattr(agent.watchdog, "is_us_market_hours", lambda *a, **k: False)

    prompt = agent.build_prompt(_book(), [], {})

    assert "a buy or a sell you place will not execute" in prompt
    assert "not queued for the open" in prompt, "the belief one run actually stated"


def test_adjust_is_exempt_because_the_record_says_so(monkeypatch):
    """**Checked, not assumed.** An earlier wording had the broker refusing "a
    buy, a sell or an adjust". On Labor Day run 15 adjusted two exits at 09:31
    while runs 16, 17 and 18 had five buys and sells refused over the next six
    hours. No adjust has ever been refused: an exit rests at the broker rather
    than trading now, and it is the one useful thing left to do with a position
    going into a long weekend."""
    monkeypatch.setattr(agent.watchdog, "is_us_market_hours", lambda *a, **k: False)

    prompt = agent.build_prompt(_book(), [], {})

    assert "`adjust` does work" in prompt


def test_an_open_market_gets_no_such_rule(monkeypatch):
    monkeypatch.setattr(agent.watchdog, "is_us_market_hours", lambda *a, **k: True)

    prompt = agent.build_prompt(_book(), [], {})

    assert "will not execute" not in prompt


def test_the_session_check_knows_about_holidays():
    """Its five callers all mean "is the session genuinely open", and each was
    wrong in the same direction on Labor Day."""
    from backend.services import watchdog

    assert not watchdog.is_us_market_hours(at(2026, 9, 7, 11, 0)), "Labor Day"
    assert watchdog.is_us_market_hours(at(2026, 9, 14, 11, 0)), "an ordinary Monday"


def test_the_session_check_ends_early_on_a_half_day():
    from backend.services import watchdog

    assert watchdog.is_us_market_hours(at(2026, 12, 24, 12, 0))
    assert not watchdog.is_us_market_hours(at(2026, 12, 24, 14, 0))
