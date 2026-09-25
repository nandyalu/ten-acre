# quiv findings for ten-acre

Written 2026-09-25 from a review of how ten-acre uses quiv, with the running container as evidence. Line numbers are as of that day. Paths are under `backend/` unless they start with `/`.

All quiv use sits in `tasks/scheduler.py`. The pin is `quiv>=1.0.0` (`/pyproject.toml:22`) and the lock resolves 1.0.0.

## Runtime evidence

Twenty minutes of the `ten-acre` container after its last start, quiv logger lines only. The window is short because the container was recreated.

| Evidence | Value |
| --- | --- |
| quiv in the running container | 1.0.0 |
| scheduler warnings or errors | 0 |
| failed or cancelled jobs | 0 |
| longest job | 6 ms |
| leftover temp databases, restarts | none |
| Docker stop grace | default, 10 s |

Every job is a few milliseconds long because every handler hands its work to the main loop and returns. See the second finding.

## Fix now, in ten-acre

**Done 2026-09-25, on quiv 1.2.0, which shipped `call_on_main`.** The handlers do their work on quiv's threads, and only `notify` and the analyses go to the main loop, through `call_on_main`. The alarm, the backstop and the final pass became one task, `agent_pass`, moved by a `JOB_*` listener, because quiv discards a `run_at` written while the task runs. `tests/test_wakeup_alarm.py` ends with the end-to-end test against a real `Quiv()`. Still open: a `timeout=` on each task, the stale notes, and `QUIV_POOL_SIZE`.

- [x] **Bump to `quiv>=1.1.0` and relock.** `/pyproject.toml:22`. 1.0.1 fixed the traceback that `run_on_main` logged for a coroutine cancelled at shutdown, which this app sees once per restart (`.claude/handoffs/fundamentals.md:11`, `:23`; `/docs/changelog.md:50`). 1.1.0 added the two methods the next two items use.
- [x] **Move the alarm with `update_task(run_at=...)`.** The module globals `_wakeup_task_id` (`tasks/scheduler.py:285`) and `_final_pass_task_id` (`:303`), and the remove-before-add in `_replace_wakeup_alarm` (`:306-314`) and `_arm_final_pass` (`:595`), exist because a one-off could not change its time. `update_task(task_id, run_at=when)` keeps the id and writes one row. When the alarm already fired and deleted itself, `update_task` raises `TaskNotFoundError`; add a new one then. Do not move an alarm whose job is running: quiv deletes a run-once row when the job finishes, so the new time is lost, and it logs a warning.
- [x] **Poll `pending_main_loop_work()` in the lifespan.** `app.py:79` calls `scheduler.shutdown()` and returns. Every handler hands work to the main loop, so `shutdown()` returns while that work may still be queued, and the loop then cancels it. After `shutdown()`, loop on `while scheduler.pending_main_loop_work(): await asyncio.sleep(0.05)` with a bound. This answers the open question in `.claude/handoffs/quiv-prs.md:22`.
- [x] **Give `shutdown()` a timeout and set the stop grace period.** The jobs are millisecond hops, so the risk is low, but Docker's default grace is 10 s and the compose file sets none. `shutdown(timeout=5)` keeps the stop inside the grace.
- [x] **Know that quiv records nothing about the real work.** Each handler is `run_on_main(_xxx_job)` and returns at once (`:416`, `:592`, `:622`, `:708`, `:768`, `:783`, `:813`, `:831`, `:855`). quiv's job history shows a one-millisecond success even when the body fails or hangs, and a per-task `timeout` would never fire. The Gemini hang on 2026-09-24 that held `_pass_lock` until a restart (`/docs/journey.md:60`) is that case. Until quiv v1.3.0 ships, the 300 s request timeout is the only guard; keep it.
- [x] **Update the stale notes.** `api/routes/jobs.py:4` said "6 scheduled jobs"; it now says "7 scheduled tasks" and names `agent_pass`. `.claude/handoffs/quiv-prs.md` and `fundamentals.md`, which carried the other stale claims (open PRs since merged, a wrong line number, an already-shipped fix called unreleased), are gone — a handoff is cleared after the task it covered.
- [ ] **Document `QUIV_POOL_SIZE`.** `tasks/scheduler.py:43` reads it, and neither `.env.example` nor `compose.example.yaml` mentions it.

Things that are right and should stay: `_next_utc_time` (`:46-62`) with `interval=86400` is the correct daily-at-a-time recipe and will be documented in quiv as such; the weekday gates inside the jobs (`:663`, `:799`, `:820`, `:836`) belong there; `restore_wakeup_alarm` (`:352-371`) and the 300 s backstop tick (`:527-570`) are the intended way to live without durable persistence.

## When quiv v1.3.0 ships (Phase 8, waiting on work)

- [x] **Replace `run_on_main` with `call_on_main` in the nine handlers.** `call_on_main` waits for the coroutine on the main loop and returns its result, and an exception in the body reaches the handler. The quiv job then carries the real duration and the real failure, `JOB_FAILED` fires, and `max_retries` and `timeout` apply. A `timeout` on the task cancels the coroutine when it fires, which is the guard the Gemini hang needed.
- [ ] **Add `timeout=` to each task** once the handlers wait: the analyses, the passes, and the watchdog each have a natural bound.
- [x] **Write one end-to-end alarm test against a real `Quiv()`.** Every test stubs `add_task`, `remove_task`, and `run_task_immediately` (`tests/test_wakeup_alarm.py:31-53`). With `wait_for_task(task_id, timeout=...)` a test can add the alarm with a `run_at` a second away, start the scheduler, and assert the finished job, so a scheduling regression shows up in CI instead of in production.

## When quiv v1.4.0 ships (Phase 9, operations)

- [ ] **Add scheduler liveness to the health endpoint** with `scheduler.is_running`.
- [ ] **Queue a wake during a pass.** `wake_agent_now` (`:374`, the call at `:403`) catches `TaskNotActiveError` when the alarm task is running and drops the wake. `run_task_immediately(task_id, after_current=True)` runs it once more when the current job finishes.
- [ ] **Read the "Running in a container" page** and compare it with the compose file and the restore logic.

## Not quiv's problem, keep as is

- The double container start on redeploy, 20 to 35 s apart (`fundamentals.md:17-18`), is a deploy issue. The restore from the database makes it harmless for the alarm.
- The heavy work runs on the main loop's default executor through `asyncio.to_thread`, and `_analysis_semaphore` (`services/analysis.py:108-109`) bounds it. quiv's pool and backpressure never see it. That stays true after `call_on_main`, which only makes the handler wait for it.
- The blocking sleeps in job paths (`candidates.py:158`, `agent.py:4401`, `agent.py:4588`, `llm_throttle.py`) block the default executor's threads, not quiv's.
