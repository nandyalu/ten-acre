"""The evening review: the agent reads its own day, speaks to the maintainer,
then to itself, and cannot act.

Pure. A fake ``generate_content`` stands in for Google's SDK, the pass and
review records live in memory (conftest), and memory notes are cleared around
each test. What is pinned: the window, what the prompt carries and what it
does not, the two forced calls and how the second follows the first, what a
revision does to memory and to the next pass's note, and what the next
prompt says about a note that came from the review.
"""
import datetime
import json
from types import SimpleNamespace

import pytest

from backend.database import db
from backend.database.models import AgentRun
from backend.services import agent, agent_book, reflection

UTC = datetime.timezone.utc
NOW = datetime.datetime(2026, 9, 25, 21, 30, tzinfo=UTC)  # Friday 5:30 PM ET


@pytest.fixture(autouse=True)
def clean_memory_notes():
    db.set_setting(agent._MEMORY_NOTES_KEY, "")
    yield
    db.set_setting(agent._MEMORY_NOTES_KEY, "")


def _run(ran_at, **kwargs):
    defaults = dict(
        reasoning="Holding cash; nothing actionable.",
        equity=10000.0, cash=10000.0, research_spent=0.0,
        wakeup_note="Watch INTC near $100.",
        next_wakeup=ran_at + datetime.timedelta(hours=18),
        woke_because="You asked to be woken now.",
    )
    return db.record_agent_run(ran_at=ran_at, **{**defaults, **kwargs})


def _part(text=None, thought=None, call=None):
    return SimpleNamespace(text=text, thought=thought, function_call=call)


def _call(name, **args):
    return SimpleNamespace(name=name, args=args, id=f"call-{name}")


def _response(parts, prompt=8000, candidates=300, thoughts=1200):
    return SimpleNamespace(
        candidates=[SimpleNamespace(content=SimpleNamespace(role="model", parts=parts))],
        usage_metadata=SimpleNamespace(
            prompt_token_count=prompt, candidates_token_count=candidates,
            thoughts_token_count=thoughts, cached_content_token_count=0,
        ),
    )


def _fake(report_args, revise_args):
    """A generate_content that answers ``report`` first and ``revise`` second,
    and records what it was allowed to call each time."""
    seen = []

    def generate(model, contents, config):
        allowed = list(config.tool_config.function_calling_config.allowed_function_names)
        seen.append({"allowed": allowed, "contents": list(contents)})
        if allowed == ["report"]:
            return _response([_part(thought=True, text="Reviewing the day."), _part(call=_call("report", **report_args))])
        return _response([_part(thought=True, text="Revising."), _part(call=_call("revise", **revise_args))])

    generate.seen = seen
    return generate


def _day(passes=None, **kwargs):
    since = NOW - datetime.timedelta(hours=24)
    return reflection.Day(
        now=NOW.astimezone(reflection.market_clock.US_MARKET_TZ), since=since,
        passes=passes if passes is not None else [], rules="- A rule.", **kwargs,
    )


# --- The window ---------------------------------------------------------------


def test_the_window_starts_at_the_last_review_or_a_day_ago(isolated_reflections):
    assert reflection.window_start(NOW) == NOW - reflection.WINDOW_FALLBACK

    db.record_reflection(ran_at=NOW - datetime.timedelta(hours=3), since=NOW - datetime.timedelta(hours=27))
    assert reflection.window_start(NOW) == NOW - datetime.timedelta(hours=3)


def test_gather_takes_every_pass_since_the_window_start_and_nothing_before(monkeypatch, isolated_reflections):
    early = _run(NOW - datetime.timedelta(hours=30), equity=9990.0)
    late_id = _run(NOW - datetime.timedelta(hours=2), equity=10010.0)
    skipped_id = _run(NOW - datetime.timedelta(hours=1), skipped="switched off")
    monkeypatch.setattr(reflection.db, "get_research_charges", lambda: [])
    monkeypatch.setattr(reflection.agent_book, "trade_history", lambda: [])
    monkeypatch.setattr(reflection.db, "get_recent_signals", lambda **kw: [])
    monkeypatch.setattr(reflection.db, "get_resolved_signals", lambda: [])
    monkeypatch.setattr(reflection.db, "get_cached_prices", lambda tickers: {})

    day = reflection.gather(NOW)

    assert [run.id for run in day.passes] == [late_id]
    assert skipped_id not in [run.id for run in day.passes], "a skipped pass never reached the model"
    assert early not in [run.id for run in day.passes]
    assert day.equity_before == 9990.0, "the equity before the window is the last pass before it"
    assert day.reviewed_before is False, "a first review has no earlier review to name"
    assert "Equity 24 hours ago" in reflection.build_prompt(day)["turn1"]
    assert day.rules.startswith("- "), "the fixed rules of the decision pass, so nothing in them is asked for"
    assert "portfolio manager" not in day.rules, "the identity paragraphs are not rules"


# --- The prompt ---------------------------------------------------------------


def test_the_prompt_carries_the_day_and_not_the_tools(isolated_reflections):
    run = AgentRun(
        id=41, ran_at=NOW - datetime.timedelta(hours=8), reasoning="Read INTC, then passed.",
        equity=10000.0, cash=10000.0, research_spent=0.05, wakeup_note="Watch INTC near $100.",
        next_wakeup=NOW + datetime.timedelta(hours=16), woke_because="You asked to be woken now.",
        turns=json.dumps([{
            "prompt": "p", "response": "r", "reasoning": "Reading INTC first.",
            "orders": [{"side": "buy", "ticker": "INTC", "quantity": 20, "order_type": "limit",
                        "limit_price": 100.5, "time_in_force": "gtc", "reason": "Pullback plan."}],
            "exchanges": [{"name": "read", "args": {"ticker": "INTC"}, "result": "..."}],
        }]),
        refusals=json.dumps([{"ticker": "NVDA", "side": "buy", "quantity": 5, "why": "not enough cash"}]),
    )
    day = _day(
        passes=[run], equity_before=9950.0, reviewed_before=True,
        memory=[{"text": "Old lesson", "written": None, "source": None},
                {"text": "Newer lesson", "written": "2026-09-24", "source": "reflection"}],
        last_notes=["Watch INTC near $100."],
    )

    prompts = reflection.build_prompt(day)
    turn1 = prompts["turn1"]

    assert "### Pass 41" in turn1
    assert "You asked to be woken now." in turn1
    assert "Fetched: read(ticker=INTC)" in turn1
    assert "Reading INTC first." in turn1
    assert "buy INTC 20 limit gtc at $100.50 — Pullback plan." in turn1
    assert "not enough cash" in turn1
    assert "Equity at your last review" in turn1 and "$9,950.00" in turn1
    assert turn1.count("It is ") == 1, "the clock line says the time once"
    assert "1. Old lesson (date unknown)" in turn1
    assert "2. Newer lesson (written 2026-09-24 in an evening review)" in turn1
    assert '> "Watch INTC near $100."' in turn1
    assert "## The rules you are given on every pass" in turn1
    assert "Do not ask for it." in turn1
    assert turn1.rstrip().endswith("Call report, with an empty list if nothing stood in your way.")
    # The review looks back; it is offered nothing to act on.
    assert "candidates" not in prompts["system"]
    assert "Price now is" not in turn1
    assert prompts["turn2"] == reflection.TURN2_PROMPT


def test_the_second_turn_carries_every_guard():
    turn2 = reflection.TURN2_PROMPT
    assert "A lesson from one trade is a hypothesis" in turn2
    assert "Judge a decision by what you knew when you made it" in turn2
    assert "A note that repeats a rule you are given on every pass is a slot lost" in turn2
    assert "Most reviews add nothing" in turn2
    assert "Do not write a memory note that works around something you reported" in turn2
    assert "Keeping everything as it is, is a valid answer" in turn2
    assert "INTC" not in turn2, "the example must not name a ticker in the record"


def test_a_pass_from_before_per_turn_reasoning_reads_its_response():
    run = AgentRun(
        id=24, ran_at=NOW - datetime.timedelta(hours=5), reasoning="last", equity=1.0, cash=1.0,
        turns=json.dumps([{"prompt": "**Why you are awake.** A rule spotted something.", "response": json.dumps({
            "reasoning": "Buying INTC on the fresh Overweight.",
            "orders": [{"side": "buy", "ticker": "INTC", "quantity": 4, "reason": "fresh signal"}],
        })}]),
    )
    turn1 = reflection.build_prompt(_day(passes=[run]))["turn1"]
    assert "Buying INTC on the fresh Overweight." in turn1
    assert "buy INTC 4 — fresh signal" in turn1
    assert "A rule spotted something." in turn1


def test_the_schemas_use_only_the_keywords_the_vendor_supports():
    allowed = {"type", "enum", "description", "properties", "required", "items", "anyOf"}

    def keys(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key not in ("properties",):
                    yield key
                yield from keys(value) if key != "properties" else (
                    k for prop in value.values() for k in keys(prop)
                )
        elif isinstance(node, list):
            for item in node:
                yield from keys(item)

    for declaration in (reflection.REPORT, reflection.REVISE):
        used = set(keys(declaration["parameters_json_schema"]))
        assert used <= allowed, used - allowed


# --- The conversation ---------------------------------------------------------


def test_the_two_turns_are_one_conversation_with_one_function_each():
    generate = _fake(
        {"notes": [{"kind": "missing_tool", "pass_id": 41, "what_was_missing": "x", "what_you_would_have_done": "y"}]},
        {"wakeup_note": "Rewritten.", "memory": [{"action": "add", "text": "A lesson, once."}]},
    )
    prompts = {"system": "sys", "turn1": "day", "turn2": "revise now"}

    out = reflection.converse(prompts, "gemini-3.5-flash-lite", generate)

    assert [s["allowed"] for s in generate.seen] == [["report"], ["revise"]]
    second = generate.seen[1]["contents"]
    assert len(second) == 3, "turn 1, the model's answer, then the response and turn 2"
    parts = second[2].parts
    assert parts[0].function_response.name == "report"
    assert parts[0].function_response.id == "call-report"
    assert "1 note(s) reached the maintainers" in parts[0].function_response.response["result"]
    assert parts[1].text == "revise now"
    assert out.report["notes"][0]["pass_id"] == 41
    assert out.revise["wakeup_note"] == "Rewritten."
    assert out.channel == "tool"
    assert out.prompt_tokens == 16000 and out.completion_tokens == 3000
    assert "Reviewing the day." in out.thinking and "Revising." in out.thinking


def test_a_prose_answer_is_kept_and_applies_nothing():
    def generate(model, contents, config):
        return _response([_part(text="I would rather write prose.")])

    out = reflection.converse({"system": "s", "turn1": "d", "turn2": "t"}, "m", generate)

    assert out.report is None and out.revise is None
    assert out.channel == "text"
    assert out.report_text == "I would rather write prose."
    assert reflection.apply(out.revise) == (None, [])


# --- Applying the revision ----------------------------------------------------


def test_a_revision_rewrites_memory_in_order_and_says_what_it_did():
    agent.add_memory_note("First, from a pass")
    agent.add_memory_note("Second, from a pass")

    note, applied = reflection.apply({
        "wakeup_note": "  Hold the line at $100.  ",
        "memory": [
            {"action": "rewrite", "index": 1, "text": "First, rewritten"},
            {"action": "remove", "index": 2},
            {"action": "add", "text": "Third, once"},
            {"action": "remove", "index": 9},
            {"action": "add", "text": "Third, once"},
        ],
    })

    assert note == "Hold the line at $100."
    assert agent.get_memory_notes() == ["First, rewritten", "Third, once"]
    entries = agent.get_memory_entries()
    assert entries[0]["source"] == "reflection" and entries[0]["written"] == agent._today_iso()
    assert entries[1]["source"] == "reflection"
    assert applied == [
        'Memory: rewrote note 1 to "First, rewritten".',
        "Memory: removed note 2.",
        'Memory: added "Third, once".',
        "Memory: could not remove note 9: no such note.",
        'Memory: not added, already there: "Third, once".',
    ]


def test_keeping_everything_is_a_valid_answer():
    agent.add_memory_note("Keep me")
    assert reflection.apply({}) == (None, [])
    assert agent.get_memory_notes() == ["Keep me"]


def test_a_memory_note_from_a_pass_carries_its_date_and_an_old_one_reads_as_undated():
    db.set_setting(agent._MEMORY_NOTES_KEY, json.dumps(["From before the change"]))
    agent.add_memory_note("From a pass today")
    entries = agent.get_memory_entries()
    assert entries[0] == {"text": "From before the change", "written": None, "source": None}
    assert entries[1] == {"text": "From a pass today", "written": agent._today_iso(), "source": "pass"}
    assert agent.get_memory_notes() == ["From before the change", "From a pass today"]
    assert agent.describe_memory_notes()[1:] == ["- From before the change", "- From a pass today"]


# --- Running it ---------------------------------------------------------------


def test_run_once_records_the_review_and_the_next_pass_reads_its_note(monkeypatch, isolated_reflections):
    _run(NOW - datetime.timedelta(hours=2), wakeup_note="The pass's own note.")
    generate = _fake(
        {"notes": []},
        {"wakeup_note": "The review's note.", "memory": [{"action": "add", "text": "Once, a lesson."}]},
    )
    monkeypatch.setattr(reflection.agent, "answers_by_tool", lambda: True)
    monkeypatch.setattr(reflection.analysis, "decision_model", lambda: "gemini-3.5-flash-lite")
    monkeypatch.setattr(reflection.db, "get_research_charges", lambda: [])
    monkeypatch.setattr(reflection.agent_book, "trade_history", lambda: [])
    monkeypatch.setattr(reflection.db, "get_recent_signals", lambda **kw: [])
    monkeypatch.setattr(reflection.db, "get_resolved_signals", lambda: [])
    monkeypatch.setattr(reflection.db, "get_cached_prices", lambda tickers: {})
    monkeypatch.setattr(reflection.llm_gemini, "client", lambda: SimpleNamespace(
        models=SimpleNamespace(generate_content=generate)
    ))

    review = reflection.run_once(NOW)

    assert review is not None and review.skipped is None
    assert review.passes == 1
    assert review.wakeup_note == "The review's note."
    assert review.memory_changed
    assert not review.notes
    row = isolated_reflections[0]
    assert row.channel == "tool" and row.model == "gemini-3.5-flash-lite"
    assert json.loads(row.notes) == []
    assert json.loads(row.applied) == ['Memory: added "Once, a lesson.".']
    assert "Call report" in row.prompt and row.turn2_prompt == reflection.TURN2_PROMPT
    # The next pass is shown the review's note in place of the pass's own,
    # and told where it came from.
    assert agent._last_pass_notes() == ["The review's note."]
    assert agent._last_notes_revised() is True
    lines = agent.describe_wakeup(
        "You asked to be woken now.", "The review's note.",
        last_pass_notes=["The review's note."], revised=True,
    )
    assert any("in your evening review" in line and "The review's note." in line for line in lines)


def test_a_review_older_than_the_newest_pass_no_longer_speaks(isolated_reflections):
    db.record_reflection(ran_at=NOW - datetime.timedelta(hours=5), since=NOW - datetime.timedelta(hours=29),
                         wakeup_note="Stale review note.")
    _run(NOW - datetime.timedelta(hours=1), wakeup_note="The newer pass's note.")

    assert agent._last_pass_notes() == ["The newer pass's note."]
    assert agent._last_notes_revised() is False


def test_no_pass_means_no_review_and_no_row(monkeypatch, isolated_reflections):
    monkeypatch.setattr(reflection.agent, "answers_by_tool", lambda: True)
    monkeypatch.setattr(reflection.db, "get_research_charges", lambda: [])
    monkeypatch.setattr(reflection.agent_book, "trade_history", lambda: [])
    monkeypatch.setattr(reflection.db, "get_recent_signals", lambda **kw: [])
    monkeypatch.setattr(reflection.db, "get_resolved_signals", lambda: [])
    monkeypatch.setattr(reflection.db, "get_cached_prices", lambda tickers: {})

    assert reflection.run_once(NOW) is None
    assert isolated_reflections == []


def test_a_failed_call_is_recorded_as_skipped_and_changes_nothing(monkeypatch, isolated_reflections):
    _run(NOW - datetime.timedelta(hours=2))
    agent.add_memory_note("Untouched")
    monkeypatch.setattr(reflection.agent, "answers_by_tool", lambda: True)
    monkeypatch.setattr(reflection.analysis, "decision_model", lambda: "m")
    monkeypatch.setattr(reflection.db, "get_research_charges", lambda: [])
    monkeypatch.setattr(reflection.agent_book, "trade_history", lambda: [])
    monkeypatch.setattr(reflection.db, "get_recent_signals", lambda **kw: [])
    monkeypatch.setattr(reflection.db, "get_resolved_signals", lambda: [])
    monkeypatch.setattr(reflection.db, "get_cached_prices", lambda tickers: {})

    def boom(*args, **kwargs):
        raise RuntimeError("503 UNAVAILABLE")

    monkeypatch.setattr(reflection, "converse", boom)

    review = reflection.run_once(NOW)

    assert review.skipped.startswith("The review call failed: 503")
    assert isolated_reflections[0].skipped == review.skipped
    assert agent.get_memory_notes() == ["Untouched"]
    assert agent._last_notes_revised() is False


def test_the_review_runs_on_the_tool_channel_only(monkeypatch, isolated_reflections):
    monkeypatch.setattr(reflection.agent, "answers_by_tool", lambda: False)
    assert reflection.run_once(NOW) is None


# --- The decision pass knows ---------------------------------------------------


def test_the_rules_tell_the_agent_a_review_may_rewrite_its_note_and_its_memory():
    assert "that review may rewrite this note before the next pass" in agent.SYSTEM_PROMPT
    assert "Your evening review may rewrite or remove one." in agent.SYSTEM_PROMPT_TOOL


def test_the_prompt_labels_a_note_that_came_from_the_review(isolated_reflections):
    _run(NOW - datetime.timedelta(hours=3), wakeup_note="From the pass.")
    db.record_reflection(ran_at=NOW - datetime.timedelta(hours=1), since=NOW - datetime.timedelta(days=1),
                         wakeup_note="From the review.")
    book = agent_book.Book(budget=1000.0, cash=1000.0, realized_pnl=0.0)

    prompt = agent.build_prompt(
        book, [], {}, wakeup_note="From the review.", last_pass_notes=["From the review."],
        woke_because="You asked to be woken now.", notes_revised=agent._last_notes_revised(),
    )

    assert '**A note you left yourself in your evening review, after your last pass:** "From the review."' in prompt
    assert "From the pass." not in prompt, "the review's note replaces the pass's note, never sits beside it"


# --- The post -----------------------------------------------------------------


def test_the_post_carries_the_notes_the_memory_and_the_new_note():
    review = reflection.Review(
        ran_at=NOW, since=NOW - datetime.timedelta(days=1), passes=3,
        notes=[{"kind": "missing_information", "pass_id": 41, "what_was_missing": "the day's low",
                "what_you_would_have_done": "waited for the pullback"}],
        wakeup_note="Hold at $100.",
        applied=['Memory: added "Once, a lesson."'],
    )
    embed = reflection.format_embed(review)
    fields = {f.name: f.value for f in embed.fields}
    assert "3 pass(es) reviewed" in embed.description
    assert "Pass 41, missing information: the day's low — it would have waited for the pullback" in fields["📝 The agent asked for something"]
    assert fields["Memory"] == 'Memory: added "Once, a lesson."'
    assert fields["Note for the next pass"] == "Hold at $100."
    assert review.memory_changed


def test_a_review_that_only_failed_to_change_memory_did_not_change_it():
    review = reflection.Review(ran_at=NOW, since=NOW, passes=1, applied=["Memory: could not remove note 9: no such note."])
    assert not review.memory_changed
