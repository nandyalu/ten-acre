"""Send the real prompt to the real model, out of the app, and keep the reasoning.

**A prompt change is a hypothesis until the model's own reasoning confirms it.**
Every prompt bug found in this project was found this way: a rule the agent
applied to the wrong thing, two sections it never read, a result it treated as
somebody else's. None was visible in the code, the tests, or the rendered
prompt — the sections were correct and correctly rendered, and the model read
past them.

**This cannot trade.** It builds a prompt and calls the model. It never reaches
run_once, screen, or any broker path.

    python -m backend.scripts.probe_prompt --turn turn1
    python -m backend.scripts.probe_prompt --turn turn2 --parallel

See .claude/skills/probe-the-prompt/SKILL.md for how to read what comes back.
"""
import argparse
import datetime
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from backend.database import db
from backend.services import agent, agent_book, analysis, positions, research

_OUT = Path("data/probe")


def build_prompts() -> dict:
    """The prompts a pass would really build, from the database as it stands.

    Prices come from the cache rather than the vendor: this is a read-only
    probe, the vendor is paced at three seconds a call, and what is under test
    is the shape of the prompt rather than the freshness of a quote.
    """
    signals = agent._recent_signals()
    empty = agent_book.build_book(price_lookup=lambda t: None)
    tickers = set(db.get_watchlist()) | {s.ticker for s in signals} | {h.ticker for h in empty.holdings}
    prices = {}
    for ticker in sorted(tickers):
        try:
            prices[ticker] = positions.get_shown_price(ticker)
        except Exception:
            prices[ticker] = None

    book = agent_book.build_book(price_lookup=prices.get)
    decisions = {s.id: s.decision for s in db.get_recent_signals(limit=200) if s.id}
    common = dict(
        closed=agent_book.closed_trades(decisions=decisions),
        regime_line=agent.current_regime_line(),
        horizon_days=agent._horizon_days(),
        menu=agent._candidate_menu() if research.is_charging() else None,
        price=research.get_price(),
        watchlist=sorted(db.get_watchlist()),
        max_watchlist=agent._max_watchlist(),
        failures=agent._recent_broker_failures(),
        wakeups=agent._recent_wakeups(),
        analysis_minutes=analysis.recent_durations(),
        running_analyses=analysis.in_flight(),
        changes=agent._recent_changes(),
        alerts=agent._recent_alerts(),
        earnings=agent._earnings_due(),
    )
    out = {
        "system": agent.SYSTEM_PROMPT,
        "turn1": agent.build_prompt(book, signals, prices, **common),
    }
    # Turn 2: what the agent sees after research it ordered has landed inside
    # this same pass. No runner is installed, so nothing is analysed — the
    # reading comes from what is already on record.
    held = book.holdings[0].ticker if book.holdings else (sorted(tickers)[0] if tickers else None)
    if held:
        agent.set_research_runner(lambda tickers: None)
        out["turn2"] = agent.build_prompt(
            book, signals, prices,
            outcomes=agent._research_and_report([held]),
            researched_now={held},
            **common,
        )
        out["held"] = held
    return out


def _backends() -> dict[str, str]:
    """Each pool container's own address, so a sample lands on a known card.

    Read from docker rather than hardcoded: the addresses move when the stack
    restarts, and a stale one sends every sample to the same place.
    """
    import subprocess
    found = {}
    for name in "abcdefg":
        try:
            ip = subprocess.run(
                ["docker", "inspect", "-f",
                 "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
                 f"ollama-pool-{name}"],
                capture_output=True, text=True, timeout=10,
            ).stdout.strip()
        except Exception:
            ip = ""
        if ip:
            found[name] = ip
    return found


def ask(base_url: str, system: str, user: str) -> dict:
    """One call, with the message shape the app uses.

    ``agent._invoke`` prepends SYSTEM_PROMPT itself, so a probe that hands it
    system+user concatenated sends the system prompt twice. This sends the two
    as the two messages they are.
    """
    from openai import OpenAI

    client = OpenAI(base_url=base_url, api_key="ollama", timeout=900)
    started = time.monotonic()
    raw = client.chat.completions.create(
        model=analysis.get_model(),
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
    )
    message = raw.choices[0].message
    thinking = getattr(message, "reasoning", None) or (message.model_extra or {}).get("reasoning") or ""
    return {
        "seconds": round(time.monotonic() - started, 1),
        "prompt_tokens": raw.usage.prompt_tokens,
        "completion_tokens": raw.usage.completion_tokens,
        "thinking": thinking,
        "answer": message.content or "",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--turn", default="turn1", help="turn1 (a fresh pass) or turn2")
    parser.add_argument("--samples", type=int, default=2, help="serial samples; ignored with --parallel")
    parser.add_argument("--parallel", action="store_true", help="one sample per GPU, all at once")
    parser.add_argument("--base-url", default="http://localhost:11435/v1", help="serial endpoint")
    args = parser.parse_args()

    prompts = build_prompts()
    if args.turn not in prompts:
        print(f"No {args.turn} prompt to build (the book may hold nothing).")
        return 1
    user = prompts[args.turn]
    print(f"{args.turn}: {len(user):,} characters")

    if args.parallel:
        cards = _backends()
        if not cards:
            print("No pool containers found — falling back to the proxy.")
            cards = {"proxy": None}
        print(f"running on {len(cards)} card(s) at once")
        print("**Wall clock here is not comparable with a serial run** — the cards")
        print("share host memory bandwidth, so they slow each other down.")

        def one(item):
            card, ip = item
            url = f"http://{ip}:11434/v1" if ip else args.base_url
            row = ask(url, prompts["system"], user) | {"card": card, "turn": args.turn}
            print(f"  card {card}: {row['seconds']:>5}s  completion {row['completion_tokens']:>5}"
                  f"  thinking {len(row['thinking']):>5}", flush=True)
            return row

        started = time.monotonic()
        with ThreadPoolExecutor(max_workers=len(cards)) as pool:
            results = list(pool.map(one, cards.items()))
        print(f"wall clock for all {len(results)}: {time.monotonic() - started:.0f}s")
    else:
        results = []
        for sample in range(1, args.samples + 1):
            row = ask(args.base_url, prompts["system"], user) | {"card": "-", "turn": args.turn, "sample": sample}
            print(f"  #{sample}: {row['seconds']}s  completion {row['completion_tokens']}"
                  f"  thinking {len(row['thinking'])}", flush=True)
            results.append(row)

    _OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    path = _OUT / f"{args.turn}-{stamp}.json"
    path.write_text(json.dumps({"prompt": user, "runs": results}, indent=1))
    print(f"\nwritten to {path}")
    print("Read the reasoning, do not grep it — see .claude/skills/probe-the-prompt/SKILL.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
