"""The Decisions page's month timeline: one dot per month, the newest month
loaded up front, everything older fetched only when a reader opens it.

Every test here monkeypatches db.get_agent_runs rather than writing real
AgentRun rows — that function's own tests already cover the real database,
and a fabricated decision pass has no business landing in the actual
project's history the way a fake ticker in a price-cache test would."""
import datetime

import pytest

from backend.database import db
from backend.database.models import AgentRun


def _run(ran_at: str, **over) -> AgentRun:
    return AgentRun(id=over.pop("id", None), ran_at=datetime.datetime.fromisoformat(ran_at), **over)


@pytest.fixture
def fake_runs(monkeypatch):
    def set_runs(runs: list[AgentRun]):
        monkeypatch.setattr(db, "get_agent_runs", lambda limit=None, **_: runs)

    return set_runs


def test_months_come_back_newest_first(fake_runs):
    fake_runs([
        _run("2026-08-15T13:35:00"),
        _run("2026-09-01T13:35:00"),
        _run("2026-09-08T13:35:00"),
    ])

    assert db.get_agent_run_months() == ["2026-09", "2026-08"]


def test_a_month_with_two_passes_counts_once(fake_runs):
    fake_runs([_run("2026-09-01T13:35:00"), _run("2026-09-08T13:35:00")])

    assert db.get_agent_run_months() == ["2026-09"]


def test_a_naive_timestamp_is_treated_as_utc(fake_runs):
    """Every pass is scheduled in UTC, and this column comes back naive from
    SQLite in places — a month boundary must not silently shift because one
    row happened to carry no tzinfo and another did."""
    fake_runs([
        _run("2026-09-01T13:35:00"),  # naive
        _run("2026-09-08T13:35:00+00:00"),  # aware
    ])

    assert db.get_agent_run_months() == ["2026-09"]


def test_get_runs_for_month_keeps_only_that_month(fake_runs):
    august = _run("2026-08-30T13:35:00", reasoning="august pass")
    september = _run("2026-09-01T13:35:00", reasoning="september pass")
    fake_runs([august, september])

    result = db.get_agent_runs_for_month("2026-09")

    assert result == [september]


def test_get_runs_for_month_preserves_oldest_first_ordering(fake_runs):
    """Matches get_agent_runs's own convention, since the route reverses
    whichever of the two it called to get newest-first for the page."""
    first = _run("2026-09-01T13:35:00")
    second = _run("2026-09-08T13:35:00")
    fake_runs([first, second])

    assert db.get_agent_runs_for_month("2026-09") == [first, second]


def test_a_month_with_no_passes_is_an_empty_list(fake_runs):
    fake_runs([_run("2026-09-01T13:35:00")])

    assert db.get_agent_runs_for_month("2026-01") == []


def test_no_history_at_all_is_an_empty_list_of_months(fake_runs):
    fake_runs([])

    assert db.get_agent_run_months() == []


# --- the routes the Decisions page actually calls -------------------------


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("PUBLIC_MODE", raising=False)
    from fastapi.testclient import TestClient

    from backend.app import app

    return TestClient(app)


def test_the_months_route_lists_every_month_newest_first(client, fake_runs):
    fake_runs([_run("2026-08-15T13:35:00"), _run("2026-09-01T13:35:00")])

    response = client.get("/api/agent/events/months")

    assert response.status_code == 200
    assert response.json() == ["2026-09", "2026-08"]


def test_the_events_route_with_a_month_returns_only_that_month(client, fake_runs):
    fake_runs([
        _run("2026-08-30T13:35:00", id=1, reasoning="august"),
        _run("2026-09-01T13:35:00", id=2, reasoning="september"),
    ])

    response = client.get("/api/agent/events", params={"month": "2026-09"})

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["reasoning"] == "september"


def test_the_events_route_without_a_month_keeps_the_old_recent_behaviour(client, monkeypatch):
    """The Overview page's small recent-activity feed calls this with no
    month at all — that must keep working exactly as before, since the new
    parameter is additive, not a replacement."""
    monkeypatch.setattr(db, "get_agent_runs", lambda limit=None, **_: [_run("2026-09-01T13:35:00", id=1)])

    response = client.get("/api/agent/events", params={"limit": 5})

    assert response.status_code == 200
    assert len(response.json()) == 1
