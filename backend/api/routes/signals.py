"""Signal history and detail. Read-only.

A signal used to carry a "follow" action that opened a hand-followed paper
position from it. There is one book now and only the agent trades it, so what a
signal leads to is visible here as the agent's own trades against it, under
``agent_trades``.
"""
import datetime

from fastapi import APIRouter, HTTPException

from backend.database import db
from backend.api.schemas import (
    AgentTradeRowOut,
    SignalDetailOut,
    SignalOut,
)
from backend.services import agent_book, signals as signals_service

router = APIRouter(prefix="/api/signals", tags=["signals"])


@router.get("", response_model=list[SignalOut])
def list_signals(ticker: str | None = None, status: str | None = None, limit: int = 20):
    ticker = ticker.upper().strip() if ticker else None
    if status == "pending":
        rows = db.get_pending_signals(datetime.date.today())
        if ticker:
            rows = [s for s in rows if s.ticker == ticker]
    elif status == "resolved":
        rows = db.get_resolved_signals(ticker)
    else:
        rows = db.get_recent_signals(ticker, limit=limit, by_time=True)
    # Sorted before the slice, and for every branch, because the three
    # disagreed: "pending" came back in whatever order the rows sat in,
    # "resolved" came back oldest first — so asking for the newest 20 resolved
    # analyses returned the 20 oldest — and "recent" ordered by calendar date
    # alone, which cannot separate a day that holds eight analyses.
    rows = signals_service.newest_first(rows)[:limit]
    return [SignalOut.model_validate(s) for s in rows]


@router.get("/{signal_id}", response_model=SignalDetailOut)
def get_signal(signal_id: int):
    signal = db.get_signal(signal_id)
    if signal is None:
        raise HTTPException(status_code=404, detail="Signal not found.")
    reports = db.get_signal_reports(signal_id)
    return SignalDetailOut(
        **SignalOut.model_validate(signal).model_dump(),
        reports=reports,
        agent_trades=[
            AgentTradeRowOut.model_validate(r)
            for r in agent_book.trades_for_signal(signal_id)
        ],
    )
