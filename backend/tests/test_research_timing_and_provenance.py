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

    def __init__(self, ticker, price=88.99, volume=17_400_000, change_pct=-1.2):
        self.ticker, self.price, self.volume, self.change_pct = ticker, price, volume, change_pct
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


def test_a_move_triggered_signal_says_so():
    """The one that matters most: the analyst was reacting to a move the price
    already holds, which is not the same as a scheduled opinion."""
    assert "moved unusually" in _line("move")
    assert "already holds" in _line("move")


@pytest.mark.parametrize(
    "trigger,phrase",
    [
        ("sweep", "normal morning schedule"),
        ("commissioned", "you asked to see it today"),
        ("earnings", "reports earnings soon"),
        ("manual", "Run by hand"),
    ],
)
def test_each_trigger_reads_as_plain_words(trigger, phrase):
    assert phrase in _line(trigger)


def test_an_unrecorded_trigger_says_nothing():
    """Rows written before the column existed have no honest value. Inventing
    one would put a guess in the record the agent reads as fact."""
    line = _line(None)
    assert "Run " not in line
    assert line.startswith("| AAA | 2026-09-03 | Buy |")
