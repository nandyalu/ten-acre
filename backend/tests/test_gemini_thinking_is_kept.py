"""A Gemini pass keeps its thinking.

Every Gemini pass stored an empty ``thinking`` column. The decision pass read
thinking only from an OpenAI-compatible response, and TradingAgents' Google
client flattens the answer to a string inside ``invoke``, which drops the
thinking blocks. The thinking is what shows how the agent read its prompt, so
losing it hides every prompt bug this project has found.

Pure — no LLM, no network.
"""
import json
from types import SimpleNamespace

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage

from backend.services import agent, analysis, llm_content, llm_traces


def _gemini_blocks():
    return [
        {"type": "thinking", "thinking": "The market is shut, so I only choose a wakeup."},
        {"type": "text", "text": '{"orders": []}'},
    ]


class _FlatteningGemini(FakeMessagesListChatModel):
    """Behaves like TradingAgents' NormalizedChatGoogleGenerativeAI: the
    response carries thinking blocks, and ``invoke`` keeps only the text."""

    def invoke(self, input, config=None, **kwargs):
        message = super().invoke(input, config, **kwargs)
        message.content, _ = llm_content.split_thinking(message.content)
        return message


# --- splitting a message --------------------------------------------------------


def test_a_plain_string_is_all_answer():
    assert llm_content.split_thinking("hello") == ("hello", None)


def test_gemini_blocks_split_into_answer_and_thinking():
    assert llm_content.split_thinking(_gemini_blocks()) == (
        '{"orders": []}', "The market is shut, so I only choose a wakeup.",
    )


def test_a_reasoning_summary_counts_as_thinking():
    content = [
        {"type": "reasoning", "summary": [{"text": "first"}, {"text": "second"}]},
        {"type": "text", "text": "answer"},
    ]

    assert llm_content.split_thinking(content) == ("answer", "first\n\nsecond")


def test_no_thinking_is_none_not_empty():
    assert llm_content.split_thinking([{"type": "text", "text": "a"}]) == ("a", None)


# --- the decision pass ----------------------------------------------------------


def test_the_thinking_is_read_before_the_client_flattens_it():
    llm = _FlatteningGemini(responses=[AIMessage(content=_gemini_blocks())])

    message, thinking = llm_content.invoke_keeping_thinking(llm, [HumanMessage("decide")])

    assert message.content == '{"orders": []}'
    assert thinking == "The market is shut, so I only choose a wakeup."


def test_a_gemini_pass_records_its_thinking(monkeypatch):
    llm = _FlatteningGemini(responses=[AIMessage(content=_gemini_blocks())])
    monkeypatch.setattr(agent.analysis, "_quick_think_llm", lambda: llm)

    answer = agent._ask("a prompt")

    assert answer == '{"orders": []}'
    assert answer.thinking == "The market is shut, so I only choose a wakeup."


def test_every_turn_keeps_its_own_thinking():
    """A pass is several turns now. Each one records what it thought."""
    first = agent._Answer("{}", 1, 1, 0.1, "first thought")
    second = agent._Answer("{}", 1, 1, 0.1, "second thought")

    turns = [agent._turn("p1", first), agent._turn("p2", second)]

    assert [t["thinking"] for t in turns] == ["first thought", "second thought"]


# --- analysis traces ------------------------------------------------------------


def test_a_trace_keeps_the_thinking_apart_from_the_answer(tmp_path):
    recorder = llm_traces.TraceRecorder("20260913T120000-abcd1234", "AAPL", "gemini", root=str(tmp_path))
    recorder.on_chat_model_start({}, [[HumanMessage("Analyse AAPL.")]], run_id="r1")
    recorder.on_llm_end(
        SimpleNamespace(generations=[[SimpleNamespace(message=AIMessage(content=_gemini_blocks()))]]),
        run_id="r1",
    )

    with open(recorder.path, encoding="utf-8") as handle:
        line = json.loads(handle.readline())

    assert line["output"][0]["content"] == '{"orders": []}'
    assert line["output"][0]["thinking"] == "The market is shut, so I only choose a wakeup."


# --- asking Gemini for it -------------------------------------------------------


def _stub_graph(monkeypatch, llm):
    monkeypatch.setattr(
        analysis, "TradingAgentsGraph",
        lambda config: SimpleNamespace(deep_thinking_llm=llm, quick_thinking_llm=llm),
    )


def test_a_gemini_client_is_asked_to_send_its_thinking(monkeypatch):
    llm = SimpleNamespace(include_thoughts=None)
    _stub_graph(monkeypatch, llm)

    analysis._build_graph("gemini-3.5-flash-lite")

    assert llm.include_thoughts is True


def test_an_explicit_no_is_left_alone(monkeypatch):
    llm = SimpleNamespace(include_thoughts=False)
    _stub_graph(monkeypatch, llm)

    analysis._build_graph("gemini-3.5-flash-lite")

    assert llm.include_thoughts is False


def test_a_client_without_the_setting_is_not_given_one(monkeypatch):
    llm = SimpleNamespace()
    _stub_graph(monkeypatch, llm)

    analysis._build_graph("gemma4-e4b-qat-128k")

    assert not hasattr(llm, "include_thoughts")
