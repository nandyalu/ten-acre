---
paths:
  - "TradingAgents/**"
  - ".gitmodules"
  - "pyproject.toml"
---

## Vendored TradingAgents repo

**`TradingAgents/` is a git submodule.** `.gitmodules` registers it, and a `160000` gitlink pins it to one commit. This repo tracks which TradingAgents commit is checked out and keeps that repo's history intact. `git clone` needs `--recurse-submodules`. Or run `git submodule update --init` after the clone.

## Remotes

**A fresh submodule checkout gets one remote, named `origin`, and it points at our fork.** That is the URL in `.gitmodules`. This working copy renamed that remote to `fork` and added upstream as `origin`. The rename does not survive a fresh clone. To restore it, run: `cd TradingAgents && git remote rename origin fork && git remote add origin https://github.com/TauricResearch/TradingAgents.git && git fetch origin`.

- `origin` = `https://github.com/TauricResearch/TradingAgents.git`, upstream. Use it only to compare. Do not push to it.
- `fork` = `https://github.com/nandyalu/TradingAgentsUI.git`, our fork. Push the branch here.

## The branch

**The checked-out branch is `trading-helper-custom`, rebased onto upstream tag `v0.6.0` on 2026-10-10.** `git log v0.6.0..trading-helper-custom` lists only our commits, and `git diff v0.6.0 trading-helper-custom` is our whole delta. `fork/main` is an old ancestor at v0.3.1, so compare against the upstream tag, not `fork/main`.

**The branch before each rebase is kept.** The tag `pre-v052-rebase` (`f4d67ab`) and the fork branch `archive/pre-v052-2026-10` hold the history before 2026-10-03. The tag `pre-v060-rebase` (`6521a46`) and the fork branch `archive/pre-v060-2026-10` hold the v0.5.2-based history. Parent-repo commits point at SHAs on those histories, so never delete them.

The v0.5.2 rebase put our changes on top as eight commits, grouped by area, then a comment fix and a README trim. Four commits followed before the v0.6.0 rebase: insider transactions, the shared reports first in prompts, and two for the request label. The eight came from one merge, so only the last of them is known to pass the suite. The message of each commit names the old SHAs it ports.

| Commit | Area | What it carries |
|---|---|---|
| 1 | Router | A `CircuitBreaker` around each vendor call (upstream PR #1071), inside upstream's `route_to_vendor`. `BadVendorArgumentError` marks a wrong request to a healthy vendor: no breaker trip, no fall-through, and the valid values reach the model. `tests/conftest.py` resets the breaker around every test. |
| 2 | LLM clients | `llm_timeout`, forwarded in `llm_clients/factory.py`. The retry of an undecodable JSON body (upstream PR #1074). `json_schema` structured output on a local OpenAI-compatible server, with no tool and no `tool_choice`. `client_args` for Gemini. The Ollama Modelfile guide (upstream PR #1149). |
| 3 | Social and macro data | Reddit: OAuth (upstream PR #1134), trawl one subreddit at a time when `REDDIT_TRAWL_URL` is set, else one public feed. A web-search supplement when Reddit or StockTwits is unavailable. The StockTwits 5 MiB cap (upstream PR #1328). `fred.redact` for FRED's own 400 body. |
| 4 | News | Global news takes one bucket per query in turn, so the first query cannot fill the limit alone. `get_news` adds Google News, Finnhub and SEC 8-K, with an optional laya grade (`LAYA_URL`). |
| 5 | Analysts | The market and fundamentals analysts fetch their data first and declare `TOOLS = ()`, so v0.5.2 builds each as one model turn. The indicator set per horizon is `INDICATORS` and `WINDOWS` in `market_analyst.py`. The news analyst keeps upstream's prompt and wrap-up turn, and adds Gemini search grounding and the printed-tool-call recovery in `agents/tool_call_recovery.py`. The sentiment sources are fetched at the same time. |
| 6 | Decision chain | `TraderProposal` has no price fields: the Trader states `stop_atr_multiple` and `target_r_multiple`, and `resolve_levels` computes the prices. See `analysis-output.md`. The probability and risk/reward review (upstream PR #1082). The horizon instruction in the research manager, trader and portfolio manager (upstream PR #1122). |
| 7 | Graph | `propagate(horizon=..., on_chunk=...)`, with the horizon in the checkpoint signature. Each analyst's tool node returns a tool error to the model (`_return_error_to_the_model` in `graph/setup.py`, re-exported from `trading_graph`; `backend/tests/test_tool_errors_reach_the_model.py` guards it). The news analyst on a separate Google model when grounding is on (`TRADINGAGENTS_GOOGLE_SEARCH_GROUNDING_MODEL`). |
| 8 | Screener and docs | The candidate screener scripts (upstream PR #1122) and the fork section of the README. |

**Gemini grounding needs `tool_config.include_server_side_tool_invocations`.** Gemini rejects `google_search` mixed with the analyst's own tools without it, confirmed against the live API on 2026-09-17. Gemini 3 models get no grounding quota on a Free-tier key; Gemma models (for example `gemma-4-31b-it`) do.

**The #1134 OAuth path is dead for this project.** Reddit's Responsible Builder Policy ended self-serve API app creation, so nobody can get the credentials for a personal tool. The RSS feed is the supported path. See [docs/setup.md](../../docs/setup.md).

**Upstream's parallel analysts replaced ours.** Upstream `9968bd8` runs each analyst in a graph of its own, the same design as our old `fc2b259`. One difference reaches the prompt: every analyst now opens with the ticker as its first message, where analysts two to four used to get a placeholder sentence.

**One upstream change stays out.** `1c44dd1` asks the Trader for absolute entry and stop prices; this fork's schema has no field for one, because model-written prices were unreliable.

**The fundamentals analyst fetches five blocks** (since 2026-10-03): the overview, the quarterly balance sheet, cash flow and income statement, and insider transactions. That is upstream `15b8276`'s insider tool, in this fork's fetch-first form. Statements follow v0.5.2's default chain, `"sec_edgar,yfinance"`, so a US filer's figures are SEC EDGAR's as filed by the analysis date; the overview and insider trades come from Yahoo, which SEC EDGAR does not serve.

**Every analysis gets the agent's book** (since 2026-10-03). `analysis.portfolio_context()` builds a `PortfolioContext` from `agent_book.build_book()`: each holding's quantity and average cost, and the free cash, unpriced. `propagate(portfolio=...)` takes it, and the Trader, the Portfolio Manager and the three risk analysts read it. A changed book also changes the checkpoint signature.

## Editing the fork: the venv does not follow the source

**`uv` does not rebuild `tradingagents` when only its source files change.** `[tool.uv.sources]` installs it from `TradingAgents/` as a built, non-editable copy, and neither `uv run` nor `uv sync` notices an edit that leaves the version alone. The installed copy then lags the source, silently.

**The tests do not catch this, because they do not use the installed copy.** pytest puts `TradingAgents/` on `sys.path`, so `import tradingagents` in a test reads the source tree while the app reads `.venv`. On 2026-09-20 that combination produced two wrong conclusions in one afternoon: a green test run beside a live run that crashed on the bug the tests said was fixed, and a "the fix did not work" verdict on a run that was executing the old code.

**Before any run that is not pytest — a probe, a script, a container-less start — rebuild it:**

```
uv sync --extra dev --reinstall-package tradingagents
diff -q .venv/lib/python3.14/site-packages/tradingagents/dataflows/vendors/reddit.py TradingAgents/tradingagents/dataflows/vendors/reddit.py
```

The second line is the check that matters: compare the file you edited. `--extra dev` is not optional — `uv sync` without it prunes pytest. The Docker image is never affected, because it copies the source tree and builds it.

## Taking the next upstream release

**Rebase our commits onto the new tag.** Do not merge `origin/main`, and do not cherry-pick upstream commits one by one: that is how this branch drifted from upstream before 2026-10-03.

```
cd TradingAgents && git fetch origin --tags
git log --oneline v0.6.0..<new-tag> --no-merges     # what upstream added
git tag -a pre-<new-tag>-rebase -m "before the <new-tag> rebase" trading-helper-custom
git rebase --onto <new-tag> v0.6.0 trading-helper-custom
```

Read `git log --stat` for each upstream commit before you start. For each conflict, apply our intent to upstream's new code; do not restore our old code over theirs. After the rebase, check what reaches a live run: compare `DEFAULT_CONFIG` key by key, compare every agent prompt for one fixed state, and compare each tool's output for one ticker on the two versions. Then update this file, `JOURNEY.md` and `docs/changelog.md`.

**v0.5.1 and v0.5.2 changed these things in a live run, and the rebase kept them** (2026-10-03):

- A portfolio block in the Trader, Portfolio Manager and three risk-analyst prompts. The backend passes the agent's book; without one they would read "Portfolio context: not provided".
- The rating is the Portfolio Manager's typed `rating` (`final_rating`). A free-text decision is read only from its `Rating:` label, and one without a label is `REVIEW`.
- The news analyst's tools take the ticker from the run's state, and its prompt no longer asks for one. After `max_tool_rounds` (20) rounds it is told to write its report.
- A sentence about Jev-screened blocks in the sentiment prompt. Jev itself runs only with `TYPESAFE_API_KEY`, which this app does not set.
- Cache files are written through a temp file of their own, and the memory log takes a lock, because two analyses run at once here.
- yfinance 1.7.0 and current LangChain and LangGraph releases.

Tool output on the live path was identical on both versions for NVDA on 2026-10-03, apart from float rounding in the indicators.

**v0.6.0 changed these things in a live run, and the rebase kept them** (2026-10-10):

- **The memory log settles every ticker, not only the one analysed.** A "Memory Log" node runs beside the analysts and calls `settle_all_pending`: every decision whose five-day window has traded gets its return and a reflection from the quick model. Before, a run settled only its own ticker, so a ticker the agent did not analyse again stayed pending for good. On 2026-10-10 that was 30 of 36 pending entries on this machine and 16 of 24 on nebula. The first run after the deploy settles that backlog, about one reflection call each, inside that run. A failed settlement is a note in the report header, not a failed run.
- **Yahoo company news falls back to Yahoo search** when the quote feed is empty, which it was for NVDA, AMD, APH and MRVL on 2026-10-10. Search returns articles that Yahoo tags with the symbol. About half were on-topic for AMD, APH and MRVL, including analyst price-target changes; for NVDA most were not. When search has no tagged article either, the Yahoo block reads as unavailable, and `get_news` still adds Google News, Finnhub and SEC 8-K.
- **A `start_date` that is not a date goes back to the model** from `get_news` and `get_stock_data`, as `start_date '…' is not a date; give it as YYYY-MM-DD`.
- **The Yahoo fundamentals overview names the currency** of each money figure, so a US stock reads `Market Cap: … USD`.
- **The free-text rating parser ignores another party's rating**, such as "Consensus rating: Buy". Only the Portfolio Manager's fallback path uses it, when its structured output fails.
- **A Gemini call has a 600-second default timeout.** The backend sets `llm_timeout` for Gemini, so it does not change.

The agent prompts for a US stock are the same on both versions. Two merges were not mechanical. `create_tier_client` (upstream #1440) merges the request-label callbacks with the caller's callbacks; without that, both arrive as `callbacks` and the call fails. The crypto subreddits from upstream #1461 apply only when `REDDIT_MULTIREDDIT_URL` is `off`, because the default feed replaces the subreddit list.

**The backtester and past-date code are in the base now** (`tradingagents/backtest.py`, `portfolio.py`, the `date_window` withhold rules). No live run uses them, because every analysis here is dated today. `memory/settlement.py` is different: every live run settles past decisions through it.

## Pull requests to watch

**TauricResearch#1076** adds engine host and streaming APIs behind a FastAPI/SSE backend. It is open since July 2026 with no activity, so treat it as dormant. Its one useful finding is already replaced here. Its runs execute on a single-worker pool, and `docs/gpu-concurrency.md` measures what concurrency gives on this hardware.

## After a change inside the submodule

**Install:** `uv` installs `tradingagents` from `./TradingAgents` as a non-editable path dependency (see `[tool.uv.sources]` in the root `pyproject.toml`). Run `uv sync` to install new commits into this repo's venv.

**Gitlink:** after you commit inside `TradingAgents/`, the parent repo still points at the old commit. From the root, run `git add TradingAgents && git commit`. `git status` at the root shows `TradingAgents` as changed while the two differ.

**Tests:** run the submodule tests from `TradingAgents/` with this repo's venv, `../.venv/bin/python -m pytest tests/`, and then `backend/tests/`.
