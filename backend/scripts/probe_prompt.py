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

**One variant asks about the prompt rather than answering it.** ``--turn
layout`` shows the real turn-1 prompt, swaps the answer shape for three
questions about how the prompt reads, and asks for prose. Use it to generate
moves worth probing, never to settle one: a stated preference is a hypothesis,
and the behavioural probe is what decides.

**On Gemini this makes the same call the app makes**: one forced ``decide``
through Google's SDK (see llm_gemini), so the probe tests the channel
production uses and not a text version of it. ``--parallel`` then means
``--samples`` at once, and the throttle spaces them inside the vendor's limit.
Run it inside a container: the host cannot reach Google (see
.claude/rules/llm-providers.md).

See .claude/skills/probe-the-prompt/SKILL.md for how to read what comes back.
"""
import argparse
import datetime
import json
import time
from concurrent.futures import ThreadPoolExecutor
from backend import paths
from backend.database import db
from backend.services import (
    agent, agent_book, analysis, analysis_reader, decision_schema, llm_gemini, positions, research,
)

_OUT = paths.data_dir() / "probe"

# The prefix of every turn that asks for prose instead of a decision. Such a
# turn goes out as a plain text call, not the forced `decide` the app makes,
# and it carries no fetches.
_PROSE_PREFIX = "layout"

# The two headings `build_prompt` can give its last section, one per channel.
# The `layout` variant cuts that section off and puts its own questions there,
# so it checks the heading first and stops rather than cut the wrong one.
_ANSWER_SHAPE_HEADINGS = ("## How to answer", "## Answer in this shape")

# What the `layout` variant asks instead of a decision.
#
# **Question 3 is the one that matters.** It asks about something the model can
# observe — what it had to carry from one part of the prompt to another — and
# that is the defect the section restructure exists to fix. Questions 1 and 2
# ask about attention, which the model cannot observe, and a stated preference
# is a hypothesis rather than evidence: the holdings price-range column was
# probed nine times across three placements and read zero times, yet a model
# asked "would a price range help?" would say yes every time.
#
# The decision comes first because question 1 asks which parts produced it. A
# model that never decided has nothing to report on.
_LAYOUT_ASK = """## Answer a different question this time

This pass is not a trading pass. Nobody will act on what you write here, and
nothing below reaches the broker. Call no function. Write prose.

First, in one sentence: what would you do with this account now?

Then answer these three questions.

1. Which parts of what you were given above did you use to get to that answer,
   and which parts did you not open at all?
2. If you could put those parts in any order, what order would you want, and why?
3. Was there anything you had to hold in your head from one part while you read
   another?"""

# The one sentence of the system message the `layout` variant replaces. Every
# rule below it stays word for word, because the rules are part of what is
# under test. Only the channel changes.
_LAYOUT_ANSWERS = "You answer in prose this time. You call no function."


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
    price_ranges = {
        h.ticker: r for h in book.holdings
        if (r := agent.price_range_since_purchase(h.ticker, h.opened, h.price)) is not None
    }
    common = dict(
        # The channel the app would really answer on, so a Gemini probe sees
        # the one-line "call decide" ending and an Ollama probe the JSON shape.
        answer_by_tool=agent.answers_by_tool(),
        price_ranges=price_ranges,
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
    # A woken pass, with a note the previous one left. Without these the probe
    # cannot see whether the agent reads either.
    # The scheduler's real wording, never a copy of it. A probe that hardcodes
    # the prompt it is testing stops testing the prompt.
    from backend.tasks.scheduler import _WOKE_BECAUSE

    woke = _WOKE_BECAUSE["Event-driven"]
    out = {
        # What a Gemini probe's fetch functions can reach: the same book,
        # prices and watchlist the prompt was built from, so a `watchlist`
        # or `candidates` call answers as it would in a pass. A fresh
        # allowance is made per sample by main().
        "tools_kwargs": dict(
            book=book, prices=prices, watchlist=common["watchlist"],
            max_watchlist=common["max_watchlist"], closed=common["closed"], day_ranges={},
        ),
        "system": agent.SYSTEM_PROMPT_TOOL if agent.answers_by_tool() else agent.SYSTEM_PROMPT,
        "turn1": agent.build_prompt(book, signals, prices, **common),
        "woken": agent.build_prompt(
            book, signals, prices,
            woke_because=woke,
            # The alert bound hides anything older than the last pass, which is
            # correct and leaves this variant with nothing to point at. The raw
            # rows stand in, so the section under test actually exists.
            wakeup_note=agent._last_wakeup_note()
            or "Waiting to see whether AVGO breaks $366.16 after the open; ruled out HPE and INTC on entry price, not worth re-reading.",
            **(common | {"alerts": _raw_alerts()}),
        ),
    }
    # An early wake: a change to the app woke the agent while its own planned
    # wakeup was still ahead. The stored plan is used when it is in the future;
    # otherwise one two days out stands in, so the section under test exists.
    planned = agent._last_planned_wakeup()
    if planned is None or planned <= datetime.datetime.now(datetime.timezone.utc):
        planned = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=2)
    out["change"] = agent.build_prompt(
        book, signals, prices,
        woke_because=_WOKE_BECAUSE["Change"],
        wakeup_note=agent._last_wakeup_note()
        or "Market reopens today. Review tracked tickers and consider deploying capital if attractive setups emerge.",
        planned_wakeup=planned,
        **common,
    )
    # Turn 2: what the agent sees after research it ordered has landed inside
    # this same pass. No runner is installed, so nothing is analysed — the
    # reading comes from what is already on record.
    held = book.holdings[0].ticker if book.holdings else (sorted(tickers)[0] if tickers else None)
    if held:
        agent.set_research_runner(lambda tickers: None)
        # A tuple of (lines, failed) since 2026-09-16. Assigning the tuple
        # whole rendered the outcomes section as a stringified list and a
        # bare "- {}" in every turn2 probe run between then and 2026-09-21.
        outcomes, _failed_research = agent._research_and_report([held])
        out["turn2"] = agent.build_prompt(
            book, signals, prices,
            outcomes=outcomes,
            researched_now={held},
            **common,
        )
        # The second turn of a pass a change to the app started: the wake is
        # history by now, and the reason to be asked again is the research.
        out["change_turn2"] = agent.build_prompt(
            book, signals, prices,
            outcomes=outcomes,
            researched_now={held},
            woke_because=_WOKE_BECAUSE["Change"],
            wakeup_note=agent._last_wakeup_note(),
            planned_wakeup=planned,
            **common,
        )
        out["held"] = held
    # "read": what a read looks like since it started carrying a short take
    # from each of the four analysts, not only the rationale (2026-09-15).
    # Picks the newest signal that actually has analyst reports on record —
    # an old row, or one whose reports never saved, would leave the section
    # under test empty.
    reported = _signal_with_reports()
    if reported:
        out["read"] = agent.build_prompt(
            book, signals, prices,
            readings=[analysis_reader.read(reported.ticker, str(reported.signal_date)[:10])],
            **common,
        )
        out["read_signal"] = f"{reported.ticker} {reported.signal_date}"
    # "retry": the turn after an answer that was partly carried out, partly
    # refused, and partly dropped for riding along beside a read. All four
    # reasons to be asked again in one prompt.
    #
    # **No other variant carries a refusal at all**, so nothing here could
    # measure where the correction sits or whether it is acted on — which is
    # exactly what a pass has one turn left to get right.
    if reported and held:
        out["retry"] = agent.build_prompt(
            book, signals, prices,
            outcomes=outcomes,
            researched_now={held},
            readings=[analysis_reader.read(reported.ticker, str(reported.signal_date)[:10])],
            dropped_with_read=[{"ticker": held, "side": "sell", "quantity": 1}],
            rejected=[agent_book.Rejection(
                ticker=reported.ticker, side="buy", quantity=500,
                why=f"costs more than the ${book.cash:,.2f} you have",
            )],
            woke_because=_WOKE_BECAUSE["Alarm"],
            **common,
        )
    # "layout": the agent asked about the prompt itself. `CLAUDE.md` already
    # treats "I cannot see X" as evidence, and this points the same mechanism
    # at a different target. It is a probe, out of the app, so it reaches no
    # broker path and adds no second decision-maker to the record.
    #
    # **Two arms, because how many sections are present is the whole point.**
    # `turn1` is the prompt the agent sees most often, and on a quiet day a
    # whole group of it is missing. `retry` is the fullest prompt this script
    # can build — every one of the four groups has something in it. Asking
    # about an order on a prompt that is missing a third of its sections would
    # answer a question nobody asked.
    out["layout"] = _layout_prompt(out["turn1"])
    if "retry" in out:
        out["layout_retry"] = _layout_prompt(out["retry"])
    out["system_layout"] = _layout_system(out["system"])
    return out


def _layout_prompt(prompt: str) -> str:
    """A real prompt, with the answer shape swapped for the questions.

    `_joined` separates every section with one rule, so the last section is
    whatever follows the last rule. It must be the answer shape, and this stops
    rather than cut a different one: a probe that quietly measures the wrong
    prompt tells you nothing and looks like it worked.
    """
    body, rule, last = prompt.rpartition("\n\n---\n\n")
    if not last.startswith(_ANSWER_SHAPE_HEADINGS):
        raise SystemExit(
            f"The last section is {last.splitlines()[0]!r}, not the answer shape. "
            f"`build_prompt` renamed it or moved it — update _ANSWER_SHAPE_HEADINGS."
        )
    return body + rule + _LAYOUT_ASK


def _layout_system(system: str) -> str:
    """The real system message, saying prose where it said to call `decide`."""
    said = agent._HOW_TO_ANSWER[agent.answers_by_tool()]
    if said not in system:
        raise SystemExit(f"The system message no longer says {said!r}.")
    return system.replace(said, _LAYOUT_ANSWERS)


def _signal_with_reports():
    """The newest signal that has at least one analyst report saved."""
    for row in db.get_recent_signals(limit=200):
        if row.id and db.get_signal_reports(row.id):
            return row
    return None


def _raw_alerts() -> list[dict]:
    """Every recent alert, ignoring the "since your last pass" bound.

    That bound is right in the app and leaves this probe variant with an empty
    section on a quiet day — so the thing under test would not be in the prompt.
    """
    import datetime as _dt
    out = []
    for row in db.get_recent_alerts(limit=8):
        at = getattr(row, "created_at", None)
        out.append({
            "at": at.strftime("%-d %b %-I:%M %p") if at else "",
            "text": str(getattr(row, "message", "") or "").lstrip("\U0001F4CA\U0001F514\u26A0\uFE0F ").strip(),
        })
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


def ask(base_url: str, system: str, user: str, tools=None, prose: bool = False) -> dict:
    """One call, with the message shape the app uses.

    ``agent._invoke`` prepends SYSTEM_PROMPT itself, so a probe that hands it
    system+user concatenated sends the system prompt twice. This sends the two
    as the two messages they are. ``tools`` is a fetch context for a Gemini
    probe, so the fetch loop runs as it does in a pass. ``prose`` asks for text
    rather than a decision, which only the ``layout`` variant wants; a local
    model answers in text anyway, so it changes nothing off Gemini.
    """
    if agent.answers_by_tool():
        return _ask_gemini_text(system, user) if prose else _ask_gemini(system, user, tools)
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


def _ask_gemini(system: str, user: str, tools=None) -> dict:
    """The call the app makes on Gemini, so the probe tests that channel.

    ``answer`` is the ``decide`` call's arguments as JSON, exactly what a pass
    records as its response. ``thinking`` is Google's summary of the model's
    thinking, present only at a stated thinking level. ``exchanges`` is every
    fetch the model made on the way, with what it was handed back: a read
    goes to the record, a ``candidates`` call to the vendor screen, none of
    it to the broker.
    """
    started = time.monotonic()
    reply = llm_gemini.decide(
        system, user, decision_schema.DECIDE, model=analysis.get_model(),
        fetches=decision_schema.FETCHES if tools is not None else (),
        fetch=tools.fetch if tools is not None else None,
        budget=tools.budget if tools is not None else None,
    )
    return {
        "seconds": round(time.monotonic() - started, 1),
        "prompt_tokens": reply.prompt_tokens,
        "completion_tokens": reply.completion_tokens,
        "thinking": reply.thinking or "",
        "answer": reply.content,
        "exchanges": reply.exchanges,
    }


def _ask_gemini_text(system: str, user: str) -> dict:
    """One Gemini call that answers in text, for a variant asking about itself.

    ``llm_gemini.decide`` forces a ``decide`` call with ``mode ANY``, which is
    the right channel for a decision and the wrong one for a question about the
    prompt. This declares no function at all.

    It still goes through ``llm_gemini.client()``, so the call counts against
    the same per-minute and per-day limits an analysis does. A probe that
    skipped the throttle would spend the day's requests without saying so.
    """
    from google.genai import types

    started = time.monotonic()
    response = llm_gemini.client().models.generate_content(
        model=analysis.get_model(),
        contents=[types.Content(role="user", parts=[types.Part(text=user)])],
        config=types.GenerateContentConfig(
            system_instruction=system,
            thinking_config=llm_gemini.thinking_config(),
        ),
    )
    parts, _content = llm_gemini._parts(response)
    used = getattr(response, "usage_metadata", None)
    said = [p for p in parts if getattr(p, "text", None)]
    return {
        "seconds": round(time.monotonic() - started, 1),
        "prompt_tokens": int(getattr(used, "prompt_token_count", 0) or 0),
        "completion_tokens": int(getattr(used, "candidates_token_count", 0) or 0)
        + int(getattr(used, "thoughts_token_count", 0) or 0),
        "thinking": "\n\n".join(p.text for p in said if getattr(p, "thought", False)),
        "answer": "\n".join(p.text for p in said if not getattr(p, "thought", False)),
    }


def _tools(prompts: dict):
    """A fresh fetch context per sample on Gemini, None elsewhere."""
    if not agent.answers_by_tool():
        return None
    return agent.ToolContext(agent._fresh_budget(), **prompts["tools_kwargs"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--turn", default="turn1", help="turn1, turn2, read, layout, or one of the other variants build_prompts() names")
    parser.add_argument("--samples", type=int, default=2, help="serial samples; ignored with --parallel")
    parser.add_argument("--parallel", action="store_true", help="one sample per GPU, all at once")
    parser.add_argument("--base-url", default="http://localhost:11435/v1", help="serial endpoint")
    args = parser.parse_args()

    prompts = build_prompts()
    if args.turn not in prompts:
        print(f"No {args.turn} prompt to build (the book may hold nothing).")
        return 1
    user = prompts[args.turn]
    # A prose turn carries its own system message and no fetches: the questions
    # are about the prompt in front of the model, not about anything it could
    # go and look up.
    prose = args.turn.startswith(_PROSE_PREFIX)
    system = prompts["system_layout"] if prose else prompts["system"]
    print(f"{args.turn}: {len(user):,} characters")
    if prose:
        print("asking for prose, so no decide call and no fetches")

    if args.parallel:
        cards = _backends()
        if agent.answers_by_tool():
            # No cards on Gemini. --parallel means --samples at once, and the
            # throttle spaces them inside the vendor's per-minute limit.
            cards = {f"sample {n}": None for n in range(1, args.samples + 1)}
        if not cards:
            print("No pool containers found — falling back to the proxy.")
            cards = {"proxy": None}
        print(f"running on {len(cards)} card(s) at once")
        print("**Wall clock here is not comparable with a serial run** — the cards")
        print("share host memory bandwidth, so they slow each other down.")

        def one(item):
            card, ip = item
            url = f"http://{ip}:11434/v1" if ip else args.base_url
            row = ask(url, system, user, None if prose else _tools(prompts), prose) | {
                "card": card, "turn": args.turn,
            }
            print(f"  card {card}: {row['seconds']:>5}s  completion {row['completion_tokens']:>5}"
                  f"  thinking {len(row['thinking']):>5}  fetched {len(row.get('exchanges', []))}",
                  flush=True)
            return row

        started = time.monotonic()
        with ThreadPoolExecutor(max_workers=len(cards)) as pool:
            results = list(pool.map(one, cards.items()))
        print(f"wall clock for all {len(results)}: {time.monotonic() - started:.0f}s")
    else:
        results = []
        for sample in range(1, args.samples + 1):
            row = ask(args.base_url, system, user, None if prose else _tools(prompts), prose) | {
                "card": "-", "turn": args.turn, "sample": sample,
            }
            print(f"  #{sample}: {row['seconds']}s  completion {row['completion_tokens']}"
                  f"  thinking {len(row['thinking'])}  fetched {len(row.get('exchanges', []))}",
                  flush=True)
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
