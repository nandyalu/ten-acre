"""The agent's story, assembled from what it did.

The dashboard shows a snapshot: what is held now, what was decided today, what
the equity is. That is data. A month of it is still data, and nobody can read
it — because the interesting thing about an experiment is not its state but
its *arc*: what it tried, what that cost, what it learned, and where it turned.

So this reads the same tables the dashboard does and writes them out in order,
day by day, in the agent's own words. Nothing here is a second source of
truth: every sentence is derived from a trade, a charge, a decision pass or a
graded signal that already exists, so the story cannot drift from the book.

**What this cannot write is the half that matters most.** It knows the agent
changed its mind; it does not know that *we* changed the prompt the week
before, or that a research charge was introduced, or that the round count was
settled by an experiment. That interpretation belongs to a person, in
JOURNEY.md. This produces the chronicle; a human writes the history.

Milestones are detected rather than declared. A first trade, a first close, a
new high, a drawdown past a threshold — those are facts about the series, and
naming them is what turns a list of days into a shape.
"""
import datetime
import logging
import os
from dataclasses import dataclass, field

from backend import paths
from backend.database import db
from backend.services import agent_book, research

log = logging.getLogger("ten-acre.journey")

# How far the book must fall from its best before the fall is worth naming.
# Below this, ordinary daily movement would litter the story with "drawdown"
# on days nothing happened.
_DRAWDOWN_PCT = 5.0


@dataclass
class Milestone:
    """Something that happened once and is worth naming when it did."""

    kind: str
    text: str


@dataclass
class Day:
    """One day of the agent's life."""

    date: datetime.date
    equity: float | None = None
    return_pct: float | None = None
    research_spent: float = 0.0
    analyses: int = 0
    reasoning: str = ""
    skipped: str | None = None
    opened: list[str] = field(default_factory=list)
    closed: list[str] = field(default_factory=list)
    milestones: list[Milestone] = field(default_factory=list)

    @property
    def quiet(self) -> bool:
        """Nothing was bought, sold or spent. Worth knowing, because a run of
        quiet days is itself a decision the agent made."""
        return not self.opened and not self.closed and not self.research_spent


def _describe_open(lot) -> str:
    return f"bought {lot.quantity:g} {lot.ticker} at ${lot.entry:,.2f}"


def _describe_close(lot) -> str:
    verdict = "made" if (lot.pnl or 0) >= 0 else "lost"
    return (
        f"sold {lot.quantity:g} {lot.ticker} at ${lot.exit:,.2f} — "
        f"{verdict} ${abs(lot.pnl):,.2f} over {lot.held_days} day(s)"
    )


def months_with_entries(budget: float | None = None) -> list[str]:
    """Every "YYYY-MM" with at least one recorded day, newest first — the
    dots on the Journal page's month timeline."""
    return sorted({day.date.strftime("%Y-%m") for day in build(budget)}, reverse=True)


def build_for_month(month: str, budget: float | None = None) -> list[Day]:
    """One month's days ("YYYY-MM"), oldest first — same ordering as
    build(), which this filters rather than querying separately. build()
    already reads the whole history in one pass (trade lots, decision runs,
    research charges, the equity curve), so there is nothing to gain from a
    second, narrower version of it — only a second place for the same logic
    to drift."""
    return [day for day in build(budget) if day.date.strftime("%Y-%m") == month]


def build(budget: float | None = None) -> list[Day]:
    """The whole story so far, oldest day first."""
    budget = budget if budget is not None else agent_book.get_budget()
    lots = agent_book.trade_history()
    runs = db.get_agent_runs()
    charges = research.spent_by_day()
    curve = {p.date: p for p in agent_book.equity_curve()}

    days: dict[datetime.date, Day] = {}

    def day_for(date: datetime.date) -> Day:
        if date not in days:
            days[date] = Day(date=date)
        return days[date]

    for lot in lots:
        day_for(lot.entry_at.date()).opened.append(_describe_open(lot))
        if lot.exit_at:
            day_for(lot.exit_at.date()).closed.append(_describe_close(lot))

    for date, spent in charges.items():
        entry = day_for(date)
        entry.research_spent += spent

    for run in runs:
        entry = day_for(run.ran_at.date())
        # The last pass of a day wins: an intraday trigger that re-decided is
        # a better account of the day than the morning batch it superseded.
        if run.skipped:
            entry.skipped = run.skipped
        elif run.reasoning:
            entry.reasoning = run.reasoning
            entry.skipped = None

    for date, point in curve.items():
        entry = day_for(date)
        entry.equity = point.equity
        entry.return_pct = (point.equity / budget - 1) * 100 if budget else None

    ordered = [days[d] for d in sorted(days)]
    _mark_milestones(ordered, budget)
    return ordered


def _mark_milestones(days: list[Day], budget: float) -> None:
    """Name the moments that gave the series its shape.

    Detected from the data rather than declared by hand, so they cannot claim
    something the book does not show.
    """
    seen_trade = seen_close = seen_win = seen_loss = False
    best = budget
    in_drawdown = False

    for day in days:
        if day.opened and not seen_trade:
            seen_trade = True
            day.milestones.append(
                Milestone("first-trade", f"First position opened: {day.opened[0]}.")
            )
        if day.closed and not seen_close:
            seen_close = True
            day.milestones.append(
                Milestone("first-close", f"First round trip completed: {day.closed[0]}.")
            )
        for text in day.closed:
            if "made" in text and not seen_win:
                seen_win = True
                day.milestones.append(Milestone("first-win", "First profitable trade."))
            if "lost" in text and not seen_loss:
                seen_loss = True
                day.milestones.append(Milestone("first-loss", "First losing trade."))

        if day.equity is None:
            continue
        if day.equity > best:
            # Only worth naming once the book is actually ahead; a new high
            # while still underwater is just a smaller loss.
            if day.equity > budget and best <= budget:
                day.milestones.append(
                    Milestone("above-water", f"Back above the starting ${budget:,.0f}.")
                )
            best = day.equity
            in_drawdown = False
        elif best > 0:
            fall = (best - day.equity) / best * 100
            if fall >= _DRAWDOWN_PCT and not in_drawdown:
                in_drawdown = True
                day.milestones.append(
                    Milestone("drawdown", f"Down {fall:.1f}% from its best of ${best:,.2f}.")
                )


def to_markdown(days: list[Day], title: str = "The analyst's journey") -> str:
    """The story as a document, for reading somewhere that is not this app.

    Quiet days are collapsed into a single line rather than listed. Six
    consecutive entries reading "nothing happened" bury the day something
    did — and "it waited six days" is the more truthful sentence anyway.
    """
    if not days:
        return f"# {title}\n\nNothing has happened yet.\n"

    out = [f"# {title}", ""]
    first, last = days[0].date, days[-1].date
    traded = sum(len(d.opened) for d in days)
    spent = sum(d.research_spent for d in days)
    closing = next((d.equity for d in reversed(days) if d.equity is not None), None)

    out.append(f"{first} to {last} — {len(days)} days, {traded} position(s) opened.")
    if spent:
        out.append(f"Research cost ${spent:,.2f} over that time.")
    if closing is not None:
        out.append(f"The book stands at ${closing:,.2f}.")
    out.append("")

    quiet_run: list[Day] = []

    def flush_quiet() -> None:
        if not quiet_run:
            return
        if len(quiet_run) == 1:
            out.append(f"**{quiet_run[0].date}** — nothing bought or sold.")
        else:
            out.append(
                f"**{quiet_run[0].date} to {quiet_run[-1].date}** — "
                f"{len(quiet_run)} days with nothing bought or sold."
            )
        out.append("")
        quiet_run.clear()

    for day in days:
        if day.quiet and not day.milestones:
            quiet_run.append(day)
            continue
        flush_quiet()

        out.append(f"## {day.date}")
        if day.equity is not None:
            line = f"Book ${day.equity:,.2f}"
            if day.return_pct is not None:
                line += f" ({day.return_pct:+.1f}% against the starting balance)"
            out.append(line + ".")
        for text in day.opened + day.closed:
            # Not str.capitalize(): it lowercases everything after the first
            # letter, which turns GOOG into goog.
            out.append(f"- {text[:1].upper()}{text[1:]}")
        if day.research_spent:
            out.append(f"- Spent ${day.research_spent:,.2f} on research.")
        if day.reasoning:
            # Its own words, quoted rather than paraphrased. A summary here
            # would be this module's opinion of a decision it did not make.
            out.append("")
            out.append(f"> {day.reasoning.strip()}")
        elif day.skipped:
            out.append(f"- Did not run: {day.skipped}")
        for milestone in day.milestones:
            out.append("")
            out.append(f"**{milestone.text}**")
        out.append("")

    flush_quiet()
    return "\n".join(out).rstrip() + "\n"


# Beside the database and the logs, in the data directory that survives a
# rebuild. A story kept only inside a container is a story you lose the first
# time you change an environment variable.
JOURNEY_DIR = str(paths.data_dir() / "journey")

_MONTH_NAMES = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)

_GENERATED_NOTE = (
    "<!-- Generated from the agent's own book. Edits here are overwritten on "
    "the next write; put commentary in JOURNEY.md instead. -->"
)


def month_summary(days: list[Day], budget: float) -> list[str]:
    """The month in four numbers, so a file can be read on its own.

    A monthly post that opens with the 3rd and ends with the 28th tells a
    reader nothing about whether the month was good or bad.
    """
    opened = sum(len(d.opened) for d in days)
    closed = sum(len(d.closed) for d in days)
    spent = sum(d.research_spent for d in days)
    priced = [d for d in days if d.equity is not None]
    lines = [
        f"- **{opened}** position(s) opened, **{closed}** closed",
        f"- **${spent:,.2f}** spent on research",
    ]
    if priced:
        start, end = priced[0].equity, priced[-1].equity
        move = (end / start - 1) * 100 if start else 0.0
        lines.append(f"- Book **${start:,.2f} → ${end:,.2f}** ({move:+.1f}% over the month)")
        lines.append(f"- Against the starting balance: **{(end / budget - 1) * 100:+.1f}%**")
    return lines


def write_month_files(root: str | None = None, budget: float | None = None) -> list[str]:
    """Write one markdown file per month, under a folder per year.

    Regenerated from the book each time rather than appended to. Appending
    would duplicate every day on a re-run, and the file is a derived artifact —
    the book is the source. The header says so, because a generated file that
    looks hand-written invites someone to edit it and lose the work.

    Returns the paths written. Never raises: failing to write the story must
    not fail whatever job was kind enough to ask for it.
    """
    root = root or JOURNEY_DIR
    budget = budget if budget is not None else agent_book.get_budget()
    days = build(budget)
    if not days:
        return []

    by_month: dict[tuple[int, int], list[Day]] = {}
    for day in days:
        by_month.setdefault((day.date.year, day.date.month), []).append(day)

    written: list[str] = []
    for (year, month), month_days in sorted(by_month.items()):
        name = _MONTH_NAMES[month - 1]
        title = f"The analyst's journey — {name} {year}"
        body = to_markdown(month_days, title=title)
        # The summary goes after the title line, where a reader meets it before
        # the day-by-day detail.
        lines = body.split("\n")
        head, rest = lines[:2], lines[2:]
        document = "\n".join(
            head + month_summary(month_days, budget) + ["", _GENERATED_NOTE, ""] + rest
        )
        try:
            folder = os.path.join(root, str(year))
            os.makedirs(folder, exist_ok=True)
            path = os.path.join(folder, f"{month:02d}-{name}.md")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(document)
        except OSError:
            log.exception("Could not write the journey for %s %s", name, year)
            continue
        written.append(path)
    return written
