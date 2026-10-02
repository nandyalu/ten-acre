"""Replay a recorded turn: ask it again, of any model, and answer every fetch
from the record of the pass.

**Why it exists.** Prompt changes land daily and the market gives about one
trade a week, so the live record cannot test a change in less than months. A
replay asks the same question again, with the same facts, of a different model
or under different fixed rules, and puts the two answers side by side.

**What it holds fixed.** The user message is the one the turn was sent,
verbatim, so every price, holding and table in it is what the agent saw then.
A fetch is answered with the result the pass got, matched by name and
arguments. **Nothing is fetched live**: a live fetch would show a model today's
data for a past decision, and that is the lookahead a replay must not have.

**What it cannot hold fixed.** A fetch the original pass never made has no
recorded result. It is answered the way the app answers a fetch that failed,
so the model is not told it is in a replay, and the record of the replay marks
the exchange ``in_record: false``. Count those before trusting a comparison.

**Replay only turns dated after the knowledge cutoff of the model under
test.** Before it, the model may know what the price did next.
"""
import json

from backend.services import agent, analysis, decision_schema, llm_gemini

# The text the app returns when a fetch fails (ToolContext.fetch). The model
# sees the same words here, so it cannot tell a replay from a live pass.
_NOT_IN_RECORD = "{name} failed with an error in this app. Decide with what you have."

_NOT_FETCHES = ("rounds", decision_schema.DECIDE["name"])


def _key(name: str, args: dict) -> str:
    return f"{name}:{json.dumps(args or {}, sort_keys=True)}"


class RecordedFetches:
    """A ``fetch(name, args)`` that answers from one turn's recorded exchanges.

    An identical call made twice gets the recorded results in order. A call
    with no arguments matches any recorded call of that name, because the
    order of a pass's no-argument fetches carries no meaning.
    """

    def __init__(self, exchanges: list[dict]):
        self._by_key: dict[str, list[str]] = {}
        self._by_name: dict[str, list[str]] = {}
        for exchange in exchanges or []:
            name = exchange.get("name")
            if name in _NOT_FETCHES:
                continue
            result = str(exchange.get("result") or "")
            self._by_key.setdefault(_key(name, exchange.get("args") or {}), []).append(result)
            self._by_name.setdefault(name, []).append(result)
        self.log: list[dict] = []

    def __call__(self, name: str, args: dict) -> str:
        queue = self._by_key.get(_key(name, args))
        if not queue and not args:
            queue = self._by_name.get(name)
        if queue:
            result = queue.pop(0) if len(queue) > 1 else queue[0]
            self.log.append({"name": name, "args": args, "result": result, "in_record": True})
            return result
        result = _NOT_IN_RECORD.format(name=name)
        self.log.append({"name": name, "args": args, "result": result, "in_record": False})
        return result


def turns_of(run) -> list[dict]:
    return json.loads(run.turns or "[]")


def replay_turn(turn: dict, *, model: str | None = None, system: str | None = None, generate=None) -> dict:
    """Ask ``turn`` again. ``system`` defaults to the one the turn was sent,
    and to today's fixed rules for a turn recorded before 2026-10-02, which
    stored none. ``model`` defaults to the deployment's decision model."""
    if turn.get("channel") != "tool":
        raise ValueError("only a tool-channel turn can be replayed; the JSON channel has no fetches to serve")
    system = system or turn.get("system") or agent.SYSTEM_PROMPT_TOOL
    model = model or analysis.decision_model()
    fetches = RecordedFetches(turn.get("exchanges") or [])
    budget = agent._fresh_budget()
    reply = llm_gemini.decide(
        system, turn["prompt"], decision_schema.DECIDE, model=model,
        fetches=decision_schema.FETCHES, fetch=fetches, budget=budget, generate=generate,
    )
    reasoning, orders = agent.parse_decision(reply.content)
    return {
        "model": model,
        "system_sha": agent.system_sha(system),
        "system_was_recorded": bool(turn.get("system")),
        "reasoning": reasoning,
        "orders": orders,
        "response": reply.content,
        "thinking": reply.thinking,
        "exchanges": fetches.log,
        "fetches_not_in_record": sum(1 for e in fetches.log if not e["in_record"]),
        "prompt_tokens": reply.prompt_tokens,
        "completion_tokens": reply.completion_tokens,
        "cached_tokens": reply.cached_tokens,
    }
