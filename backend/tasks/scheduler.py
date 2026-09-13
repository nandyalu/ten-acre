"""Scheduled jobs, on quiv. quiv's real job here is the interval/delay timer
(replacing discord.ext.tasks.loop) — each registered handler is a trivial
sync function that hops straight back to the main event loop via quiv's
run_on_main(). That hop is required, not optional: quiv runs a handler on
its own worker thread with a fresh, isolated event loop, but the actual job
bodies below touch main-loop-bound resources (the Discord client, the
analysis semaphore/asyncio.to_thread machinery in backend/services/analysis.py) that
aren't safe to touch from any other loop.

quiv has no cron/calendar scheduling (interval, plus an absolute or relative
start) — the four daily jobs below approximate a fixed UTC time via
interval=86400 plus run_at set to the next occurrence of that time, keeping
each job's existing internal weekday/Friday-only gate. alert_watchdog is a
true interval and maps over 1:1.
"""
import asyncio
import datetime
import logging
import os

from quiv import Quiv, TaskNotActiveError, TaskNotFoundError, run_on_main

from backend.database import db
from backend.services import (
    agent,
    analysis,
    candidates,
    journey,
    market_clock,
    quotes,
    regime,
    snapshot_export,
    watchdog,
)
from backend.services.digest import build_weekly_digest_embed
from backend.notifications.notify import notify
from backend.services.positions import PriceWindow, get_price_window
from backend.services.signals import SignalEvaluation, evaluate_signal_window, horizon_params

log = logging.getLogger("trading-experiment.scheduler")

scheduler = Quiv(pool_size=int(os.environ.get("QUIV_POOL_SIZE", "10")))


def _next_utc_time(hour: int, minute: int) -> datetime.datetime:
    """The next occurrence of hour:minute UTC — today if still ahead,
    tomorrow otherwise.

    Passed to ``add_task`` as ``run_at`` (quiv >=0.10.0,
    github.com/nandyalu/quiv#66), not converted to a delay in seconds here.
    This used to return the delay itself, computed against ``now`` read in
    this function — and quiv read the clock again to turn that delay back
    into a deadline, so the two reads could disagree by however long fell
    between them. Passing the instant directly removes the second read
    entirely.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += datetime.timedelta(days=1)
    return target


def _format_outcome_line(signal, evaluation: SignalEvaluation, price_now: float) -> str:
    verdict = "PASS" if evaluation.outcome == "pass" else "FAIL"
    line = (
        f"{signal.ticker} {signal.decision} from {signal.signal_date}: **{verdict}** "
        f"(${signal.price_at_signal:,.2f} → ${price_now:,.2f}, {evaluation.pct_change:+.1f}%)"
    )
    if evaluation.outcome_vs_benchmark is not None:
        benchmark_verdict = "PASS" if evaluation.outcome_vs_benchmark == "pass" else "FAIL"
        line += (
            f" · vs SPY {evaluation.benchmark_pct_change:+.1f}%: **{benchmark_verdict}**"
            f" (alpha {evaluation.alpha_pct:+.1f}%)"
        )
    else:
        line += " · vs SPY: n/a"
    if signal.price_target and evaluation.price_target_hit is not None:
        line += f" · target ${signal.price_target:,.2f}: {'hit' if evaluation.price_target_hit else 'not hit'}"
    return line


async def _evaluate_pending_signals() -> None:
    spy_windows: dict[datetime.date, PriceWindow | None] = {}  # per-run cache, keyed by signal_date
    for signal in db.get_pending_signals(datetime.date.today()):
        window = get_price_window(signal.ticker, signal.signal_date)
        if window is None:
            continue  # retry next day rather than guessing
        if signal.signal_date not in spy_windows:
            spy_windows[signal.signal_date] = get_price_window("SPY", signal.signal_date)
        spy = spy_windows[signal.signal_date]
        evaluation = evaluate_signal_window(
            decision=signal.decision,
            price_at_signal=signal.price_at_signal,
            price_now=window.last_close,
            benchmark_price_at_signal=spy.first_close if spy else None,
            benchmark_price_now=spy.last_close if spy else None,
            price_target=signal.price_target,
            window_high=window.high,
            window_low=window.low,
            # Per-signal, not the current setting: a Hold graded over two weeks
            # needs a much tighter band than one graded over six months, and
            # changing the horizon must not re-grade older signals by new rules.
            hold_band_pct=horizon_params(signal.horizon)["hold_band_pct"],
        )
        db.resolve_signal(
            signal.id,
            price_at_evaluation=window.last_close,
            outcome=evaluation.outcome,
            benchmark_price_at_signal=spy.first_close if spy else None,
            benchmark_price_at_evaluation=spy.last_close if spy else None,
            alpha_pct=evaluation.alpha_pct,
            outcome_vs_benchmark=evaluation.outcome_vs_benchmark,
            price_target_hit=evaluation.price_target_hit,
        )
        await notify(_format_outcome_line(signal, evaluation, window.last_close))


# The loop every async job runs on. Captured in register_jobs, which app.py
# calls from inside its lifespan coroutine, so a worker thread can hand work
# back to it and wait for the answer. None outside a running app — a script or
# a test — and _research_for_agent says so rather than hanging.
_main_loop: asyncio.AbstractEventLoop | None = None


def _research_for_agent(tickers: list[str]) -> dict[str, str]:
    """Run the analyses a decision pass just commissioned, and block until done.

    Returns what stopped each analysis that did not finish, by ticker. An empty
    dict means every one finished. Until 2026-09-13 this returned nothing, so
    the pass told the agent a failed analysis had finished.

    **This is what lets a synchronous pass wait for its own research.**
    `agent.run_once` is sync and runs on a worker thread; an analysis is async
    work owned by the main loop. Installed on the agent at startup so that
    module stays synchronous, with no import of this one.

    It does not ask the agent again afterwards, and must not: the pass that
    ordered this is still running and is about to be shown the result itself.
    That is the whole point of chaining — see the 2026-09-12 entry in
    JOURNEY.md — and it also retires a bug. The old path asked the agent again
    from inside `_pass_lock`, which the very pass that ordered the research
    still held, so every one of those wakes was dropped by
    `if _pass_lock.locked(): return`.
    """
    if _main_loop is None:
        raise RuntimeError("No event loop is running; research cannot be dispatched here")
    for ticker in tickers:
        log.info("Running the analysis the agent asked for: %s", ticker)
    failures: dict[str, str] = {}
    future = asyncio.run_coroutine_threadsafe(
        analysis.run_analyses(
            list(tickers),
            on_failure=lambda ticker: notify(
                f"Analysis failed for {ticker} — check the logs."
            ),
            trigger="commissioned",
            failures=failures,
        ),
        _main_loop,
    )
    future.result()
    return failures


# Event-driven agent runs are rate-limited. A triggered analysis takes about
# seven minutes and the watchdog ticks every fifteen, so a busy morning could
# otherwise have the model re-plan the whole book several times an hour against
# a book that has barely moved.
_AGENT_COOLDOWN = datetime.timedelta(minutes=30)
_last_agent_run: datetime.datetime | None = None


async def _settle_agent_fills() -> None:
    """Bring the agent's ledger up to date with the broker, and say so when a
    stop fired. Cheap — one request per still-open order, usually none."""
    if not agent.is_enabled():
        return
    try:
        settled = await asyncio.to_thread(agent.settle_pending)
    except Exception:
        log.exception("Couldn't settle agent orders")
        return
    for fill in settled:
        # A stop firing is the only trade here nobody chose to make, so it is
        # the one worth interrupting for. Ordinary fills already showed up in
        # the run that placed them.
        if fill["was_stop"] and fill["status"] == "filled":
            await notify(agent.format_stop_fill(fill))


async def _maybe_run_agent(label: str = "Event-driven") -> None:
    """Let the agent act on fresh intraday signals, but only when it could
    actually trade on them.

    **This is the trigger path, not the agent's own schedule.** It fires when
    something happened under the agent rather than when the agent asked to be
    woken.

    **The market-hours gate is gone (2026-09-12), and it was load-bearing
    until it was not.** It was here because a move worth *analysing* at midday
    is worth nothing by the next morning. But nothing here commissions an
    analysis any more — the agent is simply told what was seen — and the things
    it can do about that work at any hour: move a stop, commission research so
    the answer is ready for the open, untrack, leave a note, pick its next
    wakeup. The earnings check is the clearest case: it runs pre-market and,
    with the gate in place, could never have woken anybody at all.

    The morning sweep this docstring used to describe is gone (2026-09-08).
    Analyses now arrive because the agent paid for them, one at a time, so
    there is no batch of signals waiting to be weighed against each other.

    """
    global _last_agent_run
    if not agent.is_enabled():
        return
    now = datetime.datetime.now(datetime.timezone.utc)
    if _last_agent_run is not None and now - _last_agent_run < _AGENT_COOLDOWN:
        log.info("Agent ran %s ago — inside the cooldown, skipping", now - _last_agent_run)
        return
    # **Stamped only once a pass is really going to happen.** It used to be set
    # here, before the two calls below, so a pass that was then skipped — by the
    # lock, or by an alarm that had already fired — still spent the full thirty
    # minutes. On a busy morning that silently suppressed every later trigger.
    #
    # Pull the pending alarm forward rather than running beside it. Firing a
    # one-off also deletes it, so the time the agent is about to supersede
    # cannot arrive later and ask a question it has already answered.
    if wake_agent_now(label):
        _last_agent_run = now
        return
    if _pass_lock.locked():
        log.info("A pass is already running; not spending the cooldown on a skip")
        return
    _last_agent_run = now
    await _run_agent_pass(label)


# The last calendar date a final pass ran, so it happens once a session. Held in
# memory rather than stored: a restart during the last five minutes of a day
# re-running it costs one prompt, and the alternative is a column that exists
# only to guard against that.
_last_final_pass: datetime.date | None = None

# The pending alarm for the agent's next chosen time, if one is set.
#
# In memory because quiv's own task table is in memory: a restart deletes both,
# and _restore_wakeup_alarm rebuilds them together from the database.
_wakeup_task_id: str | None = None

# What is firing the pending alarm early, when something is. Set by
# wake_agent_now and read by _alarm_job, so the pass tells the agent the real
# reason. None means the alarm fired at the agent's own time.
_early_wake_label: str | None = None

# Two paths can decide a pass is due at the same moment. The alarm fires, and a
# second later the tick reads a next_wakeup the running pass has not replaced
# yet. Without this the agent answers the same question twice and the second
# answer overwrites the first.
_pass_lock = asyncio.Lock()

WAKEUP_TASK_NAME = "agent_wakeup_alarm"


def _replace_wakeup_alarm(when: datetime.datetime | None) -> None:
    """Set the alarm for the agent's next chosen time, replacing any pending one.

    **Replacing, not merely adding.** A ``run_once`` task deletes itself when it
    fires, so an alarm that has done its job needs no cleanup. But not every
    pass consumes an alarm — the last pass before the close does not, and nor
    does the first pass after a restore. One of those would set a second alarm
    while the first was still pending, and both would fire. Removing first makes
    "at most one alarm" true without having to know which path ran.

    A time already past fires at once — quiv's own guarantee for ``run_at``
    now, not a clamp we compute. That covers the container having been down
    when the wakeup came due: the answer is to ask the agent now, not to drop
    the wakeup it chose.

    **Passes ``when`` straight through as ``run_at`` rather than converting it
    to a delay ourselves** (quiv >=0.10.0, github.com/nandyalu/quiv#66, filed
    from this exact call site). The old form read the clock twice — once here,
    once inside quiv when it computed the deadline — and the gap between the
    two reads was a small, real error nothing here could remove. ``run_at``
    also needs no ``interval`` for a one-off any more (quiv >=0.9.0, #65); the
    ``interval=1`` used to exist only because quiv rejected a non-positive one
    even though a run-once task never reads it.
    """
    global _wakeup_task_id
    if _wakeup_task_id is not None:
        try:
            scheduler.remove_task(_wakeup_task_id)
        except Exception:
            # Already fired and deleted itself, which is the ordinary case.
            log.debug("No pending wakeup alarm to remove")
        _wakeup_task_id = None
    if when is None:
        return
    _wakeup_task_id = scheduler.add_task(
        task_name=WAKEUP_TASK_NAME,
        func=agent_wakeup_alarm,
        run_at=when,
        run_once=True,
    )
    # For the log line only — quiv reads its own clock for the actual
    # scheduling decision, so a few milliseconds of drift here costs nothing.
    minutes = max(0.0, (when - market_clock.now_et()).total_seconds()) / 60
    log.info("Wakeup alarm set for %s (in %.0f min)", when.strftime("%a %-I:%M %p"), minutes)


def restore_wakeup_alarm() -> None:
    """Rebuild the alarm from the database when the app starts.

    **This is the one step that can end the experiment.** quiv keeps its tasks
    in a temporary file that a restart deletes, so without this the agent has no
    alarm and nothing else schedules it.

    ``agent.wakeup_due`` supplies the fallback when the stored wakeup is missing
    or unreadable, so an agent with no usable time still gets asked at the next
    open. The one-minute tick is the second guard: if this ever fails silently,
    it notices within a minute.
    """
    runs = agent.db.get_agent_runs(limit=1)
    wanted = runs[0].next_wakeup if runs else None
    if wanted is not None and wanted.tzinfo is None:
        wanted = wanted.replace(tzinfo=datetime.timezone.utc)
    if wanted is None:
        wanted = market_clock.next_open()
        log.info("No stored wakeup; the agent will be asked at the next open")
    _replace_wakeup_alarm(wanted.astimezone(market_clock.US_MARKET_TZ))


# How many entries in backend/agent_changes.json had already been announced as
# of the last startup this container saw. A count rather than the newest date,
# because the file can gain two entries with the same date in one deploy (it
# did, on 2026-09-08) — a second deploy later the same day would then look
# like "nothing new" if compared by date. The file only ever grows by
# appending (see agent.describe_recent_changes), so a rising count is an
# unambiguous "the agent has not been told about this yet," however the dates
# line up.
_CHANGES_SEEN_COUNT_KEY = "agent_changes_seen_count"


def wake_agent_for_new_changes() -> None:
    """If a change note was written down since this container last started,
    wake the agent now rather than making it wait for its own next chosen
    time — which can be up to four days out.

    Called once from register_jobs, after restore_wakeup_alarm, so there is
    always a pending alarm to pull forward. **Never awaited from the lifespan
    that calls register_jobs**: pulling the alarm forward, or scheduling a
    fresh one-off task, both just hand off to quiv's own worker thread and
    return at once — a slow decision pass can never hold up the app coming
    up.

    Losing the seen-count to a database reset is an acceptable failure mode:
    the worst case is one extra wakeup after the reset, re-announcing
    something the agent may already have seen. Never waking it for a real
    change would be the worse failure, and there is no way to have neither
    without a marker that survives every reset a marker in the database
    cannot.
    """
    global _early_wake_label
    changes = agent.load_change_notes()
    seen = int(db.get_setting(_CHANGES_SEEN_COUNT_KEY) or 0)
    if len(changes) <= seen:
        return
    db.set_setting(_CHANGES_SEEN_COUNT_KEY, str(len(changes)))
    log.info(
        "%d new agent change note(s) since this container last started — waking the agent now",
        len(changes) - seen,
    )
    if not wake_agent_now("Change"):
        # No pending alarm to pull forward — should not happen right after
        # restore_wakeup_alarm, but a fresh one-off is the honest fallback
        # rather than silently doing nothing. No interval needed for a
        # one-off since quiv 0.9.0 (#65) — see _replace_wakeup_alarm.
        # The one-off runs the same alarm job, so it carries the same label.
        _early_wake_label = "Change"
        scheduler.add_task(
            task_name="agent_wakeup_new_change", func=agent_wakeup_alarm,
            delay=0, run_once=True,
        )


def wake_agent_now(label: str | None = None) -> bool:
    """Pull the pending alarm forward instead of letting it fire stale.

    Used when something else makes a pass worth running early — an analysis the
    agent asked to see today, or a watchdog trigger. Because the alarm is a
    one-off, firing it now also deletes it, so the time the agent has just
    superseded cannot arrive later and ask a question it has already answered.

    ``label`` names what woke the agent early, as a key of ``_WOKE_BECAUSE``.
    The alarm job reads it, so the pass gives the real reason. Until 2026-09-13
    every early wake told the agent "You asked to be woken now". None means the
    agent's own time, which is what the alarm means when it fires on schedule.

    Returns False when there is no alarm to pull, which leaves the caller to run
    the pass itself.

    **Catches the two specific errors this race can raise, not every
    exception** (quiv >=0.9.0, github.com/nandyalu/quiv#67, filed from this
    exact call site). Before that fix, an already-fired one-off raised
    ``HandlerNotRegisteredError`` — a name that means something else — instead
    of ``TaskNotFoundError``. A genuine ``HandlerNotRegisteredError`` here
    would mean the handler was never registered at all, which is a real bug
    and should not be swallowed alongside the two harmless races.
    """
    global _wakeup_task_id, _early_wake_label
    if _wakeup_task_id is None:
        return False
    _early_wake_label = label
    try:
        scheduler.run_task_immediately(_wakeup_task_id)
    except (TaskNotFoundError, TaskNotActiveError):
        # Already fired (deleted itself) or already running. Either way a
        # pass is happening, and it is not the one this label describes.
        _early_wake_label = None
        log.debug("Could not pull the wakeup alarm forward")
        return False
    _wakeup_task_id = None
    return True


def agent_wakeup_alarm() -> None:
    """quiv's entry point for the alarm. Runs on a worker thread."""
    run_on_main(_alarm_job)


async def _alarm_job() -> None:
    global _wakeup_task_id, _early_wake_label
    _wakeup_task_id = None  # it has fired; quiv has deleted its row
    # Read and cleared together, so a label never outlives the wake it names.
    label, _early_wake_label = _early_wake_label or "Alarm", None
    if not agent.is_enabled():
        return
    await _run_agent_pass(label)


# What each wake path means in the agent's own reading. The labels were log
# lines only until 2026-09-12: four different reasons for a pass, and the agent
# was told none of them, so it could not tell its own chosen time from a move
# it slept through.
_WOKE_BECAUSE = {
    "Alarm": "You asked to be woken now.",
    "Wakeup": "You asked to be woken around now.",
    "Event-driven": "Something was noticed while you were away. You did not ask for this pass.",
    "Earnings": "A company you track reports earnings soon. You did not ask for this pass.",
    "Final": "This is the last pass before the close. Anything you want done today has to be done now.",
    "Change": "A change to this app woke you. You did not ask for this pass.",
}
# **No reason here promises a section.** One did — "see what it was, below" —
# and the earnings path reaches the same pass with no alerts to show, so the
# prompt pointed at nothing. build_prompt adds the pointer, because only it
# knows whether the section exists. Found by probing: seven runs read past a
# line that promised something the prompt did not contain.


async def _run_agent_pass(label: str) -> None:
    """One decision pass, and everything that follows from it.

    Shared by every path that wakes the agent, so a pass behaves the same
    however it was triggered — the notification, the immediate research
    dispatch, the next alarm, and the failure handling are one implementation.
    """
    if _pass_lock.locked():
        log.info("%s pass skipped — one is already running", label)
        return
    async with _pass_lock:
        await _run_agent_pass_locked(label)


async def _run_agent_pass_locked(label: str) -> None:
    try:
        run = await asyncio.to_thread(agent.run_once, _WOKE_BECAUSE.get(label))
    except Exception:
        log.exception("%s agent run failed", label)
        # Without an alarm the agent never runs again, so a failed pass still
        # has to leave one behind. The next open is the honest fallback: the
        # pass produced no answer, so the agent chose nothing.
        _replace_wakeup_alarm(market_clock.next_open())
        return
    _replace_wakeup_alarm(run.next_wakeup or market_clock.next_open())
    # A note is worth posting even on a day it did nothing else: it is the
    # agent saying it is short of something, which is the point of having it.
    if run.acted or run.rejected or run.failed or run.notes:
        await notify(embed=agent.format_run_embed(run))


async def _agent_wakeup_job() -> None:
    """The backstop, and the last pass before the close.

    **The alarm is the mechanism now.** The agent's chosen time goes to quiv as
    a one-off task, which fires to the second. This tick exists because that
    alarm lives in a temporary file a restart deletes, and it is rebuilt at
    startup by ``restore_wakeup_alarm``. If that restore ever fails silently the
    agent would never wake again, so this notices within a minute and runs the
    pass itself.

    In normal operation it finds nothing: the alarm has already run the pass and
    written the next wakeup, so ``wakeup_due`` returns None.

    **Polled rather than scheduled, and the reason is durability.**

    An earlier version of this comment said quiv cannot schedule a one-off
    task. That is wrong: ``add_task(..., run_once=True, delay=n)`` fires once,
    at a time of your choosing, to the second.

    The real objection is that quiv keeps its tasks in a temporary SQLite file
    that is deleted on shutdown, so nothing it holds survives a restart. A
    one-off alarm set on Friday for Monday's open disappears the moment the
    container is rebuilt — which happens most days here. Nothing else would
    wake the agent, and that is the silent stop ``agent.wakeup_due`` exists to
    prevent.

    The wakeup lives in the database instead, on the run that asked for it,
    and this tick re-reads it. A restart loses nothing.

    The cost is being up to a minute late. A pass takes about a minute of GPU
    to think, and the agent asks for gaps of fifteen minutes and up, so the
    error is smaller than the thing it is scheduling.

    **A one-off would be exact, and it needs less machinery than an earlier
    version of this comment claimed.** quiv deletes a ``run_once`` task after it
    fires, so there is nothing to cancel, and tasks are independent, so there is
    nothing to replace. Two additions would do it: restore a pending alarm at
    startup, and add one after each pass.

    The one real complication is that a pass can run before its own alarm — an
    event-driven run, or a watchdog trigger. Friday has an example: the pass at
    17:00 superseded an alarm set for 17:12, which would still have fired. The
    fix is for the task to check whether it is still the newest run's wakeup,
    and that check is this function.

    So the choice is sixty seconds against one new way to never wake up: the
    startup restore. Nothing here needs sub-minute timing. If it ever does,
    keep this tick as the backstop and add the one-offs on top, so a missed
    restore costs a minute rather than the experiment.

    The final pass is decided against the real Eastern close rather than a
    fixed UTC time, so it does not drift by an hour twice a year the way the
    other jobs here do.

    **No cooldown on this path.** The cooldown exists to stop the watchdog
    re-planning a book that has barely moved. The agent choosing its own time
    is the opposite case: it named that moment, and overriding it would make
    the tool a suggestion.
    """
    global _last_final_pass
    if not agent.is_enabled():
        return
    now = market_clock.now_et()

    # **The market-hours gate is gone.** The agent sets its own times now, and
    # the fixed 13:35 pass that used to be the only entry point is removed.
    #
    # That job's docstring carried a fact worth keeping: Webull rejects a
    # market order outside the session outright
    # (``CAN_NOT_TRADING_FOR_FIXGW_NOT_READY_NIGHT``). An agent woken at
    # 3am can still read, research and plan; an order it sends then comes back
    # as a broker failure, which the next prompt shows it.
    #
    # The morning analyses have to be
    # commissioned before the day starts, and that is a timing decision the
    # agent can make for itself. It is told the market is shut, the broker
    # refuses an order while it is, and that refusal reaches the next prompt.
    due = agent.wakeup_due(now)
    if due is not None:
        log.info("Agent asked to be woken at %s", due.strftime("%a %-I:%M %p"))
        await _run_agent_pass("Wakeup")
        return

    # A last look before the close, but only if the agent has not just had
    # one. It used to run whatever the agent asked, which on 2026-09-04 meant
    # two passes eleven minutes apart.
    if (
        watchdog.is_us_market_hours()
        and now.time() >= market_clock.FINAL_PASS
        and _last_final_pass != now.date()
        and not _ran_recently(now)
    ):
        _last_final_pass = now.date()
        log.info("Final pass of the session")
        await _run_agent_pass("Final")


def _ran_recently(now: datetime.datetime, within=datetime.timedelta(minutes=30)) -> bool:
    """True when a pass has already run in the last half hour.

    Guards the end-of-day pass. An agent that asked to be woken at 3:45 has
    reviewed the day; waking it again at 3:55 spends a prompt to be told the
    same thing.
    """
    runs = agent.db.get_agent_runs(limit=1)
    if not runs:
        return False
    ran_at = runs[0].ran_at
    if ran_at is None:
        return False
    if ran_at.tzinfo is None:
        ran_at = ran_at.replace(tzinfo=datetime.timezone.utc)
    return (now - ran_at.astimezone(now.tzinfo)) < within


def agent_wakeup() -> None:
    run_on_main(_agent_wakeup_job)


async def _daily_signals_job() -> None:
    """21:30 UTC (17:30 ET): grade what matured, then write the journal.

    Stays after the close because grading reads the day's closing price. The
    watchlist sweep used to run here, then moved to the morning, and as of
    2026-09-08 does not run automatically at all — the agent commissions
    research itself now, on whatever schedule it chooses. See JOURNEY.md.
    """
    # Weekday-only: US markets are closed Sat/Sun, running would just waste a GPU pass.
    if datetime.datetime.now(datetime.timezone.utc).weekday() >= 5:
        return
    await _evaluate_pending_signals()
    # Written every evening, after grading, so the day's verdicts are in the
    # story rather than a day behind. Each run regenerates the month files
    # from the book, so today lands in the current month's file beside
    # yesterday — a timeline, not a folder of one-day notes.
    try:
        written = await asyncio.to_thread(journey.write_month_files)
        if written:
            log.info("Journey written: %s", ", ".join(written))
    except Exception:
        log.exception("Could not write the journey")


def daily_signals() -> None:
    run_on_main(_daily_signals_job)


# morning_sweep (11:00 UTC, the whole watchlist analysed whether the agent
# would have asked or not) was removed 2026-09-08. The agent now commissions
# every analysis itself, held tickers included — see JOURNEY.md for the
# reasoning and agent._commission_research for what replaced the dispatch
# this job used to do. trigger="sweep" stays a valid value on old Signal
# rows; it is simply never written again.


async def _place_queued_exits() -> None:
    """Arm the positions someone queued while the market was shut, and say so."""
    try:
        results = await asyncio.to_thread(agent.process_queued_arms)
    except Exception:
        log.exception("Could not process queued exit arming")
        return
    for result in results:
        icon = "🛡️" if result["ok"] else "⚠️"
        await notify(f"{icon} {result['message']}")


async def _alert_watchdog_job() -> None:
    """Rule-based intraday scan (no LLM): move/volume/stop/target alerts,
    plus event-triggered analyses. Long triggered runs just delay the next
    tick, which doubles as backpressure on the shared GPU."""
    if not watchdog.is_us_market_hours():
        return
    # Before the scan: a resting stop can trigger at any moment, and it is the
    # one fill nobody is waiting for. Settled only when the agent next decided,
    # the book would show a position that had already been sold — for the rest
    # of the day, and into the next morning's decision.
    await _settle_agent_fills()
    # Then the exits someone asked for while the market was shut. First tick
    # after the open drains the queue, which is the whole promise of the button
    # — a request made in the evening and silently dropped would be worse than
    # not offering to remember it.
    await _place_queued_exits()
    try:
        alerts = await asyncio.to_thread(watchdog.scan_for_alerts)
    except Exception:
        log.exception("Alert watchdog scan failed")
        return
    for alert in alerts:
        await notify(alert.message)
    # **Tell the agent, do not act for it (2026-09-12).** A sharp move used to
    # commission an analysis on the spot — sixteen minutes of GPU and $0.05 of
    # the agent's own research budget, spent on a decision it was never asked
    # about. A move is the moment "sell it now" matters most, and the agent was
    # not even asked until the study it had not ordered had finished.
    #
    # It is woken instead, and the alerts are in its prompt. If it decides the
    # move is worth studying it can commission research itself, which now runs
    # inside that same pass.
    if alerts:
        await _maybe_run_agent()


def alert_watchdog() -> None:
    run_on_main(_alert_watchdog_job)


async def _export_public_snapshot_job() -> None:
    """Regenerate the static files behind the public site. Runs only here —
    register_jobs() is never called under PUBLIC_MODE (see app.py's
    lifespan), so this job exists on the private container alone, the one
    with live data to export."""
    try:
        await asyncio.to_thread(snapshot_export.export_all)
    except Exception:
        log.exception("Public snapshot export failed")


def export_public_snapshot() -> None:
    run_on_main(_export_public_snapshot_job)


async def _earnings_check_job() -> None:
    """Pre-market (13:00 UTC = 8/9am ET): find out who reports soon, and say so.

    **It used to commission an analysis for each one (until 2026-09-12)**, on
    the agent's behalf and out of the agent's research budget. An earnings date
    is worth knowing; whether it is worth studying is the agent's call, and it
    may well prefer to sell before the report rather than pay to read about it.

    Stored rather than passed, because the prompt is built somewhere else
    entirely and calling the earnings calendar per ticker while assembling a
    prompt would put a network request per tracked name on the critical path of
    every pass.
    """
    if datetime.datetime.now(datetime.timezone.utc).weekday() >= 5:
        return
    try:
        upcoming = await asyncio.to_thread(watchdog.earnings_due)
    except Exception:
        log.exception("Earnings calendar check failed")
        return
    await asyncio.to_thread(agent.store_earnings_dates, upcoming)
    if upcoming:
        log.info("Reporting soon: %s", ", ".join(f"{t} {d}" for t, d in upcoming))
        await _maybe_run_agent("Earnings")


def earnings_check() -> None:
    run_on_main(_earnings_check_job)



async def _morning_regime_job() -> None:
    """Pre-market context post (12:45 UTC, before the earnings task): VIX,
    SPY vs 200-day, yield curve — rule-based, no LLM."""
    if datetime.datetime.now(datetime.timezone.utc).weekday() >= 5:
        return
    try:
        message = regime.format_regime_message(await asyncio.to_thread(regime.fetch_regime))
    except Exception:
        log.exception("Morning regime snapshot failed")
        return
    await notify(message)


def morning_regime() -> None:
    run_on_main(_morning_regime_job)


async def _weekly_digest_job() -> None:
    """Friday 23:00 UTC — after the daily 21:30 sweep has had time to finish."""
    if datetime.datetime.now(datetime.timezone.utc).weekday() != 4:
        return
    try:
        embed = await asyncio.to_thread(build_weekly_digest_embed)
    except Exception:
        log.exception("Weekly digest failed")
        return
    await notify(embed=embed)
    # Once a week, with the digest. Following a ticker costs about seven
    # minutes of GPU on every sweep from then on, so this is a decision to
    # make deliberately rather than a feed to skim daily.
    try:
        found = await asyncio.to_thread(candidates.fetch_candidates)
    except Exception:
        log.exception("Candidate screen failed")
        return


def weekly_digest() -> None:
    run_on_main(_weekly_digest_job)




def register_jobs() -> None:
    """Registers the scheduled jobs on the shared `scheduler`. Called once
    from backend/app.py's lifespan on every startup — quiv's task state is an
    in-memory/temp-file affair (see quiv's own docs), nothing persists
    across restarts.

    **That last sentence is why `restore_wakeup_alarm` is here.** The agent's
    next wakeup is a one-off quiv task, so a restart deletes it. Rebuilding it
    from the database is what keeps the agent running across a redeploy, and
    skipping it would leave an agent that never wakes and reports nothing
    wrong."""
    global _main_loop
    # register_jobs is called from app.py's lifespan, which is a coroutine, so
    # the running loop is the one every async job will use. A worker thread
    # running a decision pass hands its research back to it — see
    # _research_for_agent.
    _main_loop = asyncio.get_running_loop()
    agent.set_research_runner(_research_for_agent)
    scheduler.add_task(task_name="alert_watchdog", func=alert_watchdog, interval=900)
    # Same 15-minute cadence as alert_watchdog. The public site is a snapshot,
    # not a live view, and a 1-2 week holding horizon has no need for
    # anything tighter than this.
    scheduler.add_task(task_name="export_public_snapshot", func=export_public_snapshot, interval=900)
    scheduler.add_task(task_name="daily_signals", func=daily_signals, interval=86400, run_at=_next_utc_time(21, 30))
    scheduler.add_task(task_name="earnings_check", func=earnings_check, interval=86400, run_at=_next_utc_time(13, 0))
    scheduler.add_task(task_name="morning_regime", func=morning_regime, interval=86400, run_at=_next_utc_time(12, 45))
    scheduler.add_task(task_name="weekly_digest", func=weekly_digest, interval=86400, run_at=_next_utc_time(23, 0))
    # The backstop for the alarm, not the alarm itself. See _agent_wakeup_job.
    scheduler.add_task(task_name="agent_wakeup", func=agent_wakeup, interval=60)
    # Last, so the agent's alarm is rebuilt only once everything it may need is
    # registered — a restored wakeup can be due immediately.
    restore_wakeup_alarm()
    # After the alarm, not before: this pulls that alarm forward, so it needs
    # one to already exist.
    wake_agent_for_new_changes()
