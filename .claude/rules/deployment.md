---
paths:
  - "Dockerfile*"
  - "scripts/**"
  - ".github/workflows/**"
  - "backend/paths.py"
  - "backend/main.py"
  - "pyproject.toml"
  - "compose.example.yaml"
  - "dockge/**"
  - ".env.example"
  - "backend/services/snapshot_export.py"
  - "backend/services/publish.py"
  - "frontend/angular.json"
  - "frontend/src/app/core/static-data.interceptor.ts"
  - "frontend/src/app/shared/experiment.ts"
  - "docs/deploying.md"
---

## Deployment topology (important, non-obvious)

Two copies of the compose config exist and are **not synced automatically**:

- `compose.example.yaml` — the tracked template a self-hoster starts from. `dockge/` is gitignored and not authoritative.
- **Each deployment's own copy, managed by a different tool on each host (checked 2026-09-23).** On this machine, **Dockge** manages `/opt/stacks/trading-experiment/compose.yaml` and its `.env` beside it. On nebula, **Portainer** manages stack 58: the container's compose label reads `/data/compose/58/v<n>/docker-compose.yml`, a path inside the Portainer container, with `stack.env`. Until 2026-09-23 this file said this machine had moved to Portainer on 2026-09-17; that described nebula. Applying a repo edit to either means re-applying the diff by hand in that tool's editor. **Every secret lives in the `.env` and is read with `${VAR}` substitution**, so a wholesale replace of the `environment:` block is safe; check the `.env` first if a value still looks hardcoded. **Mask commented lines too when you print a `.env`**: this machine's holds two commented-out secrets, and a mask that skipped comments printed them on 2026-09-23.

**The public site is a fully static Cloudflare Pages deployment as of 2026-09-09, at `ten-acre.nandyalu.com` (a subdomain of the domain bought that same day).** It went through a live read-only mirror first, briefly: standing up a second container (`trading-experiment-public`, `PUBLIC_MODE=1`) behind a Cloudflare Tunnel surfaced two real gaps in "read-only" as a live-backend property — a read request's own database side effect wasn't caught by the write-guard middleware (see `positions.get_current_price`), and a Webull-sandbox flag read that container's own empty environment instead of the real agent's. Both were patched, but the pattern was the point: a live backend on the public side can keep developing this class of gap no matter how carefully it's gated. The fix was to remove the live backend from the public path entirely rather than keep patching it:

- `backend/services/snapshot_export.py`, run every 15 minutes by `backend/tasks/scheduler.py` on the *private* container (never on `PUBLIC_MODE`, so it can only ever run where live data actually is), renders every public page's data to static JSON under `data/public_snapshot/` on the shared `agent_data` volume.
- `frontend/angular.json`'s `public` build configuration (`ng build --configuration=production,public`) produces a second Angular bundle with no `/settings` route at all — not hidden, absent from the bundle — and `frontend/src/app/core/static-data.interceptor.ts` redirects every `/api/...` call to the matching snapshot file, so none of the existing services or components needed to change. The Decisions and Journal pages were rebuilt around this at the same time: both now load a month at a time on a click-to-expand timeline (newest month and day open by default, everything older fetched only when opened) instead of shipping a fixed recent window or the whole history at once.
- **`Dockerfile.pages-publisher` + `scripts/publish_pages.sh`** is the `pages-publisher` container in the compose file above. It is a separate image from the main one on purpose — the main `Dockerfile` deliberately keeps Node out of the runtime image, and wrangler is the only supported way to push files to Cloudflare Pages (there is no documented plain REST API for it). Needs `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, and optionally `PAGES_PROJECT_NAME` in `.env`. Its volume mount onto the shared data is read-only — this container has no reason to ever write to it, and confirmed refusing to (`touch` inside it fails with a read-only-filesystem error).

- **`R2_SNAPSHOT_BUCKET` decides which of two shapes the publisher runs in, and since 2026-09-11 this deployment uses the second.** Unset, every round bundles the Angular shell and the exporter's JSON together and deploys both to Pages. Set, the shell is deployed to Pages **once at startup** and the JSON goes to an R2 bucket on its own domain (`data.ten-acre.nandyalu.com`), uploading only the files whose contents changed.

  **The reason is deployment count, not speed.** Cloudflare refuses to delete a Pages project holding more than a hundred deployments, and a deploy every 15 minutes makes 96 a day — which is what made the old `the-allowance` project awkward to remove. The two halves of the site change at wildly different rates: the bundle about monthly, the JSON every 15 minutes.

  **The comparison is by content hash, never timestamp.** The exporter rewrites all 89 files (3.9 MB) every round and almost none of them differ, because a graded signal's JSON is frozen once written. Measured across four rounds: a cold start uploads everything, a round where the exporter rewrote every file uploads nothing.

  **The bucket name and the bundle are one setting in two places.** The bundle learns the address at build time from `Dockerfile.pages-publisher`'s `SNAPSHOT_BASE_URL` build argument (default `/data`, meaning beside the page), and the container reads `R2_SNAPSHOT_BUCKET` at run time. Build one without the other and the site renders empty rather than erroring. That happened from 2026-09-12 to 2026-09-14, when the image was rebuilt without the build argument. Since then `scripts/publish_pages.sh` reads `snapshotRoot` from the bundle and refuses to start when it disagrees with `R2_SNAPSHOT_BUCKET`. **Every rebuild of this image for this deployment needs `--build-arg SNAPSHOT_BASE_URL=https://data.ten-acre.nandyalu.com`.** The token also needs Account → R2 → Edit on top of Pages → Edit.

  **A Cloudflare Pages project cannot be renamed.** The `pages.dev` subdomain is fixed at creation; renaming means creating a new project and deleting the old one. That is why `PAGES_PROJECT_NAME` changing is a redeploy, not an edit in the dashboard.

**`trading-bot-public` and `cloudflared` are retired**, along with the Zero Trust tunnel itself (deleted outright, not just unused) — the public site is now files with no server, no database connection, and no credentials anywhere near it. `ten-acre.nandyalu.com`'s DNS points at the Cloudflare Pages project directly (Workers & Pages → the project → Custom domains), which also means the domain's *only* remaining live surface is the private app container and the pages publisher — nothing else. **On 2026-09-17 `docker ps` showed no publisher container at all**, only the app; if the public site's JSON stops updating, that is the first thing to check.

**Two deployments since 2026-09-10, each a container named `ten-acre` on an image `ten-acre:local` built on its own host.**

| | This machine | Nebula |
|---|---|---|
| Manager | Dockge | Portainer, stack 58 |
| Data on the host | `/opt/stacks/trading-experiment/data` | `/var/appdata/ten-acre/data` |
| Dashboard port | 8125 | 8126 |
| Webull account class | `INDIVIDUAL_CASH` | `INDIVIDUAL_MARGIN` |
| Start date | 2026-09-03 (`EXPERIMENT_START_DATE`; stamp 2026-09-23) | 2026-09-10 (stamp) |

Both mount their data directory at `/app/data`. **Both use the one Webull app key**, because Webull issues one per account and allows one account, so only one of them holds the trade stream; the other settles fills on the 15-minute poll. This machine's container was `trading-experiment` until 2026-09-17, and the old `trading-bot` and `analyst-bot` containers stopped on 2026-09-01.

**The database stamps the experiment's start date** the first time the agent is switched on (`backend/services/experiment.py`). See the table above for each. **This machine's stamp says 2026-09-23 although its book began on 2026-09-03.** Its database predated the stamp, and a settings save that day called `set_enabled(True)`, which stamped today. `EXPERIMENT_START_DATE=2026-09-03` corrects it, because since the same day the variable wins over the stamp, and `record_start` stamps the variable's date when one is set. `frontend/src/app/shared/experiment.ts` holds 2026-09-02 as the fallback for a site with no API behind it; everything on the site that says "since" or "day N" reads the stamped value.

Neither dashboard is on the 8080 the template defaults to. `docker ps` is the authority.

To inspect the live container: `docker logs ten-acre`, `docker exec ten-acre env`. Do not edit the deployed compose file on disk — hand the user the exact diff or snippet to paste into Dockge or Portainer instead (their stated preference). Nebula is reached with `ssh nebula`.

## Three ways to run it, since 2026-09-17

**Docker is the recommended path, and nothing about it changed for the user.** Two more paths exist for a machine without Docker, documented in `docs/deploying.md` under "Without Docker": a direct install from a release zip with `uv tool install`, and build-and-run from a checkout. What holds the three together:

- **`backend/paths.data_dir()` is the one place that decides where the app writes.** `TEN_ACRE_DATA_DIR` wins. Else `<repo>/data` when a `pyproject.toml` sits beside `backend/`, which means a checkout. Else `~/.local/share/ten-acre`, which means an installed copy. The Dockerfile sets the variable to `/app/data`, because `/app` holds no `pyproject.toml`. **Never compute a data path from `__file__` again.** Inside an installed package that lands in `site-packages`, and `uv tool install --reinstall` deletes it with the old code. The four paths that did this until 2026-09-17 were the database, the logs, the journey files and the public snapshot. An installed copy reads its `.env` from the data directory, so that `.env` cannot set `TEN_ACRE_DATA_DIR`; the systemd unit does.
- **The built dashboard and the built docs live inside the package**, at `backend/web` and `backend/site`. `angular.json`'s `outputPath` and `zensical.toml`'s `site_dir` write there directly. The Dockerfile copies from those places. Hatch packages them into the wheel as `artifacts`, because git ignores them. The `public` Angular configuration names its own `outputPath`, `dist/frontend-public`, so the publisher's build never lands in `backend/web`.
- **`backend.main:main()` is the one start sequence**: load `.env` (repo root, then the data directory), create the data directory, configure logging, run the alembic upgrade in a subprocess, start uvicorn. The Docker entrypoint is `python -m backend.main` and the installed command is `ten-acre`; both run this function. The migration is a subprocess so alembic's `fileConfig` never replaces this process's log handlers.
- **`uv sync` in the Dockerfile passes `--no-install-project`.** `pyproject.toml` is a package now, built with hatchling. Without that flag the venv would hold an editable install pointing at the `/build` stage path, which the final image does not have.
- **A release is `.github/workflows/release.yml`, on a `v*` tag.** The tag must equal `version` in `pyproject.toml`, so bump the version by hand before tagging. One job builds both static outputs, runs `uv build --wheel` for the app and for `TradingAgents/` (the fork is not on PyPI), exports `uv.lock` as a constraints file so the install resolves the same versions as the image, installs the result and starts it once as a smoke test, and attaches one zip to the GitHub release. The zip holds the two wheels, `constraints.txt` and `.env.example`. **A second job (since 2026-09-22) builds the Docker image and pushes it to `ghcr.io`**, tagged with the tag name and `latest` — the image itself is no longer something a deployment has to build from source.
