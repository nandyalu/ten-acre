"""The NYSE trading calendar, and nothing else.

**Its own module because it must import nothing.** ``market_clock`` and
``watchdog`` both need it and ``market_clock`` already imports ``watchdog``,
so a calendar living in either one puts a cycle between them. Only datetime is
needed here, which makes that impossible.
"""
import datetime


# **The NYSE calendar is computed, not fetched and not hardcoded (2026-09-12).**
# Every one of these is a stated rule, so a table would only be a copy of the
# rules that goes stale the year nobody updates it. No dependency either:
# pandas_market_calendars is a large library to answer ten questions a year.
#
# This existed because the clock had no holidays at all. On Labor Day, Monday
# 2026-09-07, the prompt said "the market closes in 5h 28m, at 4:00 PM" and the
# agent placed five orders against a shut venue across three passes — a whole
# day of the experiment spent on refusals it was told to expect.
_EARLY_CLOSE = datetime.time(13, 0)


def _easter(year: int) -> datetime.date:
    """Anonymous Gregorian algorithm. Good Friday is the only NYSE holiday
    with no fixed date and no nth-weekday rule."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m) // 451
    month, day = divmod(h + m - 7 * n + 114, 31)
    return datetime.date(year, month, day + 1)


def _nth_weekday(year: int, month: int, weekday: int, nth: int) -> datetime.date:
    """The nth given weekday of a month; nth=-1 means the last one."""
    if nth < 0:
        last = datetime.date(year, month + 1, 1) - datetime.timedelta(days=1) if month < 12 \
            else datetime.date(year, 12, 31)
        return last - datetime.timedelta(days=(last.weekday() - weekday) % 7)
    first = datetime.date(year, month, 1)
    return first + datetime.timedelta(days=(weekday - first.weekday()) % 7 + 7 * (nth - 1))


def _observed(day: datetime.date) -> datetime.date:
    """A fixed-date holiday on a Saturday is taken on the Friday, and on a
    Sunday on the Monday. The NYSE rule, not a guess."""
    if day.weekday() == 5:
        return day - datetime.timedelta(days=1)
    if day.weekday() == 6:
        return day + datetime.timedelta(days=1)
    return day


def market_holidays(year: int) -> dict[datetime.date, str]:
    """Every full NYSE closure in a year, by date."""
    easter = _easter(year)
    return {
        _observed(datetime.date(year, 1, 1)): "New Year's Day",
        _nth_weekday(year, 1, 0, 3): "Martin Luther King Jr. Day",
        _nth_weekday(year, 2, 0, 3): "Presidents' Day",
        easter - datetime.timedelta(days=2): "Good Friday",
        _nth_weekday(year, 5, 0, -1): "Memorial Day",
        _observed(datetime.date(year, 6, 19)): "Juneteenth",
        _observed(datetime.date(year, 7, 4)): "Independence Day",
        _nth_weekday(year, 9, 0, 1): "Labor Day",
        _nth_weekday(year, 11, 3, 4): "Thanksgiving",
        _observed(datetime.date(year, 12, 25)): "Christmas Day",
    }


def early_closes(year: int) -> dict[datetime.date, str]:
    """Half days — the session ends at 1:00 PM. Worth modelling for the same
    reason as the holidays: without them the prompt promises three hours that
    do not exist."""
    days = {
        _nth_weekday(year, 11, 3, 4) + datetime.timedelta(days=1): "the day after Thanksgiving",
    }
    for day, name in ((datetime.date(year, 7, 3), "Independence Day eve"),
                      (datetime.date(year, 12, 24), "Christmas Eve")):
        if day.weekday() < 5 and day not in market_holidays(year):
            days[day] = name
    return days


def holiday_name(day: datetime.date) -> str | None:
    """The closure's name, or None if the market trades that day."""
    return market_holidays(day.year).get(day)


def is_trading_day(day: datetime.date) -> bool:
    return day.weekday() < 5 and holiday_name(day) is None
