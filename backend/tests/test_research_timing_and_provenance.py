"""The agent commissions research and is told why each analysis ran.

Recorded in CLAUDE.md under "What the experiment is for": give the agent
proper tools inside reasonable restrictions. A stock can move enough in a day
to be worth taking the profit or cutting the loss, and an agent that cannot
ask to look until tomorrow cannot act on that.

Until 2026-09-08 a research order also chose *when* the answer arrived —
"now" ran it straight away, anything else waited for the next morning's
sweep. The sweep is gone: every commission runs right after the pass that
asked for it, so that choice no longer exists. See JOURNEY.md.
"""
import pytest

from backend.services import agent, agent_book


def _book(cash=1000.0):
    return agent_book.Book(budget=10_000.0, cash=cash, realized_pnl=0.0, holdings=[])


@pytest.fixture
def researchable(monkeypatch):
    monkeypatch.setattr(agent.research, "get_price", lambda: 0.05)
    monkeypatch.setattr(agent.research, "is_charging", lambda: True)
    monkeypatch.setattr(agent.db, "get_watchlist", lambda: [])
    monkeypatch.setattr(agent, "_max_watchlist", lambda: 30)


# --- when the answer arrives ---------------------------------------------------


@pytest.mark.parametrize("asked", [None, "now", "tomorrow", "whenever", ""])
def test_every_commission_runs_regardless_of_when(researchable, asked):
    """The field still parses harmlessly if an old habit or a stray prompt
    sends it, but it decides nothing any more — there is no "later" path
    left to route it to."""
    order = {"ticker": "NEW", "side": "research"}
    if asked is not None:
        order["when"] = asked

    accepted, rejected = agent.screen([order], _book(), {}, None, {"NEW"})

    assert rejected == []
    assert accepted[0]["ticker"] == "NEW"


class _Candidate:
    """The shape build_prompt reads, matching the stub in test_agent_research."""

    def __init__(self, ticker, price=88.99, volume=17_400_000, change_pct=-1.2, source="most active"):
        self.ticker, self.price, self.volume, self.change_pct = ticker, price, volume, change_pct
        self.source = source
        self.name = f"{ticker} Inc"

    @property
    def volume_m(self):
        return self.volume / 1_000_000


def test_the_prompt_no_longer_offers_a_choice_of_when(researchable):
    prompt = agent.build_prompt(
        _book(), [], {}, menu=[_Candidate("INTC")], price=0.05,
    )

    assert '"when"' not in prompt
    assert "runs inside this pass" in prompt
    # The shape is what the model copies, so a dropped field must not linger there.
    assert '{"ticker": "INTC", "side": "research", "reason": "why"}' in prompt


# --- why an analysis ran -------------------------------------------------------


class _Sig:
    ticker, signal_date, decision = "AAA", "2026-09-03", "Buy"
    entry_price = stop_loss = price_target = None
    win_probability = risk_reward = expected_value_r = None
    trigger = None


def _line(trigger):
    sig = _Sig()
    sig.trigger = trigger
    prompt = agent.build_prompt(_book(), [sig], {"AAA": 10.0})
    # The signals section became a table on 2026-09-10, so a row starts
    # with the pipe rather than a dash.
    return next(l for l in prompt.splitlines() if l.startswith("| AAA |"))


@pytest.mark.parametrize("trigger", ["move", "sweep", "commissioned", "earnings", "manual"])
def test_no_trigger_reaches_the_prompt_any_more(trigger):
    """**Removed 2026-09-21, because it had become one sentence on every row.**

    A `Why it ran` column told the agent whether the analyst was reacting to a
    move the price already held or answering a request. Nothing but the agent
    has triggered an analysis since 2026-09-12: 20 of the 21 signals this
    database has ever held say `commissioned`, and the one `move` row is dated
    the day before that change, so no other value can appear again.

    `Signal.trigger` is still recorded and the research page still shows it —
    this is about what the prompt spends a column on, not about the record.
    """
    line = _line(trigger)

    assert "Run " not in line
    for phrase in ("moved unusually", "morning schedule", "reports earnings", "by hand"):
        assert phrase not in line


def test_a_row_still_says_when_it_was_analysed():
    """The `Analysed` cell carries the in-pass marker now, so it must stay
    readable as a time first."""
    assert _line(None).startswith("| AAA | 2026-09-03 | Buy |")


# --- how far back the timing figure looks ---------------------------------------


def test_a_duration_older_than_the_window_is_not_averaged(monkeypatch):
    """**Added 2026-09-21.** The window was the newest 20 rows with no date
    bound, so on that date it reached back to the first analysis this database
    ever held and averaged two models together: `qwen-3.8-27b` at 2.3 to 4.4
    minutes and the current model at 3.2 to 9.1. The agent plans a wakeup
    around this number."""
    import datetime
    import types as _types

    from backend.services import analysis

    today = datetime.date.today()

    def row(days_ago, seconds):
        return _types.SimpleNamespace(
            signal_date=(today - datetime.timedelta(days=days_ago)).isoformat(),
            duration_seconds=seconds,
        )

    monkeypatch.setattr(
        "backend.database.db.get_recent_signals",
        lambda limit=10, **kw: [row(1, 540), row(6, 300), row(9, 120), row(40, 120)],
    )

    assert analysis.recent_durations() == [9.0, 5.0]


def test_a_quiet_week_says_nothing_rather_than_quoting_an_old_model(monkeypatch):
    """No recent run means no honest figure. The section then carries only what
    is running right now, if anything is."""
    from backend.services import agent

    assert agent.describe_analysis_timing([], {}) == []
    assert "An analysis takes about" not in "\n".join(
        agent.describe_analysis_timing([], {})
    )
