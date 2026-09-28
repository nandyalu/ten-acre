"""The public site's data comes from these files now, not a live backend.

Every route this module exports from is monkeypatched to a cheap stand-in —
these tests are about the exporter's own orchestration (which files get
written, that one failure doesn't take the rest down, that the public flag is
always true) rather than re-testing the route handlers themselves, which
already have their own tests.
"""
import json

import pytest

from backend.services import snapshot_export


class _Fake:
    """Stands in for a Pydantic response model."""

    def __init__(self, data):
        self._data = data

    def model_dump(self, mode="json"):
        return self._data


@pytest.fixture(autouse=True)
def snapshot_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshot_export, "OUTPUT_DIR", str(tmp_path))
    return tmp_path


def _read(tmp_path, relative_path):
    return json.loads((tmp_path / relative_path).read_text(encoding="utf-8"))


# --- _safe: one failure never blocks the rest ----------------------------------


def test_safe_logs_and_continues_when_the_builder_raises(snapshot_dir):
    def boom():
        raise RuntimeError("no data today")

    snapshot_export._safe("would_have_failed.json", boom)

    assert not (snapshot_dir / "would_have_failed.json").exists()


def test_safe_writes_the_file_when_the_builder_succeeds(snapshot_dir):
    snapshot_export._safe("ok.json", lambda: _Fake({"a": 1}))
    assert _read(snapshot_dir, "ok.json") == {"a": 1}


# --- export_all: orchestration, with every route stubbed -----------------------


@pytest.fixture
def stub_every_route(monkeypatch):
    """Every function export_all() calls, replaced with a fast, DB-free
    stand-in. Signatures match what export_all() actually calls (including
    the keyword arguments), so a real signature change here would fail the
    test rather than silently exporting nothing."""
    from backend.api.routes import (
        agent as agent_routes,
    )
    from backend.api.routes import digest as digest_routes
    from backend.api.routes import regime as regime_routes
    from backend.api.routes import scorecard as scorecard_routes
    from backend.api.routes import settings as settings_routes
    from backend.api.routes import signals as signals_routes
    from backend.api.routes import tickers as tickers_routes
    from backend.api.routes import watchlist as watchlist_routes
    from backend.database import db

    monkeypatch.setattr(agent_routes, "get_book", lambda: _Fake({"equity": 10_425.28}))
    monkeypatch.setattr(agent_routes, "get_trades", lambda: [])
    monkeypatch.setattr(agent_routes, "get_performance", lambda: _Fake({}))
    monkeypatch.setattr(agent_routes, "get_history", lambda: [])
    monkeypatch.setattr(agent_routes, "get_curve", lambda: [])
    monkeypatch.setattr(agent_routes, "get_unprotected", lambda: [])
    monkeypatch.setattr(agent_routes, "get_notes", lambda: [])
    monkeypatch.setattr(agent_routes, "get_memory", lambda: [])
    monkeypatch.setattr(agent_routes, "get_events", lambda limit=30, month=None: [])
    monkeypatch.setattr(agent_routes, "get_event_months", lambda: [])
    monkeypatch.setattr(agent_routes, "get_journey_entries", lambda days=10, month=None: [])
    monkeypatch.setattr(agent_routes, "get_journey_months", lambda: [])
    monkeypatch.setattr(digest_routes, "get_digest", lambda: _Fake({}))
    monkeypatch.setattr(regime_routes, "get_regime", lambda: _Fake({}))
    monkeypatch.setattr(scorecard_routes, "get_scorecard", lambda ticker=None: _Fake({}))
    monkeypatch.setattr(scorecard_routes, "get_calibration", lambda: _Fake({}))
    monkeypatch.setattr(signals_routes, "list_signals", lambda **kw: [])
    monkeypatch.setattr(watchlist_routes, "list_watchlist", lambda: [])
    monkeypatch.setattr(watchlist_routes, "get_candidates", lambda: [])
    monkeypatch.setattr(
        settings_routes, "get_settings", lambda: _Fake({"public": False, "llm_model": "x"})
    )
    monkeypatch.setattr(tickers_routes, "list_tickers", lambda: [])
    monkeypatch.setattr(db, "get_watchlist", lambda: [])


def test_export_all_writes_the_expected_files(snapshot_dir, stub_every_route):
    snapshot_export.export_all()

    assert _read(snapshot_dir, "agent.json") == {"equity": 10_425.28}
    assert _read(snapshot_dir, "watchlist.json") == []


def test_settings_json_always_reports_public_true(snapshot_dir, stub_every_route):
    """The exporter always runs on the private container, which has no
    PUBLIC_MODE of its own — settings.json must still say public=True,
    because it describes the published artifact, not the exporting process.
    Without this, the frontend's isPublic checks (the Settings nav link, the
    exits-arm button) would show controls on a site with no backend to act
    on them."""
    snapshot_export.export_all()

    settings = _read(snapshot_dir, "settings.json")
    assert settings["public"] is True
    assert settings["llm_model"] == "x"


def test_settings_json_carries_when_the_snapshot_was_taken(snapshot_dir, stub_every_route):
    """The published site is a static export refreshed on a loop, so a reader
    cannot otherwise tell a quiet afternoon from a publisher that stopped
    three days ago — the numbers look equally current either way.

    It rides on settings.json because the site already fetches that on load,
    and it is absent from the live API on purpose: on the private container
    the data is live and there is nothing to date."""
    import datetime

    snapshot_export.export_all()

    stamp = _read(snapshot_dir, "settings.json")["snapshot_generated_at"]
    when = datetime.datetime.fromisoformat(stamp)

    assert when.tzinfo is not None, "a bare local time is unreadable to a browser"
    age = (datetime.datetime.now(datetime.timezone.utc) - when).total_seconds()
    assert 0 <= age < 60, f"stamped {age}s ago — it should name this run"


def test_one_failing_export_does_not_block_the_others(snapshot_dir, stub_every_route, monkeypatch):
    from backend.api.routes import digest as digest_routes

    def broken():
        raise RuntimeError("digest service is down")

    monkeypatch.setattr(digest_routes, "get_digest", broken)

    snapshot_export.export_all()

    assert not (snapshot_dir / "digest.json").exists()
    assert (snapshot_dir / "agent.json").exists()
    assert (snapshot_dir / "settings.json").exists()


# --- per-ticker and per-signal files ---------------------------------------


def test_per_ticker_files_are_named_by_ticker(snapshot_dir, stub_every_route, monkeypatch):
    from backend.api.routes import tickers as tickers_routes
    from backend.database import db

    monkeypatch.setattr(db, "get_watchlist", lambda: ["AAPL"])
    monkeypatch.setattr(tickers_routes, "get_ticker", lambda t: _Fake({"ticker": t}))
    monkeypatch.setattr(
        tickers_routes, "get_ticker_events", lambda t, days=180: _Fake({"ticker": t, "days": days})
    )

    snapshot_export.export_all()

    assert _read(snapshot_dir, "tickers/AAPL.json") == {"ticker": "AAPL"}
    # 365: the widest day-range button, so the public page can slice the
    # returned bars client-side for every narrower range too.
    assert _read(snapshot_dir, "tickers/AAPL/events.json") == {"ticker": "AAPL", "days": 365}


# --- the Decisions page's per-month files -----------------------------------


def test_per_month_event_files_are_named_without_the_dash(snapshot_dir, stub_every_route, monkeypatch):
    from backend.api.routes import agent as agent_routes

    monkeypatch.setattr(agent_routes, "get_event_months", lambda: ["2026-09", "2026-08"])
    monkeypatch.setattr(
        agent_routes, "get_events", lambda limit=30, month=None: _Fake({"month": month})
    )

    snapshot_export.export_all()

    assert _read(snapshot_dir, "agent_events_months.json") == ["2026-09", "2026-08"]
    assert _read(snapshot_dir, "agent_events_202609.json") == {"month": "2026-09"}
    assert _read(snapshot_dir, "agent_events_202608.json") == {"month": "2026-08"}


# --- the Journal page's per-month files -------------------------------------


def test_per_month_journal_files_are_named_without_the_dash(snapshot_dir, stub_every_route, monkeypatch):
    from backend.api.routes import agent as agent_routes

    monkeypatch.setattr(agent_routes, "get_journey_months", lambda: ["2026-09", "2026-08"])
    monkeypatch.setattr(
        agent_routes,
        "get_journey_entries",
        lambda days=10, month=None: _Fake({"month": month}),
    )

    snapshot_export.export_all()

    assert _read(snapshot_dir, "journal_months.json") == ["2026-09", "2026-08"]
    assert _read(snapshot_dir, "journal_202609.json") == {"month": "2026-09"}
    assert _read(snapshot_dir, "journal_202608.json") == {"month": "2026-08"}


def test_no_months_at_all_still_writes_an_empty_index(snapshot_dir, stub_every_route, monkeypatch):
    from backend.api.routes import agent as agent_routes

    monkeypatch.setattr(agent_routes, "get_event_months", lambda: [])

    snapshot_export.export_all()

    assert _read(snapshot_dir, "agent_events_months.json") == []


def test_per_signal_files_are_named_by_id(snapshot_dir, stub_every_route, monkeypatch):
    from backend.api.routes import signals as signals_routes

    fake_signal = _Fake({"id": 7})
    fake_signal.id = 7  # list_signals() returns model instances, not dicts

    monkeypatch.setattr(signals_routes, "list_signals", lambda **kw: [fake_signal])
    monkeypatch.setattr(signals_routes, "get_signal", lambda signal_id: _Fake({"id": signal_id}))

    snapshot_export.export_all()

    assert _read(snapshot_dir, "signals/7.json") == {"id": 7}
