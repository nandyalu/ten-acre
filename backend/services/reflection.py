"""The evening review: the agent reads its own day, then speaks twice.

**A decision pass never sees its own day.** It sees prices, positions and the
note the last pass left, and decides. Nothing shows it the passes it ran, what
its trades did after, or the analyses it paid for and what the price did
since. Once a trading day, after the close and the grading, this shows it all
of that and asks two things, in two turns of one conversation:

1. **To the maintainer**, through ``report``: what stood between it and a
   better decision — a tool it lacked, information it could not see, a rule
   that contradicted another. Each note names the pass where it was felt.
   An empty list is the expected answer on most days.
2. **To itself**, through ``revise``: a new note for the next pass in place
   of the one its last pass left, and changes to its memory notes. Both
   optional. Keeping everything is a valid answer.

**It cannot act.** No order, no research, no untrack, and no change to the
next wakeup: the last pass chose the alarm, and a review that moved it would
be a decision pass in disguise. The two channels stay apart on purpose. A
note to the maintainer never reaches the agent's own prompt, or the review
becomes a self-training loop; a memory note is the agent talking to itself.

**Why two turns and not one.** Every probe on record shows one obvious job
crowding out the rest, so the two questions are asked apart, and the
maintainer note comes first so the self-review does not make it an
afterthought. Turn 2 sees turn 1, so the guard against working around a
reported gap has something to point at. Probed before it was built, on the
week of 2026-09-14: see ``.claude/rules/agent-probes.md``.

**The guards in the second turn each answer one way this goes wrong**, and
each was seen in that probe or predicted by the record. A rule from one
trade: memory notes are followed (3 of 4 samples passed on a live signal
because a note said so), so a lesson from one trade is written as a
hypothesis with its count. Outcome bias: a loss is not a mistake by itself.
Restating a rule: two of three probe notes repeated the sizing rule, and a
memory slot that repeats a rule is a slot lost. Churn: an empty memory pulls
an add, so the prompt says most reviews add nothing. Workarounds: a tool the
agent lacks is the maintainer's to build.

**The window is the last review, not the calendar day.** Everything since the
previous review's ``ran_at`` is in, or the last 24 hours when there is none.
A Saturday pass is reviewed on Monday. A window with no pass is skipped and
records nothing.
"""
from __future__ import annotations

import datetime
import json
import logging
import time
from dataclasses import dataclass, field

from google.genai import types

from backend.database import db
from backend.notifications.embed import Color, Embed
from backend.services import agent, agent_book, analysis, llm_gemini, market_clock

log = logging.getLogger("ten-acre.reflection")

# With no earlier review to bound the window, this is how far back it reaches.
WINDOW_FALLBACK = datetime.timedelta(hours=24)
# A turn's reasoning is shown to this length. The stored per-turn reasoning
# runs to a few hundred characters; the cap is for the older passes whose
# reasoning lives inside the response text and can run long.
REASONING_CAP = 1400
KINDS = ["missing_tool", "missing_information", "rule_conflict", "other"]

# The two functions, in the shape Google's SDK takes. Only the keywords the
# decide schema uses, for the reason given there: a keyword the vendor does
# not support fails the whole call.
REPORT = {
    "name": "report",
    "description": (
        "Send your notes to the people who maintain your tools. An empty list "
        "means nothing stood in your way."
    ),
    "parameters_json_schema": {
        "type": "object",
        "properties": {
            "notes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": KINDS},
                        "pass_id": {
                            "type": "integer",
                            "description": "The pass where you felt it, from the list you were shown.",
                        },
                        "what_was_missing": {
                            "type": "string",
                            "description": (
                                "The tool, the information, or the contradiction, in one or "
                                "two sentences."
                            ),
                        },
                        "what_you_would_have_done": {
                            "type": "string",
                            "description": (
                                "The decision you would have made with it, in one or two sentences."
                            ),
                        },
                    },
                    "required": ["kind", "pass_id", "what_was_missing", "what_you_would_have_done"],
                },
            }
        },
        "required": ["notes"],
    },
}

REVISE = {
    "name": "revise",
    "description": (
        "Change what your future self will see. Leave a field out to keep it as "
        "it is. Call it with nothing to keep everything."
    ),
    "parameters_json_schema": {
        "type": "object",
        "properties": {
            "wakeup_note": {
                "type": "string",
                "description": (
                    "Replaces the note your last pass left for the next one. Leave it "
                    "out to keep that note."
                ),
            },
            "memory": {
                "type": "array",
                "description": "Changes to your memory notes, in order. Leave it out to keep them.",
                "items": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["add", "rewrite", "remove"]},
                        "index": {
                            "type": "integer",
                            "description": (
                                "For rewrite and remove: the number of the note in the list "
                                "you were shown."
                            ),
                        },
                        "text": {
                            "type": "string",
                            "description": "For add and rewrite: the note, up to 500 characters.",
                        },
                    },
                    "required": ["action"],
                },
            },
        },
    },
}

SYSTEM_PROMPT = "\n".join([
    "You are a disciplined portfolio manager. You manage a small account of real "
    "money. The session has closed, and you are reviewing your own day. You answer "
    "by calling the function you are offered, never with prose.",
    "",
    "This review has two turns. In the first you speak to the people who maintain "
    "the tools you work with. In the second you speak to your own future self, and "
    "the rules for it come with it.",
    "",
    "Rules for the first turn:",
    "- Report what stood between you and a better decision: a tool you did not "
    "have, information you could not see, or a rule that contradicted another.",
    "- Every note names the pass where you felt it, and says what you would have "
    "done with the missing thing. A note with no pass behind it is a wish, and you "
    "do not send wishes.",
    "- The rules you are given on every pass are shown below. If something is in "
    "them, you have it; do not ask for it.",
    "- A mistake of your own does not belong here. That is for the second turn.",
    "- Nothing to report is a valid answer, and on most days it is the honest one.",
])

# The example names a ticker that is not in any record on purpose. The
# pre-build probe's example named INTC, the one trade of that week, and all
# three memory notes opened with its words.
TURN2_PROMPT = "\n".join([
    "Your notes reached the maintainers. Now speak to your own future self.",
    "",
    "You have no memory between passes. Two things carry forward: the note your last "
    "pass left for the next one, shown above, and your memory notes, which every pass "
    "sees until you remove them. You may change either now, or leave both as they are.",
    "",
    "Rules for this turn:",
    "- A lesson from one trade is a hypothesis. Write it as one, with the count. "
    "\"XMPL stopped me out once on a breakout entry; watch for it\" is a note. "
    "\"Avoid XMPL breakouts\" is a rule you have not earned.",
    "- Judge a decision by what you knew when you made it. A loss is not a mistake by "
    "itself, and a gain is not proof. A lesson needs the reason, not only the result.",
    "- Memory is for what you learned, not for what you are told. A note that repeats "
    "a rule you are given on every pass is a slot lost.",
    "- Memory holds ten notes and drops the oldest when an eleventh arrives. Most "
    "reviews add nothing. Review what you have first: is each still true, should "
    "two merge, should one go.",
    "- A tool you lack is the maintainers' to build. Do not write a memory note that "
    "works around something you reported in the first turn.",
    "- The note for the next pass is a handover: what you concluded, which price "
    "level matters and why. That prompt carries the prices and positions; do not "
    "restate them.",
    "- Keeping everything as it is, is a valid answer.",
    "",
    "Call revise.",
])


@dataclass
class Day:
    """Everything the review is shown, gathered once so the prompt builder
    reads no database and a test can hand it what it likes."""

    now: datetime.datetime
    since: datetime.datetime
    # False on the first review, when ``since`` is 24 hours ago and not a
    # review the prompt can name.
    reviewed_before: bool = False
    passes: list = field(default_factory=list)
    equity_before: float | None = None
    charges: list = field(default_factory=list)
    trades: list = field(default_factory=list)
    signals: list = field(default_factory=list)
    graded: list = field(default_factory=list)
    prices: dict = field(default_factory=dict)
    memory: list = field(default_factory=list)
    last_notes: list = field(default_factory=list)
    rules: str = ""


@dataclass
class Review:
    """What one review produced, for the record and the post."""

    ran_at: datetime.datetime
    since: datetime.datetime
    passes: int
    notes: list = field(default_factory=list)
    wakeup_note: str | None = None
    memory_changes: list = field(default_factory=list)
    applied: list = field(default_factory=list)
    thinking: str | None = None
    model: str | None = None
    channel: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0.0
    skipped: str | None = None
    id: int | None = None

    @property
    def memory_changed(self) -> bool:
        return any(line.startswith("Memory: ") and "could not" not in line for line in self.applied)


class Outcome:
    """The two answers of one conversation, before anything is applied."""

    def __init__(self):
        self.report: dict | None = None
        self.revise: dict | None = None
        self.report_text = ""
        self.revise_text = ""
        self.thinking: str | None = None
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.cached_tokens = 0
        self.seconds = 0.0
        self.channel = "text"


# --- Gathering ---------------------------------------------------------------


def _utc(value: datetime.datetime | None) -> datetime.datetime | None:
    """Every timestamp in the database is naive UTC."""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=datetime.timezone.utc)


def window_start(now_utc: datetime.datetime) -> datetime.datetime:
    latest = db.get_latest_reflection()
    if latest is not None:
        return _utc(latest.ran_at)
    return now_utc - WINDOW_FALLBACK


def gather(now: datetime.datetime | None = None) -> Day:
    now_et = market_clock.now_et(now)
    now_utc = now_et.astimezone(datetime.timezone.utc)
    since = window_start(now_utc)
    runs = sorted(db.get_agent_runs(), key=lambda run: _utc(run.ran_at))
    passes = [run for run in runs if _utc(run.ran_at) > since and not run.skipped]
    before = [run for run in runs if _utc(run.ran_at) <= since and run.equity is not None]
    charges = [c for c in db.get_research_charges() if _utc(c.charged_at) > since]
    trades = [
        row for row in agent_book.trade_history()
        if _utc(row.entry_at) > since or (row.exit_at is not None and _utc(row.exit_at) > since)
    ]
    signals = [
        s for s in db.get_recent_signals(limit=60, by_time=True)
        if s.created_at is not None and _utc(s.created_at) > since
    ]
    graded = [
        s for s in db.get_resolved_signals()
        if s.evaluated_at is not None and _utc(s.evaluated_at) > since
    ]
    tickers = sorted({s.ticker for s in signals})
    prices = {t: p.price for t, p in db.get_cached_prices(tickers).items()} if tickers else {}
    rules = agent.SYSTEM_PROMPT_TOOL.split("\n\n", 2)[2]
    return Day(
        now=now_et,
        since=since,
        reviewed_before=db.get_latest_reflection() is not None,
        passes=passes,
        equity_before=before[-1].equity if before else None,
        charges=charges,
        trades=trades,
        signals=signals,
        graded=graded,
        prices=prices,
        memory=agent.get_memory_entries(),
        last_notes=agent._last_pass_notes(),
        rules=rules,
    )


# --- The prompt --------------------------------------------------------------


def _et(value: datetime.datetime | None) -> str:
    if value is None:
        return "—"
    return _utc(value).astimezone(market_clock.US_MARKET_TZ).strftime("%a %-d %b %-I:%M %p ET")


def _money(value) -> str:
    return f"${float(value):,.2f}"


def _cap(text: str | None, limit: int = REASONING_CAP) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit].rstrip() + " […]"


def _loads(value, default):
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _order_line(order: dict) -> str:
    side = str(order.get("side", "?"))
    bits = [side, str(order.get("ticker") or "")]
    quantity = order.get("quantity")
    if quantity:
        bits.append(f"{float(quantity):g}")
    for key in ("order_type", "time_in_force", "date"):
        if order.get(key):
            bits.append(str(order[key]))
    if order.get("limit_price") is not None:
        bits.append(f"at {_money(order['limit_price'])}")
    if order.get("stop") is not None:
        bits.append(f"stop {_money(order['stop'])}")
    if order.get("target") is not None:
        bits.append(f"target {_money(order['target'])}")
    line = " ".join(b for b in bits if b)
    reason = str(order.get("reason") or "").strip()
    return f"{line} — {_cap(reason, 220)}" if reason else line


def _why_line(prompt: str | None) -> str | None:
    for line in (prompt or "").splitlines():
        if line.startswith("**Why you are awake.**") or line.startswith("**Why this pass started.**"):
            return line.replace("**", "").strip()
    return None


def _asked_again_line(prompt: str | None) -> str | None:
    for line in (prompt or "").splitlines():
        if line.startswith("**Why you are asked again.**"):
            return line.replace("**", "").strip()
    return None


def _generic(value) -> list[str]:
    """A stored JSON column, whatever its shape, as short lines."""
    items = _loads(value, [])
    if isinstance(items, dict):
        items = [items]
    out = []
    for item in items:
        if isinstance(item, dict):
            out.append("; ".join(
                f"{k}: {_cap(str(v), 200)}" for k, v in item.items() if v not in (None, "", [])
            ))
        else:
            out.append(_cap(str(item), 300))
    return out


def _turns_of(run) -> list[dict]:
    """Every turn with its reasoning and orders, whatever the row's vintage.

    A turn from before 2026-09-15 keeps both only inside its stored response
    text, and a single-turn pass from before 2026-09-17 stores no turns at
    all: the pass's own reasoning and orders are that turn.
    """
    turns = _loads(run.turns, [])
    if not turns:
        turns = [{
            "prompt": run.prompt, "reasoning": run.reasoning,
            "orders": _loads(run.orders, []), "exchanges": [],
        }]
    for turn in turns:
        if turn.get("reasoning") or turn.get("orders") is not None:
            continue
        answer = _loads(turn.get("response"), {})
        if isinstance(answer, dict):
            turn["reasoning"] = str(answer.get("reasoning") or "")
            orders = list(answer.get("orders") or [])
            for key in ("research", "read", "note"):
                extra = answer.get(key)
                if isinstance(extra, list):
                    orders += [o for o in extra if isinstance(o, dict)]
                elif isinstance(extra, dict):
                    orders.append(extra)
            turn["orders"] = orders
    return turns


def _describe_pass(run, spent_before: float) -> list[str]:
    lines = [f"### Pass {run.id} — {_et(run.ran_at)}"]
    turns = _turns_of(run)
    why = run.woke_because or _why_line(turns[0].get("prompt"))
    if why:
        lines.append(f"- {why}")
    for i, turn in enumerate(turns, 1):
        again = _asked_again_line(turn.get("prompt")) if i > 1 else None
        lines.append(f"- Turn {i}. {_cap(again, 300)}" if again else f"- Turn {i}.")
        fetched = [
            f"{e.get('name')}({', '.join(f'{k}={v}' for k, v in (e.get('args') or {}).items())})"
            for e in (turn.get("exchanges") or []) if e.get("name") != "rounds"
        ]
        if fetched:
            lines.append(f"  - Fetched: {', '.join(fetched)}")
        if turn.get("reasoning"):
            lines.append(f"  - Your reasoning: {_cap(turn['reasoning'])}")
        orders = turn.get("orders") or []
        if isinstance(orders, dict):
            orders = [orders]
        if orders:
            lines.append("  - You asked for:")
            lines += [f"    - {_order_line(o)}" for o in orders if isinstance(o, dict)]
        else:
            lines.append("  - You asked for nothing.")
    for label, column in (
        ("Refused by the app", "refusals"),
        ("The broker would not take", "failures"),
        ("Notes you sent the maintainers", "notes"),
    ):
        items = _generic(getattr(run, column, None))
        if items:
            lines.append(f"- {label}:")
            lines += [f"  - {item}" for item in items]
    spent_now = float(run.research_spent or 0)
    if spent_now > spent_before + 1e-9:
        lines.append(f"- Research charged in this pass: {_money(spent_now - spent_before)}.")
    if run.equity is not None:
        lines.append(
            f"- After the pass: equity {_money(run.equity)}, cash {_money(run.cash or 0)}. "
            f"Next wakeup you chose: {_et(run.next_wakeup)}."
        )
    for note in _notes_of(run.wakeup_note):
        lines.append(f'- Note you left for the next pass: "{_cap(note, 400)}"')
    lines.append("")
    return lines


def _notes_of(raw: str | None) -> list[str]:
    raw = str(raw or "").strip()
    if not raw:
        return []
    if raw.startswith("["):
        notes = _loads(raw, None)
        if isinstance(notes, list):
            return [str(n).strip() for n in notes if str(n).strip()]
    return [raw]


def build_prompt(day: Day) -> dict:
    """The system message and both user messages, from a gathered day."""
    lines: list[str] = []
    add = lines.append
    passes = day.passes
    first, last = (passes[0], passes[-1]) if passes else (None, None)
    opened = [t for t in day.trades if _utc(t.entry_at) > day.since]
    closed = [t for t in day.trades if t.exit_at is not None and _utc(t.exit_at) > day.since]
    realized = sum(t.pnl or 0 for t in closed)
    research = sum(float(c.amount_usd) for c in day.charges)

    add("You manage a small account of real money. The session has closed, and this is your review of the day.")
    add("")
    # The clock line already opens "It is ...": the probe of 2026-09-24
    # showed the sentence twice when this line said it too.
    add(market_clock.describe(day.now))
    add("You cannot trade, research, or change your schedule here. The next pass does that, with fresh prices. This is where you look back.")
    add("")
    add("---")
    add("")
    add("## The day in numbers")
    add("")
    add("| | |")
    add("|---|---|")
    if day.equity_before is not None:
        when = "at your last review" if day.reviewed_before else "24 hours ago"
        add(f"| Equity {when} ({_et(day.since)}) | {_money(day.equity_before)} |")
    elif first is not None and first.equity is not None:
        add(f"| Equity at your first pass of this window ({_et(first.ran_at)}) | {_money(first.equity)} |")
    if last is not None and last.equity is not None:
        add(f"| Equity now | {_money(last.equity)} |")
        add(f"| Cash now | {_money(last.cash or 0)} |")
    add(f"| Decision passes since {_et(day.since)} | {len(passes)} |")
    add(f"| Analyses you commissioned | {len(day.charges)}, costing {_money(research)} |")
    add(f"| Positions opened | {len(opened)} |")
    add(f"| Positions closed | {len(closed)}, realized {_money(realized)} |")
    add("")
    add("---")
    add("")
    add("## The rules you are given on every pass")
    add("")
    add("If something is in these rules, you have it. Do not ask for it.")
    add("")
    add(day.rules)
    add("")
    add("---")
    add("")
    add("## Your memory notes")
    add("")
    add("Every pass sees these until you remove them. Memory holds up to 10 notes of up to 500 characters each, and the oldest is dropped when an eleventh arrives.")
    add("")
    if day.memory:
        for i, entry in enumerate(day.memory, 1):
            add(f"{i}. {entry['text']} ({_written(entry)})")
    else:
        add("You have no memory notes.")
    add("")
    add("---")
    add("")
    if day.last_notes:
        when = f", written {_et(last.ran_at)} for a wakeup at {_et(last.next_wakeup)}" if last is not None else ""
        add(f"## The note your last pass left for the next one{when}")
        add("")
        for note in day.last_notes:
            add(f'> "{note}"')
        add("")
        add("---")
        add("")
    if day.signals:
        add("## The analyses you bought since your last review")
        add("")
        add("What the price has done since is shown; none has reached its horizon yet. Entry, Stop and Target are computed by the app from the close and ATR; a price inside an analyst's text is the analyst's own.")
        add("")
        add("| Ticker | Analysed (ET) | Decision | Price at analysis | Entry | Stop | Target | Chance | Price now | Since analysis |")
        add("|---|---|---|---|---|---|---|---|---|---|")
        for s in day.signals:
            now = day.prices.get(s.ticker)
            move = (
                f"{(now / float(s.price_at_signal) - 1) * 100:+.1f}%"
                if now and s.price_at_signal else "—"
            )
            chance = f"{s.win_probability:g}%" if s.win_probability is not None else "—"
            add(
                f"| {s.ticker} | {_et(s.created_at)} | {s.decision} | {_fmt(s.price_at_signal)} | "
                f"{_fmt(s.entry_price)} | {_fmt(s.stop_loss)} | {_fmt(s.price_target)} | {chance} | "
                f"{_fmt(now)} | {move} |"
            )
        add("")
        add("---")
        add("")
    if day.graded:
        add("## Analyses graded since your last review")
        add("")
        add("Each reached the end of its horizon, and the app scored the analyst's call against what the price did.")
        add("")
        for s in day.graded:
            add(
                f"- {s.ticker}, analysed {s.signal_date}, said {s.decision}: {s.outcome or '—'} "
                f"({_fmt(s.price_at_signal)} to {_fmt(s.price_at_evaluation)})."
            )
        add("")
        add("---")
        add("")
    add("## Your trades since your last review")
    add("")
    if day.trades:
        for t in day.trades:
            line = f"- {float(t.quantity):g} {t.ticker}: bought at {_money(t.entry)} on {_et(t.entry_at)}"
            if t.exit_at is not None and _utc(t.exit_at) > day.since:
                line += (
                    f"; sold at {_money(t.exit)} on {_et(t.exit_at)}, {_money(t.pnl or 0)} "
                    f"({t.return_pct:+.1f}%)"
                )
            elif t.is_open:
                line += "; still held"
            add(line + ".")
    else:
        add("None.")
    add("")
    add("---")
    add("")
    add("## Your passes since your last review, oldest first")
    add("")
    add("For each: why it started, then what you reasoned and asked for on every turn, then what happened. Pass numbers are the ones to name in a note.")
    add("")
    spent_before = None
    for run in passes:
        if spent_before is None:
            spent_before = float(run.research_spent or 0)
        lines += _describe_pass(run, spent_before)
        spent_before = float(run.research_spent or spent_before)
    add("---")
    add("")
    add("Speak to the maintainers first. Call report, with an empty list if nothing stood in your way.")
    return {"system": SYSTEM_PROMPT, "turn1": "\n".join(lines), "turn2": TURN2_PROMPT}


def _written(entry: dict) -> str:
    source = {"pass": "in a pass", "reflection": "in an evening review"}.get(entry.get("source") or "")
    when = entry.get("written")
    if when and source:
        return f"written {when} {source}"
    if when:
        return f"written {when}"
    return "date unknown"


def _fmt(value) -> str:
    return _money(value) if value is not None else "—"


# --- The conversation --------------------------------------------------------


def converse(prompts: dict, model: str, generate=None) -> Outcome:
    """Both turns, in one conversation. Applies nothing and records nothing,
    so the probe can call it too."""
    started = time.monotonic()
    out = Outcome()
    declared = (REPORT, REVISE)
    contents: list = [types.Content(role="user", parts=[types.Part(text=prompts["turn1"])])]

    first = llm_gemini.one_round(prompts["system"], contents, declared, ["report"], model, generate)
    if first.content is not None:
        contents.append(first.content)
    out.report = first.args if first.function == "report" else None
    out.report_text = first.text
    notes = (out.report or {}).get("notes") or []
    receipt = (
        f"Recorded. {len(notes)} note(s) reached the maintainers."
        if notes else "Recorded. You reported nothing, and that is fine."
    )
    parts = []
    if out.report is not None:
        # The response goes back under ``user`` with the call's id, the shape
        # ``decide`` sends (see llm_gemini for why not ``tool``).
        parts.append(types.Part(function_response=types.FunctionResponse(
            id=first.call_id, name="report", response={"result": receipt},
        )))
    parts.append(types.Part(text=prompts["turn2"]))
    contents.append(types.Content(role="user", parts=parts))

    second = llm_gemini.one_round(prompts["system"], contents, declared, ["revise"], model, generate)
    out.revise = second.args if second.function == "revise" else None
    out.revise_text = second.text
    out.thinking = "\n\n".join(t for t in (first.thinking, second.thinking) if t) or None
    out.prompt_tokens = first.prompt_tokens + second.prompt_tokens
    out.completion_tokens = first.completion_tokens + second.completion_tokens
    out.cached_tokens = first.cached_tokens + second.cached_tokens
    out.seconds = time.monotonic() - started
    out.channel = "tool" if (out.report is not None or out.revise is not None) else "text"
    return out


# --- Applying the second turn ------------------------------------------------


def apply(revise: dict | None) -> tuple[str | None, list[str]]:
    """Carry out the second turn: the new note for the next pass, and the
    memory changes, in order. Returns the note and one line per change."""
    revise = revise or {}
    note = str(revise.get("wakeup_note") or "").strip()[: agent._WAKEUP_NOTE_MAX_CHARS] or None
    applied: list[str] = []
    for change in revise.get("memory") or []:
        if not isinstance(change, dict):
            continue
        action = str(change.get("action") or "").lower().strip()
        text = str(change.get("text") or "").strip()
        index = change.get("index")
        position = int(index) - 1 if isinstance(index, (int, float)) and int(index) >= 1 else None
        if action == "add":
            if not text:
                applied.append("Memory: could not add a note with no text.")
            elif text in agent.get_memory_notes():
                applied.append(f'Memory: not added, already there: "{_cap(text, 120)}".')
            else:
                agent.add_memory_note(text, source="reflection")
                applied.append(f'Memory: added "{_cap(text, 200)}".')
        elif action == "rewrite":
            if position is None or not text:
                applied.append(f"Memory: could not rewrite note {index}: it needs a number and a text.")
            elif agent.rewrite_memory_note(position, text, source="reflection"):
                applied.append(f'Memory: rewrote note {index} to "{_cap(text, 200)}".')
            else:
                applied.append(f"Memory: could not rewrite note {index}: no such note.")
        elif action == "remove":
            if position is not None and agent.remove_memory_note(position):
                applied.append(f"Memory: removed note {index}.")
            else:
                applied.append(f"Memory: could not remove note {index}: no such note.")
        else:
            applied.append(f"Memory: unknown action {action!r}, nothing done.")
    return note, applied


# --- Running it --------------------------------------------------------------


def run_once(now: datetime.datetime | None = None) -> Review | None:
    """One review, end to end. None when there was nothing to review, or
    when this deployment does not answer by function call, which is the only
    channel the review runs on."""
    if not agent.answers_by_tool():
        log.info("Evening review skipped — it runs on the tool channel only")
        return None
    day = gather(now)
    if not day.passes:
        log.info("Evening review skipped — no pass since %s", day.since.isoformat())
        return None
    prompts = build_prompt(day)
    model = analysis.decision_model()
    ran_at = day.now.astimezone(datetime.timezone.utc)
    review = Review(ran_at=ran_at, since=day.since, passes=len(day.passes), model=model)
    try:
        outcome = converse(prompts, model)
    except Exception as exc:
        log.exception("The evening review failed")
        review.skipped = f"The review call failed: {exc}"[:500]
        review.id = _record(review, prompts, None)
        return review
    review.notes = [n for n in (outcome.report or {}).get("notes") or [] if isinstance(n, dict)]
    review.memory_changes = [c for c in (outcome.revise or {}).get("memory") or [] if isinstance(c, dict)]
    review.wakeup_note, review.applied = apply(outcome.revise)
    review.thinking = outcome.thinking
    review.channel = outcome.channel
    review.prompt_tokens = outcome.prompt_tokens
    review.completion_tokens = outcome.completion_tokens
    review.seconds = outcome.seconds
    review.id = _record(review, prompts, outcome)
    log.info(
        "Evening review: %d pass(es), %d note(s) to the maintainer, %d memory change(s), note %s",
        review.passes, len(review.notes), len(review.applied), "rewritten" if review.wakeup_note else "kept",
    )
    return review


def _record(review: Review, prompts: dict, outcome: Outcome | None) -> int | None:
    """Never raises: a review that changed memory must not be reported as a
    failure because the note about it could not be filed."""
    try:
        return db.record_reflection(
            ran_at=review.ran_at,
            since=review.since,
            passes=review.passes,
            prompt=prompts["turn1"],
            turn2_prompt=prompts["turn2"],
            response=(
                json.dumps(outcome.report, ensure_ascii=False) if outcome and outcome.report is not None
                else (outcome.report_text or None) if outcome else None
            ),
            revision=(
                json.dumps(outcome.revise, ensure_ascii=False) if outcome and outcome.revise is not None
                else (outcome.revise_text or None) if outcome else None
            ),
            thinking=review.thinking,
            notes=json.dumps(review.notes, ensure_ascii=False) if outcome else None,
            wakeup_note=review.wakeup_note,
            memory_changes=json.dumps(review.memory_changes, ensure_ascii=False) if outcome else None,
            applied=json.dumps(review.applied, ensure_ascii=False) if outcome else None,
            model=review.model,
            channel=review.channel,
            prompt_tokens=review.prompt_tokens or None,
            completion_tokens=review.completion_tokens or None,
            seconds=review.seconds or None,
            skipped=review.skipped,
        )
    except Exception:
        log.exception("Could not record the evening review")
        return None


# --- Discord -----------------------------------------------------------------


def format_embed(review: Review) -> Embed:
    """What the review said, for a person. Posted only when there is
    something in it: a note to the maintainer, or a change to memory."""
    embed = Embed(
        title="The agent — evening review",
        description=(
            f"{review.passes} pass(es) reviewed since {_et(review.since)}."
            + (" The note for the next pass was rewritten." if review.wakeup_note else "")
        ),
        color=Color.blue(),
        timestamp=datetime.datetime.now(datetime.timezone.utc),
    )
    if review.notes:
        embed.add_field(
            name="📝 The agent asked for something",
            value="\n".join(
                (f"Pass {n['pass_id']}" if n.get("pass_id") is not None else "No pass named")
                + f", {str(n.get('kind') or 'other').replace('_', ' ')}: "
                f"{n.get('what_was_missing')} — it would have {n.get('what_you_would_have_done')}"
                for n in review.notes
            )[:1024],
            inline=False,
        )
    if review.applied:
        embed.add_field(name="Memory", value="\n".join(review.applied)[:1024], inline=False)
    if review.wakeup_note:
        embed.add_field(name="Note for the next pass", value=review.wakeup_note[:1024], inline=False)
    embed.set_footer(text="Simulated account — no real money")
    return embed
