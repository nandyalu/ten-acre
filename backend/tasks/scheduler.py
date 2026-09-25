"""Scheduled jobs, on quiv. quiv runs each job on its own worker thread, and
the job does its work there. The FastAPI app only registers the tasks, moves
the agent's task when something asks for a pass, and reads quiv's record.

**Since 2026-09-25 the work runs inside the quiv job.** Until then every
handler handed a coroutine to the main loop with ``run_on_main`` and returned
at once, so quiv recorded a one-millisecond success whatever the work did, and
a task ``timeout`` could never fire. Now a job is synchronous, quiv records its
real duration and its real failure, and only the parts that belong to the main
loop go back to it, with ``call_on_main``, which waits for the result:

- ``notify``, because it is async and posts on the main loop.
- ``analysis.run_analyses``, because ``_analysis_semaphore`` is an
  ``asyncio.Semaphore`` bound to the main loop.

**One quiv task runs every decision pass**, ``agent_pass``. quiv never runs a
task beside itself, so two passes cannot overlap, and ``_pass_lock`` only
keeps a pass apart from the evening review, which is a different task. The
agent's chosen time, the final pass before the close, and every early wake
all move that one task. See ``agent_pass``.

quiv has no cron/calendar scheduling (interval, plus an absolute or relative
start) — the four daily jobs below approximate a fixed UTC time via
interval=86400 plus run_at set to the next occurrence of that time, keeping
each job's existing internal weekday/Friday-only gate. alert_watchdog is a
true interval and maps over 1:1.
"""
import datetime
import logging
import os
import threading

from quiv import Event, Quiv, QuivError, TaskNotFoundError, TaskStatus, call_on_main

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
from backend.services import reflection
from backend.services.positions import PriceWindow, get_price_window
from backend.services.signals import SignalEvaluation, evaluate_signal_window, horizon_params

log = logging.getLogger("ten-acre.scheduler")

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


def _utc(when: datetime.datetime) -> datetime.datetime:
    """``when`` with a zone. The database hands back naive UTC."""
    return when.replace(tzinfo=datetime.timezone.utc) if when.tzinfo is None else when


def _notify(message: str | None = None, embed=None) -> None:
    """Post from a job thread. ``notify`` is async and runs on the main loop."""
    call_on_main(notify, message, embed=embed)


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


def _evaluate_pending_signals() -> None:
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
        _notify(_format_outcome_line(signal, evaluation, window.last_close))


def _research_for_agent(tickers: list[str]) -> dict[str, str]:
    """Run the analyses a decision pass just commissioned, and block until done.

    Returns what stopped each analysis that did not finish, by ticker. An empty
    dict means every one finished. Until 2026-09-13 this returned nothing, so
    the pass told the agent a failed analysis had finished.

    **This is what lets a synchronous pass wait for its own research.**
    `agent.run_once` is sync and runs on the agent task's quiv thread; an
    analysis is async work owned by the main loop. ``call_on_main`` runs it
    there and waits. When the app shuts down, quiv sets the job's stop event,
    the wait ends with ``JobCancelledError`` and the analysis is cancelled;
    the agent reports that as research that did not finish. Installed on the
    agent at startup so that module stays synchronous, with no import of this
    one.

    It does not ask the agent again afterwards, and must not: the pass that
    ordered this is still running and is about to be shown the result itself.
    That is the whole point of chaining — see the 2026-09-12 entry in
    JOURNEY.md.
    """
    for ticker in tickers:
        log.info("Running the analysis the agent asked for: %s", ticker)
    failures: dict[str, str] = {}
    call_on_main(
        analysis.run_analyses,
        list(tickers),
        on_failure=lambda ticker: notify(
            f"Analysis failed for {ticker} — check the logs."
        ),
        trigger="commissioned",
        failures=failures,
    )
    return failures


# Event-driven agent runs are rate-limited. A triggered analysis takes about
# seven minutes and the watchdog ticks every fifteen, so a busy morning could
# otherwise have the model re-plan the whole book several times an hour against
# a book that has barely moved.
_AGENT_COOLDOWN = datetime.timedelta(minutes=30)
_last_agent_run: datetime.datetime | None = None


def _settle_agent_fills() -> None:
    """Bring the agent's ledger up to date with the broker, and say so when a
    stop fired or a limit buy filled. Cheap — one request per still-open
    order, usually none."""
    if not agent.is_enabled():
        return
    try:
        settled = agent.settle_pending()
    except Exception:
        log.exception("Couldn't settle agent orders")
        return
    announce_fills(settled)


def announce_fills(settled: list[dict]) -> None:
    """Say what the fills that were just settled mean, to a person and to the
    agent. Called by whatever settled them: the watchdog, or the trade stream.

    **Once, by whoever settled the fill (2026-09-25).** A settled order is no
    longer pending, so no later settle finds it again. The trade stream used
    to settle a stop fill and only post it to Discord, so the watchdog then
    found nothing: no alert row for the next prompt, and no wake.
    """
    stopped = False
    limit_filled = False
    for fill in settled:
        # A stop firing is the only trade here nobody chose to make, so it is
        # the one worth interrupting for. Ordinary fills already showed up in
        # the run that placed them.
        if fill["was_stop"] and fill["status"] == "filled":
            _notify(agent.format_stop_fill(fill))
            # What the next prompt says happened. The wake reason alone
            # names no ticker (2026-09-23).
            agent.record_exit_fill(fill)
            stopped = True
        # A limit buy is the other case an ordinary fill doesn't cover: it
        # never brackets (see agent._place), so it can land with nothing
        # resting under it well after the pass that placed it ended. Checked
        # after was_stop, not instead of it — a resting exit also carries
        # limit_price, so "not was_stop" is what actually says "this is an
        # entry order, not a protective one."
        elif fill["side"] == "buy" and fill.get("limit_price") and fill["status"] == "filled":
            _notify(agent.format_limit_fill(fill))
            limit_filled = True
    if stopped or limit_filled:
        # **Told to the agent, not only to Discord (2026-09-16).** A stop or
        # target closing a position on its own used to reach a person and
        # nobody else — the agent learned only whenever it next happened to
        # wake for some other reason, up to four days later. Same trigger a
        # sharp move or an earnings date already uses.
        # **A stop fill ignores the cooldown (2026-09-23).** The second INTC
        # stop filled 30 minutes after a pass and was skipped, so the agent
        # slept until the next open with a book it did not know it had.
        if stopped:
            _maybe_run_agent("Stop fill", cooldown=False)
        else:
            _maybe_run_agent("Limit buy filled")


def _maybe_run_agent(label: str = "Event-driven", *, cooldown: bool = True) -> None:
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

    **Stamped when the wake is queued (2026-09-25).** Until then a wake that
    came while a pass ran was dropped, and the stamp waited for a pass that
    really started, so a dropped wake could not spend the cooldown. A wake is
    now kept until the pass after the running one, unless the running pass
    already saw it (``_forget_wakes_seen_by``), so a queued wake always has a
    pass that answers it.
    """
    global _last_agent_run
    if not agent.is_enabled():
        return
    now = datetime.datetime.now(datetime.timezone.utc)
    if cooldown and _last_agent_run is not None and now - _last_agent_run < _AGENT_COOLDOWN:
        log.info("Agent ran %s ago — inside the cooldown, skipping", now - _last_agent_run)
        return
    _last_agent_run = now
    wake_agent_now(label)


# The last calendar date a final pass ran, so it happens once a session. Held in
# memory rather than stored: a restart during the last five minutes of a day
# re-running it costs one prompt, and the alternative is a column that exists
# only to guard against that.
_last_final_pass: datetime.date | None = None

# When the next final pass is due. In memory because quiv's task table is in
# memory: a restart deletes both, and restore_agent_task sets both again.
_final_pass_at: datetime.datetime | None = None

# The id of the one task that runs every decision pass. See agent_pass.
_agent_task_id: str | None = None

# A wake that something asked for and no pass has answered yet: its label and
# when it was asked for, in UTC. Guarded by _wake_guard, because the watchdog,
# the trade stream and the agent task each run on their own thread.
_pending_wake: tuple[str, datetime.datetime] | None = None
_wake_guard = threading.Lock()

# When the agent task last ran. A wakeup that was already past then, and is
# still the stored one, was not served: the pass failed, or the agent is
# switched off. Without this the task would run again at once, in a loop. With
# it, the backstop asks again, as the old five-minute tick did.
_agent_task_ran_at: datetime.datetime | None = None

# Keeps a pass apart from the evening review, which is a different quiv task.
# quiv keeps two passes apart by itself. Waited for, never skipped: a pass that
# finds the review running starts when the review ends, and the other way round.
_pass_lock = threading.Lock()

AGENT_TASK_NAME = "agent_pass"

# How often the agent task runs when nothing asks for it sooner. A run with
# nothing due does nothing. It guards the restore: if restore_agent_task ever
# set a wrong time, a pass that is due waits five minutes at most.
_BACKSTOP_SECONDS = 300


def wake_agent_now(label: str) -> None:
    """Ask for a pass as soon as possible, for the reason ``label`` names.

    Used when something else makes a pass worth running early — a watchdog
    trigger, the earnings check, a stop that filled, a position left with
    nothing under it. ``label`` is a key of ``_WOKE_BECAUSE``, and the pass
    gives the agent that reason. Until 2026-09-13 every early wake told the
    agent "You asked to be woken now".

    **Recorded first, then the task is moved.** quiv discards a ``run_at``
    written while the task is running: when the job ends, it computes the next
    run from the interval. So the wake is stored in ``_pending_wake``, and the
    listener ``_after_agent_job`` moves the task once the running pass ends.
    When no pass is running, ``_reschedule`` moves it now. The first reason
    asked for is kept until a pass answers it.
    """
    global _pending_wake
    with _wake_guard:
        if _pending_wake is None:
            _pending_wake = (label, datetime.datetime.now(datetime.timezone.utc))
    _reschedule()


def _forget_wakes_seen_by(looked_at: datetime.datetime | None) -> None:
    """Drop a pending wake that was asked for before the pass last looked.

    That pass built its last prompt after the event, so it has already seen
    it, and a second pass would answer the same question again.
    """
    global _pending_wake
    if looked_at is None:
        return
    with _wake_guard:
        if _pending_wake is not None and _pending_wake[1] <= _utc(looked_at):
            log.info("%s came before the pass last looked; that pass saw it", _pending_wake[0])
            _pending_wake = None


def _stored_wakeup() -> datetime.datetime:
    """The agent's chosen time, from the last stored run, the same way
    ``agent.wakeup_due`` reads it: the next open when the run named none."""
    runs = agent.db.get_agent_runs(limit=1)
    if not runs:
        return market_clock.next_open()
    wanted = runs[0].next_wakeup
    if wanted is None:
        ran_at = runs[0].ran_at
        return market_clock.next_open(_utc(ran_at) if ran_at is not None else None)
    return _utc(wanted)


def _next_pass_time() -> datetime.datetime:
    """When the agent task should run next: the soonest of a pending wake
    (now), the agent's chosen time, the final pass, and the backstop.

    A time already past runs at once — quiv's own guarantee for ``run_at``.
    That covers the container having been down when the wakeup came due: the
    answer is to ask the agent now, not to drop the wakeup it chose.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    if _pending_wake is not None:
        return now
    times = [now + datetime.timedelta(seconds=_BACKSTOP_SECONDS)]
    wanted = _stored_wakeup()
    if _agent_task_ran_at is None or wanted > _agent_task_ran_at:
        times.append(wanted)
    if _final_pass_at is not None:
        times.append(_final_pass_at)
    return min(times)


def _reschedule() -> None:
    """Point the agent task at ``_next_pass_time()``.

    Skipped while a pass runs, because quiv would discard the time when the
    job ends. ``_after_agent_job`` calls this again then. Under
    ``_wake_guard``, so a listener that read the state before a wake came in
    cannot write its older time after the wake has written a newer one.
    """
    if _agent_task_id is None:
        return
    try:
        with _wake_guard:
            if scheduler.get_task(_agent_task_id).status == TaskStatus.RUNNING:
                return
            when = _next_pass_time()
            scheduler.update_task(_agent_task_id, run_at=when)
    except QuivError as exc:
        # A task that is gone or a scheduler that has stopped. The backstop
        # and the restore at the next start cover both.
        log.warning("Could not move the agent task: %s", exc)
        return
    log.debug("Agent task set for %s", when.astimezone(market_clock.US_MARKET_TZ).strftime("%a %-I:%M:%S %p"))


def _after_agent_job(event, task, job) -> None:
    """quiv listener: move the agent task once a pass has ended.

    quiv calls it after it has finalised the job, so the task is active again
    and a ``run_at`` written now is kept. This is what makes the time the pass
    just chose, and any wake that came while it ran, take effect.
    """
    if task.id == _agent_task_id:
        _reschedule()


def restore_agent_task() -> None:
    """Add the agent task, and point it at the next pass.

    **This is the one step that can end the experiment.** quiv keeps its tasks
    in a temporary file that a restart deletes, so without this the agent has
    no task and nothing else schedules it. The agent's chosen time lives in
    the database, on the run that asked for it, and ``_stored_wakeup`` reads
    it back. The final pass comes from the market calendar.
    """
    global _agent_task_id, _final_pass_at
    _final_pass_at = market_clock.next_final_pass()
    when = _next_pass_time()
    _agent_task_id = scheduler.add_task(
        task_name=AGENT_TASK_NAME,
        func=agent_pass,
        interval=_BACKSTOP_SECONDS,
        fixed_interval=False,
        run_at=when,
    )
    for event in (Event.JOB_COMPLETED, Event.JOB_FAILED, Event.JOB_CANCELLED):
        scheduler.add_listener(event, _after_agent_job)
    minutes = max(0.0, (when - datetime.datetime.now(datetime.timezone.utc)).total_seconds()) / 60
    log.info(
        "Agent task set for %s (in %.0f min); final pass %s",
        when.astimezone(market_clock.US_MARKET_TZ).strftime("%a %-I:%M %p"),
        minutes,
        _final_pass_at.strftime("%a %-I:%M %p"),
    )


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
    return (now - _utc(ran_at).astimezone(now.tzinfo)) < within


def _take_final_pass(now: datetime.datetime) -> bool:
    """True when the final pass is due now and should run.

    Due or not, a final pass that has come round is moved to the next session,
    so it never fires twice. Skipped when the agent has just had a pass: it
    used to run whatever the agent asked, which on 2026-09-04 meant two passes
    eleven minutes apart. Skipped too when the session is already over, which
    can only mean the task ran late after a stall — a "final" pass after the
    close reviews nothing. The date guard is belt to ``_ran_recently``'s
    braces: one final pass a session.

    The time comes from ``market_clock.next_final_pass``: five minutes before
    that session's close, so a 1:00 PM half-day gets a 12:55 pass.
    """
    global _final_pass_at, _last_final_pass
    if _final_pass_at is None or now < _final_pass_at:
        return False
    _final_pass_at = market_clock.next_final_pass(now)
    if not watchdog.is_us_market_hours():
        log.info("Final pass skipped — the session is over")
        return False
    if _last_final_pass == now.date() or _ran_recently(now):
        log.info("Final pass skipped — the agent has just had a pass")
        return False
    _last_final_pass = now.date()
    return True


def agent_pass(stop_event: threading.Event | None = None) -> None:
    """quiv's entry point for every decision pass. Runs on a quiv thread.

    **One task, moved, never a second one added.** The task runs at the
    soonest of four times (``_next_pass_time``), and on each run this decides
    which one came:

    1. The final pass before the close, when it is due.
    2. A wake something asked for (``wake_agent_now``).
    3. The time the agent chose, read from its last run
       (``agent.wakeup_due``).
    4. None of them: the backstop run. It does nothing.

    **No cooldown on the agent's own time.** The cooldown exists to stop the
    watchdog re-planning a book that has barely moved. The agent choosing its
    own time is the opposite case: it named that moment, and overriding it
    would make the tool a suggestion.

    **No market-hours gate either.** The agent sets its own times, and a pass
    outside the session can still read, research and plan. Webull rejects a
    market order outside the session outright
    (``CAN_NOT_TRADING_FOR_FIXGW_NOT_READY_NIGHT``), so an order sent then
    comes back as a broker failure, which the next prompt shows the agent.
    """
    global _pending_wake, _agent_task_ran_at
    now = market_clock.now_et()
    _agent_task_ran_at = now
    with _wake_guard:
        wake, _pending_wake = _pending_wake, None
    # Before the switch, so a final pass that comes due while the agent is
    # off still moves to the next session instead of staying due.
    final = _take_final_pass(now)
    if not agent.is_enabled():
        return
    if final:
        label = "Final"
        log.info("Final pass of the session")
    elif wake is not None:
        label = wake[0]
    elif agent.wakeup_due(now) is not None:
        label = "Alarm"
        log.info("Agent asked to be woken now")
    else:
        return
    _run_agent_pass(label, stop_event)


# What each wake path means in the agent's own reading. The labels were log
# lines only until 2026-09-12: four different reasons for a pass, and the agent
# was told none of them, so it could not tell its own chosen time from a move
# it slept through.
_WOKE_BECAUSE = {
    "Alarm": "You asked to be woken now.",
    "Wakeup": "You asked to be woken around now.",
    "Event-driven": "A rule watching your tickers spotted something. You did not ask for this pass.",
    "Earnings": "A company you track reports earnings soon. You did not ask for this pass.",
    "Final": "This is the last pass before the close. Anything you want done today has to be done now.",
    "Stop fill": "A resting stop or target closed one of your positions on its own. You did not ask for this pass.",
    "Unguarded position": "A position of yours has nothing resting under it to protect it. You did not ask for this pass.",
}
# **No reason here promises a section.** One did — "see what it was, below" —
# and the earnings path reaches the same pass with no alerts to show, so the
# prompt pointed at nothing. build_prompt adds the pointer, because only it
# knows whether the section exists. Found by probing: seven runs read past a
# line that promised something the prompt did not contain.


def _run_agent_pass(label: str, stop_event: threading.Event | None = None) -> None:
    """One decision pass, and everything that follows from it.

    Shared by every path that wakes the agent, so a pass behaves the same
    however it was triggered — the notification, the research, the next
    wakeup, and the failure handling are one implementation.
    """
    with _pass_lock:
        _run_agent_pass_locked(label, stop_event)


def _run_agent_pass_locked(label: str, stop_event: threading.Event | None = None) -> None:
    try:
        run = agent.run_once(
            _WOKE_BECAUSE.get(label),
            should_stop=stop_event.is_set if stop_event is not None else None,
        )
    except Exception:
        log.exception("%s agent run failed", label)
        # The pass produced no answer, so the wakeup it was serving stays in
        # the past. _next_pass_time skips it, and the backstop asks again.
        return
    # A wake asked for while this pass ran is kept for the next pass, unless
    # this pass built its last prompt after it and so has already seen it.
    # getattr, because a caller may hand back a stand-in run, and tests do.
    looked_at = getattr(run, "looked_at", None)
    _forget_wakes_seen_by(looked_at)
    # A fill the pass settled itself is one _settle_agent_fills will never
    # find — the trade is no longer pending by the time it looks. Announced
    # here instead, so a stop firing still reaches a person exactly once, and
    # a limit buy that landed mid-pass still says its shares have nothing
    # resting under them.
    limit_filled = False
    for fill in getattr(run, "fills_seen", []):
        if fill["was_stop"]:
            _notify(agent.format_stop_fill(fill))
        else:
            _notify(agent.format_limit_fill(fill))
            limit_filled = True
    if limit_filled and not run.unguarded:
        # Its shares landed with nothing resting under them, and if the fill
        # came on the last turn the pass never saw them. Below, an unguarded
        # position asks for a pass anyway, so this does not double up.
        wake_agent_now("Limit buy filled")
    if run.unguarded:
        # **Asked for after the pass, never during it (2026-09-16).** The
        # pass has finished and chosen its own time; this only brings the
        # next pass forward.
        wake_agent_now("Unguarded position")
    elif _fill_after(looked_at):
        # A stop filled after this pass built its last prompt, so the pass
        # never saw it. See _settle_agent_fills.
        wake_agent_now("Stop fill")
    # A note is worth posting even on a day it did nothing else: it is the
    # agent saying it is short of something, which is the point of having it.
    if run.acted or run.rejected or run.failed or run.notes:
        _notify(embed=agent.format_run_embed(run))


def _fill_after(looked_at: datetime.datetime | None) -> bool:
    """True when a stop or target filled after ``looked_at``."""
    if looked_at is None:
        return False
    for alert in agent.db.get_recent_alerts(limit=20):
        if alert.alert_type not in ("stop_fill", "target_fill"):
            continue
        if _utc(alert.created_at) > _utc(looked_at):
            return True
    return False


def daily_signals() -> None:
    """21:30 UTC (17:30 ET): grade what matured, then write the journal.

    Stays after the close because grading reads the day's closing price. The
    watchlist sweep used to run here, then moved to the morning, and as of
    2026-09-08 does not run automatically at all — the agent commissions
    research itself now, on whatever schedule it chooses. See JOURNEY.md.
    """
    # Weekday-only: US markets are closed Sat/Sun, running would just waste a GPU pass.
    if datetime.datetime.now(datetime.timezone.utc).weekday() >= 5:
        return
    _evaluate_pending_signals()
    # Written every evening, after grading, so the day's verdicts are in the
    # story rather than a day behind. Each run regenerates the month files
    # from the book, so today lands in the current month's file beside
    # yesterday — a timeline, not a folder of one-day notes.
    try:
        written = journey.write_month_files()
        if written:
            log.info("Journey written: %s", ", ".join(written))
    except Exception:
        log.exception("Could not write the journey")
    # Last, after the grading, so the day's verdicts are in front of the
    # agent when it reads its own day.
    _evening_review()


def _evening_review() -> None:
    """The agent reads its own day and speaks twice: to the maintainer, then
    to itself. See ``reflection``.

    Under ``_pass_lock``, and waiting for it rather than skipping: a pass
    still running at this hour is researching, and the review must see that
    pass whole, not half of it. A pass that comes due while the review holds
    the lock waits for it, so the note it rewrites is the one the next pass
    reads. Posted only when it said something — a note to the maintainer, or
    a change to memory — because most days it says nothing, and that is the
    expected answer, not news.
    """
    if not agent.is_enabled():
        return
    with _pass_lock:
        try:
            review = reflection.run_once()
        except Exception:
            log.exception("The evening review failed")
            return
    if review is None or review.skipped:
        return
    if review.notes or review.memory_changed:
        _notify(embed=reflection.format_embed(review))


# morning_sweep (11:00 UTC, the whole watchlist analysed whether the agent
# would have asked or not) was removed 2026-09-08. The agent now commissions
# every analysis itself, held tickers included — see JOURNEY.md for the
# reasoning and agent._commission_research for what replaced the dispatch
# this job used to do. trigger="sweep" stays a valid value on old Signal
# rows; it is simply never written again.


def _place_queued_exits() -> None:
    """Arm the positions someone queued while the market was shut, and say so."""
    try:
        results = agent.process_queued_arms()
    except Exception:
        log.exception("Could not process queued exit arming")
        return
    for result in results:
        icon = "🛡️" if result["ok"] else "⚠️"
        _notify(f"{icon} {result['message']}")


def alert_watchdog() -> None:
    """Rule-based intraday scan (no LLM): move/volume/stop/target alerts.
    quiv never runs it beside itself, so a slow tick delays the next one."""
    if not watchdog.is_us_market_hours():
        return
    # Before the scan: a resting stop can trigger at any moment, and it is the
    # one fill nobody is waiting for. Settled only when the agent next decided,
    # the book would show a position that had already been sold — for the rest
    # of the day, and into the next morning's decision.
    _settle_agent_fills()
    # Then the exits someone asked for while the market was shut. First tick
    # after the open drains the queue, which is the whole promise of the button
    # — a request made in the evening and silently dropped would be worse than
    # not offering to remember it.
    _place_queued_exits()
    try:
        alerts = watchdog.scan_for_alerts()
    except Exception:
        log.exception("Alert watchdog scan failed")
        return
    for alert in alerts:
        _notify(alert.message)
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
        _maybe_run_agent()


def export_public_snapshot() -> None:
    """Regenerate the static files behind the public site. Runs only here —
    register_jobs() is never called under PUBLIC_MODE (see app.py's
    lifespan), so this job exists on the private container alone, the one
    with live data to export."""
    try:
        snapshot_export.export_all()
    except Exception:
        log.exception("Public snapshot export failed")


def earnings_check() -> None:
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
        upcoming = watchdog.earnings_due()
    except Exception:
        log.exception("Earnings calendar check failed")
        return
    agent.store_earnings_dates(upcoming)
    if upcoming:
        log.info("Reporting soon: %s", ", ".join(f"{t} {d}" for t, d in upcoming))
        _maybe_run_agent("Earnings")


def morning_regime() -> None:
    """Pre-market context post (12:45 UTC, before the earnings task): VIX,
    SPY vs 200-day, yield curve — rule-based, no LLM."""
    if datetime.datetime.now(datetime.timezone.utc).weekday() >= 5:
        return
    try:
        message = regime.format_regime_message(regime.fetch_regime())
    except Exception:
        log.exception("Morning regime snapshot failed")
        return
    _notify(message)


def weekly_digest() -> None:
    """Friday 23:00 UTC — after the daily 21:30 sweep has had time to finish."""
    if datetime.datetime.now(datetime.timezone.utc).weekday() != 4:
        return
    try:
        embed = build_weekly_digest_embed()
    except Exception:
        log.exception("Weekly digest failed")
        return
    _notify(embed=embed)
    # Once a week, with the digest. Following a ticker costs about seven
    # minutes of GPU on every sweep from then on, so this is a decision to
    # make deliberately rather than a feed to skim daily.
    try:
        candidates.fetch_candidates()
    except Exception:
        log.exception("Candidate screen failed")


def register_jobs() -> None:
    """Registers the scheduled jobs on the shared `scheduler`. Called once
    from backend/app.py's lifespan on every startup — quiv's task state is an
    in-memory/temp-file affair (see quiv's own docs), nothing persists
    across restarts.

    **That last sentence is why `restore_agent_task` is here.** The agent's
    next pass is a quiv task, so a restart deletes it. Rebuilding it from the
    database is what keeps the agent running across a redeploy, and skipping
    it would leave an agent that never wakes and reports nothing wrong."""
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
    # Last, so the agent's task is added only once everything it may need is
    # registered — a restored wakeup can be due immediately.
    restore_agent_task()
