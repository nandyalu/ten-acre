"""What one analysis cost: tokens in, tokens out, and wall-clock time.

A self-hosted model's cost is time on a GPU you already own; a cloud
provider's is tokens on an invoice. Neither number converts into the other, so
deciding between them needs both recorded per run. The counts come from the
provider's own accounting (every OpenAI-compatible endpoint returns a ``usage``
block, ollama included) rather than from an estimate — a tokenizer guess would
be wrong by exactly the amount that matters when it is multiplied by a price
per million.

``llm_calls`` is recorded alongside the totals because a run that reports zero
tokens over twenty calls means the endpoint stopped reporting usage, while zero
tokens over zero calls means the run died early. Without the call count those
are the same row.
"""
import logging
import threading
from dataclasses import dataclass

from langchain_core.callbacks import BaseCallbackHandler

log = logging.getLogger("ten-acre.llm_usage")


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    llm_calls: int = 0
    duration_seconds: float | None = None

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


def tokens_from_message(message) -> tuple[int, int]:
    """(prompt, completion) for a single message returned by ``.invoke()``.

    ``_tokens_from`` below reads the ``LLMResult`` that a callback handler
    sees, which is a different shape: ``.generate()`` wraps messages in
    ``generations``, ``.invoke()`` hands back the message itself. The agent's
    decision pass uses ``.invoke()`` directly on a client no tracker is
    attached to, so it needs this one.

    Reads the provider's own accounting and never estimates, for the reason in
    this module's docstring. A message carrying neither shape counts as zero
    rather than raising: losing the telemetry for a pass must never lose the
    pass.
    """
    usage = getattr(message, "usage_metadata", None) or {}
    prompt = usage.get("input_tokens", 0) or 0
    completion = usage.get("output_tokens", 0) or 0
    if prompt or completion:
        return prompt, completion

    raw = (getattr(message, "response_metadata", None) or {}).get("token_usage") or {}
    return raw.get("prompt_tokens", 0) or 0, raw.get("completion_tokens", 0) or 0


def _tokens_from(response) -> tuple[int, int]:
    """(prompt, completion) for one LLM response.

    ``usage_metadata`` on the message is the provider-neutral form LangChain
    normalizes into, so it is tried first; ``llm_output`` is the raw OpenAI
    shape and covers clients that don't populate the former. A response with
    neither counts as zero rather than raising — losing the telemetry for a run
    must never lose the run."""
    prompt = completion = 0
    for generations in getattr(response, "generations", []) or []:
        for generation in generations:
            usage = getattr(getattr(generation, "message", None), "usage_metadata", None)
            if usage:
                prompt += usage.get("input_tokens", 0) or 0
                completion += usage.get("output_tokens", 0) or 0
    if prompt or completion:
        return prompt, completion

    raw = (getattr(response, "llm_output", None) or {}).get("token_usage") or {}
    return raw.get("prompt_tokens", 0) or 0, raw.get("completion_tokens", 0) or 0


class UsageTracker(BaseCallbackHandler):
    """Totals every LLM call made through the clients it is attached to.

    One per analysis run, never shared: the graph is rebuilt per run anyway
    (see analysis._build_graph), so a fresh tracker attaches to fresh clients
    and two concurrent analyses can't count each other's tokens. The lock is
    for the tool-calling nodes, which may finish off the main thread.
    """

    def __init__(self) -> None:
        self.usage = Usage()
        self._lock = threading.Lock()

    def on_llm_end(self, response, **kwargs) -> None:
        prompt, completion = _tokens_from(response)
        with self._lock:
            self.usage.llm_calls += 1
            self.usage.prompt_tokens += prompt
            self.usage.completion_tokens += completion

    def finish(self, duration_seconds: float) -> Usage:
        with self._lock:
            self.usage.duration_seconds = duration_seconds
            if self.usage.llm_calls and not self.usage.total_tokens:
                log.warning(
                    "No token usage reported over %d LLM calls — the endpoint may not "
                    "return a usage block, so cost comparisons will read as zero.",
                    self.usage.llm_calls,
                )
            return self.usage


def attach(tracker: UsageTracker, *llms) -> None:
    """Register ``tracker`` on each client, keeping any callbacks already set.

    Attaching to the client rather than passing callbacks per-invocation is
    what makes this complete: every agent, the debate, the reflector, and the
    signal processor all share these two client objects, and none of them
    accepts a callback argument from here.
    """
    for llm in llms:
        llm.callbacks = [*(llm.callbacks or []), tracker]
