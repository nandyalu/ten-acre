# Releases

**The notes for each tagged release, newest first.** Each release on [GitHub](https://github.com/nandyalu/ten-acre/releases) uses the same text. A release gives a zip with the app wheel, the `TradingAgents` wheel, `constraints.txt` and `.env.example`, and a Docker image at `ghcr.io`. For how to use them, see [Run it yourself](deploying.md).

For each change by date, see [the changelog](changelog.md) and [the journey](journey.md).

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
