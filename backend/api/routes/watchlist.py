"""The watchlist, read-only.

**Nothing here adds or removes a ticker, and that is the design.** The agent
chooses what it watches: it commissions research with a ``research`` action and
drops a name with an ``untrack`` action, and it pays for every ticker on the
list every morning. A hand-added ticker would appear in that record as a name
the agent chose and never chose, and no reading of the book afterwards could
tell the two apart.

The candidate list is here for the same reason it is in the agent's prompt: to
be looked at. Acting on one is the agent's move to make.
"""
from fastapi import APIRouter

from backend.database import db
from backend.api.schemas import CandidateOut
from backend.services import candidates, trend

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])


@router.get("", response_model=list[str])
def list_watchlist():
    return db.get_watchlist()


@router.get("/candidates", response_model=list[CandidateOut])
def get_candidates():
    """The screened names the agent may commission, minus the ones it already
    tracks. The same list ``agent.build_prompt`` puts in front of the model,
    with the same four stock cells (2026-10-01), rendered the way the prompt
    renders them, so the page and the prompt cannot say different things."""
    found = candidates.fetch_candidates()
    trends = trend.describe_many(c.ticker for c in found)
    rows = []
    for c in found:
        cells = trend.cells(trends.get(c.ticker))
        rows.append(CandidateOut(
            ticker=c.ticker, name=c.name, price=c.price, volume=c.volume,
            change_pct=c.change_pct, source=c.source,
            trend=cells[0], month_quarter=cells[1], volume_vs_normal=cells[2],
            range_per_day=cells[3],
        ))
    return rows
