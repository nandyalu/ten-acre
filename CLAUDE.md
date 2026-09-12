# Ten Acre — Claude Code context

**One autonomous agent trading one simulated book with $10,000.** FastAPI +
Angular dashboard and a notification-only Discord bot (`backend/`), delegating
analysis to a vendored multi-agent framework (`TradingAgents/`, a git submodule
— see "Vendored TradingAgents repo" below). See [README.md](README.md) and
[docs/overview.md](docs/overview.md) for architecture — this file only covers
what those don't.

## What the experiment is for

**Give the agent proper tools inside reasonable restrictions, and let it trade.**

That sentence decides most arguments about this codebase, so read it before proposing a change to what the agent may do.

- **A tool is something the agent needs to decide well.** Research it chooses. Exits it can move. A way to say what it is missing. Timing it controls. When the agent lacks one, the honest reading is that the experiment has not been set up properly yet — not that the agent should work around it.
- **A restriction exists to keep the experiment honest or the account solvent**, and for no other reason. It never spends more cash than it has. It never sells shares it does not hold. No shorting, no options, no real money. Python refuses what cannot be executed as stated, and never resizes, because resizing turns the agent's decision into a different one.
- **The two are not in tension, and the tie goes to the tool.** A restriction that exists only because nobody built the tool yet is a gap, not a rule.

**The question being asked is whether an AI agent can trade profitably when it is given real tools.** Not whether it can trade well while blindfolded. Withholding a capability does not make the result cleaner — it makes it an answer to a question nobody asked.

This is also why the note action exists. The agent saying "I cannot see X" is the experiment reporting a missing tool, and it is evidence, not noise.

**Tokens spent deciding well are not waste; tokens spent guessing are.** These pull in opposite directions and both are worth acting on. A prompt that leaves the agent working out what year it is, which of two same-day analyses is current, or how long an analysis takes is burning tokens on ambiguity that a clearer prompt removes for free — every one of those was real, and each was found by reading a stored thinking block. But an agent that wants to read six analyses before it commits money is doing the thing a careful person would do, and rationing that to save a few thousand tokens buys nothing. **Cut the confusion, fund the deliberation.** When a limit exists only because an unbounded version might loop, bound it — do not ration it.

**The one thing that is not a tool is a human hand.** No control lets a person nudge the book, because that puts a second decision-maker in the record and afterwards nothing can tell which one produced a result. The agent's autonomy and the operator's absence are the same rule seen from two sides.

**Since 2026-09-01 there are no manual controls anywhere.** No Discord slash
commands, no button that adds a ticker, starts an analysis, or places a trade.
The reason is the whole experiment: a control that lets a person nudge the book
puts a second decision-maker in the record, and afterwards nothing can tell
which one produced a result. **Do not add one back.** If something needs
correcting, the route is an entry in `JOURNEY.md` saying what and why, then a
change made by hand.

The one write endpoint that survives is `POST /api/agent/exits/{ticker}`, and
it decides nothing — it rests the stop and target the agent already chose, under
shares it already owns, for the case where the broker refused the bracket at
purchase.

Everything else is gone with it: the real-portfolio book and its Webull sync,
the hand-followed paper book, the model-comparison sweep, `AGENT_ONLY`, and the
position-sizing settings. The tag `v1-two-book-experiment` marks the commit
before the removal.

## Markdown conventions

Don't hard-wrap prose — write each sentence (or paragraph) on one line, no
matter how long. Zensical (the `docs/` site generator) sometimes mis-renders
a sentence that's been split across multiple source lines. Applies to every
`.md` file in the repo, not just `docs/`.

## Deployment topology (important, non-obvious)

Two copies of the compose config exist and are **not synced automatically**:

- `dockge/trading-experiment.compose.yaml` — local-only working template
  (`dockge/` is gitignored, not tracked in this repo). Edit this one.
  `analyst-bot.compose.yaml` is deleted; that deployment ended 2026-09-01.
- `/opt/stacks/trading-experiment/compose.yaml` + `.env` — the actually-deployed
  copy, managed via the Dockge UI. **Root-owned**, outside this repo. Applying
  a repo edit to the deployed stack means manually re-applying the diff in the
  Dockge UI's compose editor, since the two files are not synced automatically.
  **As of 2026-09-09, every secret for every container in this stack (and
  others on this host) lives in `.env` and is read with `${VAR}` substitution**
  — the earlier drift, where Discord and Webull secrets were pasted directly
  into the deployed `environment:` block, is gone. A wholesale replace of the
  `environment:` block is safe now; check `.env` first if a value still looks
  hardcoded.

**The public site is a fully static Cloudflare Pages deployment as of
2026-09-09, at `ten-acre.nandyalu.com` (a subdomain of the domain bought
that same day).** It went through a live read-only mirror first, briefly:
standing up a second container (`trading-experiment-public`, `PUBLIC_MODE=1`)
behind a Cloudflare Tunnel surfaced two real gaps in "read-only" as a
live-backend property — a read request's own database side effect wasn't
caught by the write-guard middleware (see `positions.get_current_price`), and
a Webull-sandbox flag read that container's own empty environment instead of
the real agent's. Both were patched, but the pattern was the point: a live
backend on the public side can keep developing this class of gap no matter
how carefully it's gated. The fix was to remove the live backend from the
public path entirely rather than keep patching it:

- `backend/services/snapshot_export.py`, run every 15 minutes by
  `backend/tasks/scheduler.py` on the *private* container (never on
  `PUBLIC_MODE`, so it can only ever run where live data actually is),
  renders every public page's data to static JSON under
  `data/public_snapshot/` on the shared `agent_data` volume.
- `frontend/angular.json`'s `public` build configuration
  (`ng build --configuration=production,public`) produces a second Angular
  bundle with no `/settings` route at all — not hidden, absent from the
  bundle — and `frontend/src/app/core/static-data.interceptor.ts` redirects
  every `/api/...` call to the matching snapshot file, so none of the
  existing services or components needed to change. The Decisions and
  Journal pages were rebuilt around this at the same time: both now load a
  month at a time on a click-to-expand timeline (newest month and day open by
  default, everything older fetched only when opened) instead of shipping a
  fixed recent window or the whole history at once.
- **`Dockerfile.pages-publisher` + `scripts/publish_pages.sh`** is the
  `pages-publisher` container in the compose file above. It is a separate
  image from the main one on purpose — the main `Dockerfile` deliberately
  keeps Node out of the runtime image, and wrangler is the only supported way
  to push files to Cloudflare Pages (there is no documented plain REST API for
  it). Needs `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, and optionally
  `PAGES_PROJECT_NAME` in `.env`. Its
  volume mount onto the shared data is read-only — this container has no
  reason to ever write to it, and confirmed refusing to (`touch` inside it
  fails with a read-only-filesystem error).

- **`R2_SNAPSHOT_BUCKET` decides which of two shapes the publisher runs in,
  and since 2026-09-11 this deployment uses the second.** Unset, every round
  bundles the Angular shell and the exporter's JSON together and deploys both
  to Pages. Set, the shell is deployed to Pages **once at startup** and the
  JSON goes to an R2 bucket on its own domain
  (`data.ten-acre.nandyalu.com`), uploading only the files whose contents
  changed.

  **The reason is deployment count, not speed.** Cloudflare refuses to delete
  a Pages project holding more than a hundred deployments, and a deploy every
  15 minutes makes 96 a day — which is what made the old `the-allowance`
  project awkward to remove. The two halves of the site change at wildly
  different rates: the bundle about monthly, the JSON every 15 minutes.

  **The comparison is by content hash, never timestamp.** The exporter
  rewrites all 89 files (3.9 MB) every round and almost none of them differ,
  because a graded signal's JSON is frozen once written. Measured across four
  rounds: a cold start uploads everything, a round where the exporter rewrote
  every file uploads nothing.

  **The bucket name and the bundle are one setting in two places.** The
  bundle learns the address at build time from `Dockerfile.pages-publisher`'s
  `SNAPSHOT_BASE_URL` build argument (default `/data`, meaning beside the
  page), and the container reads `R2_SNAPSHOT_BUCKET` at run time. Build one
  without the other and the site renders empty rather than erroring. The
  token also needs Account → R2 → Edit on top of Pages → Edit.

  **A Cloudflare Pages project cannot be renamed.** The `pages.dev` subdomain
  is fixed at creation; renaming means creating a new project and deleting the
  old one. That is why `PAGES_PROJECT_NAME` changing is a redeploy, not an
  edit in the dashboard.

**`trading-bot-public` and `cloudflared` are retired**, along with the Zero
Trust tunnel itself (deleted outright, not just unused) — the public site is
now files with no server, no database connection, and no credentials
anywhere near it. `ten-acre.nandyalu.com`'s DNS points at the Cloudflare
Pages project directly (Workers & Pages → the project → Custom domains),
which also means the domain's *only* remaining live surface is
`trading-experiment` (private) and `trading-experiment-pages-publisher` —
both containers listed in `docker ps`, nothing else.

**One deployment, `trading-experiment`, live since 2026-09-02.** The
old `trading-bot` and `analyst-bot` containers stopped on 2026-09-01; their volumes
are kept as a record of the two experiments that ended. The new container runs
on its own `agent_data` volume, from an empty database and a freshly reset
Webull paper account.

**The experiment's start date is 2026-09-02, not the 1st.** The code was
written on the 1st and nothing was running; the agent could first act on the
2nd. `frontend/src/app/shared/experiment.ts` holds that date as one constant,
and everything on the site that says "since" or "day N" reads it from there.

The dashboard runs on **8125**, not the 8080 the template defaults to — the
deployed copy sets its own port, the same drift the `environment:` block has.
`docker ps` is the authority. The container is named `trading-experiment`.

To inspect the live container: `docker logs trading-experiment`, `docker exec
trading-experiment env`. Don't sudo-edit `/opt/stacks/...` directly — hand the user
the exact diff/snippet to paste into the Dockge UI instead (their stated
preference).

## Ollama pool topology (deployed ≠ the repo template)

`dockge/ollama-pool.compose.yaml` describes **two** backends (`ollama-pool`,
`ollama-pool-b`) behind an nginx round-robin named `ollama-lb`. That is stale.
What actually runs (verified 2026-08-06):

- **Seven** backends: `ollama-pool-a` … `ollama-pool-g`, one AMD card each (three added 2026-08-26).
  The cards are **RX 6600 (gfx1032, 8 GiB)**, not gfx1030 as an earlier note
  here claimed. They report as gfx1030 only because every pool container sets
  `HSA_OVERRIDE_GFX_VERSION=10.3.0` — ROCm's support for gfx1032 is unofficial
  and the override is what makes them work. Anyone adding cards who trusts the
  old note would omit it and spend a day on it.

  Each container is pinned to one card at the device level — `/dev/dri/card0`
  plus `renderD128` for `-a`, `card1`/`renderD129` for `-b`, and so on — which
  is why `HIP_VISIBLE_DEVICES=0` is correct in every one: it means "the only
  card I can see", not "card zero". `-e`…`-g` follow the same pattern on
  `card4`…`card6` with `renderD132`…`renderD134`.

  **Every pool container bind-mounts the same host directory** at
  `/root/.ollama/models` (`/opt/stacks/ollama-gpus/ollama/models`), so a model
  pulled or built through any one of them is immediately visible to all. There
  is no per-backend model state to keep in sync, and adding a card needs no
  model work at all.
- `ollama-proxy` (image `ollama-proxy:local`) replaced nginx. It is a small
  FastAPI app: least-active-connections routing, `CONCURRENCY_PER_BACKEND=1`,
  `WAIT_TIMEOUT=600` (queues rather than 503s), and a `/healthz` endpoint
  reporting per-backend health and active count. Still on host port 11435.

**One analysis occupies exactly one GPU.** TradingAgents' graph is internally
sequential — analysts run one after another, then the debate, then the trader —
so a single `propagate()` never has more than one LLM request in flight. Extra
GPUs are only used by running *several analyses at once*.

That is about concurrency, not stickiness. **An earlier note here said the
proxy scatters one analysis's ~20 calls across whichever backends are idle.
That is no longer true**: the proxy polls each backend's `/api/ps` and prefers
a free backend that already holds the requested model warm, so a *sequential*
run stays on one card. Concurrent runs still spread, which is the intent.

Benchmarking still has to bypass the proxy, for a different reason: you cannot
tell which card served a run. The pool containers' docker-bridge IPs are
reachable from the host
(`docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'
ollama-pool-a`), so pointing `OLLAMA_BASE_URL` at `http://<ip>:11434/v1` pins a
run to one card. **And measure one model at a time.** Seven cards are not seven
independent measurements — gemma4's E-series keeps its per-layer embeddings in
host RAM, so every card competes for the same CPU and memory bandwidth. The
same model at the same context measured 43.5, 28.0 and **69.6** tok/s depending
only on how busy the rest of the pool was (2026-08-26).

That makes `TRADINGAGENTS_MAX_CONCURRENT_ANALYSES` the knob that decides GPU
utilization. With one deployment it should equal the backend count; anything
above just queues in the proxy.

**One deployment runs, so it gets all seven.** Set
`TRADINGAGENTS_MAX_CONCURRENT_ANALYSES=7`. The two-deployment arithmetic that
used to live here — a 4/3 split, then a planned 2/5 — is gone with the second
deployment.

**Seven concurrent is the right setting, and fourteen buys nothing.** Both were
measured on 2026-09-02 with the same fourteen tickers, twice each:

| | 14 at once | **7 at once** |
|---|---|---|
| Wall clock for 14 analyses | 42.8 min | **42.5 min** |
| Median per analysis | 34.0 min | **18.6 min** |
| Tokens per analysis | 129,844 | 129,251 |
| CPU / mean GPU busy | 97% / 65% | 98% / 63% |
| VRAM peak / pool power | 5.52 GiB / 701 W | 5.45 GiB / 700 W |

**Identical throughput, half the latency, identical machine load.** The CPU
saturates before the GPUs do — the cards idle around 37% of the time at either
setting — because gemma4's E-series keeps its per-layer embeddings in host RAM.
Stacking a second analysis onto a card that is already waiting on the CPU does
not make that card produce more.

So `TRADINGAGENTS_MAX_CONCURRENT_ANALYSES=7`, and `_MAX_WATCHLIST = 30` on the
3.05 min/analysis throughput against the 120-minute window.

**Two failures in 28, both at 14 concurrent, and neither was capacity.** The
model asked for an indicator that does not exist — `macd_histogram`,
`boll_upper` — and the vendor router raised rather than telling the model the
valid names. Since 2026-09-02 the error goes back to the model instead.

**Overshooting the sum costs latency, not failures, and an earlier note here
was wrong about why.** It said `WAIT_TIMEOUT=600` is ten minutes against an
eight-minute analysis, so a third wave of queued work would start timing out.
That misreads the proxy: it holds a backend for **one LLM call**, releasing it
in the response streamer's `finally`, not for a whole analysis. A call is a
minute or two, so the timeout has enormous margin. Verified 2026-08-27 — ten
concurrent requests against seven backends all succeeded, the slowest in 38.4
seconds. Match the sum to the backend count to keep the cards busy, not to
avoid an error that cannot happen.

**A card holds one model at a time.** Loading `gemma4-e4b-qat-128k` on a
backend already holding `gemma4-e2b-96k` evicts the smaller model, even though
1.9 GiB and 3.7 GiB would both fit in the 8 GiB. So two deployments running
different models will occasionally reload one on a card the other just used.
The warm-model preference keeps a sequential run on its own card, so this only
bites at wave boundaries, and it costs one load — 6 to 40 seconds against a
7-to-17-minute analysis. Not worth engineering around.

Every multi-ticker caller must go through `analysis.run_analyses()`, which
dispatches with `asyncio.gather` and lets the shared semaphore do the bounding.
A `for ticker in …: await run_analysis_and_notify(ticker)` loop looks correct
and silently pins the whole sweep to one GPU — that bug shipped in the daily
sweep, the watchdog triggers, and the earnings check.

To check which backends served a run:
`docker logs --since 24h ollama-pool-a | grep "starting runner"` (an idle
backend has no recent entries), or `curl localhost:11435/healthz`.

## Custom context builds (`ollama/`)

`ollama/*.Modelfile` plus `ollama/build.sh`. **An earlier note here said a
model has to be installed on every backend or the proxy sends some analyses to
one that lacks it. That was wrong**: the pool shares one models directory, so
building once reaches all of them. The script builds on the first backend and
then checks the rest can see it, which is cheap and catches the day somebody
gives a container its own volume. See `ollama/README.md` for the numbers.

The non-obvious part, measured 2026-08-11 on the 8 GiB cards: **the
compute graph is what limits context, not the KV cache.** The cache is already
`q4_0` (flash attention is on) and costs 2.6 GiB at 96k, while the compute
graph at the default `num_batch 512` wants 5.1 GiB and pushes 40% of a
llama-3.2-3B's layers onto the CPU. Dropping `num_batch` to 64 fits the full
128k in 6.6 GiB, entirely on the GPU, for 16% slower prefill (411 vs 490
tok/s). So a new build needs `PARAMETER num_batch`, not just `num_ctx`, and
`ollama ps` must read `100% GPU` — any CPU split costs far more than the batch
size ever will.

Gemma is the exception that made this confusing: `gemma4-e2b-96k` runs at 96k
with the default batch because sliding-window attention keeps its compute graph
small. Don't reason from it to a Llama of the same size.

## LLM provider switching

`TradingAgents/tradingagents/llm_clients/` is a full multi-provider
abstraction (ollama, google, openai, anthropic, azure, bedrock, etc.) —
switching providers is a config change, not a code change.

**Two naming layers, and confusing them wastes an afternoon.** The app reads
`TRADINGAGENTS_LLM_PROVIDER`, `TRADINGAGENTS_LLM_BACKEND_URL`,
`TRADINGAGENTS_DEEP_THINK_LLM` and `TRADINGAGENTS_QUICK_THINK_LLM`. The short
names below are **compose-level aliases only** — `dockge/trading-experiment.compose.yaml`
and `compose.example.yaml` map them, and nothing else does. Anywhere the app
itself prints advice (the `/setup` page's `fix` lines, an error message), use
the long names, because that text is read by someone who may not be using
either compose file. A `LLM_BACKEND_URL` shipped on the setup page on
2026-09-10 for exactly this reason and did nothing at all.

The aliases in the compose files:

- `LLM_PROVIDER` → `TRADINGAGENTS_LLM_PROVIDER` (defaults to `ollama`)
- `LLM_MODEL` → both think stages (they share one value; splitting them needs
  a small code change in `TradingAgents/tradingagents/graph/trading_graph.py`)
- `GOOGLE_API_KEY` and `OPENAI_COMPATIBLE_API_KEY` pass straight through

**`compose.example.yaml` is tracked; `dockge/` is not.** The `dockge/` copy is
this machine's working template and is gitignored, so anything written for a
self-hoster must point at `compose.example.yaml` — README and two docs pages
pointed at the untracked one until 2026-09-10.

**The model is also a runtime setting.** `analysis.get_model()/set_model()`
store it in `BotSetting` under `llm_model` and `_build_graph()` applies it to
both think stages, so the settings page switches models without a redeploy.
That matters for the record: flipping the env var and redeploying would change
the model between one morning and the next. The env var above is only the default for an unset setting.
`analysis.model_choices()` lists what the endpoint actually serves, via the
OpenAI-compatible `/v1/models` route (ollama serves it too), and an
unreachable endpoint degrades to a free-text field rather than blocking a
save. Every `Signal` records `model`, and the scorecard's `by_model`
breakdown is the point of the whole mechanism — switching models teaches you
nothing if the win rates blend.

**The model to run is `gemma4-e4b-qat-128k` (measured 2026-08-26/27).** It is
what the fresh 2026-09-01 deployment should start on, because the agent chooses
its own research now and the candidate-menu decision is what e4b does and e2b
does not. `gemma4:e4b-it-qat` (8.0B raw, 4B effective) runs **100%
on the GPU at the full 131,072-token context**, using 3.7 GiB of the 8 GiB
card. Built here as `gemma4-e4b-qat-128k`. `gemma4:12b` does not fit at any context
— two to five layers always land on the CPU, and the QAT build does not rescue
it — and `gemma4:26b` (MoE, 4B active) is far worse. Full numbers in
[ollama/README.md](ollama/README.md).

**Take the QAT tag, not the default one.** `gemma4:e4b-it-qat` beats plain
`gemma4:e4b` where it has been measured repeatably: 1,122 tok/s prefill against
861 (30% faster, and prefill is what sets run time), 6.1 GB on disk against
9.6, slightly less VRAM, same 43/43 placement, same menu behaviour (4 of 4
each). Quantization-aware training puts the 4-bit rounding inside the training
loop rather than applying it to a finished model, so it should also be the more
accurate of the two. `gemma4:e4b-it-q4_K_M` is not a third option — same
manifest id as `gemma4:e4b`.

**Measure prefill on a long, cache-busted prompt.** A 30-token prompt measures
per-request overhead, not prefill, and ranked QAT *below* plain e4b — the exact
opposite of the truth. Repeating an identical prompt is as bad: Ollama's prompt
cache returns the second one in 0.03s, which reads as 219,000 tok/s. Use
several thousand tokens with a unique prefix per run.

The reason to switch off e2b is not speed; e4b is about **twice as slow**
(13m12s alone, 17m26s when two run together, against e2b's 7m15s). It is that
e4b uses the candidate menu and e2b does not. On the identical deployed prompt,
both e4b builds chose research in 4 runs of 4, picking 4-5 names of 15 with a
stated reason each; e2b managed 2 of 4, and had answered "no research" three
real mornings running. It also passes the tool-calling test that disqualified
four earlier models: 18-26 calls and **99-174k prompt tokens against the
baseline's 103k**, which is the right direction — every model that failed here
was faster because it never fetched the data. The prices are real too: AAPL's
actual $313.45 close appears verbatim in three of four runs.

**One structured-output failure per run is normal for the e4b family** — both
builds logged one, at the Sentiment Analyst or Trader stage, and both recovered
by retrying as free text. Do not read that as a QAT defect; plain `gemma4:e4b`
does it too. It is a different thing from the four-per-run failures that
disqualified `llama3.2:3b` and `phi4-mini`. Treat a count above one, or a
failure that does not recover, as a regression.

**The same ticker on the same day gave Underweight, then Overweight.** Plain
e4b gave Underweight, then Hold. That is temperature 1 — Gemma's recommended
setting, which `gemma4-e2b-96k` has been running at in production all along —
so it is not new with e4b and not a reason to tune sampling down. It does mean
a single analysis is one sample, and the Scorecard's `by_model` breakdown is
the only honest way to compare two models.

**Timing is the real cost, and the binding limit is the watchlist, not the
hour.** The sweep runs at 11:00 UTC and has until `earnings_check` at 13:00 —
**two hours**, not the one an earlier note here assumed. At three concurrent
and the paired 17.4 minutes per analysis, that is about **20 tickers**, against
roughly 51 on e2b's 7 minutes. Six tickers is two waves and comfortable.

**The watchlist ratchet this describes is solved, and only half of it.**
Commissioning a ticker calls `db.add_to_watchlist` and for a while nothing
could ever remove one. The `untrack` action and a watchlist cap landed on
2026-08-27 and cap the growth. **The cap is 30 from 2026-09-02**, measured
rather than derived: fourteen tickers at seven concurrent took 42.5 and 43.4
minutes, which is about 3.05 minutes of throughput each, and the two-hour
window fits roughly 39.

**What is still missing is an ageing rule** — nothing drops a name the agent has
stopped holding and stopped asking about. At the cap the agent must trade one
name's coverage for another's, which is a decision it can make but is never
prompted to revisit.

**The paragraphs above describe the sweep, and the sweep no longer runs
(2026-09-08).** The two-hour pre-open window, the 39-analysis throughput
figure, and `_MAX_RESEARCH_PER_DAY = 15` were all about fitting a forced daily
analysis of the whole watchlist into that window before the open. There is no
such analysis now — the agent commissions research itself, spread across the
day, bounded by cash rather than a count — so none of that arithmetic decides
anything current. The GPU throughput numbers themselves are still real
measurements and still the reference for how fast this hardware analyses a
ticker; only the reasoning that turned them into `_MAX_WATCHLIST = 30` no
longer applies. See the 2026-09-08 entry in JOURNEY.md.

**Sampling stays at Gemma's published values** — temperature 1 / top_k 64 /
top_p 0.95 (<https://ollama.com/library/gemma4>). `gemma4-e2b-96k` briefly ran
at 0.15/20/0.9 on 2026-08-26 to make the agent's research choice consistent;
that treated a symptom of the 2B model's capability as a sampling problem and
is reverted. Separately and still true: the app talks to Ollama over
`/v1/chat/completions`, which **silently ignores `temperature` in the request
body**, so `TRADINGAGENTS_TEMPERATURE` does nothing and a Modelfile is the only
channel that reaches the model.

**The previous model (2026-08-11 to 2026-09-01):** `gemma4-e2b-96k`, a custom
Modelfile build of `gemma4:e2b` with the context raised to 96k. A full analysis
takes about 7 minutes, against roughly 15 for `qwen3:latest`. That is 23 LLM calls spending
roughly 142k tokens, about 86% of them prompt tokens (one AAPL run measured
2026-08-11). An earlier "2-3 minutes" figure here was wrong: 7 matches both
that run and days of observed sweeps.

**`lfm2.5:8b` was the fifth rejection and it no longer stands (2026-08-27).**
Every failure behind it was a defect in this app, and three fixes landed the
same day: `json_schema` structured output, no price fields on `TraderProposal`,
and a rescaled sentiment score. It went from 4 structured-output failures a run
to 0 in each of two, and from citing nothing near the real close to 7 of 9 and
5 of 6 — including 315.18 against a 315.20 close. **It is 2.5x faster than e4b
and still the wrong model here**, because this agent chooses its own research
and lfm2.5 makes the candidate-menu decision in only 2 runs of 8. Full record in
[ollama/README.md](ollama/README.md).

**That should cast doubt on the four below, not confirm them.** They were
rejected on the same evidence, before any of these fixes existed, and none has
been retested since. The instruction not to retest them "without a fix for tool
calling" has been met.

**Five models were tested against this pipeline and all five failed
the same way** (kotakneo and alma-trader 2026-08-11; `llama3.2:3b` and
`phi4-mini` 2026-08-12; `lfm2.5:8b` 2026-08-27). They cannot drive
TradingAgents' tool-calling loop: either they print the tool call as text and
invent its output, or they never retrieve the data and answer anyway. Measured
against gemma4-e2b-96k's 7m15s / 21 calls / 123k tokens / **0**
structured-output failures:

| Model | Time | Tokens | Structured-output failures | What the market report contained |
|---|---|---|---|---|
| `llama3.2:3b` @128k | 3m23s | 52k | 4 | "no available market data for AAPL" — then issued a Buy anyway |
| `phi4-mini` @96k | 4m10s | 49k | 4 | the raw tool call as text, plus fabricated 2023 OHLCV around $130 for a stock at $308 |
| `lfm2.5:8b` @128k | 9m06s, 7m31s | 146k, 119k | 4, 4 | prices around $188-196 and then $144-150, for a stock at $313.45 |

**`lfm2.5:8b` is the one to read carefully, because it breaks the rule the
other four taught.** Those four were caught by a collapse in prompt tokens —
42-45k against gemma4's 103k, because a model that never fetched the data has
far less to read. lfm2.5 spent 77-96k and still invented every price. The
better tell here is the **completion share**: 34-35% against gemma4's 14-17%.
It read enough and then talked over it.

Its two runs cited prices from different years — roughly AAPL in 2024, then
AAPL in 2023. That rules out a stale cache and leaves recall from training.

**Its model card claims tool calling as a strength and it declares the `tools`
capability. Both are true and neither predicts anything here.** A tool-calling
benchmark measures whether a model picks the right function from a list. This
pipeline needs it to carry the returned number into a structured field twenty
calls later. Treat a vendor's tool-calling claim as a reason to test, never as
evidence.

Also note what it cost to find out: `lfm2.5:8b` is the **fastest model that has
ever fit this hardware** — 2,138 tok/s prefill and 107 tok/s generation, all 25
layers on the GPU at 128k in 6.1 GiB, with nothing mapped to host RAM. Speed
buys nothing on its own. Raising context does not help either, because none of
these was a context problem; don't retest any of the five without a fix for
tool calling.

This supersedes an earlier "known-bad model" note about `gemma4:e2b`. Stock
`gemma4:e2b` did hit a `GraphRecursionError` on ZBH — its reasoning loop never
terminated — but that was a context-length failure: tool-call history was being
truncated out from under the loop. At 96k the failure does not occur. Don't
revert to stock `gemma4:e2b` at the default context; `qwen3:latest` stays the
known-good slow fallback.

Analysis speed is what makes the 1-2 week trade horizon practical — signals
have to be produced faster than they expire — so treat a regression here as a
correctness problem, not a performance one.

**Gemini capacity note (2026-07-30):** `gemini-3.5-flash` returned 100%
persistent `503 UNAVAILABLE` ("high demand") over ~12h straight — looked
like a tier/capacity issue with that specific just-GA'd model, not a
transient blip. `gemini-3.1-flash-lite` worked reliably (16/16 calls
succeeded), ran a full analysis in <1 min vs Ollama's ~15 min, at roughly
$0.02–0.08/analysis. If revisiting Gemini, start with `flash-lite`, not
`3.5-flash`.

### What Gemini actually bills (measured 2026-08-22 and 2026-08-25)

**`gemini-2.5-flash-lite` is retired.** It still appears in the models list,
and calling it returns `404 NOT_FOUND`: "no longer available to new users.
Please update your code to use `models/gemini-3.5-flash-lite`". Both
`gemini-3.5-flash-lite` and `gemini-3.1-flash-lite` work on a current key, so
3.1 remains the fallback the capacity note above points at.

Speed is not in doubt and is the reason to care: a Flash-Lite analysis takes
**1.2-1.6 minutes** against gemma4-e2b-96k's **7-10**, on the same tickers the
same morning. A nine-ticker sweep is ~11 minutes of wall clock against ~85 of
GPU.

The cost is less settled.

**Always price input and output separately. Never use a blended rate.**

List is $0.30 per 1M input and $2.50 per 1M output — an 8x gap — so a blended
figure is a function of the completion share, not a property of the model:

| Completion share | Implied blended rate |
|---|---|
| 8.5% (a Flash-Lite sweep) | $0.487 / 1M |
| 16.0% (gemma4's history) | $0.652 / 1M |
| 25% | $0.850 / 1M |

This is not academic. An earlier revision of this file derived $0.399/1M from a
single Flash-Lite run and applied it to gemma4's 11.49M-token history, which
runs at 16% completion. That produced **$4.59** where the correct split-rate
answer is **$7.49** — 39% low, and it replaced a figure that had been right.
Blending was the entire error.

**Two measurements, and they disagree.** Priced at list, with rates split:

| | 22 Aug — 1 analysis | 25 Aug — 9 analyses |
|---|---|---|
| Prompt / completion | 90,713 / 9,629 | 911,876 / 84,189 |
| Completion share | 9.6% | 8.5% |
| List cost | $0.051 | $0.484 |
| Billed | $0.04 | $0.50 |
| **Billed vs list** | **78%** | **103%** |

The first reading looked like an implicit-caching discount and was written up
here as one. The second says otherwise, and three explanations fit without
being distinguishable from the dashboard: a single $0.04 line item rounds
coarsely and may simply have displayed $0.0513 as $0.04; caching may behave
differently for one analysis than for nine dispatched together; or the $0.50
may carry trailing usage that had not settled on the 22nd.

**So estimate at list.** It sat within 3% of the larger sample and does not
rest on caching behaviour nobody here has verified.

Projections for the current 9-ticker watchlist, at the 25 Aug measurement:
**$0.056/analysis, $0.50/sweep, $2.50/week, $10.58/month.**

Note the month figure **exceeds the $10/month billing cap** on the account, and
that ceiling is now the binding constraint rather than a footnote. There is no
comparison sweep any more, so pointing the model at Gemini means every analysis
goes there — the full sweep, every day. Adding tickers trips the cap sooner.
Intraday triggers are the cheap part.

**Against self-hosting.** Running the whole history to date on Flash-Lite —
9,656,261 prompt + 1,837,356 completion — would have cost **$7.49** at list,
against **$0.26** of marginal GPU electricity (11.76 GPU-hours x 100 W at
$0.22/kWh). That is **29x**.

But marginal is the right comparison only because the host is a shared home
server that would be powered anyway. Its wall meter read 22.48 kWh for 21 days
of August — **$4.95, at an average draw of 44 W** — of which the analyses were
5%. Load the idle 42 W onto the trading bot and the two costs converge; leave
it as the shared overhead it is, and self-hosting wins by an order of
magnitude. Cost is therefore not the reason to move to a vendor. Speed is.

Also worth carrying forward: **a single-ticker sample understates the average.**
The 22 Aug GOOG run was 100k tokens; the nine-ticker sweep averaged 111k, with
several tickers at 106-137k. Estimating a sweep from one small run was
optimistic by about 10% before any pricing question.

**The comparison that would have settled this never finished.** It ran from
2026-08-25 and the experiment ended on 2026-09-01 with the reset, so the numbers
above are what exists: two measurements a week apart that disagree by 25% on
billing, and a clear answer on speed. Re-derive them before switching, and
expect to have to run the switch itself as the measurement now that a paired
comparison is no longer possible.

## Vendored TradingAgents repo

It's a real nested git repo, registered as a **git submodule** of this repo
(`.gitmodules`, pinned to a commit via a `160000` gitlink) — this repo tracks
which TradingAgents commit is checked out without flattening its history.
`git clone` needs `--recurse-submodules` (or `git submodule update --init`
afterward) to populate it.

**Remote-naming gotcha**: a fresh submodule checkout only gets one remote,
named `origin`, pointing at whatever URL is in `.gitmodules` — that's the
`fork` (nandyalu/TradingAgentsUI), not upstream. The two-remote setup
described below (`origin` = TauricResearch upstream, `fork` = our fork) is
this particular working copy's local addition, done once when the branch was
first built; it does not survive a fresh clone. To restore it after a fresh
clone: `cd TradingAgents && git remote rename origin fork && git remote add
origin https://github.com/TauricResearch/TradingAgents.git && git fetch origin`.

Remotes (in a working copy that's had the above applied):

- `origin` = `https://github.com/TauricResearch/TradingAgents.git` — kept
  only as a reference point for diffing; do not push here.
- `fork` = `https://github.com/nandyalu/TradingAgentsUI.git` — our fork.
  Checked-out branch is `trading-helper-custom`, based on `fork/main`
  (itself a few commits ahead of the old v0.3.1 pin we used to track) plus 9
  commits cherry-picked from open upstream PRs that weren't merged yet but
  were judged worth having:

  - #1189 unparseable ratings surface as REVIEW instead of a silent Hold
  - #1200 avoid inventing arguments in opening debate turns
  - #1071 simplified vendor routing + CircuitBreaker for LLM vendor resilience
  - #1149 custom Ollama Modelfile guide (fast/accurate profiles)
  - #1074 retry an undecodable JSON response body instead of aborting the run
  - #1082 probability + risk/reward review on every trader proposal
  - #1134 Reddit OAuth2 (100 QPM) with automatic fallback to the RSS scraper
    when `REDDIT_CLIENT_ID`/`REDDIT_CLIENT_SECRET` are unset. **The OAuth path
    is effectively dead for this project**: Reddit's Responsible Builder Policy
    ended self-serve API app creation, so those credentials can't be obtained
    for a personal tool. RSS is the supported path — see
    [docs/setup.md](docs/setup.md). Don't treat 429 warnings as a bug.
  - #1122 candidate screener script + trade-horizon-aware analysis prompts
  - plus one local fix-up commit resolving integration issues between the
    above (an `UnboundLocalError` in `sentiment_analyst.py` that was latent
    in #1134's own diff, plus two test fixtures)

  Full upstream test suite (606 passed, 2 pre-existing skips) and
  this repo's own `backend/tests/` (80 passed) were both green against this
  branch before it was pushed.

**One upstream PR is still worth watching: TauricResearch#1076** — engine host
and streaming APIs behind a FastAPI/SSE backend. Open since July 2026 and
untouched since, so treat it as dormant rather than pending. Its one useful
finding is already superseded here: its runs execute on a single-worker pool,
and `docs/gpu-concurrency.md` measures what concurrency is actually worth on
this hardware.

To check whether `fork/main` has moved (new commits merged upstream into the
fork) before assuming a bug needs a local fix: `cd TradingAgents && git fetch
fork && git log HEAD..fork/main --oneline`. Check `git log --stat` on any new
commits before merging — don't blind-merge, and re-apply the same care used
for the original cherry-picks if `fork/main` and `trading-helper-custom`
diverge further. Dependencies (including `tradingagents` itself, installed
non-editable from `./TradingAgents` — see root `pyproject.toml`'s
`[tool.uv.sources]`) are managed with `uv`: to pick up new commits made here
in this repo's own venv, just `uv sync` — it re-resolves the local path
dependency automatically, no manual reinstall needed.

After committing inside `TradingAgents/` (new cherry-picks, a rebase onto a
moved `fork/main`, etc.), the parent repo still points at the old commit
until you also commit the updated gitlink here: `git add TradingAgents && git
commit -m "..."` from this repo's root. `git status` at the root shows
`TradingAgents` as dirty/ahead whenever the two are out of sync.

## The auto trader: what it is now

**This section is the current contract — what the agent is shown, what it may ask for, and what Python refuses.** For how it got that way, and why any given rule exists, see the changelog in [JOURNEY.md](JOURNEY.md).

**Record every behaviour change there before making it** — a reworded rule, a new number in the prompt, a different limit. Behaviour is mostly prompt, so an experiment that runs for a month across three prompt revisions has three experiments in it, and no way to tell them apart afterwards unless someone wrote down when the question changed.

### What the agent is shown

One prompt per decision pass, assembled by `agent.build_prompt()`. In order:

1. **The clock** — the Eastern time, the date, and how long until the close.
   First, because everything below is read against it and because the agent
   chooses its own next wakeup, which is a question about the time.
1b. **Why it is awake, and the note the last pass left for this one.** Four
   things start a pass — its own chosen time, something noticed while it slept,
   the last call before the close, a change to the app — and it was told none of
   them until 2026-09-12; the labels were log lines. `next_wakeup_note` is the
   other half: the agent has no memory between passes, and the prompt carries
   prices and positions but never conclusions, so this is the one place it can
   hand something to its own future self. **No wake reason may promise a
   section** — one said "see what it was, below" and the earnings path reaches
   the same pass with no alerts, so the prompt pointed at nothing and seven
   probe runs read straight past it. `build_prompt` adds the pointer, because
   only it knows whether the section is there.

   **A worked example beats a prohibition.** The rule told the agent not to
   spend the note on prices the next prompt already carries, and two of seven
   runs wrote "my cash is critically low" anyway. Adding two examples of a good
   note — *"ruled out HPE at a $59.83 entry, worth another look under $56"* —
   moved notes naming a concrete price level from **0 of 7 to 4 of 7**, and
   notes stating a condition from 3 to 5. It did not stop the balance-restating
   (still 2 of 7); it raised the floor of everything else.
2. **The regime line** when one is available — VIX, SPY against its 200-day
   average, the yield curve, as one sentence.
3. **Recent changes to the app**, when any were written down in the last three
   days — see "Telling the agent when a note was answered" below. Placed early
   because it is a fact about the world the agent should read before the
   numbers, the same reason the regime line goes here.
4. **The account**: total budget, uninvested cash, total equity with its return
   against the budget, realized profit — plus how much of the cash is unsettled,
   when any is. Unsettled money is spendable but a buy made with it cannot carry
   its stop and target in the same order, which is the real restriction on a
   cash account. The *broker's* balance is never shown.
   The simulated account holds $1,000,000 and the agent is given a small
   fraction of it; if that number reached the prompt the budget would be
   meaningless.
5. **Holdings**, one line each: quantity, average cost, current price, market
   value, unrealized profit, share of the account, days held, what is resting
   at the broker under it, and what selling all of it would raise. A holding
   with no resting exit says `NOTHING is resting to close it` — the agent
   cannot move an exit it cannot see, nor notice one that was never placed.
6. **Recent analyst signals**, up to 12 from the last 3 days, filtered to the
   model the app is configured to use. Each carries the decision, the current
   price, the suggested entry, stop and target, the model's own chance of
   working, the risk/reward and the expected value in R-multiples — plus, in
   plain words, how many whole shares the cash could buy. That last part is
   computed in Python, because the model proposed $1,944 of buys against
   $1,000 of cash on a live run when it was left to do the arithmetic.
7. **Every tracked ticker**, held or watched-only, one line each: current
   price, and either "Never analysed" or the date and price of the last
   analysis, the percentage moved since, and what that analysis said. Since
   2026-09-08 nothing is analysed on a schedule — this is the only ambient
   signal telling the agent a name has gone stale and might be worth a fresh
   $0.05 look, or that a name has never been looked at at all.
8. **Its own track record**: closed trades, how many were profitable, the net
   result, the average holding period, and the last six individually with what
   the analyst had said at entry. Once it has bought on a Hold signal twice, it
   is told how that worked out specifically — that being the pattern it
   actually falls into.
9. **How long an analysis takes**, from its own recent runs, and what is being
   analysed right now with how long it has been running. It cannot plan a
   wakeup around research it ordered without both.
10. **Its recent wakeups**, and whether each led to an action. Feedback rather
    than a limit: waking costs nothing, so pricing it would be an invented cost,
    and whether the agent learns to space them is a result worth having.
11. **What it asked to read**, when the previous turn asked for an analysis.
    Placed with the refusals, because both are replies to something the agent
    said rather than new facts about the world.
12. **What the rules noticed since the last pass** — the watchdog's own
    alerts, bounded by the previous pass rather than by a count, and which
    tracked tickers report earnings soon. Facts, with nothing done about them.
13. **What its own orders did, earlier in this same pass** — the fill and what
    is resting under it, the analysis it commissioned and what that analysis
    concluded, the untrack, the refusal. Same placement and the same reason.
14. **The rules** (below), then the JSON shape to answer in.

### Telling the agent when a note was answered

A `note` order (see the rules) reaches the people who maintain this app, and
"nothing acts on it automatically" — that was only ever true in one direction.
If a maintainer builds what a note asked for, the agent had no way to learn its
note had been read, and would keep asking or keep working around a restriction
that no longer exists.

`backend/agent_changes.json` is a git-tracked list of `{"date", "message"}`
entries, edited by hand and committed in the same change that needs one —
`agent.describe_recent_changes` shows entries from the last three days in the
prompt. Edit it at the same time as the JOURNEY.md entry the change also
needs — JOURNEY.md is prose for a person and mixes in changes that have
nothing to do with the agent's own tools; this is the one-line version aimed
at the agent, for the ones that do. Not every JOURNEY.md entry needs one: a
docs reshuffle or a GPU benchmark has nothing for the agent to act on.

A git-tracked file rather than a database row on purpose: this project has
reset its own database more than once (see "Since 2026-09-01..." above), and
an announcement should survive that the same way JOURNEY.md does.

**Shown for a number of passes rather than a number of days (2026-09-12), and
it is a correction rather than an announcement.** Three things were wrong with
the old shape at once, and together they put roughly **1,600 tokens of
changelog** in front of the agent before it saw a price:

- **Days were wildly uneven.** The agent picks its own cadence and ran between
  3 and 11 passes a day over a measured week, so one note was read about 25
  times if it landed on a busy Tuesday and twice if it landed before a quiet
  weekend — or never, if the agent slept through the window. `_CHANGE_NOTES_PASSES`
  is 3 and the count lives in `BotSetting`. **This is not "until
  acknowledged"**, which was considered and rejected: that needed a judgement
  about whether the agent had understood, and a counter judges nothing.
- **There was no cap.** Seven notes landed on 2026-09-10 alone.
  `_CHANGE_NOTES_SHOWN` is 5, and **the pool is capped before the seen-filter,
  never after** — filtering first would rotate, surfacing the batch behind the
  newest as each one expired, and the agent would work through every note ever
  written. A note that newer ones have pushed out has been superseded.
- **The notes had drifted into commit messages**, averaging 530 characters
  with two at 982 and 971. `_CHANGE_NOTE_MAX_CHARS` is 240, the renderer
  truncates as a backstop, and a test keeps the file itself inside the budget
  so it never fires.

**Write what is no longer true, not how it works now.** The agent has no
memory between passes: it reads the current rules fresh every time, so a note
explaining the new rule repeats the rules it sits beside. What the rules cannot
explain is the agent's *own history* — the decisions, wakeups and track record
lower in the same prompt were produced under the older rules. JOURNEY.md holds
the long version. Same-day notes are collapsed under one date.

`mark_changes_seen` is called **once per pass**, from `run_once` after the
first answer — a pass builds several prompts, and counting those would expire a
note inside the very pass that first showed it.

**A new entry also wakes the agent, on the restart that ships it**, rather
than waiting for whatever time the agent last chose for itself — which can be
up to four days out. `backend/tasks/scheduler.py`'s
`wake_agent_for_new_changes()` runs once at startup, after the agent's normal
wakeup alarm has been restored, and pulls that alarm forward the same way an
intraday trigger or the agent's own "research now" already does. It compares
how many entries are in the file against how many this container has already
announced (a count, stored in `BotSetting`, not a date — two entries can
share a date across two same-day deploys, and a date comparison alone would
call the second one "nothing new"). Scheduled through quiv and never
awaited, so a slow decision pass can never hold up the app coming up.

### The rules, verbatim

**The rules live in two places since 2026-09-10, split by whether they quote a number from the pass.** The fixed ones are in `SYSTEM_PROMPT`, sent once per call and never rebuilt. The ones carrying a figure — the cash limit, the watchlist cap, the trade horizon, the conviction floor, the research price — stay in the user message beside the numbers they name. `_FIXED_RULES` holds the first set; `build_prompt` assembles the second.

The system message opens:

> You are a disciplined portfolio manager. You answer with JSON only — no prose
> outside it. You never spend more cash than you have and never sell shares you
> do not hold.
>
> The rules below never change. The message that follows carries this pass's own
> figures — the clock, your cash, your holdings, the analyst signals — and the
> few rules that quote a number from them.

Then the thirteen fixed rules. The list below is all of them plus the dynamic ones, in the order the model reads them; a rule marked **(pass)** is the kind that carries a figure and therefore sits in the user message.

The opener above it, from `build_prompt`:

> You manage a small account of real money. Decide what to do with it now, if
> anything.

The rules block:

- The buys you place must cost `$X` or less in total, added up across every buy.
  Not each — in total.
- Orders execute in the order you list them, so a sell frees its cash for a buy
  listed after it. To buy something you cannot currently afford, sell something
  first and put that sell earlier in the list.
- You may only sell shares you hold. No shorting, no options. Whole shares only.
- What the analysts' decisions mean: Buy means they expect it to rise. Sell
  means they expect it to fall, so exit it if you hold it. Hold means no action
  is recommended — if you do not own it, a Hold is not a reason to buy it.
- Some signals carry how good the analyst thought the bet was. [...] Signals
  without these numbers are not worse bets, only ones where the analyst did not
  say.
- You can also move the stop and take-profit on something you already hold,
  without buying or selling any of it. Use side `adjust` [...]
- You can sell any position at any time, for your own reasons. You do not have
  to wait for a stop or a target to be reached, and you do not need an analyst
  to say Sell first. [...] A resting stop is a floor under a position, not a
  reason to leave it alone.
- Nothing is analysed automatically, holdings included. Use side `research`
  [...] to have something looked at, new or already tracked — **it runs inside
  this pass**: you wait while it runs, and are then shown what it decided and
  the analyst's own reasoning, with a chance to act on it before you finish.
  There is no daily count on how many you may commission, only cash.
- **Nothing is ever analysed unless you ask for it and pay for it.** Rules
  watch your tracked tickers for a sharp move, a volume spike, a stop or target
  being reached, and for earnings coming up. What they see is reported to you
  above and nothing else happens [...] selling into it is often the better
  answer: an analysis takes the time stated above, and the price will have
  moved again by the time it lands.
- **The market is closed right now, so a buy or a sell you place will not
  execute.** [...] **Moving a stop or a target with `adjust` does work**,
  because that rests at the broker rather than trading now. *(pass — appears
  only when the session is shut.)*
- **A signal line is a verdict, not the case for it.** The reasoning behind it
  is on record and reading it costs nothing [...] **Read one before you act on
  it.**
- To read one, use side `read` with a ticker, and a `date` [...]
- You may read up to `N` analyses before deciding, and ask again after reading
  up to `M` times [...] **Read what you need**: this is the one place spending
  is encouraged, because a decision made on a verdict alone is the thing this
  is trying to avoid. Reading is not acting, though: a pass that only read is
  an idle pass, and the budget runs out.
- **A Hold is the decision that says least**, and the one most worth reading.
  [...]

**The stance on reading changed on 2026-09-10, from sparing to expected.** The first wording said to read "when the reasoning would change what you do, not out of habit", which is advice to hesitate over something free. The costs are asymmetric: reading too often spends a turn, reading too rarely means acting on a single word. A live pass spent a long stretch reasoning about what one Hold might have meant, which is exactly what a read answers.
- You may track at most `N` tickers. To stop watching one, use side `untrack`
  [...]
- Untracking frees a slot the same way a sell frees cash, and in the same
  order: to research something when the list is full, list the untrack first
  and the research after it.
- You cannot untrack something you hold. Sell it first [...]
- If something is stopping you deciding well — a number you cannot see, a tool
  you do not have, a rule that contradicts another — say so with side `note`.
  It reaches the people who maintain you. Nothing acts on it automatically, so
  it is a message and not a request.
- A note is never a substitute for a decision. [...]
- Doing nothing is a valid answer, and often the right one.
- You decide when you are next asked, and nothing else does. `next_wakeup`
  takes an ISO datetime — `"2026-09-11T09:00"` is Eastern, a trailing Z or an
  offset is read as given. Minimum 5 minutes, maximum 4 days. **The other
  forms still parse** (minutes, `"2h"`, `"14:30"`, `"3:58 PM ET"`) and are kept
  as a fallback: dropping a usable answer costs a whole pass. One instructed
  format is what stopped the agent converting 9 AM into "1021 minutes" by hand.
  Any hour is allowed, including before the open, after the close and at the
  weekend — research works then, orders do not.
- If you name no time, you are next asked at the following open. That is a
  fallback, not a plan.
- The thesis behind a trade usually runs about `N` days [...]
- Before answering, add up what your buys cost and check it against your cash.

Two more appear only in the state that produces them, which is why they are
easy to miss when reading the code:

- **When the balance is zero or negative**, the budget rule is replaced by "You
  have no money to spend. The balance is `$X`." rather than quoting a negative
  figure as a spending limit.
- **When a conviction floor is set** (it defaults to off): "You may only open a
  new position on a signal that meets the conviction floor [...] Selling is
  never blocked this way."

Three of those exist because of a specific failure and should not be trimmed as
padding: the total-not-each wording, the sell-to-fund ordering, and the
explanation of what a Hold means. Each was added after the model got that exact
thing wrong on a live run.

### What Python enforces, regardless of what the model says

The division is deliberate and load-bearing: **the model decides what and how
much; Python refuses what cannot be executed as stated, and never resizes.**
Resizing would quietly turn its decision into a different one, and then the
record would be of a strategy nobody chose.

- **Orders are screened against a running book, not the opening one.** Three
  buys that are each affordable alone are not necessarily affordable together.
- **An unaffordable order is dropped, not shrunk.**
- **Sells cannot exceed holdings** — and since the margin account will short
  where a cash account refuses, that is now enforced in `sandbox_broker` too
  rather than inherited from the account type.
- **Exit levels that would execute on placement are refused**: a stop at or
  above the price, a target at or below it.
- **A held ticker cannot be untracked.** Since 2026-09-08 nothing is analysed
  automatically at all, holdings included — untracking a position would take
  away the only way left to ever research it again. Sell it first.
- **The watchlist is screened against a running copy too**, for the same reason
  as cash: an untrack listed before a research frees a slot for it, and two
  researches cannot share one freed slot. This only matters for a genuinely new
  ticker — re-researching one already tracked does not touch the count.
- **A ticker already tracked may be re-researched as often as the agent will
  pay for it, same day included.** Before 2026-09-08 this was refused
  outright, on the assumption that a daily sweep covered every tracked ticker
  for free. There is no such sweep now, so refusing a fresh look at something
  already tracked would mean it could never be re-analysed at all. A once-a-day
  guard (`has_signal_today`) briefly stood in its place and was removed the
  same day: an analysis finishes in about twenty minutes, and a price nearing
  its stop or target is exactly the case where a second look the same day is
  the right call. Cash is what bounds it now. `has_signal_today` still gates
  the watchdog's own automatic move-triggered re-analysis — a different
  question, the system deciding whether to auto-trigger rather than the agent
  deciding whether to ask. See the 2026-09-08 entries in JOURNEY.md.
- **A closed market refuses the order, never the pass.** Until 2026-09-10
  `run_once` returned before the model was asked whenever the session was
  shut, which made the prompt's standing invitation to wake early and
  commission the open's research quietly untrue. The venue's refusal is about
  orders; research, moving a stop, untracking, leaving a note and choosing the
  next wakeup all work at any hour. `market_clock.describe()` is the first
  line of every prompt and says which it is, and an order sent anyway comes
  back as a broker failure that the next prompt shows. **Do not add a
  timing gate here again** — the two gates in `run_once` are the sandbox
  boundary and the on/off switch, and neither is about the clock.
- **Nothing but the agent commissions an analysis (2026-09-12).** A sharp move
  and a volume spike used to nominate a ticker for one, and the pre-market
  earnings check did the same. Both decided what the agent should study, and
  both were billed to the agent's research budget — **every analysis charges,
  whoever ordered it**, which is what made this a real cost and not just a
  philosophical one: ten charges on the live book that the agent never asked
  for. `watchdog.scan_for_alerts` returns alerts only, `AlertCandidate` has no
  `trigger_analysis`, `earnings_due` replaces `earnings_tickers_to_analyze`,
  `_run_triggered_analyses` is deleted rather than left dormant, and
  `db.has_signal_today` went with its last caller. **Do not add a path that
  analyses something the agent did not order.**
- **Triggered wakes are not gated on market hours.** The gate existed because a
  move worth *analysing* at midday is worth nothing by morning; nothing
  analyses now. The earnings check runs pre-market and under that gate could
  never have woken anyone.
- **"What you just did" and "What was noticed" sit above the signal table, and
  that placement was measured.** They were between the two tables, at 42% and
  47% of the prompt, and four probe runs against the live book referenced
  neither — the model read the clock, the account and the tables and skimmed
  the prose between them. Moved above the tables, the next four runs quoted the
  alert figures verbatim: `$37.38`, `40.34` and `7.9%` appear nowhere else in
  the prompt. **Probe the prompt by reading the reasoning, not by assuming a
  section is read because it renders.**
- **Alerts are bounded by the previous pass, not by a count.** The first
  version took the newest eight whatever their age, so a Saturday pass was
  shown Thursday's moves under a heading saying "noticed". With no previous
  pass, 24 hours is the fallback.
- **A pass is a loop: act, see what happened, be asked again (2026-09-12).**
  `run_once` rebuilds the book, the prices and the signals on every turn,
  because a buy changed the cash and a research order put a new analysis in the
  table the next turn has to see. It ends when an answer does nothing — which
  is also what "no orders, wake me later" looks like — or at `_MAX_ACT_TURNS`
  (3). The read allowance is **one dict shared across those turns**, so six
  reads a pass cannot become eighteen.
- **An answer repeating the previous turn's orders ends the pass.** Acting is
  not reading: executing a repeat would buy twice, and screening only catches
  the second buy once the cash has run out, which is far too late. The prompt
  also says what has already been done and not to place it again; the guard is
  what makes that safe rather than hopeful.
- **Research runs inside the pass, and the scheduler supplies the bridge.**
  `run_once` is synchronous on a worker thread; an analysis is async work owned
  by the main loop. `scheduler._research_for_agent` is installed on the agent by
  `register_jobs` and blocks on `run_coroutine_threadsafe`, which keeps
  `agent.py` synchronous and free of a scheduler import. **The cost is that
  `_pass_lock` is held for the whole analysis**, about sixteen minutes, and that
  is deliberate — see the 2026-09-12 entry in JOURNEY.md.
- **Never ask the agent again from inside a pass.** That was the old shape and
  it never once worked: the pass dispatched its research, then called
  `_maybe_run_agent` while still holding `_pass_lock`, whose first line is
  `if _pass_lock.locked(): return`. Seventeen analyses across two days produced
  no pass at all. The prompt had promised "you are asked again automatically",
  which made it a lie the agent planned around.
- **A read earns one follow-up turn, and it is the same one a refusal uses.**
  `read` never reaches `screen` — it moves no cash, no shares and no watchlist
  slot — so it is split out in `_decide` before screening, because it changes
  the control flow rather than the book. A second read in the same pass is
  dropped rather than answered: silently granting a third turn is how "let me
  look at one more thing" becomes the whole pass. The refusals from a pass that
  read still stand and still reach the next prompt.
- **A read resolves by ticker and an optional date, and sorts locally.**
  `db.get_recent_signals` orders by `signal_date` alone by default, which is a
  calendar date, so two analyses of one ticker on one day come back in row
  order — asking for INTC's 2026-09-08 analysis returned the 19:06 one over the
  19:18 one. `analysis_reader` sorts by date, then `created_at`, then id, using
  `signals.newest_first` — one copy of that key, shared with the research page,
  which had the same fault for the same reason. **Do not change the query's
  default ordering: every other caller reads it.**
- **Sorting after the fetch is not enough when a LIMIT is involved**, which is
  the one case that needs the query. A day here holds up to eleven analyses, so
  a limit landing inside a day keeps whichever rows the table offers first — the
  day's *oldest* — and the newest are never fetched at all, so no later sort can
  recover them. Measured on the live book 2026-09-11: `limit=10` returned
  2026-09-10's four oldest and dropped its seven newest. `get_recent_signals`
  therefore takes an opt-in `by_time=True` that adds `created_at` and `id` to
  the ORDER BY. Opt-in, not the default, for the reason above.
- **`Signal.created_at` is when the analysis *started*, for every row.** It
  meant the finish on rows written by `record_signal` and the start on rows
  recovered from `trace_id`, about sixteen minutes apart, until 2026-09-11. The
  start won because it is the instant actually observed — `trace_id` encodes it
  — where a finish has to be computed. `propagate_ticker` puts it in
  `final_state["started_at"]` and `record_signal` passes it down;
  `backend/scripts/backfill_signal_timestamps.py` resets every row that carries
  a trace_id and is safe to re-run. See the 2026-09-11 entry in JOURNEY.md.
- **A refused order is fed back once** and the model asked again, which is how
  it learns it may sell to fund a buy, and untrack to fund a research. The
  advice in that retry is matched to the refusal — cash advice does not help a
  full watchlist, and the first live probe produced exactly that mistake.
- **The clock knows the NYSE calendar, and it is computed rather than listed.**
  `market_clock.describe()` had no holidays at all, so on Labor Day 2026-09-07
  it read "the market closes in 5h 28m, at 4:00 PM" and the agent placed five
  orders across three passes that the venue refused — **five of the six
  market-closed refusals in the whole record are that one day**, and the sixth
  is a Saturday. `market_holidays()` derives all ten closures from the
  published rules (including Good Friday from Easter, and the
  Saturday-to-Friday / Sunday-to-Monday observance), `early_closes()` covers
  the three half-days, and `next_open()` skips both. **No dependency and no
  table**: a table is a copy of the rules that goes stale the year nobody
  updates it. The line now names the reason and the reopen, and the model's
  reasoning restates it — *"The market is closed until Monday, 14 September
  2026 at 9:30 AM ET."*
- **A closed session stops a buy and a sell; `adjust` still works, and that was
  checked rather than assumed.** The clock line was never enough on its own —
  three probe runs wrote "the market is closed" and placed an order in the same
  breath, and one stated the belief behind it: *"any buys/sells placed now will
  not execute until market open"*, which is a fair guess and wrong, because this
  broker refuses rather than queues. The rule now sits beside the order rules
  and only when `watchdog.is_us_market_hours()` is false.

  **The `adjust` exemption is the load-bearing part.** An earlier wording had
  the broker refusing "a buy, a sell or an adjust" and the record contradicts
  it: on Labor Day run 15 adjusted two exits at 09:31 while runs 16, 17 and 18
  had five buys and sells refused over the next six hours. **No adjust has ever
  been refused** in nine broker failures. An exit rests rather than trading now,
  so there is nothing to reject — and it is the one useful thing left to do with
  a position going into a long weekend. Do not re-broaden it.

  **The behavioural claim is unproven and should not be repeated as fact.**
  Seven probe samples with the rule placed no buys or sells — but the baseline
  placed none either, and across every baseline run it is 2 of 11. What the
  probe does show is that the correction is *read*: six of seven restate it,
  one as *"Orders will not execute, except for `adjust` (stop/target)."* The
  measurement that matters is whether the live book's closed-market failures,
  six of nine to date, stop growing.
- **`is_us_market_hours()` knows holidays and half-days.** It answered True all
  afternoon on Labor Day, and its five callers all mean "is the session
  genuinely open" — one refuses a reset needing a market order, one queues an
  exit-arm, one takes a live quote instead of the last close. Each was wrong in
  the same direction that day. The calendar lives in
  `backend/services/market_calendar.py`, which **imports nothing from this
  project on purpose**: `market_clock` already imports `watchdog`, so a calendar
  inside either one puts a cycle between them.
- **The parser reads orders the model put beside `orders`, and repairs two
  malformations.** Five decisions were dropped silently across 52 stored
  answers — two notes and three research orders — and it surfaced only because
  the agent's own note mentioned research that never ran. The model sometimes
  answers `{"research": [...]}` or `{"note": "..."}` with no `orders` key, and
  run 44 closed a string with an apostrophe (`…the thesis'`) so the whole
  answer parsed to nothing. **Only `research` and `note` are salvaged**: a bare
  `"sell": "AVGO"` does not say how many shares, and guessing places a trade
  nobody chose. Only repairs that cannot change a meaning are applied, and
  anything else stays a clean loss.
- **A `note` is accepted before any check that could refuse it**, so an account
  with no cash left can still leave one. It moves no cash, no shares and no
  watchlist slot, and **it does not count as acting** — a pass that only left a
  note is still an idle pass. Without that, "I need better data" stands in for
  the decision the agent owed.
- **Broker failures are stored, not just counted**, and the last five from the
  last three passes appear in the next prompt. `refusals` and `failures` are
  separate columns on purpose: a refusal says the agent's arithmetic was wrong,
  a failure says it formed the order correctly and the world would not take it.
  Those are different facts and it needs the difference.

### Four guards keep this a simulation, and none of them may be relaxed

**The prompt may lie to the model. The code must never lie to itself.**

**The prompt stopped telling the agent the money is fake on 2026-09-09, and nothing below changed.** It now opens "You manage a small account of real money", because an agent that knows the stakes are imaginary is not being asked the question this experiment exists to ask. That is a story told to a model. The four checks below are the code's own knowledge of what it is connected to, and the two must never be traded against each other.

Four checks stand between this experiment and a machine spending real money:

- **`_assert_sandbox()`** runs immediately before every order, not once at import, so flipping the environment mid-process cannot leave a live client armed.
- **The `DE` account-number prefix check.** Every simulated account on the sandbox host is DE-prefixed, in both the DEM and DEL series. Widening it from `DEM` to `DE` on 2026-09-03 corrected a wrong observation — the check had been written from two accounts out of five — and was not a relaxation.
- **The account-class check.** The target is resolved by `account_class == INDIVIDUAL_CASH`, never hardcoded.
- **`WEBULL_ACCOUNT_ID` names the one account this deployment owns**, and an unset or empty value stops order flow rather than falling back to anything. Added 2026-09-09 for a second container: the three checks above narrow five accounts to one, and narrow to the *same* one for every deployment applying the same rule — so two containers would trade one book and the record could not say which placed an order. It takes the account number (`DE…`) or the internal id, matches either, and is applied **after** the other three, so it can only ever narrow what they allowed. There is deliberately no default: the value of this guard is that a person wrote down which book the container owns.

**The day someone relaxes one of them *because the agent thinks it is real anyway* is the day this becomes dangerous.** A prompt is a story told to a model. These are the code's own knowledge of what it is connected to, and the two must never be traded against each other.

### How a position is opened and protected

A buy goes out as a **bracket**: a `MASTER` entry with `STOP_PROFIT` and
`STOP_LOSS` legs, one submission, one shared combo id. The broker activates the
exits when the entry fills, so the shares are never held with nothing under
them. The entry is a marketable limit rather than a market order because Webull
refuses a `MARKET` master — and the limit caps slippage, which matters on an
app-enforced budget.

When the stated stop is unusable, one is derived from 2×ATR(14) at the moment
of purchase. When a combo is refused — which happens routinely, because a cash
account will not accept one against unsettled funds — it falls back to a market
order plus separately-armed exits.

### What is not built, and what will never be

**Read this before proposing work.** Two of these were considered and rejected on purpose, and a proposal to add one arrives about once a month.

Open, and worth building:

- **Replay** — re-run a past decision pass against a changed prompt, so a prompt change can be told apart from a market change. Behaviour here is mostly prompt, which makes this the missing measurement rather than a nice-to-have.
- **Backtester** — grade the strategy over history rather than only forward.
- **A watchlist ageing rule.** The cap of 30 stops the list growing without limit, but nothing drops a name the agent has stopped holding and stopped asking about. At the cap it must trade one name's coverage for another, which it may do but is never prompted to revisit.
- **A position-size cap** — *deliberately absent, not forgotten.* The agent has put 100% of the book into one name. A cap changes what the agent may decide rather than correcting its arithmetic, so it needs its own journal entry and its own reasoning, not a quiet fix. See the 2026-08-29 entry in [the analyst experiment](docs/analyst-experiment.md).

Permanent non-goals:

- **Real order execution.** Every order goes to Webull's sandbox and the agent refuses to run without `WEBULL_SANDBOX=1`. This is a boundary, not a phase that has not arrived.
- **Manual controls of any kind.** Corrections go through the journal and then by hand.
- **Shorting.** A sell closes a long. Enforced in `sandbox_broker` rather than inherited from the account type, because a margin account will short where a cash account refuses.
- **Intraday LLM analysis.** The local model is the bottleneck — an analysis takes about eighteen minutes — so alerts stay rule-based. Adding one would put a model in a loop it cannot keep up with.

### Where a change gets written down — three files, three questions

**This file answers "what are the rules now".** The sections above are the
current contract: what the agent is shown, what it may ask for, and what
Python refuses. Read it before changing the code.

**[JOURNEY.md](JOURNEY.md) answers "when did the experiment's question
change".** Read it before changing a rule, and add to it before you do.

**[docs/changelog.md](docs/changelog.md) answers "what changed about the
app".** Deployment, setup, guards, infrastructure, the site, the docs,
dependencies.

**One question decides between the last two: does this change make two periods
of the experiment non-comparable?** Yes, JOURNEY.md. No, the changelog. Three
tests, any one of which is enough for JOURNEY.md:

- **Behaviour** — it changes what the agent is shown, what it may ask for, or
  what Python refuses.
- **Evidence** — it changes what the record contains or means. Telemetry
  counts. A field that is null before a date is exactly what trips up whoever
  reads the data later, and "it is only plumbing" is how those get missed.
- **Incident** — the agent's behaviour changed without anyone intending it, so
  real days are contaminated. A silent bug feels like a fix and is not.

**JOURNEY.md entries are a sentence or two: what changed, and why.** It grew to
789 lines and 50 entries by 2026-09-10, of which roughly 60% were not about the
agent at all, and a record nobody reads end to end cannot do its job. The sweep
that fixed it left 28 entries in 108 lines. Long-form reasoning that constrains
a future edit does not go there — it goes here, because this is the file that
gets read before the code is changed.

The total-not-each wording, the sell-to-fund ordering and the meaning of a Hold
are the clearest cases: each was added after the model got that exact thing
wrong, and each reads as padding to anyone who does not know that. That is why
they are stated in the sections above rather than left in a dated entry.

### Probe the prompt: send it to the model and read the reasoning

**A prompt change is a hypothesis until the model's own reasoning confirms it.** `python -m backend.scripts.probe_prompt --turn turn1 [--parallel]` builds the real prompt from the database and calls the model out of the app — it never reaches `run_once`, `screen` or any broker path. `.claude/skills/probe-the-prompt/SKILL.md` says how to read what comes back.

**Every prompt bug in this project was found this way, and none was visible in the code, the tests, or the rendered prompt:**

| What looked fine | What the reasoning showed |
|---|---|
| The sharp-move rule | Applied to a ticker nothing was watching |
| "What was noticed" | Not one of four runs referenced it |
| A research result handed back | Cited as "the analyst", never as its own spend |
| The change-note section | 1,600 tokens, quoted by nobody |

The last three were **correct code producing correct output**. Tests passed, the sections rendered, and the model read past them.

Three findings worth carrying forward, because each cost a round of probing:

- **Tables get read; prose between tables gets skimmed.** Two sections at 42% and 47% of the prompt went untouched by four runs. Above the tables, the next runs quoted them — `$37.38` and `40.34` appear nowhere else in the prompt, which is what makes that hard evidence rather than an impression.
- **Put a fact where it is already read rather than repeating it.** A research result appeared twice, as a signals-table row and as a prose block, and the model reconciled the copies and kept the table — calling it "the analyst". It was not ignoring the prose; it was picking the canonical copy. Marking the row fixed it (0 to 4 of 7 claiming it as their own); adding emphasis to the prose had not.
- **Do not grep the reasoning for the words you wrote.** The model paraphrases, and a keyword scan reported "not used" for a run that had quoted the section verbatim. Find a number that appears in only one section, or read it.

**`agent._invoke` prepends `SYSTEM_PROMPT` itself**, so a probe that hands it system+user concatenated sends the system prompt twice. The script sends them as the two messages they are.

### Before changing the prompt

Add the entry to **[JOURNEY.md](JOURNEY.md)** first, with the date and the reason. A month of runs across an undocumented prompt revision cannot be analysed, and the temptation to reconstruct the reasoning afterwards produces a story about what we would like to have been thinking.

**Then re-check "The rules, verbatim" above in the same edit.** That section quotes the prompt, and a quotation is stale the moment the original moves. By 2026-09-10 it had drifted three ways at once: it quoted a system message that still said "paper-trading" when the code had dropped the word a day earlier, it was missing the sell-any-time rule entirely, and it was missing the rule about a sharply-moving stock being analysed unasked. **A wrong quotation here is worse than no quotation**, because this file is what gets read instead of the code.

## A tool error goes to the model, not to the logs

**A raising tool used to end the analysis.** On 2026-09-02 that discarded two
complete forty-minute runs: the model asked for an indicator called
`macd_histogram` when the real name is `macdh`, and the error listing all
thirteen valid names went to the logs instead of to the model.

Two things were wrong and the second was worse.

**A caller error counted as vendor ill-health.** The bad name raised a plain
`ValueError`, the router caught it generically and recorded it against the
circuit breaker. Five bad names opened yfinance's circuit at the third, and for
the next five minutes *every* `get_indicators` call failed with "No available
vendor" — including the valid ones. `CircuitBreaker`'s own docstring says only
transient errors should open it.

So `BadVendorArgumentError` now marks "the request was wrong, the vendor is
fine". The router **does not touch the breaker** for one and **does not fall
through to the next vendor** — every vendor rejects the same invalid argument,
so trying them in turn only wastes requests and buries the message.

**Every `ToolNode` sets `handle_tool_errors`.** The model is handed the message
and calls again, which is how a tool-calling model is meant to recover, at a
cost of one call against an analysis of twenty.

**The handler returns the message and nothing else.** No suggestion of what to
try instead. A model told "that failed, try something else" invents a plausible
substitute, and an invented answer that reads as data is exactly what
disqualified four models in August. The vendor's message already names the
valid values; anything past that is us guessing.

`backend/tests/test_tool_errors_reach_the_model.py` holds the guarantee in this
repo, so a submodule bump that reverts it fails here rather than in a live run.

## Per-run cost telemetry

`backend/services/llm_usage.py` counts what each analysis spends: wall-clock
seconds, LLM calls, and prompt/completion tokens, stored on `Signal` and shown
in the Discord footer and the signal detail page. It exists to make the
self-host-vs-cloud decision arithmetic rather than guesswork, which is why the
token split by direction matters (cloud output tokens cost several times input
ones).

Two things about it are easy to get wrong:

- **The counts come from the provider's `usage` block, never a tokenizer
  estimate.** An estimate is wrong by exactly the amount that matters once it
  is multiplied by a price per million.
- **`UsageTracker` attaches to the two LLM client objects, not to a
  `propagate()` argument** — `propagate()` accepts no callbacks, and every
  agent, the debate, the reflector, and the signal processor all share
  `graph.deep_thinking_llm` / `graph.quick_thinking_llm`. Attaching there is
  what makes the count cover the whole run. A per-run tracker is safe because
  `_build_graph` already builds a fresh graph per analysis.

An unmeasured run stores NULL, never 0 — a zero would read as a free run.

## The model invents price levels (important)

`gemma4-e2b` reasons acceptably in prose but does not reliably carry concrete
figures into structured numeric fields. On 2026-08-06, 3 of 8 signals came back
with fabricated entry / stop / target levels:

| Ticker | Real price | Model's entry |
|---|---|---|
| GOOG | $356.62 | $2,000.00 |
| VERI | $1.26 | $4.50 |
| VERI | $1.25 | $30.00 |

The numbers look like **prices the model remembers from training** — $2,000 is
roughly pre-split GOOG, $30 roughly VERI's 2021 range. Two runs on the same
stock the same day produced entries 24× apart, so it is invention, not stale
data and not another ticker bleeding in. The market analyst's own report had
the right prices throughout; the *trader* stage was simply never given a price.

**The root fix landed 2026-08-27: the model is no longer asked for a price.**
`TraderProposal` has no `entry_price`, `stop_loss` or `target_price` field at
all. It states two distances — `stop_atr_multiple` and `target_r_multiple` —
and `resolve_levels` computes the prices from the verified close and ATR that
`verified_levels_basis` returns as numbers. A field that does not exist cannot
be filled from memory, which is stronger than any instruction not to.

The rendered markdown is unchanged, because `analysis._trade_plan_levels`
parses the level lines out of it. Only who computes the number moved.

**The defenses below all stay.** They now catch a different class of error — a
bad multiple, a missing basis, a level that survived one check and not another
— rather than a remembered price, and they are what makes the new arithmetic
safe to trust:

1. **The trader still receives the deterministic snapshot.**
   `build_verified_market_snapshot` (computed in Python from the same OHLCV,
   never by a model) goes into the trader prompt, which is what the model
   reasons over when choosing how much room the trade needs.
2. **`analysis._trade_plan_levels` discards levels far from the traded price**,
   using `max_level_deviation_pct` per horizon (swing 35%, position 70%).
   `risk_reward` and `expected_value_r` go out with them, since TradingAgents
   computes both *from* those levels. `win_probability` survives — it is the
   model's own estimate, not a derivation.

Defense 2 is the one that must never be removed. A prompt cannot make a 2B
model reliable, and a fabricated stop is worse than no stop: the watchdog arms
an alert at a price the stock may never reach, or fires one immediately.

3. **`analysis._levels_on_the_wrong_side` drops a stop at or above the traded
   price and a target at or below it.** Defense 2 asks only how far a level is
   from the price, never which side of it the level is on, and the gap between
   those two questions is wide: 14 of the first 42 signals — a third of the
   book — held a level on the wrong side while every one of them sat inside the
   deviation tolerance. The shape is always the same. The model proposes a
   pullback entry, draws its stop and target around *that* entry, and the
   pullback never comes: ZBH on 2026-08-12 wanted to buy $91.00 with a $90.76
   stop and a $92.00 target while the stock traded at $97.89. All three levels
   are within 8% of the price and the plan is internally coherent — it is only
   the entry that never happened. Levels are read from the traded price
   forward, so a target under it is reached the instant it is stored and a stop
   over it triggers the same way. Nulling the stop also hands the signal to the
   ATR fallback in `_resolve_stop_loss`, which is how a Buy still ends up with
   a usable exit. Sell-ish decisions are exempt: this app is long-only, takes
   no action on them, and their levels point the other way by design.

`backend/scripts/scrub_implausible_levels.py` clears bad levels from rows
written before defense 2 existed; `backend/scripts/clear_wrong_side_levels.py`
does the same for defense 3. Both leave graded signals alone — grading read the
target to decide `price_target_hit`, so rewriting it afterwards would
contradict a verdict already given.

## Market data goes through the bar cache

`backend/services/bars.py` is a read-through cache over the `dailybar` table
(`(ticker, date)`). **Route any new daily-history read through `bars.get_bars()`,
not `yf.Ticker(...).history()` or a direct Webull call** — the whole point is
that a completed session never changes, so refetching one is waste and
rate-limit risk.

**Webull first, yfinance as fallback (2026-09-08).** `bars._fetch_history` tries
`_fetch_from_webull` (the same history-bar endpoint `backend/services/intraday.py`
uses for 1-minute bars, called here with `Timespan.D`) and falls through to
`_fetch_from_yfinance` only when that returns `None` — Webull not configured,
the call failing, or coming back empty. Confirmed live: the endpoint pages back
daily bars with no real depth ceiling, over 2,000 bars deep in testing. yfinance
is not removed — it is what already produces this app's "possibly delisted"
false positives and 429s, and a Webull outage must not take the whole daily
cache down with it.

Two legitimate direct yfinance uses remain, neither of them history:
`positions.get_current_price` (a live quote, Webull's fallback) and
`watchdog.get_next_earnings_date` (the calendar).

Non-obvious rules the cache depends on:

- **Today's bar is never stored.** It is still moving. `include_today=True` gets
  it via a separate live request instead.
- **Pass `today=` when the caller has a market-relative date.** The watchdog
  does: after about 8pm ET the local clock is already tomorrow, so the default
  would treat the just-closed session as still in progress.
- **`_earliest_attempt` records what was asked for, not what came back.** Without
  it, a ticker with less history than requested refetches on every call forever.
- **`last_completed_session` ignores holidays deliberately.** The 30-minute
  recheck throttle absorbs the resulting extra request.

The table is pure cache; dropping it costs only a refetch.

## Webull OpenAPI reference

**<https://developer.webull.com/apis/llms.txt>** — an LLM-oriented index of the
whole OpenAPI documentation, with links to every endpoint page. Read it before
guessing at a Webull payload shape or endpoint name. The Python SDK
(`webull-openapi-python-sdk`) returns **raw dicts with no typed models**, so the
field names this repo relies on were discovered from live responses, not from
the package — the docs are the only other source of truth.

What the index covers, and where each maps here:

| Docs area | Used by |
|---|---|
| Account Management — Account List, Account Balance, Account Positions | `backend/services/broker.py` (`fetch_broker_positions`) |
| Market Data — snapshot, tick, depth, bars, fundamentals | `backend/services/quotes.py` (`get_realtime_price`) |
| Authentication — HMAC-SHA1 signature, client token lifecycle | `quotes.get_api_client`, token cached under `WEBULL_OPENAPI_TOKEN_DIR` |
| Order Management — preview/place/replace/cancel | `backend/services/sandbox_broker.py`, **against the sandbox only**. Every call passes `_assert_sandbox()` first. Real order execution on a production account is a standing non-goal |

### Combo orders: what the docs and `preview_order` both get wrong

The agent buys with a **bracket** — `MASTER` entry plus `STOP_PROFIT` and
`STOP_LOSS` legs, one `place_order` call, one shared `client_combo_order_id`.
The broker activates the exits when the entry fills, so no position is ever
held with nothing resting under it. Arming afterwards always had that window:
on 2026-08-13 both buys filled and neither got its exits, because the sell legs
were validated while the account still held nothing and read as a new short
(`GENERATE_NEW_SHORT_POSITION`).

Brackets are **not in `llms.txt`** — only the Place Order page's `combo_type`
enum mentions them. Four rules were found by testing against the live sandbox,
and none of them appear in the docs:

- A `MASTER` leg **cannot be `MARKET`**, and cannot be `GTC`
  (`INVALID_PARAMETER`). The entry is a marketable limit instead — 0.5% through
  the offer, `DAY` — which behaves like a market order and caps slippage.
- The exits **may be `GTC` under a `DAY` master**. They have to be: a `DAY`
  exit protects the position for an afternoon and then quietly stops existing.
- A stop at or above the entry limit is refused
  (`TRADE_STOP_LOSS_PRICE_LT_OPENPRICE`) — and the refusal takes the **buy**
  with it, because the legs are one submission. `agent._place` screens both
  levels before sending, so a bad level costs nothing.
- **Any combo is refused while the cash is unsettled**
  (`CANT_USE_UNSETTLE_FUNDS_FOR_COMBO_ORDER`). A plain market order may be
  placed against unsettled proceeds; a combo may not. Selling to fund a buy in
  the same pass is something the agent's prompt explicitly permits, so this is
  routine, not an edge case — `_place` falls back to a market order plus
  `_arm_exits` rather than failing the trade.

**`preview_order` cannot be used to check a combo.** It returned 200 for the
`MARKET` master that `place_order` then rejected outright. Preview validates
cost, not shape.

Two things the index settles that have bitten this project:

- It confirms there is **no news or social-sentiment endpoint**, so Webull
  cannot replace `get_news`/`reddit.py` no matter how the quota looks.
- It documents **MQTT streaming** for real-time data. This repo polls instead,
  which is the right call at a 1-2 week horizon, but it is the thing to reach
  for if intraday granularity ever matters.

Rate limits are not in the index itself — they live on the linked Market Data
API Overview page. Order History is documented at 2 requests per 2 seconds.

### Confirmed from the docs (2026-08-07)

**Account Positions** returns exactly: `position_id`, `currency`, `quantity`,
`symbol`, `option_strategy`, `instrument_type`, `last_price`, `cost_price`,
`unrealized_profit_loss`, `event_outcome`, `legs[]`. The four names
`broker._parse_position` relies on are correct.

**There is no acquisition-date field on a position.** Not under any name. This
makes `broker._parse_opened_at` dead code — it tries `open_date`,
`position_date`, `open_time` and others, and none of them can ever match, so
every synced holding falls through to the `(date unknown)` path and is excluded
from the vs-SPY comparison. It was written that way because the payload shape
had never been checked; the fallback is doing all the work.

**Order History is where the dates are**, and the sync now reads it
(`broker.fetch_order_fills` → `reconstruct_open_lots`). Three things about that
endpoint are not obvious and cost time to find:

- **Rows are combo wrappers, not orders.** Each row is
  `{client_order_id, combo_type, combo_order_id, orders: [...]}`, and a
  single-leg order is still wrapped in a one-element list. Read the top level
  and you find no symbol, no side, no quantity — every row parses to nothing.
  `broker.orders_in()` unwraps it.
- **`page_size` must be 10-100.** Anything outside that is HTTP 417,
  `OAUTH_OPENAPI_PARAM_ERR`.
- **The documented "2 requests per 2 seconds" is optimistic.** Pacing at exactly
  that returned 429s; `_ORDER_HISTORY_PAUSE` is 2.5s.

Verified against the live account 2026-08-07: 53 equity fills back to
2021-02-23. Dropped rows were all correct — unfilled orders, plus OPTION and
CRYPTO fills this equities-only app has no use for.

`fix_import_dates --from-webull` rebuilds already-imported holdings the same
way. Holdings with no fill (transferred in, or pre-2018) keep a date-unknown
remainder lot and stay out of the benchmark comparison rather than corrupting
it — which is also how a fractional dividend share is handled, since it never
came through an order.

## Tickers that stop trading

`backend/services/listings.py` marks a ticker inactive once no fresh bar has
appeared for `STALE_AFTER_TRADING_DAYS` (7). Every fetch path checks it: the bar
cache, `get_current_price`, and the watchdog's tracked list.

**Why it needs detecting at all:** a delisted symbol does not fail cleanly.
AILEQ returned five bars across two months, every one priced at $0.000001.
Nothing in that looks like an error — to the bar cache it was a ticker merely
behind, so it refetched every 30 minutes forever, and the (now-retired) daily
sweep spent minutes of GPU analyzing a company with no market, then could not
record the signal because there was no price to record it against.

**The rule is freshness, not price.** A real penny stock at $0.0001 is still
real and must keep working; a price threshold would wrongly exclude it.

An inactive ticker is still rechecked once a day, so a lifted halt recovers
without anyone noticing. `/ignore` and `/unignore` are the manual override, and
a manual setting is never overwritten by detection.

A held position stays in the portfolio — there is just nothing to fetch, and its
lots are excluded from the vs-SPY comparison like any other undateable lot.

## Reddit/social-sentiment data source

`TradingAgents/tradingagents/dataflows/reddit.py` scrapes Reddit's public RSS
search feed (no API key). Occasional `429`/warning logs are expected and
handled gracefully (retry-once-with-backoff, then degrades to "no posts
found" for that subreddit) — not a bug unless it fails on *every* run.
`stocktwits.py` exists in the same directory as an unused alternative if
Reddit ever becomes unreliable enough to matter.

The Webull OpenAPI SDK (`backend/services/quotes.py`, `backend/services/broker.py`) is market-data +
brokerage only (quotes, fundamentals, financials, trading) — it has no
news-article or social-sentiment endpoints, so it can't replace
`get_news`/`reddit.py`.
