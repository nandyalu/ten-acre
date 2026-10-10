"""Ask a recorded turn again, and put the new answers beside the original.

**This cannot trade.** It reads one pass from the database, calls the model,
and writes a file. It never reaches run_once, screen or any broker path.

    python -m backend.scripts.replay --run 106
    python -m backend.scripts.replay --run 106 --turn 2 --model gemini-3.8-flash --samples 4
    python -m backend.scripts.replay --run 106 --system current

``--system current`` sends today's fixed rules instead of the ones the turn
was sent: that is how a change to the rules is tested against past passes. A
change to the user message is not a replay; probe it with probe_prompt.

Run it inside a container: the host cannot reach Google (see
.claude/rules/llm-providers.md). See backend/services/replay.py for what a
replay holds fixed and what it cannot.
"""
import argparse
import datetime
import json
from concurrent.futures import ThreadPoolExecutor

from sqlmodel import Session

from backend import paths
from backend.database.engine import engine
from backend.database.models import AgentRun
from backend.services import agent, replay


def _brief(orders: list[dict]) -> str:
    return ", ".join(
        f"{o.get('side')} {o.get('ticker') or ''} {o.get('quantity') or ''}".strip() for o in orders
    ) or "no orders"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=int, required=True, help="the agentrun id")
    parser.add_argument("--turn", type=int, default=1, help="1-based turn of that pass")
    parser.add_argument("--model", default=None, help="defaults to the deployment's decision model")
    parser.add_argument("--system", choices=("recorded", "current"), default="recorded")
    parser.add_argument("--samples", type=int, default=4)
    args = parser.parse_args()

    with Session(engine) as session:
        run = session.get(AgentRun, args.run)
    if run is None:
        print(f"No pass {args.run}.")
        return 1
    turns = replay.turns_of(run)
    if not 1 <= args.turn <= len(turns):
        print(f"Pass {args.run} has {len(turns)} turn(s).")
        return 1
    turn = turns[args.turn - 1]
    system = agent.SYSTEM_PROMPT_TOOL if args.system == "current" else None
    print(f"pass {args.run} at {run.ran_at}, turn {args.turn} of {len(turns)}, "
          f"recorded on {turn.get('model') or 'an unrecorded model'}")
    print(f"  original: {_brief(turn.get('orders') or [])}")

    def one(n: int) -> dict:
        try:
            return {"sample": n, **replay.replay_turn(turn, model=args.model, system=system)}
        except Exception as exc:
            return {"sample": n, "error": repr(exc)}

    with ThreadPoolExecutor(max_workers=args.samples) as pool:
        samples = list(pool.map(one, range(1, args.samples + 1)))
    for s in samples:
        if "error" in s:
            print(f"  #{s['sample']}: failed: {s['error']}")
            continue
        print(f"  #{s['sample']}: {_brief(s['orders'])}  "
              f"(fetches not in the record: {s['fetches_not_in_record']})")

    out = paths.data_dir() / "replay"
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S")
    path = out / f"run{args.run}-turn{args.turn}-{stamp}.json"
    path.write_text(json.dumps({
        "run": args.run, "turn": args.turn, "ran_at": str(run.ran_at),
        "original": {k: turn.get(k) for k in ("model", "system_sha", "reasoning", "orders", "response", "thinking", "exchanges")},
        "samples": samples,
    }, indent=1, default=str))
    print(f"\nwritten to {path}")
    print("Read the reasoning, do not grep it — see .claude/skills/probe-the-prompt/SKILL.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
