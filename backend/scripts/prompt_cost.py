"""Where the tokens of a tool-channel pass go, for a person to decide what to trim.

Item 3 of the "Later" list in PLAN.md. A tool-channel pass costs about ten
times the prompt tokens of a JSON-channel pass, because each round resends the
whole conversation, fetch results included. This script trims nothing. It
prints three tables:

1. Each section of the user prompt, by its ``## `` heading: in how many turns
   it appeared, and its mean and largest size.
2. Each fetch: how often it was called, and the mean and largest size of what
   it returned.
3. Each pass: the prompt tokens the API reported for all its rounds together,
   beside the size of its prompt and of its fetch results.

    python -m backend.scripts.prompt_cost
    python -m backend.scripts.prompt_cost --db /path/to/trading.db --since 2026-09-19

Sizes are characters divided by four, an estimate of tokens. Only the third
table's "reported" column is a real token count.
"""
import argparse
import collections
import datetime
import json
import re

from sqlalchemy import create_engine
from sqlmodel import Session, select

from backend.database.models import AgentRun
from backend.services import agent

_HEADING = re.compile(r"^## (.+)$", re.M)


def tok(chars: float) -> int:
    return round(chars / 4)


def sections(prompt: str) -> dict[str, int]:
    """Characters under each ``## `` heading. Text above the first is "(opening)"."""
    out = {}
    marks = [(m.start(), m.group(1).strip()) for m in _HEADING.finditer(prompt)]
    starts = [(0, "(opening)")] + marks
    for (start, name), (end, _) in zip(starts, starts[1:] + [(len(prompt), "")]):
        out[name] = out.get(name, 0) + end - start
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--db", help="path to trading.db; default is the app's own database")
    parser.add_argument("--since", default="2026-09-17", help="first day, YYYY-MM-DD")
    args = parser.parse_args()

    if args.db:
        engine = create_engine(f"sqlite:///file:{args.db}?mode=ro&uri=true")
    else:
        from backend.database.engine import engine
    with Session(engine) as session:
        runs = session.exec(
            select(AgentRun)
            .where(AgentRun.ran_at >= datetime.datetime.fromisoformat(args.since))
            .order_by(AgentRun.id)
        ).all()

    by_section = collections.defaultdict(list)
    by_fetch = collections.defaultdict(list)
    passes = []
    turns_seen = 0
    for run in runs:
        turns = [t for t in json.loads(run.turns or "[]") if t.get("channel") == "tool"]
        if not turns:
            continue
        prompt_chars = fetch_chars = 0
        for turn in turns:
            turns_seen += 1
            prompt = turn.get("prompt") or ""
            prompt_chars += len(prompt)
            for name, size in sections(prompt).items():
                by_section[name].append(size)
            for ex in turn.get("exchanges") or []:
                if "result" in ex:
                    size = len(str(ex["result"]))
                    by_fetch[ex["name"]].append(size)
                    fetch_chars += size
        passes.append((run.id, len(turns), run.prompt_tokens, prompt_chars, fetch_chars))

    system = len(agent._system_prompt(True))
    print(f"\n{len(passes)} tool-channel passes, {turns_seen} turns, since {args.since}.")
    print(f"The system prompt is ≈{tok(system):,} tokens and is sent with every round.\n")

    print("| Section of the user prompt | Turns | Mean ≈tokens | Largest ≈tokens |")
    print("|---|---|---|---|")
    for name, sizes in sorted(by_section.items(), key=lambda kv: -sum(kv[1]) / turns_seen):
        print(f"| {name} | {len(sizes)} | {tok(sum(sizes) / len(sizes)):,} | {tok(max(sizes)):,} |")

    print("\n| Fetch | Calls | Mean ≈tokens | Largest ≈tokens |")
    print("|---|---|---|---|")
    for name, sizes in sorted(by_fetch.items(), key=lambda kv: -sum(kv[1])):
        print(f"| `{name}` | {len(sizes)} | {tok(sum(sizes) / len(sizes)):,} | {tok(max(sizes)):,} |")

    print("\n| Pass | Turns | Reported prompt tokens | Prompts ≈tokens | Fetch results ≈tokens |")
    print("|---|---|---|---|---|")
    for rid, n, reported, prompt_chars, fetch_chars in passes:
        shown = f"{reported:,}" if reported else "-"
        print(f"| {rid} | {n} | {shown} | {tok(prompt_chars):,} | {tok(fetch_chars):,} |")


if __name__ == "__main__":
    main()
