"""The agent's chosen time is a real alarm, and it survives a restart.

A ``run_once`` quiv task fires at the second the agent asked for. quiv keeps
its tasks in a temporary file that a restart deletes, so the alarm is rebuilt
from ``agentrun.next_wakeup`` at startup. **That restore is the one step that
can end the experiment**: without it the agent has no alarm and nothing else
schedules it.
"""
import asyncio
import datetime
import types
from zoneinfo import ZoneInfo

import pytest
import quiv as quiv_lib  # the `quiv` fixture below shadows the module name

from backend.tasks import scheduler

ET = ZoneInfo("America/New_York")


def _et(hour, minute=0, day=4):
    return datetime.datetime(2026, 9, day, hour, minute, tzinfo=ET)


class _Run:
    def __init__(self, next_wakeup=None):
        self.next_wakeup = next_wakeup


@pytest.fixture
def quiv(monkeypatch):
    """Records what the scheduler was asked to do.

    ``add_task`` no longer requires ``interval`` for a one-off (quiv 0.9.0,
    #65) and a wakeup is scheduled with ``run_at`` rather than ``delay``
    (quiv 0.10.0, #66) — see _replace_wakeup_alarm. The fallback task in
    wake_agent_for_new_changes still uses a plain ``delay``, so this records
    whichever of the two a call actually passed.
    """
    calls = {"added": [], "removed": [], "fired": []}

    def add_task(task_name, func, **kw):
        assert kw.get("run_once"), "a wakeup must not repeat"
        calls["added"].append(kw.get("run_at", kw.get("delay")))
        return f"task-{len(calls['added'])}"

    monkeypatch.setattr(scheduler.scheduler, "add_task", add_task)
    monkeypatch.setattr(scheduler.scheduler, "remove_task", lambda tid: calls["removed"].append(tid))
    monkeypatch.setattr(scheduler.scheduler, "run_task_immediately", lambda tid: calls["fired"].append(tid))
    monkeypatch.setattr(scheduler, "_wakeup_task_id", None)
    monkeypatch.setattr(scheduler.market_clock, "now_et", lambda *a: _et(10, 0))
    return calls


# --- setting the alarm ---------------------------------------------------------


def test_the_alarm_is_set_for_the_time_the_agent_chose(quiv):
    scheduler._replace_wakeup_alarm(_et(10, 30))

    assert quiv["added"] == [_et(10, 30)]  # run_at, passed straight through


def test_a_time_already_past_fires_at_once(quiv):
    """The container was down when the wakeup came due. Ask the agent now
    rather than dropping the time it chose.

    This used to be our own clamp (``max(0.0, ...)``) computed against a
    delay in seconds. It is now quiv's own guarantee for ``run_at`` — a past
    instant runs at once — so this just confirms the past time is passed
    through unchanged rather than dropped or clamped here."""
    scheduler._replace_wakeup_alarm(_et(9, 0))

    assert quiv["added"] == [_et(9, 0)]


def test_setting_an_alarm_replaces_the_pending_one(quiv):
    """**Not every pass consumes an alarm.** The last pass before the close does
    not, nor does the first after a restore. One of those would leave two
    alarms pending and both would fire."""
    scheduler._replace_wakeup_alarm(_et(10, 30))
    scheduler._replace_wakeup_alarm(_et(11, 0))

    assert quiv["removed"] == ["task-1"]
    assert len(quiv["added"]) == 2


def test_no_time_means_no_alarm(quiv):
    scheduler._replace_wakeup_alarm(None)

    assert quiv["added"] == []


def test_removing_an_already_fired_alarm_is_not_an_error(quiv, monkeypatch):
    """A one-off deletes itself when it fires, so the ordinary case is that the
    remove finds nothing."""
    scheduler._replace_wakeup_alarm(_et(10, 30))
    monkeypatch.setattr(
        scheduler.scheduler, "remove_task",
        lambda tid: (_ for _ in ()).throw(KeyError("gone")),
    )

    scheduler._replace_wakeup_alarm(_et(11, 0))  # must not raise

    assert len(quiv["added"]) == 2


# --- surviving a restart -------------------------------------------------------


def test_the_stored_wakeup_is_restored_at_startup(quiv, monkeypatch):
    """quiv's task file is deleted on restart. Without this the agent never
    wakes and nothing reports a problem."""
    stored = datetime.datetime(2026, 9, 4, 15, 0)  # naive UTC, as SQLite returns it
    monkeypatch.setattr(scheduler.agent.db, "get_agent_runs", lambda limit: [_Run(stored)])

    scheduler.restore_wakeup_alarm()

    assert len(quiv["added"]) == 1


def test_a_restore_with_no_stored_time_falls_back_to_the_next_open(quiv, monkeypatch):
    """An unreadable or missing wakeup must still leave the agent scheduled."""
    monkeypatch.setattr(scheduler.agent.db, "get_agent_runs", lambda limit: [_Run(None)])

    scheduler.restore_wakeup_alarm()

    assert len(quiv["added"]) == 1


def test_a_restore_with_no_runs_at_all_still_sets_an_alarm(quiv, monkeypatch):
    """A fresh database must not mean an agent that never starts."""
    monkeypatch.setattr(scheduler.agent.db, "get_agent_runs", lambda limit: [])

    scheduler.restore_wakeup_alarm()

    assert len(quiv["added"]) == 1


# --- pulling the alarm forward -------------------------------------------------


def test_an_early_pass_pulls_the_alarm_forward(quiv):
    """Friday's case: an analysis landed at 17:00, twelve minutes before the
    alarm set for 17:12. Firing the one-off now also deletes it, so the
    superseded time cannot arrive later."""
    scheduler._replace_wakeup_alarm(_et(10, 30))

    assert scheduler.wake_agent_now() is True
    assert quiv["fired"] == ["task-1"]


def test_pulling_forward_with_no_alarm_reports_it(quiv):
    """The caller then runs the pass itself rather than skipping it."""
    assert scheduler.wake_agent_now() is False


def test_an_already_running_alarm_reports_it(quiv, monkeypatch):
    """Already running. A pass is happening, and the caller must not start a
    second alongside it."""
    scheduler._replace_wakeup_alarm(_et(10, 30))
    monkeypatch.setattr(
        scheduler.scheduler, "run_task_immediately",
        lambda tid: (_ for _ in ()).throw(scheduler.TaskNotActiveError("running")),
    )

    assert scheduler.wake_agent_now() is False


def test_an_already_fired_alarm_reports_it(quiv, monkeypatch):
    """Already fired and deleted itself. Same outcome, different quiv error —
    both must be caught (quiv 0.9.0 made this one TaskNotFoundError; it used
    to be the misleading HandlerNotRegisteredError, see #67)."""
    scheduler._replace_wakeup_alarm(_et(10, 30))
    monkeypatch.setattr(
        scheduler.scheduler, "run_task_immediately",
        lambda tid: (_ for _ in ()).throw(scheduler.TaskNotFoundError("gone")),
    )

    assert scheduler.wake_agent_now() is False


def test_a_genuinely_unregistered_handler_is_not_swallowed(quiv, monkeypatch):
    """HandlerNotRegisteredError means something else entirely — the handler
    was never registered at all, a real bug — and must not be read as one of
    the two harmless races above."""
    scheduler._replace_wakeup_alarm(_et(10, 30))
    monkeypatch.setattr(
        scheduler.scheduler, "run_task_immediately",
        lambda tid: (_ for _ in ()).throw(quiv_lib.HandlerNotRegisteredError("no handler")),
    )

    with pytest.raises(quiv_lib.HandlerNotRegisteredError):
        scheduler.wake_agent_now()


# --- never two passes at once --------------------------------------------------


def test_two_passes_cannot_overlap(quiv, monkeypatch):
    """The alarm fires, and a second later the tick reads a next_wakeup the
    running pass has not replaced yet."""
    started = []

    async def slow_run(label):
        started.append(label)
        await asyncio.sleep(0.05)

    monkeypatch.setattr(scheduler, "_run_agent_pass_locked", slow_run)

    async def both():
        await asyncio.gather(
            scheduler._run_agent_pass("alarm"), scheduler._run_agent_pass("tick")
        )

    asyncio.run(both())

    assert started == ["alarm"]


def test_a_failed_pass_still_leaves_an_alarm(quiv, monkeypatch):
    """Without one the agent never runs again. The next open is honest: the
    pass produced no answer, so the agent chose nothing."""
    monkeypatch.setattr(
        scheduler.agent, "run_once",
        lambda: (_ for _ in ()).throw(RuntimeError("model down")),
    )

    asyncio.run(scheduler._run_agent_pass_locked("test"))

    assert len(quiv["added"]) == 1


def test_an_unguarded_position_pulls_the_alarm_forward(quiv, monkeypatch):
    """A position that came out of this pass with nothing resting under it is
    worth a fresh decision sooner than whatever the agent just chose — not a
    wait of up to four days for its own next wakeup. Pulled forward after the
    pass's own alarm is already set (2026-09-16), never during the pass
    itself — see the comment at the call site for why that order matters."""
    monkeypatch.setattr(
        scheduler.agent, "run_once",
        lambda woke_because=None: types.SimpleNamespace(
            next_wakeup=_et(14, 0), unguarded=["ZBH: the broker refused them: boom"],
            acted=True, rejected=[], failed=[], notes=[],
        ),
    )
    monkeypatch.setattr(scheduler, "notify", lambda *a, **kw: asyncio.sleep(0))
    monkeypatch.setattr(scheduler.agent, "format_run_embed", lambda run: None)

    asyncio.run(scheduler._run_agent_pass_locked("test"))

    assert quiv["added"] == [_et(14, 0)]  # the pass's own choice, set first
    assert quiv["fired"] == ["task-1"]  # then pulled forward


def test_a_pass_with_no_unguarded_position_does_not_touch_the_alarm(quiv, monkeypatch):
    monkeypatch.setattr(
        scheduler.agent, "run_once",
        lambda woke_because=None: types.SimpleNamespace(
            next_wakeup=_et(14, 0), unguarded=[], acted=False, rejected=[], failed=[], notes=[],
        ),
    )

    asyncio.run(scheduler._run_agent_pass_locked("test"))

    assert quiv["fired"] == []


# --- waking for a new change note -----------------------------------------------


@pytest.fixture
def settings(monkeypatch):
    """A fake BotSetting store, the shape test_research_charge.py uses."""
    store: dict[str, str] = {}
    monkeypatch.setattr(scheduler.db, "get_setting", lambda k: store.get(k))
    monkeypatch.setattr(scheduler.db, "set_setting", lambda k, v: store.__setitem__(k, v))
    return store


def _stub_changes(monkeypatch, n):
    monkeypatch.setattr(
        scheduler.agent, "load_change_notes",
        lambda: [{"date": "2026-09-08", "message": f"change {i}"} for i in range(n)],
    )


def test_no_file_means_no_wake(quiv, settings, monkeypatch):
    _stub_changes(monkeypatch, 0)

    scheduler.wake_agent_for_new_changes()

    assert quiv["fired"] == [] and quiv["added"] == []
    assert settings == {}


def test_a_new_entry_pulls_the_pending_alarm_forward(quiv, settings, monkeypatch):
    """The ordinary case: restore_wakeup_alarm already ran, so there is an
    alarm to pull forward rather than a fresh task to add."""
    scheduler._replace_wakeup_alarm(_et(10, 30))
    quiv["added"].clear()  # only the change-note trigger's own calls matter below
    _stub_changes(monkeypatch, 2)

    scheduler.wake_agent_for_new_changes()

    assert quiv["fired"] == ["task-1"]
    assert quiv["added"] == []
    # The count is written when the pass completes, never when the wake is
    # scheduled: a restart in between must wake again.
    assert "agent_changes_seen_count" not in settings


def test_nothing_new_does_not_wake_again(quiv, settings, monkeypatch):
    """Once the count has been recorded, the same two entries must not fire a
    second wakeup on the next startup."""
    settings["agent_changes_seen_count"] = "2"
    _stub_changes(monkeypatch, 2)

    scheduler.wake_agent_for_new_changes()

    assert quiv["fired"] == [] and quiv["added"] == []


def test_a_grown_file_wakes_again(quiv, settings, monkeypatch):
    scheduler._replace_wakeup_alarm(_et(10, 30))
    quiv["added"].clear()
    settings["agent_changes_seen_count"] = "2"
    _stub_changes(monkeypatch, 3)

    scheduler.wake_agent_for_new_changes()

    assert quiv["fired"] == ["task-1"]
    assert settings["agent_changes_seen_count"] == "2", "unchanged until a pass completes"


def test_no_pending_alarm_falls_back_to_a_fresh_one_off_task(quiv, settings, monkeypatch):
    """Should not happen right after restore_wakeup_alarm, but a fresh task is
    the honest fallback rather than silently doing nothing."""
    _stub_changes(monkeypatch, 1)

    scheduler.wake_agent_for_new_changes()

    assert quiv["fired"] == []
    assert quiv["added"] == [0]  # run now, as a one-off


def test_same_dates_two_deploys_apart_still_wakes(quiv, settings, monkeypatch):
    """The reason this counts entries instead of comparing dates: a second
    deploy the same day adds an entry that shares its date with one already
    seen, and a date comparison alone would call that "nothing new"."""
    settings["agent_changes_seen_count"] = "2"
    scheduler._replace_wakeup_alarm(_et(10, 30))
    quiv["added"].clear()
    _stub_changes(monkeypatch, 3)  # all three share "2026-09-08"

    scheduler.wake_agent_for_new_changes()

    assert quiv["fired"] == ["task-1"]


def _pass(**fields):
    return lambda woke_because=None: types.SimpleNamespace(
        next_wakeup=_et(14, 0), unguarded=[], acted=False, rejected=[], failed=[], notes=[],
        **fields,
    )


def test_a_completed_pass_records_the_count(quiv, settings, monkeypatch):
    """Whatever woke it, a pass shows every note on file, so any completed
    pass is the moment the notes count as announced."""
    _stub_changes(monkeypatch, 3)
    monkeypatch.setattr(scheduler.agent, "run_once", _pass())

    asyncio.run(scheduler._run_agent_pass_locked("Alarm"))

    assert settings["agent_changes_seen_count"] == "3"


def test_a_failed_pass_records_nothing(quiv, settings, monkeypatch):
    _stub_changes(monkeypatch, 3)

    def boom(woke_because=None):
        raise RuntimeError("the model did not answer")

    monkeypatch.setattr(scheduler.agent, "run_once", boom)

    asyncio.run(scheduler._run_agent_pass_locked("Change"))

    assert "agent_changes_seen_count" not in settings


def test_a_restart_before_the_pass_wakes_again(quiv, settings, monkeypatch):
    """The 2026-09-19 redeploy: the container was replaced 35 seconds after
    it started, between the wake and its pass. The old code had written the
    count at the wake, so the second start found nothing new and the note
    waited for the agent's planned time, days away."""
    _stub_changes(monkeypatch, 3)
    scheduler._replace_wakeup_alarm(_et(10, 30))
    scheduler.wake_agent_for_new_changes()
    # The stop cancelled the pass. The next start restores the alarm and asks again.
    scheduler._replace_wakeup_alarm(_et(10, 30))

    scheduler.wake_agent_for_new_changes()

    assert quiv["fired"] == ["task-1", "task-2"]
