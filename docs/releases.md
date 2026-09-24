# Releases

**The notes for each tagged release, newest first.** Each release on [GitHub](https://github.com/nandyalu/ten-acre/releases) uses the same text. A release gives a zip with the app wheel, the `TradingAgents` wheel, `constraints.txt` and `.env.example`, and a Docker image at `ghcr.io`. For how to use them, see [Run it yourself](deploying.md).

For each change by date, see [the changelog](changelog.md) and [the journey](journey.md).

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
