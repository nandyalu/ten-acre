"""The four stock cells (backend/services/trend.py), and where they land.

Facts about a stock, computed from completed sessions, shown beside the price
in the signals table, the watchlist table and the candidate table. Nothing in
Python decides from them, so the whole contract is: the arithmetic is right, a
short history gives dashes rather than a wrong figure, a broken read gives
dashes rather than a broken pass, and each table carries them where the
docstring says it does.

Pure apart from two tests that patch the bar cache.
"""
import datetime
from types import SimpleNamespace

import pytest

from backend.services import agent, agent_book, bars, trend

# The autouse fixture in conftest replaces describe_many for every test, so
# the real one is kept here, at import time, for the tests that are about it.
_REAL_DESCRIBE_MANY = trend.describe_many


def _bar(i, close, volume=1_000_000.0, spread=1.0):
    day = datetime.date(2025, 1, 1) + datetime.timedelta(days=i)
    return SimpleNamespace(
        date=day.isoformat(), open=close, high=close + spread, low=close - spread,
        close=close, volume=volume,
    )


def _rising(n=260, start=100.0, step=0.5, **extra):
    return [_bar(i, start + i * step, **extra) for i in range(n)]


# --- compute ----------------------------------------------------------------------


def test_a_steady_climb_is_up_and_every_figure_is_filled():
    t = trend.compute(_rising())

    assert t.word == "UP"
    assert t.as_of == _rising()[-1].date
    assert t.vs_short_pct > 0 and t.vs_long_pct > 0
    # 21 sessions of +0.5 on a close of 229.5: the 21-session change is from
    # 219.0, so +4.8%.
    assert t.month_pct == pytest.approx((229.5 - 219.0) / 219.0 * 100)
    assert t.quarter_pct == pytest.approx((229.5 - 198.0) / 198.0 * 100)
    assert t.volume_ratio == pytest.approx(1.0)
    # A constant spread of 1 either side gives a true range of 2 (the gap to the
    # previous close is smaller), so the range is 2 / 229.5.
    assert t.range_pct == pytest.approx(2 / 229.5 * 100)


def test_a_steady_fall_is_down():
    history = [_bar(i, 300.0 - i * 0.5) for i in range(260)]
    assert trend.compute(history).word == "DOWN"


def test_between_the_averages_is_mixed():
    # A long climb, then a sharp drop that takes the close under the 50-day
    # average while it stays above the 200-day one.
    history = _rising(250)
    for i in range(250, 260):
        history.append(_bar(i, 190.0))
    t = trend.compute(history)
    assert t.vs_short_pct < 0 < t.vs_long_pct
    assert t.word == "MIXED"


def test_a_volume_burst_shows_against_normal():
    history = _rising(200)
    for bar in history[-5:]:
        bar.volume = 3_000_000.0
    t = trend.compute(history)
    # Five sessions at 3M among sixty: normal is (55 + 15) / 60 M.
    assert t.volume_ratio == pytest.approx(3.0 / ((55 * 1.0 + 5 * 3.0) / 60))
    assert t.volume_cell() == "2.6× normal"


def test_a_short_history_gives_dashes_not_wrong_numbers():
    """A listing a few months old has a month and a quarter and no 200-day
    average. The cell says so instead of averaging what there is."""
    t = trend.compute(_rising(80))

    assert t.vs_short_pct is not None and t.vs_long_pct is None
    assert t.word is None
    assert t.trend_cell().startswith("50d +") and t.trend_cell().endswith(", no 200-day yet")
    assert t.month_pct is not None and t.quarter_pct is not None
    assert t.volume_ratio is not None


def test_too_short_for_anything_is_all_dashes_but_the_cells_still_render():
    t = trend.compute(_rising(3))

    assert t.word is None
    assert t.cells() == ("—", "—", "—", "—")
    assert t.trend_cell() == "—"
    assert t.returns_cell() == "—"
    assert t.volume_cell() == "—"


def test_a_split_or_a_bad_bar_withholds_the_cells_and_says_so():
    """CTVA read 77.65 one session and 12.57 the next in the bar cache, and the
    first probe showed the agent `DOWN: 50d -84.3%`. A break is named, and
    nothing is computed across it."""
    history = _rising(220)
    for i in range(220, 230):
        history.append(_bar(i, (100.0 + i * 0.5) / 6, volume=80_000_000.0))

    t = trend.compute(history)

    assert t.break_on == history[220].date
    assert t.break_pct == pytest.approx((history[220].close / history[219].close - 1) * 100)
    assert t.word is None
    day = datetime.date.fromisoformat(history[220].date).strftime("%-d %b")
    assert t.cells() == (
        f"withheld: the price history breaks on {day} (-83% in one session, a split or a data error)",
        "—", "—", "—",
    )


def test_a_real_forty_percent_fall_is_not_a_break():
    history = _rising(220)
    for i in range(220, 230):
        history.append(_bar(i, (100.0 + i * 0.5) * 0.6))
    t = trend.compute(history)
    assert t.break_on is None
    assert t.word == "DOWN"


def test_a_break_older_than_the_longest_window_does_not_count():
    history = [_bar(i, 10.0) for i in range(20)] + _rising(230, start=100.0)
    for i, bar in enumerate(history):
        bar.date = (datetime.date(2025, 1, 1) + datetime.timedelta(days=i)).isoformat()
    assert trend.compute(history).break_on is None


def test_no_bars_is_no_trend():
    assert trend.compute([]) is None
    assert trend.cells(None) == ("—", "—", "—", "—")


def test_the_cells_read_the_way_the_legend_says():
    t = trend.Trend(
        as_of="2026-09-30", close=231.1, break_on=None, break_pct=None,
        vs_short_pct=3.14, vs_long_pct=12.4,
        month_pct=4.21, quarter_pct=15.0, volume_ratio=1.63, range_pct=2.31,
    )
    assert t.cells() == (
        "UP: 50d +3.1%, 200d +12.4%", "+4.2% / +15.0%", "1.6× normal", "2.3%",
    )


def test_the_legend_states_the_windows_it_actually_uses():
    """The numbers in the legend are read from the constants, never typed
    twice, the same way the watchlist legend reads its threshold."""
    legend = trend.legend()
    for figure in (
        f"{trend.SHORT_AVERAGE}-day", f"{trend.LONG_AVERAGE}-day",
        f"last {trend.MONTH} and {trend.QUARTER} sessions",
        f"last {trend.VOLUME_RECENT} sessions", f"last {trend.VOLUME_NORMAL}",
        f"over {trend.RANGE_PERIOD} sessions",
    ):
        assert figure in legend
    for column in trend.COLUMNS:
        assert f"**{column}**" in legend


# --- describe: the bar cache, and failure -----------------------------------------


def test_describe_reads_completed_sessions_only(monkeypatch):
    asked = {}

    def get_bars(ticker, start, end=None, include_today=False, today=None):
        asked.update(ticker=ticker, start=start, include_today=include_today, today=today)
        return _rising()

    monkeypatch.setattr(bars, "get_bars", get_bars)
    today = datetime.date(2026, 10, 1)

    t = trend.describe("aapl", today=today)

    assert t.word == "UP"
    assert asked["ticker"] == "aapl"
    assert asked["include_today"] is False, "a mid-session bar understates the day"
    assert asked["today"] == today
    assert (today - asked["start"]).days >= 290, "200 sessions is about 290 calendar days"


def test_a_history_that_cannot_be_read_is_no_trend_and_no_exception(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("vendor down")

    monkeypatch.setattr(bars, "get_bars", boom)

    assert trend.describe("AAPL") is None
    assert _REAL_DESCRIBE_MANY(["AAPL", "nvda", "", None]) == {}


def test_describe_many_reads_each_distinct_ticker_once(monkeypatch):
    seen = []
    monkeypatch.setattr(
        trend, "describe",
        lambda ticker, today=None: seen.append(ticker) or (trend.compute(_rising()) if ticker != "NONE" else None),
    )

    found = _REAL_DESCRIBE_MANY(["aapl", "AAPL", "intc", "NONE"])

    assert seen == ["AAPL", "INTC", "NONE"]
    assert set(found) == {"AAPL", "INTC"}


# --- where the cells land -------------------------------------------------------------


def _book(cash=1000.0):
    return agent_book.Book(budget=10_000.0, cash=cash, realized_pnl=0.0, holdings=[])


def _trend(word_sign=1):
    return trend.Trend(
        as_of="2026-09-30", close=100.0, break_on=None, break_pct=None,
        vs_short_pct=3.1 * word_sign, vs_long_pct=12.4 * word_sign,
        month_pct=4.2, quarter_pct=15.0, volume_ratio=1.6, range_pct=2.3,
    )


class _Candidate:
    def __init__(self, ticker):
        self.ticker, self.name, self.price = ticker, f"{ticker} Inc", 50.0
        self.volume, self.change_pct, self.source = 5_000_000.0, 1.0, "most active"

    @property
    def volume_m(self):
        return self.volume / 1_000_000


def test_the_candidate_table_carries_the_cells_and_dashes_a_name_without_them():
    lines = agent.describe_menu([_Candidate("AAA"), _Candidate("BBB")], 0.05, {"AAA": _trend()})

    header = next(l for l in lines if l.startswith("| Ticker"))
    assert header.endswith("| " + " | ".join(trend.COLUMNS) + " |")
    assert "| AAA | AAA Inc | $50.00 | +1.0% | 5.0M | most active | UP: 50d +3.1%, 200d +12.4% | +4.2% / +15.0% | 1.6× normal | 2.3% |" in lines
    assert "| BBB | BBB Inc | $50.00 | +1.0% | 5.0M | most active | — | — | — | — |" in lines
    assert trend.legend() in lines[0]
    assert "A research order on one costs $0.05 and runs inside this pass" in lines[0]


def test_the_watchlist_table_puts_the_cells_between_the_price_and_the_analysis(monkeypatch):
    monkeypatch.setattr(agent.db, "get_recent_signals", lambda ticker, limit=1: [])

    lines = agent.describe_watchlist(
        ["AAA"], 4, _book(), {"AAA": 50.0}, None, "2026-10-01 10:15 AM ET", {"AAA": _trend(-1)},
    )

    assert "| AAA | watched | $50.00 | — | — | DOWN: 50d -3.1%, 200d -12.4% | +4.2% / +15.0% | 1.6× normal | 2.3% | never | never | never | never |" in lines
    assert trend.legend() in lines[0]


def test_the_signals_table_puts_the_cells_before_the_two_columns_about_the_agent():
    signal = SimpleNamespace(
        ticker="AAA", signal_date="2026-10-01", created_at="2026-10-01 09:05:00", decision="Buy",
        price_at_signal=49.0, entry_price=49.5, stop_loss=47.0, price_target=56.0,
        win_probability=60.0, risk_reward=2.6, expected_value_r=None,
    )
    lines = agent.describe_signals(
        [signal], _book(), {"AAA": 50.0}, "2026-10-01 10:15 AM ET", trends={"AAA": _trend()},
    )

    row = next(l for l in lines if l.startswith("| AAA |"))
    # After the analyst's figures, before `You hold` and `You could buy`. The
    # share count is not pinned: it moves with the buying-power margin.
    assert "| 60% | 2.6:1 | UP: 50d +3.1%, 200d +12.4% | +4.2% / +15.0% | 1.6× normal | 2.3% | — | " in row
    assert row.endswith("share(s) |")
    assert trend.legend() in lines[0]


def test_the_pass_reads_the_cells_once_and_hands_them_to_every_table(monkeypatch):
    """`_decide` reads one trend per ticker the pass can show and passes the
    same dict to the prompt and to the fetches, so a retry and a `watchlist`
    call describe the stock the first answer saw."""
    import backend.tests.test_agent as _t

    asked = []
    monkeypatch.setattr(
        trend, "describe_many",
        lambda tickers, today=None: asked.append(sorted(tickers)) or {"AAA": _trend()},
    )
    monkeypatch.setattr(agent.db, "get_watchlist", lambda: ["AAA", "BBB"])
    monkeypatch.setattr(agent.db, "get_recent_signals", lambda ticker=None, limit=1: [])
    monkeypatch.setattr(agent, "_max_watchlist", lambda: 4)
    monkeypatch.setattr(agent, "_ask", lambda prompt, tools=None: '{"reasoning": "hold", "orders": []}')
    monkeypatch.setattr(agent, "answers_by_tool", lambda: False)
    # The day's range reads the intraday cache, which is not under test here.
    monkeypatch.setattr(agent, "day_range_today", lambda *args, **kwargs: None)

    decision = agent._decide(_t._book(), [], {"AAA": 50.0, "BBB": 20.0}, menu=[_Candidate("CCC")])

    assert asked == [["AAA", "BBB", "CCC"]], "one read for the signals, the watchlist and the menu"
    rows = {line.split(" | ")[0].strip("| "): line for line in decision.prompt.splitlines()
            if line.startswith("| AAA |") or line.startswith("| BBB |")}
    assert "| UP: 50d +3.1%, 200d +12.4% | +4.2% / +15.0% | 1.6× normal | 2.3% |" in rows["AAA"]
    assert "| BBB | watched | $20.00 | — | — | — | — | — | — | never |" in rows["BBB"]


def test_the_candidates_fetch_reads_the_menus_cells_once_per_pass(monkeypatch):
    import backend.tests.test_agent as _t

    reads = []
    monkeypatch.setattr(agent, "_candidate_menu", lambda: [_Candidate("CRWV")])
    monkeypatch.setattr(agent.research, "is_charging", lambda: True)
    monkeypatch.setattr(agent.research, "get_price", lambda: 0.05)
    monkeypatch.setattr(
        trend, "describe_many",
        lambda tickers, today=None: reads.append(sorted(tickers)) or {"CRWV": _trend()},
    )
    context = agent.ToolContext(
        agent._fresh_budget(), book=_t._book(), prices={}, watchlist=[], max_watchlist=4,
        closed=[], day_ranges={},
    )

    first = context.fetch("candidates", {})
    second = context.fetch("candidates", {})

    assert "| CRWV | CRWV Inc | $50.00 | +1.0% | 5.0M | most active | UP: 50d +3.1%, 200d +12.4% |" in first
    assert first == second
    assert reads == [["CRWV"]], "the menu's history is read once, beside the menu"


def test_the_candidates_api_carries_the_same_cells_the_prompt_does(monkeypatch):
    """The research page shows the menu "exactly as the agent is given it", so
    the API renders the cells the way the prompt does, dashes included."""
    from backend.api.routes import watchlist as route

    monkeypatch.setattr(
        route.candidates, "fetch_candidates", lambda: [_Candidate("AAA"), _Candidate("BBB")]
    )
    monkeypatch.setattr(trend, "describe_many", lambda tickers, today=None: {"AAA": _trend()})

    rows = route.get_candidates()

    assert [r.ticker for r in rows] == ["AAA", "BBB"]
    assert (rows[0].trend, rows[0].month_quarter, rows[0].volume_vs_normal, rows[0].range_per_day) == (
        "UP: 50d +3.1%, 200d +12.4%", "+4.2% / +15.0%", "1.6× normal", "2.3%",
    )
    assert (rows[1].trend, rows[1].month_quarter, rows[1].volume_vs_normal, rows[1].range_per_day) == (
        "—", "—", "—", "—",
    )
