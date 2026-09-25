"""Renders every public read page's data to static JSON, for Cloudflare Pages.

The public site used to be a second live copy of this app (``PUBLIC_MODE=1``),
kept read-only by a middleware check on the HTTP method. That still let one
real gap through: a read request's own database side effect (a price-cache
write in ``positions.get_current_price``) was not a request the middleware
ever saw. This module removes the live backend from the public path instead
of tightening the guard further — there is nothing here for a future bug like
that to hide in, because there is no server on the other end at all.

Every function below calls the *same* route handler the live private
dashboard already calls (``backend/api/routes/*.py``) — not a reimplementation
of its logic — and writes the handler's return value to a JSON file under
``OUTPUT_DIR``. Those handlers are plain functions with no FastAPI request
dependency, the same way every job in ``backend/tasks/scheduler.py`` already
calls service-layer functions directly, so calling them here needs no HTTP
round trip and can never drift from what the live route returns.

Never raises. A scheduled export that failed loudly would take the whole job
down over one bad ticker; ``_safe`` catches and logs instead, the same
discipline ``journey.write_month_files`` uses for the same reason.
"""
import datetime
import json
import logging
import os
from typing import Any, Callable

from backend import paths
from backend.api.routes import agent as agent_routes
from backend.api.routes import digest as digest_routes
from backend.api.routes import regime as regime_routes
from backend.api.routes import scorecard as scorecard_routes
from backend.api.routes import settings as settings_routes
from backend.api.routes import signals as signals_routes
from backend.api.routes import tickers as tickers_routes
from backend.api.routes import watchlist as watchlist_routes
from backend.database import db

log = logging.getLogger("ten-acre.snapshot_export")

# Beside journey/, in the data directory that survives a rebuild — this is a
# generated artifact, not application state, but it still has to survive a
# redeploy between one export and the next.
OUTPUT_DIR = str(paths.data_dir() / "public_snapshot")

# Far above anything this experiment will produce. Big enough to mean "every
# signal there is" without the exporter needing to know the true count.
_ALL_SIGNALS_LIMIT = 5000


def _dump(model) -> Any:
    """A Pydantic response model (or a plain JSON-safe value) to a JSON-ready
    value. Route handlers return either a model, a list of models, or (for
    the watchlist) a bare list of strings — all three fall out of this the
    same way."""
    if isinstance(model, list):
        return [_dump(item) for item in model]
    if hasattr(model, "model_dump"):
        return model.model_dump(mode="json")
    return model


def _write_json(relative_path: str, payload: Any) -> None:
    path = os.path.join(OUTPUT_DIR, relative_path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, default=str)


def _safe(relative_path: str, build: Callable[[], Any]) -> None:
    try:
        _write_json(relative_path, _dump(build()))
    except Exception:
        log.exception("Snapshot export failed for %s", relative_path)


def export_all() -> None:
    """Regenerate every public snapshot file. Safe to call on any schedule —
    each file is independent, so one failure never blocks the rest."""
    # Stamped at the start rather than when settings.json happens to be
    # written, so the time names the run rather than a file's position in it.
    # The published site is a static export refreshed on a loop, and without
    # this a reader has no way to tell a quiet afternoon from a publisher that
    # stopped three days ago — the numbers look equally current either way.
    started = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)

    _safe("agent.json", agent_routes.get_book)
    _safe("agent_trades.json", agent_routes.get_trades)
    _safe("agent_performance.json", agent_routes.get_performance)
    _safe("agent_history.json", agent_routes.get_history)
    _safe("agent_curve.json", agent_routes.get_curve)
    _safe("agent_unprotected.json", agent_routes.get_unprotected)
    _safe("agent_notes.json", agent_routes.get_notes)
    _safe("agent_system-prompt.json", agent_routes.get_system_prompt)
    # One file, not one per month: a review is short beside a pass, and one a
    # day is a small payload for months.
    _safe("agent_reflections.json", lambda: agent_routes.get_reflections(limit=200))
    # The Overview page's small recent-activity feed — unaffected by the
    # month files below, which are for the Decisions page's own timeline.
    _safe("agent_events.json", lambda: agent_routes.get_events(limit=200))
    _export_agent_events_by_month()
    # No page calls this bare any more — journal-view.ts moved to the
    # month files below, the same way decisions-view.ts did for events —
    # but the route itself still supports it, so this stays as a small,
    # cheap fallback file rather than dead weight to remove.
    _safe("journey_entries.json", lambda: agent_routes.get_journey_entries(days=10))
    _export_journal_by_month()

    _safe("digest.json", digest_routes.get_digest)
    _safe("regime.json", regime_routes.get_regime)
    _safe("scorecard.json", lambda: scorecard_routes.get_scorecard(None))
    _safe("calibration.json", scorecard_routes.get_calibration)

    # Three files, not one filtered client-side: "pending" and "resolved" go
    # through different database queries (db.get_pending_signals /
    # get_resolved_signals), not a simple filter over "recent" — baking all
    # three separately means the static /research page reproduces exactly
    # what the three live tabs return today, with no risk of the two falling
    # out of sync with a client-side guess at the filtering rule.
    _safe("signals_all.json", lambda: signals_routes.list_signals(limit=_ALL_SIGNALS_LIMIT))
    _safe(
        "signals_pending.json",
        lambda: signals_routes.list_signals(status="pending", limit=_ALL_SIGNALS_LIMIT),
    )
    _safe(
        "signals_resolved.json",
        lambda: signals_routes.list_signals(status="resolved", limit=_ALL_SIGNALS_LIMIT),
    )
    _export_signal_details()

    _safe("watchlist.json", watchlist_routes.list_watchlist)
    _safe("watchlist_candidates.json", watchlist_routes.get_candidates)

    # settings.json always reports public=True, regardless of this
    # container's own PUBLIC_MODE — the exporter runs on the private
    # container so it can reach live data, but the file it writes describes
    # the published artifact, not the process that built it. This is what
    # the frontend's existing isPublic checks (the Settings nav link, the
    # exits-arm button) read to hide themselves on the public build.
    # `snapshot_generated_at` rides here rather than in a file of its own so
    # the site needs no extra request: settings.json is already fetched on
    # load. It is absent from the live API, which is correct — on the private
    # container the data is live and there is nothing to date.
    _safe(
        "settings.json",
        lambda: {
            **_dump(settings_routes.get_settings()),
            "public": True,
            "snapshot_generated_at": started.isoformat(),
        },
    )

    _export_tickers()


def _export_agent_events_by_month() -> None:
    """One file per calendar month with a decision pass, plus an index of
    which months exist. The Decisions page loads the index up front, expands
    only the newest month by default, and fetches an older month's file only
    when a reader opens it — this is what makes that possible without the
    page shipping its entire history on every visit."""
    try:
        months = agent_routes.get_event_months()
    except Exception:
        log.exception("Could not list decision-pass months for export")
        return
    _safe("agent_events_months.json", lambda: months)
    for month in months:
        file_key = month.replace("-", "")  # "2026-09" -> "202609"
        _safe(f"agent_events_{file_key}.json", lambda m=month: agent_routes.get_events(month=m))


def _export_journal_by_month() -> None:
    """The Journal page's own month timeline — same shape as
    _export_agent_events_by_month, one calendar-day journal entry per
    file's row instead of a day's decision passes."""
    try:
        months = agent_routes.get_journey_months()
    except Exception:
        log.exception("Could not list journal months for export")
        return
    _safe("journal_months.json", lambda: months)
    for month in months:
        file_key = month.replace("-", "")
        _safe(
            f"journal_{file_key}.json",
            lambda m=month: agent_routes.get_journey_entries(month=m),
        )


def _export_signal_details() -> None:
    try:
        signals = signals_routes.list_signals(limit=_ALL_SIGNALS_LIMIT)
    except Exception:
        log.exception("Could not list signals for per-signal export")
        return
    for signal in signals:
        _safe(f"signals/{signal.id}.json", lambda s=signal: signals_routes.get_signal(s.id))


def _export_tickers() -> None:
    try:
        watchlist = db.get_watchlist()
    except Exception:
        log.exception("Could not read the watchlist for per-ticker export")
        return

    _safe("tickers.json", tickers_routes.list_tickers)
    for ticker in watchlist:
        _safe(f"tickers/{ticker}.json", lambda t=ticker: tickers_routes.get_ticker(t))
        # 365 days — the widest of the day-range buttons on the ticker page.
        # Only the bar array actually depends on the day count
        # (get_ticker_events computes signals/alerts/trades/lots without
        # regard to `days`), so exporting the widest range once lets the
        # public page slice `bars` by date client-side for every button
        # without a separate file per range.
        _safe(
            f"tickers/{ticker}/events.json",
            lambda t=ticker: tickers_routes.get_ticker_events(t, days=365),
        )
