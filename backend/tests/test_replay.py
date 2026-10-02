"""A replay asks a recorded turn again and answers every fetch from the record."""
import pytest

from backend.services import agent, replay
from backend.tests.test_gemini_decides_by_function_call import _call, _decided, _Generate, _part, _response

TURN = {
    "channel": "tool",
    "prompt": "You manage a small account of real money.",
    "system_sha": "abc123",
    "exchanges": [
        {"name": "watchlist", "args": {}, "result": "You track 3 tickers."},
        {"name": "read", "args": {"ticker": "INTC"}, "result": "INTC said Overweight."},
        {"name": "rounds", "args": {}, "result": "ONE_ROUND_LEFT"},
    ],
}


def test_a_recorded_fetch_gets_its_recorded_result_and_an_unrecorded_one_reads_as_a_failure():
    fetch = replay.RecordedFetches(TURN["exchanges"])
    assert fetch("read", {"ticker": "INTC"}) == "INTC said Overweight."
    assert fetch("watchlist", {}) == "You track 3 tickers."
    assert fetch("read", {"ticker": "AAPL"}) == "read failed with an error in this app. Decide with what you have."
    assert [e["in_record"] for e in fetch.log] == [True, True, False]


def test_a_replay_sends_the_recorded_message_and_system_and_serves_the_record(monkeypatch):
    monkeypatch.setattr(replay.db, "get_system_prompt", lambda sha: "the fixed rules as they were" if sha == "abc123" else None)
    generate = _Generate(
        _response([_part(call=_call("read", ticker="INTC")), _part(call=_call("candidates"))]),
        _decided(),
    )
    result = replay.replay_turn(TURN, model="gemini-3.8-flash", generate=generate)
    model, contents, config = generate.calls[0]
    assert model == "gemini-3.8-flash"
    assert contents[0].parts[0].text == TURN["prompt"]
    assert config.system_instruction == "the fixed rules as they were"
    assert result["fetches_not_in_record"] == 1  # candidates was never fetched
    assert result["orders"][0]["ticker"] == "AAPL"
    assert result["system_sha"] == agent.system_sha("the fixed rules as they were")
    assert result["system_was_recorded"]


def test_a_json_channel_turn_cannot_be_replayed():
    with pytest.raises(ValueError, match="tool-channel"):
        replay.replay_turn({**TURN, "channel": "json"}, model="m")


def test_a_turn_keeps_the_hash_and_the_text_is_stored_once(monkeypatch):
    class Answer(str):
        channel = "tool"

    stored = {}
    monkeypatch.setattr(agent.db, "remember_system_prompt", lambda sha, text: stored.setdefault(sha, text))
    monkeypatch.setattr(agent, "_remembered_systems", set())
    turn = agent._turn("prompt", Answer('{"reasoning": "r", "orders": []}'))
    assert "system" not in turn
    assert stored == {turn["system_sha"]: agent.SYSTEM_PROMPT_TOOL}
