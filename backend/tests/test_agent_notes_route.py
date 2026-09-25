"""The notes page: every note the agent has ever left, pulled out of every
pass's orders and shown in one place, newest first.

Every test here monkeypatches db.get_agent_runs rather than writing real
AgentRun rows — the same discipline test_agent_events_by_month.py uses, since
that function's own tests already cover the real database.
"""
import datetime
import json

import pytest

from backend.database import db
from backend.database.models import AgentRun


def _run(ran_at: str, orders: list[dict] | None = None, **over) -> AgentRun:
    return AgentRun(
        id=over.pop("id", None),
        ran_at=datetime.datetime.fromisoformat(ran_at),
        orders=json.dumps(orders) if orders is not None else None,
        **over,
    )


@pytest.fixture
def fake_runs(monkeypatch):
    def set_runs(runs: list[AgentRun]):
        monkeypatch.setattr(db, "get_agent_runs", lambda limit=None: runs)

    return set_runs


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("PUBLIC_MODE", raising=False)
    from fastapi.testclient import TestClient

    from backend.app import app

    return TestClient(app)


def test_a_note_comes_back_with_its_timestamp_and_reason(client, fake_runs):
    fake_runs([
        _run(
            "2026-09-03T13:35:00",
            id=1,
            orders=[{"side": "note", "reason": "I cannot see sector data."}],
        )
    ])

    response = client.get("/api/agent/notes")

    assert response.status_code == 200
    # A pass note carries none of the review-only fields (2026-09-24): it
    # names no pass, no kind, and nothing it would have done.
    assert response.json() == [
        {
            "id": 1, "ran_at": "2026-09-03T13:35:00Z", "reason": "I cannot see sector data.",
            "source": "pass", "kind": None, "pass_id": None, "would_have_done": None,
        }
    ]


def test_notes_come_back_newest_first(client, fake_runs):
    fake_runs([
        _run("2026-08-15T13:35:00", id=1, orders=[{"side": "note", "reason": "older"}]),
        _run("2026-09-01T13:35:00", id=2, orders=[{"side": "note", "reason": "newer"}]),
    ])

    response = client.get("/api/agent/notes")

    assert [row["reason"] for row in response.json()] == ["newer", "older"]


def test_a_pass_with_no_note_contributes_nothing(client, fake_runs):
    fake_runs([
        _run("2026-09-01T13:35:00", id=1, orders=[{"side": "buy", "ticker": "AAPL", "quantity": 1}]),
        _run("2026-09-02T13:35:00", id=2, orders=[]),
        _run("2026-09-03T13:35:00", id=3),
    ])

    response = client.get("/api/agent/notes")

    assert response.json() == []


def test_a_pass_can_carry_a_note_alongside_a_trade(client, fake_runs):
    """A note rides in the same orders list as everything else the pass did —
    this must pull it out without needing the pass to be note-only."""
    fake_runs([
        _run(
            "2026-09-01T13:35:00",
            id=1,
            orders=[
                {"side": "buy", "ticker": "AAPL", "quantity": 1},
                {"side": "note", "reason": "I need a position-size cap."},
            ],
        )
    ])

    response = client.get("/api/agent/notes")

    assert [row["reason"] for row in response.json()] == ["I need a position-size cap."]


def test_an_empty_note_is_left_out(client, fake_runs):
    """A blank reason is the model filling in the shape, not saying
    something — screen() already drops these before they are ever stored
    (see test_agent_notes.py), but the route stays defensive in case an old
    row somehow carries one."""
    fake_runs([_run("2026-09-01T13:35:00", id=1, orders=[{"side": "note", "reason": ""}])])

    response = client.get("/api/agent/notes")

    assert response.json() == []


def test_no_history_at_all_is_an_empty_list(client, fake_runs):
    fake_runs([])

    response = client.get("/api/agent/notes")

    assert response.json() == []
