"""What a decision pass cost: tokens in, tokens out, and the wall clock.

Signal has carried this for an analysis since the cost telemetry landed. A
decision pass stored its prompt and its answer in full and not one token of
what they cost, because the agent's ``_ask`` uses a client no ``UsageTracker``
is attached to — the tracker binds to the graph's two LLM objects, and this
call does not go through the graph.

It matters because the agent wakes several times a day and every prompt change
moves the number. Adding the sell-any-time rule on 2026-09-09 made the prompt
459 characters longer, and nothing in the record could see it.
"""
import pytest

from backend.services import agent


class _Message:
    """What ``.invoke()`` hands back: the message itself, not the
    ``LLMResult`` a callback handler sees."""

    def __init__(self, content="{}", usage=None, metadata=None):
        self.content = content
        self.usage_metadata = usage
        self.response_metadata = metadata or {}


# --- reading the provider's own counts ----------------------------------------


def test_the_normalized_shape_is_read():
    from backend.services import llm_usage

    message = _Message(usage={"input_tokens": 12_882, "output_tokens": 367})

    assert llm_usage.tokens_from_message(message) == (12_882, 367)


def test_the_raw_openai_shape_is_read_when_the_normalized_one_is_missing():
    from backend.services import llm_usage

    message = _Message(metadata={"token_usage": {"prompt_tokens": 100, "completion_tokens": 20}})

    assert llm_usage.tokens_from_message(message) == (100, 20)


def test_a_message_reporting_nothing_counts_as_zero():
    """Losing the telemetry for a pass must never lose the pass."""
    from backend.services import llm_usage

    assert llm_usage.tokens_from_message(_Message()) == (0, 0)
    assert llm_usage.tokens_from_message(object()) == (0, 0)


# --- the answer carries them --------------------------------------------------


def test_ask_attaches_the_counts_and_the_time(monkeypatch):
    class _Client:
        def invoke(self, messages):
            return _Message('{"orders": []}', usage={"input_tokens": 900, "output_tokens": 30})

    monkeypatch.setattr(agent.analysis, "_quick_think_llm", lambda: _Client())

    answer = agent._ask("a prompt")

    assert answer == '{"orders": []}'  # still exactly the string it was
    assert answer.prompt_tokens == 900
    assert answer.completion_tokens == 30
    assert answer.seconds >= 0


def test_the_answer_is_still_a_plain_string_to_everything_else(monkeypatch):
    """A dozen callers and test fakes treat it as a str. Widening the return
    type would have broken all of them for three numbers one caller reads."""
    class _Client:
        def invoke(self, messages):
            return _Message("hello", usage={"input_tokens": 1, "output_tokens": 1})

    monkeypatch.setattr(agent.analysis, "_quick_think_llm", lambda: _Client())

    answer = agent._ask("a prompt")

    assert isinstance(answer, str)
    assert answer.upper() == "HELLO"
    assert answer + "!" == "hello!"


# --- summing across the retry -------------------------------------------------


def test_a_spend_reads_a_plain_string_as_zero():
    """Several tests patch _ask with ``lambda _p: "..."``. Those must keep
    working, reporting nothing rather than raising."""
    assert agent._Spend.of("just a string") == (0, 0, 0.0)


def test_two_spends_add_up():
    """A retry is a second real call and its tokens are spent whether or not
    its answer is the one used."""
    first = agent._Spend(100, 10, 1.5)
    second = agent._Spend(120, 12, 2.0)

    assert first + second == (220, 22, 3.5)


def test_the_run_carries_what_the_decision_cost():
    run = agent.AgentRun(prompt_tokens=12_882, completion_tokens=367, seconds=41.2)

    assert run.prompt_tokens == 12_882
    assert run.completion_tokens == 367
    assert run.seconds == pytest.approx(41.2)


# --- the thinking ---------------------------------------------------------------


class _RawClient:
    """The OpenAI-compatible client LangChain builds. ``reasoning`` is a
    non-standard field ollama adds beside the content, which ChatOpenAI drops
    on purpose — so ``_invoke`` reads it off the raw response instead."""

    def __init__(self, content='{"orders": []}', reasoning="Here's a thinking process:", usage=(4271, 1595)):
        self._content, self._reasoning, self._usage = content, reasoning, usage
        self.calls = []

    def create(self, model, messages):
        self.calls.append((model, messages))
        prompt_tokens, completion_tokens = self._usage
        message = type("Msg", (), {
            "content": self._content,
            "reasoning": self._reasoning,
            "model_extra": {"reasoning": self._reasoning},
        })()
        usage = type("Usage", (), {
            "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
        })()
        return type("Raw", (), {
            "choices": [type("Choice", (), {"message": message})()], "usage": usage,
        })()


class _Llm:
    model_name = "gemma4-e4b-qat-128k"

    def __init__(self, client=None, message=None):
        self.client = client
        self._message = message

    def invoke(self, messages):
        return self._message


def test_the_thinking_is_captured(monkeypatch):
    """It is most of what the model generates and all of why it decided. On a
    replayed pass the answer was 606 characters against 4,034 of reasoning —
    generated, paid for, and until 2026-09-09 thrown away."""
    monkeypatch.setattr(agent.analysis, "_quick_think_llm", lambda: _Llm(client=_RawClient()))

    answer = agent._ask("a prompt")

    assert answer.thinking == "Here's a thinking process:"
    assert answer.prompt_tokens == 4271
    assert answer.completion_tokens == 1595


def test_the_thinking_is_captured_from_llama_server(monkeypatch):
    """llama-server sends the thinking as ``reasoning_content``, not
    ``reasoning``. Without this read the record would lose it silently."""

    class _LlamaServerClient(_RawClient):
        def create(self, model, messages):
            raw = super().create(model, messages)
            message = raw.choices[0].message
            message.reasoning = None
            message.model_extra = {"reasoning_content": self._reasoning}
            return raw

    monkeypatch.setattr(agent.analysis, "_quick_think_llm", lambda: _Llm(client=_LlamaServerClient()))

    answer = agent._ask("a prompt")

    assert answer.thinking == "Here's a thinking process:"


def test_the_system_prompt_still_goes_with_it(monkeypatch):
    """The raw path builds its own message list, so it has to carry the same
    system message ``llm.invoke`` was given."""
    client = _RawClient()
    monkeypatch.setattr(agent.analysis, "_quick_think_llm", lambda: _Llm(client=client))

    agent._ask("a prompt")

    _, messages = client.calls[0]
    assert messages[0] == {"role": "system", "content": agent.SYSTEM_PROMPT}
    assert messages[1] == {"role": "user", "content": "a prompt"}


def test_a_client_without_reasoning_still_answers(monkeypatch):
    """Anthropic and Google go through their own LangChain packages. Losing
    the thinking is the cost of switching provider; losing the pass is not."""
    message = _Message('{"orders": []}', usage={"input_tokens": 10, "output_tokens": 2})
    monkeypatch.setattr(agent.analysis, "_quick_think_llm", lambda: _Llm(message=message))

    answer = agent._ask("a prompt")

    assert answer == '{"orders": []}'
    assert answer.thinking is None
    assert answer.prompt_tokens == 10


def test_a_raw_client_that_raises_falls_back_rather_than_losing_the_pass(monkeypatch):
    class _Broken:
        def create(self, model, messages):
            raise RuntimeError("unexpected client shape")

    message = _Message('{"orders": []}', usage={"input_tokens": 10, "output_tokens": 2})
    monkeypatch.setattr(
        agent.analysis, "_quick_think_llm", lambda: _Llm(client=_Broken(), message=message)
    )

    answer = agent._ask("a prompt")

    assert answer == '{"orders": []}'
    assert answer.thinking is None


def test_a_pass_that_never_asked_reports_nothing():
    """A skipped run, or one the market was shut for, made no call at all."""
    run = agent.AgentRun(skipped="market closed")

    assert run.prompt_tokens == 0
    assert run.completion_tokens == 0
    assert run.seconds == 0.0
