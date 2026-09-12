"""The research page lists analyses newest first, within a day as well as across days.

Several analyses a day is the normal case here, not an edge one: eight on
2026-09-10 and nine on 2026-09-08 in the live book. ``signal_date`` is a
calendar date and cannot separate them, so the page showed 2026-09-10's 06:02
analysis above its 16:57 one.

The three filter branches disagreed with each other as well, which is why each
is pinned separately here.
"""
import types

import pytest

from backend.api.routes import signals as signals_route


def _signal(sid, ticker, date, created=None):
    return types.SimpleNamespace(
        id=sid,
        ticker=ticker,
        signal_date=date,
        created_at=created,
        decision="Hold",
        rationale="",
        time_horizon_text=None,
        price_target=None,
        price_at_signal=1.0,
        evaluation_date=date,
        price_at_evaluation=None,
        outcome=None,
        evaluated_at=None,
        message_id=None,
        benchmark_price_at_signal=None,
        benchmark_price_at_evaluation=None,
        alpha_pct=None,
        outcome_vs_benchmark=None,
        price_target_hit=None,
        horizon=None,
        model=None,
        trigger=None,
        duration_seconds=None,
        prompt_tokens=None,
        completion_tokens=None,
        llm_calls=None,
        entry_price=None,
        stop_loss=None,
        win_probability=None,
        risk_reward=None,
        expected_value_r=None,
    )


# One day, three analyses, deliberately supplied oldest first — which is the
# order the live query actually returned them in.
ONE_DAY = [
    _signal(33, "AVGO", "2026-09-10", "2026-09-10 06:02:00.851667"),
    _signal(35, "CRWV", "2026-09-10", "2026-09-10 13:58:08.810767"),
    _signal(40, "CRWV", "2026-09-10", "2026-09-10 16:57:44.361768"),
]


@pytest.fixture
def stored(monkeypatch):
    monkeypatch.setattr(
        signals_route.db,
        "get_recent_signals",
        lambda ticker=None, limit=10, by_time=False: list(ONE_DAY),
    )
    monkeypatch.setattr(signals_route.db, "get_pending_signals", lambda as_of: list(ONE_DAY))
    monkeypatch.setattr(signals_route.db, "get_resolved_signals", lambda ticker=None: list(ONE_DAY))


def test_the_default_list_puts_the_days_newest_analysis_first(stored):
    assert [s.id for s in signals_route.list_signals()] == [40, 35, 33]


def test_the_pending_filter_orders_too(stored):
    """`get_pending_signals` has no ORDER BY at all, so its rows arrived in
    whatever order the table held them."""
    assert [s.id for s in signals_route.list_signals(status="pending")] == [40, 35, 33]


def test_the_resolved_filter_no_longer_returns_the_oldest_rows(stored):
    """`get_resolved_signals` orders ascending, and the route sliced that
    before sorting — so asking for the newest 2 resolved analyses returned the
    2 oldest."""
    assert [s.id for s in signals_route.list_signals(status="resolved", limit=2)] == [40, 35]


def test_rows_sharing_a_timestamp_still_have_a_stable_order():
    """The retired 11:00 sweep dispatched seven analyses in one second, and
    all seven share a created_at to the microsecond."""
    from backend.services import signals as signals_service

    same = [
        _signal(23, "MARA", "2026-09-08", "2026-09-08 11:00:00.000000"),
        _signal(29, "HPE", "2026-09-08", "2026-09-08 11:00:00.000000"),
        _signal(26, "TSLA", "2026-09-08", "2026-09-08 11:00:00.000000"),
    ]
    assert [s.id for s in signals_service.newest_first(same)] == [29, 26, 23]


def test_a_row_with_no_timestamp_sorts_below_one_that_has_it():
    """`created_at` is null on rows with no trace_id to recover it from. Such
    a row must not sort above a dated one from the same day and displace it."""
    from backend.services import signals as signals_service

    rows = [
        _signal(10, "AAA", "2026-09-08", None),
        _signal(11, "BBB", "2026-09-08", "2026-09-08 11:00:00.000000"),
    ]
    assert [s.id for s in signals_service.newest_first(rows)] == [11, 10]


def test_the_limit_keeps_a_days_newest_analyses_not_its_oldest():
    """The LIMIT has to apply to correctly-ordered rows, because sorting
    afterwards cannot recover a row the query never fetched.

    Ordered by `signal_date` alone, a day that holds more analyses than the
    limit leaves room for hands back whichever rows the table offers first —
    the day's oldest. Measured on the live book: `limit=10` returned
    2026-09-10's four oldest and dropped its seven newest outright.

    An in-memory database rather than a fake, because the ordering under test
    is in the SQL, and conftest deliberately stops a test reaching the real one.
    """
    import datetime as dt

    from sqlmodel import Session, SQLModel, create_engine

    from backend.database import db as real_db
    from backend.database.models import Signal as SignalRow

    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    day = dt.date(2026, 9, 10)
    with Session(engine) as session:
        # Eleven analyses on one calendar day, inserted oldest first so row
        # order and time order agree — which is what makes the bug visible.
        for i in range(11):
            session.add(
                SignalRow(
                    ticker=f"T{i:02d}",
                    signal_date=day,
                    created_at=dt.datetime(2026, 9, 10, 6 + i, 0, 0),
                    decision="Hold",
                    rationale="",
                    price_at_signal=1.0,
                    evaluation_date=day,
                )
            )
        session.commit()

        newest = real_db.get_recent_signals(limit=4, by_time=True, _session=session)
        assert [s.ticker for s in newest] == ["T10", "T09", "T08", "T07"]
