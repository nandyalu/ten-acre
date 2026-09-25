"""Scheduled-job status/history, backed directly by quiv's own Task/Job
SQLModel objects — quiv's docs describe these as safe to return straight
from FastAPI endpoints (UTC-aware datetimes, no serialization surprises).
Covers the 7 scheduled tasks in backend/tasks/scheduler.py, agent_pass among
them, which runs every decision pass; on-demand analyze/
analyze-all runs on the main loop directly (see backend/api/routes/tickers.py)
rather than through quiv, so they don't show up here — poll
GET /api/signals?ticker=... for those instead.
"""
from fastapi import APIRouter

from backend.tasks.scheduler import scheduler

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("/tasks")
def list_tasks():
    return scheduler.get_all_tasks()


@router.get("")
def list_jobs(status: str | None = None):
    return scheduler.get_all_jobs(status=status)
