"""The decision pass on Gemini, through Google's own SDK.

**One forced call to ``decide``, and the arguments come back as JSON text.**
Until 2026-09-17 the agent wrote its answer as JSON inside prose, and
``agent.parse_decision`` read it with two repairs and a salvage step, all
written for a local model that fenced, wrapped and mistyped its JSON. Gemini
can be told that it must answer with a function call (``mode ANY``) and which
ones are allowed (``allowed_function_names``), so a prose answer stops being
possible and a malformed one is refused by the server. The arguments are
handed back as the same JSON text the parser has always read, so nothing
downstream changes: the record, the wakeup parsers and the site all see the
one shape they know.

**Fetches run inside the same call.** Beside ``decide`` the model may call a
fetch function: read an analysis, list the candidates, ask for a ticker's
fundamentals. The result goes back as a function response and the model is
asked again, in the same conversation, until it calls ``decide``. A pass's
allowance of fetches and rounds is a dict the caller owns, so a pass that
acts and is asked again cannot fetch from a full allowance each time. Once
the allowance is spent, ``decide`` is the only function still allowed, which
is what ends the loop.

**Google's SDK, not the LangChain wrapper the analysis graph uses.**
TradingAgents' Google client flattens every reply to a string
(``normalize_content``), which drops a function call, the thought parts, and
the thought signatures a later round has to send back. The SDK returns the
reply as typed parts, and Google documents that shape: the model's content is
appended to the conversation exactly as received, signatures included, and a
function response goes back under the ``user`` role (the API refuses ``tool``,
whatever the SDK's README shows; found on the first live pass).

**One client for the process, built from the environment.** ``genai.Client()``
reads ``GOOGLE_API_KEY`` (or ``GEMINI_API_KEY``), the same variable the graph's
client reads. The throttle is attached to it once, the way
``analysis._build_graph`` attaches it to the graph's clients, so every round
counts against the same per-minute and per-day limits as an analysis: they
share one key.

Thinking comes back only at a stated level (``TRADINGAGENTS_GOOGLE_THINKING_LEVEL``),
the same setting the graph honours, and thoughts are asked for whenever a
level is set. What arrives is Google's summary of the thinking, kept as the
pass's ``thinking`` like any other provider's, every round's joined.
"""
import json
import logging
import threading
from types import SimpleNamespace
from typing import Callable, NamedTuple

from google import genai
from google.genai import types
from tradingagents.default_config import DEFAULT_CONFIG

from backend.services import llm_throttle

log = logging.getLogger("ten-acre.llm_gemini")

_client = None
_client_lock = threading.Lock()
# HTTP options for the one client, read when it is built. None keeps the SDK's
# defaults. The probe sets one attempt: the SDK retries a 503 by itself, the
# throttle does not see those retries, and Google counts each one against the
# day's limit (2026-09-23: five requests counted for four calls).
http_options: types.HttpOptions | None = None

# What a fetch is told when it was asked for and could not run.
ALLOWANCE_SPENT = "Not run: the fetch allowance for this pass is spent. Decide with what you have."
DECIDE_WITH_FETCH = (
    "Not carried out: it came in the same round as a fetch, and only the fetches "
    "ran. Call decide again now, with everything you still want."
)
# What the last function response of a round carries beside its result when
# the pass is running out of rounds (2026-09-19). A round is one Google request
# that resends the whole conversation, so rounds stay few, and a model that is
# given a number plans around it — a live pass wrote "I need to be efficient".
# Told here, in the response it reads results from, under its own key so the
# result itself stays verbatim; a part the API did not ask for is not added.
ONE_ROUND_LEFT = (
    "One fetch round left in this pass after this one; the answer after it must "
    "be decide. If you will want more than that, decide now with what you have "
    "and ask to be woken in 5 minutes to carry on."
)
NO_ROUNDS_LEFT = (
    "That was the last fetch round of this pass: the next answer must be decide. "
    "Anything else you want to look at can wait for a wakeup 5 minutes from now."
)


def _rounds_note(budget: dict) -> str | None:
    """The warning a round's last function response carries, if any."""
    if budget.get("rounds", 0) <= 0 or budget.get("fetches", 0) <= 0:
        return NO_ROUNDS_LEFT
    if budget.get("rounds", 0) == 1:
        return ONE_ROUND_LEFT
    return None


class Reply(NamedTuple):
    """What one decision call produced, across every round it took."""

    content: str
    thinking: str | None
    prompt_tokens: int
    completion_tokens: int
    # One entry per fetch the model asked for, in order: name, args, result.
    exchanges: list[dict]
    # Input tokens Google served from its cache, across every round. They are
    # part of prompt_tokens and cost a tenth of the full input price. Each
    # round sends the whole conversation again, so this shows how much of
    # that the cache absorbed.
    cached_tokens: int = 0


def client() -> genai.Client:
    """The one SDK client, throttled. Built on first use, so importing this
    module never needs a key."""
    global _client
    with _client_lock:
        if _client is None:
            made = genai.Client(http_options=http_options) if http_options else genai.Client()
            # attach() looks for ``.client.models.generate_content``, the shape
            # of the graph's LangChain wrapper. A holder gives this bare client
            # the same shape, so one throttle serves both.
            llm_throttle.attach(SimpleNamespace(client=made))
            _client = made
        return _client


def thinking_config() -> types.ThinkingConfig | None:
    """Ask for thinking at the configured level, or not at all.

    None when no level is set: gemini-3.5-flash-lite thinks only at a stated
    level (measured 2026-09-13), and sending an empty config changes nothing.
    """
    level = str(DEFAULT_CONFIG.get("google_thinking_level") or "").strip().lower()
    if not level:
        return None
    return types.ThinkingConfig(include_thoughts=True, thinking_level=level)


def decide(
    system: str,
    prompt: str,
    declaration: dict,
    model: str,
    fetches: list[dict] | tuple = (),
    fetch: Callable[[str, dict], str] | None = None,
    budget: dict | None = None,
    generate=None,
) -> Reply:
    """Ask until the model calls ``declaration`` (``decide``), running its
    fetches on the way.

    ``fetch(name, args)`` runs one fetch and returns text; it must not raise.
    ``budget`` is the caller's dict with ``fetches`` and ``rounds`` left, spent
    here in place; a round is one request, however many fetches it carries. ``generate`` is the SDK's ``models.generate_content``; a
    test hands in a fake, and the app leaves it to ``client()``.

    ``content`` is the ``decide`` call's arguments as JSON text or, should the
    model answer with text despite ``mode ANY``, that text.
    """
    name = declaration["name"]
    generate = generate or client().models.generate_content
    budget = budget if budget is not None else {"fetches": 0, "rounds": 0}
    fetch_names = [f["name"] for f in fetches] if fetch is not None else []
    declared = [types.FunctionDeclaration(**d) for d in (declaration, *fetches)]
    contents: list = [types.Content(role="user", parts=[types.Part(text=prompt)])]
    thoughts: list[str] = []
    exchanges: list[dict] = []
    prompt_tokens = completion_tokens = cached_tokens = 0

    while True:
        may_fetch = bool(fetch_names) and budget.get("fetches", 0) > 0 and budget.get("rounds", 0) > 0
        allowed = [name, *fetch_names] if may_fetch else [name]
        config = types.GenerateContentConfig(
            system_instruction=system,
            tools=[types.Tool(function_declarations=declared)],
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(
                    mode=types.FunctionCallingConfigMode.ANY,
                    allowed_function_names=allowed,
                )
            ),
            thinking_config=thinking_config(),
        )
        response = generate(model=model, contents=contents, config=config)
        parts, content = _parts(response)
        used = getattr(response, "usage_metadata", None)
        prompt_tokens += int(getattr(used, "prompt_token_count", 0) or 0)
        cached_tokens += int(getattr(used, "cached_content_token_count", 0) or 0)
        # Thoughts are output the pass paid for, the same way an Ollama
        # reply's reasoning counts inside its completion_tokens.
        completion_tokens += int(getattr(used, "candidates_token_count", 0) or 0) + int(
            getattr(used, "thoughts_token_count", 0) or 0
        )
        thoughts += [p.text for p in parts if getattr(p, "thought", False) and getattr(p, "text", None)]
        if content is not None:
            # Verbatim, thought signatures included: Google's docs say a
            # stateless conversation must resend the model's own turn exactly.
            contents.append(content)

        calls = [p.function_call for p in parts if getattr(p, "function_call", None) is not None]
        decided = next((c for c in calls if getattr(c, "name", None) == name), None)
        wanted = [c for c in calls if getattr(c, "name", None) in fetch_names]

        if wanted and may_fetch:
            taking = wanted[: budget["fetches"]]
            budget["fetches"] -= len(taking)
            budget["rounds"] -= 1
            # (call id, name, response) for each call answered in this round.
            responses: list[tuple[str | None, str, dict]] = []
            for call in wanted:
                args = plain(dict(getattr(call, "args", None) or {}))
                text = fetch(call.name, args) if call in taking else ALLOWANCE_SPENT
                exchanges.append({"name": call.name, "args": args, "result": text})
                responses.append((getattr(call, "id", None), call.name, {"result": text}))
            if decided is not None:
                # The rule the read side has always had: a fetch and an
                # answer in one round, and only the fetch runs. Said in the
                # function's own response, so the model reads it where it
                # looks for results.
                exchanges.append({"name": name, "args": plain(dict(decided.args or {})), "result": DECIDE_WITH_FETCH})
                responses.append((getattr(decided, "id", None), name, {"result": DECIDE_WITH_FETCH}))
            note = _rounds_note(budget)
            if note is not None:
                responses[-1][2]["rounds"] = note
                # On the record too, as its own row, so the Decisions page
                # shows what the model was told and when.
                exchanges.append({"name": "rounds", "args": {}, "result": note})
            # **``user``, not ``tool``.** The SDK's README shows ``tool``, and
            # the API refused it on the first live pass (2026-09-17): "Role
            # 'tool' is not supported. Please use a valid role: ... USER,
            # MODEL". Function responses travel under ``user`` on this
            # endpoint, the shape Google's function-calling guide shows.
            #
            # **Each response carries the id of the call it answers.** Google's
            # Gemini 3.8 migration checklist requires the id and the name on
            # every FunctionResponse. Part.from_function_response takes no id,
            # so the part is built directly. A call with no id sends none.
            contents.append(types.Content(
                role="user",
                parts=[
                    types.Part(function_response=types.FunctionResponse(id=i, name=n, response=r))
                    for i, n, r in responses
                ],
            ))
            continue

        thinking = "\n\n".join(thoughts) or None
        if decided is not None:
            if len(calls) > 1:
                log.warning("Gemini made %d function calls in the deciding round; only %s is read", len(calls), name)
            text = json.dumps(plain(dict(getattr(decided, "args", None) or {})), ensure_ascii=False)
            return Reply(text, thinking, prompt_tokens, completion_tokens, exchanges, cached_tokens)
        # mode ANY makes this a vendor fault, not a model choice. Whatever text
        # came back goes to the tolerant parser, which is where every answer
        # went before this module existed.
        text = "\n".join(p.text for p in parts if getattr(p, "text", None) and not getattr(p, "thought", False))
        log.warning("Gemini answered without calling %s; reading its text instead", name)
        return Reply(text, thinking, prompt_tokens, completion_tokens, exchanges, cached_tokens)


def _parts(response) -> tuple[list, object]:
    """(parts, the model's content object) from one response."""
    candidates = getattr(response, "candidates", None) or []
    content = getattr(candidates[0], "content", None) if candidates else None
    return list(getattr(content, "parts", None) or []), content


def plain(value):
    """JSON-ready, with whole-number floats as ints.

    The API types every number as a float, so a quantity of 2 arrives as 2.0,
    and every line that prints it with ``:g`` would still read fine, but the
    record would carry ``2.0`` where every earlier answer carried ``2``. A
    price keeps its decimals; only a value with none loses the ``.0``.
    """
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value
