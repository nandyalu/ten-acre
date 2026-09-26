"""Count what the agent fetches, and whether its reasoning uses what came back.

Step 1 of PLAN.md. For every tool-channel pass, count the calls to each fetch
by name. Then ask whether the turn's reasoning uses a figure from the fetch's
result.

    python -m backend.scripts.fetch_audit
    python -m backend.scripts.fetch_audit --since 2026-09-17 --evidence
    python -m backend.scripts.fetch_audit --db /path/to/trading.db --json audit.json

The model paraphrases, so a search for the fetch's own words misses real reads.
**The default judge looks for a number instead**, the rule the
``probe-the-prompt`` skill gives: a figure that appears in the fetch's result
and nowhere in that turn's prompt can only have reached the reasoning through
the fetch. A paraphrase keeps its figures. A read that changed the model's mind
without a figure is missed, so the column is a floor.

``--judge laya`` asks a laya sidecar instead (see
``tradingagents/dataflows/laya.py``). **It failed on real passes on
2026-09-26**: over 44 calls every score fell between 0.56 and 0.77, and the
highest ``track_record`` score went to a piece of reasoning that quotes nothing
from the track record. laya reads about 700 tokens at a time, the script keeps
the highest of many pairs, and the highest of many noisy scores is always high.
It stays here to be measured again on a better checkpoint.

The judge reads the record after the fact and changes nothing in it.
``--evidence`` prints what the judge found for each call, so a person can check it.
"""
import argparse
import collections
import datetime
import json
import os
import re
import sys

from sqlalchemy import create_engine
from sqlmodel import Session, select

from backend.database.models import AgentRun
from backend.services.decision_schema import FETCH_NAMES
from tradingagents.dataflows import laya

# The typed-decisions checkpoint reads 1,024 tokens, and the question takes up
# to 256 of them. Two pieces of 1,300 characters each fit in what remains.
PIECE_CHARS = 1300
QUESTION = {
    "uses_result": {
        "type": "noul",
        # Of four phrasings tried on 2026-09-26 against eight labelled pairs,
        # this one separated the two classes by the widest margin.
        "instructions": "Does the agent_reasoning use information from the fetch_result?",
    }
}


def pieces(text: str, size: int = PIECE_CHARS) -> list[str]:
    """Cut text into pieces of at most ``size`` characters, at whitespace."""
    out, rest = [], text.strip()
    while rest:
        if len(rest) <= size:
            out.append(rest)
            break
        cut = rest.rfind(" ", 0, size)
        cut = cut if cut > size // 2 else size
        out.append(rest[:cut])
        rest = rest[cut:].lstrip()
    return out


def judge(result: str, reasoning: str) -> tuple[float, str, str] | None:
    """The highest probability over all piece pairs, with the pair that gave it."""
    best = None
    for r in pieces(result):
        for t in pieces(reasoning):
            answers = laya.ask(
                {"fetch_result": r, "agent_reasoning": t}, QUESTION, model="typed-decisions"
            )
            if answers is None:
                return None
            p = float(answers["uses_result"]["noul"])
            if best is None or p > best[0]:
                best = (p, r, t)
    return best


# A figure worth matching: a decimal, or three digits or more. Small whole
# numbers ("2 shares", "day 5") appear everywhere and prove nothing.
_FIGURE = re.compile(r"\d[\d,]*\.\d+|\d{3,}")


def figures(text: str) -> set[str]:
    return {f.replace(",", "") for f in _FIGURE.findall(text or "")}


def judge_by_figures(result: str, reasoning: str, prompt: str) -> tuple[float, str, str]:
    """1.0 and the figures when the reasoning carries one only the result had."""
    only_here = figures(result) - figures(prompt)
    used = sorted(only_here & figures(reasoning))
    return (1.0 if used else 0.0), ", ".join(only_here and sorted(only_here)[:12] or []), ", ".join(used)


def tool_turns(run: AgentRun):
    """Each tool-channel turn of a pass that fetched something."""
    for turn in json.loads(run.turns or "[]"):
        if turn.get("channel") == "tool" and turn.get("exchanges"):
            yield turn


def audit(runs: list[AgentRun], threshold: float, judge_with: str | None, evidence: bool) -> dict:
    calls = collections.Counter()
    called_in = collections.defaultdict(set)
    quoted_in = collections.defaultdict(set)
    rows = []
    tool_passes = 0
    for run in runs:
        turns = list(tool_turns(run))
        if not turns and not any(
            t.get("channel") == "tool" for t in json.loads(run.turns or "[]")
        ):
            continue
        tool_passes += 1
        for turn in turns:
            reasoning = "\n\n".join(
                part for part in (turn.get("reasoning"), turn.get("thinking")) if part
            )
            for ex in turn["exchanges"]:
                name = ex["name"]
                calls[name] += 1
                called_in[name].add(run.id)
                row = {"pass": run.id, "fetch": name, "args": ex.get("args"), "p": None}
                if judge_with and reasoning and ex.get("result"):
                    if judge_with == "figures":
                        verdict = judge_by_figures(str(ex["result"]), reasoning, turn.get("prompt") or "")
                    else:
                        verdict = judge(ex["result"], reasoning)
                    if verdict is None:
                        sys.exit("laya stopped answering. Check LAYA_URL.")
                    row["p"], row["result_piece"], row["reasoning_piece"] = verdict
                    if row["p"] >= threshold:
                        quoted_in[name].add(run.id)
                    if evidence:
                        print(f"\npass {run.id} {name} {ex.get('args')} p={row['p']:.2f}", file=sys.stderr)
                        # figures: what only the result had, and what of it the reasoning used.
                        # laya: the result piece and the reasoning piece that scored highest.
                        print(f"  from the result:    {row['result_piece'][:300]!r}", file=sys.stderr)
                        print(f"  in the reasoning:   {row['reasoning_piece'][:300]!r}", file=sys.stderr)
                rows.append(row)
    return {
        "tool_passes": tool_passes,
        "calls": calls,
        "called_in": called_in,
        "quoted_in": quoted_in,
        "rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--db", help="path to trading.db; default is the app's own database")
    parser.add_argument("--since", default="2026-09-17", help="first day, YYYY-MM-DD (exchanges are stored from 2026-09-17)")
    parser.add_argument("--judge", choices=["figures", "laya"], default="figures", help="how to decide that a pass quoted a fetch")
    parser.add_argument("--threshold", type=float, default=0.4, help="laya probability that counts as quoted")
    parser.add_argument("--evidence", action="store_true", help="print the best-scoring pair for each call")
    parser.add_argument("--json", help="write every judged call to this file")
    args = parser.parse_args()

    if args.db:
        engine = create_engine(f"sqlite:///file:{args.db}?mode=ro&uri=true")
    else:
        from backend.database.engine import engine
    since = datetime.datetime.fromisoformat(args.since)
    with Session(engine) as session:
        runs = session.exec(
            select(AgentRun).where(AgentRun.ran_at >= since).order_by(AgentRun.id)
        ).all()

    judge_with = args.judge
    if judge_with == "laya" and laya.laya_url() is None:
        print("LAYA_URL is not set. Counting calls only.", file=sys.stderr)
        judge_with = None
    result = audit(runs, args.threshold, judge_with, args.evidence)

    print(f"\n{result['tool_passes']} tool-channel passes since {args.since}\n")
    print(f"| Fetch | Passes that called it | Calls per calling pass | Passes that quoted it |")
    print(f"|---|---|---|---|")
    for name in FETCH_NAMES + sorted(set(result["calls"]) - set(FETCH_NAMES)):
        passes = len(result["called_in"][name])
        per = f"{result['calls'][name] / passes:.1f}" if passes else "-"
        quoted = str(len(result["quoted_in"][name])) if judge_with else "-"
        print(f"| `{name}` | {passes} | {per} | {quoted} |")

    if args.json:
        with open(args.json, "w") as f:
            json.dump(result["rows"], f, indent=1)
        print(f"\nWrote {len(result['rows'])} judged calls to {args.json}", file=sys.stderr)


if __name__ == "__main__":
    main()
