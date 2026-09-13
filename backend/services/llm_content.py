"""Separate a model's answer from its thinking, whatever shape the provider used.

Most providers return a message's content as one string. Gemini and Anthropic
return a list of typed blocks, and the thinking is one kind of block.
TradingAgents' Google client joins the text blocks into a string inside
``invoke`` and drops every other block (``normalize_content``). That is correct
for the agents that read the answer. It is wrong for a record that needs the
thinking too.

Two readers use this: the decision pass (``agent._invoke``) and the trace
recorder (``llm_traces``). They share one copy, so they cannot disagree about
what counts as thinking.
"""
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models import BaseChatModel


def split_thinking(content) -> tuple[str, str | None]:
    """(answer text, thinking) from one message's content.

    Thinking is None, not "", when the message carried none. A plain string is
    all answer. In a list, a ``text`` block or a bare string is answer, and a
    ``thinking`` or ``reasoning`` block is thinking. Any other block, such as a
    tool call, is neither and is left out: a tool call has its own field on the
    message.
    """
    if content is None:
        return "", None
    if not isinstance(content, list):
        return str(content), None
    text: list[str] = []
    thinking: list[str] = []
    for block in content:
        if isinstance(block, str):
            text.append(block)
            continue
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text":
            text.append(str(block.get("text") or ""))
        elif kind == "thinking":
            thinking.append(str(block.get("thinking") or ""))
        elif kind == "reasoning":
            # OpenAI's Responses API puts a list of summaries in the block.
            # Other providers put the text under "reasoning".
            summary = block.get("summary")
            if isinstance(summary, list):
                thinking.extend(str(s.get("text") or "") for s in summary if isinstance(s, dict))
            else:
                thinking.append(str(block.get("reasoning") or block.get("text") or ""))
    joined = "\n\n".join(t for t in thinking if t)
    return "\n".join(t for t in text if t), joined or None


class _ThinkingCapture(BaseCallbackHandler):
    """Reads the thinking from a response before the client removes it.

    The callback runs inside ``invoke``, before the Google client flattens the
    content, so this is the one place the thinking blocks still exist. The text
    is copied at once, because the client then changes the same message object.
    """

    def __init__(self):
        self.thinking: str | None = None

    def on_llm_end(self, response, **kwargs) -> None:
        found = []
        for batch in getattr(response, "generations", []) or []:
            for generation in batch:
                message = getattr(generation, "message", None)
                if message is None:
                    continue
                _, thinking = split_thinking(message.content)
                if thinking:
                    found.append(thinking)
        self.thinking = "\n\n".join(found) or None


def invoke_keeping_thinking(llm, messages):
    """(message, thinking) for ``llm.invoke(messages)``.

    A real LangChain chat model gets a callback that reads the response before
    the client flattens it. Any other object, such as a test double, is invoked
    as it is, and its thinking is read from the message it returns.
    """
    if not isinstance(llm, BaseChatModel):
        message = llm.invoke(messages)
        return message, split_thinking(getattr(message, "content", ""))[1]
    capture = _ThinkingCapture()
    message = llm.invoke(messages, config={"callbacks": [capture]})
    return message, capture.thinking or split_thinking(message.content)[1]
