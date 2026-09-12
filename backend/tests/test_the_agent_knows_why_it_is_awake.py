"""Why this pass is happening, and what the last pass left for this one.

Four different things start a pass — the agent's own chosen time, a move it
slept through, the last call before the close, a change to the app — and until
2026-09-12 it was told none of them. The labels existed, as log lines.

The note is the other half. The agent has no memory between passes, so
everything it works out is gone by the next one: the prompt carries prices and
positions, never conclusions.
"""
import datetime
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

    assert "**Why you are awake.** Something was noticed while you were away." in prompt


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
    assert "do not spend the note on them" in prompt.lower()


# --- the wiring ----------------------------------------------------------------


def test_every_wake_path_has_a_reason_the_agent_can_read():
    for label in ("Alarm", "Wakeup", "Event-driven", "Final", "Change"):
        assert scheduler._WOKE_BECAUSE.get(label), label


def test_the_reason_reaches_run_once(monkeypatch):
    seen = {}

    def fake_run_once(woke_because=None):
        seen["why"] = woke_because
        return types.SimpleNamespace(
            acted=False, rejected=[], failed=[], notes=[], next_wakeup=None, skipped=None,
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
