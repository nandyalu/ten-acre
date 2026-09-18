"""The application: FastAPI serving the JSON API (/api), the Zensical docs
build (/docs), and the Angular production build everywhere else (with an
index.html fallback for client-side routes) — plus the quiv scheduler and
the optional Discord client, both started/stopped in the lifespan. This is
the process now; backend/main.py just runs it via uvicorn. Replaces
backend/app.py and the old discord.py-owns-the-loop model entirely.
"""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from backend.api.routes import (
    agent,
    alerts,
    digest,
    jobs,
    regime,
    scorecard,
    settings,
    setup,
    signals,
    tickers,
    watchlist,
)
from backend.notifications import notify as notifier
from backend.services import publish, trade_stream
from backend.services.agent import load_change_notes
from backend.tasks.scheduler import register_jobs, scheduler

log = logging.getLogger("trading-experiment.app")

# The built dashboard and the built docs live inside the package, at
# backend/web and backend/site. A checkout, the container and an installed
# copy (uv tool install) then all look in one place, and a wheel carries both
# folders with the code. The build tools write there directly: angular.json
# sets the dashboard's outputPath and zensical.toml sets site_dir.
_PACKAGE_DIR = Path(__file__).resolve().parent
_WEB_DIR = _PACKAGE_DIR / "web"
_SITE_DIR = _PACKAGE_DIR / "site"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start the scheduler, Discord and the trade stream — unless this is the
    published copy, which observes and never acts.

    **This is the half of PUBLIC_MODE that matters most, and it is not about
    the API at all.** The published copy is a second container over the same
    database. If it also ran the scheduler there would be two agents deciding
    on one book: two of every commissioned analysis, paying twice for the
    same look, and two sets of orders at the broker against one ledger.
    The refused writes would not stop any of it, because none of it arrives as
    an HTTP request.

    So PUBLIC_MODE means one thing said two ways: this copy does not act. It
    refuses writes, and it runs no jobs, posts nothing to Discord, and holds no
    trade-event subscription — the last of which also matters because Webull
    allows one per app key, and a second subscriber is refused outright.
    """
    if publish.is_public():
        log.info("PUBLIC_MODE — serving reads only: no scheduler, no Discord, no trade stream")
        yield
        return

    register_jobs()
    scheduler.start()
    if notifier.is_configured():
        log.info("Discord notifications on, through a webhook")
    else:
        log.info("DISCORD_WEBHOOK_URL not set — running without notifications")
    # Read and logged here, not just at prompt-build time, so a typo in
    # agent_changes.json is visible in the logs the moment the container
    # starts rather than discovered mid-decision weeks later. The agent still
    # only sees these on its own next decision pass — starting the container
    # does not force one, the same way nothing else here does.
    changes = sorted(load_change_notes(), key=lambda e: e.get("date", ""))
    if changes:
        log.info(
            "%d agent change note(s) on file — most recent (%s): %s",
            len(changes), changes[-1].get("date"), changes[-1].get("message"),
        )
    else:
        log.info("No agent change notes on file (backend/agent_changes.json)")
    # Best effort, and never fatal: without it a resting stop or take-profit
    # is noticed by the 15-minute poll instead of within a second.
    trade_stream.start()
    yield
    trade_stream.stop()
    scheduler.shutdown()  # mandatory per quiv's own docs — cancels jobs, deletes its temp DB


# FastAPI's own interactive API reference moves under /api, because /docs
# belongs to the user-facing Zensical site mounted below. FastAPI registers its
# routes at construction, before any mount, so leaving it at the default made
# Swagger UI shadow the real docs entirely — the /docs link in the dashboard
# nav reached the API reference instead.
#
# The published copy turns all three off. The write guard below already
# refuses anything the reference would let a visitor try, so this is not a
# safety fix — it is that a page for reading results has no reason to hand out
# a map of every endpoint to whoever asks.
_api_reference_urls = (
    {"docs_url": None, "redoc_url": None, "openapi_url": None}
    if publish.is_public()
    else {
        "docs_url": "/api/docs",
        "redoc_url": "/api/redoc",
        "openapi_url": "/api/openapi.json",
    }
)
app = FastAPI(title="Ten Acre", lifespan=lifespan, **_api_reference_urls)

# --- the public read-only guard ------------------------------------------------
#
# Registered before every router, so it sees every request including the ones
# added later. A per-route check would protect only the routes somebody
# remembered to annotate, and the frontend hiding a button stops nobody who can
# type a URL.
#
# GET and HEAD pass. OPTIONS passes because a browser preflight is not a write.
# Everything else is refused with a reason a person can act on, rather than a
# bare 403 that reads like a bug.
_READ_METHODS = {"GET", "HEAD", "OPTIONS"}


@app.middleware("http")
async def refuse_writes_when_public(request: Request, call_next):
    if publish.is_public() and request.method not in _READ_METHODS:
        return JSONResponse(
            status_code=403,
            content={
                "detail": (
                    "This is the published copy of the experiment and it is "
                    "read-only. Nothing here can change what the agent does — "
                    "that is the point of it."
                )
            },
        )
    return await call_next(request)


app.include_router(watchlist.router)
app.include_router(tickers.router)
app.include_router(signals.router)
app.include_router(scorecard.router)
app.include_router(digest.router)
app.include_router(regime.router)
app.include_router(settings.router)
app.include_router(setup.router)
app.include_router(alerts.router)
app.include_router(jobs.router)
app.include_router(agent.router)

if _SITE_DIR.is_dir():
    app.mount("/docs", StaticFiles(directory=_SITE_DIR, html=True), name="docs")

    # A Mount at "/docs" only matches "/docs/...", so a bare "/docs" — which is
    # what the dashboard nav links to and what anyone types — falls through to
    # the SPA catch-all below and silently lands on the dashboard home instead
    # of the docs. Starlette would normally redirect, but only for paths its
    # Mount actually matches. Redirect explicitly, before the catch-all.
    @app.get("/docs", include_in_schema=False)
    async def docs_index() -> RedirectResponse:
        return RedirectResponse("/docs/")
else:
    log.info("No built docs at %s — /docs not mounted (run `zensical build`)", _SITE_DIR)

if _WEB_DIR.is_dir():
    # Explicit catch-all rather than `app.mount("/", StaticFiles(...))`: a
    # root Mount intercepts every path (including /api/*) ahead of any route
    # matching, and StaticFiles' own file-not-found 404 doesn't fall through
    # to a later route either way. This serves an exact static asset when
    # the path matches a real file, and index.html otherwise so the Angular
    # Router can handle deep links client-side.
    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str) -> FileResponse:
        candidate = _WEB_DIR / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_WEB_DIR / "index.html")
else:
    log.info("No built frontend at %s — serving API only", _WEB_DIR)
