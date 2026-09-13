---
paths:
  - "TradingAgents/**"
  - ".gitmodules"
  - "pyproject.toml"
---

<!-- Moved from CLAUDE.md on 2026-09-13. This file loads when Claude reads a file that matches paths. -->

## Vendored TradingAgents repo

It's a real nested git repo, registered as a **git submodule** of this repo (`.gitmodules`, pinned to a commit via a `160000` gitlink) — this repo tracks which TradingAgents commit is checked out without flattening its history. `git clone` needs `--recurse-submodules` (or `git submodule update --init` afterward) to populate it.

**Remote-naming gotcha**: a fresh submodule checkout only gets one remote, named `origin`, pointing at whatever URL is in `.gitmodules` — that's the `fork` (nandyalu/TradingAgentsUI), not upstream. The two-remote setup described below (`origin` = TauricResearch upstream, `fork` = our fork) is this particular working copy's local addition, done once when the branch was first built; it does not survive a fresh clone. To restore it after a fresh clone: `cd TradingAgents && git remote rename origin fork && git remote add origin https://github.com/TauricResearch/TradingAgents.git && git fetch origin`.

Remotes (in a working copy that's had the above applied):

- `origin` = `https://github.com/TauricResearch/TradingAgents.git` — kept only as a reference point for diffing; do not push here.
- `fork` = `https://github.com/nandyalu/TradingAgentsUI.git` — our fork. Checked-out branch is `trading-helper-custom`, based on `fork/main` (itself a few commits ahead of the old v0.3.1 pin we used to track) plus 9 commits cherry-picked from open upstream PRs that weren't merged yet but were judged worth having:

  - #1189 unparseable ratings surface as REVIEW instead of a silent Hold
  - #1200 avoid inventing arguments in opening debate turns
  - #1071 simplified vendor routing + CircuitBreaker for LLM vendor resilience
  - #1149 custom Ollama Modelfile guide (fast/accurate profiles)
  - #1074 retry an undecodable JSON response body instead of aborting the run
  - #1082 probability + risk/reward review on every trader proposal
  - #1134 Reddit OAuth2 (100 QPM) with automatic fallback to the RSS scraper when `REDDIT_CLIENT_ID`/`REDDIT_CLIENT_SECRET` are unset. **The OAuth path is effectively dead for this project**: Reddit's Responsible Builder Policy ended self-serve API app creation, so those credentials can't be obtained for a personal tool. RSS is the supported path — see [docs/setup.md](../../docs/setup.md). Don't treat 429 warnings as a bug.
  - #1122 candidate screener script + trade-horizon-aware analysis prompts
  - plus one local fix-up commit resolving integration issues between the above (an `UnboundLocalError` in `sentiment_analyst.py` that was latent in #1134's own diff, plus two test fixtures)

  Full upstream test suite (606 passed, 2 pre-existing skips) and this repo's own `backend/tests/` (80 passed) were both green against this branch before it was pushed.

**One upstream PR is still worth watching: TauricResearch#1076** — engine host and streaming APIs behind a FastAPI/SSE backend. Open since July 2026 and untouched since, so treat it as dormant rather than pending. Its one useful finding is already superseded here: its runs execute on a single-worker pool, and `docs/gpu-concurrency.md` measures what concurrency is actually worth on this hardware.

To check whether `fork/main` has moved (new commits merged upstream into the fork) before assuming a bug needs a local fix: `cd TradingAgents && git fetch fork && git log HEAD..fork/main --oneline`. Check `git log --stat` on any new commits before merging — don't blind-merge, and re-apply the same care used for the original cherry-picks if `fork/main` and `trading-helper-custom` diverge further. Dependencies (including `tradingagents` itself, installed non-editable from `./TradingAgents` — see root `pyproject.toml`'s `[tool.uv.sources]`) are managed with `uv`: to pick up new commits made here in this repo's own venv, just `uv sync` — it re-resolves the local path dependency automatically, no manual reinstall needed.

After committing inside `TradingAgents/` (new cherry-picks, a rebase onto a moved `fork/main`, etc.), the parent repo still points at the old commit until you also commit the updated gitlink here: `git add TradingAgents && git commit -m "..."` from this repo's root. `git status` at the root shows `TradingAgents` as dirty/ahead whenever the two are out of sync.
