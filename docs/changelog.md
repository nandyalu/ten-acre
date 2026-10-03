# Changelog

**Everything that changed about the app but not about the agent.** Deployment, setup, guards, infrastructure, the site, the docs and dependencies. The Claude Code tooling in `.claude/`, such as hooks, skills and the usage bar, is not the app, and its changes are not recorded here.

Changes to what the agent does, what it is shown, or what its record contains go in [the journey](journey.md) instead, and that page states the test. The split exists because the journey answers one question — when did the experiment's question change — and it can only answer it if the reader is not wading through image builds and CSS.

Entries are one or two lines: what changed, and why. Newest first.

Everything before experiment 2 is on [the experiment 1 changelog](experiment-1-changelog.md).

## 2026-10-03

- **Analysis** — Each analysis gets the agent's book (`analysis.portfolio_context()`: holdings with quantity and average cost, and the free cash) through `propagate(portfolio=...)`. The yfinance override for statements is gone, so they come from SEC EDGAR as filed, and the fundamentals analyst also fetches insider transactions.
- **Dependencies** — The TradingAgents fork is rebased onto upstream v0.5.2, and the pin moves to `d1f1f78`. `yf_retry` now lives in `tradingagents.dataflows.vendors.yahoo.common`, so `bars`, `fundamentals`, `positions`, `regime` and `watchdog` import it from there. The lock moves many packages, and the fork needs Python 3.11 or later and pandas 3.

## 2026-10-02

- **Deployment** — A market container: the same image with `MARKET_MODE=1`. It holds the Webull keys, runs no agent and no scheduler, and answers `/api/market/*`. A book with `MARKET_DATA_URL` asks it first. `compose.experiment2.example.yaml` adds the service `ten-acre-market` (port 8130). A migration (`c4f2a8e9d613`) adds the table `marketfetch`. No existing row changes. See [the journal](journey.md) for the reason.
- **Data** — A new hourly job, `corporate_actions`, reads splits and spin-offs and applies them to the stored data. Two new tables, `corporateaction` and `systemprompt`, come with one migration (`b6e1c4d8f372`). No existing row changes. The Alpaca broker reads the feed from its data host with keys and no account number. See [the journal](journey.md) for what the agent sees.
- **Broker** — A third broker, `BROKER=sim`, runs inside the process. It has no host, no SDK and no credential, so no setting can point it at real money. It fills market orders at the price the agent was shown plus `SIM_SLIPPAGE_BPS` (default 5), and fills limits, stops and targets from the 1-minute bars after the order. Its orders are in the new `simorder` table. `webull` stays the default, and nothing changes for a deployment that does not set it. The setup page shows "Simulated trading" as ready for it. This is the first step of experiment 2 in [the plan](https://github.com/nandyalu/ten-acre/blob/main/PLAN.md); no deployment uses it yet.
- **Data** — Each candidate screen now writes the names it returned to a new table, `candidatescreen` (ticker, source, price, volume, time), before the agent's watchlist is taken out. Experiment 2's random control books draw their tickers from it, so every book of one day uses one list. A migration adds the table. No existing row changes.
- **Scripts** — `backend/scripts/experiment2_report.py` reads the databases of all books of experiment 2, builds the random control books from the bar cache and the stored screens, and applies the success test.
- **Deployment** — `compose.experiment2.example.yaml` runs four books from one image, `ten-acre:exp2`, each with `BROKER=sim`, its own data volume and one pinned model. It is an example, and no running deployment uses it.
- **Scripts** — `backend/scripts/replay.py` and `backend/services/replay.py` send a recorded turn to the model again. They are not wired into the app.
- **Docs** — The journal and the changelog of experiment 1 moved, unchanged, to `docs/experiment-1-journey.md` and `docs/experiment-1-changelog.md`. `JOURNEY.md` and this page now start with experiment 2, so each stays readable from end to end. A code comment that cites an old date means an entry on the archive page. Both archive pages are in the site navigation.
