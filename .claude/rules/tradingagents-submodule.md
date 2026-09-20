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

**The checked-out branch is `trading-helper-custom`, based on upstream v0.4.1** (merge `9dee508`, 2026-09-01). `fork/main` is an ancestor and stays at v0.3.1 plus six commits, so compare against `origin/main`, not `fork/main`.

On top of v0.4.1, the branch carries three groups of commits.

**Cherry-picks from upstream pull requests that were open when we took them:**

| PR | What it adds |
|---|---|
| #1071 | Simpler vendor routing, and a `CircuitBreaker` for vendor failures |
| #1149 | A guide for custom Ollama Modelfiles (fast and accurate profiles) |
| #1074 | A retry when a JSON response body does not decode |
| #1082 | A probability and risk/reward review on every trader proposal |
| #1134 | Reddit OAuth2, with the RSS feed as the fallback |
| #1122 | A candidate screener script and trade-horizon-aware prompts |
| #1324 | The FRED API key removed from HTTP error text (taken 2026-09-14 as `2a57bfa`, `ecfac18`) |
| #1328 | A StockTwits read that stops at 5 MiB (taken 2026-09-14 as `8e550e1`) |

Commit `4c6c356` repairs how those picks fit together. #1189 (an unparseable rating becomes REVIEW) now arrives from upstream as `43fc275`. #1200 is no longer a separate commit on the branch.

**The #1134 OAuth path is dead for this project.** Reddit's Responsible Builder Policy ended self-serve API app creation, so nobody can get the credentials for a personal tool. The RSS feed is the supported path. See [docs/setup.md](../../docs/setup.md).

**Our own commits.** These change how an analysis runs. Read the commit before you drop or reorder one in a rebase:

- `64dcfb1`, `afdb60a`, `b7d93b1`: the Trader states ATR multiples, and `resolve_levels` computes the prices. See `analysis-output.md`.
- `bfac9bc`: every macro query is fetched, not only the first.
- `8535395`, `d53fa23`, `4f6a694`: an analyst that answers without fetching data, and a tool call printed as text.
- `941a5f4`: a sentiment score on the wrong scale is rescaled.
- `e403ad7`: a tool error goes to the model. `backend/tests/test_tool_errors_reach_the_model.py` guards it.
- `9d8f437`: repairs after the rebase onto v0.4.1.
- `f195daa`: the four analysts of one analysis run at the same time.
- `67fc9f5`: upstream `7cc478a` cherry-picked from v0.4.2, with the OAuth path kept.
- `5260a28`: Reddit through a trawl browser when `REDDIT_TRAWL_URL` is set. See `market-data.md`.
- `ce80173`: the FRED API key removed from connection errors and timeouts too. #1324 missed that path, and a `requests.ConnectionError` quotes the full URL with `api_key=`.
- `7a6d4c2` (merge of fork PR #1) + `78dbb52`: the news analyst can attach Gemini's built-in search-grounding tool, behind `TRADINGAGENTS_GOOGLE_SEARCH_GROUNDING` (default off). The PR as submitted needed a fix: Gemini rejects `google_search` mixed with the analyst's custom tools unless `tool_config.include_server_side_tool_invocations` is set, confirmed against the live API. **Do not flip the default on** — the Free tier gives Gemini 3 zero grounding quota (`429 RESOURCE_EXHAUSTED` on every Gemini 3 model tested, including aliases like `gemini-flash-latest`), so turning it on unconditionally breaks the news analyst on any deployment without a Google Cloud billing account (Tier 1) linked. Gemma models (e.g. `gemma-4-31b-it`) had working grounding on the same Free-tier key — the block is Gemini-3-specific, not account-wide.
- `7e6ec65`: `TRADINGAGENTS_GOOGLE_SEARCH_GROUNDING_MODEL` lets the news analyst run on a different Google model than `quick_think_llm`/`deep_think_llm` when grounding is on — e.g. keep `gemini-3.1-flash-lite` for everything else and set this to `gemma-4-31b-it`, so grounding works on a Free-tier key without touching the main model. `TradingAgentsGraph.news_analyst_llm` resolves this and is what `GraphSetup` hands to `create_news_analyst`; every other analyst still gets `quick_thinking_llm`.

## Editing the fork: the venv does not follow the source

**`uv` does not rebuild `tradingagents` when only its source files change.** `[tool.uv.sources]` installs it from `TradingAgents/` as a built, non-editable copy, and neither `uv run` nor `uv sync` notices an edit that leaves the version alone. The installed copy then lags the source, silently.

**The tests do not catch this, because they do not use the installed copy.** pytest puts `TradingAgents/` on `sys.path`, so `import tradingagents` in a test reads the source tree while the app reads `.venv`. On 2026-09-20 that combination produced two wrong conclusions in one afternoon: a green test run beside a live run that crashed on the bug the tests said was fixed, and a "the fix did not work" verdict on a run that was executing the old code.

**Before any run that is not pytest — a probe, a script, a container-less start — rebuild it:**

```
uv sync --extra dev --reinstall-package tradingagents
diff -q .venv/lib/python3.14/site-packages/tradingagents/dataflows/reddit.py TradingAgents/tradingagents/dataflows/reddit.py
```

The second line is the check that matters: compare the file you edited. `--extra dev` is not optional — `uv sync` without it prunes pytest. The Docker image is never affected, because it copies the source tree and builds it.

## Upstream releases after the base

**Upstream tagged v0.4.0, then nothing until v0.5.0 on 2026-09-18.** v0.4.1 and v0.4.2 exist only as merged pull requests named after the version, so the GitHub Releases page and `git tag` do not show those two. To see what upstream has that we do not, run:

```
cd TradingAgents && git fetch origin
git log --oneline --merges HEAD..origin/main   # new versions
git cherry -v HEAD origin/main                 # "+" = not on our branch
```

**v0.4.2** (PR #1310, merged 2026-09-07) holds 11 commits. Decisions so far:

| Commit | Change | Decision |
|---|---|---|
| `7cc478a` | A failed Reddit fetch shows as unavailable, not as "no posts found". The back-off without a `Retry-After` header goes from 5 s to 60 s, once per run. | **Taken** 2026-09-13. The merge kept the OAuth path. |
| `1c44dd1` | Tells the Trader to state entry and stop as absolute prices. | **Declined.** Our `TraderProposal` has no price fields. The model states distances, and Python computes the prices, because model-written prices were unreliable (see `analysis-output.md`). Never apply this commit. |
| `ef383df` | A newest bar with no close no longer turns the whole price history into "no data". | **Taken** 2026-09-14 as `ca87aad`. Our analyses use today's date, so an unsettled bar can occur. No trace had the error text on 2026-09-14. |
| `96111aa` | A run with a past `curr_date` no longer gets today's company profile. | **Take with the backtester.** Live runs are unchanged. |
| `16f7fd6`, `ffd5d9a`, `d6ca23a`, `260c899`, `94113c8`, `d58b838`, `821848b` | Alpha Vantage date trim, logging cleanup, Kimi models, tests, comments. | **Take for an easier sync.** None of them changes a live run. We use yfinance, not Alpha Vantage. |

Read `git log --stat` for each new commit before you take it. Do not merge `origin/main` as a whole: it would bring back `1c44dd1`.

## v0.5.0

**v0.5.0** (PR #1364, merged 2026-09-18, and the first tag since v0.4.0) holds 60 commits. **29 of them are on this branch**, cherry-picked on 2026-09-20 in upstream order. `git log --oneline pre-v050-sync..HEAD` lists them, and the tag `pre-v050-sync` marks the commit before the sync.

Most of the picks needed no decision. These did:

| What we took | Note |
|---|---|
| `62d3479` conflict alone is not a reason to Hold | A prompt change at four sites: both managers' prompts and both rating fields. See the 2026-09-20 entry in `JOURNEY.md`. |
| `486dec1` the decision prompts state their output shape | Taken with the trader's section rewritten. Upstream asks the trader for **Entry Price** and **Stop Loss**; this fork asks for the ATR multiples, because Python computes every level. |
| `241638d` one combined Reddit request | Reconciled with our trawl commit `5260a28`, and the first reconciliation was wrong: Reddit's HTML search page has no `r/a+b+c` form, so the trawl path returned zero posts and reported them as a real absence. The feed and the OAuth endpoint take the combined request; trawl asks for each subreddit on its own, at the same time, and a page it cannot read goes to the feed by itself. A trawl post gets its subreddit from its own permalink, since only the feed labels each entry. See `market-data.md`. |
| `b20c8e6` vendor keys out of request errors | Upstream's shared `get_scrubbed` helper replaced our local `ce80173`. It detaches the response and the exception chain, because both hold the URL. `fred.redact` stays for FRED's own 400 body, which no request helper sees. |
| `f8042ef` an unreadable price does not discard the decision | Only the coercion half. A range ("2-3") in an ATR multiple now nulls one field instead of failing the whole proposal. The renderer half names price fields this fork does not have. |
| `d5ba41b` a vendor failure is reported as one | Merged into our circuit-breaker routing from #1071. A chain where every vendor is throttled now answers `DATA_UNAVAILABLE` instead of ending the run. |
| `aef4af9` the next vendor serves what Alpha Vantage cannot | An unsupported indicator is a `NoMarketDataError`, not a `BadVendorArgumentError`, so the router falls through to yfinance. |
| `f881c4a` US statements as filed, from SEC EDGAR | Opt in by naming `sec_edgar` in the `fundamental_data` chain. It feeds the fundamentals **analyst**, and is separate from `backend/services/fundamentals.py`, which the agent reads. |

**`8ac4371` (Ollama takes the local-compatible client) arrived as a test only.** This fork already had the client fix, and that fork test now asserts our stronger contract: structured output on Ollama answers with `json_schema`, which constrains the server's sampler, so no tool and no `tool_choice` are sent.

**`8d64416` was skipped.** Our `bfac9bc` already trims global news before the limit, with one bucket per macro query.

### What v0.5.0 holds for the backtester and replay, and is not on this branch

**Take this set together when the backtester or replay is built**, not before. Every commit here is about a run dated in the past, which no live run performs. Taking one on its own pulls in test files for the other two.

| Commit | What it does |
|---|---|
| `8721b92`, `d8eceb6`, `2ca59cc`, `76a93d6` | `tradingagents/backtest.py`: run the graph over a grid of tickers and dates, and score each decision against the direction it claimed. Reads the decision log, not a portfolio; upstream states it must never grow an execution model. `iter_grid` stops the grid at today. |
| `6436d1f` | `propagate(..., portfolio=...)` and `tradingagents/portfolio.py`: the caller's holdings and cash reach the trader and the portfolio manager. Three states stay distinct: a position, a flat book, and no context. Our `propagate` already carries `horizon`, so this is a merge point. |
| `85d9137` | `holding_period_days` sets the window an outcome is measured over, and the reflection states the window it judges. |
| `d04693a`, `fadc698`, `c3bb991`, `f0a1cf6`, `96111aa` (v0.4.2) | Tool dates bounded by the run's trade date; insider rows dated by the trade; prediction markets bounded; the company profile withheld when it has no historical vintage. `date_window.withhold_live_profile` belongs here. This is the precondition for an honest past-dated run. |
| `375af05`, `9683194`, `6398951`, `4a9f196`, `34899bd`, `008ac65`, `2c1ba38` | The CLI's run surface, remembered selections, and the decision log on the CLI path. This app never runs the CLI. |

**Two upstream tests were removed from the picks for the same reason**, with a comment at the end of `tests/test_rating_integrity.py`: one grades a backtest run, one drives the CLI.

**`tests/test_structured_agents.py::TestSentimentAnalystAgent::test_structured_path_produces_rendered_markdown` hangs**, on this branch and on `pre-v050-sync` alike, so it is not from the sync. Run the suite with `-k "not SentimentAnalystAgent"` until someone fixes it.

## Pull requests to watch

**TauricResearch#1076** adds engine host and streaming APIs behind a FastAPI/SSE backend. It is open since July 2026 with no activity, so treat it as dormant. Its one useful finding is already replaced here. Its runs execute on a single-worker pool, and `docs/gpu-concurrency.md` measures what concurrency gives on this hardware.

## After a change inside the submodule

**Install:** `uv` installs `tradingagents` from `./TradingAgents` as a non-editable path dependency (see `[tool.uv.sources]` in the root `pyproject.toml`). Run `uv sync` to install new commits into this repo's venv.

**Gitlink:** after you commit inside `TradingAgents/`, the parent repo still points at the old commit. From the root, run `git add TradingAgents && git commit`. `git status` at the root shows `TradingAgents` as changed while the two differ.

**Tests:** run the submodule tests from `TradingAgents/` with this repo's venv, `../.venv/bin/python -m pytest tests/`, and then `backend/tests/`.
