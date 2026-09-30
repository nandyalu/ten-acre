"""A pass the model never answers leaves a record, and the next pass ignores it.

On 2026-09-28 Google answered eight passes in a row with 503. Each pass raised
before it wrote a row, so the record showed a quiet open. Then an analysis the
agent ordered was stopped by a restart, and the next prompt would have said the
broker refused it, in quiv's words. See the 2026-09-28 entry in JOURNEY.md.
"""
import datetime

import pytest
from quiv import JobCancelledError

from backend.services import agent, agent_book
from backend.tasks import scheduler


def _book():
    return agent_book.Book(budget=1000.0, cash=1000.0, realized_pnl=0.0, holdings=[])


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
    monkeypatch.setattr(agent.research, "is_charging", lambda: False)
    recorded = []
    monkeypatch.setattr(agent, "_record_run", recorded.append)
    return recorded


def test_a_first_turn_with_no_answer_is_recorded_and_still_raises(quiet, monkeypatch):
    def unavailable(*a, **kw):
        raise RuntimeError("503 UNAVAILABLE")

    monkeypatch.setattr(agent, "_decide", unavailable)

    # The scheduler must still see a failure, so the backstop asks again.
    with pytest.raises(RuntimeError):
        agent.run_once("You asked to be woken now.")

    [run] = quiet
    assert run.unanswered
    assert "503 UNAVAILABLE" in run.skipped
    assert run.woke_because == "You asked to be woken now."


def test_a_later_turn_with_no_answer_keeps_what_the_earlier_turns_did(quiet, monkeypatch):
    monkeypatch.setattr(agent, "_untrack", lambda order, run: None)
    replies = [("dropping it", [{"side": "untrack", "ticker": "AAA"}], [])]

    def decide(*a, **kw):
        if replies:
            return replies.pop(0)
        raise RuntimeError("503 UNAVAILABLE")

    monkeypatch.setattr(agent, "_decide", decide)

    run = agent.run_once()

    assert quiet == [run]
    assert not run.unanswered
    assert run.reasoning == "dropping it"


def test_the_previous_pass_is_the_last_one_that_answered(isolated_agent_runs):
    now = datetime.datetime(2026, 9, 28, 14, 0, tzinfo=datetime.timezone.utc)
    agent.db.record_agent_run(now, reasoning="decided", wakeup_note="watch INTC")
    agent.db.record_agent_run(
        now + datetime.timedelta(minutes=5), skipped="The model did not answer", unanswered=True,
    )

    assert agent._last_pass_notes() == ["watch INTC"]


def test_a_research_stopped_by_a_shutdown_says_so_plainly(monkeypatch):
    def cancelled(*a, **kw):
        raise JobCancelledError("call_on_main(<function run_analyses>) was stopped")

    monkeypatch.setattr(scheduler, "call_on_main", cancelled)

    failures = scheduler._research_for_agent(["INTC"])

    assert "shut down" in failures["INTC"]
    assert "call_on_main" not in failures["INTC"]


def test_a_failed_research_is_not_labelled_a_broker_refusal():
    run = agent.AgentRun()
    run.failed = [
        ({"side": "research", "ticker": "INTC", "quantity": 0}, "stopped"),
        ({"side": "buy", "ticker": "AAA", "quantity": 1}, "unsettled cash"),
    ]

    import json
    by_side = {e["side"]: e["refused_by"] for e in json.loads(agent._refusals_json(run))}

    assert by_side == {"research": "analysis", "buy": "broker"}
