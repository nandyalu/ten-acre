"""The agent's chosen time is kept, and it survives a restart.

**Since 2026-09-25 one quiv task runs every decision pass**, and everything
that wants a pass moves it: the agent's own time, the final pass before the
close, and every early wake. quiv never runs a task beside itself, so two
passes cannot overlap.

**quiv discards a ``run_at`` written while the task runs.** When the job ends
it computes the next run from the interval. So a wake that comes during a pass
is stored, and a listener moves the task once the pass has ended. The last test
here runs a real ``Quiv`` to prove that the time a pass chooses is kept.

quiv keeps its tasks in a temporary file that a restart deletes, so the task
is added again at startup and pointed at ``agentrun.next_wakeup``. **That
restore is the one step that can end the experiment.**
"""
import asyncio
import datetime
import threading
import time
import types

import pytest
from quiv import Event, Quiv, TaskStatus

from backend.tasks import scheduler

UTC = datetime.timezone.utc


def _now():
    return datetime.datetime.now(UTC)


def _in(seconds):
    return _now() + datetime.timedelta(seconds=seconds)


class _Run:
    def __init__(self, next_wakeup=None, ran_at=None):
        self.next_wakeup = next_wakeup
        self.ran_at = ran_at


@pytest.fixture
def task(monkeypatch):
    """A stand-in for quiv that records where the agent task was pointed."""
    state = types.SimpleNamespace(status=TaskStatus.ACTIVE, run_at=[], added=[], listeners=[])

    def add_task(task_name, func, **kw):
        state.added.append(kw)
        return "agent"

    def update_task(task_id, run_at):
        state.run_at.append(run_at)

    monkeypatch.setattr(scheduler.scheduler, "add_task", add_task)
    monkeypatch.setattr(scheduler.scheduler, "update_task", update_task)
    monkeypatch.setattr(scheduler.scheduler, "add_listener", lambda event, cb: state.listeners.append(event))
    monkeypatch.setattr(
        scheduler.scheduler, "get_task", lambda task_id: types.SimpleNamespace(id=task_id, status=state.status)
    )
    monkeypatch.setattr(scheduler, "_agent_task_id", "agent")
    monkeypatch.setattr(scheduler, "_pending_wake", None)
    monkeypatch.setattr(scheduler, "_agent_task_ran_at", None)
    monkeypatch.setattr(scheduler, "_final_pass_at", _in(86400))
    monkeypatch.setattr(scheduler, "_last_final_pass", None)
    monkeypatch.setattr(scheduler.market_clock, "next_final_pass", lambda *a: _in(86400))
    monkeypatch.setattr(scheduler.agent, "is_enabled", lambda: True)
    monkeypatch.setattr(scheduler.agent.db, "get_agent_runs", lambda limit: [_Run(_in(86400))])
    monkeypatch.setattr(scheduler.market_clock, "now_et", lambda *a: _now().astimezone(scheduler.market_clock.US_MARKET_TZ))
    return state


def _near_now(when):
    return abs((when - _now()).total_seconds()) < 5


# --- where the task points -----------------------------------------------------


def test_the_task_runs_at_the_time_the_agent_chose(task, monkeypatch):
    chosen = _in(120)
    monkeypatch.setattr(scheduler.agent.db, "get_agent_runs", lambda limit: [_Run(chosen)])

    scheduler.restore_agent_task()

    assert task.added[0]["run_at"] == chosen
    assert task.added[0]["interval"] == scheduler._BACKSTOP_SECONDS


def test_a_time_far_ahead_still_leaves_the_backstop(task, monkeypatch):
    """The task runs at least every five minutes. A run with nothing due does
    nothing, and it guards a restore that set a wrong time."""
    monkeypatch.setattr(scheduler.agent.db, "get_agent_runs", lambda limit: [_Run(_in(7200))])

    scheduler.restore_agent_task()

    assert abs((task.added[0]["run_at"] - _in(scheduler._BACKSTOP_SECONDS)).total_seconds()) < 5


def test_a_time_already_past_runs_at_once(task, monkeypatch):
    """The container was down when the wakeup came due. Ask the agent now
    rather than dropping the time it chose. A past ``run_at`` runs at once."""
    past = _now() - datetime.timedelta(hours=1)
    monkeypatch.setattr(scheduler.agent.db, "get_agent_runs", lambda limit: [_Run(past.replace(tzinfo=None))])

    scheduler.restore_agent_task()

    assert task.added[0]["run_at"] == past  # naive UTC from SQLite, read as UTC


def test_a_restore_with_no_runs_at_all_still_adds_the_task(task, monkeypatch):
    """A fresh database must not mean an agent that never starts."""
    monkeypatch.setattr(scheduler.agent.db, "get_agent_runs", lambda limit: [])

    scheduler.restore_agent_task()

    assert len(task.added) == 1
    assert set(task.listeners) == {Event.JOB_COMPLETED, Event.JOB_FAILED, Event.JOB_CANCELLED}


def test_the_final_pass_is_one_of_the_times(task, monkeypatch):
    soon = _in(60)
    monkeypatch.setattr(scheduler, "_final_pass_at", soon)

    assert scheduler._next_pass_time() == soon


# --- a wake from outside -------------------------------------------------------


def test_a_wake_between_passes_moves_the_task_now(task):
    scheduler.wake_agent_now("Event-driven")

    assert len(task.run_at) == 1 and _near_now(task.run_at[0])


def test_a_wake_during_a_pass_waits_for_it_to_end(task):
    """quiv would discard the time. The listener moves the task instead."""
    task.status = TaskStatus.RUNNING
    scheduler.wake_agent_now("Stop fill")

    assert task.run_at == []

    task.status = TaskStatus.ACTIVE
    scheduler._after_agent_job(Event.JOB_COMPLETED, types.SimpleNamespace(id="agent"), None)

    assert len(task.run_at) == 1 and _near_now(task.run_at[0])


def test_the_listener_ignores_other_tasks(task):
    scheduler._after_agent_job(Event.JOB_COMPLETED, types.SimpleNamespace(id="alert_watchdog"), None)

    assert task.run_at == []


def test_the_first_reason_is_kept(task):
    scheduler.wake_agent_now("Earnings")
    scheduler.wake_agent_now("Event-driven")

    assert scheduler._pending_wake[0] == "Earnings"


def test_a_wake_the_pass_already_saw_is_dropped(task):
    scheduler.wake_agent_now("Event-driven")
    scheduler._forget_wakes_seen_by(_in(1))

    assert scheduler._pending_wake is None


def test_a_wake_after_the_pass_last_looked_is_kept(task):
    looked_at = _now() - datetime.timedelta(seconds=1)
    scheduler.wake_agent_now("Stop fill")
    scheduler._forget_wakes_seen_by(looked_at)

    assert scheduler._pending_wake[0] == "Stop fill"


# --- what a run of the task does -----------------------------------------------


@pytest.fixture
def passes(monkeypatch):
    ran = []
    monkeypatch.setattr(scheduler, "_run_agent_pass", lambda label, stop_event=None: ran.append(label))
    return ran


def test_a_run_with_nothing_due_does_nothing(task, passes):
    scheduler.agent_pass()

    assert passes == []


def test_a_run_at_the_agents_time_is_its_alarm(task, passes, monkeypatch):
    monkeypatch.setattr(scheduler.agent, "wakeup_due", lambda now: now)

    scheduler.agent_pass()

    assert passes == ["Alarm"]


def test_a_pending_wake_names_the_pass_and_is_used_up(task, passes):
    scheduler.wake_agent_now("Earnings")

    scheduler.agent_pass()

    assert passes == ["Earnings"]
    assert scheduler._pending_wake is None


def test_the_final_pass_runs_once_and_moves_to_the_next_session(task, passes, monkeypatch):
    monkeypatch.setattr(scheduler, "_final_pass_at", _now() - datetime.timedelta(seconds=1))
    monkeypatch.setattr(scheduler.watchdog, "is_us_market_hours", lambda: True)
    monkeypatch.setattr(scheduler, "_ran_recently", lambda now: False)

    scheduler.agent_pass()

    assert passes == ["Final"]
    assert scheduler._final_pass_at > _now()


def test_a_final_pass_due_while_the_agent_is_off_still_moves(task, passes, monkeypatch):
    """Or it would stay due, and the task would run again at once, in a loop."""
    monkeypatch.setattr(scheduler, "_final_pass_at", _now() - datetime.timedelta(seconds=1))
    monkeypatch.setattr(scheduler.watchdog, "is_us_market_hours", lambda: True)
    monkeypatch.setattr(scheduler, "_ran_recently", lambda now: False)
    monkeypatch.setattr(scheduler.agent, "is_enabled", lambda: False)

    scheduler.agent_pass()

    assert passes == []
    assert scheduler._final_pass_at > _now()


def test_an_unserved_wakeup_waits_for_the_backstop(task, passes, monkeypatch):
    """A pass that fails, or an agent switched off, leaves the wakeup in the
    past. Without this the task would run again at once, in a loop."""
    past = _now() - datetime.timedelta(minutes=10)
    monkeypatch.setattr(scheduler.agent.db, "get_agent_runs", lambda limit: [_Run(past)])
    monkeypatch.setattr(scheduler.agent, "is_enabled", lambda: False)

    scheduler.agent_pass()

    assert abs((scheduler._next_pass_time() - _in(scheduler._BACKSTOP_SECONDS)).total_seconds()) < 5


# --- after a pass --------------------------------------------------------------


def _stand_in_run(**kw):
    fields = dict(unguarded=[], acted=False, rejected=[], failed=[], notes=[], fills_seen=[], looked_at=None)
    fields.update(kw)
    return types.SimpleNamespace(**fields)


def test_an_unguarded_position_asks_for_the_next_pass_at_once(task, monkeypatch):
    """A position that came out of this pass with nothing resting under it is
    worth a fresh decision sooner than whatever the agent just chose. Asked
    for after the pass (2026-09-16), never during it."""
    monkeypatch.setattr(
        scheduler.agent, "run_once",
        lambda woke_because=None, should_stop=None: _stand_in_run(
            unguarded=["ZBH: the broker refused them: boom"], acted=True
        ),
    )
    monkeypatch.setattr(scheduler, "notify", lambda *a, **kw: asyncio.sleep(0))
    monkeypatch.setattr(scheduler.agent, "format_run_embed", lambda run: None)
    task.status = TaskStatus.RUNNING  # the pass is this task's own job

    scheduler._run_agent_pass_locked("test")

    assert scheduler._pending_wake[0] == "Unguarded position"


def test_a_pass_with_no_unguarded_position_asks_for_nothing(task, monkeypatch):
    monkeypatch.setattr(scheduler.agent, "run_once", lambda woke_because=None, should_stop=None: _stand_in_run())

    scheduler._run_agent_pass_locked("test")

    assert scheduler._pending_wake is None


def test_a_failed_pass_does_not_escape(task, monkeypatch):
    monkeypatch.setattr(
        scheduler.agent, "run_once",
        lambda woke_because=None, should_stop=None: (_ for _ in ()).throw(RuntimeError("model down")),
    )

    scheduler._run_agent_pass_locked("test")  # must not raise


def test_the_stop_event_reaches_the_pass(task, monkeypatch):
    seen = {}

    def run_once(woke_because=None, should_stop=None):
        seen["should_stop"] = should_stop
        return _stand_in_run()

    monkeypatch.setattr(scheduler.agent, "run_once", run_once)
    stop = threading.Event()

    scheduler._run_agent_pass_locked("test", stop)

    assert seen["should_stop"]() is False
    stop.set()
    assert seen["should_stop"]() is True


def test_the_review_waits_for_a_running_pass(monkeypatch):
    """The review must see a pass whole, and a pass must not start under it."""
    monkeypatch.setattr(scheduler.agent, "is_enabled", lambda: True)
    reviewed = threading.Event()
    monkeypatch.setattr(scheduler.reflection, "run_once", lambda: reviewed.set())

    with scheduler._pass_lock:
        review = threading.Thread(target=scheduler._evening_review)
        review.start()
        assert not reviewed.wait(0.2)
    review.join(2)

    assert reviewed.is_set()


# --- end to end, against a real quiv -------------------------------------------


def test_the_time_a_pass_chooses_is_kept_by_a_real_quiv(monkeypatch):
    """The listener is what keeps a time chosen during a pass. quiv 1.2.0
    discards a ``run_at`` written to a running task, so without it the second
    pass would wait for the five-minute backstop and this test would time out.
    """
    monkeypatch.setattr(scheduler, "_pending_wake", None)
    monkeypatch.setattr(scheduler, "_agent_task_ran_at", None)
    monkeypatch.setattr(scheduler, "_last_final_pass", None)
    monkeypatch.setattr(scheduler.market_clock, "next_final_pass", lambda *a: _in(86400))
    monkeypatch.setattr(scheduler.agent, "is_enabled", lambda: True)

    stored = {"wakeup": _now() - datetime.timedelta(seconds=1)}  # due at start
    passes = []

    def run_once(woke_because=None, should_stop=None):
        passes.append(_now())
        # The pass chooses its next time while quiv shows the task as running.
        stored["wakeup"] = _in(1)
        return _stand_in_run()

    monkeypatch.setattr(scheduler.agent.db, "get_agent_runs", lambda limit: [_Run(stored["wakeup"])])
    monkeypatch.setattr(scheduler.agent, "wakeup_due", lambda now: stored["wakeup"] if stored["wakeup"] <= now else None)
    monkeypatch.setattr(scheduler.agent, "run_once", run_once)

    async def scenario():
        real = Quiv(pool_size=2)
        monkeypatch.setattr(scheduler, "scheduler", real)
        scheduler.restore_agent_task()
        real.start()
        try:
            deadline = time.monotonic() + 10
            while len(passes) < 2 and time.monotonic() < deadline:
                await asyncio.sleep(0.05)
        finally:
            await asyncio.to_thread(real.shutdown, 5)

    asyncio.run(scenario())

    assert len(passes) >= 2, "the second pass never came: the chosen time was lost"
    gap = (passes[1] - passes[0]).total_seconds()
    assert 0.5 < gap < 5, f"the second pass came {gap:.1f}s after the first, not at the chosen time"
