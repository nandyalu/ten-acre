"""The autonomous agent that trades the simulated book.

On a schedule it sets for itself, this hands the model its own book — budget,
cash, holdings, unrealized P/L — plus the signals it commissioned and current
prices, and asks it what to do. Nothing is analysed on a schedule: the agent
orders every analysis itself and pays for it (see docs/changelog.md and
JOURNEY.md, both 2026-09-08). What to buy and how much is the
model's decision. Python's job is narrower and non-negotiable: refuse orders
that cannot be executed as stated, and place the rest on the simulated account.

Two things here are easy to get wrong and both would cost real budget:

- **Orders are validated against a running book, not the starting one.** Three
  buys that are each affordable alone are not necessarily affordable together.
  Checking each against the opening cash balance would let all three through
  and overspend by design.
- **The model's arithmetic is never trusted.** It is told the prices and the
  cash; if it proposes a quantity whose cost exceeds what is left, the order is
  dropped, not resized. Resizing would silently turn its decision into a
  different one — see backend/services/analysis.py on the same model inventing
  price levels.

Everything here is blocking (LLM, broker, DB) — call via asyncio.to_thread.
"""
import datetime
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import NamedTuple
from pathlib import Path

from backend.database import db
from backend.services import (
    agent_book, analysis, analysis_reader, bars, candidates, experiment, llm_content,
    llm_throttle,
    llm_usage,
    market_clock,
    quotes, research, sandbox_broker, watchdog,
)
from backend.services.positions import get_current_price
from backend.services.sizing import get_atr, suggest_position
from backend.notifications.embed import Color, Embed

log = logging.getLogger("trading-experiment.agent")

_ENABLED_SETTING_KEY = "agent_enabled"

# The conviction floor: how good a signal has to look before the agent may
# open a position on it. Both default to 0, meaning off, and that default is
# the point rather than caution.
#
# The thresholds read win_probability and risk_reward. The first is the one
# number the model asserts rather than derives, and until the Scorecard's
# calibration says it is honest and that it sorts outcomes, a threshold on it
# is a threshold on a number that may not mean anything — filtering by it would
# feel like discipline while being arbitrary. Turn these on once calibration
# earns it.
_MIN_WIN_PROBABILITY_KEY = "agent_min_win_probability"
_MIN_RISK_REWARD_KEY = "agent_min_risk_reward"

# How far back to show signals. The trade horizon is 1-2 weeks, so a signal
# older than this has already had its chance and would just crowd the prompt.
_SIGNAL_LOOKBACK_DAYS = 3
_MAX_SIGNALS = 12

# Discord's embed limits, same as backend/services/analysis.py.
_DESCRIPTION_MAX = 4096
_FIELD_MAX = 1024


def is_enabled() -> bool:
    return db.get_setting(_ENABLED_SETTING_KEY) == "on"


def set_enabled(enabled: bool) -> None:
    db.set_setting(_ENABLED_SETTING_KEY, "on" if enabled else "off")
    # Switching the agent on for the first time is what starts the experiment
    # — the agent is off until a person turns it on, so this is a deliberate
    # act on a date they chose. Write-once, so pausing and resuming later does
    # not restart the clock. See backend/services/experiment.py.
    if enabled:
        experiment.record_start()


def _threshold(key: str) -> float:
    stored = db.get_setting(key)
    try:
        return max(0.0, float(stored)) if stored else 0.0
    except (TypeError, ValueError):
        log.warning("Ignoring unparseable %s %r", key, stored)
        return 0.0


def get_conviction() -> tuple[float, float]:
    """(minimum win probability, minimum risk/reward). Zero means no floor."""
    return _threshold(_MIN_WIN_PROBABILITY_KEY), _threshold(_MIN_RISK_REWARD_KEY)


def set_conviction(min_win_probability: float | None, min_risk_reward: float | None) -> None:
    if min_win_probability is not None:
        if not 0 <= min_win_probability <= 100:
            raise ValueError("Minimum win probability must be between 0 and 100.")
        db.set_setting(_MIN_WIN_PROBABILITY_KEY, str(min_win_probability))
    if min_risk_reward is not None:
        if min_risk_reward < 0:
            raise ValueError("Minimum risk/reward cannot be negative.")
        db.set_setting(_MIN_RISK_REWARD_KEY, str(min_risk_reward))


def fails_conviction(signal, min_probability: float, min_risk_reward: float) -> str | None:
    """Why this signal is not good enough to open a position on, or None.

    A signal that states no probability *fails* a probability floor rather than
    passing it. Asking for at least 60% confidence and accepting a signal that
    claims nothing would make the floor trivially avoidable — the model would
    only have to stop answering the question.

    None as the signal itself means the agent proposed a ticker with no recent
    analysis at all, which is the plainest case a conviction floor exists to
    stop.
    """
    if not min_probability and not min_risk_reward:
        return None
    if signal is None:
        return "no recent signal to justify it"
    if min_probability:
        stated = signal.win_probability
        if stated is None:
            return f"states no win probability (floor is {min_probability:.0f}%)"
        if stated < min_probability:
            return f"{stated:.0f}% win probability is below the {min_probability:.0f}% floor"
    if min_risk_reward:
        stated = signal.risk_reward
        if stated is None:
            return f"states no risk/reward (floor is {min_risk_reward:.2f})"
        if stated < min_risk_reward:
            return f"risk/reward {stated:.2f} is below the {min_risk_reward:.2f} floor"
    return None


def _newest_signal_per_ticker(signals) -> dict:
    """The most recent signal for each ticker.

    Sorts rather than trusting the caller's order. ``get_recent_signals``
    returns newest-first today, but a plain "keep the first one seen" would
    silently invert the moment that changed, and the failure it caused was
    invisible: a stale stop and target are still real levels from a real
    signal, so nothing looks wrong in the ledger or at the broker.

    ``signal_date`` is a date, so two analyses of one ticker on the same day
    tie. ``id`` breaks the tie, and a higher id is the later row.
    """
    newest: dict = {}
    for signal in sorted(signals, key=lambda s: (s.signal_date, s.id or 0)):
        newest[signal.ticker] = signal
    return newest


def _recent_signals() -> list:
    """The signals the agent decides on: recent, and from the model in use.

    The model filter matters as soon as a second one is being evaluated. Every
    signal records which model produced it, so running a comparison sweep puts
    two signals per ticker in the table — sometimes disagreeing — and without
    this the agent would trade on the mix, quietly folding an experiment into
    the live book. It should act on the model the app is configured to use, and
    nothing else.

    Signals from before the model column existed carry NULL and are kept: they
    are real track record, they just cannot be attributed.
    """
    cutoff = datetime.date.today() - datetime.timedelta(days=_SIGNAL_LOOKBACK_DAYS)
    configured = analysis.get_model()
    return [
        s
        for s in db.get_recent_signals(limit=_MAX_SIGNALS * 3)
        if s.signal_date >= cutoff and (s.model is None or s.model == configured)
    ][:_MAX_SIGNALS]


# How many past trades to show individually. Enough to see a pattern, few
# enough that the list does not crowd out today's actual decision.
_HISTORY_SHOWN = 6

# How many recent broker failures to show. Small on purpose: the point is
# "this did not work, do not repeat it", and a long list of old failures
# would push the signals that matter today further down the prompt.
_FAILURES_SHOWN = 5

# How far back to look for them. A failure from last month is history, not a
# warning — the unsettled cash that caused it has long since settled.
_FAILURE_LOOKBACK_RUNS = 3


# How many past wakeups to report. Enough to show a pattern, few enough that
# the agent does not spend its attention reading its own log.
_WAKEUPS_SHOWN = 6

# **A change note is shown for a number of passes, not a number of days
# (2026-09-12).** Days were wildly uneven, because the agent picks its own
# cadence: measured over a week it ran between 3 and 11 passes a day, so the
# same note was read about 25 times if it landed on a busy Tuesday and twice
# if it landed before a quiet weekend — or never, if the agent slept through
# the window. Counting passes is fair at both ends and caps the cost exactly.
#
# **This is not "until acknowledged", which was considered and rejected.**
# That needed a judgement about whether the agent had understood, which is one
# more thing to get wrong. A counter judges nothing.
_CHANGE_NOTES_PASSES = 3

# At most this many notes at once, newest first. A burst of edits — seven
# landed on 2026-09-10 — must not crowd out the pass's own decision.
_CHANGE_NOTES_SHOWN = 5

# **Per note, and it is a budget rather than a suggestion.** The notes drifted
# into commit messages: they averaged 530 characters by 2026-09-12 and the two
# longest were 982 and 971, which put roughly 1,600 tokens of changelog in
# front of the agent before it saw a single price. A note does not need to
# explain the new rule — the rules are in the same prompt and already current.
# It needs to say what is no longer true.
_CHANGE_NOTE_MAX_CHARS = 240

# Where the seen-counts live. A database row rather than the git-tracked file,
# because it is this deployment's state and not a fact about the app. Losing
# it to a reset re-shows a few notes, which is the harmless direction.
_CHANGES_SEEN_KEY = "change_notes_seen"

# Git-tracked rather than in the database — see describe_recent_changes for
# why. backend/services/agent.py -> backend/ -> agent_changes.json.
_CHANGES_FILE = Path(__file__).resolve().parent.parent / "agent_changes.json"

_MEMORY_NOTES_KEY = "agent_memory_notes"
_MEMORY_NOTE_MAX_CHARS = 500
_MAX_MEMORY_NOTES = 10


def get_memory_notes() -> list[str]:
    """Persistent memory notes stored across turns and passes by the agent."""
    stored = db.get_setting(_MEMORY_NOTES_KEY)
    if not stored:
        return []
    try:
        data = json.loads(stored)
        if isinstance(data, list):
            return [str(n).strip() for n in data if str(n).strip()][:_MAX_MEMORY_NOTES]
    except Exception:
        log.exception("Could not parse agent memory notes setting")
    return []


def set_memory_notes(notes: list[str]) -> None:
    """Overwrite the agent's persistent memory notes."""
    cleaned = [str(n).strip()[:_MEMORY_NOTE_MAX_CHARS] for n in notes if str(n).strip()][:_MAX_MEMORY_NOTES]
    db.set_setting(_MEMORY_NOTES_KEY, json.dumps(cleaned))


def add_memory_note(text: str) -> None:
    """Add a persistent memory note if not already present."""
    note = str(text or "").strip()[:_MEMORY_NOTE_MAX_CHARS]
    if not note:
        return
    notes = get_memory_notes()
    if note not in notes:
        if len(notes) >= _MAX_MEMORY_NOTES:
            notes.pop(0)  # FIFO cap eviction if max reached
        notes.append(note)
        set_memory_notes(notes)


def remove_memory_note(text_or_index: str | int) -> bool:
    """Remove a persistent memory note by text or 0-based index."""
    notes = get_memory_notes()
    if isinstance(text_or_index, int):
        if 0 <= text_or_index < len(notes):
            notes.pop(text_or_index)
            set_memory_notes(notes)
            return True
        return False
    target = str(text_or_index or "").strip()
    if target in notes:
        notes.remove(target)
        set_memory_notes(notes)
        return True
    return False


def describe_memory_notes() -> list[str]:
    """Format persistent memory notes for the agent prompt."""
    notes = get_memory_notes()
    if not notes:
        return []
    return [
        "## Your persistent memory notes across passes:",
        "These are long-term notes you recorded previously that persist until you clear them:",
        *(f"- {n}" for n in notes),
    ]


def load_change_notes() -> list[dict]:
    """Read backend/agent_changes.json, defensively.

    Read fresh rather than cached: the file is a few hundred bytes, read at
    most a few times an hour, and caching it would only add a staleness bug
    for no measurable benefit.

    Also called from the app's startup log (see backend/app.py's lifespan),
    so a typo in the file is visible in the logs the moment the container
    starts rather than discovered mid-decision weeks later. A malformed file
    must never break a decision pass, so every failure here is logged loudly
    and answered with an empty list rather than raised.
    """
    try:
        raw = _CHANGES_FILE.read_text()
    except FileNotFoundError:
        return []
    try:
        entries = json.loads(raw)
    except ValueError:
        log.exception("%s is not valid JSON — showing the agent nothing", _CHANGES_FILE)
        return []
    if not isinstance(entries, list):
        log.error("%s must be a JSON list — showing the agent nothing", _CHANGES_FILE)
        return []
    return [e for e in entries if isinstance(e, dict) and e.get("date") and e.get("message")]


def describe_analysis_timing(
    durations: list[float], running: dict, now: datetime.datetime | None = None
) -> list[str]:
    """How long an analysis takes, and what is running right now.

    **The agent picks its own next wakeup and cannot plan one around research
    it ordered without this.** An analysis takes about eighteen minutes, so
    asking to be woken in five after commissioning one spends a pass on an
    answer that is not there yet.

    The numbers come from its own recent runs, not from a constant. Analysis
    time moves with the model, the hardware and how many run at once, and a
    figure typed into the prompt would be wrong the first time any of those
    changed.
    """
    lines: list[str] = []
    if durations:
        ordered = sorted(durations)
        median = ordered[len(ordered) // 2]
        lines.append(
            f"An analysis takes about {median:.0f} minutes here — recently between "
            f"{ordered[0]:.0f} and {ordered[-1]:.0f}. A \"research\" order runs inside this "
            "pass: you wait that long and are then shown what it found, before you finish. "
            "So a wakeup you set is for something else."
        )
    if running:
        here = (now or datetime.datetime.now(datetime.timezone.utc))
        if here.tzinfo is None:
            here = here.replace(tzinfo=datetime.timezone.utc)
        parts = []
        for ticker, started in sorted(running.items()):
            if started.tzinfo is None:
                started = started.replace(tzinfo=datetime.timezone.utc)
            parts.append(f"{ticker} ({(here - started).total_seconds() / 60:.0f} min so far)")
        lines.append("Being analysed right now: " + ", ".join(parts) + ".")
    return lines


def describe_recent_changes(changes: list[dict]) -> list[str]:
    """Tell the agent what is no longer true, and why that matters to it.

    **A note reaches the people who maintain this app, and "nothing acts on
    it automatically."** That was only ever true in one direction. If a
    maintainer actually built what a note asked for, the agent had no way to
    learn its note had been read — it would keep asking, or keep working
    around a restriction that no longer existed.

    **It is a correction, not an explanation, and that is the whole reason it
    can be short.** The agent has no memory between passes: it reads the
    current rules fresh every time, so a note that describes how something
    works now is repeating the rules it sits beside. What the rules cannot do
    is explain the agent's *own history* — the past decisions, wakeups and
    track record further down were produced under the older rules, and without
    a note the agent can read a pattern out of behaviour that is no longer
    possible.

    These are written by hand in ``backend/agent_changes.json``, in the same
    commit as the change itself and often alongside the JOURNEY.md entry it
    also needs. **JOURNEY.md is where the long version goes.** A git-tracked
    file rather than a database row on purpose: this project has reset its own
    database more than once, and an entry here should survive that the way
    JOURNEY.md already does.

    Same-day notes are collapsed under one date. Seven landed on 2026-09-10
    and read as seven separate upheavals rather than one day's work.
    """
    if not changes:
        return []
    lines = [
        "**What is no longer true.** The rules below are already current, so "
        "nothing here repeats them. They are here because your own past "
        "decisions and wakeups further down were made under the older rules:"
    ]
    by_date: dict[str, list[str]] = {}
    for c in changes:
        text = str(c.get("message", "") or "").strip()
        if len(text) > _CHANGE_NOTE_MAX_CHARS:
            # A backstop, not the mechanism — a test keeps the file itself
            # inside the budget so this never fires in practice.
            text = text[:_CHANGE_NOTE_MAX_CHARS].rsplit(" ", 1)[0] + "…"
        by_date.setdefault(str(c.get("date", "")), []).append(text)
    for date, texts in by_date.items():
        lines.append(f"- {date}: " + " ".join(texts))
    return lines


def describe_recent_wakeups(wakeups: list[dict]) -> list[str]:
    """What the agent's own chosen cadence has produced.

    **A wakeup costs it nothing, so the obvious failure is asking for the
    minimum every time** and spending the session on passes that do nothing.
    Pricing a wakeup was rejected: the work is trivial and a charge would be an
    invented cost dressed up as a rule, which is the mistake the research
    timing deliberately avoids.

    This is feedback instead of a limit. The agent is shown how its last
    several wakeups turned out and left to draw the conclusion. Whether it
    learns to space them is a result worth having, and a cap would have
    answered that question before it was asked.
    """
    if not wakeups:
        return []
    recent = wakeups[-_WAKEUPS_SHOWN:]
    idle = sum(1 for w in recent if not w.get("acted"))
    lines = ["Your recent wakeups, and whether each one led to an action:"]
    for w in recent:
        at = w.get("at", "")
        lines.append(f"- {at}: {'acted' if w.get('acted') else 'did nothing'}")
    if idle == len(recent) and len(recent) >= 3:
        lines.append(
            f"All {idle} did nothing. Waking more often does not make the market move — "
            "if there is nothing to react to, ask for a later time and spend the "
            "attention when something has actually changed."
        )
    elif idle:
        lines.append(
            f"{idle} of the last {len(recent)} did nothing. Pick the next time for when "
            "you expect something to have changed, not out of habit."
        )
    return lines


def describe_recent_failures(failures: list[dict]) -> list[str]:
    """Orders the broker refused on earlier passes, for this pass's prompt.

    **Different from the refusals fed back mid-pass.** Those are Python
    declining an order before it is sent, and they say the agent's arithmetic
    was wrong. These are orders that were correctly formed and that the world
    would not take — unsettled cash, a closed session, a symbol the broker will
    not trade.

    The agent could not see these at all until 2026-09-02. It would form the
    same order the next morning, be refused again, and nothing in the record
    explained the repetition.

    Deliberately a prompt section rather than a mid-pass retry: it costs no
    extra call, cannot loop on an error that is not going away, and leaves a
    decision pass as one comparable unit.

    **The closing line used to say a repeat "will usually fail the same
    way."** That is true of a structural refusal — unsettled cash, a closed
    session — but false of one caused by timing: the market has since opened,
    or a slow cancel has since cleared. On 2026-09-08 the agent sold AVGO to
    take profit and cut concentration risk, watched the sell fail on a timing
    bug, and then held for two more passes citing reasons that never
    mentioned the concentration risk it had just named — reasoning that reads
    like the old line's own conclusion rather than a reconsidered one. The
    line cannot tell a bug from a standing restriction, so it no longer
    guesses which this is; it points at the reason instead and leaves the
    judgment to the agent.
    """
    if not failures:
        return []
    lines = [
        "Orders of yours the broker would not take recently. These were not "
        "refused for arithmetic — they were formed correctly and rejected:"
    ]
    for f in failures[-_FAILURES_SHOWN:]:
        side = str(f.get("side", "")).upper()
        qty = f.get("quantity") or 0
        lines.append(f"- {side} {qty:g} {f.get('ticker', '')}: {f.get('why', '')}")
    lines.append(
        "Look at why each one failed before deciding what to do about it. Some "
        "of these are standing restrictions that will refuse the same order "
        "again unchanged — unsettled cash, a market that is still closed. "
        "Others were about timing or a broker hiccup, not about whether the "
        "underlying decision was right, and trying again can succeed once the "
        "reason no longer applies. If the decision behind a failed order still "
        "holds, do not let the failure alone talk you out of it."
    )
    return lines


def describe_history(closed: list) -> list[str]:
    """The agent's own track record, in its own prompt.

    Without this it wakes every morning with a book and no idea that the last
    four things it bought on a Hold signal all lost money. A model that cannot
    see its outcomes cannot avoid repeating them, and neither can you tell
    whether it is learning.

    Returns [] on an empty record rather than a line saying so — "you have made
    no trades" is noise on day one, and the holdings section already says the
    book is empty.
    """
    if not closed:
        return []
    wins = sum(1 for t in closed if t.won)
    net = sum(t.pnl for t in closed)
    held = sum(t.held_days for t in closed) / len(closed)
    lines = [
        f"How your own past trades turned out — {len(closed)} closed, {wins} profitable, "
        f"{net:+,.2f} net, held {held:.0f} days on average:",
    ]
    for trade in closed[-_HISTORY_SHOWN:]:
        origin = f" (analyst said {trade.signal_decision})" if trade.signal_decision else ""
        lines.append(
            f"- {trade.ticker}: bought ${trade.entry:,.2f}, sold ${trade.exit:,.2f}, "
            f"{trade.return_pct:+.1f}% over {trade.held_days} day(s){origin}"
        )

    # The pattern most worth naming: this model bought a stock whose only signal
    # was Hold, and put 98% of the budget into it.
    on_hold = [t for t in closed if (t.signal_decision or "").lower() == "hold"]
    if len(on_hold) >= 2:
        hold_wins = sum(1 for t in on_hold if t.won)
        lines.append(
            f"Of the {len(on_hold)} you bought on a Hold signal, {hold_wins} made money."
        )
    return lines


# What each trigger means, in the agent's own reading. Plain words rather than
# the stored key: "move" tells it nothing, "run because the stock moved
# unusually" tells it the analyst was reacting to something already priced in.


_TRIGGER_PHRASE = {
    "sweep": " Run on the normal morning schedule.",
    "commissioned": " Run today because you asked to see it today.",
    "move": " Run because the stock moved unusually, so this analyst was reacting to a move the price already holds.",
    "earnings": " Run because the company reports earnings soon.",
    "manual": " Run by hand, outside the schedule.",
}


def day_range_today(
    ticker: str, current_price: float | None = None, today: datetime.date | None = None
) -> tuple[float, float] | None:
    """The low and high of today's price session so far for a ticker.

    None when the session has no intraday or daily bar yet.
    """
    today = today or datetime.date.today()
    try:
        bar = bars._todays_bar(ticker, today)
        if bar is not None:
            low, high = bar.low, bar.high
            if current_price is not None:
                low, high = min(low, current_price), max(high, current_price)
            return low, high
    except Exception:
        log.exception("Could not read today's price range for %s", ticker)
    return None


def price_range_since_purchase(
    ticker: str, opened: datetime.date | None, current_price: float | None,
    today: datetime.date | None = None,
) -> tuple[float, float] | None:
    """The low and high of a holding's price since it was bought.

    Entry price, research price and current price are three points; a stock
    that fell 20% and recovered looks identical to one that only ever climbed
    when those are all the agent is shown. Completed sessions come from the
    bar cache (never a direct vendor call, per market-data.md); the still-open
    day is folded in from ``current_price``, already read once by the caller,
    rather than a second live request per holding.

    A caller, not build_prompt itself: build_prompt is a pure formatter over
    data the caller already fetched (see ``prices`` below), and this one call
    per holding touches the bar cache and, on a cold ticker, a vendor.

    None when the purchase date is unknown or history has not settled yet —
    a holding bought earlier today, for instance, has no completed session.
    """
    if opened is None:
        return None
    try:
        history = bars.get_bars(ticker, opened, today=today or datetime.date.today())
    except Exception:
        log.exception("Could not read price history for %s", ticker)
        return None
    if not history:
        return None
    low = min(bar.low for bar in history)
    high = max(bar.high for bar in history)
    if current_price is not None:
        low, high = min(low, current_price), max(high, current_price)
    return low, high


def build_prompt(
    book: agent_book.Book,
    signals: list,
    prices: dict[str, float | None],
    rejected: list[agent_book.Rejection] | None = None,
    closed: list[agent_book.ClosedTrade] | None = None,
    regime_line: str | None = None,
    horizon_days: int | None = None,
    menu: list | None = None,
    price: float = 0.0,
    watchlist: list[str] | None = None,
    max_watchlist: int = 0,
    failures: list[dict] | None = None,
    unsettled_cash: float = 0.0,
    wakeups: list[dict] | None = None,
    analysis_minutes: list[float] | None = None,
    running_analyses: dict | None = None,
    changes: list[dict] | None = None,
    readings: list[str] | None = None,
    dropped_with_read: list[dict] | None = None,
    outcomes: list[str] | None = None,
    researched_now: set | None = None,
    woke_because: str | None = None,
    wakeup_note: str | None = None,
    pass_notes: list[str] | None = None,
    alerts: list[dict] | None = None,
    earnings: list | None = None,
    planned_wakeup: "datetime.datetime | None" = None,
    price_ranges: dict[str, tuple[float, float]] | None = None,
    day_ranges: dict[str, tuple[float, float]] | None = None,
) -> str:
    """Everything the model gets. Written as plain figures rather than a table
    of jargon, because the numbers are the whole input and a misread one is a
    wrong trade.

    Three things here exist because the model got them wrong on a live run. It
    proposed $1,944 of buys against $1,000 of cash, so the affordable share
    count is now computed in Python and stated per ticker rather than left as
    arithmetic. It treated Hold signals on stocks it did not own as buy
    candidates, so what each decision means is spelled out. And it did not
    realize it could sell to fund a buy, so the ordering rule is stated
    explicitly.

    ``rejected`` carries the reasons a previous attempt's orders were refused,
    turning a hard failure into a correction the model can act on.
    """
    lines = [
        # "Decide what to trade today" asked for a trade while a rule far below
        # says doing nothing is often right, and an opening line beats a late
        # rule. "today" was stale too: the agent has set its own cadence since
        # 2026-09-05 and wakes several times a day, so most passes are about
        # positions already open rather than about a new trade.
        #
        # "of real money" is a deliberate lie, and the only one here. See
        # CLAUDE.md's "Three guards keep this a simulation": the prompt may lie
        # to the model, the code must never lie to itself. Every order still
        # passes _assert_sandbox(). Do not relax a guard on the grounds that
        # the agent believes this is real — that belief is manufactured here.
        "You manage a small account of real money. Decide what to do with it now, if anything.",
        "",
        # First, because everything below is read against it and because the
        # agent chooses its own next wakeup — a question about the time it
        # could not answer while nothing in the prompt said what time it was.
        market_clock.describe(),
        # **The fallback, as a real instant.** The rules say "the following
        # open" and the agent had to work out which day that was — at 3:59 PM
        # on a Thursday it reasoned through the weekend to get there. Stating
        # it costs one line and removes the arithmetic.
        f"If you name no next_wakeup, you will next be asked at "
        f"{market_clock.next_open().astimezone(market_clock.US_MARKET_TZ).strftime('%Y-%m-%dT%H:%M')} "
        "Eastern, the following open. Name a time if you want a different one.",
    ]
    # **Directly under the clock, because it changes how the rest is read.**
    # Its own chosen time and a move it slept through call for different
    # answers, and until 2026-09-12 the agent was told neither.
    # **A later turn of the same pass says why it is asked again (2026-09-13).**
    # The second turn repeated "A change to this app woke you" as though the
    # agent had just been woken. Each reason names a section, so each one is
    # added only here, where the section is known to exist.
    asked_again = []
    if outcomes:
        asked_again.append(
            'the orders in your last answer have been carried out (see "What you '
            'just did, a moment ago, in this pass" below)'
        )
    if readings:
        asked_again.append('you asked to read an analysis (see "What you asked to read" below)')
    if dropped_with_read:
        asked_again.append(
            'part of your last answer went with a read, so it was not carried out '
            '(see "What was not carried out" below)'
        )
    if rejected:
        asked_again.append(
            'part of your last answer was refused (see "Your previous answer was refused" below)'
        )
    lines += describe_wakeup(
        woke_because, wakeup_note, has_news=bool(alerts), planned=planned_wakeup,
        asked_again=asked_again, pass_notes=pass_notes,
    )
    lines += ["", "---", ""]
    if regime_line:
        lines += [regime_line, ""]
    recent_changes = describe_recent_changes(changes or [])
    if recent_changes:
        lines += [*recent_changes, ""]
    memory_notes = describe_memory_notes()
    if memory_notes:
        lines += [*memory_notes, ""]
    # Every "price now" in this prompt was read at the same moment, and the
    # agent asked which moment that was. Named once, used by both tables.
    as_of = market_clock.now_et().strftime("%Y-%m-%d %-I:%M %p ET")
    lines += [
        f"Your account is ${book.budget:,.2f} in total. That is all you will ever have — "
        "there is no more money coming.",
        f"Of it, ${book.cash:,.2f} is uninvested and available to spend right now.",
        f"Total equity: ${book.equity:,.2f} ({book.return_pct:+.1f}% against the account)",
        f"Realized profit so far: ${book.realized_pnl:,.2f}",
    ]
    # What a cash account actually restricts. The broker refuses a bracket
    # against unsettled funds and the app quietly falls back to a plain order
    # plus separately-armed exits — a rule the agent has been running into
    # and was never told about. Silent at zero, which is the ordinary case.
    if unsettled_cash:
        lines += [
            f"Of that cash, ${unsettled_cash:,.2f} came from sales that have not settled "
            "yet. You can spend it, but a buy made with unsettled money cannot carry its stop "
            "and take-profit in the same order — they get placed separately, and that second "
            "step can fail and leave the position unprotected. Settled money is the safer "
            "purchase.",
        ]
    lines += ["", "---", ""]

    if book.holdings:
        price_ranges = price_ranges or {}
        exits_by_ticker = {
            h.ticker: {
                t.exit_kind: t.limit_price
                for t in db.get_resting_exits(h.ticker)
                if t.exit_kind and t.limit_price
            }
            for h in book.holdings
        }
        # **A table, not a sentence (2026-09-15).** The prose line ran to nine
        # clauses and buried "has ranged $X to $Y since you bought it" as the
        # second-to-last — three probe runs, laser-focused on one holding with
        # nothing else in the prompt to read, quoted every earlier clause on
        # the line and never that one. Named columns read the same way the
        # signals table already does, and **Stop**/**Target** now say `UNSET`
        # outright rather than folding a missing exit into a sentence that
        # reads the same whether one side is resting or neither is.
        lines += [
            "You currently hold. **Value** is quantity times price now, and is "
            "also about what selling the whole position would raise, before "
            "slippage. **Stop** and **Target** are what is actually resting at "
            "the broker, not a level you asked for earlier — `UNSET` means "
            "nothing is resting on that side, and a move against you on it "
            "would not be caught.",
            "",
            "| Ticker | Shares | Avg cost | Price now | Range since purchase | Value"
            " | Unrealized | % of account | Held | Stop | Target |",
            "|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for h in book.holdings:
            value = f"${h.market_value:,.2f}" if h.market_value is not None else "unpriced"
            pnl = f"{h.unrealized_pnl:+,.2f}" if h.unrealized_pnl is not None else "unknown"
            # Named apart from the `price` parameter deliberately. Reusing it
            # here rebound the research price to a string, and the menu block
            # below then formatted that string as a float. The crash needed a
            # holding and a menu together, so it was invisible until the agent
            # first bought something.
            price_each = f"${h.price:,.2f}" if h.price is not None else "unavailable"
            weight = book.weight_pct(h)
            weight_text = f"{weight:.0f}%" if weight is not None else "—"
            held_days = h.held_days()
            held_text = f"{held_days}d" if held_days is not None else "—"
            # The caller computes this (see price_range_since_purchase) — the
            # entry, research and current price are three points, and a name
            # that dipped 20% and recovered looks identical to one that only
            # ever climbed without it. Placed beside Price now, not at the end
            # of the row — see the 2026-09-15 JOURNEY.md entry: end-of-row was
            # measured and never once read.
            price_range = price_ranges.get(h.ticker)
            range_text = (
                f"${price_range[0]:,.2f}–${price_range[1]:,.2f}" if price_range else "—"
            )
            # What is actually resting at the broker on this position. Without
            # it the model cannot tell an exit it should move from one that is
            # already where it wants it — or notice there is none at all.
            resting = exits_by_ticker.get(h.ticker, {})
            stop_text = f"${resting['stop']:,.2f}" if resting.get("stop") else "UNSET"
            target_text = f"${resting['target']:,.2f}" if resting.get("target") else "UNSET"
            lines.append(
                f"| {h.ticker} | {h.quantity:g} | ${h.avg_cost:,.2f} | {price_each}"
                f" | {range_text} | {value} | {pnl} | {weight_text} | {held_text}"
                f" | {stop_text} | {target_text} |"
            )
    else:
        lines.append("You hold nothing. The whole account is in cash.")
    lines += ["", "---", ""]

    # **A limit order that has not filled yet is otherwise invisible here.**
    # Holdings above are filled positions; a GTC limit buy can sit unfilled
    # for days without ever becoming one, and nothing else in this prompt
    # says it exists — the outcome line that announced it belonged to the
    # pass that placed it and is gone by the next one. Without this a later
    # pass could forget an order it is still waiting on, or place a second
    # one on the same ticker having lost track of the first (screening still
    # protects the cash either way — see agent_book.build_book — but a
    # forgotten order is still a confused decision).
    pending_entries = [
        t for t in db.get_pending_agent_trades() if not t.is_stop and t.limit_price
    ]
    if pending_entries:
        lines += [
            "**Orders you placed that have not filled yet.** Use side \"cancel\" "
            "with the ticker to withdraw one you no longer want.",
            "",
            "| Ticker | Side | Shares | Limit price | Placed |",
            "|---|---|---|---|---|",
        ]
        for t in pending_entries:
            lines.append(
                f"| {t.ticker} | {t.side} | {t.quantity:g} | ${t.limit_price:,.2f} "
                f"| {t.placed_at.strftime('%Y-%m-%d %-I:%M %p')} |"
            )
        lines += ["", "---", ""]

    # **Both of these sit above the signal table, and that placement was
    # measured (2026-09-12).** They were between the two tables, at 42% and 47%
    # of the prompt, and four probe runs against the live book referenced
    # neither — the model read the clock, the account and the tables, and
    # skimmed the prose between them. They also both change how the tables
    # below should be read, which is an argument for being above them anyway.
    if outcomes:
        lines += [
            "",
            "## What you just did, a moment ago, in this pass",
            "",
            "**You ordered these yourself, earlier in this same pass, and they have "
            "already happened.** They are not suggestions and not history from an "
            "older pass — they are the result of your own last answer, and anything "
            "you paid for is already paid for. Do not order them again. Read them "
            "before the tables below, because they may change what those mean. An "
            "analysis you paid for here also has its own row in the signals table, "
            "marked as yours; this is the reasoning behind that row.",
            "",
            *(f"- {line}" for line in outcomes),
        ]

    # **What the rules noticed, as facts.** News about the world rather than an
    # answer to something the agent said, which is why it sits with the account
    # and the holdings rather than with the refusals and readings.
    watchdog_lines = describe_watchdog(alerts or [], earnings or [])
    if watchdog_lines:
        lines += watchdog_lines

    if signals:
        # **A table, not a sentence.** The prose version ran "now $107.10,
        # suggested entry $106.24" together, and the agent's own reasoning
        # showed it working out which price was which. Named columns say it
        # once, and the header says outright what "now" and "at analysis"
        # mean, because those two are the pair that was being confused.
        lines += [
            f"Recent analyst signals. **Price now** is the price as of {as_of}; "
            "**Day High** and **Day Low** are today's session range so far; "
            "**At analysis** is what it cost when the analyst looked. **Entry/Stop/Target** "
            "are the analyst's proposed levels, not orders that exist. Rows are newest "
            "first, and **Analysed** carries the time because a ticker can be analysed "
            "more than once in a day.",
            "",
            "| Ticker | Analysed (ET) | Decision | Price now | Day High | Day Low | At analysis | Entry | Stop | Target |"
            " Chance | R:R | You could buy | Why it ran |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        # Newest first, and sorted here rather than relied upon: the query
        # orders by `signal_date`, a calendar date, so two analyses of one
        # ticker on one day arrive in row order. The header says the rows are
        # newest first, and a header that lies is worse than no header.
        for s in sorted(
            signals,
            key=lambda x: (str(x.signal_date)[:10],
                           str(getattr(x, "created_at", "") or ""),
                           getattr(x, "id", 0) or 0),
            reverse=True,
        ):
            # Also kept off `price`, for the same reason as the holdings loop.
            live = prices.get(s.ticker)
            money = lambda v: f"${v:,.2f}" if v else "—"
            price_text = f"${live:,.2f}" if live is not None else "unavailable"
            # How good the analyst thought the bet was, not merely which way it
            # pointed. Without these every Buy reads as equally good and the
            # choice between them comes down to what happens to be affordable.
            chance = f"{s.win_probability:.0f}%" if s.win_probability is not None else "—"
            rr = f"{s.risk_reward:.1f}:1" if s.risk_reward is not None else "—"
            # Computed here, not left to the model: the affordable count is the
            # arithmetic it actually got wrong.
            if live:
                # Floor division on a negative balance returns -1, not 0, and
                # -1 is truthy — so a book at minus $8.00 was told "you can
                # afford -1 share(s)" on every signal line. Clamped, and the
                # branch now tests for a positive count rather than a non-zero
                # one, because those differ only when the answer is nonsense.
                affordable = max(0, int(book.cash // (live * agent_book.buying_power_margin())))
                afford_text = f"{affordable} share(s)" if affordable > 0 else "none, too dear"
            else:
                afford_text = "no price"
            # Why this analysis exists. A signal produced because the stock
            # just moved sharply is the analyst reacting to a move already in
            # the price; a scheduled one is not reacting to anything. Those
            # deserve different weight and the agent could not tell them apart.
            # Silent when NULL — rows written before this was recorded have no
            # honest value, and inventing one would be a guess in the record.
            # getattr, because several tests pass signal-shaped stand-ins
            # rather than the model, the same way the Decision unpacking does.
            # **Provenance belongs in the table, not in a prose block above
            # it.** A research result was appearing twice — once as this row
            # and once in "What you just did" — and the model cited the row and
            # called it "the analyst", never registering that it had paid for
            # it moments earlier. It was not ignoring the prose; it was
            # reconciling two copies of one fact and keeping the canonical
            # one. So the canonical one now carries the provenance.
            if researched_now and s.ticker in researched_now:
                because = "**YOU paid for this one, in this pass, minutes ago.** It is here because you ordered it."
            else:
                because = _TRIGGER_PHRASE.get(getattr(s, "trigger", None) or "", "").strip() or "—"
            day_range = (day_ranges or {}).get(s.ticker)
            day_high = f"${day_range[1]:,.2f}" if day_range else "—"
            day_low = f"${day_range[0]:,.2f}" if day_range else "—"
            lines.append(
                f"| {s.ticker} | {_analysed_at(s)} | {s.decision} | {price_text} | "
                f"{day_high} | {day_low} | {money(getattr(s, 'price_at_signal', None))} | "
                f"{money(s.entry_price)} | {money(s.stop_loss)} | {money(s.price_target)} | "
                f"{chance} | {rr} | {afford_text} | {because} |"
            )
        # Expected value is the analyst's own derivation from the levels above,
        # so it sits under the table rather than adding a column that is empty
        # for most rows.
        evs = [f"{s.ticker} {s.expected_value_r:+.2f}R" for s in signals
               if getattr(s, "expected_value_r", None) is not None]
        if evs:
            lines.append("")
            lines.append("Expected value, where the analyst gave one: " + ", ".join(evs) + ".")
    else:
        lines.append("No new signals today.")

    history = describe_history(closed or [])
    recent_failures = describe_recent_failures(failures or [])
    timing = describe_analysis_timing(analysis_minutes or [], running_analyses or {})
    recent_wakeups = describe_recent_wakeups(wakeups or [])
    # One divider for the whole cluster, and only when it has something in
    # it — an unconditional one here would sit right beside the next
    # section's own divider (reading, or Rules) on a pass with no history,
    # no failures, no timing and no wakeups, printing two rules in a row.
    if history or recent_failures or timing or recent_wakeups:
        lines += ["", "---", ""]
    if history:
        lines += ["", *history]
    if recent_failures:
        lines += ["", *recent_failures]
    if timing:
        lines += ["", *timing]
    if recent_wakeups:
        lines += ["", *recent_wakeups]

    # What it asked to read on the previous turn. Its own section, with its
    # own break, since 2026-09-16 — it used to run on straight from "recent
    # wakeups" with no divider, and read as that section's last line rather
    # than a reply to something the agent said moments ago.
    reading_lines = analysis_reader.describe(readings or [])
    if reading_lines:
        lines += ["", "---", "", *reading_lines]

    if dropped_with_read or rejected:
        lines += ["", "---"]

    if dropped_with_read:
        lines += [
            "",
            "What was not carried out: your last answer also asked to read, and only "
            "the read runs from an answer that asks for one. None of this happened:",
            *(f"- {_describe_order(o)}" for o in dropped_with_read),
            "Resend anything above that you still want, now that you have read it.",
        ]

    if rejected:
        lines += [
            "",
            "Your previous answer was refused. Fix it:",
            *(f"- {r.side.upper()} {r.quantity:g} {r.ticker}: {r.why}" for r in rejected),
            "Answer again, within the cash you actually have. If you want something you",
            "cannot afford, sell something first and list the sell before the buy.",
            # The retry is the one chance to correct a refusal, and advice about
            # cash does not help a watchlist refusal. Observed on the first live
            # probe: the model asked to research two names without untracking
            # anything, which is exactly the mistake this line answers.
            *(
                ["If a research was refused because the watchlist is full, untrack "
                 "something first and list the untrack before the research."]
                if any(r.side == "research" and "watchlist is full" in r.why for r in rejected)
                else []
            ),
            # Caught live 2026-09-15, same pass as the read-and-order bug above:
            # this is the pass's last turn, so a read asked for here was always
            # dropped — but nothing told the agent that before it tried. Said up
            # front now, so it spends this one chance fixing the order instead.
            "A read does not run on this turn — it is your one chance to fix the",
            "refusal above, not another chance to read. If you also want to read",
            "something, leave it for your next turn or wakeup and fix the order now.",
        ]

    # What "no money" means here is what screen() refuses at: below the research
    # price nothing can be commissioned, and below a share price nothing can be
    # bought. The research price is the lower of the two and the one this app
    # controls, so it is the threshold the prompt speaks about.
    research_price_floor = price if price else 0.01

    min_probability, min_risk_reward = get_conviction()
    floors = []
    if min_probability:
        floors.append(f"at least {min_probability:.0f}% chance of working")
    if min_risk_reward:
        floors.append(f"risk/reward of at least {min_risk_reward:.2f}")
    conviction_line = " and ".join(floors)

    if (watchlist and max_watchlist) or menu:
        # Shared by the watchlist and the candidate menu below, so the price
        # is explained exactly once. Since 2026-09-08 nothing is analysed
        # automatically — not even what is held — so every analysis, new
        # ticker or re-look, is this same $0.05 decision, and there is no
        # daily count on how many you may make: cash is what bounds it.
        lines += [
            "",
            "---",
            "",
            f"Nothing is analysed automatically, holdings included. A \"research\" order "
            f"costs ${price:,.2f} and runs inside this pass — you are shown what it found "
            "and can act on it before you finish. A bad choice of what to study is a loss "
            "like any other, so spend it where you actually want a fresh look — not "
            "because it is free to ask.",
        ]

    if watchlist and max_watchlist:
        # Every tracked ticker, priced and dated, so staleness is something
        # the agent can see rather than something it has to remember. Held
        # names are marked apart from watched-only ones because only the
        # second kind can be dropped, and hiding that invites orders Python
        # refuses.
        held_tickers = {h.ticker for h in book.holdings}
        # A table for the same reason the signals are one: the prose ran the
        # live price and the price at the last analysis into one sentence, and
        # "moved since" is a comparison between exactly those two.
        lines += [
            "",
            f"You track {len(watchlist)} of at most {max_watchlist} tickers. **Moved since** "
            f"is the price as of {as_of} against the price at the most recent analysis of that "
            "ticker — a large move on a stale analysis is the signal that a fresh look may be "
            "worth paying for. Tickers left watched with stale or 'never' analysed status consume "
            "watchlist slots; untrack watched tickers you no longer plan to trade to keep slots available.",
            "",
            "| Ticker | Held? | Price now | Day High | Day Low | Last analysed (ET) | Price then | Moved since | It said |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for ticker in sorted(watchlist):
            live = prices.get(ticker)
            price_text = f"${live:,.2f}" if live is not None else "unavailable"
            status = "held" if ticker in held_tickers else "watched"
            day_range = (day_ranges or {}).get(ticker)
            day_high = f"${day_range[1]:,.2f}" if day_range else "—"
            day_low = f"${day_range[0]:,.2f}" if day_range else "—"
            # **Newest by time, not by date.** `get_recent_signals` orders by
            # `signal_date`, a calendar date, so two analyses of one ticker on
            # one day come back in row order — and this row showed INTC at
            # $106.24 while the signals table above showed a later one at
            # $100.44, a 5.5% move. The agent read both and asked which was
            # current. Same sort as analysis_reader._newest_first, and local
            # for the same reason: every other caller reads that ordering.
            recent = sorted(
                db.get_recent_signals(ticker, limit=20),
                # getattr throughout: several tests pass signal-shaped
                # stand-ins rather than the model, the same way the Decision
                # unpacking and the trigger phrase already do.
                key=lambda x: (str(x.signal_date)[:10],
                               str(getattr(x, "created_at", "") or ""),
                               getattr(x, "id", 0) or 0),
                reverse=True,
            )[:1]
            when = then = move = said = "never"
            if recent and recent[0].price_at_signal:
                last = recent[0]
                when = _analysed_at(last)
                then = f"${last.price_at_signal:,.2f}"
                said = last.decision
                move = "—"
                if live is not None:
                    pct = (live - last.price_at_signal) / last.price_at_signal * 100
                    move = f"{pct:+.1f}%"
            lines.append(
                f"| {ticker} | {status} | {price_text} | {day_high} | {day_low} | "
                f"{when} | {then} | {move} | {said} |"
            )
        if len(watchlist) >= max_watchlist:
            lines.append(
                "That is the limit, so nothing new can be tracked until you stop "
                "watching something. Look for watched (unheld) tickers with stale or "
                "'never' analysed status and untrack them to free slots for new research."
            )

    if menu:
        lines += [
            "",
            "Nothing has been analysed on these yet — they are screened for being liquid and "
            "actively traded, not for being good. Researching one buys an analyst's opinion, "
            "not a position today:",
        ]
        for candidate in menu:
            move = f", {candidate.change_pct:+.1f}% today" if candidate.change_pct is not None else ""
            lines.append(
                f"- {candidate.ticker}: {candidate.name[:40]} at ${candidate.price:,.2f}"
                f"{move}, {candidate.volume_m:,.1f}M shares traded, via {candidate.source}"
            )

    lines += [
        "",
        "---",
        "",
        "Rules:",
        # **What the closed session actually stops, stated beside the orders
        # rather than only in the clock line (2026-09-12).** The first line of
        # every prompt has always said the market is shut and the agent read it
        # and ordered anyway: three probe runs wrote "the market is closed" and
        # placed a buy or a sell in the same breath, and six of the nine broker
        # failures on the live book are exactly that. One run showed the belief
        # behind it — "any buys/sells placed now will not execute until market
        # open" — which is a reasonable guess and wrong here, because this
        # broker refuses rather than queues.
        #
        # **Adjust is exempt, and that was checked rather than assumed.** An
        # earlier wording had the broker refusing "a buy, a sell or an adjust",
        # which the record contradicts: on Labor Day run 15 adjusted two exits
        # at 09:31 and runs 16, 17 and 18 had five buys and sells refused over
        # the next six hours. No adjust has ever been refused. An exit rests at
        # the broker instead of trading now, so there is nothing to reject —
        # and it is the one useful thing left to do with a position going into
        # a long weekend.
        *(
            []
            if watchdog.is_us_market_hours()
            else [
                "- **The market is closed right now, so a buy or a sell you place will not "
                "execute.** The broker refuses an order to trade outside the session: it is "
                "not queued for the open, it does not rest, it comes back as a failure and "
                "nothing happens. **Moving a stop or a target with `adjust` does work**, "
                "because that rests at the broker rather than trading now. Research, reading "
                "an analysis and choosing your next wakeup all work at any hour too.",
            ]
        ),
        # A balance at or below zero used to render as "must cost $-8.00 or
        # less in total", which is not an instruction anybody can follow. State
        # the condition, what it prevents, and what changes it.
        *(
            [
                f"- **You have no money to spend. The balance is ${book.cash:,.2f}.** You",
                "  cannot buy anything and cannot pay for a new analysis until that",
                "  changes. Selling is the only thing that raises cash.",
            ]
            if book.cash < research_price_floor
            else [
                f"- The buys you place must cost ${book.cash:,.2f} or less in total, added up "
                "across every buy. Not each — in total. If placing multiple buys in one turn, "
                "allocate quantities so that sum(quantity × price) fits within your available cash.",
            ]
        ),
        *(
            [
                f"- You may only open a new position on a signal that meets the conviction "
                f"floor: {conviction_line}. A signal below it, or one that does not state "
                "the number, cannot be bought. Selling is never blocked this way.",
            ]
            if conviction_line
            else []
        ),
        # Added 2026-09-09. Nothing ever forbade an early exit and Python has
        # always allowed one, but nothing said so either — and on this model a
        # capability permitted by omission is not permitted at all. The last
        # sentence is the load-bearing one: on 2026-09-08 the agent named a real
        # reason to sell AVGO, then held on the next pass because "existing
        # positions are already managed with resting exits".
        # The signal lines above carry the verdict and the levels, never the
        # reasoning. A Hold that means "keep a fifth of it and defend below
        # 102.70" reaches the agent as the same word as a flat Hold.
        *(
            [
                "- Nothing is analysed automatically, holdings included. To have something",
                "  looked at, use side \"research\" with a ticker and no quantity. **It runs",
                "  inside this pass**: you wait while it runs, and are then shown what it",
                "  decided and the analyst\'s own reasoning, with a chance to act on it",
                "  before you finish. You do not need to set a wakeup for it. The timing",
                "  line above says how long one takes here. A new ticker must come from the",
                "  candidate list above; one you already track can be re-researched as",
                "  often as you judge it worth $0.05. A second look the same day is",
                "  often the right call, not a wasteful one.",
                "  Choosing what to study is the only way anything changes,",
                "  and paying to study something you then ignore is how the money leaves",
                "  this account.",
                "- **Nothing is ever analysed unless you ask for it and pay for it.**",
                "  Rules watch your tracked tickers for a sharp move, a volume spike, a",
                "  stop or target being reached, and for earnings coming up. What they see",
                "  is reported to you above and nothing else happens — no analysis is",
                "  started on your behalf and nothing is charged. Whether a move is worth",
                "  studying is your call, and selling into it is often the better answer:",
                "  an analysis takes the time stated above, and the price will have moved",
                "  again by the time it lands.",
            ]
            if (watchlist or menu)
            else []
        ),
        *(
            [
                f"- You may track at most {max_watchlist} tickers. Tracking costs nothing by",
                "  itself; a \"research\" order is what charges. A full list cannot take a",
                "  new name until you free a slot. To stop watching one, use side \"untrack\"",
                "  with its ticker and no quantity. Untracking costs nothing and refunds",
                "  nothing.",
                "- Untracking frees a slot the same way a sell frees cash, and in the same",
                "  order: to research something when the list is full, list the untrack",
                "  first and the research after it.",
                "- You cannot untrack something you hold. Sell it first — untracking it",
                "  would leave you unable to ever research it again.",
            ]
            if max_watchlist
            else []
        ),
        *(
            [
                # Reworded 2026-09-09. "These are meant to be N-day trades"
                # read as a duration to serve out, when it was only ever an
                # upper bound — see the sell-any-time rule above.
                f"- The thesis behind a trade usually runs about {horizon_days} days, and a",
                "  position held much longer than that has outlived it, whether or not",
                "  anything has told you to sell. That is a ceiling, not a schedule.",
            ]
            if horizon_days
            else []
        ),
        "",
        "---",
        "",
        "**Answer in this shape:**",
        "",
        '{"reasoning": "one or two sentences", "next_wakeup": "2026-09-11T09:00", '
        '"next_wakeup_note": "what you want to remember from this pass", "orders": '
        '[{"ticker": "AAPL", "side": "buy", "quantity": 2, "reason": "why"},',
        ' {"ticker": "TSLA", "side": "buy", "quantity": 5, "order_type": "limit", '
        '"limit_price": 240.00, "time_in_force": "gtc", "reason": "why"},',
        ' {"ticker": "MSFT", "side": "adjust", "stop": 410.5, "reason": "why"},',
        ' {"ticker": "TSLA", "side": "cancel", "reason": "why"},',
        # Unconditional, unlike research/untrack below: the read rules above
        # are never gated on watchlist or menu, so the example should not be
        # either. prompt_evaluation.md flagged this order type as absent from
        # the schema shown to the model — this was the one real gap.
        ' {"ticker": "GOOGL", "side": "read", "date": "2026-09-08", "reason": "why"},',
        # Shown only where there is something to research — a menu of new
        # candidates, or a watchlist with something already on it.
        *(
            [' {"ticker": "INTC", "side": "research", "reason": "why"},']
            if (watchlist or menu)
            else []
        ),
        # The untrack example appears only where untracking is possible. The
        # live deployment has no cap and no such action, and an example of an
        # order it can only have refused would cost it a retry to learn that.
        *([' {"ticker": "NOK", "side": "untrack", "reason": "why"},'] if max_watchlist else []),
        # Always last, so exactly one line closes the array and the object no
        # matter which of the optional examples above are present.
        ' {"side": "note", "reason": "what would help you decide better"}]}',
        "",
        "Use an empty list for orders if you want to hold everything.",
    ]
    return "\n".join(_unwrapped(lines))


def _analysed_at(signal) -> str:
    """When an analysis started, to the minute, in Eastern.

    **The date alone could not order two analyses of one ticker on one day**,
    and on 2026-09-10 that is exactly what the agent hit: two INTC rows, one at
    $106.24 and one at $100.44, both dated 2026-09-10, and no way to tell which
    was current. It spent a paragraph guessing.

    `created_at` is UTC and null on rows written before 2026-09-08, which fall
    back to the bare date rather than inventing a time. It is when the analysis
    *started*, for every row, since 2026-09-11 — it had meant the finish on
    newer rows and the start on older ones, about sixteen minutes apart.
    """
    created = getattr(signal, "created_at", None)
    if not created:
        return str(signal.signal_date)[:10]
    try:
        when = created if isinstance(created, datetime.datetime) else datetime.datetime.fromisoformat(str(created))
        if when.tzinfo is None:
            when = when.replace(tzinfo=datetime.timezone.utc)
        return market_clock.now_et(when).strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return str(signal.signal_date)[:10]


def _unwrapped(lines: list[str]) -> list[str]:
    """Fold a rule's continuation lines back into one line.

    The rules are written wrapped in the source so the file stays readable,
    and that wrapping was reaching the model: a rule arrived as five lines,
    four of which begin mid-sentence. This repo already forbids hard-wrapping
    prose in Markdown for the same reason — a sentence split across lines
    renders and reads as though it were several.

    **Two spaces marks a continuation; one does not.** The JSON example at the
    end of the prompt is indented by one, and folding it would destroy the
    shape the model is being asked to copy.
    """
    out: list[str] = []
    for line in lines:
        if line.startswith("  ") and not line.startswith("   ") and out and out[-1].strip():
            out[-1] = out[-1].rstrip() + " " + line.strip()
        else:
            out.append(line)
    return out


# A string closed with an apostrophe instead of a quote, which is what run 44
# did: `"reason": "…to evaluate the thesis'`. The closing quote is simply
# missing, so the raw newline after it lands inside the string and json calls
# it an invalid control character. Unambiguous to spot — a string whose content
# really ended in an apostrophe would be written `…thesis\'"`, with the quote
# still there — so the apostrophe can only be the mistyped quote.
_UNCLOSED_STRING = re.compile(r"""(:\s*"[^"\n]*)'(\s*[,\}\]]?\s*)$""", re.M)
# A comma before a closing brace or bracket. JSON forbids it; removing one
# cannot change what the document means.
_TRAILING_COMMA = re.compile(r",(\s*[\}\]])")


def _repaired(raw: str):
    """One more try at a document json refused, or None.

    **Only repairs actually seen in this book, and only ones that cannot change
    a meaning.** A pass that guesses at a malformed answer is worse than a pass
    that skips: run 44 asked to research two tickers and lost both, which is a
    day of the experiment, but a repair that invented an order would be a trade
    nobody chose. Anything not listed here stays a loss.
    """
    fixed = _TRAILING_COMMA.sub(r"\1", _UNCLOSED_STRING.sub(r'\1"\2', raw))
    if fixed == raw:
        return None
    try:
        payload = json.loads(fixed)
    except ValueError:
        return None
    log.warning("Agent reply needed repairing before it would parse")
    return payload if isinstance(payload, dict) else None


def parse_decision(text: str) -> tuple[str, list[dict]]:
    """(reasoning, orders) from the model's reply.

    Tolerant on purpose: this model wraps JSON in prose and code fences often
    enough that a strict parser would throw away usable decisions. Anything
    that still can't be read yields no orders — the agent skips a day, which is
    the safe failure.
    """
    if not text:
        return "", []
    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    candidate = fenced.group(1) if fenced else text
    brace = re.search(r"\{.*\}", candidate, re.S)
    if not brace:
        return "", []
    try:
        payload = json.loads(brace.group(0))
    except ValueError:
        payload = _repaired(brace.group(0))
        if payload is None:
            log.warning("Agent reply was not parseable JSON: %s", text[:300])
            return "", []
    if not isinstance(payload, dict):
        return "", []
    orders = payload.get("orders")
    if not isinstance(orders, list):
        orders = []
    orders = [o for o in orders if isinstance(o, dict)]
    return str(payload.get("reasoning") or ""), orders + _salvage(payload)


# **Only the two sides that move no money.** A `"research"` or a `"note"` read
# out of the wrong place costs five cents or a line of text, and both are
# screened afterwards anyway. A `"sell": "AVGO"` at the top level does not say
# how many shares, and guessing at that is how a parser places an order nobody
# asked for. Those stay strict: if a buy or a sell is not in `orders`, it did
# not happen.
_SALVAGED = {
    "research": ("research", ("research",)),
    "note": ("note", ("note", "notes", "memo")),
    "memory": ("memory", ("memory", "memories")),
}
# Values a model writes when it means "nothing here". Left out rather than
# turned into an empty note.
_EMPTY = {"", "none", "null", "[]", "{}", "n/a", "-"}


def _salvage(payload: dict) -> list[dict]:
    """Orders the model put beside ``orders`` instead of inside it.

    **The parser is tolerant on purpose, and this is the same reason.** On
    2026-09-12 the agent answered `{"research": [{"ticker": "NVDA", ...}]}` with
    no `orders` key at all, and the analysis it asked for was dropped without a
    word. Reading back through fifty stored answers found two notes lost the
    same way — and a note is the agent telling the people who maintain it that
    something is missing, which makes a silent drop the worst kind.

    One in fifty is rare enough to be invisible and often enough to matter.
    """
    found: list[dict] = []
    for side, keys in _SALVAGED.values():
        for key in keys:
            if key in payload and key != "orders":
                found += _as_orders(payload[key], side)
    return found


def _as_orders(value, side: str) -> list[dict]:
    """One top-level value, as however many orders it is really carrying."""
    if isinstance(value, list):
        return [o for item in value for o in _as_orders(item, side)]
    if isinstance(value, dict):
        order = {**value, "side": side}
        if side in ("note", "memory") and not str(order.get("reason") or "").strip():
            order["reason"] = str(value.get("note") or value.get("message") or value.get("memory") or "").strip()
        return [order] if (side not in ("note", "memory") or order.get("reason") or order.get("action")) else []
    text = str(value or "").strip()
    if text.lower() in _EMPTY:
        return []
    if side in ("note", "memory"):
        return [{"side": side, "reason": text}]
    # A bare string for a ticker-shaped side is the ticker.
    return [{"side": side, "ticker": text.upper()}]


_WAKEUP_NOTE_MAX_CHARS = 300


def parse_wakeup_note(text: str) -> str | None:
    """The agent's note to its own future self, from its answer.

    **Not a justification for the wakeup — a handover.** The agent has no
    memory between passes. Everything it worked out this pass is gone by the
    next one unless the prompt carries it, and the prompt carries prices and
    positions, not conclusions. This is the one place it can write down what it
    wants to remember: what it was watching, what it decided to wait for, what
    it had already ruled out.

    Read with the same tolerance as the rest of the answer — a model that
    wraps JSON in prose often enough to need parse_decision's leniency needs it
    here too.
    """
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    brace = re.search(r"\{.*\}", fenced.group(1) if fenced else text, re.S)
    if not brace:
        return None
    try:
        payload = json.loads(brace.group(0))
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    note = (
        payload.get("next_wakeup_note")
        or payload.get("next_wakeup_reason")
        or payload.get("wakeup_reason")
    )
    note = str(note or "").strip()
    return note[:_WAKEUP_NOTE_MAX_CHARS] or None


def parse_wakeup(text: str, now: datetime.datetime | None = None) -> datetime.datetime | None:
    """When the agent asked to be woken next, as a real instant.

    Separate from ``parse_decision`` rather than a third element of its tuple:
    every caller unpacks that pair, and widening it would break them all to
    carry a value most of them do not want.

    **Tolerant, like the rest of this model's output handling.** The prompt asks
    for minutes or an Eastern clock time, and the model writes prose. All of
    "45", "45 minutes", "45m", "14:30" and "2:30 PM" are read. Anything else
    returns None, which the scheduler treats as "asked for nothing" — a
    fallback, never a decision the agent gets credited with.

    The value is not bounded here. ``market_clock.clamp_wakeup`` does that, so
    what the agent asked for and what it got stay separately visible.
    """
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    brace = re.search(r"\{.*\}", fenced.group(1) if fenced else text, re.S)
    if not brace:
        return None
    try:
        payload = json.loads(brace.group(0))
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    raw = payload.get("next_wakeup")
    if raw is None:
        return None

    here = market_clock.now_et(now)
    if isinstance(raw, (int, float)):
        return here + datetime.timedelta(minutes=float(raw))
    text_value = str(raw).strip().lower()
    if not text_value:
        return None
    # **A trailing zone label used to cost the whole answer.** On 2026-09-04 the
    # last pass of the day asked for "3:58 PM ET"; the pattern matched "3:58 pm"
    # and refused the " et", so the run stored no wakeup. Nothing else was
    # scheduled to notice. Every time here is Eastern already, so the label adds
    # nothing and is dropped before anything else is read.
    text_value = re.sub(r"\s*\b(et|est|edt|eastern|us/eastern|america/new_york)\b\.?$", "", text_value)
    text_value = text_value.strip().rstrip(".")

    # **An ISO datetime, which is what the rules now ask for.** Everything
    # below is kept because a model still writes "45 minutes" sometimes and
    # dropping a usable answer costs a whole pass — but one unambiguous form
    # in the instruction is what stopped the agent converting 9 AM into "1021
    # minutes" by hand to avoid guessing how a bare clock time would be read.
    #
    # A naive value is Eastern, because that is the clock the prompt speaks in
    # and the rules say so. An offset or a trailing Z is honoured as given.
    iso = re.match(r"^\d{4}-\d{2}-\d{2}[ t]\d{2}:\d{2}(:\d{2})?([+-]\d{2}:?\d{2}|z)?$", text_value)
    if iso:
        try:
            when = datetime.datetime.fromisoformat(text_value.replace("z", "+00:00").replace(" ", "T"))
        except ValueError:
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=here.tzinfo)
        return when.astimezone(here.tzinfo)

    # A clock time — "14:30", "2:30 pm". Checked before the minutes form,
    # because "1430" and "14:30" mean very different things and only the
    # colon tells them apart.
    clock = re.match(r"^(\d{1,2}):(\d{2})\s*(a\.?m\.?|p\.?m\.?)?$", text_value)
    if clock:
        hour, minute = int(clock.group(1)), int(clock.group(2))
        meridiem = (clock.group(3) or "").replace(".", "") or None
        if meridiem == "pm" and hour < 12:
            hour += 12
        elif meridiem == "am" and hour == 12:
            hour = 0
        if hour > 23 or minute > 59:
            return None
        at = here.replace(hour=hour, minute=minute, second=0, microsecond=0)
        # **The next occurrence, not this morning's.** A clock time already
        # past resolved into the past, and `clamp_wakeup` then pulled it to
        # the floor — so "09:00" asked at 3:59 PM woke the agent at 4:04 PM
        # rather than the next morning, and the record showed it choosing
        # that. A model reasoning at the close spotted the ambiguity and spent
        # a paragraph converting 9 AM into "1021 minutes" by hand to dodge it.
        if at <= here:
            at += datetime.timedelta(days=1)
        return at

    minutes = re.match(r"^(\d+(?:\.\d+)?)\s*(m|min|mins|minute|minutes)?$", text_value)
    if minutes:
        return here + datetime.timedelta(minutes=float(minutes.group(1)))
    hours = re.match(r"^(\d+(?:\.\d+)?)\s*(h|hr|hrs|hour|hours)$", text_value)
    if hours:
        return here + datetime.timedelta(hours=float(hours.group(1)))
    log.info("Could not read a wakeup time from %r", raw)
    return None


def screen(
    orders: list[dict],
    book: agent_book.Book,
    prices: dict[str, float | None],
    signals_by_ticker: dict | None = None,
    menu: set[str] | None = None,
):
    """Split proposed orders into (accepted, rejected), applying each accepted
    one to a running copy of the book.

    This is the part that must not be simplified into a per-order check against
    the opening balance: three buys that each fit the starting cash do not
    necessarily fit together.

    The conviction floor is enforced here rather than by leaving low-conviction
    signals out of the prompt. The agent still needs to see them — a Sell it
    has no confidence in is still a reason to close a position it holds — so
    the floor applies to opening a position, not to knowing about one. And it
    is checked in Python for the same reason every other limit is: a rule
    stated only in the prompt is a request, not a limit.

    A floor of zero, which is the default, skips the check entirely.
    """
    min_probability, min_risk_reward = get_conviction()
    # Research is spent from the same cash as the trades, and it is spent
    # first: a buy listed after it must see the money already gone, or the
    # agent could commit the same dollar twice.
    research_price = research.get_price()
    max_watchlist = _max_watchlist()
    researched: list[str] = []
    untracked: list[str] = []
    # A running watchlist, not the opening one, for the same reason cash is
    # running: an untrack listed before a research frees a slot for it in the
    # same pass, and two researches against one free slot must not both pass.
    watchlist = set(db.get_watchlist())
    cash = book.cash
    held = {h.ticker: h.quantity for h in book.holdings}
    # A running copy, not the opening one, for the same reason cash is
    # running: cancelling a pending limit sell listed earlier in the same
    # answer frees its shares for a sell listed after it.
    reserved_shares = dict(book.reserved_shares)
    accepted: list[dict] = []
    rejected: list[agent_book.Rejection] = []

    for order in orders:
        ticker = str(order.get("ticker", "")).upper().strip()
        if str(order.get("side", "")).lower().strip() == "note":
            # A message to whoever maintains this, and nothing else. It buys
            # nothing, sells nothing, costs nothing, and is refused for
            # nothing — so it is accepted before any check that could reject
            # it, and it never touches cash, shares or the watchlist.
            #
            # It is deliberately not a request that anything acts on. The
            # agent talking is not a second decision-maker in the record; the
            # agent trading would be. If we build what it asks for, that is a
            # change like any other and belongs in JOURNEY.md first.
            text = str(order.get("reason") or "").strip()
            if text:
                accepted.append(
                    {"side": "note", "ticker": ticker, "quantity": 0, "reason": text}
                )
            continue

        if str(order.get("side", "")).lower().strip() == "memory":
            text = str(order.get("reason") or order.get("text") or order.get("note") or order.get("memory") or "").strip()
            action = str(order.get("action") or "").lower().strip()
            idx = order.get("index")
            accepted.append(
                {"side": "memory", "ticker": ticker, "quantity": 0, "reason": text, "action": action, "index": idx}
            )
            continue

        if str(order.get("side", "")).lower().strip() == "research":
            # Neither a buy nor a sell: it moves cash but no shares, and what
            # it buys is an opinion rather than a position today.
            #
            # Since 2026-09-08 there is no sweep to defer to, so every
            # commission runs right after this pass — there is no separate
            # "tomorrow" any more; asking later is just choosing a later
            # next_wakeup and researching then.
            #
            # **A ticker already on the watchlist may be re-researched, as
            # often as the agent is willing to pay for it.** Before
            # 2026-09-08 that was refused outright, on the assumption that
            # the sweep was covering it for free every morning. With nothing
            # covering it automatically any more — holdings included —
            # refusing a fresh look at something already tracked would mean
            # nothing could ever be re-analysed at all.
            #
            # There used to be a once-a-day guard here too
            # (`db.has_signal_today`), removed the same day for the same
            # reason as the daily count below: an analysis finishes in about
            # twenty minutes, well inside a trading day, and a price nearing
            # its stop or target is exactly the case where a second look the
            # same day is the right call, not a wasteful one. `has_signal_
            # today` still gates the watchdog's own automatic move-triggered
            # re-analysis (backend/services/watchdog.py) — a different
            # question, the system deciding whether to auto-trigger, not the
            # agent deciding whether to ask.
            #
            # No cap on how many of these a pass may commission in a day
            # (removed 2026-09-08, alongside the sweep). The count existed
            # to pace GPU load within the sweep's fixed pre-open window,
            # which no longer exists — research is spread across the day as
            # the agent decides to spend on it, one $0.05 decision at a
            # time, and cash is what actually bounds it now.
            already_tracked = ticker in watchlist
            why = None
            if menu is not None and ticker not in menu and not already_tracked:
                why = "not on today's candidate list"
            elif ticker in researched:
                why = "already commissioned this pass"
            elif not already_tracked and len(watchlist) >= max_watchlist:
                # Named as a swap rather than a wall, because it is one: an
                # untrack listed earlier in the same answer would have made
                # room. The prompt says so too; this is what it reads like
                # when the agent has not done it. Only a genuinely new
                # ticker needs a free slot — re-researching one already
                # tracked does not grow the list.
                why = (
                    f"the watchlist is full at {max_watchlist} — untrack something "
                    "first, and list the untrack before this"
                )
            elif cash < research_price:
                why = f"costs ${research_price:,.2f} and only ${cash:,.2f} is left"
            if why:
                rejected.append(
                    agent_book.Rejection(ticker=ticker, side="research", quantity=0, why=why)
                )
                continue
            cash -= research_price
            researched.append(ticker)
            watchlist.add(ticker)
            accepted.append({**order, "ticker": ticker, "side": "research", "quantity": 0})
            continue

        if str(order.get("side", "")).lower().strip() == "untrack":
            # Moves no cash and no shares. What it changes is the watchlist
            # cap: research adds to it permanently and nothing else here
            # removes.
            why = None
            if ticker not in watchlist:
                why = "not being watched, so there is nothing to stop watching"
            elif held.get(ticker, 0.0) > 0:
                # Enforced here rather than asked for in the prompt, like
                # every other limit that must hold. Untracking a holding
                # would take away the one way left to re-research it —
                # nothing is analysed automatically any more, held tickers
                # included, so a tracked position is the only kind you can
                # still ask about. Sell it first if it is genuinely not
                # worth watching.
                why = (
                    f"holds {held[ticker]:g} of it — sell it first, since untracking it "
                    "would leave you unable to ever research it again"
                )
            if why:
                rejected.append(
                    agent_book.Rejection(ticker=ticker, side="untrack", quantity=0, why=why)
                )
                continue
            watchlist.discard(ticker)
            untracked.append(ticker)
            accepted.append({**order, "ticker": ticker, "side": "untrack", "quantity": 0})
            continue

        if str(order.get("side", "")).lower().strip() == "adjust":
            # Moves no cash and no shares, so the running-balance machinery
            # below has nothing to say about it. What it does need is a
            # position to rest on.
            quantity_held = held.get(ticker, 0.0)
            if quantity_held <= 0:
                rejected.append(
                    agent_book.Rejection(
                        ticker=ticker, side="adjust", quantity=0,
                        why="holds none of it, so there are no exits to move",
                    )
                )
                continue
            if order.get("stop") is None and order.get("target") is None:
                rejected.append(
                    agent_book.Rejection(
                        ticker=ticker, side="adjust", quantity=0,
                        why="no new stop or target given",
                    )
                )
                continue
            accepted.append(
                {**order, "ticker": ticker, "side": "adjust", "quantity": quantity_held}
            )
            continue

        if str(order.get("side", "")).lower().strip() == "cancel":
            # Withdraws a still-unfilled entry order — never a resting
            # protective exit, which stays reachable through adjust or a
            # sell. Moves no cash directly; the cash it frees comes back
            # through build_book's reservation once the row settles.
            pending_entry = next(
                (t for t in db.get_pending_agent_trades() if t.ticker == ticker and not t.is_stop),
                None,
            )
            if pending_entry is None:
                rejected.append(
                    agent_book.Rejection(
                        ticker=ticker, side="cancel", quantity=0,
                        why="nothing pending to cancel",
                    )
                )
                continue
            if pending_entry.side == "sell":
                # Frees the shares for a sell listed later in this same
                # answer, the same way an untrack frees a watchlist slot.
                reserved_shares[ticker] = max(
                    0.0, reserved_shares.get(ticker, 0.0) - pending_entry.quantity
                )
            accepted.append({**order, "ticker": ticker, "side": "cancel", "quantity": 0})
            continue
        running = agent_book.Book(
            budget=book.budget,
            cash=cash,
            realized_pnl=book.realized_pnl,
            holdings=[
                agent_book.Holding(ticker=t, quantity=q, avg_cost=0.0) for t, q in held.items()
            ],
            reserved_shares=reserved_shares,
        )
        price = prices.get(ticker)
        rejection = agent_book.validate(order, running, price)
        if rejection is not None:
            rejected.append(rejection)
            continue

        quantity = float(order["quantity"])
        side = str(order["side"]).lower()
        if side == "buy":
            why = fails_conviction(
                (signals_by_ticker or {}).get(ticker), min_probability, min_risk_reward
            )
            if why is not None:
                rejected.append(
                    agent_book.Rejection(ticker=ticker, side=side, quantity=quantity, why=why)
                )
                continue
        if side == "buy":
            if str(order.get("order_type") or "").lower().strip() == "limit":
                # Reserve at the stated limit price, the true worst case for
                # a limit buy — and don't add the shares to the running
                # `held` yet. A limit order may never fill this pass (or at
                # all), so a later order in the same answer (an adjust, a
                # sell, an untrack) must still see this ticker as not held.
                cash -= quantity * float(order["limit_price"])
            else:
                cash -= quantity * price
                held[ticker] = held.get(ticker, 0.0) + quantity
        else:
            # A sell is allowed without a price — you can always exit a
            # position — but unknown proceeds are counted as zero rather than
            # guessed, so they can't fund a later buy in the same pass.
            cash += quantity * price if price is not None else 0.0
            held[ticker] = held.get(ticker, 0.0) - quantity
            if held[ticker] <= 0:
                held.pop(ticker, None)
        accepted.append({**order, "ticker": ticker, "side": side, "quantity": quantity})

    return accepted, rejected


def current_regime_line() -> str | None:
    """One line of market context — VIX, SPY against its 200-day, the yield
    curve — already computed for the 12:45 post.

    How aggressively to deploy cash is exactly the kind of judgement this
    should inform, and the numbers were being thrown away every morning. Best
    effort: a failed fetch drops the line rather than the decision.
    """
    from backend.services import regime

    try:
        data = regime.fetch_regime()
        label, emoji = regime.classify_regime(
            data.vix, data.spy_vs_ma_pct, data.curve_spread_pct
        )
    except Exception:
        log.warning("Couldn't read the market regime for the agent prompt", exc_info=True)
        return None
    line = f"Market conditions today: {label}."
    if data.vix is not None:
        line += f" VIX {data.vix:.1f}."
    if data.spy_vs_ma_pct is not None:
        line += f" The S&P is {data.spy_vs_ma_pct:+.1f}% against its 200-day average."
    return line


def _horizon_days() -> int | None:
    """How long a position is meant to be held, from the configured horizon."""
    from backend.services.signals import horizon_params

    try:
        return horizon_params(analysis.get_horizon())["eval_days"]
    except Exception:
        return None


def _price_map(tickers) -> dict[str, float | None]:
    return {ticker: get_current_price(ticker) for ticker in sorted(set(tickers))}


# Lifted out of _ask so it can be hashed alongside the rest of the prompt.
# A change here changes the agent's behaviour as surely as a change to the
# rules, and an experiment that cannot tell which prompt produced which
# decision cannot attribute a change in behaviour to anything.
# "paper-trading" was dropped from here on 2026-09-09 along with the opening
# line of build_prompt. It appeared in both, and leaving it in the system
# message would have kept the tell in the more influential of the two.
# How much reading one pass may do before it has to decide.
#
# **A person deciding whether to buy reads the research first**, and often more
# than one piece of it. Rationing that to a single analysis was a restriction
# with no reason behind it but the fear of a loop, and tokens spent reading
# research are the tokens this experiment most wants spent — see "What the
# experiment is for" in CLAUDE.md.
#
# Two budgets rather than one, because they stop different things. The analyses
# cap stops a pass reading the whole watchlist; the turns cap stops a model
# asking for one more thing on every round. Six and three allow "read three,
# think, read two more, decide" and refuse an unbounded chain.
_MAX_READS_PER_PASS = 6
_MAX_READ_TURNS = 3
# **How many times the agent may act and then be asked again in one pass.**
# Acting is not free the way reading is — each turn can move real money — so
# this stays bounded rather than open-ended. Three only covered one ticker's
# happy path: commission research, act on the verdict, see the fill. It left
# no turn to rest a stop and target the broker refused at purchase, or to
# correct a mistake once the fill was seen — both real, both already allowed
# by this app's own tools.
#
# **Raised to 8 on 2026-09-15**, once research measured 9-10 minutes rather
# than 16-20: a turn that commissions research holds `_pass_lock` for the
# whole wait, so the old figure made even 3 of those turns a possible
# hour-long pass. Not a number the model is ever told — see the read budget
# above for the one that is — so this is a pure backstop, raised to see
# whether the agent actually uses the room or keeps settling a pass in 2-3
# turns regardless.
_MAX_ACT_TURNS = 8


# **Rules that never change, moved here on 2026-09-10.** They were rebuilt into
# every user message, which cost tokens on every pass and buried the figures
# that do change. The split is by whether a rule quotes a number from this
# pass — the cash limit, the watchlist cap, the trade horizon — not by how
# important it is. Those stay in the user message, beside the numbers they
# name.
_FIXED_RULES = [
    "- Orders execute in the order you list them, so a sell frees its cash for "
    "a buy listed after it. To buy something you cannot currently afford, "
    "sell something first and put that sell earlier in the list.",
    "- You may only sell shares you hold. No shorting, no options. Whole shares "
    "only.",
    "- What the analysts' decisions mean: Buy means they expect it to rise. "
    "Sell means they expect it to fall, so exit it if you hold it. Hold means "
    "no action is recommended — if you do not own it, a Hold is not a reason "
    "to buy it.",
    "- Some signals carry how good the analyst thought the bet was. The chance "
    "of working is their own estimate. Risk/reward compares what is gained if "
    "the target is reached against what is lost if the stop is hit. Expected "
    "value is in R-multiples, where one R is the amount risked: positive means "
    "the bet pays at the stated odds, negative means it does not. Signals "
    "without these numbers are not worse bets, only ones where the analyst "
    "did not say.",
    # The one word that hides the most. A Hold on 2026-09-10 meant "keep a
    # fifth of the position and defend it below 102.70", and reached the agent
    # as the same word as a flat Hold. It spent a long stretch of one pass
    # reasoning about whether a particular Hold was worth acting on, which is
    # exactly the question a read answers.
    "- **A Hold is the decision that says least**, and the one most worth "
    "reading. It can mean the analyst saw nothing, or that they saw a case "
    "worth holding a position for and no case for adding to it. The word is "
    "the same either way.",
    "- You can also move the stop and take-profit on something you already "
    "hold, without buying or selling any of it. Use side \"adjust\" with a "
    "\"stop\" or a \"target\" price, or both. The stop must be below the current "
    "price and the target above it, or the order would execute the moment it "
    "was placed. Raising a stop as a position gains is how a profit is "
    "protected; today's analysis is what tells you where the thesis now "
    "breaks. If a holding has an UNSET stop or is in unrealized profit, use "
    "adjust to set or raise its stop to protect gains.",
    "- You can sell any position at any time, for your own reasons. You do not "
    "have to wait for a stop or a target to be reached, and you do not need "
    "an analyst to say Sell first. Taking a profit while it is there, cutting "
    "a loss before the stop gets to it, and trimming a position that has "
    "grown too large are all yours to decide on any pass. A resting stop is a "
    "floor under a position, not a reason to leave it alone.",
    "- A buy or a sell defaults to a market order. Add \"order_type\": \"limit\" "
    "and a \"limit_price\" to name your own price instead — a buy fills at "
    "that price or better, a sell at that price or better. Add "
    "\"time_in_force\": \"gtc\" to let it wait past today; leave it out, or "
    "use \"day\", and it is gone at the close if it never filled.",
    "- **A limit buy does NOT get a stop or target placed under it, even when "
    "the signal has one.** It is not filled yet, and may not fill this pass "
    "at all, so there is nothing to rest an exit on. The instant it fills you "
    "are woken and told the fill price — set the stop and/or target yourself "
    "then, with \"adjust\", off the price you actually got. A limit sell "
    "needs no such follow-up: it can only close a position you already hold.",
    "- **A GTC order keeps something tied up until it fills or you cancel "
    "it** — a buy keeps its cash reserved, a sell keeps its shares "
    "committed and unsellable again until it resolves. Neither is free to "
    "leave sitting. Use side \"cancel\" with the ticker to withdraw one that "
    "has not filled. This never touches a resting stop or target; move "
    "those with \"adjust\", or sell the position.",
    # **The stance changed on 2026-09-10, from sparing to expected.** It read
    # "read when the reasoning would change what you do, not out of habit",
    # which is advice to hesitate. Reading is free and one pass can only do it
    # once, so the cost of reading too often is a turn and the cost of reading
    # too rarely is acting on a word. A signal line is a verdict; the reasoning
    # is where the case for it lives.
    "- **A signal line is a verdict, not the case for it.** The reasoning "
    "behind it is on record and reading it costs nothing — you already paid "
    "for that analysis. Read one before you act on it: the line tells you what "
    "the analyst concluded, and the reasoning tells you what they saw, how "
    "sure they were, and what would change their mind.",
    "- To read one, use side \"read\" with a ticker, and a \"date\" like "
    "\"2026-09-08\" if you want a particular analysis rather than the newest. "
    "Comparing the one you bought on against today's is how you tell whether a "
    "thesis still holds.",
    f"- You may read up to {_MAX_READS_PER_PASS} analyses before deciding, and "
    f"ask again after reading up to {_MAX_READ_TURNS} times — list several "
    "reads together if you want them at once. Read what you need: this is the "
    "one place spending is encouraged, because a decision made on a verdict "
    "alone is the thing this is trying to avoid. Reading is not acting, "
    "though: a pass that only read is an idle pass, and the budget runs out.",
    # Caught live 2026-09-15: an answer bundled a read with a buy and an
    # adjust, and the buy and adjust vanished with no trace — not refused, not
    # failed, nowhere the model could see it. A read changes the pass's flow
    # rather than the book, so only the read runs; but the silence was the
    # bug, not the drop itself.
    "- **A read is the only thing that runs from an answer that asks for "
    "one.** If your answer also has a buy, a sell, an adjust, an untrack or a "
    "note, none of it is carried out — you will be shown the read's result "
    "and asked again, and you must resend anything else you still want then. "
    "Answer with only the read when you mean to read first and decide after.",
    "- Doing nothing is a valid answer, and often the right one.",
    "- You decide when you are next asked, and nothing else does. Put "
    "\"next_wakeup\" beside your orders as an ISO datetime — "
    "\"2026-09-11T09:00\" is Eastern, and a trailing Z or an offset is read as "
    "given. One format, so there is nothing to work out: no minute arithmetic, "
    "and no question of which day a bare clock time means. The minimum is 5 "
    "minutes from now and the maximum is 4 days.",
    "- **Write a note to your future self with \"next_wakeup_note\".** You will "
    "not remember this pass. Next time you are given the same kind of prompt "
    "you have now — the clock, your cash, your holdings, the signals, all of it "
    "current — plus why you were woken and this note, and nothing else from "
    "today.",
    "- **The note is for what the next prompt cannot tell you.** It will already "
    "show your cash, your positions and every price, so a note about those is a "
    "wasted one. Write down instead what you worked out and could not recover: "
    "what you are waiting to see, what you ruled out and why, what would change "
    "your mind. Not \"my cash is low\" — the next prompt says your cash. "
    "Rather \"ruled out HPE at a $59.83 entry, worth another look under $56\", "
    "or \"holding AVGO until it breaks $366.16; sell if it closes below $354\". "
    "One or two sentences, and only if you have something worth carrying.",
    "- **Write long-term persistent notes with side \"memory\".** Unlike \"next_wakeup_note\" "
    "which only lasts until the next wakeup, a note written with side \"memory\" (e.g. "
    "{\"side\": \"memory\", \"reason\": \"your long-term note\"}) is saved permanently "
    "and injected into every prompt until cleared with {\"side\": \"memory\", \"action\": \"clear\"}.",
    "- You may ask for any time, including before the open, after the close and "
    "at the weekend. Research and planning work at any hour. Orders do not — "
    "the broker rejects one outright while the market is shut, and you will "
    "see that rejection here next time. Waking early to commission the "
    "analyses you want ready for the open is a good use of this; sending an "
    "order at midnight is not.",
    "- **If you name no time, you will next be asked at the following open.** "
    "That is a fallback, not a plan. Name the time you actually want. Waking "
    "costs nothing, which is exactly why asking for the minimum every time "
    "wastes the day rather than saving it.",
    "- Before answering, add up what your buys cost and check it against your "
    "cash.",
    "- If something is stopping you deciding well — a number you cannot see, "
    "a tool you do not have, a rule that contradicts another — say so with "
    "side \"note\". It reaches the people who maintain you. Nothing acts on it "
    "automatically, so it is a message and not a request.",
    "- A note is never a substitute for a decision. Leave one if you have "
    "something to say, and still answer with what you want done today, "
    "including doing nothing. Reply with JSON only, in the shape specified below:",
]

SYSTEM_PROMPT = (
    "You are a disciplined portfolio manager. You answer with JSON only — no "
    "prose outside it. You never spend more cash than you have and never sell "
    "shares you do not hold.\n\n"
    "The rules below never change. The message that follows carries this "
    "pass's own figures — the clock, your cash, your holdings, the analyst "
    "signals — and the few rules that quote a number from them.\n\n"
    + "\n".join(_FIXED_RULES)
)


class _Answer(str):
    """The model's reply, carrying what the call cost.

    A ``str`` subclass on purpose. Every caller and a dozen test fakes treat
    the answer as a plain string — several patch ``_ask`` with
    ``lambda _p: "..."`` — so widening the return type to a tuple or a
    dataclass would break all of them for the sake of three numbers only one
    caller reads. As a str it is still exactly the string it was, and
    ``_decide`` picks the counts off it with ``getattr`` so a fake that
    returns a bare string reports zero rather than raising.
    """

    def __new__(cls, text, prompt_tokens=0, completion_tokens=0, seconds=0.0, thinking=None):
        answer = super().__new__(cls, text)
        answer.prompt_tokens = prompt_tokens
        answer.completion_tokens = completion_tokens
        answer.seconds = seconds
        answer.thinking = thinking
        return answer


def _invoke(llm, prompt: str) -> tuple[str, str | None, int, int]:
    """One call to the model: (content, thinking, prompt tokens, completion tokens).

    **Goes through the OpenAI-compatible client LangChain already built**,
    rather than ``llm.invoke``, for one reason: the model's reasoning. Ollama
    returns it on ``/v1/chat/completions`` as a ``reasoning`` field beside the
    content, and ``ChatOpenAI`` drops it on purpose — its own docstring says it
    targets the official OpenAI specification and does not extract
    "non-standard response fields added by third-party providers". There is no
    flag to keep it. Using ``llm.client`` inherits the base URL, the key and
    the timeouts that were configured once, so nothing about the connection is
    duplicated here.

    **Falls back to ``llm.invoke`` for any client that does not work this
    way** — Anthropic and Google go through their own LangChain packages, and
    switching provider is a config change this app supports. That path reads
    the thinking from the response's thinking blocks instead. Until 2026-09-13
    it returned None, so every Gemini pass stored an empty ``thinking``.
    """
    client = getattr(llm, "client", None)
    if client is not None and hasattr(client, "create"):
        try:
            # The decision pass reaches the client directly rather than through
            # the graph, so it needs the throttle attached here too — attaching
            # is idempotent, so asking twice costs nothing.
            llm_throttle.attach(llm)
            raw = client.create(
                model=llm.model_name,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
            )
            message = raw.choices[0].message
            thinking = getattr(message, "reasoning", None) or (
                message.model_extra or {}
            ).get("reasoning")
            usage = raw.usage
            return (
                message.content or "",
                thinking or None,
                getattr(usage, "prompt_tokens", 0) or 0,
                getattr(usage, "completion_tokens", 0) or 0,
            )
        except llm_throttle.DailyLimitReached:
            # Not a problem with the client's shape. The fallback would be
            # refused the same way.
            raise
        except Exception:
            # Never fatal. A pass must not be lost because the richer path
            # failed on a client shape this did not anticipate.
            log.warning("Falling back to the LangChain client for this pass", exc_info=True)

    message, thinking = llm_content.invoke_keeping_thinking(
        llm, [("system", SYSTEM_PROMPT), ("human", prompt)]
    )
    content, _ = llm_content.split_thinking(message.content)
    prompt_tokens, completion_tokens = llm_usage.tokens_from_message(message)
    return content, thinking, prompt_tokens, completion_tokens


def _ask(prompt: str) -> _Answer:
    """One call to the model: the answer, what it cost, and how it got there.

    The counts come from the provider's ``usage`` block, never a tokenizer
    estimate — see backend/services/llm_usage.py. This client is the one no
    ``UsageTracker`` attaches to (a tracker binds to the graph's two LLM
    objects, and the decision pass does not go through the graph), which is
    why the reading happens here rather than there. Until 2026-09-09 nothing
    counted it at all: an ``agentrun`` row recorded the prompt and the answer
    in full and not one token of what they cost.

    ``thinking`` is the model's own reasoning, which is most of what it
    generates and was thrown away until the same day — see ``_invoke``. On one
    replayed pass the stored answer was 606 characters against 4,034 of
    reasoning.
    """
    started = time.monotonic()
    content, thinking, prompt_tokens, completion_tokens = _invoke(
        analysis._quick_think_llm(), prompt
    )
    seconds = time.monotonic() - started
    return _Answer(content, prompt_tokens, completion_tokens, seconds, thinking)


def wakeup_due(now: datetime.datetime | None = None) -> datetime.datetime | None:
    """The wakeup the agent asked for, if it has arrived. None otherwise.

    Read from the last stored run, so a wakeup survives a restart — the pass
    that asked for it and the pass that answers it are different processes an
    hour apart.

    Also None once the wakeup has been served, which the run ordering gives for
    free: the pass that runs on waking stores its own next time, so the one
    just used is no longer the latest.

    **A pass that asked for nothing falls back to the next open.** Since the
    fixed decision pass was removed there is no other clock, so a run with no
    readable wakeup would end the experiment in silence — no error, no alert,
    just an agent that never runs again and looks like one choosing to sit
    still. That is not hypothetical: on 2026-09-04 the last pass of the day
    asked for "3:58 PM ET" and the parser dropped the zone label.

    The fallback is not a schedule anybody chose. It is what happens when the
    agent's answer cannot be read, and it makes a bad parse cost one pass
    rather than the experiment.
    """
    runs = db.get_agent_runs(limit=1)
    if not runs:
        # Nothing has ever run. The next open is the first sensible moment.
        return market_clock.next_open(now)
    here = market_clock.now_et(now)
    wanted = runs[0].next_wakeup
    if wanted is None:
        ran_at = runs[0].ran_at
        if ran_at is not None and ran_at.tzinfo is None:
            ran_at = ran_at.replace(tzinfo=datetime.timezone.utc)
        fallback = market_clock.next_open(ran_at)
        return fallback if fallback <= here else None
    if wanted.tzinfo is None:
        wanted = wanted.replace(tzinfo=datetime.timezone.utc)
    return wanted.astimezone(here.tzinfo) if wanted <= here else None


def _unsettled_cash() -> float:
    """Cash from sales that has not settled, as the broker sees it.

    **This is the real constraint on a cash account**, and the one the agent
    was never shown. Webull refuses a bracket order against unsettled funds, so
    a buy made with it gets a plain order and separately-armed exits instead —
    and that second step can fail and leave the position unprotected.

    The pattern day trader rule is *not* the constraint here and was
    deliberately not modelled: PDT governs margin accounts and this one is
    INDIVIDUAL_CASH. Simulating a rule the account does not have would make the
    record less faithful, not more.

    Zero on any failure. A missing number must never read as a large unsettled
    balance and talk the agent out of a purchase it could have made.
    """
    try:
        balance = sandbox_broker.get_balance() or {}
        assets = balance.get("account_currency_assets") or []
        if not assets:
            return 0.0
        return float(assets[0].get("unsettled_cash") or 0.0)
    except Exception:
        log.exception("Could not read the unsettled cash balance")
        return 0.0


def _recent_wakeups() -> list[dict]:
    """The last several passes, and whether each one did anything.

    Read from the stored runs, not held in memory: a wakeup the agent asked for
    at 10am is judged by a pass that runs in a different process an hour later.

    "Acted" counts orders placed, exits adjusted, research commissioned and
    names untracked. **A note does not count**, for the same reason it does not
    count anywhere else — a pass that only said something is still an idle
    pass, and letting it read as action would tell the agent its cadence is
    earning more than it is.
    """
    out: list[dict] = []
    for row in reversed(db.get_agent_runs(limit=_WAKEUPS_SHOWN)):
        acted = bool((row.placed or 0) or (row.adjusted or 0))
        # Research and untracks live in the orders JSON rather than in a count
        # column, so a pass that only commissioned research would otherwise
        # read as idle — which is exactly backwards, since choosing what to
        # study is the only way anything new enters the account.
        if not acted and row.orders:
            try:
                sides = {o.get("side") for o in json.loads(row.orders)}
                acted = bool(sides & {"buy", "sell", "adjust", "research", "untrack"})
            except (ValueError, AttributeError, TypeError):
                pass
        at = row.ran_at
        if at is not None and at.tzinfo is None:
            at = at.replace(tzinfo=datetime.timezone.utc)
        out.append({
            "at": market_clock.now_et(at).strftime("%-I:%M %p") if at else "",
            "acted": acted,
        })
    return out


def _change_key(entry: dict) -> str:
    """A stable id for one note, from its own content.

    Its position in the file would be simpler and is not safe: an entry
    inserted or reordered would shift every id after it and re-show notes the
    agent has already read. Editing a note's text does make it a new note,
    which is the honest reading — the agent never saw those words.
    """
    raw = f"{entry.get('date', '')}|{entry.get('message', '')}".encode()
    return hashlib.sha1(raw).hexdigest()[:12]


def _is_dated(entry: dict) -> bool:
    """Whether the date reads as a date. An unreadable one means a typo, which
    the startup log already names; showing it would put "- not-a-date:" in
    front of the agent."""
    try:
        datetime.date.fromisoformat(entry["date"])
        return True
    except (ValueError, KeyError, TypeError):
        return False


def _changes_seen() -> dict[str, int]:
    """How many passes have shown each note. Unreadable state counts as none
    seen, which re-shows a few notes rather than silently hiding them."""
    try:
        stored = json.loads(db.get_setting(_CHANGES_SEEN_KEY) or "{}")
    except ValueError:
        return {}
    return {k: int(v) for k, v in stored.items() if isinstance(k, str)} if isinstance(stored, dict) else {}


def mark_changes_seen(changes: list[dict]) -> None:
    """Count one pass against each note that was shown.

    **Called once per pass, not once per prompt.** A pass builds several
    prompts — a read, a refusal retry, another act-turn — and counting those
    would expire a note inside the pass that first showed it.

    Entries no longer in the file are pruned here, so the row cannot grow
    forever as notes are added over months.
    """
    if not changes:
        return
    live = {_change_key(e) for e in load_change_notes()}
    seen = {k: v for k, v in _changes_seen().items() if k in live}
    for entry in changes:
        key = _change_key(entry)
        seen[key] = seen.get(key, 0) + 1
    db.set_setting(_CHANGES_SEEN_KEY, json.dumps(seen))


def _recent_changes() -> list[dict]:
    """Notes the agent has not yet read enough times, oldest first.

    Oldest first is the order every other "recent history" list in the prompt
    uses. Capped at the newest few, so a day that produced seven notes does not
    hand the agent a changelog before it sees a price.
    """
    seen = _changes_seen()
    unread = []
    # **The pool is capped before the seen-filter, not after.** Filtering
    # first would rotate: once the newest few had been read the batch behind
    # them would surface, and an agent would work through every note ever
    # written. A note the newer ones have pushed out has been superseded.
    dated = [e for e in load_change_notes() if _is_dated(e)]
    for entry in dated[-_CHANGE_NOTES_SHOWN:]:
        if seen.get(_change_key(entry), 0) < _CHANGE_NOTES_PASSES:
            unread.append(entry)
    unread.sort(key=lambda e: e.get("date", ""))
    return unread


_EARNINGS_KEY = "earnings_due"
_ALERTS_SHOWN = 8


def store_earnings_dates(upcoming) -> None:
    """Record which tracked tickers report soon, for the next prompt to read.

    Written by the daily pre-market check rather than looked up while building
    a prompt, because the earnings calendar is one network request per ticker
    and a prompt must not carry that on its critical path.
    """
    payload = json.dumps([[t, str(d)] for t, d in (upcoming or [])])
    db.set_setting(_EARNINGS_KEY, payload)


def _earnings_due() -> list[tuple[str, str]]:
    """What the last pre-market check found, dropping anything already past."""
    try:
        stored = json.loads(db.get_setting(_EARNINGS_KEY) or "[]")
    except ValueError:
        return []
    today = str(datetime.date.today())
    return [(t, d) for t, d in stored if isinstance(t, str) and str(d) >= today]


def _recent_alerts() -> list[dict]:
    """What the watchdog saw **since the agent last looked**, newest first.

    **The agent could not see any of this until 2026-09-12.** The watchdog
    alerted on a sharp move and then commissioned an analysis itself, so the
    only trace that reached the agent was a signal it had not asked for. The
    tracked-ticker table shows "moved since the last analysis", which cannot
    tell a 5% fall this morning from a 5% drift over three weeks.

    **The window is the previous pass, and the first version had none.** It
    took the newest eight rows whatever their age, so a Saturday pass was shown
    Thursday's and Friday's moves under a heading that says "noticed" — and
    four probe runs against the live book ignored the section completely. A
    section that claims to say what has happened since you last looked has to
    mean it. With no previous pass to measure from, 24 hours is the fallback.
    """
    runs = db.get_agent_runs(limit=1)
    since = getattr(runs[0], "ran_at", None) if runs else None
    if since is None:
        since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=24)
    if since.tzinfo is None:
        since = since.replace(tzinfo=datetime.timezone.utc)
    out: list[dict] = []
    for row in db.get_recent_alerts(limit=_ALERTS_SHOWN * 4):
        raised = getattr(row, "created_at", None)
        if raised is not None:
            if raised.tzinfo is None:
                raised = raised.replace(tzinfo=datetime.timezone.utc)
            if raised < since:
                continue
        if len(out) >= _ALERTS_SHOWN:
            break
        at = getattr(row, "created_at", None)
        if at is not None and at.tzinfo is None:
            at = at.replace(tzinfo=datetime.timezone.utc)
        out.append({
            "at": market_clock.now_et(at).strftime("%-d %b %-I:%M %p") if at else "",
            "text": str(getattr(row, "message", "") or "").lstrip("📊🔔⚠️ ").strip(),
        })
    return out


def describe_watchdog(alerts: list[dict], earnings: list[tuple[str, str]]) -> list[str]:
    """What was noticed since the agent last looked, as facts rather than actions.

    Nothing here has been acted on. That is the point: the rule that a sharp
    move is analysed unasked is gone, because deciding a move is worth sixteen
    minutes and $0.05 was a decision being taken for the agent — and a sharp
    move is exactly when selling may beat studying.
    """
    lines: list[str] = []
    if alerts:
        lines += [
            "",
            "## What was noticed since your last pass",
            "",
            "**These happened while you were away.** Rules spotted them; nothing was "
            "done about any of them and nothing was analysed. Decide whether any is "
            "worth acting on, or worth paying to study.",
            "",
            "| When (ET) | What |",
            "|---|---|",
            *(f"| {a['at']} | {a['text']} |" for a in alerts),
        ]
    if earnings:
        lines += [
            "",
            "**Reporting earnings soon:** "
            + ", ".join(f"{t} on {d}" for t, d in earnings)
            + ". A report is the event most likely to move one of these sharply, and "
            "you can sell before it, study it, or hold through it.",
        ]
    return lines


def _last_planned_wakeup() -> "datetime.datetime | None":
    """The wakeup the previous pass planned, as an aware instant, or None.

    Stored as naive UTC, like every timestamp in the database.
    """
    runs = db.get_agent_runs(limit=1)
    wanted = getattr(runs[0], "next_wakeup", None) if runs else None
    if not isinstance(wanted, datetime.datetime):
        return None
    if wanted.tzinfo is None:
        wanted = wanted.replace(tzinfo=datetime.timezone.utc)
    return wanted


# A planned wakeup less than this far ahead is the pass it planned, not an
# early one. The alarm fires to the second, so this only absorbs clock drift.
_EARLY_WAKE_MARGIN = datetime.timedelta(minutes=1)


def _last_wakeup_note() -> str | None:
    """The note the previous pass left for this one, if it left one.

    Read from the newest run rather than matched to the alarm that fired: the
    agent may be woken early by something else, and the note it left is still
    what it was waiting for — arguably more useful then, because it explains
    what the early wake interrupted.
    """
    runs = db.get_agent_runs(limit=1)
    if not runs:
        return None
    note = str(getattr(runs[0], "wakeup_note", None) or "").strip()
    return note[:_WAKEUP_NOTE_MAX_CHARS] or None


def describe_wakeup(
    woke_because: str | None,
    note: str | None,
    has_news: bool = False,
    planned: "datetime.datetime | None" = None,
    now: "datetime.datetime | None" = None,
    asked_again: list[str] | None = None,
    pass_notes: list[str] | None = None,
) -> list[str]:
    """Why this pass is happening, and what the last pass left for this one.

    **Four different things could start a pass and the agent was told none of
    them** until 2026-09-12 — its own chosen time, a move it slept through, the
    last call before the close, a change to the app. They call for different
    answers and it could not tell them apart.

    The note is the previous pass writing to this one. Everything the agent
    worked out back then is otherwise gone: the prompt carries prices and
    positions, never conclusions.

    **An early pass says that it is early (2026-09-13).** When the wakeup the
    agent planned is still ahead, its note was written for that time, not for
    this pass. The prompt names the time. It also asks the agent to choose its
    wakeup and write its note again, because this pass replaces the planned
    alarm with whatever it answers. Before this, an early wake read as the
    planned one.

    **`pass_notes` is the same handoff within one pass (2026-09-17).** A pass
    that acts and is asked again is several turns of one call chain, and
    ``note`` above only ever carries the *previous pass's* note — stable all
    the way through, because this pass has not been recorded yet. A note a
    turn wrote for its own later turns had nowhere to land: seen live on
    2026-09-17, and see the JOURNEY.md entry the same day.
    """
    early = planned is not None and planned > market_clock.now_et(now) + _EARLY_WAKE_MARGIN
    if not woke_because and not note and not early and not asked_again and not pass_notes:
        return []
    when = (
        planned.astimezone(market_clock.US_MARKET_TZ).strftime("%A %-d %B at %-I:%M %p Eastern")
        if early else ""
    )
    lines = [""]
    if woke_because:
        # The pointer is added here and only here, because this is the only
        # place that knows the section is really in the prompt.
        pointer = ' See "What was noticed since your last pass" below.' if has_news else ""
        # On a later turn the wake is history, so it is named as that.
        heading = "Why this pass started" if asked_again else "Why you are awake"
        lines.append(f"**{heading}.** {woke_because}{pointer}")
    if asked_again:
        lines.append(
            "**Why you are asked again.** This is the same pass, not a new wake: "
            + "; and ".join(asked_again) + "."
        )
    if note and early:
        lines.append(f'**The note you left for your wakeup on {when}:** "{note}"')
        lines.append("That wakeup has not come yet. This pass is earlier than the one you planned.")
    elif note:
        lines.append(
            f'**A note you left yourself last pass:** "{note}"'
        )
    elif early:
        lines.append(f"**You planned to wake on {when}.** This pass is earlier than that.")
    if pass_notes:
        lines.append(
            "**Notes you wrote to yourself earlier in this same pass, oldest first "
            "— this is still the pass you are in, not a later one:**"
        )
        lines += [f'{i}. "{n}"' for i, n in enumerate(pass_notes, 1)]
        if len(pass_notes) > 1:
            lines.append(
                "Where two disagree, the later one is your more recent thinking."
            )
    if note or pass_notes:
        lines.append(
            "Those are your own words, not an instruction. The prices and "
            "positions below are current and the note is not — act on it only "
            "where it still holds."
        )
    if early:
        lost = " If you write no note, the note above is gone." if note else ""
        lines.append(
            f"**Choose your next wakeup again.** This pass replaces the wakeup you "
            f'planned for {when}. Give "next_wakeup" a time, the same one if it '
            'still suits you, and write a "next_wakeup_note" for that pass. If you '
            f"give no time, you are asked at the following open.{lost}"
        )
    return lines


def _recent_broker_failures() -> list[dict]:
    """Orders the broker refused on the last few passes.

    Read from the stored runs rather than held in memory, because the point is
    to carry a failure across days — the process that saw it has long exited.

    Bounded to the last few runs on purpose. A failure from last month is
    history rather than a warning: the unsettled cash that caused it settled
    weeks ago, and showing it would push today's signals further down the
    prompt for nothing.
    """
    out: list[dict] = []
    for row in db.get_agent_runs(limit=_FAILURE_LOOKBACK_RUNS):
        if not row.failures:
            continue
        try:
            parsed = json.loads(row.failures)
        except ValueError:
            continue
        if isinstance(parsed, list):
            out.extend(f for f in parsed if isinstance(f, dict))
    return out


def _decide(book, signals, prices, closed=None, regime_line=None, horizon_days=None,
            menu=None, outcomes=None, budget=None, changes=None, researched_now=None,
            woke_because=None, pass_notes=None):
    """(reasoning, accepted, rejected), with one correction pass.

    A refused order is information the model never sees otherwise: it proposed
    $1,944 of buys against $1,000 of cash on a live run, and simply dropping the
    overspend threw away whatever it was trying to express. Showing it the
    refusal and asking again lets it either resize or — the case this exists for
    — sell something to fund the buy it wanted.

    Only one retry. If the second answer is still unaffordable, the screened
    subset stands; a loop that keeps arguing with a small model would spend the
    market open doing it. The retry's answer replaces the first wholesale rather
    than merging, because nothing has been placed yet and two half-adopted plans
    are harder to reason about than one.
    """
    by_ticker = {s.ticker: s for s in signals}
    menu_tickers = {c.ticker for c in menu} if menu else None
    # Read once and passed to both attempts, so the retry describes the same
    # watchlist the first answer was screened against.
    watchlist = sorted(db.get_watchlist())
    # Orders the broker would not take on recent passes. Read once and given to
    # both attempts, so the retry sees the same history the first answer did.
    recent_failures = _recent_broker_failures()
    # Read once and shared with the retry too. The retry is the same pass, so
    # its cadence history has not changed.
    recent_wakeups = _recent_wakeups()
    # **Supplied by the caller, because a note is counted per pass and this
    # runs per turn.** Reading them here as well would make the same pass see
    # three different lists as its own turns expired them. None means a caller
    # with no pass around it — a test — and reading them is then right.
    recent_changes = _recent_changes() if changes is None else changes
    # What the rules noticed, and who reports soon. Read once and shared with
    # every turn of this pass, for the same reason as everything above.
    alerts = _recent_alerts()
    earnings = _earnings_due()
    # What the agent said it wanted this wakeup for, on the pass that set
    # it. Its own words, carried across a gap it cannot remember across.
    last_note = _last_wakeup_note()
    # The time that note was written for. When it is still ahead, this pass is
    # early and the prompt says so.
    planned_wakeup = _last_planned_wakeup()
    # What an analysis costs in time, and what is in flight. The agent needs
    # both to choose a wakeup that lands after the answer it is waiting for.
    analysis_minutes = analysis.recent_durations()
    running_analyses = analysis.in_flight()
    # How much of the cash is unsettled, from the broker rather than the app's
    # own budget arithmetic — the broker is what actually refuses a bracket
    # against it. Clamped to the agent's cash, because the sandbox pot can be
    # larger than the slice the app lets it spend and an unsettled figure above
    # its own balance would be nonsense.
    unsettled = min(_unsettled_cash(), book.cash) if book.cash > 0 else 0.0
    # Read once and shared with every turn of this pass, same reason as
    # everything above: a retry describes the same holdings the first answer
    # saw. One bar-cache read per holding, not per turn.
    price_ranges = {
        h.ticker: r for h in book.holdings
        if (r := price_range_since_purchase(h.ticker, h.opened, h.price)) is not None
    }
    all_tickers = {s.ticker for s in signals} | set(watchlist)
    day_ranges = {
        ticker: r for ticker in all_tickers
        if (r := day_range_today(ticker, prices.get(ticker))) is not None
    }
    # The exact prompt, kept so the Events page can show what was asked. A
    # retry replaces it, because the retry is the prompt the accepted orders
    # were actually screened from.
    shown = build_prompt(
        book, signals, prices, closed=closed, regime_line=regime_line,
        horizon_days=horizon_days, menu=menu, price=research.get_price(),
        watchlist=watchlist, max_watchlist=_max_watchlist(),
        failures=recent_failures, unsettled_cash=unsettled, wakeups=recent_wakeups,
        analysis_minutes=analysis_minutes, running_analyses=running_analyses,
        changes=recent_changes, outcomes=outcomes,
                             alerts=alerts, earnings=earnings,
                             researched_now=researched_now,
                             woke_because=woke_because, wakeup_note=last_note,
                             pass_notes=pass_notes,
                             planned_wakeup=planned_wakeup,
                             price_ranges=price_ranges,
                             day_ranges=day_ranges,
    )
    answer = _ask(shown)
    # Accumulated rather than taken from the last call: a retry is a second
    # real call to the model and its tokens are spent whether or not its
    # answer is the one used. getattr covers the fakes that return a plain
    # string — see _Answer.
    spend = _Spend.of(answer)
    turns = [_turn(shown, answer)]
    reasoning, proposed = parse_decision(answer)

    # **It may read as much as it needs, inside a bound.** A person deciding
    # whether to buy reads the research first, and often more than one piece of
    # it; rationing that to a single analysis was a restriction with no reason
    # behind it but the fear of a loop. The bound answers the loop directly —
    # a budget of analyses and a budget of turns — so the agent can ask again
    # after reading, and cannot ask forever.
    #
    # Reads no longer share the refusal retry's budget. They are different
    # things: one is the agent gathering what it needs to decide, the other is
    # Python telling it the decision it gave cannot be executed.
    # **One budget for the whole pass, not one per act-turn.** `budget` is a
    # dict the caller owns and this mutates, so an agent that acts and is asked
    # again cannot start reading from a full allowance each time — six reads a
    # pass would become eighteen across three turns.
    if budget is None:
        budget = {"reads": _MAX_READS_PER_PASS, "turns": _MAX_READ_TURNS}
    read_budget, turn_budget = budget["reads"], budget["turns"]
    wants, proposed = _split_reads(proposed)
    read_any = bool(wants)
    # Whatever rode along beside this read in the same answer. It is dropped
    # here — proposed is about to be replaced by the next answer's orders —
    # so it is shown once, in the very next prompt, or it is gone with no
    # trace the model could ever read. See the 2026-09-15 JOURNEY.md entry.
    dropped_with_read = proposed if wants else []
    while wants and read_budget > 0 and turn_budget > 0:
        taking = wants[:read_budget]
        read_budget -= len(taking)
        turn_budget -= 1
        readings = [analysis_reader.read(w.get("ticker"), w.get("date")) for w in taking]
        log.info(
            "Re-asking after reading %s (%d read(s) and %d turn(s) left)",
            ", ".join(str(w.get("ticker")) for w in taking), read_budget, turn_budget,
        )
        if dropped_with_read:
            log.info(
                "Not carried out, because it rode along with a read: %s",
                [_describe_order(o) for o in dropped_with_read],
            )
        shown = build_prompt(book, signals, prices, closed=closed,
                             regime_line=regime_line, horizon_days=horizon_days, menu=menu,
                             price=research.get_price(),
                             watchlist=watchlist, max_watchlist=_max_watchlist(),
                             failures=recent_failures, unsettled_cash=unsettled,
                             wakeups=recent_wakeups, analysis_minutes=analysis_minutes,
                             running_analyses=running_analyses, changes=recent_changes, outcomes=outcomes,
                             alerts=alerts, earnings=earnings,
                             researched_now=researched_now,
                             woke_because=woke_because, wakeup_note=last_note,
                             pass_notes=pass_notes,
                             planned_wakeup=planned_wakeup,
                             price_ranges=price_ranges,
                             day_ranges=day_ranges,
                             readings=readings,
                             dropped_with_read=dropped_with_read)
        answer = _ask(shown)
        spend = spend + _Spend.of(answer)
        turns.append(_turn(shown, answer))
        read_reasoning, proposed = parse_decision(answer)
        reasoning = read_reasoning or reasoning
        wants, proposed = _split_reads(proposed)
        dropped_with_read = proposed if wants else []
    budget["reads"], budget["turns"] = read_budget, turn_budget
    if wants:
        # Out of budget with more asked for. Dropped rather than answered:
        # this is the loop the bound exists to stop, and the pass still has a
        # decision to give.
        log.info("Read budget spent; ignoring %d further read(s)", len(wants))

    accepted, rejected = screen(proposed, book, prices, by_ticker, menu_tickers)
    # The retry keeps its own single turn, which reading no longer spends.
    if not rejected:
        return Decision(reasoning, accepted, rejected, shown, answer,
                        getattr(answer, "thinking", None), *spend, turns=turns)

    log.info("Re-asking after %d refused order(s): %s", len(rejected), [r.why for r in rejected])
    shown = build_prompt(book, signals, prices, rejected=rejected, closed=closed,
                         regime_line=regime_line, horizon_days=horizon_days, menu=menu,
                         price=research.get_price(),
                         watchlist=watchlist, max_watchlist=_max_watchlist(),
                         failures=recent_failures, unsettled_cash=unsettled,
                         wakeups=recent_wakeups, analysis_minutes=analysis_minutes,
                         running_analyses=running_analyses, changes=recent_changes, outcomes=outcomes,
                             alerts=alerts, earnings=earnings,
                             researched_now=researched_now,
                             woke_because=woke_because, wakeup_note=last_note,
                             pass_notes=pass_notes,
                             planned_wakeup=planned_wakeup,
                             price_ranges=price_ranges,
                             day_ranges=day_ranges)
    retry_answer = _ask(shown)
    spend = spend + _Spend.of(retry_answer)
    turns.append(_turn(shown, retry_answer))
    retry_reasoning, retry_proposed = parse_decision(retry_answer)
    # A read on the retry is dropped: the follow-up is already spent. The
    # prompt now says this before the retry is asked for (2026-09-15), so this
    # should be rare; logged rather than surfaced to the model, because there
    # is no further turn left in this pass to show it in.
    retry_wants, retry_proposed = _split_reads(retry_proposed)
    if retry_wants:
        log.info(
            "Read ignored on the refusal retry (no turn left this pass): %s",
            [w.get("ticker") for w in retry_wants],
        )
    if not retry_proposed:
        # A retry that proposes nothing is a decision to stand pat; keep the
        # first answer's accepted orders rather than discarding them.
        return Decision(reasoning, accepted, rejected, shown, retry_answer,
                        getattr(retry_answer, "thinking", None), *spend, turns=turns)
    retry_accepted, retry_rejected = screen(retry_proposed, book, prices, by_ticker, menu_tickers)
    return Decision(retry_reasoning or reasoning, retry_accepted, retry_rejected,
                    shown, retry_answer, getattr(retry_answer, "thinking", None), *spend,
                    turns=turns)




def _turn(prompt: str, answer) -> dict:
    """One turn of a pass, for the record.

    Kept verbatim, like `Decision.prompt` and `Decision.response`, because
    behaviour here is mostly prompt and a turn nobody wrote down cannot be
    read back later.

    **`reasoning` and `orders` added 2026-09-15**, so the events page can show
    what each turn itself said and asked for, not only the pass's final
    reasoning — which was always the *last* turn's, silently discarded for
    every turn before it once `_MAX_ACT_TURNS` grew past a couple of turns.
    Parsed with the same `parse_decision` the rest of the pipeline trusts,
    including its repairs for malformed JSON, rather than a second parser in
    the frontend that could read a turn differently than Python did. `orders`
    here is what this turn *asked for* — read, buy, whatever it said — not
    what was actually screened and executed; a read on a middle turn shows up
    honestly as a read, even though it never reaches `screen`.
    """
    reasoning, orders = parse_decision(answer)
    return {
        "prompt": str(prompt or ""),
        "response": str(answer or ""),
        "thinking": getattr(answer, "thinking", None),
        "reasoning": reasoning,
        "orders": [
            {
                "side": str(o.get("side", "")),
                "ticker": str(o.get("ticker", "")),
                "quantity": o.get("quantity") or 0,
                "reason": str(o.get("reason") or ""),
            }
            for o in orders
        ],
    }


def _split_reads(orders: list[dict]) -> tuple[list[dict], list[dict]]:
    """(reads, everything else).

    A read is not an order: it moves no cash, no shares and no watchlist slot,
    so it never reaches `screen`. It is pulled out here because it changes the
    control flow — it earns another turn — rather than the book.
    """
    reads, rest = [], []
    for order in orders or []:
        if str(order.get("side", "")).lower().strip() == "read":
            reads.append(order)
        else:
            rest.append(order)
    return reads, rest


def _describe_order(order: dict) -> str:
    """One order dict, in the model's own shape, as a line a person or the
    model can read back — used only for what was dropped alongside a read,
    where there is no Rejection object to format instead."""
    side = str(order.get("side", "")).upper()
    ticker = order.get("ticker", "")
    quantity = order.get("quantity")
    head = f"{side} {quantity:g} {ticker}" if quantity is not None else f"{side} {ticker}"
    extra = [
        f"{key} {order[key]:g}" for key in ("stop", "target") if order.get(key) is not None
    ]
    return f"{head} ({', '.join(extra)})" if extra else head


class _Spend(NamedTuple):
    """What the model calls in one pass cost, summed across the retry.

    A tuple so it unpacks straight into Decision's trailing fields, and named
    so the three numbers cannot be swapped by accident on the way there.
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0.0

    @classmethod
    def of(cls, answer) -> "_Spend":
        """Read the counts off one answer, tolerating a plain string — a dozen
        tests patch ``_ask`` with one, and telemetry must never be the reason a
        pass fails."""
        return cls(
            getattr(answer, "prompt_tokens", 0),
            getattr(answer, "completion_tokens", 0),
            getattr(answer, "seconds", 0.0),
        )

    def __add__(self, other: "_Spend") -> "_Spend":
        return _Spend(
            self.prompt_tokens + other.prompt_tokens,
            self.completion_tokens + other.completion_tokens,
            self.seconds + other.seconds,
        )


@dataclass
class Decision:
    """One decision pass: what was asked, what came back, and what survived.

    ``prompt`` and ``response`` are kept verbatim because the counts and the
    one-line reasoning describe a decision while these two *are* it. Behaviour
    here is mostly prompt, so a month of runs across three prompt revisions
    cannot be told apart afterwards without them.

    The three token/time fields are what those two cost, added up across the
    retry when there was one. Zero means nothing reported them rather than a
    free call — see ``_Answer``.
    """

    reasoning: str
    accepted: list[dict]
    rejected: list
    prompt: str = ""
    response: str = ""
    # The model's own reasoning behind that response. Replaced by a retry for
    # the same reason `response` is: the retry is the answer the accepted
    # orders were actually screened from.
    thinking: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0.0
    # Every turn of the pass, oldest first, as {"prompt", "response",
    # "thinking"}. `prompt` and `response` above stay the LAST turn, because
    # that is the one the accepted orders were screened from and every existing
    # reader expects it.
    #
    # **A pass has had more than one turn since retries existed, and only the
    # last was recorded.** The first prompt was overwritten, so a two-turn pass
    # was published as though it were one — against a site whose claim is that
    # every prompt the agent saw is on the record. Rare while a retry was the
    # only second turn; normal now that the agent can ask to read.
    turns: list[dict] = field(default_factory=list)

    def __iter__(self):
        """Unpack like the tuple this replaced, so existing callers and tests
        that write ``reasoning, accepted, rejected = _decide(...)`` keep
        working."""
        return iter((self.reasoning, self.accepted, self.rejected))


@dataclass
class AgentRun:
    """What one pass decided and what came of it."""

    reasoning: str = ""
    placed: list[dict] = field(default_factory=list)
    rejected: list[agent_book.Rejection] = field(default_factory=list)
    failed: list[tuple[dict, str]] = field(default_factory=list)
    # Exits moved to new levels. Kept apart from placed: no position was
    # opened or closed, but the risk on an open one changed, which is worth
    # reporting rather than folding into "no trades".
    adjusted: list[str] = field(default_factory=list)
    # Tickers it paid to have analysed. Not a trade — the position it may or
    # may not take is tomorrow's decision — but money left the account, so a
    # pass that only researched is not an idle pass.
    researched: list[str] = field(default_factory=list)
    # Of ``researched``, the ones it asked to see today rather than tomorrow.
    # The pass itself cannot run them: run_once is synchronous and an analysis
    # is minutes of async work, so this is the handoff to the scheduler, which
    # dispatches them and then asks the agent again when they land.
    research_now: list[str] = field(default_factory=list)
    # Every turn of the pass, oldest first. `prompt`/`response` stay the last
    # turn — see Decision.turns for why that split is deliberate.
    turns: list[dict] = field(default_factory=list)
    # When the agent asked to be woken, and when it actually will be. They
    # differ when it aimed outside market hours. None means it asked for
    # nothing — the scheduler falls back, and the agent is not credited with
    # having chosen the fallback.
    wakeup_asked: "datetime.datetime | None" = None
    next_wakeup: "datetime.datetime | None" = None
    # What it said it wanted that wakeup *for*, in its own words, shown back to
    # it on the pass the wakeup starts. The agent has no memory between passes.
    wakeup_note: "str | None" = None
    # Tickers it stopped watching. No money moves either way, but tomorrow's
    # sweep is smaller for it, so this is a decision and not housekeeping.
    untracked: list[str] = field(default_factory=list)
    # Positions this pass left with no resting stop or target: a fill with no
    # usable level, a buy whose bracket the broker refused, or a failed sell
    # that could not get its exits back. Until 2026-09-16 this was a DB alert
    # and a Discord line and nothing else — the agent could go up to four days
    # not knowing one of its own positions had nothing under it. Scheduler
    # reads this after the pass to wake the agent early; see
    # _run_agent_pass_locked.
    unguarded: list[str] = field(default_factory=list)
    # What the agent asked us for: a tool it lacks, data it cannot see, a rule
    # it finds contradictory. Nothing reads these automatically and nothing
    # acts on them — they are evidence about the prompt and the tool set, which
    # is the thing this experiment exists to produce.
    #
    # Kept out of ``acted``: a pass that only left a note did not act, and
    # counting it as action would let "I need better data" stand in for a
    # decision the agent still owed.
    notes: list[str] = field(default_factory=list)
    book: agent_book.Book | None = None
    skipped: str | None = None  # why the run did nothing at all
    # The words, kept for the Events page. Empty on a pass that never asked —
    # a skipped run, or one the market was shut for.
    prompt: str = ""
    response: str = ""
    # The model's own reasoning. Most of what it generates and all of why —
    # see _invoke. None on a pass that never asked, and on any provider whose
    # client does not return it.
    thinking: str | None = None
    # What those words cost: the provider's own counts and the wall clock of
    # the model calls, summed across a retry. Zero on a pass that never asked,
    # and on one whose endpoint reported no usage — stored as NULL rather than
    # 0 for exactly that reason, the same rule Signal already follows.
    prompt_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0.0

    @property
    def acted(self) -> bool:
        return bool(self.placed or self.adjusted or self.researched or self.untracked)


def format_run_embed(run: AgentRun) -> "Embed":
    """What the agent did, for Discord. Rejections are shown, not hidden — a
    decision the model made that could not be executed is the most useful
    thing on the page when the budget is the binding constraint."""
    if run.skipped:
        return Embed(
            title="The agent — skipped", description=run.skipped, color=Color.greyple()
        )

    book = run.book
    color = Color.blue() if run.acted else Color.greyple()
    embed = Embed(
        title="The agent" + (" — traded" if run.acted else " — no trades"),
        description=run.reasoning[:_DESCRIPTION_MAX] or "(no reasoning given)",
        color=color,
        timestamp=datetime.datetime.now(datetime.timezone.utc),
    )

    if book is not None:
        embed.add_field(
            name="Book",
            value=(
                f"Equity **${book.equity:,.2f}** ({book.return_pct:+.1f}% vs "
                f"${book.budget:,.0f} budget)\n"
                f"Cash ${book.cash:,.2f} · invested ${book.invested:,.2f} · "
                f"realized ${book.realized_pnl:+,.2f}"
            ),
            inline=False,
        )

    if run.placed:
        embed.add_field(
            name="Placed",
            value="\n".join(
                f"{o['side'].upper()} {o['quantity']:g} {o['ticker']}"
                + (f" — {o['reason']}" if o.get("reason") else "")
                for o in run.placed
            )[:_FIELD_MAX],
            inline=False,
        )
    if run.adjusted:
        embed.add_field(
            name="Exits moved",
            value="\n".join(run.adjusted)[:_FIELD_MAX],
            inline=False,
        )
    if run.notes:
        # High in the embed rather than at the bottom. A note is the agent
        # telling us it is short of something, which is worth reading before
        # the list of what it managed to do anyway.
        embed.add_field(
            name="📝 The agent asked for something",
            value="\n".join(run.notes)[:_FIELD_MAX],
            inline=False,
        )
    if run.rejected:
        embed.add_field(
            name="Rejected",
            value="\n".join(
                f"{r.side.upper()} {r.quantity:g} {r.ticker} — {r.why}" for r in run.rejected
            )[:_FIELD_MAX],
            inline=False,
        )
    if run.failed:
        embed.add_field(
            name="Failed at the broker",
            value="\n".join(
                f"{o['side'].upper()} {o['quantity']:g} {o['ticker']} — {why}"
                for o, why in run.failed
            )[:_FIELD_MAX],
            inline=False,
        )

    embed.set_footer(text="Simulated account — no real money")
    return embed


# How many tickers may be tracked at once.
#
# **This number was derived from morning_sweep's throughput, and that sweep
# is gone (2026-09-08) — the derivation below is history, not the current
# reason for 30.** There was also a daily research cap meant to bound how
# fast the list could grow; that is gone too, since cash already bounds it
# and the pacing problem it solved (fitting a bulk sweep into a fixed
# pre-open window) no longer exists. What 30 still does is bound the size of
# the per-pass watchlist listing itself — a genuinely different, much
# looser constraint than the one that originally produced this number, and
# nobody has yet asked what the right number is for that job specifically.
# Treat 30 as inherited, not chosen, until someone does.
#
# The original derivation, kept for the record: morning_sweep ran at 11:00
# UTC and earnings_check put its own analyses on the same pool at 13:00, so
# there were two hours. Measured 2026-09-02, fourteen tickers at seven
# concurrent, twice: 42.5 and 43.4 minutes of wall clock, 28 of 28
# succeeding — about 3.05 minutes of throughput per analysis, fitting
# roughly 39 in the window. Thirty left a quarter of it spare. The previous
# value was 12, derived from three concurrent analyses at 17.4 minutes each
# — the figures for a pool shared with a second deployment that ended on
# 2026-09-01. See JOURNEY.md, 2026-09-02.
_MAX_WATCHLIST = 30


def _max_watchlist() -> int:
    return _MAX_WATCHLIST


# How many screened names to show. Enough to choose from, few enough that the
# list does not crowd out the holdings and signals above it — and every extra
# line is prompt tokens on every pass, whether or not anything is researched.
_MENU_SIZE = 15


def _candidate_menu() -> list:
    """Screened names the agent may pay to have analysed.

    Never free-form. A model naming its own tickers invents symbols, reaches
    illiquid things with no price data, and picks the day's pump — a raw screen
    once returned a stock up 927%, which the price floor alone does not catch
    because the pump is what lifted the price over the floor. candidates.py
    already filters for liquidity and excludes anything up more than 30%.
    """
    try:
        found = candidates.fetch_candidates()
    except Exception:
        log.exception("Could not screen for candidates — the agent decides without a menu")
        return []
    return found[:_MENU_SIZE]


def _place(
    order: dict,
    price: float | None,
    stops: dict[str, float],
    targets: dict[str, float],
    run: "AgentRun | None" = None,
) -> dict:
    """Place one accepted order, as a bracket where that is possible.

    A bracket submits the buy and its exits together and the broker activates
    the exits itself the moment the entry fills, so there is no window in which
    the shares are owned and nothing is protecting them. Arming afterwards
    always had that window, however short the wait for the fill.

    It is not always possible, and the fallback is not a formality:

    - a sell is never bracketed — it *is* the exit;
    - a buy with no usable level has nothing to bracket with;
    - and a combo is refused outright while the cash is unsettled, which is
      routine here, because selling to fund a buy in the same pass is
      something the agent is explicitly told it can do.

    So a refused bracket falls back to the plain market order rather than
    failing the trade. The position is then armed the slower way, which is the
    behaviour this replaced and is still correct — just briefly exposed.

    **A limit order never brackets.** It may sit unfilled for a session or,
    under GTC, for days — the broker will not hold a combo's exit legs
    inactive that long against a master that hasn't filled. A limit buy goes
    out alone; the signal's stop/target, if any, are left unplaced, and
    ``_describe_fill`` tells the agent to resend them with ``adjust`` once
    the fill is confirmed, rather than this function guessing when that will
    be.
    """
    ticker = order["ticker"]
    if order["side"] != "buy":
        # Not a plain market order: the resting exits have to be cleared before
        # the broker will accept the sell, and put back if it fails.
        return _sell_and_restore_on_failure(order, run=run)

    if str(order.get("order_type") or "").lower().strip() == "limit":
        return sandbox_broker.place_limit_order(
            ticker, "BUY", order["quantity"], float(order["limit_price"]),
            str(order.get("time_in_force") or "day").upper(),
        )

    stop, target = usable_levels(ticker, stops.get(ticker), targets.get(ticker), price)
    if stop is None and price:
        stop = atr_stop(ticker, price)
    if price and (stop or target):
        try:
            return sandbox_broker.place_bracket_order(
                ticker, order["quantity"], price, stop, target
            )
        except Exception as exc:
            log.warning(
                "Bracket refused for %s (%s) — buying at market and arming separately",
                ticker, str(exc)[:200],
            )

    return sandbox_broker.place_market_order(ticker, "BUY", order["quantity"])


def _record_unguarded(
    ticker: str, quantity: float, why: str, run: "AgentRun | None" = None
) -> None:
    """Write down that a position was opened with nothing protecting it.

    Until this existed the failure was completely silent: no ledger row, no
    alert, no Discord line. Two positions sat unguarded for days on
    2026-08-20 and the only reason anyone found out was a person noticing the
    broker screen — by then the run's logs had been erased with the container,
    so *why* the exits never rested had to be reconstructed from prices.

    Recorded as an alert rather than a trade, because nothing was traded. The
    watchdog's alert table is already the place the dashboard reads for things
    that need a person.

    **Also recorded on the pass itself, when one is running (2026-09-16).**
    The alert used to be the only trace, which meant a person watching
    Discord was the sole way this was ever noticed — the agent that just
    caused it never learned, and neither did any later pass, since nothing
    woke it early. See `run.unguarded` and `scheduler._run_agent_pass_locked`.
    """
    log.error("%s is unguarded: %s", ticker, why)
    try:
        db.record_alert(
            ticker=ticker,
            alert_type="unguarded_position",
            # Once per ticker per day. The same position stays unguarded until
            # someone acts on it, and re-announcing it every pass would bury
            # the alert that is still new.
            dedupe_key=f"unguarded:{ticker}:{datetime.date.today().isoformat()}",
            message=f"{quantity:g} share(s) of {ticker} have no resting exit — {why}",
        )
    except Exception:
        # An unrecordable alert must not undo a filled buy.
        log.exception("Couldn't record the unguarded-position alert for %s", ticker)
    if run is not None:
        run.unguarded.append(f"{ticker}: {why}")


def atr_stop(ticker: str, price: float) -> float | None:
    """A stop derived from the stock's own volatility, for a buy whose stated
    stop cannot be used.

    Every position needs an exit, and the signal's stop is missing more often
    than it looks. ``_resolve_stop_loss`` already substitutes this at
    signal-record time — but only for Buy and Overweight, and the agent buys on
    Hold signals too. A Hold therefore carries whatever the trader stated and
    no safety net, and days can pass between the signal and the purchase.

    That gap is what went wrong on 2026-08-18 and 2026-08-19. NOK was bought at
    $10.47 against a $10.56 stop and INTC at $91.84 against a $94.00 stop —
    both stocks had fallen through their own stop in the meantime, so the level
    was discarded as unusable and the position opened with nothing under it.

    Derived from the price at purchase rather than at the signal, and 2×ATR
    below it by construction, so it cannot come back on the wrong side.
    """
    atr = get_atr(ticker)
    if atr is None:
        log.warning("No ATR for %s — cannot derive a stop", ticker)
        return None
    suggestion = suggest_position(price, atr)
    if suggestion is None or suggestion.stop is None or suggestion.stop >= price:
        return None
    log.info(
        "Using an ATR-derived stop of %.2f for %s at %.2f — the stated stop was unusable",
        suggestion.stop, ticker, price,
    )
    return suggestion.stop


def _record_exits(ticker: str, exits: list[dict]) -> None:
    """Ledger rows for exits the broker is already holding.

    Separate from ``_arm_exits`` because there is nothing to arm: these legs
    were submitted with the buy and the broker activates them on the fill. All
    that is left is to write down what is resting, so the dashboard and the
    settlement pass can see it.
    """
    for leg in exits:
        label = "stop-loss" if leg["kind"] == "stop" else "take-profit"
        db.record_agent_trade(
            ticker=ticker,
            side="sell",
            quantity=leg.get("quantity") or 0,
            client_order_id=leg["client_order_id"],
            placed_at=leg["placed_at"],
            reason=f"{label} resting at ${leg['price']:,.2f}",
            signal_id=None,
            is_stop=True,
            limit_price=leg["price"],
            exit_kind=leg["kind"],
        )
    log.info(
        "Bracketed %s with %s",
        ticker, ", ".join(f"{l['kind']} {l['price']:,.2f}" for l in exits),
    )


def _cancel_resting_exits(ticker: str) -> list[dict]:
    """Cancel every resting exit on a ticker, and report what was cancelled.

    Two reasons to cancel. An exit left behind after a position closes is a
    live order to sell shares nobody owns, which the broker rejects later or
    fills into a short. And the broker will not accept the closing sell at all
    while those exits rest — see ``_sell_and_restore_on_failure``.

    **Returns the levels, not a count.** A sell that fails has to put the exits
    back exactly as they were, and the resting orders are the only record of
    where they were: the agent moves its stops and targets during the day, so
    the original signal's levels are stale.
    """
    cancelled: list[dict] = []
    for trade in db.get_pending_agent_trades():
        if not trade.is_stop or trade.ticker != ticker:
            continue
        if sandbox_broker.cancel_order(trade.client_order_id):
            db.settle_agent_trade(trade.client_order_id, status="rejected")
            cancelled.append({
                "kind": trade.exit_kind,
                "price": trade.limit_price,
                "quantity": trade.quantity,
                "client_order_id": trade.client_order_id,
            })
    if cancelled:
        log.info("Cancelled %d resting exit(s) on %s", len(cancelled), ticker)
    return cancelled


# How long to wait for a cancelled exit to actually clear before selling into
# the freed position, and how often to ask. Mirrors _FILL_WAIT_SECONDS below:
# cancel_order returns as soon as the broker accepts the request, not once the
# cancel has taken effect, and the confirmation arrives later, over the trade
# stream. A sell submitted in that gap still sees the old exit as resting and
# is refused as OPENAPI_ORDER_NOT_SUPPORT_REVERSE_OPTION (AVGO, 2026-09-07 and
# 2026-09-08, both recovered by _restore_resting_exits but neither sold).
_CANCEL_WAIT_SECONDS = 20
_CANCEL_POLL_SECONDS = 2


def _await_cancels(client_order_ids: list[str]) -> None:
    """Block until every one of these exits shows cancelled at the broker, or
    give up.

    Asking once and hoping is what produced the AVGO failures: the broker
    answers "accepted" to the cancel immediately and confirms it later, so a
    sell placed right after asking can still land in the gap. Giving up here
    is not a failure — it only means the sell that follows might still be
    refused, and if it is, the normal restore-on-failure path in
    ``_sell_and_restore_on_failure`` puts the exits straight back. So this
    trades a little time for a much smaller chance of that round trip, rather
    than promising it never happens.
    """
    pending = {cid for cid in client_order_ids if cid}
    if not pending:
        return
    deadline = time.monotonic() + _CANCEL_WAIT_SECONDS
    while pending and time.monotonic() < deadline:
        for client_order_id in list(pending):
            detail = sandbox_broker.get_order_detail(client_order_id)
            status = str((detail or {}).get("status") or "").upper()
            if status in ("CANCELLED", "REJECTED", "FAILED", "EXPIRED"):
                pending.discard(client_order_id)
        if pending:
            time.sleep(_CANCEL_POLL_SECONDS)
    if pending:
        log.warning(
            "%d cancelled exit(s) had not confirmed after %ss — selling anyway",
            len(pending), _CANCEL_WAIT_SECONDS,
        )


def _clear_before_arming(ticker: str) -> None:
    """Cancel and await whatever is already resting on a ticker, before a
    fresh stop/target is placed under it.

    Shared by ``_arm_exits`` and ``adjust_exits``'s arm-new branch, both of
    which used to place a brand-new exit pair without checking first. That is
    what actually jammed AVGO on 2026-09-17: a bracket refused a second time
    on unsettled cash fell back to arming a second stop and target on top of
    the first, so the broker saw more shares committed to sell than the
    account owned and refused every later ``adjust`` with
    ``OPENAPI_ORDER_NOT_SUPPORT_REVERSE_OPTION`` — identically, three times,
    until an unrelated sell elsewhere in the pass cancelled the stale order as
    a side effect. The sell path already cancelled first
    (``_sell_and_restore_on_failure``); this gives arming the same guarantee.

    A no-op when nothing is resting — ``_cancel_resting_exits`` returns an
    empty list immediately, so a genuinely new position pays no extra wait.
    """
    cancelled = _cancel_resting_exits(ticker)
    _await_cancels([c.get("client_order_id") for c in cancelled])


def _sell_and_restore_on_failure(order: dict, run: "AgentRun | None" = None) -> dict:
    """Sell a position, clearing its resting exits first.

    **The broker refuses a sell while exits rest on the position.** A bracketed
    buy leaves a stop and a target, each for the full quantity, so a position of
    four shares already has eight shares of sells against it. A third sell reads
    as going short and returns
    ``OPENAPI_ORDER_NOT_SUPPORT_REVERSE_OPTION``.

    Until 2026-09-05 the cancel ran *after* the sell, so it never ran at all —
    the sell raised first and the loop skipped past it. A bracketed position
    could only close through its own stop or target, and the agent could not
    choose to leave one.

    **The cancel is awaited, not merely sent** — see ``_await_cancels``. Until
    2026-09-08 the code moved straight to the sell, and the broker's own delay
    in confirming the cancel meant AVGO's sell was refused two days running.

    **If the sell fails, the exits go back.** Between the cancel and the fill
    the shares have nothing under them, and leaving them that way would replace
    one defect with a worse one: a naked position that nothing reports.

    **A limit sell needs the same cancel-first treatment as a market one.**
    The broker refuses *any* new sell while something rests on the position —
    the order type asked for doesn't change that.
    """
    cancelled = _cancel_resting_exits(order["ticker"])
    _await_cancels([c.get("client_order_id") for c in cancelled])
    try:
        if str(order.get("order_type") or "").lower().strip() == "limit":
            return sandbox_broker.place_limit_order(
                order["ticker"], order["side"].upper(), order["quantity"],
                float(order["limit_price"]),
                str(order.get("time_in_force") or "day").upper(),
            )
        return sandbox_broker.place_market_order(
            order["ticker"], order["side"].upper(), order["quantity"]
        )
    except Exception:
        if cancelled:
            _restore_resting_exits(order["ticker"], cancelled, run=run)
        raise


def _restore_resting_exits(
    ticker: str, cancelled: list[dict], run: "AgentRun | None" = None
) -> None:
    """Put back the exits a failed sell had cleared.

    Best-effort, and loud when it fails. The shares are held either way, so
    raising here would only replace a reported failure with an unreported one.
    """
    stop = next((c["price"] for c in cancelled if c["kind"] == "stop"), None)
    target = next((c["price"] for c in cancelled if c["kind"] == "target"), None)
    quantity = max(c["quantity"] for c in cancelled)
    try:
        legs = sandbox_broker.place_exit_bracket(ticker, quantity, stop, target)
    except Exception as exc:
        log.exception("Could not restore the exits on %s after a failed sell", ticker)
        _record_unguarded(
            ticker,
            quantity,
            f"a failed sell cleared them and they would not go back: {exc}",
            run=run,
        )
        return
    for leg in legs:
        label = "stop-loss" if leg["kind"] == "stop" else "take-profit"
        db.record_agent_trade(
            ticker=ticker,
            side="sell",
            quantity=quantity,
            client_order_id=leg["client_order_id"],
            placed_at=leg["placed_at"],
            reason=f"{label} restored at ${leg['price']:,.2f} after a failed sell",
            signal_id=None,
            is_stop=True,
            limit_price=leg["price"],
            exit_kind="stop" if leg["kind"] == "stop" else "target",
        )
    log.info("Restored %d exit(s) on %s after a failed sell", len(legs), ticker)


def usable_levels(
    ticker: str, stop_price: float | None, target_price: float | None, price: float | None
) -> tuple[float | None, float | None]:
    """Drop the exit levels that would execute the instant they exist.

    A limit sell below the market fills at market, and a stop above it triggers
    at once. Either liquidates the position the moment it is opened — and the
    take-profit would be announced as a profit while booking a loss. Not
    hypothetical: a live signal produced a $95.96 target on a stock trading at
    $97.57.

    An unknown price is not evidence against a level, so nothing is dropped
    when the quote is missing.
    """
    if price is None:
        return stop_price, target_price
    if stop_price is not None and stop_price >= price:
        log.warning(
            "Refusing a stop at %.2f on %s trading at %.2f — it would trigger at once",
            stop_price, ticker, price,
        )
        stop_price = None
    if target_price is not None and target_price <= price:
        log.warning(
            "Refusing a target at %.2f on %s trading at %.2f — it would fill at once",
            target_price, ticker, price,
        )
        target_price = None
    return stop_price, target_price


# How long to wait for a buy to fill before arming its exits, and how often to
# ask. A market order in session hours fills in well under a second; this is
# generous enough to cover a slow one without holding the decision pass open.
_FILL_WAIT_SECONDS = 20
_FILL_POLL_SECONDS = 2


def _await_fill(client_order_id: str) -> bool:
    """Block until the buy has actually filled, or give up.

    Exits cannot be placed before the shares exist. A cash account counts every
    resting sell against the position it can see, so a stop and a take-profit
    for three shares each, placed while the buy is still submitted, read as six
    shares sold against nothing — the broker refuses the pair outright with
    GENERATE_NEW_SHORT_POSITION. That is exactly what happened on 2026-08-13:
    both buys filled and neither got its exits, because arming ran milliseconds
    after the order was sent rather than after it was done.
    """
    deadline = time.monotonic() + _FILL_WAIT_SECONDS
    while time.monotonic() < deadline:
        detail = sandbox_broker.get_order_detail(client_order_id)
        status = str((detail or {}).get("status") or "").upper()
        if status in ("FILLED", "PARTIAL_FILLED"):
            return True
        if status in ("CANCELLED", "REJECTED", "FAILED", "EXPIRED"):
            return False
        time.sleep(_FILL_POLL_SECONDS)
    return False


def _arm_exits(
    order: dict,
    stop_price: float | None,
    target_price: float | None,
    client_order_id: str | None = None,
    run: "AgentRun | None" = None,
) -> str | None:
    """Rest the exits under a position the agent just opened: a stop where the
    thesis is wrong, a take-profit where it has played out.

    Both come from the analysis, and both are optional — the trader states them
    only when it has a view, and an implausible level was already discarded
    when the signal was recorded. Whichever exists is placed; inventing the
    missing one would be inventing the exit price of a real trade.

    Best-effort on purpose: a failed exit must not undo a filled buy, because
    the shares are owned either way and raising here would leave the ledger
    disagreeing with the account. It is logged loudly instead — a position
    running naked is worth knowing about.

    Returns the unguarded-position message when arming failed, or None on
    success, so a caller mid-pass (2026-09-16) can put it in front of the
    agent in the same breath rather than only in the alert log.
    """
    price = get_current_price(order["ticker"])
    stop_price, target_price = usable_levels(order["ticker"], stop_price, target_price, price)

    if not stop_price and not target_price:
        why = "the analysis gave no usable stop or target"
        _record_unguarded(order["ticker"], order["quantity"], why, run=run)
        return f"{order['ticker']}: {why}"

    # The shares have to exist before anything can rest against them.
    if client_order_id and not _await_fill(client_order_id):
        why = (
            f"the buy had not filled after {_FILL_WAIT_SECONDS}s, so exits could not be placed "
            "against shares that may not exist"
        )
        _record_unguarded(order["ticker"], order["quantity"], why, run=run)
        return f"{order['ticker']}: {why}"
    _clear_before_arming(order["ticker"])
    try:
        legs = sandbox_broker.place_exit_bracket(
            order["ticker"], order["quantity"], stop_price, target_price
        )
    except Exception as exc:
        log.exception("Couldn't arm exits for %s", order["ticker"])
        why = f"the broker refused them: {exc}"
        _record_unguarded(order["ticker"], order["quantity"], why, run=run)
        return f"{order['ticker']}: {why}"
    for leg in legs:
        label = "stop-loss" if leg["kind"] == "stop" else "take-profit"
        db.record_agent_trade(
            ticker=order["ticker"],
            side="sell",
            quantity=order["quantity"],
            client_order_id=leg["client_order_id"],
            placed_at=leg["placed_at"],
            reason=f"{label} resting at ${leg['price']:,.2f}",
            signal_id=None,
            is_stop=True,
            limit_price=leg["price"],
            exit_kind="stop" if leg["kind"] == "stop" else "target",
        )
    log.info(
        "Armed %d exit(s) for %s: %s",
        len(legs), order["ticker"], ", ".join(l["kind"] for l in legs),
    )


def settle_pending() -> list[dict]:
    """Ask the broker about every order still awaiting a fill and apply the
    answer. Returns what changed, so a caller can announce it.

    Resting stops are included deliberately. A stop that triggers is a sale
    the app did not initiate, and it is the one fill nobody is waiting for —
    so if this only ran when the agent next decided, the book would show a
    position that had already been sold, sometimes for a whole day.
    """
    settled: list[dict] = []
    for trade in db.get_pending_agent_trades():
        detail = sandbox_broker.get_order_detail(trade.client_order_id)
        if not detail:
            continue
        # Field names verified against a live sandbox fill: status, filled_price,
        # filled_quantity. order_status/avg_fill_price appear in other Webull
        # payloads and are kept as fallbacks, not guesses to rely on.
        status = str(detail.get("status") or detail.get("order_status") or "").upper()
        filled_qty = _as_float(detail.get("filled_quantity"))
        price = _as_float(detail.get("filled_price") or detail.get("avg_fill_price"))
        if status in ("FILLED", "PARTIAL_FILLED") and price and filled_qty:
            db.settle_agent_trade(
                trade.client_order_id,
                status="filled",
                price=price,
                # What actually filled, not what was asked for. A partial fill
                # recorded at the requested size would put shares in the ledger
                # that the account does not hold.
                quantity=filled_qty,
                filled_at=datetime.datetime.now(datetime.timezone.utc),
                broker_order_id=str(detail.get("order_id") or "") or None,
            )
            settled.append({
                "ticker": trade.ticker,
                "side": trade.side,
                "quantity": filled_qty,
                "price": price,
                "was_stop": trade.is_stop,
                "reason": trade.reason,
                "status": "filled",
                # Set only on a not-yet-filled *entry* order placed as a
                # limit — a resting protective exit also carries limit_price,
                # which is why "was_stop" has to be checked too before this
                # means "a limit buy just filled and needs its exits."
                "limit_price": trade.limit_price,
            })
        elif status in ("CANCELLED", "REJECTED", "FAILED", "EXPIRED"):
            db.settle_agent_trade(trade.client_order_id, status="rejected")
            settled.append({
                "ticker": trade.ticker,
                "side": trade.side,
                "quantity": trade.quantity,
                "price": None,
                "was_stop": trade.is_stop,
                "status": "rejected",
                "limit_price": trade.limit_price,
            })
    return settled


def _as_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# **How a synchronous pass runs an analysis and waits for it.** `run_once` is
# sync and runs on a worker thread; an analysis is async work belonging to the
# main event loop. The scheduler owns that knowledge and installs the bridge at
# startup, so this module stays synchronous and testable, and a deployment with
# no scheduler (a script, a test) simply has no runner and says so.
_research_runner = None


def set_research_runner(runner) -> None:
    """Install the callable that runs analyses and blocks until they land.

    Takes a list of tickers and returns nothing; raising is fine, and the
    caller reports the failure to the model rather than losing the pass.
    """
    global _research_runner
    _research_runner = runner


def _research_and_report(tickers: list[str]) -> tuple[list[str], dict[str, str]]:
    """Run the analyses the agent just commissioned, and hand back what they said.

    **This is the point of chaining.** The agent asked to have something looked
    at because it could not decide without it; ending the pass there and asking
    again later means the answer arrives with the reasoning that wanted it
    already gone. It now waits, sees the verdict and the analyst's own case for
    it, and decides in the same breath.

    Each line carries the same trimmed rationale the `read` tool returns, so
    the agent does not have to spend a turn reading what it just paid for.

    **Also returns which of `tickers` never produced a usable analysis
    (2026-09-16).** Until then this was computed here and thrown away outside
    of the lines above — the order record kept only `research TICKER` with a
    blank reason, so the Decisions page could not tell a research that
    finished from one that silently did not. The caller moves a failed ticker
    from `run.researched` into `run.failed`, the same bucket a broker failure
    already lands in.
    """
    if _research_runner is None:
        log.warning("No research runner installed; %s will not run in this pass", tickers)
        why = "nothing is set up to run it here"
        return (
            [f"{t}: could not be analysed in this pass — {why}." for t in tickers],
            {t: why for t in tickers},
        )
    failed: dict[str, str] = {}
    # **Research that cannot finish inside today's model requests is refused
    # here, before anything runs or is charged (2026-09-13).** An analysis makes
    # about twenty model calls. On a key with a daily cap, an analysis that
    # starts with twelve requests left fails halfway, after it spends them, and
    # run_analyses reports no reason back. This is also the one place the
    # reason can reach the agent.
    refused: list[str] = []
    left = llm_throttle.requests_left_today()
    if left is not None:
        fits = left // llm_throttle.REQUESTS_PER_ANALYSIS
        if fits < len(tickers):
            why = llm_throttle.describe_daily_shortfall(left)
            for t in tickers[fits:]:
                refused.append(f"{t}: not analysed, and nothing was charged. {why}")
                failed[t] = why
            log.info("Not enough model requests left today for %s", ", ".join(tickers[fits:]))
            tickers = tickers[:fits]
    if not tickers:
        return refused, failed
    log.info("Running %s and waiting for the result, in the same pass", ", ".join(tickers))
    try:
        # What stopped each analysis that did not finish. A runner that
        # reports nothing — a test double — reports no failures.
        run_failures = _research_runner(list(tickers)) or {}
    except Exception as exc:
        log.exception("In-pass research failed for %s", tickers)
        why = str(exc)
        for t in tickers:
            refused.append(f"{t}: the analysis did not finish — {exc}")
            failed[t] = why
        return refused, failed
    charge = research.get_price()
    lines = []
    for t in tickers:
        if t in run_failures:
            # **Never "it has finished" for an analysis that did not.** Until
            # 2026-09-13 every commissioned ticker got that line, and the
            # reasoning under it was the older analysis the table still held.
            failed[t] = run_failures[t]
            lines.append(
                f"**{t}: the analysis you ordered did not finish.** {run_failures[t]} "
                f"Any row for {t} in the signals table is from an earlier analysis."
            )
            continue
        lines.append(
            f"**{t}: the analysis YOU ordered minutes ago"
            + (f", and paid ${charge:,.2f} for" if charge else "")
            + ".** It has finished, its verdict and levels are the row marked "
            f"as yours in the signals table, and this is the reasoning behind "
            f"it:\n{analysis_reader.read(t)}"
        )
    return refused + lines, failed


def _execute_orders(accepted, run, prices, stops, targets, signal_by_ticker, researched=None) -> list[str]:
    """Carry out what survived screening, and say what happened to each order.

    **The returned lines go back to the model in the same pass.** Until
    2026-09-12 an order left the pass and its result arrived, if at all, in
    whatever pass came next — so the agent placed a buy and learned whether it
    filled hours later, against a book it could no longer remember deciding on.
    A person placing an order watches it fill. See the 2026-09-12 entry in
    JOURNEY.md.

    A note produces no line. It is a message to the people who maintain this
    app, not an action with a result, and reporting it back to the model would
    only invite it to answer itself.

    Research produces one line per ticker and runs last, together, because
    ``analysis.run_analyses`` dispatches a batch across the GPUs and one at a
    time would serialise them.
    """
    outcomes: list[str] = []
    research_wanted: list[str] = []
    for order in accepted:
        if order["side"] == "note":
            # Recorded and reported; nothing else happens. Placed before the
            # broker paths so a note can never reach one of them.
            run.notes.append(order["reason"])
            log.info("Note from the agent: %s", order["reason"])
            continue
        if order["side"] == "memory":
            action = str(order.get("action") or "").lower().strip()
            text = str(order.get("reason") or "").strip()
            idx = order.get("index")
            if action in ("clear", "clear_all") or text.lower() in ("clear", "clear_all", "clear all", "reset"):
                set_memory_notes([])
                outcomes.append("Memory notes: cleared all notes.")
            elif action in ("remove", "delete") or (isinstance(idx, int) and idx >= 0):
                if isinstance(idx, int) and remove_memory_note(idx):
                    outcomes.append(f"Memory notes: removed note at index {idx}.")
                elif text and remove_memory_note(text):
                    outcomes.append(f"Memory notes: removed note '{text}'.")
                else:
                    outcomes.append("Memory notes: could not find note to remove.")
            elif text:
                add_memory_note(text)
                outcomes.append(f'Memory notes: recorded "{text}".')
            continue
        if order["side"] == "research":
            _commission_research(order, run)
            research_wanted.append(order["ticker"])
            continue
        if order["side"] == "untrack":
            _untrack(order, run)
            outcomes.append(f"{order['ticker']}: no longer tracked.")
            continue
        if order["side"] == "adjust":
            outcome = adjust_exits(
                order["ticker"], order.get("stop"), order.get("target"), run=run
            )
            log.info("Adjust %s: %s", order["ticker"], outcome["message"])
            if outcome["ok"]:
                run.adjusted.append(outcome["message"])
                outcomes.append(f"{order['ticker']}: {outcome['message']}")
            else:
                run.failed.append((order, outcome["message"]))
                outcomes.append(f"{order['ticker']}: NOT adjusted — {outcome['message']}")
            continue
        if order["side"] == "cancel":
            # Re-checked here, not only trusted from screening: the pending
            # order could have filled in the gap between screening this
            # answer and reaching it, same as any other order in the list.
            pending_entry = next(
                (t for t in db.get_pending_agent_trades()
                 if t.ticker == order["ticker"] and not t.is_stop),
                None,
            )
            if pending_entry is None:
                outcomes.append(f"{order['ticker']}: nothing pending to cancel.")
                continue
            try:
                cancelled_ok = sandbox_broker.cancel_order(pending_entry.client_order_id)
            except Exception as exc:
                log.exception("Couldn't cancel the pending order on %s", order["ticker"])
                run.failed.append((order, str(exc)))
                outcomes.append(
                    f"{order['ticker']}: the broker would NOT cancel the pending order — {exc}"
                )
                continue
            if cancelled_ok:
                db.settle_agent_trade(pending_entry.client_order_id, status="rejected")
                outcomes.append(
                    f"{order['ticker']}: cancelled the pending {pending_entry.side} of "
                    f"{pending_entry.quantity:g}."
                )
            else:
                outcomes.append(
                    f"{order['ticker']}: the broker did not confirm the cancel — it may "
                    "have just filled."
                )
            continue
        try:
            result = _place(order, prices.get(order["ticker"]), stops, targets, run=run)
        except Exception as exc:  # broker refusal, network, bad symbol
            log.exception("Order failed for %s", order["ticker"])
            run.failed.append((order, str(exc)))
            outcomes.append(
                f"{order['ticker']}: the broker would NOT take your "
                f"{order['side']} of {order['quantity']} — {exc}"
            )
            continue
        is_limit = str(order.get("order_type") or "").lower().strip() == "limit"
        db.record_agent_trade(
            ticker=order["ticker"],
            side=order["side"],
            quantity=order["quantity"],
            client_order_id=result["client_order_id"],
            placed_at=result["placed_at"],
            reason=str(order.get("reason") or "")[:500] or None,
            signal_id=signal_by_ticker.get(order["ticker"]),
            limit_price=float(order["limit_price"]) if is_limit else None,
        )
        run.placed.append(order)
        outcomes.append(_describe_fill(order, result, prices, stops, targets))
        if order["side"] == "buy":
            if result.get("exits") is not None:
                _record_exits(order["ticker"], result["exits"])
            elif is_limit:
                pass  # _describe_fill already said this isn't armed yet.
            else:
                unguarded = _arm_exits(
                    order,
                    stops.get(order["ticker"]),
                    targets.get(order["ticker"]),
                    client_order_id=result["client_order_id"],
                    run=run,
                )
                if unguarded:
                    # Told in the same breath as the fill, not only in the
                    # alert log — the agent can act on it before this pass
                    # even ends.
                    outcomes.append(f"UNGUARDED — {unguarded}. Nothing rests under it.")
        # A sell has already cleared its own resting exits, before the order
        # went out — the broker refuses it otherwise. See _place.
    if research_wanted:
        lines, failed = _research_and_report(research_wanted)
        outcomes.extend(lines)
        for t in research_wanted:
            if t not in failed:
                continue
            # Commissioned optimistically in _commission_research, before the
            # analysis itself was known to succeed or fail. A failure moves it
            # into the same bucket a broker refusal already uses, so the
            # Decisions page shows it as failed rather than as an ordinary
            # research order with a blank reason.
            if t in run.researched:
                run.researched.remove(t)
            run.failed.append(({"side": "research", "ticker": t, "quantity": 0}, failed[t]))
        # So the next turn's signal table can mark the rows this pass paid
        # for — only the ones that actually produced a signal.
        if researched is not None:
            researched.update(t for t in research_wanted if t not in failed)
    return outcomes


def _describe_fill(order, result, prices, stops, targets) -> str:
    """One line saying what an order did, for the model's next turn.

    Says what is resting under a buy, because that is the part the agent
    cannot otherwise see until the following pass — and a bracket refused
    against unsettled cash is routine here, not an edge case.
    """
    ticker = order["ticker"]
    price = prices.get(ticker)
    at = f" at about ${price:,.2f}" if price else ""
    is_limit = str(order.get("order_type") or "").lower().strip() == "limit"

    if order["side"] != "buy":
        if is_limit:
            return (
                f"{ticker}: limit sell of {order['quantity']:g} at "
                f"${float(order['limit_price']):,.2f} placed, not yet filled."
            )
        return f"{ticker}: sold {order['quantity']}{at}."

    stop, target = stops.get(ticker), targets.get(ticker)
    if is_limit:
        # Never bracketed — see _place. Nothing is resting under this yet,
        # whether or not the signal had a usable stop/target, because a limit
        # buy may not have filled at all. The agent is woken the instant it
        # does (scheduler._settle_agent_fills, agent.format_limit_fill) and
        # sets the exits itself then, off the real fill price.
        note = ""
        if stop or target:
            note = " The signal's stop/target were NOT placed — resend them with adjust once this fills."
        return (
            f"{ticker}: limit buy of {order['quantity']:g} at "
            f"${float(order['limit_price']):,.2f} placed, not yet filled.{note}"
        )
    if result.get("exits") is not None:
        under = "The broker took the stop and target with it."
    elif stop or target:
        under = "The broker refused the bracket, so the exits were armed separately."
    else:
        under = "NOTHING is resting under it — no usable stop or target was on the signal."
    levels = []
    if stop:
        levels.append(f"stop ${stop:,.2f}")
    if target:
        levels.append(f"target ${target:,.2f}")
    tail = f" ({', '.join(levels)})" if levels else ""
    return f"{ticker}: bought {order['quantity']}{at}. {under}{tail}"


def _fold_in(run, decision, reasoning, rejected, book) -> None:
    """Merge a later turn of the same pass into the run being built.

    One pass is one row, however many turns it took. Tokens and seconds add up
    because every turn was really spent; the prompt, the answer and the
    thinking are the latest, because that is the turn the pass ended on; and
    refusals accumulate, because a refusal in an earlier turn happened whether
    or not the agent went on to do something else.
    """
    run.reasoning = reasoning or run.reasoning
    run.prompt = getattr(decision, "prompt", "") or run.prompt
    run.response = getattr(decision, "response", "") or run.response
    run.thinking = getattr(decision, "thinking", None) or run.thinking
    run.prompt_tokens = (run.prompt_tokens or 0) + getattr(decision, "prompt_tokens", 0)
    run.completion_tokens = (run.completion_tokens or 0) + getattr(decision, "completion_tokens", 0)
    run.seconds = (run.seconds or 0.0) + getattr(decision, "seconds", 0.0)
    run.turns = list(run.turns or []) + list(getattr(decision, "turns", []) or [])
    run.rejected = list(run.rejected or []) + list(rejected or [])
    run.book = book


def run_once(woke_because: str | None = None) -> AgentRun:
    """One decision pass: settle fills, build the book, ask the model, screen
    the answer, place what survives.

    Refuses to run outside the sandbox. sandbox_broker would refuse each order
    anyway, but failing here means the model is never even asked, so a
    misconfigured deployment costs nothing instead of a full analysis.
    """
    # Skipped passes are recorded too. Four days of "switched off" is part of
    # the story — a journey that showed only the days something happened would
    # credit the agent with patience it never had the chance to show.
    if not quotes.is_sandbox():
        return _skip("Webull is not in sandbox mode — refusing to trade.")
    if not is_enabled():
        return _skip("The trading agent is switched off.")
    # **A closed market is not a reason to skip the pass (2026-09-10).** There
    # was a gate here refusing every pass while the session was shut, and it
    # was answering the wrong question. It exists because the venue rejects an
    # order outside the session
    # (CAN_NOT_TRADING_FOR_FIXGW_NOT_READY_MARKET) — an argument about orders,
    # not about whether the agent may think.
    #
    # Research, moving a stop, untracking a name, leaving a note and choosing
    # the next wakeup all work at any hour, and the prompt has invited the
    # agent to use them since 2026-09-05: "Waking early to commission the
    # analyses you want ready for the open is a good use of this." The gate
    # made that untrue, silently — a 6am wakeup the agent had chosen produced
    # nothing at all.
    #
    # Nothing is lost by removing it. `market_clock.describe()` is the first
    # line of every prompt and says whether the market is open, closed for the
    # day, or closed for the weekend, so the agent knows. An order sent anyway
    # is refused by sandbox_broker and recorded as a broker failure, and the
    # last five failures appear in the next prompt — which is the loop the
    # rules already describe.
    settled = settle_pending()
    if settled:
        log.info("Settled %d pending order(s) before deciding", len(settled))

    # **The pass is a loop now, not a single question (2026-09-12).** The agent
    # acts, sees what its own orders did, and is asked again — the way a person
    # placing an order watches it fill, and the way it already works for a read.
    # It ends when an answer does nothing, which is also what "no action, just
    # tell me when to wake you" looks like.
    #
    # Everything below the loop head is rebuilt on every turn on purpose: a buy
    # changed the cash, a sell changed the holdings, and a research order put a
    # new analysis in the signal table that the next turn has to be able to see.
    run = None
    outcomes: list[str] = []
    # Read once for the whole pass, and counted once — see mark_changes_seen.
    changes = _recent_changes()
    # Tickers this pass has paid to have analysed. The signals table marks
    # their rows, because that is where the agent actually looks.
    researched: set[str] = set()
    # One read allowance for the whole pass — see _decide.
    budget = {"reads": _MAX_READS_PER_PASS, "turns": _MAX_READ_TURNS}
    last_signature = None
    # **Every next_wakeup_note this pass has written so far, oldest first.**
    # `run.wakeup_note` below is overwritten each turn and only the last one
    # survives to the next pass — right for that, since one pass should hand
    # off one note. But a mid-pass turn only ever saw the *previous pass's*
    # note (`last_note` in `_decide`, stable for the whole loop because
    # `_record_run` has not run yet) — never what it or an earlier turn of
    # this same pass had just written. A note set in turn 1 was gone by turn
    # 2 unless the model happened to repeat it. See the 2026-09-17 JOURNEY.md
    # entry.
    notes_this_pass: list[str] = []
    for act_turn in range(_MAX_ACT_TURNS):
        signals = _recent_signals()
        book = agent_book.build_book(price_lookup=get_current_price)
        # The full watchlist, not just signal/holding tickers — since 2026-09-08
        # the prompt shows a live price for every tracked ticker, held or not, so
        # the agent can judge staleness for a name nothing has auto-analysed.
        watchlist = sorted(db.get_watchlist())
        prices = _price_map(
            [s.ticker for s in signals] + [h.ticker for h in book.holdings] + watchlist
        )
        book = agent_book.build_book(price_lookup=prices.get)

        # What its own past decisions did. Signal decisions are joined in so the
        # history can say "you bought this on a Hold", which is the pattern worth
        # naming.
        decisions = {s.id: s.decision for s in db.get_recent_signals(limit=200) if s.id}
        closed = agent_book.closed_trades(decisions=decisions)
        # Only fetched when research is actually charged for. Without a price the
        # agent has no scarcity to reason about, and a menu it can take from for
        # free would just be a longer watchlist someone else chose.
        menu = _candidate_menu() if research.is_charging() else None
        decision = _decide(
            book, signals, prices, closed=closed,
            regime_line=current_regime_line(), horizon_days=_horizon_days(), menu=menu,
            outcomes=outcomes, budget=budget, changes=changes,
            researched_now=researched, woke_because=woke_because,
            pass_notes=notes_this_pass,
        )
        if act_turn == 0:
            # After the first answer, not before it: a pass that fell over on
            # the way to the model never showed the agent anything.
            mark_changes_seen(changes)

        reasoning, accepted, rejected = decision
        # getattr, because Decision unpacks like the tuple it replaced and a caller
        # may still hand back a plain one — several tests patch _decide that way.
        # Accepting both is the point of keeping __iter__.
        if run is None:
            run = AgentRun(reasoning=reasoning, rejected=rejected, book=book,
                           prompt=getattr(decision, "prompt", ""),
                           response=getattr(decision, "response", ""),
                           thinking=getattr(decision, "thinking", None),
                           prompt_tokens=getattr(decision, "prompt_tokens", 0),
                           completion_tokens=getattr(decision, "completion_tokens", 0),
                           seconds=getattr(decision, "seconds", 0.0),
                           turns=list(getattr(decision, "turns", []) or []))
        else:
            _fold_in(run, decision, reasoning, rejected, book)
        # What it asked for and what it got, kept apart. `next_wakeup` is the
        # clamped, usable instant the scheduler acts on; `wakeup_asked` is the raw
        # request. When they differ the agent aimed somewhere the market is shut,
        # and that is worth being able to read afterwards.
        run.wakeup_asked = parse_wakeup(getattr(decision, "response", ""))
        run.next_wakeup = market_clock.clamp_wakeup(run.wakeup_asked)
        run.wakeup_note = parse_wakeup_note(getattr(decision, "response", ""))
        # Kept for the *next* turn of this same pass to read — see
        # `notes_this_pass` above. Skipped when blank or an exact repeat of
        # the one just shown, so a model that restates its note unchanged
        # does not pile up duplicates.
        if run.wakeup_note and run.wakeup_note != (notes_this_pass[-1] if notes_this_pass else None):
            notes_this_pass.append(run.wakeup_note)
        # **The newest signal per ticker, chosen explicitly.** These three used to
        # be dict comprehensions over the signal list, and a dict comprehension
        # keeps the *last* value it sees. The list arrives newest-first, so the
        # oldest signal won every time a ticker had been analysed twice.
        #
        # On 2026-08-28 the agent bought SMCI and rested the exits from the
        # 27th — a stop of 34.16 and a target of 45.21 — when that morning's
        # analysis had said 34.04 and 49.51. The target was $4.30 out on a
        # 260-share position, and nothing reported it, because both numbers are
        # real levels from real signals.
        latest = _newest_signal_per_ticker(signals)
        signal_by_ticker = {t: s.id for t, s in latest.items()}
        # The stop the analysis named, per ticker. Already checked for
        # plausibility when the signal was recorded (see analysis._trade_plan_levels),
        # with an ATR-derived fallback, so a level here is one worth resting on.
        stops = {t: s.stop_loss for t, s in latest.items() if s.stop_loss}
        # The level the analysis expects it to reach. Same provenance as the stop:
        # stated by the trader, discarded if implausible against the traded price.
        targets = {t: s.price_target for t, s in latest.items() if s.price_target}
        # **An answer that repeats the last one is a loop, and it moves money.**
        # The prompt says what has already been done and not to place it again,
        # but a small model can still hand back the same list, and executing it
        # twice would buy twice. Screening would catch the second buy only when
        # the cash had run out, which is far too late to rely on.
        signature = [
            (
                o.get("side"),
                o.get("ticker"),
                o.get("quantity"),
                o.get("stop"),
                o.get("target"),
                o.get("date"),
            )
            for o in accepted
        ]
        if signature and signature == last_signature:
            log.info("The answer repeats the previous turn's orders; ending the pass")
            break
        last_signature = signature
        did = _execute_orders(accepted, run, prices, stops, targets, signal_by_ticker, researched)
        # Settle again on the way out, and re-read the book. A market order placed
        # in session hours fills in well under a second, but nothing would notice
        # until the next scheduled pass — so the Discord post and the dashboard
        # would both spend a day reporting cash that has already been spent, beside
        # an order marked "waiting to fill" that filled immediately.
        if run.placed:
            settle_pending()
            run.book = agent_book.build_book(price_lookup=prices.get)
        if not did:
            # Nothing happened that the agent could react to. A pass that only
            # left a note lands here too, and should: a note is a message to the
            # people who maintain this app, not an order with a result.
            break
        outcomes = outcomes + did
        log.info(
            "Asking again after acting (%d of %d act-turns used)",
            act_turn + 1, _MAX_ACT_TURNS,
        )
    else:
        log.info("Act-turn budget spent; the pass ends here")

    _record_run(run)
    return run


def _refusals_json(run: "AgentRun") -> str | None:
    """Everything the agent asked for and did not get, with the reason.

    Screened refusals and broker failures are kept apart because they mean
    different things: the first is the agent asking for something impossible,
    the second is the venue saying no to something reasonable. Reading a month
    of runs, those point at different fixes.
    """
    entries = [
        {"ticker": r.ticker, "side": r.side, "quantity": r.quantity,
         "why": r.why, "refused_by": "screening"}
        for r in run.rejected
    ] + [
        {"ticker": o.get("ticker"), "side": o.get("side"), "quantity": o.get("quantity"),
         "why": why, "refused_by": "broker"}
        for o, why in run.failed
    ]
    if not entries:
        return None
    try:
        return json.dumps(entries)[:4000]
    except (TypeError, ValueError):
        log.exception("Could not serialise this run's refusals")
        return None


def _skip(why: str) -> "AgentRun":
    """A pass that never reached the model, recorded rather than dropped."""
    run = AgentRun(skipped=why)
    _record_run(run)
    return run


def _commission_research(order: dict, run: "AgentRun") -> None:
    """Pay for an analysis and run it inside this pass.

    **There is no sweep to wait for any more (2026-09-08).** Every
    commission — a brand new candidate or a fresh look at something already
    tracked, held or not — runs immediately.

    **And it runs inside this pass since 2026-09-12**, via
    ``_research_and_report``, which waits for it and hands the verdict and the
    analyst's reasoning back to the agent on its next turn. The old shape
    dispatched it after the pass and asked the agent again afterwards, which
    meant the answer arrived with the reasoning that wanted it already gone.

    Tracking is a side effect kept for a not-yet-tracked ticker, not the
    mechanism any more — nothing reads the watchlist to decide what to
    analyse today, so adding it here only means the ticker will still be
    there tomorrow to be researched again.

    **Nothing is charged here.** The charge belongs to the analysis and lands
    when it actually runs, in propagate_ticker.
    """
    ticker = order["ticker"]
    try:
        db.add_to_watchlist(ticker)
    except Exception:
        log.exception("Could not track %s for research", ticker)
        run.failed.append((order, "could not be added to the watchlist"))
        return
    run.researched.append(ticker)
    run.research_now.append(ticker)
    log.info("Agent commissioned research on %s", ticker)


def _untrack(order: dict, run: "AgentRun") -> None:
    """Stop being able to research a ticker until it is tracked again.

    The counterpart to ``_commission_research``, and the reason the watchlist
    is no longer a ratchet. Commissioning adds a ticker permanently; without
    this, the only way one ever left was somebody typing ``/untrack``.

    Nothing is refunded. The analyses already run were paid for and produced
    the opinions that led here, so there is nothing to give back. Since
    2026-09-08 nothing is charged automatically either way — what untracking
    actually frees is a slot, for something worth tracking instead.

    ``screen`` has already refused this for a ticker the agent still holds.
    """
    ticker = order["ticker"]
    try:
        db.remove_from_watchlist(ticker)
    except Exception:
        log.exception("Could not untrack %s", ticker)
        run.failed.append((order, "could not be removed from the watchlist"))
        return
    run.untracked.append(ticker)
    log.info("Agent stopped watching %s", ticker)


def _failures_json(run: "AgentRun") -> str | None:
    """What the broker refused, so tomorrow's prompt can say so.

    Only the count was kept until 2026-09-02. That made a failure invisible the
    next morning: the agent would form the same order, be refused again, and
    nothing in the record would explain the repetition.
    """
    if not run.failed:
        return None
    return json.dumps([
        {"side": o.get("side", ""), "ticker": o.get("ticker", ""),
         "quantity": o.get("quantity") or 0, "why": str(why)[:300]}
        for o, why in run.failed
    ])


def _orders_json(run: "AgentRun") -> str | None:
    """Every order the pass produced, for the Events page.

    ``agenttrade`` holds the buys and sells, but an untrack, a research and an
    adjust move no shares and leave no row there. Without this the page could
    show a pass that untracked two tickers as having done nothing.
    """
    orders = (
        [{"side": o["side"], "ticker": o["ticker"],
          "quantity": o.get("quantity") or 0,
          "reason": str(o.get("reason") or "")[:300]}
         for o in run.placed]
        # The ticker is cut from the front of the message, which reads
        # "AVGO: moved stop to $333.84." — so the first word carries the colon
        # that separates it from the rest. Left in, every reader of this field
        # showed "AVGO:" as the ticker.
        + [{"side": "adjust", "ticker": a.split()[0].rstrip(":") if a else "",
            "quantity": 0, "reason": a} for a in run.adjusted]
        + [{"side": "research", "ticker": t, "quantity": 0, "reason": ""}
           for t in run.researched]
        + [{"side": "untrack", "ticker": t, "quantity": 0, "reason": ""}
           for t in run.untracked]
        + [{"side": "note", "ticker": "", "quantity": 0, "reason": n}
           for n in run.notes]
    )
    return json.dumps(orders) if orders else None


def _record_run(run: "AgentRun") -> None:
    """Write down what this pass decided, including when it decided nothing.

    The reasoning used to go to Discord and evaporate, which left the record
    with trades and no account of the days between them. A book you can only
    read on the days money moved is a ledger, not a history.

    Never raises. A pass that traded successfully must not be reported as a
    failure because the note about it could not be filed.
    """
    book = run.book
    try:
        db.record_agent_run(
            ran_at=datetime.datetime.now(datetime.timezone.utc),
            reasoning=run.reasoning,
            placed=len(run.placed),
            rejected=len(run.rejected),
            failed=len(run.failed),
            adjusted=len(run.adjusted),
            skipped=run.skipped,
            refusals=_refusals_json(run),
            prompt=run.prompt or None,
            response=run.response or None,
            thinking=run.thinking or None,
            # JSON, or NULL for a pass with nothing to add beyond the single
            # turn already in prompt/response.
            turns=json.dumps(run.turns) if len(run.turns or []) > 1 else None,
            # NULL, not 0, when nothing reported them — a zero would read as a
            # free call. Same rule as Signal's own usage columns.
            prompt_tokens=run.prompt_tokens or None,
            completion_tokens=run.completion_tokens or None,
            seconds=run.seconds or None,
            orders=_orders_json(run),
            failures=_failures_json(run),
            notes=json.dumps(run.notes) if run.notes else None,
            equity=book.equity if book else None,
            cash=book.cash if book else None,
            research_spent=book.research_spent if book else None,
            # Stored as UTC like every other timestamp here. The agent reasons
            # in Eastern and the database does not.
            next_wakeup=(
                run.next_wakeup.astimezone(datetime.timezone.utc)
                if run.next_wakeup
                else None
            ),
            wakeup_note=run.wakeup_note,
        )
    except Exception:
        log.exception("Could not record the agent run")


def format_stop_fill(fill: dict) -> str:
    """A resting exit triggering is the one event here nobody asked for and
    would most want to hear about — a position was sold without anyone
    deciding to sell it that day."""
    hit_target = (fill.get("reason") or "").startswith("take-profit")
    if hit_target:
        return (
            f"🎯 Target reached: sold {fill['quantity']:g} {fill['ticker']} "
            f"at ${fill['price']:,.2f}. The analysis's price target was hit, so the "
            "paper position is closed at a profit."
        )
    return (
        f"🛑 Stop triggered: sold {fill['quantity']:g} {fill['ticker']} "
        f"at ${fill['price']:,.2f}. The thesis level from the analysis was reached, "
        "so the paper position is closed."
    )


def format_limit_fill(fill: dict) -> str:
    """A limit buy filling is the other event nobody was waiting on this
    pass: it never brackets (see ``_place``), so the shares land with nothing
    resting under them until the agent sets a stop and/or target itself."""
    return (
        f"🟢 Limit buy filled: bought {fill['quantity']:g} {fill['ticker']} "
        f"at ${fill['price']:,.2f}. Nothing is protecting these shares yet — "
        "set a stop and/or target with adjust now."
    )


@dataclass
class ResetResult:
    cancelled: int = 0
    closed: list[str] = field(default_factory=list)
    cleared: int = 0
    refused: str | None = None


def reset_book(pending_external_flatten: bool = False) -> ResetResult:
    """Return the agent to a flat book and an empty ledger.

    For when the agent itself has changed enough that its record describes a
    system that no longer exists — a new prompt, new rules, exits it did not
    have. Not for a run that went badly: resetting on bad results is how you
    accumulate no evidence at all, and the baselines exist precisely so a bad
    run can be read rather than erased.

    The broker is asked first, and what it says decides the work. An account
    already flat — reset from Webull's own site, say — needs nothing sold, so
    the ledger is simply cleared and the market's hours are irrelevant. Only
    when shares are actually held does this have to cancel and sell, which
    market orders make a market-hours operation.

    The ledger is cleared **only after the account is confirmed empty**.
    Clearing first would leave the ledger claiming nothing while the broker
    still held shares, which is the one disagreement reconciliation cannot
    recover from.
    """
    if not quotes.is_sandbox():
        return ResetResult(refused="Webull is not in sandbox mode.")

    result = ResetResult()
    held = sandbox_broker.get_positions()

    if pending_external_flatten and held:
        # The account is going to be flattened from Webull's own site, so the
        # usual refusal would just block a reset that is genuinely intended.
        # But between now and then the ledger and the account disagree, and an
        # agent trading into that gap would buy positions the site reset then
        # silently wipes — leaving the ledger claiming stock that is gone. So
        # the agent is switched off as part of this, and only turning it back
        # on resumes trading.
        for ticker in sorted(held):
            result.cancelled += len(_cancel_resting_exits(ticker))
        set_enabled(False)
        result.cleared = db.clear_agent_trades()
        result.refused = (
            f"Ledger cleared and the agent switched OFF. The account still holds {held} "
            "with the exits cancelled — flatten it on Webull's site, then switch the "
            "agent back on."
        )
        log.warning("Ledger cleared ahead of an external flatten; agent disabled")
        return result
    if held is None:
        return ResetResult(refused="Couldn't read the account — nothing was touched.")

    if held:
        # Checked before anything is touched. Market orders are rejected outside
        # the session, so a reset started after the close would cancel the exits,
        # fail to sell, and leave the positions naked overnight — which is how
        # this guard came to exist.
        if not watchdog.is_us_market_hours():
            return ResetResult(
                refused=f"The account still holds {held}, and closing a position needs a "
                "market order. Nothing was touched — reset it on Webull's site, or run "
                "this again once the market opens."
            )
        # Cancel and sell one holding at a time. Cancelling everything up front
        # means a failure on the first sell leaves every other position
        # unprotected too, rather than only the one being closed.
        for ticker, quantity in sorted(held.items()):
            result.cancelled += len(_cancel_resting_exits(ticker))
            try:
                sandbox_broker.place_market_order(ticker, "SELL", quantity)
                result.closed.append(f"{quantity:g} {ticker}")
            except Exception as exc:
                log.exception("Couldn't close %s during reset", ticker)
                result.refused = f"Couldn't close {ticker}: {exc}"
                return result

        still_held = sandbox_broker.get_positions()
        if still_held is None or still_held:
            result.refused = (
                f"Account still holds {still_held} — the sells may not have filled yet. "
                "Ledger left alone; try again once they have."
            )
            return result

    # Nothing is held, so any exit still resting belongs to a position that no
    # longer exists — an order to sell shares the account does not have.
    for trade in db.get_pending_agent_trades():
        if trade.is_stop and sandbox_broker.cancel_order(trade.client_order_id):
            result.cancelled += 1

    result.cleared = db.clear_agent_trades()
    log.info("Agent book reset: cleared %d ledger row(s)", result.cleared)
    return result


def arm_exits_now(ticker: str) -> dict:
    """Place the missing exits on a position the agent already holds.

    The remediation for a position that ended up unguarded — a bracket the
    broker refused, a buy that filled too slowly, a stop the price had already
    fallen through. All three used to need someone with a Python shell; the
    first time it happened, that someone was reconstructing the right levels by
    hand while the position sat exposed.

    The levels are the same ones a fresh buy would get: the newest signal's,
    screened against the current price, with a volatility-derived stop when the
    stated one cannot be used. Nothing here invents a target — a made-up exit
    price on a real position is worse than none, because it looks decided.

    Refuses rather than duplicates when exits are already resting. Two stops on
    one position sell it twice, and the second sale is a short.

    Returns {"ok": bool, "message": str} — this answers a button, so the reason
    for a refusal has to be readable rather than an exception type.
    """
    from backend.services import ticker_book

    ticker = ticker.upper().strip()
    if not quotes.is_sandbox():
        return {"ok": False, "message": "Webull is not in sandbox mode, so no order can be placed."}

    price = get_current_price(ticker)
    position = ticker_book.agent_position(ticker, price)
    if position is None:
        return {"ok": False, "message": f"The auto trader holds no {ticker}."}
    if position.exits:
        resting = ", ".join(f"{e.kind} at ${e.price:,.2f}" for e in position.exits)
        return {"ok": False, "message": f"{ticker} already has {resting} resting."}

    signal = next(iter(db.get_recent_signals(ticker, limit=1)), None)
    stop, target = usable_levels(
        ticker,
        signal.stop_loss if signal else None,
        signal.price_target if signal else None,
        price,
    )
    if stop is None and price:
        stop = atr_stop(ticker, price)
    if stop is None and target is None:
        return {
            "ok": False,
            "message": (
                f"No usable level for {ticker}: the analysis gives none that the price "
                "has not already passed, and there is too little history to derive one."
            ),
        }

    # The broker takes a standalone order at any hour but refuses a combo —
    # an OCO pair or a bracket — outside 9:30-16:00 ET, because linking legs
    # needs the routing session that only runs then. Rather than telling
    # someone who has already noticed the problem to come back in the morning,
    # remember the request and act on it at the open.
    if not watchdog.is_us_market_hours():
        db.queue_exit_arm(ticker)
        return {
            "ok": True,
            "queued": True,
            "message": (
                f"The market is shut, so {ticker} is queued — the exits go on at the next open. "
                "Nothing is protecting it until then."
            ),
        }

    order = {"ticker": ticker, "side": "buy", "quantity": position.quantity}
    try:
        _arm_exits(order, stop, target)
    except Exception as exc:  # _arm_exits is best-effort, but a broker refusal can still surface
        return {"ok": False, "message": f"The broker refused it: {exc}"}

    placed = ticker_book.agent_position(ticker, price)
    if placed is None or not placed.exits:
        return {
            "ok": False,
            "message": (
                f"Nothing rested on {ticker}. The market is open 9:30–16:00 ET; outside those "
                "hours the broker refuses every order. Check the log for the exact reason."
            ),
        }
    resting = ", ".join(f"{e.kind} at ${e.price:,.2f}" for e in placed.exits)
    return {"ok": True, "message": f"Armed {ticker}: {resting}."}


def process_queued_arms() -> list[dict]:
    """Act on every request queued while the market was shut.

    Called from the intraday pass, which already runs only during market hours,
    so the first tick after the open drains the queue. Returns what happened
    per ticker, so a caller can announce it — a request made the previous
    evening and silently dropped would be worse than not offering the queue.

    Each is re-checked rather than replayed. Hours have passed: the position
    may have been sold, exits may have been placed by a fresh buy, and the
    price has certainly moved, so the levels are computed now rather than
    remembered from when the button was pressed.
    """
    results = []
    for request in db.get_pending_exit_arms():
        try:
            outcome = arm_exits_now(request.ticker)
        except Exception as exc:  # a bad request must not stall the rest of the queue
            log.exception("Queued arming failed for %s", request.ticker)
            outcome = {"ok": False, "message": f"Failed: {exc}"}
        # A request that re-queues itself would loop forever, so a queued
        # answer here counts as failure — the market is open, and if arming
        # still cannot happen the reason is not the hour.
        ok = bool(outcome.get("ok")) and not outcome.get("queued")
        db.complete_exit_arm(request.id, ok, outcome["message"])
        results.append({"ticker": request.ticker, "ok": ok, "message": outcome["message"]})
    return results


def adjust_exits(
    ticker: str, stop: float | None, target: float | None, run: "AgentRun | None" = None
) -> dict:
    """Move the exits resting under a position to new levels.

    The agent re-reads every holding each day, and until this existed it could
    do nothing with what it learned: the stop and target were fixed when the
    position opened and sat unchanged until it closed. GOOG spent a week with a
    $377.09 take-profit while each morning's analysis put the move's end at
    $345.00 — a level the position would never have reached.

    Letting the model move them is deliberate. Tightening a stop as a trade
    works is what the exits are *for*, and a rule that only ever placed them
    once is not risk management, it is a fossil. What Python keeps is the same
    thing it keeps everywhere else: a level that cannot be executed as stated
    is refused, never silently corrected.

    Replaces rather than cancelling and re-placing. Cancelling first leaves a
    window with nothing under the position, which is the state this app spends
    most of its effort avoiding. A level with nothing resting yet is armed
    instead, so "set my exits to these" works whether or not there are any.
    """
    ticker = ticker.upper().strip()
    if not quotes.is_sandbox():
        return {"ok": False, "message": "Webull is not in sandbox mode, so no order can be placed."}

    price = get_current_price(ticker)
    stop, target = usable_levels(ticker, stop, target, price)
    if stop is None and target is None:
        return {"ok": False, "message": f"No usable level for {ticker} — nothing was changed."}

    resting = {t.exit_kind: t for t in db.get_resting_exits(ticker) if t.exit_kind}
    moved, armed, failed = [], [], []
    for kind, level in (("stop", stop), ("target", target)):
        if level is None:
            continue
        existing = resting.get(kind)
        if existing is None:
            armed.append((kind, level))
            continue
        if existing.limit_price is not None and abs(existing.limit_price - level) < 0.005:
            continue  # already there; a replace would be a round trip for nothing
        try:
            sandbox_broker.replace_exit(existing.client_order_id, kind, level)
        except Exception as exc:
            log.exception("Couldn't move the %s on %s", kind, ticker)
            failed.append(f"{kind} ({exc})")
            continue
        db.move_resting_exit(existing.id, level)
        moved.append((kind, level))

    # Whatever had nothing resting yet is placed now, in one call so a pair
    # still goes out as a pair.
    if armed:
        levels = dict(armed)
        position = next(
            (h for h in agent_book.build_book().holdings if h.ticker == ticker), None
        )
        if position is not None:
            # _arm_exits clears whatever is already resting before placing
            # the new pair (see _clear_before_arming) — needed here too: the
            # local ledger showing nothing resting is not proof the broker
            # agrees, and arming on top of a stale broker-side exit is
            # exactly what produced AVGO's repeated REVERSE_OPTION refusal.
            unguarded = _arm_exits(
                {"ticker": ticker, "side": "buy", "quantity": position.quantity},
                levels.get("stop"),
                levels.get("target"),
                run=run,
            )
            if unguarded:
                # Until 2026-09-16 this call's result was never checked, so a
                # broker refusal here still reported "placed" below.
                armed = [(k, v) for k, v in armed if k not in levels]
                failed.append(unguarded)

    parts = [f"moved {k} to ${v:,.2f}" for k, v in moved]
    parts += [f"placed {k} at ${v:,.2f}" for k, v in armed]
    if failed:
        parts += [f"could not move {f}" for f in failed]
    if not parts:
        return {"ok": True, "message": f"{ticker} exits already at those levels."}
    return {"ok": not failed, "message": f"{ticker}: {', '.join(parts)}."}
