# Ten Acre — Claude Code context

**One autonomous agent trades one simulated book with $10,000.** The FastAPI and Angular dashboard and a notification-only Discord bot are in `backend/` and `frontend/`. The analysis comes from a vendored multi-agent framework, `TradingAgents/`, which is a git submodule. See [README.md](README.md) and [docs/overview.md](docs/overview.md) for the architecture. This file covers only what those do not.

## Where the detailed notes are

**This file holds what every session needs. The long notes for one part of the code are in `.claude/rules/`.** A rule file loads when Claude reads a file that matches its `paths:` list, so a session about the site never pays for the notes about the GPU pool.

| Rule file | What it covers | It loads when Claude reads, for example |
|---|---|---|
| `agent.md` | The agent contract: what the agent is shown, the prompt rules quoted verbatim, what Python refuses, how to probe the prompt | `backend/services/agent.py`, `backend/tasks/scheduler.py`, `backend/agent_changes.json`, `JOURNEY.md` |
| `analysis-output.md` | Tool errors, per-run cost telemetry, invented price levels | `backend/services/analysis.py`, `TradingAgents/tradingagents/agents/**` |
| `llm-providers.md` | Provider and model switching, Gemini thinking, rate limits and billing | `backend/services/llm_*.py`, `backend/services/analysis.py` |
| `gpu-pool.md` | The seven-card Ollama pool, concurrency, custom context builds | `ollama/**`, `docs/gpu-*.md`, `backend/services/analysis.py` |
| `deployment.md` | The two compose files, the static public site, the R2 publisher, the three ways to run it and the one data directory | `Dockerfile*`, `scripts/**`, `compose.example.yaml`, `backend/services/snapshot_export.py`, `backend/paths.py`, `pyproject.toml`, `.github/workflows/**` |
| `tradingagents-submodule.md` | The fork, its remotes, the cherry-picks, how to move the pin | `TradingAgents/**`, `.gitmodules`, `pyproject.toml` |
| `market-data.md` | The bar cache, tickers that stop trading, Reddit | `backend/services/bars.py`, `listings.py`, `quotes.py`, `watchdog.py` |
| `webull.md` | The Webull OpenAPI, combo orders, order history | `backend/services/sandbox_broker.py`, `quotes.py`, `intraday.py` |

**A `graft` query does not load a rule file**, because graft runs through Bash and not through the Read tool. Read a file before you edit it, and its rules load. If you change code in an area and have not read one of its files, read the rule file itself.

## What the experiment is for

**Give the agent proper tools inside reasonable restrictions, and let it trade.** That sentence decides most arguments about this codebase. Read it before you propose a change to what the agent may do.

- **A tool is something the agent needs to decide well:** research it chooses, exits it can move, a way to say what it is missing, timing it controls. If the agent lacks one, the experiment is not set up properly yet. The agent must not work around it.
- **A restriction exists only to keep the experiment honest or the account solvent.** The agent never spends more cash than it has. It never sells shares it does not hold. No shorting, no options, no real money. Python refuses what cannot be executed as stated, and never resizes, because resizing turns the agent's decision into a different one.
- **When a tool and a restriction conflict, the tool wins.** A restriction that exists only because nobody built the tool yet is a gap, not a rule.

**The question is whether an AI agent can trade profitably when it has real tools**, not whether it can trade while blindfolded. The note action exists for the same reason: "I cannot see X" is the experiment reporting a missing tool, and it is evidence.

**Cut the confusion, fund the deliberation.** A prompt that leaves the agent to work out the year, which of two same-day analyses is current, or how long an analysis takes burns tokens that a clearer prompt saves for free. An agent that reads six analyses before it commits money does what a careful person does. When a limit exists only because an unbounded version might loop, bound it. Do not ration it.

**The one thing that is not a tool is a human hand.** A control that lets a person nudge the book puts a second decision-maker in the record, and afterwards nothing can tell which one produced a result.

**Since 2026-09-01 there are no manual controls anywhere.** No Discord slash commands, and no button that adds a ticker, starts an analysis, or places a trade. **Do not add one back.** To correct something, write an entry in `JOURNEY.md` that says what and why, then make the change by hand. The one write endpoint left is `POST /api/agent/exits/{ticker}`. It decides nothing: it rests the stop and target the agent already chose, under shares it already owns, when the broker refused the bracket at purchase. The tag `v1-two-book-experiment` marks the commit before the removal.

## Rules that apply to every change

- **Nothing but the agent commissions an analysis.** Do not add a path that analyses something the agent did not order. Every analysis charges the agent's research budget.
- **Every multi-ticker caller goes through `analysis.run_analyses()`.** A `for` loop that awaits `run_analysis_and_notify` looks correct and runs the whole batch one analysis at a time.
- **Route every daily-history read through `bars.get_bars()`**, not `yf.Ticker(...).history()` or a direct Webull call.
- **The model is never asked for a price.** `resolve_levels` computes the levels from the verified close and ATR. Never remove the deviation check in `analysis._trade_plan_levels`.
- **A tool error goes back to the model, not only to the logs.** Every `ToolNode` sets `handle_tool_errors`.
- **A closed market refuses the order, never the pass.** Do not add a timing gate to `run_once`.
- **Text the app shows a person uses the long variable names**, such as `TRADINGAGENTS_LLM_PROVIDER`. The short names are compose aliases only.
- **Point a self-hoster at `compose.example.yaml`.** `dockge/` is gitignored and exists only on this machine.

## Four guards keep this a simulation, and none of them may be relaxed

**The prompt may lie to the model. The code must never lie to itself.** Since 2026-09-09 the prompt tells the agent "You manage a small account of real money", because an agent that knows the stakes are imaginary is not asked the question this experiment exists to ask. The four checks below are the code's own knowledge of what it is connected to.

- **`_assert_sandbox()`** runs immediately before every order, not once at import, so a change to the environment mid-process cannot leave a live client armed.
- **The `DE` account-number prefix check.** Every simulated account on the sandbox host is DE-prefixed, in both the DEM and DEL series. The widening from `DEM` to `DE` on 2026-09-03 corrected a wrong observation and was not a relaxation.
- **The account-class check.** The target is resolved by `account_class == INDIVIDUAL_CASH`, never hardcoded.
- **`WEBULL_ACCOUNT_ID` names the one account this deployment owns.** An unset or empty value stops order flow and never falls back to anything. It takes the account number (`DE…`) or the internal id, and it applies after the other three checks, so it can only narrow what they allowed. There is no default on purpose: a person must write down which book the container owns.

**The day someone relaxes one of them because the agent thinks the money is real anyway is the day this becomes dangerous.**

## Deployment in brief

- **One deployment, `trading-experiment`, runs since 2026-09-02.** The dashboard is on port **8125**. `docker ps` is the authority. `docker logs trading-experiment` shows the live container.
- **The experiment starts on 2026-09-02, not the 1st.** `frontend/src/app/shared/experiment.ts` holds that date as one constant.
- **The deployed compose file is `/opt/stacks/trading-experiment/compose.yaml` with its `.env`.** It is root-owned and managed in the Dockge UI. Do not edit it with sudo. Give the user the exact snippet to paste into the Dockge compose editor. Every secret is in `.env` and read with `${VAR}`.
- **The public site `ten-acre.nandyalu.com` is static files** on Cloudflare Pages, with the JSON on R2. No server, database or credential is on the public path. `deployment.md` has the details.

## Markdown conventions

Do not hard-wrap prose. Write each sentence or paragraph on one line. Zensical, the `docs/` site generator, can mis-render a sentence split across source lines. This applies to every `.md` file in the repo.

## What is not built, and what will never be

**Read this before you propose work.** Two of these were rejected on purpose, and a proposal to add one arrives about once a month.

Open, and worth building:

- **Replay:** run a past decision pass again against a changed prompt, so a prompt change can be told apart from a market change.
- **Backtester:** grade the strategy over history, not only forward.
- **A watchlist ageing rule.** The cap of 30 stops growth, but nothing drops a name the agent stopped holding and stopped asking about.
- **A position-size cap, deliberately absent.** A cap changes what the agent may decide. It needs its own journal entry and reasoning, not a quiet fix. See the 2026-08-29 entry in [the analyst experiment](docs/analyst-experiment.md).
- **Stronger news and sentiment sources for `TradingAgents/tradingagents/dataflows/`.** A human picking trades reads news, MACD-style signals, and sentiment; the sentiment analyst tries to give the model the same, but Reddit alone (RSS, 429-prone) is thin and unreliable, and the analysis is poorer for it. Benzinga and MarketWatch's free feeds were tried for candidate *discovery* on 2026-09-15 and dropped there because a headline names a company, not a ticker, and guessing one is an invented fact. That objection goes away for a news *source feeding an analysis*: a ticker is already known before the fetch, so resolving it to a company name for the query is a one-time, free lookup (Webull or yfinance both return one from a profile call) — not a guess made after the fact. Worth revisiting with that fix.
- **The same news and sentiment sources could also let the agent pick its own tickers to research or watch**, not only feed an analysis already under way. This is a second use of the sources above, not a restatement of them: give the agent a tool call it can invoke on demand — in the pattern of `read`, `research`, and `note` — rather than folding the sources into the standing prompt, where they would cost prompt tokens on every pass whether or not the agent wants them that turn.
- **The decision-making agent sees far less of a report than the model that wrote it — partly fixed 2026-09-15.** A read now carries a 500-character take from each of `market_report`, `sentiment_report`, `news_report` and `fundamentals_report`, alongside the rationale (still capped at 1,400 characters). `analysis_reader._analyst_snapshot` does this on demand, when the agent asks to read an analysis. **Probed the same day: 2 of 2 samples ignored it**, reasoning entirely from the signals and holdings tables and quoting nothing unique to the four sections — shipped anyway, unconfirmed as read, on the same reasoning as the holdings price-range column. **Still open: the ambient signals table**, where every tracked ticker's last analysis is shown with no way to see the four analysts behind it at all, and more probe samples to firm up the read-or-not finding above — see the 2026-09-15 entry in JOURNEY.md.

Permanent non-goals:

- **Real order execution.** Every order goes to the Webull sandbox, and the agent refuses to run without `WEBULL_SANDBOX=1`.
- **Manual controls of any kind.**
- **Shorting.** A sell closes a long. `sandbox_broker` enforces it, because a margin account shorts where a cash account refuses.
- **Intraday LLM analysis.** An analysis takes about eighteen minutes on the local model, so alerts stay rule-based.

## Where a change gets written down

Three files answer three questions:

| File | Question |
|---|---|
| `CLAUDE.md` and `.claude/rules/` | What are the rules now, and what reasoning must a future edit not undo? |
| [JOURNEY.md](JOURNEY.md) | When did the experiment's question change? |
| [docs/changelog.md](docs/changelog.md) | What changed about the app: deployment, setup, guards, infrastructure, the site, the docs, dependencies? |

**One question decides between the journal and the changelog: does this change make two periods of the experiment non-comparable?** If yes, write it in `JOURNEY.md`. Any one of these tests is enough:

- **Behavior:** it changes what the agent is shown, what it may ask for, or what Python refuses.
- **Evidence:** it changes what the record contains or means. A field that is NULL before a date counts.
- **Incident:** the agent's behavior changed without anyone intending it, so real days are contaminated.

**`JOURNEY.md` entries are a sentence or two: what changed, and why.** Long reasoning that constrains a future edit goes in `CLAUDE.md` or in the rule file for that area.

**Before you change the prompt**, add the `JOURNEY.md` entry first, with the date and the reason. Then update the quotation in `.claude/rules/agent.md` in the same edit, and probe the change with the `probe-the-prompt` skill. A prompt change is a hypothesis until the model's own reasoning confirms it. Run the `stale-check` skill before you commit.

## Working with Claude Code in this repo

Every step Claude takes re-reads the whole session. Before 2026-09-13, sessions here ran for days at 400k to 600k tokens, and that was most of the usage.

- **One task per session.** Before you switch to an unrelated task, use the `handoff` skill, then run `/clear`.
- **Long work runs in the background.** Use the `wait-in-background` skill. A `PreToolUse` hook refuses a foreground wait loop.
- **A `UserPromptSubmit` hook reports the session size** above 150k tokens, so Claude can suggest `/clear` when the topic changes. `CONTEXT_NUDGE_TOKENS` changes the threshold.
- **Keep output short.** Use `graft skeleton`, `tail -20`, or a line range, not a whole file or a whole log.
