"""Handing the agent an analysis it has already paid for.

The prompt shows one line per signal — the decision and the levels — and never
the reasoning. That is enough to rank two Buys and not enough to tell a flat
Hold from one that means "keep a fifth of the position and defend it below
102.70". Both reach the agent as the word *Hold*.

**Free, because the agent already bought this.** The $0.05 charge exists so
that choosing what to *study* costs something. Re-reading what it already owns
teaches nothing about that choice, so charging for it would be a toll rather
than a price.

**Resolution is by ticker and an optional date**, because those are what the
prompt already shows and what the agent can therefore name. A date is not
unique — INTC has two analyses on 2026-09-08 and SMCI three on 2026-09-04 — so
the newest of a day wins and the reply says how many others there were. Reading
the wrong one and being told is better than naming one it cannot see.

**Since 2026-09-15, a read also carries a short take from each of the four
analysts** (market, sentiment, news, fundamentals) — see JOURNEY.md. Before
this, those four reports were computed and stored for every analysis and never
reached the agent at all: it traded on the Rating and the Executive Summary
alone, with no way to check either against the evidence behind it. A missing
section (an analysis run before this date, or one whose report rows never
saved) is left out rather than shown as empty.
"""
import datetime
import logging

from backend.database import db
from backend.services import signals as signals_service
from backend.database.models import Signal

log = logging.getLogger("trading-experiment.analysis_reader")

# Enough for the Rating and the Executive Summary, which is where the
# actionable part of a rationale sits. The whole stored report runs to about
# 23,000 characters — roughly 5,750 tokens — and twelve of those would bury
# the rules block in a prompt that currently runs to 4,600.
_MAX_CHARS = 1400

# How far back a read may reach. Deliberately wider than the twelve signals the
# prompt shows: "the analysis I bought on" is usually older than that, and it
# is the comparison this exists for.
_SEARCH_LIMIT = 200

# Per analyst section, not per read: four of these plus the rationale above
# stays well under the twelve-times figure that the module docstring warns
# against. Each report runs 3,000-8,000 characters and opens with its own
# title, date and instrument lines before any real content, so this is short
# enough to trim that boilerplate rather than deliver it whole, and does not
# try to be cleverer than that — see the 2026-09-15 JOURNEY.md entry for why a
# plain trim was chosen over parsing out a "verdict" line per report type.
_SECTION_MAX_CHARS = 500

# report_type (backend/database/models.py SignalReport) to the label shown
# beside it, in the order the agent reads them.
_ANALYST_SECTIONS = (
    ("market_report", "Market"),
    ("sentiment_report", "Sentiment"),
    ("news_report", "News"),
    ("fundamentals_report", "Fundamentals"),
)


def _parse_date(raw) -> datetime.date | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return datetime.date.fromisoformat(text[:10])
    except ValueError:
        return None


def _trim(text: str, max_chars: int = _MAX_CHARS) -> str:
    """Cut on a paragraph boundary when there is one nearby, so the reply does
    not end mid-sentence."""
    body = (text or "").strip()
    if len(body) <= max_chars:
        return body
    cut = body.rfind("\n\n", 0, max_chars)
    if cut < max_chars // 2:
        cut = max_chars
    return body[:cut].rstrip() + "\n[...truncated]"


def _analyst_snapshot(signal_id: int) -> str:
    """A short take from each of the four analyst reports, or "" when none are
    on record — an analysis run before 2026-09-15, or a report row that never
    saved. Not shown as an empty section: nothing to read is not the same as
    the analysts having nothing to say."""
    reports = db.get_signal_reports(signal_id)
    lines = []
    for report_type, label in _ANALYST_SECTIONS:
        content = _trim(reports.get(report_type, ""), _SECTION_MAX_CHARS)
        if content:
            lines.append(f"{label} — {content}")
    if not lines:
        return ""
    return "Each analyst's own report, in short:\n" + "\n\n".join(lines)


def _newest_first(signals: list[Signal]) -> list[Signal]:
    """Sorted so the head is genuinely the newest.

    ``db.get_recent_signals`` orders by ``signal_date`` alone, which is a
    calendar date, so two analyses of the same ticker on the same day come
    back in whatever order the rows happen to sit in — and asking for INTC's
    2026-09-08 analysis returned the 19:06 one over the 19:18 one.

    One copy of the key, shared with the research page, which had the same
    fault for the same reason.
    """
    return signals_service.newest_first(signals)


def _matching(ticker: str, on: datetime.date | None) -> list[Signal]:
    found = _newest_first(db.get_recent_signals(ticker=ticker, limit=_SEARCH_LIMIT))
    if on is None:
        return found
    return [s for s in found if str(s.signal_date)[:10] == on.isoformat()]


def read(ticker: str, on=None) -> str:
    """The rationale of one analysis, as a block for the next prompt.

    Always returns something a model can act on, including when nothing
    matched — a silent miss would leave the agent waiting for an answer that
    never comes, and it has already spent its one extra turn asking.
    """
    ticker = str(ticker or "").upper().strip()
    if not ticker:
        return "You asked to read an analysis but named no ticker."

    wanted = _parse_date(on)
    if on and wanted is None:
        return (
            f"You asked for {ticker}'s analysis of {str(on)!r}, which is not a date "
            "I can read. Use YYYY-MM-DD, or name no date for the newest."
        )

    found = _matching(ticker, wanted)
    if not found:
        when = f" from {wanted.isoformat()}" if wanted else ""
        return (
            f"There is no analysis of {ticker}{when} on record. "
            'Use side "research" to commission one.'
        )

    # Newest first out of the query, so the head is the newest of the day when
    # a date was given and the newest overall when it was not.
    signal = found[0]
    stamp = str(signal.signal_date)[:10]
    # Same-day siblings only. With no date given, `found` holds every analysis
    # of the ticker, and counting those announced "3 other analyses that day"
    # for a day that had one.
    others = sum(1 for s in found if str(s.signal_date)[:10] == stamp) - 1

    header = f"{ticker}'s analysis of {stamp} said {signal.decision}"
    created = getattr(signal, "created_at", None)
    if created:
        header += f" (run at {str(created)[11:16]} UTC)"
    if others == 1:
        header += ". 1 other analysis that day is not shown"
    elif others > 1:
        header += f". {others} other analyses that day are not shown"

    body = _trim(signal.rationale or "")
    snapshot = _analyst_snapshot(signal.id)
    if not body:
        reply = f"{header}. It recorded no reasoning."
    else:
        reply = f"{header}:\n{body}"
    return f"{reply}\n\n{snapshot}" if snapshot else reply


def describe(readings: list[str]) -> list[str]:
    """The prompt section for what the agent asked to read.

    Headed so the agent does not mistake it for information that arrived on
    its own — it asked for this, and the next answer is the one that counts.
    """
    if not readings:
        return []
    lines = ["What you asked to read:", ""]
    for text in readings:
        lines += [text, ""]
    return lines
