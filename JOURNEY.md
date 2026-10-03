# The agent's journey — what we changed, and why

The app writes its own record: what it bought, what that cost, and what the agent said about it, day by day. Every sentence in it comes from a trade, a charge, or a decision pass, so it cannot drift from the book.

Read it at `/api/agent/journey`, or as files: one per month, under a folder per year, in the data volume beside the database and the logs.

```
data/journey/2026/08-August.md
data/journey/2026/09-September.md
```

The app rewrites them after grading each evening. `python -m backend.scripts.write_journey` regenerates them on demand.

Each file opens with the month in four numbers: positions opened, positions closed, research spent, and where the book started and finished. That way a file reads on its own, not only as part of a series. That is what makes them publishable later: a month is a post.

**The app generates them, and rewriting them is how they stay true.** Do not edit them. The next write discards the edit. Put commentary here instead.

**This file is the other half, and it is the half the app cannot write.** It knows the agent changed its mind. It does not know that we changed the prompt the week before, or added a research charge, or settled the debate-round count by running an experiment.

Without those causes, a month of record is a list of events nobody can learn from.

Two rules, both learned the hard way elsewhere in this project:

- **Write it when it happens, not afterwards.** A reason you reconstruct two weeks later is a story about what you would like to have been thinking.
- **Record what was wrong, not only what worked.** The entries that say "this turned out to be noise" are worth more than the ones that say "this worked". They are what stops someone proposing the same idea again in three weeks.


---

## What belongs here, and what does not

**One question decides it: does this change make two periods of the experiment non-comparable?** If yes, it goes here. If no, it goes in [the changelog](https://github.com/nandyalu/ten-acre/blob/main/docs/changelog.md).

That splits into three tests. Any one of them is enough:

- **Behaviour** — it changes what the agent is shown, what it may ask for, or what Python refuses.
- **Evidence** — it changes what the record contains or means. Telemetry counts: a field that is null before a date is exactly what trips up whoever reads the data later.
- **Incident** — the agent's behaviour changed without anyone intending it, so real days are contaminated.

Everything else is a changelog entry: setup, deployment, guards, infrastructure, site copy, docs, dependencies. Those matter, and they are not this.

**Keep an entry to a sentence or two — what changed, and why.** Long-form reasoning that constrains a future edit belongs in `CLAUDE.md`, or in the `.claude/rules/` file for that area. Those are read before the code is changed. This file answers "when did the question change", and it can only do that if it stays readable end to end.

## Every change to the agent, and why

**This file covers experiment 2: four books, one agent each, on the in-process simulated broker.** The design and the success test are in [PLAN.md](https://github.com/nandyalu/ten-acre/blob/main/PLAN.md). Experiment 1, the single agent that traded Webull's sandbox from 2026-09-01, has its own page, [the experiment 1 journey](https://github.com/nandyalu/ten-acre/blob/main/docs/experiment-1-journey.md). A rule file or a code comment that cites a JOURNEY.md entry by a date from before experiment 2 means an entry there, unless the entry is below.

`CLAUDE.md` and `.claude/rules/agent.md` describe what the rules are. This describes how they got that way. Add an entry here **before** changing a rule, not after.

Newest first.

**2026-10-03 — three more inputs reach every analysis: the agent's own book, statements as filed, and insider trades.** The Trader, the Portfolio Manager and the three risk analysts now read each holding with its quantity and average cost, and the free cash, where they read "Portfolio context: not provided" since the rebase earlier today. An analysis can now tell adding to a position from opening one. The fundamentals analyst reads the quarterly statements from SEC EDGAR, at the values filed by the analysis date, where it read Yahoo's; the overview still comes from Yahoo. It also reads recent insider buying and selling, which no analyst here read before. These come from the maintainer's decisions on the v0.5.2 rebase: SEC EDGAR is the better source, a missing tool is a gap, and the book is something an analysis needs to size a trade. None of it changes the agent's own prompt, and Python still refuses nothing on size. Like the rebase, it reaches the live analyses at the next deploy, planned for 2026-10-05 on both deployments.

**2026-10-03 — the TradingAgents fork is rebased onto upstream v0.5.2, and five things change in what an analysis prompt says.** The branch had drifted from upstream through four releases of cherry-picks, and v0.5.1 moved most modules, so each upstream fix had become a manual port. Our own changes are now eight commits on top of the upstream tag; the list is in `.claude/rules/tradingagents-submodule.md`. What reaches an analysis: the Trader, the Portfolio Manager and the three risk analysts read "Portfolio context: not provided", because the backend passes no portfolio; the rating is the Portfolio Manager's typed rating, and a free-text answer is read only from its `Rating:` label; the news analyst's tools take the ticker from the run, and after 20 tool rounds it is told to write its report; every analyst opens with the ticker, where three of them used to open with a placeholder sentence; and the sentiment prompt has one sentence about screened posts, a screen this app does not turn on. Statements stay on yfinance, although v0.5.2 reads SEC EDGAR first, so the fundamentals analyst reads what it read before. Tool output for NVDA was the same on both versions, apart from float rounding. Two analyses on `gemini-3.5-flash-lite` outside the app gave NVDA Overweight and AMD Underweight, each in about 80 seconds with every report present. **It reaches the live analyses at the next deploy.** The week of 2026-10-05 measures the move to `gemini-3.8-flash`, so a deploy before that week ends puts two changes into it.

**2026-10-02 — in experiment 2, every book takes its market data from one market container.** Quotes, daily and minute bars, candidate screens and corporate actions come from one container, so two books see the same input at the same moment. Before, each book fetched its own, and a book without Webull keys saw Yahoo's delayed close. A book that cannot reach the market container fetches its own data and counts it in a new table, `marketfetch` (day, kind, source, count). The report lists those days for each book, because on those days the inputs can differ. A book without `MARKET_DATA_URL`, and experiment 1, work as before. Rows in `marketfetch` start on the first day a book has `MARKET_DATA_URL`.

**2026-10-02 — a split or a spin-off is applied to the book, the way a broker applies one, and the agent is told.** `corporate_actions.check` runs every hour and reads splits, reverse splits and spin-offs from Alpaca's corporate-actions feed, or Yahoo's split list without Alpaca keys. A split moves every number from before the ex-date to the new units: the agent's ledger, the simulated broker's orders and resting exits, stored signal levels, cached prices and bars. A spin-off puts the new shares in the book and moves part of the cost to them, after the ex-date's close; until then the parent's simulated stop and target are paused. Each one reaches the agent as a row in "What was noticed since your last pass". Before this, CTVA's spin-off on 2026-10-01 read as an 88% crash in the bars, and a split would have filled a resting stop at the open.

**2026-10-02 — the agent gets two fetches of raw data, `bars` and `news`.** `bars` returns a ticker's completed daily sessions, open, high, low, close and volume, for up to 250 sessions. `news` returns the items the analysis's news analyst reads: Yahoo, Google News, Finnhub and the company's SEC 8-K filings, for up to the last 30 days. Until now every fetch gave the agent what someone else concluded, or a list, and the question of the experiment is whether the agent can trade with real tools. The fixed rule that names the fetches names the two new ones. The analysis stays as research the agent may buy. This ships on the experiment-2 branch and does not reach experiment 1.

**2026-10-02 — each turn of a decision pass now records which system message it was sent.** The turn record gets `system_sha`, a hash of the message, and the text is stored once per hash in a new table, `systemprompt`. The fixed rules change rarely, so the text is not repeated on every turn. Before, a replay of an old turn could not know which fixed rules the turn saw, and the prompt in the code today is not the prompt of last week. Every turn before this date has no hash. The agent sees nothing new. Experiment 2 needs the hash, because it sends the same turn again.
