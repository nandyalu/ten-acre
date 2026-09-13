"""A pass that never ran must not spend the 30-minute cooldown.

``_maybe_run_agent`` stamped ``_last_agent_run`` before it tried to run
anything, so a pass that was then skipped — by the lock, or by an alarm that
had already fired — still consumed the full cooldown and silently suppressed
every trigger for the next half hour. Measured on the live book 2026-09-11: of
seventeen analyses landing over two days, essentially none led to a pass.

See the 2026-09-12 entry in JOURNEY.md.
"""
import asyncio
import datetime

import pytest

from backend.tasks import scheduler


@pytest.fixture(autouse=True)
def open_market(monkeypatch):
    monkeypatch.setattr(scheduler.agent, "is_enabled", lambda: True)
    monkeypatch.setattr(scheduler.watchdog, "is_us_market_hours", lambda: True)
    monkeypatch.setattr(scheduler, "_last_agent_run", None)
    monkeypatch.setattr(scheduler, "wake_agent_now", lambda label=None: False)


def test_a_pass_blocked_by_the_lock_does_not_spend_the_cooldown(monkeypatch):
    ran = []

    async def never_runs(label):
        ran.append(label)

    monkeypatch.setattr(scheduler, "_run_agent_pass_locked", never_runs)

    async def drive():
        # Hold the lock the way a pass that is waiting for its own research did.
        async with scheduler._pass_lock:
            await scheduler._maybe_run_agent()

    asyncio.run(drive())

    assert ran == [], "the pass could not run"
    assert scheduler._last_agent_run is None, "so the cooldown must still be unspent"


def test_a_pass_that_does_run_spends_it(monkeypatch):
    ran = []

    async def runs(label):
        ran.append(label)

    monkeypatch.setattr(scheduler, "_run_agent_pass_locked", runs)

    asyncio.run(scheduler._maybe_run_agent())

    assert ran == ["Event-driven"]
    assert scheduler._last_agent_run is not None


def test_pulling_the_alarm_forward_spends_it_too(monkeypatch):
    """That path really does produce a pass, just not on this call stack."""
    ran = []

    async def runs(label):
        ran.append(label)

    monkeypatch.setattr(scheduler, "_run_agent_pass_locked", runs)
    monkeypatch.setattr(scheduler, "wake_agent_now", lambda label=None: True)

    asyncio.run(scheduler._maybe_run_agent())

    assert ran == [], "the alarm runs it, not this call"
    assert scheduler._last_agent_run is not None


def test_the_cooldown_still_holds_within_the_window(monkeypatch):
    ran = []

    async def runs(label):
        ran.append(label)

    monkeypatch.setattr(scheduler, "_run_agent_pass_locked", runs)
    monkeypatch.setattr(
        scheduler, "_last_agent_run",
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=5),
    )

    asyncio.run(scheduler._maybe_run_agent())

    assert ran == []
