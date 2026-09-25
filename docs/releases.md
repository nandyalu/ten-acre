# Releases

**The notes for each tagged release, newest first.** Each release on [GitHub](https://github.com/nandyalu/ten-acre/releases) uses the same text. A release gives a zip with the app wheel, the `TradingAgents` wheel, `constraints.txt` and `.env.example`, and a Docker image at `ghcr.io`. For how to use them, see [Run it yourself](deploying.md).

For each change by date, see [the changelog](changelog.md) and [the journey](journey.md).

## v0.4.0 — 2026-09-25

- **A wake that comes during a pass now gets a pass after it.** Before, a watchdog alert, an earnings wake or a fill that came while a pass ran was dropped, and the agent heard of it only at its next own wakeup. Now it is kept, and it is dropped only when the running pass built its last prompt after the event. A pass that comes due during the evening review now waits for the review, where before it was skipped.
- **A stop that the trade stream catches now wakes the agent and reaches its prompt.** The stream settled the fill and posted it to Discord only, so the agent got no `stop_fill` alert and no wake. It now goes through `scheduler.announce_fills`, the same path as the 15-minute poll. A limit buy that filled was missed the same way and is fixed too.
- **The account line gives the equity first.** It started with "Your account is $10,000.00 in total", and pass 88 sized its risk on $10,000 when the book was worth $10,942.50. The stop-fill alert no longer says "No pass ordered this sale", which was false: a pass placed the stop.
- **The scheduler's jobs run on quiv's threads, and quiv's job history is now true.** Every job used to hand its work to the main loop with `run_on_main` and return, so each one showed a one-millisecond success. With quiv 1.2.0 only `notify` and the analyses go to the main loop, through `call_on_main`. The alarm, the five-minute backstop and the final pass are now one task, `agent_pass`, so the jobs page lists 7 tasks.
- **Set `stop_grace_period: 20s` on the `ten-acre` service.** At shutdown the app gives the scheduler 10 seconds to stop its jobs and 3 more for work queued on its loop, and Docker's default of 10 seconds would kill it first. `compose.example.yaml` has the line; a deployed compose file needs it added by hand. A pass that is running stops between turns, never between an order and its record.
- **The Decisions page can show the system prompt**, the rules every pass gets, behind a "Show the system prompt" button. `GET /api/agent/system-prompt` serves it, and the static site reads `agent_system-prompt.json`.
- **The site's tables, the Journal and the Decisions transcript are redesigned.** The trade log, the positions and the analysis list filter and page in the browser. The Journal shows each day's parts on their own, a closed day carries a one-line summary, and one "Show the transcript" button opens the prompt and the model's answer side by side.
- **A test runs the agent task against a real quiv** and checks that the time a pass chooses is kept, and `quiv_findings.md` records how the app uses quiv.

## v0.3.0 — 2026-09-24

- **The agent reviews its own day each evening.** After the close and the grading, it is shown every pass since its last review, what its trades did, and the analyses it bought with what the price did since. It answers two forced calls in one conversation: `report`, notes for whoever maintains it, each naming the pass where a tool or a fact was missing; then `revise`, a new note for the next pass and changes to its memory notes. It can place no order, commission nothing, and cannot move its alarm. The review runs on the tool channel only, so a deployment on the JSON channel skips it with a log line.
- **The next pass sees the review's note in place of the last pass's own, labelled as the review's**, and the fixed rules now say the review exists and may rewrite the note and the memory. A memory note now carries the day it was written and whether a pass or a review wrote it. A note from before this release reads as undated.
- **A new table, `agentreflection`, keeps each review**: both prompts, both answers, the notes, the memory changes and what was applied. The migration runs at startup like every other. `GET /api/agent/reflections` serves it, and the static site gets `agent_reflections.json`.
- **The Decisions page shows each review in its day, above the passes, and the Notes page lists the review's notes with the pass each one names.** Discord gets a post only when a review sent a note or changed memory. Most days it says nothing, and that is the expected answer.
- **`probe_prompt.py --turn reflection` probes the review out of the app**: the real prompt from the database as it stands, both calls, nothing applied and nothing recorded.
- **The notes for each release live in `docs/releases.md`**, and a `release` skill gives the steps to cut one.

## v0.2.3 — 2026-09-24

- **Every Gemini request now has a time limit.** A Google request fails after 300 seconds (`llm_gemini.REQUEST_TIMEOUT_SECONDS`), and `TRADINGAGENTS_LLM_TIMEOUT` can change this value. On 2026-09-24, one request that never returned stopped the agent for ten hours.
- **The agent now sees a stop or target that fills during a pass.** Each turn of a pass settles pending orders before it builds the book, so the agent no longer sees shares that it sold already. A fill found during a pass is sent as an alert, and a limit buy that fills during a pass wakes the agent again.
- **A refused `adjust` now says when the exit already filled.** The agent gets "that exit already filled at $X, so there is nothing left to move" and not the raw broker error `OPENAPI_ORDER_CANT_NOT_BE_REPLACE`. For all other refusals, the agent still gets the broker's own words.
- **Tests no longer read the developer's database for pass records.** The prompt tests now keep pass records in memory, so they pass when the local `data/trading.db` has not had the latest migration.

## v0.2.2 — 2026-09-23

This release puts the agent's prompt in four headed groups, tells the agent why each pass started, which stop or target filled and when the next pass comes, moves a stop on every lot of a position, lets the agent decide with its own Gemini model, and publishes the Docker image to ghcr.io.

## v0.2.1 — 2026-09-19

No notes were written. See the [commits from v0.2.0](https://github.com/nandyalu/ten-acre/compare/v0.2.0...v0.2.1).

## v0.2.0 — 2026-09-19

No notes were written. See the [commits from v0.1.0](https://github.com/nandyalu/ten-acre/compare/v0.1.0...v0.2.0).

## v0.1.0 — 2026-09-18

The first release.
