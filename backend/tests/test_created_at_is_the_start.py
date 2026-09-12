"""``Signal.created_at`` is when the analysis started, for every row.

It meant two things until 2026-09-11: ``db.record_signal`` stored the moment
the run finished, while rows recovered from ``trace_id`` held the moment it
started. An analysis takes about sixteen minutes on this hardware, so the two
readings sat far apart and nothing marked the seam. See the 2026-09-11 entry
in JOURNEY.md.
"""
import datetime
import types

import pytest

from backend.scripts import backfill_signal_timestamps as backfill


START = datetime.datetime(2026, 9, 11, 13, 42, 3)


def _signal(sid, created, trace="20260911T134203-aaaabbbb"):
    return types.SimpleNamespace(id=sid, ticker="INTC", created_at=created, trace_id=trace)


class _Recorder:
    """Stands in for the database, so the script's decisions are visible."""

    def __init__(self, rows):
        self.rows = rows
        self.written = {}

    def get_signals_with_trace(self):
        return self.rows

    def set_signal_created_at(self, signal_id, created_at):
        self.written[signal_id] = created_at


@pytest.fixture
def db(monkeypatch):
    rows = [
        # A backfilled row: already the start, to the second.
        _signal(31, START),
        # A row written by record_signal before this change: start + duration.
        _signal(32, START + datetime.timedelta(seconds=1001)),
        # A row from before the column existed.
        _signal(33, None),
        # Nothing to recover from; must be left exactly as it is.
        _signal(34, START + datetime.timedelta(seconds=990), trace=None),
    ]
    recorder = _Recorder(rows)
    monkeypatch.setattr(backfill, "db", recorder)
    monkeypatch.setattr("sys.argv", ["backfill"])
    return recorder


def test_a_finish_time_is_corrected_to_the_runs_start(db):
    backfill.main()

    assert db.written[32] == START


def test_a_null_row_is_filled_from_its_trace(db):
    backfill.main()

    assert db.written[33] == START


def test_a_row_already_holding_the_start_is_not_rewritten(db):
    """Re-running must be a no-op, so this can be applied twice without
    walking every row's timestamp forward."""
    backfill.main()

    assert 31 not in db.written


def test_a_row_with_no_trace_is_left_alone(db):
    """There is nothing to recover it from, and computing one from
    duration_seconds would put a time in the record nobody observed."""
    backfill.main()

    assert 34 not in db.written


def test_a_dry_run_writes_nothing(db, monkeypatch):
    monkeypatch.setattr("sys.argv", ["backfill", "--dry-run"])

    backfill.main()

    assert db.written == {}


def test_record_signal_stores_the_start_the_caller_supplies(monkeypatch):
    """The start is observed once, in propagate_ticker, and carried down.
    Without this, record_signal stamped its own clock — the finish."""
    from backend.database import db as real_db

    captured = {}

    class _Session:
        def add(self, row):
            captured["row"] = row

        def commit(self):
            pass

        def refresh(self, row):
            row.id = 1

    real_db.record_signal(
        ticker="INTC",
        decision="Hold",
        rationale="",
        price_at_signal=1.0,
        evaluation_date=datetime.date(2026, 9, 25),
        created_at=START,
        _session=_Session(),
    )

    assert captured["row"].created_at == START


def test_record_signal_falls_back_to_now_when_no_start_is_given(monkeypatch):
    """A caller with no run behind it — a test, a replayed final_state — has
    no start to supply, and the record time is the only instant it has."""
    from backend.database import db as real_db

    captured = {}

    class _Session:
        def add(self, row):
            captured["row"] = row

        def commit(self):
            pass

        def refresh(self, row):
            row.id = 1

    real_db.record_signal(
        ticker="INTC",
        decision="Hold",
        rationale="",
        price_at_signal=1.0,
        evaluation_date=datetime.date(2026, 9, 25),
        _session=_Session(),
    )

    assert captured["row"].created_at is not None
