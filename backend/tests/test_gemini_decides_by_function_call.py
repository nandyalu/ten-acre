"""On Gemini the agent answers by calling ``decide``, through Google's SDK,
and fetches what it needs on the way.

Pure: a fake ``generate_content`` stands in for the SDK, so nothing here
needs a key or a network. What is pinned is the call the app makes (which
functions, forced how, with what thinking), what it makes of the reply, and
how a fetch round goes back into the conversation.
"""
import json
from types import SimpleNamespace

import pytest

from backend.services import agent, analysis, decision_schema, llm_gemini, llm_throttle
from backend.tests.test_agent_run_usage import _Llm, _Message

ARGS = {
    "reasoning": "Hold INTC; nothing new.",
    "next_wakeup": "2026-09-18T09:30",
    "next_wakeup_note": "INTC stop at 105, target 125.",
    "orders": [
        {"side": "buy", "ticker": "AAPL", "quantity": 2.0, "order_type": "limit",
         "limit_price": 236.0, "time_in_force": "gtc", "reason": "breakout"},
    ],
}


def _part(text=None, thought=None, call=None):
    return SimpleNamespace(text=text, thought=thought, function_call=call)


def _call(name, **args):
    return SimpleNamespace(name=name, args=args)


def _response(parts, prompt=4271, candidates=95, thoughts=1500):
    return SimpleNamespace(
        candidates=[SimpleNamespace(content=SimpleNamespace(role="model", parts=parts))],
        usage_metadata=SimpleNamespace(
            prompt_token_count=prompt, candidates_token_count=candidates,
            thoughts_token_count=thoughts,
        ),
    )


def _decided(args=ARGS, thought="The market is shut, so only the wakeup matters."):
    return _response([_part(text=thought, thought=True), _part(call=_call("decide", **args))])


class _Generate:
    """The SDK's ``models.generate_content``, recording what it was asked and
    answering from a queue, one response per round."""

    def __init__(self, *responses, raises=None):
        self.responses, self.raises, self.calls = list(responses), raises, []
        self.responses_sent = []

    def __call__(self, model, contents, config):
        # The list the loop appends to is the same object across rounds, so
        # copy it now or every recorded call shows the final conversation.
        self.calls.append((model, list(contents), config))
        if self.raises is not None:
            raise self.raises
        response = self.responses.pop(0)
        self.responses_sent.append(response)
        return response


def _allowed(config) -> list[str]:
    return config.tool_config.function_calling_config.allowed_function_names


def _decide(generate, **kwargs):
    return llm_gemini.decide(
        "sys", "user", decision_schema.DECIDE, model="gemini-3.5-flash-lite",
        generate=generate, **kwargs,
    )


# --- one call ------------------------------------------------------------------


def test_the_arguments_come_back_as_the_json_the_parser_reads():
    reply = _decide(_Generate(_decided()))

    reasoning, orders = agent.parse_decision(reply.content)
    assert reasoning == "Hold INTC; nothing new."
    assert orders == [{"side": "buy", "ticker": "AAPL", "quantity": 2, "order_type": "limit",
                       "limit_price": 236, "time_in_force": "gtc", "reason": "breakout"}]
    assert agent.parse_wakeup_note(reply.content) == "INTC stop at 105, target 125."
    assert reply.thinking == "The market is shut, so only the wakeup matters."
    assert (reply.prompt_tokens, reply.completion_tokens) == (4271, 95 + 1500)
    assert reply.exchanges == []


def test_the_call_forces_decide_and_nothing_else_without_fetches():
    generate = _Generate(_decided())

    _decide(generate)

    model, contents, config = generate.calls[0]
    assert model == "gemini-3.5-flash-lite"
    assert [p.text for p in contents[0].parts] == ["user"]
    assert contents[0].role == "user"
    assert config.system_instruction == "sys"
    assert config.tool_config.function_calling_config.mode.name == "ANY"
    assert _allowed(config) == ["decide"]
    declared = config.tools[0].function_declarations
    assert [d.name for d in declared] == ["decide"]
    assert declared[0].parameters_json_schema == decision_schema.DECIDE["parameters_json_schema"]


def test_thinking_is_asked_for_at_the_configured_level(monkeypatch):
    monkeypatch.setitem(llm_gemini.DEFAULT_CONFIG, "google_thinking_level", "high")
    generate = _Generate(_decided())

    _decide(generate)

    thinking = generate.calls[0][2].thinking_config
    assert thinking.include_thoughts is True
    # The SDK turns the string into its enum, whose value is upper-case.
    assert str(thinking.thinking_level.value).lower() == "high"


def test_no_level_means_no_thinking_config(monkeypatch):
    """gemini-3.5-flash-lite thinks only at a stated level; an empty config
    would change nothing and is not sent."""
    monkeypatch.setitem(llm_gemini.DEFAULT_CONFIG, "google_thinking_level", None)
    generate = _Generate(_decided())

    _decide(generate)

    assert generate.calls[0][2].thinking_config is None


def test_a_reply_without_the_call_is_read_as_text():
    """mode ANY should make this impossible. If it happens anyway, the text
    goes to the tolerant parser, which is where every answer used to go."""
    generate = _Generate(_response([_part(text='{"reasoning": "fine", "orders": []}')]))

    reply = _decide(generate)

    assert agent.parse_decision(reply.content) == ("fine", [])
    assert reply.thinking is None


def test_whole_number_floats_become_ints_and_prices_keep_their_decimals():
    assert llm_gemini.plain(
        {"quantity": 2.0, "stop": 105.5, "orders": [{"limit_price": 240.0, "reason": "r"}]}
    ) == {"quantity": 2, "stop": 105.5, "orders": [{"limit_price": 240, "reason": "r"}]}


# --- fetch rounds --------------------------------------------------------------


def _fetching(budget=None, results=None):
    """A fetch callback that records what it was asked and answers from a map."""
    asked = []

    def fetch(name, args):
        asked.append((name, args))
        return (results or {}).get(name, f"{name} result")

    return fetch, asked, (budget if budget is not None else {"fetches": 12, "rounds": 5})


def test_a_fetch_runs_and_its_result_goes_back_under_the_user_role():
    """``user``, not ``tool``: the API refused ``tool`` on the first live pass
    (2026-09-17), and the fallback carried the pass."""
    fetch, asked, budget = _fetching(results={"read": "NVDA's analysis of 2026-09-08 said Hold"})
    generate = _Generate(
        _response([_part(text="Let me read it first.", thought=True),
                   _part(call=_call("read", ticker="NVDA", date="2026-09-08"))]),
        _decided(thought="Read it; holding."),
    )

    reply = _decide(generate, fetches=decision_schema.FETCHES, fetch=fetch, budget=budget)

    assert asked == [("read", {"ticker": "NVDA", "date": "2026-09-08"})]
    assert reply.exchanges == [
        {"name": "read", "args": {"ticker": "NVDA", "date": "2026-09-08"},
         "result": "NVDA's analysis of 2026-09-08 said Hold"},
    ]
    assert json.loads(reply.content)["reasoning"] == "Hold INTC; nothing new."
    # Both rounds' thinking, in order, and both rounds' tokens.
    assert reply.thinking == "Let me read it first.\n\nRead it; holding."
    assert reply.prompt_tokens == 4271 * 2
    # The second round carries the whole conversation: the prompt, the model's
    # own turn exactly as it came back, and the fetch result under `user`.
    _, contents, config = generate.calls[1]
    assert [c.role for c in contents] == ["user", "model", "user"]
    assert contents[1] is generate.responses_sent[0].candidates[0].content
    response_part = contents[2].parts[0]
    assert response_part.function_response.name == "read"
    assert response_part.function_response.response == {
        "result": "NVDA's analysis of 2026-09-08 said Hold"
    }
    assert budget == {"fetches": 11, "rounds": 4}


def test_every_fetch_is_offered_while_the_allowance_lasts_and_only_decide_after():
    fetch, _, budget = _fetching(budget={"fetches": 1, "rounds": 3})
    generate = _Generate(
        _response([_part(call=_call("candidates"))]),
        _decided(),
    )

    _decide(generate, fetches=decision_schema.FETCHES, fetch=fetch, budget=budget)

    assert _allowed(generate.calls[0][2]) == ["decide", *decision_schema.FETCH_NAMES]
    assert _allowed(generate.calls[1][2]) == ["decide"]
    assert budget == {"fetches": 0, "rounds": 2}


def test_several_fetches_in_one_round_cost_one_round():
    fetch, asked, budget = _fetching()
    generate = _Generate(
        _response([_part(call=_call("read", ticker="NVDA")), _part(call=_call("fundamentals", ticker="NVDA"))]),
        _decided(),
    )

    reply = _decide(generate, fetches=decision_schema.FETCHES, fetch=fetch, budget=budget)

    assert [name for name, _ in asked] == ["read", "fundamentals"]
    assert budget == {"fetches": 10, "rounds": 4}
    _, contents, _ = generate.calls[1]
    assert [p.function_response.name for p in contents[2].parts] == ["read", "fundamentals"]
    assert [e["name"] for e in reply.exchanges] == ["read", "fundamentals"]


def test_a_fetch_past_the_allowance_is_told_so_rather_than_run():
    fetch, asked, budget = _fetching(budget={"fetches": 1, "rounds": 3})
    generate = _Generate(
        _response([_part(call=_call("read", ticker="NVDA")), _part(call=_call("read", ticker="AAPL"))]),
        _decided(),
    )

    reply = _decide(generate, fetches=decision_schema.FETCHES, fetch=fetch, budget=budget)

    assert asked == [("read", {"ticker": "NVDA"})]
    assert reply.exchanges[1] == {"name": "read", "args": {"ticker": "AAPL"}, "result": llm_gemini.ALLOWANCE_SPENT}


def test_the_model_is_told_when_rounds_run_out():
    """A round is one Google request that resends the whole conversation, so
    rounds stay few (6 since 2026-09-19), and a model given a number plans
    around it. So it is told when one round is left and when none are, in the
    last function response of the round, where it reads results, under a key
    of its own so the result stays verbatim. The record gets a row too."""
    fetch, _, budget = _fetching(budget={"fetches": 30, "rounds": 2})
    generate = _Generate(
        _response([_part(call=_call("watchlist"))]),
        _response([_part(call=_call("read", ticker="NVDA"))]),
        _decided(),
    )

    reply = _decide(generate, fetches=decision_schema.FETCHES, fetch=fetch, budget=budget)

    first = generate.calls[1][1][2].parts[0].function_response.response
    assert first == {"result": "watchlist result", "rounds": llm_gemini.ONE_ROUND_LEFT}
    second = generate.calls[2][1][4].parts[0].function_response.response
    assert second == {"result": "read result", "rounds": llm_gemini.NO_ROUNDS_LEFT}
    assert _allowed(generate.calls[2][2]) == ["decide"]
    assert [e["name"] for e in reply.exchanges] == ["watchlist", "rounds", "read", "rounds"]
    assert reply.exchanges[1]["result"] == llm_gemini.ONE_ROUND_LEFT
    assert budget == {"fetches": 28, "rounds": 0}


def test_a_round_with_plenty_left_carries_no_warning():
    fetch, _, budget = _fetching(budget={"fetches": 30, "rounds": 6})
    generate = _Generate(_response([_part(call=_call("watchlist"))]), _decided())

    reply = _decide(generate, fetches=decision_schema.FETCHES, fetch=fetch, budget=budget)

    assert generate.calls[1][1][2].parts[0].function_response.response == {"result": "watchlist result"}
    assert [e["name"] for e in reply.exchanges] == ["watchlist"]


def test_a_decide_in_the_same_round_as_a_fetch_is_not_carried_out():
    """The rule the read side has always had, said in the function's own
    response so the model reads it where it looks for results."""
    fetch, _, budget = _fetching()
    generate = _Generate(
        _response([_part(call=_call("read", ticker="NVDA")), _part(call=_call("decide", **ARGS))]),
        _decided(args={**ARGS, "reasoning": "Decided after reading."}),
    )

    reply = _decide(generate, fetches=decision_schema.FETCHES, fetch=fetch, budget=budget)

    assert json.loads(reply.content)["reasoning"] == "Decided after reading."
    assert reply.exchanges[-1]["name"] == "decide"
    assert reply.exchanges[-1]["result"] == llm_gemini.DECIDE_WITH_FETCH
    _, contents, _ = generate.calls[1]
    assert [p.function_response.name for p in contents[2].parts] == ["read", "decide"]


def test_the_rounds_are_bounded_by_the_turn_allowance():
    """Two rounds of fetching, then decide is the only function left."""
    fetch, asked, budget = _fetching(budget={"fetches": 10, "rounds": 2})
    generate = _Generate(
        _response([_part(call=_call("read", ticker="A"))]),
        _response([_part(call=_call("read", ticker="B"))]),
        _decided(),
    )

    _decide(generate, fetches=decision_schema.FETCHES, fetch=fetch, budget=budget)

    assert [args["ticker"] for _, args in asked] == ["A", "B"]
    assert _allowed(generate.calls[2][2]) == ["decide"]
    assert budget == {"fetches": 8, "rounds": 0}


def test_the_pass_allowance_is_thirty_fetches_across_six_rounds_and_reads_keep_theirs():
    """The first live probe hit the old six-fetch cap after three reads, since
    the three table fetches spend from the same pot; 12 across 5 then read as
    a budget to save (2026-09-19: "I need to be efficient"). A fetch never
    reaches Google and a round does, so the fetches are generous and the
    rounds few, and the rule calls it a ceiling. The JSON channel's read loop
    keeps its own numbers."""
    budget = agent._fresh_budget()

    assert (budget["fetches"], budget["rounds"]) == (30, 6)
    assert (budget["reads"], budget["turns"]) == (6, 3)
    assert "The ceiling is 30 fetches across 6 rounds" in agent.SYSTEM_PROMPT_TOOL
    assert "it exists to stop a loop, not to be saved" in agent.SYSTEM_PROMPT_TOOL
    assert "allowance runs out" not in agent.SYSTEM_PROMPT_TOOL
    assert "read up to 6 analyses before deciding" in agent.SYSTEM_PROMPT


# --- the pass ------------------------------------------------------------------


def _on_gemini(monkeypatch, generate):
    monkeypatch.setattr(agent, "answers_by_tool", lambda: True)
    monkeypatch.setattr(analysis, "get_model", lambda: "gemini-3.5-flash-lite")
    monkeypatch.setattr(
        llm_gemini, "client",
        lambda: SimpleNamespace(models=SimpleNamespace(generate_content=generate)),
    )


def test_ask_goes_through_decide_with_the_tool_system_message(monkeypatch):
    generate = _Generate(_decided())
    _on_gemini(monkeypatch, generate)

    answer = agent._ask("a prompt")

    assert json.loads(answer)["reasoning"] == "Hold INTC; nothing new."
    assert answer.thinking == "The market is shut, so only the wakeup matters."
    assert (answer.prompt_tokens, answer.completion_tokens) == (4271, 1595)
    assert answer.exchanges == []
    assert answer.channel == "tool"
    assert agent._turn("a prompt", answer)["channel"] == "tool"
    _, contents, config = generate.calls[0]
    assert contents[0].parts[0].text == "a prompt"
    assert config.system_instruction == agent.SYSTEM_PROMPT_TOOL
    # No fetch context, no fetches offered.
    assert _allowed(config) == ["decide"]


def test_ask_with_a_fetch_context_runs_the_fetch_and_records_it(monkeypatch):
    generate = _Generate(
        _response([_part(call=_call("track_record"))]),
        _decided(),
    )
    _on_gemini(monkeypatch, generate)
    import backend.tests.test_agent as _t
    tools = agent.ToolContext(
        agent._fresh_budget(), book=_t._book(), prices={}, watchlist=[], max_watchlist=30,
        closed=[], day_ranges={},
    )

    answer = agent._ask("a prompt", tools)

    assert answer.exchanges == [{"name": "track_record", "args": {}, "result": "No closed trades yet."}]
    turn = agent._turn("a prompt", answer)
    assert turn["exchanges"] == answer.exchanges
    assert _allowed(generate.calls[0][2]) == ["decide", *decision_schema.FETCH_NAMES]


def test_a_failed_decide_call_falls_back_to_the_text_channel(monkeypatch):
    """A pass must not be lost to a client shape or a schema the vendor
    refuses. The fallback is the path every other provider uses."""
    _on_gemini(monkeypatch, _Generate(raises=RuntimeError("schema refused")))
    message = _Message('{"orders": []}', usage={"input_tokens": 10, "output_tokens": 2})
    monkeypatch.setattr(analysis, "_quick_think_llm", lambda: _Llm(message=message))

    answer = agent._ask("a prompt")

    assert answer == '{"orders": []}'
    assert answer.prompt_tokens == 10
    assert answer.exchanges == []
    assert answer.channel == "text-fallback"
    assert agent._turn("a prompt", answer)["channel"] == "text-fallback"


def test_a_fallback_is_kept_in_the_record_and_named_in_the_discord_post():
    """A fallback turn was otherwise identical to a tool turn that fetched
    nothing, and the reason lived only in the log (2026-09-17)."""
    fallen = {"prompt": "p", "response": "{}", "thinking": None, "channel": "text-fallback"}
    fine = {"prompt": "p", "response": "{}", "thinking": None, "channel": "tool"}

    assert agent._turns_worth_keeping([fallen]) is True
    assert agent._turns_worth_keeping([fine]) is False
    assert agent._turns_worth_keeping([{"prompt": "p", "response": "{}"}]) is False

    embed = agent.format_run_embed(agent.AgentRun(reasoning="held", turns=[fallen, fine]))
    warning = next((f for f in embed.fields if "text fallback" in f.name), None)
    assert warning is not None
    assert "1 of 2 turn(s)" in warning.value
    assert not any(
        "text fallback" in f.name
        for f in agent.format_run_embed(agent.AgentRun(reasoning="held", turns=[fine])).fields
    )


def test_a_bare_string_answer_counts_as_the_json_channel():
    """Every test fake that returns a plain string, and every turn before
    2026-09-17, is the JSON channel."""
    assert agent._turn("p", '{"orders": []}')["channel"] == "json"
    assert agent._Answer("{}").channel == "json"


def test_the_fallback_carries_the_json_shape_the_tool_prompt_lacks(monkeypatch):
    """The first live fallback (2026-09-17) answered without a `reasoning`
    key: the tool-channel prompt shows no JSON example, so the text answer
    had nothing to copy. The shape now travels with the fallback."""
    _on_gemini(monkeypatch, _Generate(raises=RuntimeError("Role 'tool' is not supported")))
    seen = []

    class _Llm:
        client = None

        def invoke(self, messages):
            seen.append(messages)
            return _Message('{"reasoning": "fine", "orders": []}',
                            usage={"input_tokens": 10, "output_tokens": 2})
    monkeypatch.setattr(analysis, "_quick_think_llm", lambda: _Llm())

    agent._ask("the tool prompt")

    system, human = seen[0]
    assert system == ("system", agent.SYSTEM_PROMPT)
    assert human[1].startswith("the tool prompt\n\n")
    assert '"reasoning": "one or two sentences"' in human[1]
    assert '"order_type": "market"' in human[1]


def test_the_days_limit_is_not_swallowed_by_the_fallback(monkeypatch):
    _on_gemini(monkeypatch, _Generate(raises=llm_throttle.DailyLimitReached("spent")))

    with pytest.raises(llm_throttle.DailyLimitReached):
        agent._ask("a prompt")


# --- the fetch context ---------------------------------------------------------


def _context(**overrides):
    import backend.tests.test_agent as _t
    base = dict(book=_t._book(), prices={}, watchlist=[], max_watchlist=30, closed=[], day_ranges={})
    return agent.ToolContext(agent._fresh_budget(), **(base | overrides))


def test_a_fetch_that_fails_returns_its_failure_as_text(monkeypatch):
    def boom(*_):
        raise RuntimeError("vendor down")
    monkeypatch.setattr(agent.fundamentals, "describe", boom)

    assert _context().fetch("fundamentals", {"ticker": "AAPL"}) == (
        "fundamentals failed with an error in this app. Decide with what you have."
    )
    assert _context().fetch("no_such", {}) == "There is no fetch named no_such."


def test_candidates_are_screened_once_per_pass_and_define_the_universe(monkeypatch):
    calls = []
    menu = [SimpleNamespace(ticker="CRWV", name="CoreWeave", price=95.0, volume=12_000_000.0,
                            volume_m=12.0, change_pct=1.5, source="most active")]
    monkeypatch.setattr(agent, "_candidate_menu", lambda: calls.append(1) or menu)
    monkeypatch.setattr(agent.research, "is_charging", lambda: True)
    context = _context()

    first = context.fetch("candidates", {})
    second = context.fetch("candidates", {})

    assert "CRWV: CoreWeave at $95.00, +1.5% today, 12.0M shares traded, via most active" in first
    assert first == second
    assert calls == [1], "the vendor screen ran once for the pass"
    assert {c.ticker for c in context.budget["menu"]} == {"CRWV"}


def test_an_empty_screen_says_so(monkeypatch):
    monkeypatch.setattr(agent, "_candidate_menu", lambda: [])
    monkeypatch.setattr(agent.research, "is_charging", lambda: True)

    assert _context().fetch("candidates", {}).startswith("No candidate passed the screen")


def test_the_watchlist_fetch_is_the_prompts_own_table(monkeypatch):
    import backend.tests.test_agent as _t
    monkeypatch.setattr(agent.db, "get_recent_signals", lambda *a, **k: [])
    book = _t._book()
    text = _context(book=book, watchlist=["AAPL", "INTC"], prices={"AAPL": 236.2}).fetch("watchlist", {})

    assert "You track 2 of at most 30 tickers." in text
    assert "| AAPL | watched | $236.20 |" in text
    assert "| INTC | watched | unavailable |" in text
    assert _context().fetch("watchlist", {}) == "You track nothing."


# --- which channel -------------------------------------------------------------


def test_the_google_provider_answers_by_tool(monkeypatch):
    monkeypatch.delenv("AGENT_ANSWER_CHANNEL", raising=False)
    monkeypatch.setitem(analysis.DEFAULT_CONFIG, "llm_provider", "google")

    assert agent.answers_by_tool() is True


def test_every_other_provider_keeps_the_json_channel(monkeypatch):
    monkeypatch.delenv("AGENT_ANSWER_CHANNEL", raising=False)
    for provider in ("ollama", "openai_compatible", "anthropic", ""):
        monkeypatch.setitem(analysis.DEFAULT_CONFIG, "llm_provider", provider)
        assert agent.answers_by_tool() is False, provider


def test_the_json_channel_can_be_forced_without_a_rebuild(monkeypatch):
    monkeypatch.setitem(analysis.DEFAULT_CONFIG, "llm_provider", "google")
    monkeypatch.setenv("AGENT_ANSWER_CHANNEL", "json")

    assert agent.answers_by_tool() is False


# --- the prompt on each channel ------------------------------------------------


def test_the_tool_prompt_ends_by_naming_decide_and_carries_no_json_example():
    import backend.tests.test_agent as _t

    by_tool = agent.build_prompt(_t._book(), [], {}, answer_by_tool=True)
    by_json = agent.build_prompt(_t._book(), [], {}, answer_by_tool=False)

    assert "Answer in this shape" not in by_tool
    assert '"reasoning": "one or two sentences"' not in by_tool
    assert by_tool.rstrip().endswith("leave the list empty to hold everything.")
    assert "**Answer by calling `decide`.**" in by_tool
    assert "Answer in this shape" in by_json
    assert '"reasoning": "one or two sentences"' in by_json


def test_the_tool_prompt_offers_the_candidates_fetch_instead_of_the_menu():
    import backend.tests.test_agent as _t
    menu = [SimpleNamespace(ticker="CRWV", name="CoreWeave", price=95.0, volume=12_000_000.0,
                            volume_m=12.0, change_pct=None, source="most active")]

    by_tool = agent.build_prompt(_t._book(), [], {}, menu=menu, answer_by_tool=True)
    by_json = agent.build_prompt(_t._book(), [], {}, menu=menu, answer_by_tool=False)

    assert "call candidates to see them" in by_tool
    assert "CRWV: CoreWeave" not in by_tool
    assert "A new ticker must come from the list that candidates returns" in by_tool
    assert "CRWV: CoreWeave at $95.00, 12.0M shares traded, via most active" in by_json
    assert "A new ticker must come from the candidate list above" in by_json


def _signal(ticker, price_at_signal, decision="Hold"):
    return SimpleNamespace(
        ticker=ticker, signal_date="2026-09-15", created_at="2026-09-15 14:00", id=1,
        price_at_signal=price_at_signal, decision=decision,
    )


def test_the_tool_prompt_shrinks_the_watchlist_to_the_names_worth_a_look(monkeypatch):
    """The table is the `watchlist` fetch on this channel. The line keeps
    what the table was for: a name never analysed, or one that has moved
    since it was."""
    import backend.tests.test_agent as _t
    analysed = {"AAPL": _signal("AAPL", 200.0), "INTC": _signal("INTC", 100.0), "MU": _signal("MU", 50.0)}
    monkeypatch.setattr(
        agent.db, "get_recent_signals",
        lambda ticker=None, *a, **k: [analysed[ticker]] if ticker in analysed else [],
    )
    prices = {"AAPL": 202.0, "INTC": 107.5, "MU": 46.0, "NVDA": 180.0}

    by_tool = agent.build_prompt(_t._book(), [], prices, watchlist=["AAPL", "INTC", "MU", "NVDA"],
                                 max_watchlist=30, answer_by_tool=True)
    by_json = agent.build_prompt(_t._book(), [], prices, watchlist=["AAPL", "INTC", "MU", "NVDA"],
                                 max_watchlist=30, answer_by_tool=False)

    assert (
        "You track 4 of at most 30 tickers. Never analysed: NVDA. Moved 5% or more since "
        "their last analysis: INTC +7.5%, MU -8.0%. Call watchlist for every one"
    ) in by_tool
    assert "| Ticker | Held? | Price now |" not in by_tool
    assert "| Ticker | Held? | Price now |" in by_json
    assert "| AAPL | watched | $202.00 |" in by_json


def test_a_full_watchlist_is_still_said_to_be_full_on_the_tool_channel(monkeypatch):
    import backend.tests.test_agent as _t
    monkeypatch.setattr(agent.db, "get_recent_signals", lambda *a, **k: [])

    by_tool = agent.build_prompt(_t._book(), [], {}, watchlist=["AAPL", "INTC"], max_watchlist=2,
                                 answer_by_tool=True)

    assert "That is the limit, so nothing new can be tracked until you untrack" in by_tool


def _closed(ticker, won, pnl, held_days, signal_decision="Buy"):
    return SimpleNamespace(
        ticker=ticker, won=won, pnl=pnl, held_days=held_days, entry=10.0, exit=11.0,
        return_pct=10.0, signal_decision=signal_decision,
    )


def test_the_tool_prompt_keeps_the_track_record_totals_and_the_hold_pattern():
    import backend.tests.test_agent as _t
    closed = [
        _closed("AAPL", True, 12.5, 4), _closed("INTC", False, -8.0, 9, "Hold"),
        _closed("MU", False, -3.0, 2, "Hold"),
    ]

    by_tool = agent.build_prompt(_t._book(), [], {}, closed=closed, answer_by_tool=True)
    by_json = agent.build_prompt(_t._book(), [], {}, closed=closed, answer_by_tool=False)

    assert (
        "Your own past trades: 3 closed, 1 profitable, +1.50 net, held 5 days on average. "
        "Call track_record for each one"
    ) in by_tool
    assert "Of the 2 you bought on a Hold signal, 0 made money." in by_tool
    assert "- AAPL: bought $10.00" not in by_tool
    assert "- AAPL: bought $10.00, sold $11.00" in by_json
    assert "Of the 2 you bought on a Hold signal, 0 made money." in by_json


def test_the_tool_prompt_counts_the_wakeups_instead_of_listing_them():
    import backend.tests.test_agent as _t
    wakeups = [
        {"at": "Mon 9:30 AM", "acted": True}, {"at": "Mon 11:00 AM", "acted": False},
        {"at": "Mon 3:00 PM", "acted": False},
    ]

    by_tool = agent.build_prompt(_t._book(), [], {}, wakeups=wakeups, answer_by_tool=True)
    by_json = agent.build_prompt(_t._book(), [], {}, wakeups=wakeups, answer_by_tool=False)

    assert "Your last 3 wakeups: 1 led to an action, 2 did nothing." in by_tool
    assert "Mon 11:00 AM" not in by_tool
    assert "2 of the last 3 did nothing." in by_tool
    assert "- Mon 11:00 AM: did nothing" in by_json


def test_the_two_system_messages_share_every_rule_that_is_not_about_reading():
    assert "JSON only" in agent.SYSTEM_PROMPT
    assert "JSON only" not in agent.SYSTEM_PROMPT_TOOL
    assert "calling the decide function" in agent.SYSTEM_PROMPT_TOOL
    assert agent.SYSTEM_PROMPT.endswith("Reply with JSON only, in the shape specified below:")
    assert agent.SYSTEM_PROMPT_TOOL.endswith(
        "Answer by calling decide, with every order inside its orders list."
    )
    for rule in agent._FIXED_RULES:
        if isinstance(rule, dict):
            assert rule[False] in agent.SYSTEM_PROMPT and rule[False] not in agent.SYSTEM_PROMPT_TOOL
            assert rule[True] in agent.SYSTEM_PROMPT_TOOL and rule[True] not in agent.SYSTEM_PROMPT
        else:
            assert rule in agent.SYSTEM_PROMPT and rule in agent.SYSTEM_PROMPT_TOOL
    # Exactly the three rules about reading differ.
    assert sum(isinstance(rule, dict) for rule in agent._FIXED_RULES) == 3
    assert "call read with a ticker" in agent.SYSTEM_PROMPT_TOOL
    assert "Fetch what you need before deciding" in agent.SYSTEM_PROMPT_TOOL


# --- a whole decision on the tool channel ---------------------------------------


def _menu(*tickers):
    return [
        SimpleNamespace(ticker=t, name=f"{t} Inc", price=50.0, volume=5_000_000.0,
                        volume_m=5.0, change_pct=None, source="most active")
        for t in tickers
    ]


def _research(ticker):
    return {"reasoning": "worth a look", "orders": [
        {"side": "research", "ticker": ticker, "reason": "liquid and unanalysed"},
    ]}


def test_a_research_of_a_fetched_candidate_is_accepted(monkeypatch):
    """The whole path: the model calls candidates, is shown the screen inside
    the same call, and researches one of the names it returned."""
    import backend.tests.test_agent as _t
    screens = []
    monkeypatch.setattr(agent, "_candidate_menu", lambda: screens.append(1) or _menu("CRWV"))
    monkeypatch.setattr(agent.research, "is_charging", lambda: True)
    generate = _Generate(
        _response([_part(call=_call("candidates"))]),
        _decided(args=_research("CRWV")),
    )
    _on_gemini(monkeypatch, generate)

    reasoning, accepted, rejected = agent._decide(_t._book(cash=250.0), [], {})

    assert reasoning == "worth a look"
    assert [(o["side"], o["ticker"]) for o in accepted] == [("research", "CRWV")]
    assert rejected == []
    assert screens == [1], "the vendor screen ran once, when the model asked"
    # The screen reached the model as a function result, not as prompt text.
    _, contents, _ = generate.calls[1]
    assert "CRWV: CRWV Inc at $50.00" in contents[2].parts[0].function_response.response["result"]
    assert "CRWV Inc" not in generate.calls[0][1][0].parts[0].text


def test_a_research_of_a_name_never_fetched_is_refused(monkeypatch):
    """The universe on the tool channel is what candidates returned this
    pass. A model that never called it cannot research a new ticker, the same
    rule the menu in the prompt always enforced."""
    import backend.tests.test_agent as _t
    monkeypatch.setattr(agent, "_candidate_menu", lambda: _menu("CRWV"))
    monkeypatch.setattr(agent.research, "is_charging", lambda: True)
    generate = _Generate(
        _decided(args=_research("ZZZZ")),
        # The refusal retry: the model asks again without fetching.
        _decided(args={"reasoning": "hold then", "orders": []}),
    )
    _on_gemini(monkeypatch, generate)

    _, accepted, rejected = agent._decide(_t._book(cash=250.0), [], {})

    assert accepted == []
    assert [r.ticker for r in rejected] == ["ZZZZ"]
    assert "not on the candidate list" in rejected[0].why
    # The retry's prompt carried the refusal, and no "a read does not run on
    # this turn" line: on this channel it would.
    retry_prompt = generate.calls[1][1][0].parts[0].text
    assert "Your previous answer was refused" in retry_prompt
    assert "A read does not run on this turn" not in retry_prompt
