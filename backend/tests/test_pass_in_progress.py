"""The decision pass in progress, as the dashboard sees it (2026-10-01).

``run_once`` holds a record of the pass while it runs and clears it when the
pass ends, by any path. ``/api/agent/pass`` serves it. Nothing reads it back
into a prompt, and the static snapshot never writes it.
"""
import datetime

import pytest

from backend.services import agent, analysis


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("PUBLIC_MODE", raising=False)
    from fastapi.testclient import TestClient

    from backend.app import app

    return TestClient(app)


def test_no_pass_reads_as_not_running(client):
    assert client.get("/api/agent/pass").json() == {
        "running": False, "started_at": None, "woke_because": None,
        "turn": None, "doing": None, "research": {},
    }


def test_a_pass_is_visible_while_it_runs_and_gone_after(client, monkeypatch):
    seen = {}

    def fake_pass(woke_because, should_stop):
        agent._pass_doing("Asking the model", turn=2)
        seen["body"] = client.get("/api/agent/pass").json()
        return "run"

    monkeypatch.setattr(agent, "_run_once", fake_pass)
    started = datetime.datetime(2026, 10, 1, 14, 5, tzinfo=datetime.timezone.utc)
    monkeypatch.setattr(analysis, "_in_flight", {"AMZN": started})

    assert agent.run_once("A resting stop or target closed one of your positions.") == "run"

    body = seen["body"]
    assert body["running"] is True
    assert body["turn"] == 2 and body["doing"] == "Asking the model"
    assert body["woke_because"].startswith("A resting stop")
    assert body["research"] == {"AMZN": "2026-10-01T14:05:00Z"}
    assert agent.current_pass() is None
    assert client.get("/api/agent/pass").json()["running"] is False


def test_a_pass_that_raises_is_cleared_too(monkeypatch):
    def boom(woke_because, should_stop):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(agent, "_run_once", boom)

    with pytest.raises(RuntimeError):
        agent.run_once()

    assert agent.current_pass() is None


def test_the_snapshot_never_exports_it():
    """The public site is a record. A pass in progress describes this second."""
    from pathlib import Path

    exporter = Path(agent.__file__).with_name("snapshot_export.py").read_text()
    assert "get_pass_in_progress" not in exporter
