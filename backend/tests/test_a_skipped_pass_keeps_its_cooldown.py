"""A wake that spends the 30-minute cooldown is always answered by a pass.

``_maybe_run_agent`` once stamped ``_last_agent_run`` before it tried to run
anything, so a pass that was then skipped — by the lock, or by an alarm that
had already fired — still consumed the full cooldown and silently suppressed
every trigger for the next half hour. Measured on the live book 2026-09-11: of
seventeen analyses landing over two days, essentially none led to a pass. See
the 2026-09-12 entry in JOURNEY.md.

**Since 2026-09-25 no wake is skipped.** A wake is queued on the one agent
task, and a wake that comes during a pass is kept for the pass after it, or
dropped only because the running pass already saw it. Either way a pass
answers it, so stamping the cooldown when the wake is queued is right.
"""
import datetime

import pytest

from backend.tasks import scheduler


@pytest.fixture(autouse=True)
def open_market(monkeypatch):
    monkeypatch.setattr(scheduler.agent, "is_enabled", lambda: True)
    monkeypatch.setattr(scheduler.watchdog, "is_us_market_hours", lambda: True)
    monkeypatch.setattr(scheduler, "_last_agent_run", None)


@pytest.fixture
def asked(monkeypatch):
    labels = []
    monkeypatch.setattr(scheduler, "wake_agent_now", labels.append)
    return labels


def test_a_queued_wake_spends_the_cooldown(asked):
    scheduler._maybe_run_agent()

    assert asked == ["Event-driven"]
    assert scheduler._last_agent_run is not None


def test_the_cooldown_still_holds_within_the_window(asked, monkeypatch):
    monkeypatch.setattr(
        scheduler, "_last_agent_run",
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=5),
    )

    scheduler._maybe_run_agent()

    assert asked == []


def test_a_switched_off_agent_spends_nothing(asked, monkeypatch):
    monkeypatch.setattr(scheduler.agent, "is_enabled", lambda: False)

    scheduler._maybe_run_agent()

    assert asked == []
    assert scheduler._last_agent_run is None
