"""A pass acts, sees what happened, and is asked again.

Until 2026-09-12 an order left the pass and its result arrived, if at all, in
whatever pass came next. Research was worse: the pass ordered an analysis, ended,
and the wake meant to bring the answer back was dropped by a lock the same pass
still held — so of seventeen analyses finishing over two days, none led to a
pass. See the 2026-09-12 entry in JOURNEY.md.
"""
import pytest

from backend.services import agent, agent_book


def _book(cash=1000.0, holdings=None, budget=1000.0):
    return agent_book.Book(
        budget=budget,
        cash=cash,
        realized_pnl=0.0,
        holdings=[
            agent_book.Holding(ticker=t, quantity=q, avg_cost=c)
            for t, q, c in (holdings or [])
        ],
    )


@pytest.fixture
def quiet(monkeypatch):
    """Everything a pass touches that is not the subject of these tests."""
    monkeypatch.setattr(agent.quotes, "is_sandbox", lambda: True)
    monkeypatch.setattr(agent, "is_enabled", lambda: True)
    monkeypatch.setattr(agent.watchdog, "is_us_market_hours", lambda: True)
    monkeypatch.setattr(agent, "settle_pending", lambda: [])
    monkeypatch.setattr(agent, "_recent_signals", lambda: [])
    monkeypatch.setattr(agent.db, "get_recent_signals", lambda limit=200: [])
    monkeypatch.setattr(agent.agent_book, "closed_trades", lambda decisions=None: [])
    monkeypatch.setattr(agent, "_price_map", lambda _t: {})
    monkeypatch.setattr(agent.agent_book, "build_book", lambda price_lookup=None: _book())
    monkeypatch.setattr(agent.db, "get_watchlist", lambda: [])
    monkeypatch.setattr(agent, "_record_run", lambda run: None)
    monkeypatch.setattr(agent.research, "is_charging", lambda: False)


def _answers(monkeypatch, replies):
    """Drive _decide with a scripted list, recording the outcomes it was shown."""
    seen = []

    def fake_decide(*a, **kw):
        seen.append(list(kw.get("outcomes") or []))
        return replies.pop(0) if replies else ("done", [], [])

    monkeypatch.setattr(agent, "_decide", fake_decide)
    return seen


def test_a_pass_that_does_nothing_ends_after_one_turn(quiet, monkeypatch):
    """"No action, just tell me when to wake you" must not cost a second call."""
    seen = _answers(monkeypatch, [("holding", [], [])])

    agent.run_once()

    assert len(seen) == 1


def test_a_note_alone_does_not_earn_another_turn(quiet, monkeypatch):
    """A note is a message to the people who maintain this app, not an order
    with a result, so there is nothing to show the agent back."""
    seen = _answers(
        monkeypatch,
        [("asking", [{"side": "note", "reason": "I cannot see X"}], [])],
    )

    run = agent.run_once()

    assert run.notes == ["I cannot see X"]
    assert len(seen) == 1


def test_what_an_order_did_is_shown_on_the_next_turn(quiet, monkeypatch):
    """The point of the whole change."""
    monkeypatch.setattr(agent, "_untrack", lambda order, run: None)
    seen = _answers(
        monkeypatch,
        [
            ("dropping it", [{"side": "untrack", "ticker": "AAA"}], []),
            ("nothing more", [], []),
        ],
    )

    agent.run_once()

    assert seen[0] == [], "the first turn has nothing to report yet"
    assert seen[1] == ["AAA: no longer tracked."]


def test_research_runs_in_the_pass_and_its_verdict_comes_back(quiet, monkeypatch):
    ran = []
    monkeypatch.setattr(agent, "_commission_research", lambda order, run: None)
    monkeypatch.setattr(agent, "set_research_runner", agent.set_research_runner)
    agent.set_research_runner(lambda tickers: ran.append(list(tickers)))
    monkeypatch.setattr(
        agent.analysis_reader, "read", lambda t, on=None: f"{t} analysis: Overweight because ..."
    )
    seen = _answers(
        monkeypatch,
        [
            ("look at INTC", [{"side": "research", "ticker": "INTC"}], []),
            ("seen it", [], []),
        ],
    )

    agent.run_once()

    assert ran == [["INTC"]]
    # Prefixed with what it cost and when, because four probe runs read the
    # analysis and then treated it as just another analyst signal rather than
    # as their own spend from a moment earlier.
    assert "INTC analysis: Overweight because ..." in seen[1][0]
    # Claimed as the agent's own, and pointed at the table row that carries the
    # verdict — the result was appearing twice and being read as "the analyst".
    assert "YOU ordered minutes ago" in seen[1][0]
    assert "marked as yours in the signals table" in seen[1][0]


def test_a_failed_analysis_is_reported_rather_than_losing_the_pass(quiet, monkeypatch):
    monkeypatch.setattr(agent, "_commission_research", lambda order, run: None)

    def boom(tickers):
        raise RuntimeError("the GPU pool is down")

    agent.set_research_runner(boom)
    seen = _answers(
        monkeypatch,
        [
            ("look at INTC", [{"side": "research", "ticker": "INTC"}], []),
            ("oh well", [], []),
        ],
    )

    agent.run_once()

    assert "the GPU pool is down" in seen[1][0]


def test_with_no_runner_installed_the_agent_is_told_so(quiet, monkeypatch):
    """A deployment with no scheduler — a script, a test — must not hang or
    pretend the analysis happened."""
    monkeypatch.setattr(agent, "_commission_research", lambda order, run: None)
    agent.set_research_runner(None)
    seen = _answers(
        monkeypatch,
        [
            ("look at INTC", [{"side": "research", "ticker": "INTC"}], []),
            ("fine", [], []),
        ],
    )

    agent.run_once()

    assert "could not be analysed" in seen[1][0]


def test_an_answer_that_repeats_itself_ends_the_pass(quiet, monkeypatch):
    """A repeated order list is a loop, and this one moves money. Screening
    would only catch a second buy once the cash ran out, which is far too late."""
    untracked = []
    monkeypatch.setattr(agent, "_untrack", lambda order, run: untracked.append(order["ticker"]))
    same = ("again", [{"side": "untrack", "ticker": "AAA"}], [])
    _answers(monkeypatch, [same, same, same])

    agent.run_once()

    assert untracked == ["AAA"], "the repeat must not be executed twice"


def test_the_act_turns_are_bounded(quiet, monkeypatch):
    """Each turn can move real money, so the bound is what stops a pass that
    keeps finding one more thing to do."""
    n = 0

    def fake_decide(*a, **kw):
        nonlocal n
        n += 1
        # A different order every turn, so the repeat guard never fires.
        return ("more", [{"side": "untrack", "ticker": f"T{n}"}], [])

    monkeypatch.setattr(agent, "_untrack", lambda order, run: None)
    monkeypatch.setattr(agent, "_decide", fake_decide)

    agent.run_once()

    assert n == agent._MAX_ACT_TURNS


def test_the_read_budget_is_shared_across_act_turns(quiet, monkeypatch):
    """Six reads a pass must not become eighteen because the agent acted twice."""
    budgets = []

    def fake_decide(*a, **kw):
        budget = kw.get("budget")
        budgets.append(dict(budget))
        budget["reads"] -= 2  # as though it had read two analyses
        return ("more", [{"side": "untrack", "ticker": f"T{len(budgets)}"}], [])

    monkeypatch.setattr(agent, "_untrack", lambda order, run: None)
    monkeypatch.setattr(agent, "_decide", fake_decide)

    agent.run_once()

    assert budgets[0]["reads"] == agent._MAX_READS_PER_PASS
    assert budgets[1]["reads"] == agent._MAX_READS_PER_PASS - 2
    assert budgets[2]["reads"] == agent._MAX_READS_PER_PASS - 4
