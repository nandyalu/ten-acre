"""Every LLM call of an analysis, written to disk so it can train a model later.

Five small models have been rejected here for inventing prices, and the fix
that would rescue one is a fine-tune on runs that went right (see
[docs/model-training.md](../../docs/model-training.md)). That needs a dataset,
and **a dataset of past runs cannot be collected afterwards.** The analyses run
either way, so the marginal cost of keeping them is disk.

What one line of a trace file holds is one LLM call: the messages that went in,
including the tool definitions the model was offered, and what came back,
including any tool calls it made. About 21 lines make one analysis.

Three decisions worth knowing about.

**Nothing here may break a run.** Every write is wrapped, and a failure is
logged once and dropped. The same rule as ``llm_usage``: losing the telemetry
for a run must never lose the run.

**Traces are keyed by a run id, not a signal id.** The signal does not exist
until the analysis finishes, so the id is generated first, written into the
file name, and stored on the ``Signal`` afterwards. ``Signal.trace_id`` is what
turns a pile of traces into a training set: it lets a later filter keep only
the runs the Scorecard eventually graded a pass, which is the advantage this
app has over any public dataset.

**Off unless a directory is set.** ``LLM_TRACE_DIR`` enables it. A deployment
that does not want the disk cost pays nothing, and no default path quietly
fills a volume.
"""
import datetime
import json
import logging
import os
import threading
import uuid

from langchain_core.callbacks import BaseCallbackHandler

from backend.services import llm_content

log = logging.getLogger("ten-acre.llm_traces")

# Set to a writable path to record. Unset means record nothing.
TRACE_DIR = os.environ.get("LLM_TRACE_DIR") or ""

# A single call above this many characters is written truncated. Guards against
# one runaway context turning a day of traces into gigabytes; the cap is far
# above a normal call, which runs 20-40k characters.
MAX_CHARS_PER_FIELD = 400_000


def is_recording() -> bool:
    return bool(TRACE_DIR)


def new_run_id() -> str:
    """Short, sortable, and unique enough for a file name.

    Date first so a directory listing groups by day without reading any file.
    """
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"{stamp}-{uuid.uuid4().hex[:8]}"


def _clip(text: str) -> str:
    if len(text) <= MAX_CHARS_PER_FIELD:
        return text
    return text[:MAX_CHARS_PER_FIELD] + f"\n[clipped, {len(text)} chars total]"


def _message_to_dict(message) -> dict:
    """One langchain message as plain JSON.

    Tool calls are kept separate from content because they are the part a
    fine-tune is actually learning. A model that writes the call into the
    content field instead is the exact failure this dataset exists to fix, and
    flattening the two would hide it.
    """
    # Gemini and Anthropic send thinking as its own block. Until 2026-09-13 the
    # blocks were joined into the content as stringified dicts. Thinking is a
    # separate field now, so a reader can take the answer without it.
    content, thinking = llm_content.split_thinking(message.content)
    record = {
        "role": getattr(message, "type", None) or message.__class__.__name__,
        "content": _clip(content),
    }
    if thinking:
        record["thinking"] = _clip(thinking)
    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls:
        record["tool_calls"] = [
            {"name": c.get("name"), "args": c.get("args")} for c in tool_calls
        ]
    # Present on ToolMessage: which call this is the answer to.
    if getattr(message, "tool_call_id", None):
        record["tool_call_id"] = message.tool_call_id
    if getattr(message, "name", None):
        record["name"] = message.name
    return record


class TraceRecorder(BaseCallbackHandler):
    """Writes one JSON line per LLM call.

    Attaches through ``llm_usage.attach`` alongside the ``UsageTracker``, which
    is what makes it complete: every agent, the debate, the reflector and the
    signal processor share the same two client objects, and none of them
    accepts a callback from the caller.

    One recorder per run, like the tracker. The lock is for the tool-calling
    nodes, which may finish off the main thread.
    """

    def __init__(self, run_id: str, ticker: str, model: str, root: str | None = None):
        self.run_id = run_id
        self.ticker = ticker
        self.model = model
        self.root = root if root is not None else TRACE_DIR
        self.calls = 0
        self._pending: dict[str, list] = {}
        self._lock = threading.Lock()
        self._broken = False

    @property
    def path(self) -> str:
        day = self.run_id.split("T")[0]
        return os.path.join(self.root, day, f"{self.ticker}-{self.run_id}.jsonl")

    def on_chat_model_start(self, serialized, messages, **kwargs) -> None:
        """Hold the input until the matching response arrives.

        Keyed by langchain's run_id so concurrent calls inside one analysis
        cannot pair the wrong prompt with the wrong answer.
        """
        if not self.root:
            return
        key = str(kwargs.get("run_id", ""))
        try:
            flat = [_message_to_dict(m) for batch in messages for m in batch]
            with self._lock:
                self._pending[key] = flat
        except Exception:
            self._note_failure("could not record the prompt")

    def on_llm_end(self, response, **kwargs) -> None:
        if not self.root:
            return
        key = str(kwargs.get("run_id", ""))
        with self._lock:
            prompt = self._pending.pop(key, None)
        try:
            outputs = []
            for batch in getattr(response, "generations", []) or []:
                for generation in batch:
                    message = getattr(generation, "message", None)
                    if message is not None:
                        outputs.append(_message_to_dict(message))
                    else:
                        outputs.append({"role": "ai", "content": _clip(str(generation.text))})
            if not outputs:
                # A response carrying no generation teaches nothing, and a line
                # of it would have to be filtered out of the dataset later.
                # Drop it here instead.
                return
            self._write({
                "run_id": self.run_id,
                "ticker": self.ticker,
                "model": self.model,
                "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "messages": prompt or [],
                "output": outputs,
            })
        except Exception:
            self._note_failure("could not record the response")

    def _write(self, record: dict) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, default=str) + "\n")
        with self._lock:
            self.calls += 1

    def _note_failure(self, what: str) -> None:
        """Log the first failure only.

        A broken directory would otherwise log twenty identical stack traces
        per analysis and bury whatever else was in the log that morning.
        """
        if self._broken:
            return
        self._broken = True
        log.exception("%s for %s — traces for this run are incomplete", what, self.run_id)


def recorder_for(ticker: str, model: str, run_id: str | None = None) -> TraceRecorder | None:
    """A recorder, or None when ``LLM_TRACE_DIR`` is unset."""
    if not TRACE_DIR:
        return None
    return TraceRecorder(run_id or new_run_id(), ticker, model)
