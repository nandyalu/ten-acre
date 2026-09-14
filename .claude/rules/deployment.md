---
paths:
  - "Dockerfile*"
  - "scripts/**"
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

<!-- Moved from CLAUDE.md on 2026-09-13. This file loads when Claude reads a file that matches paths. -->

## Deployment topology (important, non-obvious)

Two copies of the compose config exist and are **not synced automatically**:

- `dockge/trading-experiment.compose.yaml` — local-only working template (`dockge/` is gitignored, not tracked in this repo). Edit this one. `analyst-bot.compose.yaml` is deleted; that deployment ended 2026-09-01.
- `/opt/stacks/trading-experiment/compose.yaml` + `.env` — the actually-deployed copy, managed via the Dockge UI. **Root-owned**, outside this repo. Applying a repo edit to the deployed stack means manually re-applying the diff in the Dockge UI's compose editor, since the two files are not synced automatically. **As of 2026-09-09, every secret for every container in this stack (and others on this host) lives in `.env` and is read with `${VAR}` substitution** — the earlier drift, where Discord and Webull secrets were pasted directly into the deployed `environment:` block, is gone. A wholesale replace of the `environment:` block is safe now; check `.env` first if a value still looks hardcoded.

**The public site is a fully static Cloudflare Pages deployment as of 2026-09-09, at `ten-acre.nandyalu.com` (a subdomain of the domain bought that same day).** It went through a live read-only mirror first, briefly: standing up a second container (`trading-experiment-public`, `PUBLIC_MODE=1`) behind a Cloudflare Tunnel surfaced two real gaps in "read-only" as a live-backend property — a read request's own database side effect wasn't caught by the write-guard middleware (see `positions.get_current_price`), and a Webull-sandbox flag read that container's own empty environment instead of the real agent's. Both were patched, but the pattern was the point: a live backend on the public side can keep developing this class of gap no matter how carefully it's gated. The fix was to remove the live backend from the public path entirely rather than keep patching it:

- `backend/services/snapshot_export.py`, run every 15 minutes by `backend/tasks/scheduler.py` on the *private* container (never on `PUBLIC_MODE`, so it can only ever run where live data actually is), renders every public page's data to static JSON under `data/public_snapshot/` on the shared `agent_data` volume.
- `frontend/angular.json`'s `public` build configuration (`ng build --configuration=production,public`) produces a second Angular bundle with no `/settings` route at all — not hidden, absent from the bundle — and `frontend/src/app/core/static-data.interceptor.ts` redirects every `/api/...` call to the matching snapshot file, so none of the existing services or components needed to change. The Decisions and Journal pages were rebuilt around this at the same time: both now load a month at a time on a click-to-expand timeline (newest month and day open by default, everything older fetched only when opened) instead of shipping a fixed recent window or the whole history at once.
- **`Dockerfile.pages-publisher` + `scripts/publish_pages.sh`** is the `pages-publisher` container in the compose file above. It is a separate image from the main one on purpose — the main `Dockerfile` deliberately keeps Node out of the runtime image, and wrangler is the only supported way to push files to Cloudflare Pages (there is no documented plain REST API for it). Needs `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, and optionally `PAGES_PROJECT_NAME` in `.env`. Its volume mount onto the shared data is read-only — this container has no reason to ever write to it, and confirmed refusing to (`touch` inside it fails with a read-only-filesystem error).

- **`R2_SNAPSHOT_BUCKET` decides which of two shapes the publisher runs in, and since 2026-09-11 this deployment uses the second.** Unset, every round bundles the Angular shell and the exporter's JSON together and deploys both to Pages. Set, the shell is deployed to Pages **once at startup** and the JSON goes to an R2 bucket on its own domain (`data.ten-acre.nandyalu.com`), uploading only the files whose contents changed.

  **The reason is deployment count, not speed.** Cloudflare refuses to delete a Pages project holding more than a hundred deployments, and a deploy every 15 minutes makes 96 a day — which is what made the old `the-allowance` project awkward to remove. The two halves of the site change at wildly different rates: the bundle about monthly, the JSON every 15 minutes.

  **The comparison is by content hash, never timestamp.** The exporter rewrites all 89 files (3.9 MB) every round and almost none of them differ, because a graded signal's JSON is frozen once written. Measured across four rounds: a cold start uploads everything, a round where the exporter rewrote every file uploads nothing.

  **The bucket name and the bundle are one setting in two places.** The bundle learns the address at build time from `Dockerfile.pages-publisher`'s `SNAPSHOT_BASE_URL` build argument (default `/data`, meaning beside the page), and the container reads `R2_SNAPSHOT_BUCKET` at run time. Build one without the other and the site renders empty rather than erroring. That happened from 2026-09-12 to 2026-09-14, when the image was rebuilt without the build argument. Since then `scripts/publish_pages.sh` reads `snapshotRoot` from the bundle and refuses to start when it disagrees with `R2_SNAPSHOT_BUCKET`. **Every rebuild of this image for this deployment needs `--build-arg SNAPSHOT_BASE_URL=https://data.ten-acre.nandyalu.com`.** The token also needs Account → R2 → Edit on top of Pages → Edit.

  **A Cloudflare Pages project cannot be renamed.** The `pages.dev` subdomain is fixed at creation; renaming means creating a new project and deleting the old one. That is why `PAGES_PROJECT_NAME` changing is a redeploy, not an edit in the dashboard.

**`trading-bot-public` and `cloudflared` are retired**, along with the Zero Trust tunnel itself (deleted outright, not just unused) — the public site is now files with no server, no database connection, and no credentials anywhere near it. `ten-acre.nandyalu.com`'s DNS points at the Cloudflare Pages project directly (Workers & Pages → the project → Custom domains), which also means the domain's *only* remaining live surface is `trading-experiment` (private) and `trading-experiment-pages-publisher` — both containers listed in `docker ps`, nothing else.

**One deployment, `trading-experiment`, live since 2026-09-02.** The old `trading-bot` and `analyst-bot` containers stopped on 2026-09-01; their volumes are kept as a record of the two experiments that ended. The new container runs on its own `agent_data` volume, from an empty database and a freshly reset Webull paper account.

**The experiment's start date is 2026-09-02, not the 1st.** The code was written on the 1st and nothing was running; the agent could first act on the 2nd. `frontend/src/app/shared/experiment.ts` holds that date as one constant, and everything on the site that says "since" or "day N" reads it from there.

The dashboard runs on **8125**, not the 8080 the template defaults to — the deployed copy sets its own port, the same drift the `environment:` block has. `docker ps` is the authority. The container is named `trading-experiment`.

To inspect the live container: `docker logs trading-experiment`, `docker exec trading-experiment env`. Don't sudo-edit `/opt/stacks/...` directly — hand the user the exact diff/snippet to paste into the Dockge UI instead (their stated preference).
