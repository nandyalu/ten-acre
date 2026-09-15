"""Handing the agent an analysis it already paid for.

Both bugs pinned here were found by running this against the live database
rather than by reading it, which is the only reason they were found at all.
"""
import types

import pytest

from backend.services import analysis_reader


def _signal(sid, ticker, date, decision, created=None, rationale="reasoning here"):
    return types.SimpleNamespace(
        id=sid, ticker=ticker, signal_date=date, decision=decision,
        created_at=created, rationale=rationale,
    )


@pytest.fixture
def stored(monkeypatch):
    """Two INTC analyses on one day, plus an older one. No analyst reports on
    record for any of them, matching a signal written before 2026-09-15."""
    rows = [
        _signal(30, "INTC", "2026-09-08", "Overweight", "2026-09-08 19:06:46"),
        _signal(31, "INTC", "2026-09-08", "Hold", "2026-09-08 19:18:04"),
        _signal(20, "INTC", "2026-09-02", "Buy", "2026-09-02 14:00:00"),
    ]
    monkeypatch.setattr(
        analysis_reader.db, "get_recent_signals",
        lambda ticker=None, limit=10: [r for r in rows if r.ticker == ticker],
    )
    monkeypatch.setattr(analysis_reader.db, "get_signal_reports", lambda signal_id: {})
    return rows


def test_the_newest_of_a_day_wins_a_tie(stored):
    """`db.get_recent_signals` orders by `signal_date` alone, which is a
    calendar date, so two analyses of one ticker on one day come back in
    whatever order the rows sit in. Asking for INTC's 2026-09-08 analysis
    returned the 19:06 one over the 19:18 one until this sorted locally."""
    assert "19:18" in analysis_reader.read("INTC", "2026-09-08")


def test_only_same_day_siblings_are_counted(stored):
    """With no date given, the search holds every analysis of the ticker.
    Counting those announced '2 other analyses that day' for a day that had
    none."""
    newest = analysis_reader.read("INTC")

    assert "2026-09-08" in newest
    assert "1 other analysis that day is not shown" in newest


def test_a_day_with_siblings_says_how_many(stored):
    assert "1 other analysis that day is not shown" in analysis_reader.read("INTC", "2026-09-08")


def test_a_day_with_one_analysis_mentions_no_others(stored):
    assert "not shown" not in analysis_reader.read("INTC", "2026-09-02")


def test_a_missing_analysis_says_so_and_names_the_way_to_get_one(stored):
    """Never silent. The agent has spent its one follow-up turn asking, so an
    empty answer would leave it waiting for something that never arrives."""
    reply = analysis_reader.read("INTC", "2026-01-01")

    assert "no analysis of INTC from 2026-01-01" in reply
    assert '"research"' in reply


def test_an_unparseable_date_explains_the_format(stored):
    reply = analysis_reader.read("INTC", "last tuesday")

    assert "YYYY-MM-DD" in reply


def test_a_long_rationale_is_trimmed(monkeypatch):
    """The whole stored report runs to about 23,000 characters. Twelve of
    those would bury the rules block in a prompt that runs to 4,600."""
    long_one = _signal(1, "AAA", "2026-09-10", "Buy", rationale="para\n\n" * 2000)
    monkeypatch.setattr(analysis_reader.db, "get_recent_signals", lambda **k: [long_one])
    monkeypatch.setattr(analysis_reader.db, "get_signal_reports", lambda signal_id: {})

    reply = analysis_reader.read("AAA")

    assert len(reply) < analysis_reader._MAX_CHARS + 200
    assert "truncated" in reply


def test_an_analysis_with_no_reasoning_says_that_rather_than_nothing(monkeypatch):
    bare = _signal(1, "AAA", "2026-09-10", "Hold", rationale="")
    monkeypatch.setattr(analysis_reader.db, "get_recent_signals", lambda **k: [bare])
    monkeypatch.setattr(analysis_reader.db, "get_signal_reports", lambda signal_id: {})

    assert "recorded no reasoning" in analysis_reader.read("AAA")


def test_naming_no_ticker_is_answered_not_ignored():
    assert "named no ticker" in analysis_reader.read("")


def test_a_read_carries_a_short_take_from_each_analyst(monkeypatch):
    """The four analyst reports never reached the agent before 2026-09-15 —
    only the Rating and the rationale did. A read now adds a trimmed excerpt
    of each, so the agent can check the verdict against the evidence."""
    signal = _signal(1, "AAA", "2026-09-15", "Buy")
    monkeypatch.setattr(analysis_reader.db, "get_recent_signals", lambda **k: [signal])
    monkeypatch.setattr(
        analysis_reader.db, "get_signal_reports",
        lambda signal_id: {
            "market_report": "FINAL TRANSACTION PROPOSAL: **BUY** — momentum is strong.",
            "sentiment_report": "**Overall Sentiment:** Mixed.",
            "news_report": "A supplier deal was announced this week.",
            "fundamentals_report": "Margins expanded year over year.",
        },
    )

    reply = analysis_reader.read("AAA")

    assert "Each analyst's own report, in short:" in reply
    assert "Market — FINAL TRANSACTION PROPOSAL" in reply
    assert "Sentiment — **Overall Sentiment:** Mixed." in reply
    assert "News — A supplier deal was announced this week." in reply
    assert "Fundamentals — Margins expanded year over year." in reply


def test_a_read_with_no_analyst_reports_omits_the_section(monkeypatch):
    """An analysis run before 2026-09-15, or one whose report rows never
    saved, has nothing to show here — the section is left out rather than
    printed empty."""
    signal = _signal(1, "AAA", "2026-09-10", "Buy")
    monkeypatch.setattr(analysis_reader.db, "get_recent_signals", lambda **k: [signal])
    monkeypatch.setattr(analysis_reader.db, "get_signal_reports", lambda signal_id: {})

    assert "Each analyst's own report" not in analysis_reader.read("AAA")


def test_a_missing_section_is_skipped_not_shown_blank(monkeypatch):
    """One analyst's report row failing to save should not blank the other
    three, and should not print an empty line for the one that is missing."""
    signal = _signal(1, "AAA", "2026-09-15", "Buy")
    monkeypatch.setattr(analysis_reader.db, "get_recent_signals", lambda **k: [signal])
    monkeypatch.setattr(
        analysis_reader.db, "get_signal_reports",
        lambda signal_id: {"market_report": "Uptrend intact."},
    )

    reply = analysis_reader.read("AAA")

    assert "Market — Uptrend intact." in reply
    assert "Sentiment —" not in reply
