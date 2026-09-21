"""Why this pass is happening, and what the last pass left for this one.

Four different things start a pass — the agent's own chosen time, a move it
slept through, the last call before the close, a change to the app — and until
2026-09-12 it was told none of them. The labels existed, as log lines.

The note is the other half. The agent has no memory between passes, so
everything it works out is gone by the next one: the prompt carries prices and
positions, never conclusions.
"""
import datetime
import json
import types

import pytest

from backend.services import agent, agent_book
from backend.tasks import scheduler


def _book():
    return agent_book.Book(budget=1000.0, cash=500.0, realized_pnl=0.0, holdings=[])


# --- the agent writes the note -------------------------------------------------


def test_the_note_is_read_out_of_the_answer():
    answer = '{"reasoning": "x", "next_wakeup": "2026-09-14T09:30", "next_wakeup_note": "waiting to see if CRWV holds $88", "orders": []}'

    assert agent.parse_wakeup_note(answer) == "waiting to see if CRWV holds $88"


def test_the_older_field_names_still_work():
    """A model that has seen one wording should not lose its note to the other."""
    assert agent.parse_wakeup_note('{"next_wakeup_reason": "a"}') == "a"
    assert agent.parse_wakeup_note('{"wakeup_reason": "b"}') == "b"


def test_a_fenced_answer_is_read_like_every_other():
    answer = '```json\n{"next_wakeup_note": "fenced"}\n```'

    assert agent.parse_wakeup_note(answer) == "fenced"


def test_no_note_is_none_rather_than_empty():
    """NULL means it named none, which differs from naming an empty one."""
    assert agent.parse_wakeup_note('{"reasoning": "x", "orders": []}') is None
    assert agent.parse_wakeup_note('{"next_wakeup_note": "   "}') is None
    assert agent.parse_wakeup_note("not json at all") is None


def test_a_long_note_is_capped():
    assert len(agent.parse_wakeup_note('{"next_wakeup_note": "%s"}' % ("x" * 900))) == agent._WAKEUP_NOTE_MAX_CHARS


# --- and reads it back next time -----------------------------------------------


def test_the_prompt_says_why_the_agent_is_awake():
    prompt = agent.build_prompt(
        _book(), [], {}, woke_because="Something was noticed while you were away.",
    )

    assert "## Why you are awake" in prompt
    assert "Something was noticed while you were away." in prompt


def test_the_note_comes_back_as_the_agents_own_words():
    prompt = agent.build_prompt(
        _book(), [], {}, wakeup_note="waiting to see if CRWV holds $88",
    )

    assert 'A note you left yourself last pass:** "waiting to see if CRWV holds $88"' in prompt
    # It must not read as an order. The book moved while the agent slept.
    assert "not an instruction" in prompt


def test_both_sit_under_the_clock_and_above_the_account():
    """It changes how everything below is read, so it goes first. Placement is
    measured here — see the probe-the-prompt skill."""
    prompt = agent.build_prompt(
        _book(), [], {}, woke_because="You asked to be woken now.", wakeup_note="n",
    )

    assert prompt.index("It is ") < prompt.index("Why you are awake") < prompt.index("Your account is")


def test_a_pass_with_neither_says_nothing():
    """A first-ever pass has no previous note and no reason, and must not be
    given an empty heading to reason about."""
    assert agent.describe_wakeup(None, None) == []


def test_the_rule_tells_the_agent_what_it_will_and_will_not_get():
    """The note is only worth writing if the agent knows what the next prompt
    already carries — otherwise it spends it restating the price."""
    prompt = agent.SYSTEM_PROMPT + agent.build_prompt(_book(), [], {})

    assert "next_wakeup_note" in prompt
    assert "You will not remember this pass" in prompt
    # **A worked example, not a prohibition.** Two of seven probe runs spent
    # the note restating the cash balance, which the next prompt shows anyway,
    # against a rule that only told them not to.
    assert "a wasted one" in prompt
    assert 'Not "my cash is low"' in prompt
    assert "ruled out HPE at a $59.83 entry" in prompt


# --- the wiring ----------------------------------------------------------------


def test_every_wake_path_has_a_reason_the_agent_can_read():
    for label in (
        "Alarm", "Wakeup", "Event-driven", "Final", "Change", "Stop fill", "Unguarded position",
    ):
        assert scheduler._WOKE_BECAUSE.get(label), label


def test_the_reason_reaches_run_once(monkeypatch):
    seen = {}

    def fake_run_once(woke_because=None):
        seen["why"] = woke_because
        return types.SimpleNamespace(
            acted=False, rejected=[], failed=[], notes=[], next_wakeup=None, skipped=None,
            unguarded=[],
        )

    monkeypatch.setattr(scheduler.agent, "run_once", fake_run_once)
    monkeypatch.setattr(scheduler, "_replace_wakeup_alarm", lambda when: None)

    import asyncio

    asyncio.run(scheduler._run_agent_pass_locked("Event-driven"))

    assert seen["why"] == scheduler._WOKE_BECAUSE["Event-driven"]


def test_the_note_is_read_from_the_previous_pass(monkeypatch):
    monkeypatch.setattr(
        agent.db, "get_agent_runs",
        lambda limit=1: [types.SimpleNamespace(wakeup_note="what I was waiting for")],
    )

    assert agent._last_wakeup_note() == "what I was waiting for"


def test_an_early_wake_still_shows_the_note(monkeypatch):
    """Arguably more useful then: it explains what the early wake interrupted.
    Read from the newest run rather than matched to the alarm that fired."""
    monkeypatch.setattr(
        agent.db, "get_agent_runs",
        lambda limit=1: [types.SimpleNamespace(wakeup_note="waiting for the open")],
    )
    prompt = agent.build_prompt(
        _book(), [], {},
        woke_because=scheduler._WOKE_BECAUSE["Event-driven"],
        wakeup_note=agent._last_wakeup_note(),
    )

    assert "You did not ask for this pass" in prompt
    assert "waiting for the open" in prompt


# --- a whole pass's notes, not only its last turn's (2026-09-17) --------------


def test_encoding_no_notes_is_null():
    assert agent._encode_wakeup_notes([]) is None


def test_encoding_one_note_is_plain_text():
    """The common case, and what every row before 2026-09-17 already holds —
    a pass that wrote one note must not suddenly look different in the DB."""
    assert agent._encode_wakeup_notes(["watching AAPL for a breakout"]) == "watching AAPL for a breakout"


def test_encoding_several_notes_is_a_json_list():
    encoded = agent._encode_wakeup_notes(["first note", "second note"])
    assert encoded == '["first note", "second note"]'


def test_reading_a_pre_2026_09_17_row_still_works(monkeypatch):
    """A plain-text row from before this existed has no brackets — read back
    as the one note it always was, no migration required."""
    monkeypatch.setattr(
        agent.db, "get_agent_runs",
        lambda limit=1: [types.SimpleNamespace(wakeup_note="what I was waiting for")],
    )

    assert agent._last_pass_notes() == ["what I was waiting for"]


def test_reading_several_notes_back_in_order(monkeypatch):
    monkeypatch.setattr(
        agent.db, "get_agent_runs",
        lambda limit=1: [types.SimpleNamespace(
            wakeup_note=agent._encode_wakeup_notes(["ruled out HPE at $59.83", "watching AVGO for $366.16"]),
        )],
    )

    assert agent._last_pass_notes() == ["ruled out HPE at $59.83", "watching AVGO for $366.16"]
    # The early-wake case still gets just the freshest one.
    assert agent._last_wakeup_note() == "watching AVGO for $366.16"


def test_no_runs_is_an_empty_list_not_none(monkeypatch):
    monkeypatch.setattr(agent.db, "get_agent_runs", lambda limit=1: [])

    assert agent._last_pass_notes() == []


def test_a_pass_with_several_notes_shows_them_all_next_wakeup():
    """Before this, only the *final* turn's note ever reached the next pass —
    a note written earlier in a multi-turn pass and not restated in its last
    answer was silently dropped at the pass boundary. See the 2026-09-17
    JOURNEY.md entry."""
    lines = agent.describe_wakeup(
        None, "watching AVGO for $366.16",
        last_pass_notes=["ruled out HPE at $59.83", "watching AVGO for $366.16"],
    )
    text = "\n".join(lines)

    assert "ruled out HPE at $59.83" in text
    assert "watching AVGO for $366.16" in text
    assert "Notes you left yourself last pass" in text
    assert text.index("ruled out HPE") < text.index("watching AVGO")


def test_last_pass_notes_and_this_pass_notes_are_both_shown_and_distinct():
    lines = agent.describe_wakeup(
        None, "b",
        last_pass_notes=["a", "b"],
        pass_notes=["c"],
    )
    text = "\n".join(lines)

    assert "Notes you left yourself last pass" in text
    assert "earlier in this same pass" in text
    assert text.index("Notes you left yourself last pass") < text.index("earlier in this same pass")


def test_a_caller_passing_only_note_still_gets_the_old_single_note_rendering():
    """Backward compatibility: a caller that never learned about
    `last_pass_notes` (probe_prompt.py, older tests) must render exactly as
    it did before this feature existed."""
    prompt = agent.build_prompt(_book(), [], {}, wakeup_note="waiting to see if CRWV holds $88")

    assert 'A note you left yourself last pass:** "waiting to see if CRWV holds $88"' in prompt


def test_a_pass_that_writes_several_notes_records_all_of_them(monkeypatch):
    """The wiring end to end: run_once must store what the whole pass wrote,
    not just whatever the final turn's answer happened to contain."""
    monkeypatch.setattr(agent.quotes, "is_sandbox", lambda: True)
    monkeypatch.setattr(agent.watchdog, "is_us_market_hours", lambda: True)
    monkeypatch.setattr(agent, "is_enabled", lambda: True)
    monkeypatch.setattr(agent, "settle_pending", lambda: [])
    monkeypatch.setattr(agent, "_recent_signals", lambda: [])
    monkeypatch.setattr(agent.db, "get_recent_signals", lambda limit=200: [])
    monkeypatch.setattr(agent.agent_book, "closed_trades", lambda decisions=None: [])
    monkeypatch.setattr(agent, "_price_map", lambda _t: {})
    monkeypatch.setattr(agent.agent_book, "build_book", lambda price_lookup=None: _book())
    monkeypatch.setattr(agent.db, "get_pending_agent_trades", lambda: [])
    monkeypatch.setattr(agent.research, "is_charging", lambda: False)

    recorded = {}
    monkeypatch.setattr(
        agent.db, "record_agent_run",
        lambda **kw: recorded.update(kw) or 1,
    )

    decisions = iter([
        agent.Decision(
            reasoning="turn1", accepted=[{"ticker": "ZZZ", "side": "cancel", "quantity": 0}],
            rejected=[], prompt="p1",
            response=json.dumps({"reasoning": "turn1", "next_wakeup_note": "first", "orders": []}),
            turns=[{"prompt": "p1", "response": "r1"}],
        ),
        agent.Decision(
            reasoning="turn2", accepted=[], rejected=[], prompt="p2",
            response=json.dumps({"reasoning": "turn2", "next_wakeup_note": "second", "orders": []}),
            turns=[{"prompt": "p2", "response": "r2"}],
        ),
    ])
    monkeypatch.setattr(agent, "_decide", lambda *a, **kw: next(decisions))

    agent.run_once()

    assert recorded["wakeup_note"] == agent._encode_wakeup_notes(["first", "second"])


# --- the record keeps the wake reason ------------------------------------------
#
# The agent has been told why it is awake since 2026-09-12 and the record kept
# nothing, so the Decisions page could say what the agent did and never why it
# was asked at all. See the 2026-09-21 JOURNEY.md entry.


def _quiet_pass(monkeypatch, decisions):
    """Everything run_once touches on the way to the model, stubbed out.

    Same set as test_a_pass_that_writes_several_notes_records_all_of_them
    above, which is the only other test that drives the whole of run_once.
    Returns the dict record_agent_run was called with.
    """
    monkeypatch.setattr(agent.quotes, "is_sandbox", lambda: True)
    monkeypatch.setattr(agent.watchdog, "is_us_market_hours", lambda: True)
    monkeypatch.setattr(agent, "is_enabled", lambda: True)
    monkeypatch.setattr(agent, "settle_pending", lambda: [])
    monkeypatch.setattr(agent, "_recent_signals", lambda: [])
    monkeypatch.setattr(agent.db, "get_recent_signals", lambda limit=200: [])
    monkeypatch.setattr(agent.agent_book, "closed_trades", lambda decisions=None: [])
    monkeypatch.setattr(agent, "_price_map", lambda _t: {})
    monkeypatch.setattr(agent.agent_book, "build_book", lambda price_lookup=None: _book())
    monkeypatch.setattr(agent.db, "get_pending_agent_trades", lambda: [])
    monkeypatch.setattr(agent.research, "is_charging", lambda: False)

    recorded = {}
    monkeypatch.setattr(agent.db, "record_agent_run", lambda **kw: recorded.update(kw) or 1)
    answers = iter(decisions)
    monkeypatch.setattr(agent, "_decide", lambda *a, **kw: next(answers))
    return recorded


def _answer(reasoning: str, orders=None) -> "agent.Decision":
    return agent.Decision(
        reasoning=reasoning, accepted=orders or [], rejected=[], prompt="p",
        response=json.dumps({"reasoning": reasoning, "orders": []}),
        turns=[{"prompt": "p", "response": "r"}],
    )


def test_the_reason_run_once_was_given_reaches_the_record(monkeypatch):
    recorded = _quiet_pass(monkeypatch, [_answer("nothing to do")])

    agent.run_once(scheduler._WOKE_BECAUSE["Stop fill"])

    assert recorded["woke_because"] == scheduler._WOKE_BECAUSE["Stop fill"]


def test_a_multi_turn_pass_records_the_one_wake_that_started_it(monkeypatch):
    """One pass is one row however many turns it ran to, and only the first
    turn is built with the reason. `_fold_in` must not lose it, and a later
    turn must not overwrite it with something else — a pass is not woken
    twice."""
    recorded = _quiet_pass(monkeypatch, [
        _answer("turn1", orders=[{"ticker": "ZZZ", "side": "cancel", "quantity": 0}]),
        _answer("turn2"),
    ])

    agent.run_once(scheduler._WOKE_BECAUSE["Final"])

    assert recorded["woke_because"] == scheduler._WOKE_BECAUSE["Final"]


def test_a_pass_nobody_labelled_records_nothing_rather_than_a_guess(monkeypatch):
    """NULL says "no reason on record", which is what a pass before 2026-09-21
    and a skipped pass both are. Inventing "the agent's own time" here would
    put a fact in the record that nobody observed."""
    recorded = _quiet_pass(monkeypatch, [_answer("nothing to do")])

    agent.run_once()

    assert recorded["woke_because"] is None


def test_the_events_route_hands_the_reason_to_the_page(monkeypatch):
    """End of the wire. The Decisions page prints this at the top of every
    turn block, so a field the route drops is a line the page cannot draw."""
    from fastapi.testclient import TestClient

    from backend.app import app
    from backend.database import db as database
    from backend.database.models import AgentRun as AgentRunRow

    monkeypatch.delenv("PUBLIC_MODE", raising=False)
    row = AgentRunRow(
        id=1,
        ran_at=datetime.datetime(2026, 9, 21, 13, 35),
        woke_because=scheduler._WOKE_BECAUSE["Unguarded position"],
    )
    monkeypatch.setattr(database, "get_agent_runs", lambda limit=None: [row])

    response = TestClient(app).get("/api/agent/events", params={"limit": 5})

    assert response.status_code == 200
    assert response.json()[0]["woke_because"] == scheduler._WOKE_BECAUSE["Unguarded position"]
