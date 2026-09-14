---
paths:
  - "TradingAgents/**"
  - ".gitmodules"
  - "pyproject.toml"
---

<!-- Moved from CLAUDE.md on 2026-09-13. This file loads when Claude reads a file that matches paths. -->

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

## Upstream releases after the base

**Upstream stopped tagging after v0.4.0.** v0.4.1 and v0.4.2 exist only as merged pull requests named after the version. The GitHub Releases page and `git tag` do not show them. To see what upstream has that we do not, run:

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
| the other 9 | Point-in-time fundamentals, an unsettled last bar, Alpha Vantage date trim, Kimi models, tests, cleanup. | Not reviewed yet. |

Read `git log --stat` for each new commit before you take it. Do not merge `origin/main` as a whole: it would bring back `1c44dd1`.

## Pull requests to watch

**TauricResearch#1076** adds engine host and streaming APIs behind a FastAPI/SSE backend. It is open since July 2026 with no activity, so treat it as dormant. Its one useful finding is already replaced here. Its runs execute on a single-worker pool, and `docs/gpu-concurrency.md` measures what concurrency gives on this hardware.

## After a change inside the submodule

**Install:** `uv` installs `tradingagents` from `./TradingAgents` as a non-editable path dependency (see `[tool.uv.sources]` in the root `pyproject.toml`). Run `uv sync` to install new commits into this repo's venv.

**Gitlink:** after you commit inside `TradingAgents/`, the parent repo still points at the old commit. From the root, run `git add TradingAgents && git commit`. `git status` at the root shows `TradingAgents` as changed while the two differ.

**Tests:** run the submodule tests from `TradingAgents/` with this repo's venv, `../.venv/bin/python -m pytest tests/`, and then `backend/tests/`.
