# Ten Acre — Claude Code context

**One autonomous agent trades one simulated book with $10,000.** The FastAPI and Angular dashboard and a notification-only Discord bot are in `backend/` and `frontend/`. The analysis comes from `TradingAgents/`, a vendored multi-agent framework held as a git submodule. For the architecture, read [README.md](README.md) and [docs/overview.md](docs/overview.md). For frontend work, read `frontend/CLAUDE.md`. For the backlog and the work that was rejected, read [PLAN.md](PLAN.md).

## Why this exists

**Give the agent proper tools inside reasonable restrictions, and let it trade.** The question is whether an AI agent can trade profitably when it has real tools, not whether it can trade blindfolded. That sentence settles most arguments about this codebase.

- **A tool is what the agent needs to decide well:** research it chooses, exits it can move, a way to say what it is missing, timing it controls. A missing tool is a gap in the experiment. The agent MUST NOT work around one. The `note` action exists for this: "I cannot see X" is evidence.
- **A restriction keeps the experiment honest or the account solvent, and does nothing else.** Python refuses what cannot be executed as stated, and NEVER resizes, because a resize turns the agent's decision into a different one.
- **When a tool and a restriction conflict, the tool wins.** A restriction that exists only because nobody built the tool yet is a gap, not a rule.
- **NEVER put a second decision-maker in the record.** No human hand, and no second model that can change an outcome. Afterwards nothing can tell which one produced a result.
- **Cut the confusion, fund the deliberation.** Make the prompt clearer before you limit what the agent may read. Bound what could loop. NEVER ration what only costs tokens.

## Four guards keep this a simulation. NEVER relax one.

The prompt may lie to the model. The code MUST never lie to itself. The reasoning and the dates are in `.claude/rules/webull.md`.

1. **`_assert_sandbox()` runs immediately before every order**, never once at import.
2. **The account number MUST carry the `DE` prefix**, in the DEM and the DEL series both.
3. **The target account is resolved by `account_class == INDIVIDUAL_CASH`**, never hardcoded.
4. **`WEBULL_ACCOUNT_ID` names the one account this deployment owns.** An unset or empty value stops order flow. NEVER add a fallback.

## Invariants for every change

- **NEVER add a manual control.** No slash command, and no button that adds a ticker, starts an analysis or places a trade. To correct something, write an entry in `JOURNEY.md` that says what and why, then make the change by hand. `POST /api/agent/exits/{ticker}` is the one write endpoint left, and it decides nothing.
- **NEVER start an analysis the agent did not order.** Every analysis charges the agent's research budget.
- **Route every daily-history read through `bars.get_bars()`**, never `yf.Ticker(...).history()` and never a direct Webull call.
- **NEVER add a position-size cap without its own `JOURNEY.md` entry and reasoning.** A cap changes what the agent may decide. The sizing rule in the prompt is a method, not a cap: Python still refuses nothing on size. See the 2026-08-29 entry in [the analyst experiment](docs/analyst-experiment.md).
- **Before you change the agent's prompt**, write the `JOURNEY.md` entry first, then update the quotation in `.claude/rules/agent.md` in the same edit, then run the `probe-the-prompt` skill. A prompt change is a hypothesis until the model's own reasoning confirms it.
- **Run the `stale-check` skill before every commit.** It records the change in the right file and sweeps what goes stale silently. `CLAUDE.md` and `.claude/rules/` hold the rules as they are now. [JOURNEY.md](JOURNEY.md) says when the experiment's question changed. [docs/changelog.md](docs/changelog.md) says what changed about the app.

## Permanent non-goals

- **Real order execution.** Every order goes to the Webull sandbox, and the agent refuses to run without `WEBULL_SANDBOX=1`.
- **Manual controls of any kind.**
- **Shorting.** A sell closes a long. `sandbox_broker` enforces it, because a margin account shorts where a cash account refuses.
- **Intraday LLM analysis.** An analysis takes about eighteen minutes on the local model, so alerts stay rule-based.

## Deployment in brief

Two deployments, each a container `ten-acre` on an image `ten-acre:local` built on its own host: this machine (Dockge, dashboard on **8125**) and nebula (`ssh nebula`, Portainer, dashboard on **8126**). `docker ps` is the authority, and `docker logs ten-acre` shows the live container. **Dockge and Portainer own the deployed compose files. NEVER edit them on disk** — give the user the exact snippet to paste into the right editor. Everything else is in `.claude/rules/deployment.md`.

## Session hygiene

- **One task per session.** Before you switch to an unrelated task, run `/kit:handoff`, then `/clear`, then say "continue from handoff <topic>".
- **Long work runs in the background.** Use the `wait-in-background` skill. A `PreToolUse` hook refuses a foreground wait loop.
- **Keep output short.** Use `graft skeleton`, `tail -20`, or a line range, never a whole file or a whole log.

## Where the detailed notes are

A rule file loads when Claude reads a file that matches its `paths:` list, so a session about the site never pays for the notes about the GPU pool. **A `graft` query does not load a rule file**, because graft runs through Bash and not through the Read tool. Read a file before you edit it, and its rules load. If you change code in an area and have read none of its files, read the rule file itself.

| Rule file | What it covers |
|---|---|
| `agent.md` | The agent contract: what the agent is shown, the prompt rules quoted verbatim, what Python refuses, how to probe the prompt |
| `analysis-output.md` | Tool errors, per-run cost telemetry, invented price levels |
| `llm-providers.md` | Provider and model switching, Gemini thinking, rate limits, billing, the two naming layers |
| `gpu-pool.md` | The seven-card Ollama pool, concurrency, custom context builds |
| `deployment.md` | The compose files, the static public site, the R2 publisher, the three ways to run it, the one data directory |
| `tradingagents-submodule.md` | The fork, its remotes, the cherry-picks, how to move the pin |
| `market-data.md` | The bar cache, tickers that stop trading, Reddit |
| `webull.md` | The Webull OpenAPI, combo orders, order history, the four guards in full |
