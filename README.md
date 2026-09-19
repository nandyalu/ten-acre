<!-- The mark follows the reader's theme. GitHub honours <picture> with
     prefers-color-scheme, and the two files differ only in ink — see
     frontend/public/logo/README.md for what the mark means and the rules for
     using it. -->
<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="frontend/public/logo/logo-dark.svg">
    <img src="frontend/public/logo/logo-light.svg" alt="Ten Acre" width="96" height="96">
  </picture>
</p>

<h1 align="center">Ten Acre</h1>

<p align="center"><strong>One question, asked in public: what does an autonomous AI agent do with $10,000?</strong></p>

<p align="center">
  <a href="https://ten-acre.nandyalu.com">The live site</a> ·
  <a href="CONTRIBUTING.md">Contributing</a> ·
  <a href="AI_POLICY.md">Using AI on this project</a> ·
  <a href="JOURNEY.md">The journey</a>
</p>

*Ten Acre* — a small plot with a fence around it, and one worker left alone to farm it. That is the setup exactly.

Ten Acre is a self-hosted experiment. An agent gets a simulated brokerage account, a fixed budget, and a bill for every piece of research it orders. It decides what to study, what to buy, what to sell, and when to give up on a name. Nobody helps it. A web dashboard and a Discord channel report what it did.

The agent runs a multi-agent AI analysis ([TradingAgents](TradingAgents/README.md)) on the stocks it chooses to watch. The app records every call that analysis makes and grades each one against reality, against a SPY buy-and-hold, and against the analysis's own price target.

**No real money is involved and no order can reach a real account.** Webull access is the sandbox only.

## Nobody can nudge it, and that is the point

There is no button that adds a ticker, starts an analysis, or places a trade. There are no Discord commands. The app used to have twenty-three of them and they are all gone.

A control that lets a person nudge the book puts a second decision-maker in the record. Afterwards, nothing can tell which one produced a result — and a record with two authors is not evidence about either.

When something needs correcting, the route is deliberate: write down what changed and why in [JOURNEY.md](JOURNEY.md), then make the correction by hand. That costs a few minutes and leaves the record readable.

## What happens each day

| Time (UTC) | What runs |
|---|---|
| 12:45 | The market regime line — VIX, SPY against its 200-day average, the yield curve |
| 13:00 | Earnings check: if a tracked ticker reports within two days, the agent is woken and told. Nothing is analysed unless it asks |
| **whenever it asked** | **The agent sets every one of its own passes.** It names the next time, and that time becomes a real alarm. Minimum 5 minutes, maximum 4 days, any hour |
| **5 min before the close** | A last pass before the close, if it has not just had one: 3:55 PM ET, or 12:55 PM on a half-day. Read off the Eastern close rather than the clock above, so it does not drift by an hour twice a year |
| every 15 min | The watchdog: big moves, volume spikes, breached stops, reached targets |
| 21:30 | Grading, then the journal is rewritten |
| Fri 23:00 | The weekly digest: the week's outcomes, the win-rate trend, and the book |

**Nothing is analysed on a schedule, holdings included.** There was a morning sweep that analysed the whole watchlist every day; it ended on 2026-09-08. The agent now commissions each analysis itself, pays $0.05 for it, and gets it back within the hour. What the table above still holds is the small set of things a clock is genuinely better at than a decision: a macro reading, an earnings calendar check, a price watchdog, and the grading of past calls.

The one row that overrides the agent is the final pass, five minutes before the close — whatever time the agent asked for, so no position goes into the night unreviewed.

## What the agent may do

It answers with a list of actions, and Python refuses what cannot be executed as stated. **It never resizes.** Shrinking an order would quietly turn the agent's decision into a different one, and the record would then describe a strategy nobody chose.

- **buy** and **sell** — whole shares, long only, never more cash than it holds. A buy goes out as a bracket: the entry with a stop and a take-profit attached, so the shares are never held with nothing under them.
- **adjust** — move the stop or target on something it already holds.
- **research** — pay $0.05 to have something analysed, new or already tracked. It runs right after the pass that ordered it, and there is no daily count on how many it may commission — only cash. This is the only way anything gets analysed at all.
- **untrack** — stop watching a name, and stop paying for it. It cannot untrack something it holds.
- **next_wakeup** — say when to be asked again. **This is the only thing that schedules the agent.** It may name any hour, including before the open, so it can have the morning's analyses ready. Waking costs nothing, so the agent is shown what its recent wakeups produced rather than charged for them.

## Documentation

| Page | What's in it |
|---|---|
| [Running it yourself](docs/deploying.md) | **Start here to deploy it.** Prerequisites, every environment variable, and what goes wrong |
| [Credentials](docs/setup.md) | How to get each one, and what happens if you skip it |
| [How it works](docs/overview.md) | Architecture, the signal lifecycle, the daily schedule, data sources |
| [The site](docs/dashboard.md) | What each page shows, and how to publish it |
| [What Discord posts](docs/discord.md) | The scheduled posts and the alerts |
| [The daily workflow](docs/trading-workflow.md) | How to read the experiment |
| [Finding your edge](docs/finding-your-edge.md) | How to read the scorecard without fooling yourself |
| [The journey](JOURNEY.md) | Every change to the agent, when, and why |
| [Changelog](docs/changelog.md) | Everything else that changed: deployment, setup, the site, the docs |
| [Model training](docs/model-training.md) | What it would take to make a small model reliable here |
| [Contributing](CONTRIBUTING.md) | **Start here to change anything.** The four rules, where a change gets recorded, and the licence terms |
| [Using AI on this project](AI_POLICY.md) | Most of this was written by an AI assistant. What that costs, and what goes wrong |

## Quick start

1. `cp .env.example .env` and fill it in: `WEBULL_APP_KEY`, `WEBULL_APP_SECRET`, **`WEBULL_SANDBOX=1`**, **`WEBULL_ACCOUNT_ID`**, and your model settings. Optionally `DISCORD_WEBHOOK_URL` for notifications. The agent refuses to trade without the sandbox flag, and places no order without the account id. See [Credentials](docs/setup.md).
2. Build the image. In VSCode, press **Ctrl+Shift+B**. Or run `docker build -t ten-acre:local .`
3. `cp compose.example.yaml compose.yaml`, then `docker compose up -d`. Every setting in it is commented, and it reads its values from `.env`. The container applies its own database migrations at startup.
4. Open the dashboard. If anything is missing it sends you to **`/setup`**, which names each requirement and shows the lines to paste for it.
5. Switch the agent on in Settings. **That day becomes day one** — the start date is stamped then and never moves. It starts with an empty watchlist and buys its first research at the next decision pass.

**[Running it yourself](docs/deploying.md) has the full version**, including two ways to run it without Docker (a direct install from a release zip with `uv tool install`, or build and run from a checkout), how to run without a GPU pool, and how to publish the site read-only.

## Honesty notes

- **The app never places a real order.** Every order goes to Webull's sandbox, and the agent refuses to run at all when the app holds production credentials.
- Signals come from a small local model. Treat each one as a structured second opinion, not as a fact — the scorecard exists to show how much to trust it.
- **A single analysis is one sample.** The model runs at temperature 1, so the same ticker on the same day has returned opposite decisions. The scorecard's by-model breakdown is the only honest way to compare two models.
- Nothing here is financial advice.
- **Most of this codebase was written by an AI assistant.** [AI_POLICY.md](AI_POLICY.md) says what that means and lists, with dates, the specific ways it has gone wrong here.

## Licence

[PolyForm Noncommercial 1.0.0](LICENSE). **Source-available, not open source:** read it, run it, change it and share it for any noncommercial purpose; do not sell it or put it in a commercial product.

Contributions are welcome under the terms in [CONTRIBUTING.md](CONTRIBUTING.md), which asks for a `Signed-off-by` line and one extra grant. Read the licence rather than this summary.
