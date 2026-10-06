---
paths:
  - "ollama/**"
  - "docs/gpu-*.md"
  - "backend/services/analysis.py"
---

## Ollama pool topology (deployed ≠ the repo template)

`dockge/ollama-pool.compose.yaml` describes **two** backends (`ollama-pool`, `ollama-pool-b`) behind an nginx round-robin named `ollama-lb`. That is stale. What actually runs (verified 2026-08-06):

- **Seven** backends: `ollama-pool-a` … `ollama-pool-g`, one AMD card each (three added 2026-08-26). From 2026-09-26 to 2026-09-27 the riser card of `ollama-pool-f` (PCI `0000:18:00.0`) ran Laya instead, and `-f` is back in the pool since. See "Laya on these cards" below. The cards are **RX 6600 (gfx1032, 8 GiB)**, not gfx1030 as an earlier note here claimed. They report as gfx1030 only because every pool container sets `HSA_OVERRIDE_GFX_VERSION=10.3.0` — ROCm's support for gfx1032 is unofficial and the override is what makes them work. Anyone adding cards who trusts the old note would omit it and spend a day on it.

  Each container is pinned to one card at the device level — `/dev/dri/card0` plus `renderD128` for `-a`, `card1`/`renderD129` for `-b`, and so on — which is why `HIP_VISIBLE_DEVICES=0` is correct in every one: it means "the only card I can see", not "card zero". `-e`…`-g` follow the same pattern on `card4`…`card6` with `renderD132`…`renderD134`.

  **Every pool container bind-mounts the same host directory** at `/root/.ollama/models` (`/opt/stacks/ollama-gpus/ollama/models`), so a model pulled or built through any one of them is immediately visible to all. There is no per-backend model state to keep in sync, and adding a card needs no model work at all.
- `ollama-proxy` (image `ollama-proxy:local`) replaced nginx. It is a small FastAPI app: least-active-connections routing, `CONCURRENCY_PER_BACKEND=1`, `WAIT_TIMEOUT=600` (queues rather than 503s), and a `/healthz` endpoint reporting per-backend health and active count. Still on host port 11435.

**Since 2026-09-15 `choose_backend()` picks a direct-slot card before a riser card.** The riser cards (`-d`, `-e`, `-f`, PCIe 2.0 x1) write slower than the four direct cards. `RISER_BACKENDS` (default `ollama-pool-d,ollama-pool-e,ollama-pool-f`) marks them; a riser card is chosen only once every direct card is busy. The deployed stack set it to `ollama-pool-d,ollama-pool-e` while `-f` ran Laya, and back to all three on 2026-09-27.

**One analysis uses up to four GPUs, for about three minutes (since 2026-09-13).** The four analysts run at the same time, each with its own request in flight. The Bull/Bear debate, the trader and the risk debate then run in turn, one request at a time. Most of the extra GPU use still comes from running *several analyses at once*.

Measured 2026-09-13, outside the app: one NVDA analysis alone took 10.4 to 11.8 min, against 16.2 min for the old sequential graph, with the analyst stage at 3.0 to 3.4 min against 8.6. Four analyses started together all finished in 14.9 min, with 7 cards busy and the host CPU at 97% mean. **The CPU limits the analyst stage**, for the same reason as the table below. The table was measured before this change and has not been measured again.

**`gemma4-e4b-qat-128k` sets `num_thread 1` on purpose (2026-09-14).** Ollama's default is 4 threads for each runner, and seven runners then ask for 28 threads from the 8-thread i3-10100F. With 1 thread, the pool writes 16% faster and reads 5% faster when all 7 cards work, and one card alone reads at the same speed. The splitter cards (`-d`, `-e`, `-f`) gain only 5% when they write, probably because their PCIe 2.0 x1 uplink limits them. That cause is not proven. The measurements are in the Modelfile. A new model build for this pool needs the same test before it gets a thread count.

**`num_thread 1` still holds now that the agent asks for one analysis at a time (measured 2026-09-15).** The table above was measured at 7 concurrent analyses, a load pattern the app no longer produces — every batch caller (daily sweep, watchdog, earnings check) was moved behind the agent's own $0.05-a-run decision, so the normal case is one analysis, four concurrent analysts, not seven or fourteen. Outside the app, one NVDA analysis at a time, on the production `gemma4-e4b-qat-128k` build against a test build differing only in `num_thread`:

| | `num_thread 1` (production) | `num_thread 2` (test) |
|---|---|---|
| Wall clock | 9.90 min | 9.98 min |
| Host CPU busy, mean | 31.9% | 42.8% |
| Host CPU busy, peak | 76.3% | 90.0% |

**Same wall clock, more CPU spent to get there.** The i3-10100F is no longer anywhere near saturated at this concurrency — the CPU-side work `num_thread` controls (tokenizing, sampling, and Gemma's host-RAM per-layer embeddings) was never this run's slow part, so giving it a second thread per runner bought nothing and only lit up more cores to do it. Kept `num_thread 1`. This also closes a CPU-upgrade question raised the same day: the host has headroom under the current one-at-a-time load, so a faster CPU (an i7-10700 or i7-11700 were the candidates, both used, $140-$180) would not shorten an analysis today. Revisit only if a future change reintroduces multiple analyses running together.

That is about concurrency, not stickiness. **An earlier note here said the proxy scatters one analysis's ~20 calls across whichever backends are idle. That is no longer true**: the proxy polls each backend's `/api/ps` and prefers a free backend that already holds the requested model warm, so a *sequential* run stays on one card. Concurrent runs still spread, which is the intent.

**Since 2026-09-14 the proxy also routes by conversation (image `ollama-proxy:dashboard`, source `ollama-stack/ollama-pool/proxy/main.py` and `dashboard.html`, gitignored).** A chat request whose messages start with all the messages of an earlier request goes to the card that served that earlier request, if the card is free. llama-server keeps earlier prompts in a RAM cache (8 GiB per card), so the card reads only the new messages: in the test, 21 tokens in 0.1 s against 5,214 tokens in 4.7 s. **Do not loosen the match to a shared system prompt.** Gemma uses sliding-window attention, so llama-server reuses its cache only from a checkpoint near the end of an earlier prompt, and a prompt that shares only its start is read from token 0 (seen in the card logs on 2026-09-12). A busy match never makes a request wait. Before this change, random routing took 5-26% of prompt tokens from the cache, and reading is about 10% of model time, so the gain has a ceiling of about 10%. `AFFINITY=0` turns it off. The older proxy images were deleted on 2026-09-14. **With 4 analyses at once, affinity added almost nothing** (2026-09-14: 25.3% of prompt tokens from cache against 25.6% the day before, 14 hits against 16 busy misses), because 16 analyst conversations share 7 cards and the matching card is usually busy. It helps when fewer analyses run at once. The dashboard at `:11435/` counts hits, busy misses and cached tokens; Ollama reports cached tokens as `usage.prompt_tokens_details.cached_tokens` on `/v1` and `prompt_eval_cached_count` on `/api/chat`.

Benchmarking still has to bypass the proxy, for a different reason: you cannot tell which card served a run. The pool containers' docker-bridge IPs are reachable from the host (`docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' ollama-pool-a`), so pointing `OLLAMA_BASE_URL` at `http://<ip>:11434/v1` pins a run to one card. **And measure one model at a time.** Seven cards are not seven independent measurements — gemma4's E-series keeps its per-layer embeddings in host RAM, so every card competes for the same CPU and memory bandwidth. The same model at the same context measured 43.5, 28.0 and **69.6** tok/s depending only on how busy the rest of the pool was (2026-08-26).

That makes `TRADINGAGENTS_MAX_CONCURRENT_ANALYSES` the knob that decides GPU utilization. With one deployment it should equal the backend count; anything above just queues in the proxy.

**The deployed value is 1 (checked 2026-09-26).** The agent orders one analysis at a time, and one analysis keeps up to four cards busy. The measurements below chose 7 when batch callers ran many analyses at once. If a change brings that load back, the ceiling is now six, the count of Ollama cards. The two-deployment arithmetic that used to live here — a 4/3 split, then a planned 2/5 — is gone with the second deployment.

**Seven concurrent is the right setting, and fourteen buys nothing.** Both were measured on 2026-09-02 with the same fourteen tickers, twice each:

| | 14 at once | **7 at once** |
|---|---|---|
| Wall clock for 14 analyses | 42.8 min | **42.5 min** |
| Median per analysis | 34.0 min | **18.6 min** |
| Tokens per analysis | 129,844 | 129,251 |
| CPU / mean GPU busy | 97% / 65% | 98% / 63% |
| VRAM peak / pool power | 5.52 GiB / 701 W | 5.45 GiB / 700 W |

**Identical throughput, half the latency, identical machine load.** The CPU saturates before the GPUs do — the cards idle around 37% of the time at either setting — because gemma4's E-series keeps its per-layer embeddings in host RAM. Stacking a second analysis onto a card that is already waiting on the CPU does not make that card produce more.

So `TRADINGAGENTS_MAX_CONCURRENT_ANALYSES=7` was chosen then, and `_MAX_WATCHLIST = 30` on the 3.05 min/analysis throughput against the 120-minute window.

**Two failures in 28, both at 14 concurrent, and neither was capacity.** The model asked for an indicator that does not exist — `macd_histogram`, `boll_upper` — and the vendor router raised rather than telling the model the valid names. Since 2026-09-02 the error goes back to the model instead.

**Overshooting the sum costs latency, not failures, and an earlier note here was wrong about why.** It said `WAIT_TIMEOUT=600` is ten minutes against an eight-minute analysis, so a third wave of queued work would start timing out. That misreads the proxy: it holds a backend for **one LLM call**, releasing it in the response streamer's `finally`, not for a whole analysis. A call is a minute or two, so the timeout has enormous margin. Verified 2026-08-27 — ten concurrent requests against seven backends all succeeded, the slowest in 38.4 seconds. Match the sum to the backend count to keep the cards busy, not to avoid an error that cannot happen.

**A card holds one model at a time.** Loading `gemma4-e4b-qat-128k` on a backend already holding `gemma4-e2b-96k` evicts the smaller model, even though 1.9 GiB and 3.7 GiB would both fit in the 8 GiB. So two deployments running different models will occasionally reload one on a card the other just used. The warm-model preference keeps a sequential run on its own card, so this only bites at wave boundaries, and it costs one load — 6 to 40 seconds against a 7-to-17-minute analysis. Not worth engineering around.

Every multi-ticker caller must go through `analysis.run_analyses()`, which dispatches with `asyncio.gather` and lets the shared semaphore do the bounding. A `for ticker in …: await run_analysis_and_notify(ticker)` loop looks correct and silently runs the whole sweep one analysis at a time — that bug shipped in the daily sweep, the watchdog triggers, and the earnings check.

To check which backends served a run: `docker logs --since 24h ollama-pool-a | grep "starting runner"` (an idle backend has no recent entries), or `curl localhost:11435/healthz`.

## llama-server flags through Ollama (measured 2026-10-03, Ollama 0.35.1)

**Ollama runs upstream `llama-server` and gives it the container's environment** (`cmd.Env = os.Environ()` in Ollama's `llm/llama_server.go`). `llama-server` reads each flag that is not on its command line from a `LLAMA_ARG_*` env var. So a flag that Ollama does not pass can be set in the pool's compose env. A flag that Ollama passes (`-c`, `-np`, `-b`/`-ub`, `--cache-type-*`, `-t`, `--host`, `--port`) cannot be changed this way, because the command line wins. `--poll` and `-tb` have no env var.

**`LLAMA_ARG_ENDPOINT_METRICS=1` turns on `/metrics`.** The proxy reads it through a Docker exec in each container, because `llama-server` listens on a random port on `127.0.0.1`. See `ollama-stack/ollama-pool/proxy/README.md` and the scoped exec rules in `ollama-stack/ollama-pool/socket-proxy/haproxy.cfg.template`.

**Since 2026-10-04 the dashboard takes each request's tokens and times from `llama-server`'s timing block in the container log**, through a scoped `GET /containers/ollama-pool-[a-g]/logs` rule in the same template. The response body is the fallback. This makes `/v1` write speeds exact, where before they were estimates. The routing facts (queue wait, affinity, which card) still come only from the proxy, because only the proxy knows them.

None of the speed flags helped. These were measured on riser card `-f` (PCI `0000:18:00.0`, named card c in `llama-pool` since 2026-10-06), alone, with a synthetic 8,850-token analysis prompt with a new prefix on each run, Gemma's own sampling and `num_predict 512`. Each config had one warm-up and three runs:

| Config | Read tok/s | Write tok/s | Drafts accepted |
|---|---|---|---|
| Baseline | 943.5 | 31.56 | - |
| `LLAMA_ARG_SPEC_TYPE=ngram-simple` | 939.5 | 31.33 | 4.2% |
| `LLAMA_ARG_SPEC_TYPE=ngram-map-k` | 939.2 | 31.42 | 2.1% |
| `LLAMA_ARG_SPEC_TYPE=ngram-mod` | 938.6 | 31.12 | 6.0% |
| `LLAMA_ARG_MMPROJ_OFFLOAD=0` | 938.8 | 31.50 | - |
| `MMPROJ_OFFLOAD=0` and `LLAMA_ARG_OVERRIDE_TENSOR=per_layer_token_embd.weight=ROCm0` | 1,003.9 | **22.03** | - |

- **N-gram speculation finds almost nothing to copy.** A report quotes single numbers, not runs of 12 or more tokens, so the drafts are few and most are rejected.
- **Moving the per-layer embeddings to the GPU makes writing 19-30% slower.** It reads 6% faster and moves 2.2 GiB from host RAM to VRAM (`CPU_Mapped` 2,730 to 525 MiB). Writing is about 90% of model time, so the total is a loss. This was one card alone. When all seven cards compete for host RAM, the result can differ, but the write loss is large enough that the test is not worth doing again unless the pool goes back to many analyses at once.
- **Moving the vision projector to the CPU costs nothing** and gives back about 1.15 GiB of VRAM on each card. The analysts send no images. Use it if a model ever needs the VRAM.
- **Not tested:** `LLAMA_ARG_SWA_FULL` and `LLAMA_ARG_CTX_CHECKPOINTS`. They can help only a follow-up turn that shares a prompt with an earlier one, so they need a multi-turn probe. The ceiling is about 10% (see the affinity notes above).

## Custom context builds (`ollama/`)

`ollama/*.Modelfile` plus `ollama/build.sh`. **An earlier note here said a model has to be installed on every backend or the proxy sends some analyses to one that lacks it. That was wrong**: the pool shares one models directory, so building once reaches all of them. The script builds on the first backend and then checks the rest can see it, which is cheap and catches the day somebody gives a container its own volume. See `ollama/README.md` for the numbers.

The non-obvious part, measured 2026-08-11 on the 8 GiB cards: **the compute graph is what limits context, not the KV cache.** The cache is already `q4_0` (flash attention is on) and costs 2.6 GiB at 96k, while the compute graph at the default `num_batch 512` wants 5.1 GiB and pushes 40% of a llama-3.2-3B's layers onto the CPU. Dropping `num_batch` to 64 fits the full 128k in 6.6 GiB, entirely on the GPU, for 16% slower prefill (411 vs 490 tok/s). So a new build needs `PARAMETER num_batch`, not just `num_ctx`, and `ollama ps` must read `100% GPU` — any CPU split costs far more than the batch size ever will.

Gemma is the exception that made this confusing: `gemma4-e2b-96k` runs at 96k with the default batch because sliding-window attention keeps its compute graph small. Don't reason from it to a Llama of the same size.

## Laya on these cards (tried 2026-09-26, shelved 2026-09-27)

**Nothing runs Laya now.** The grading it would do was measured and shelved on 2026-09-27; see `news_sources.py` and `fetch_audit.py` for the results. `LAYA_URL` is unset in both deployments, and the code treats Laya as optional. What follows is how to run it again on these cards.

It ran as the `laya` service in the `ollama-pool` stack, on the riser card at PCI `0000:18:00.0` (`renderD133`), in place of `ollama-pool-f`. For one evening a separate `laya-pool` stack ran one Laya per card behind an nginx `least_conn` balancer on port 8300, because one `laya-serve` answers one request at a time. The direct cards stay with the analysts, because an analysis runs for minutes and a Laya request runs for milliseconds. On the direct card of `ollama-pool-c`, Laya loaded in 12.4 s against 18.0 s on the riser, and a request took 76 ms against 81 ms. The riser costs load time, not request time.

| | CPU (`laya:local`) | GPU (`laya:rocm`, riser card) |
|---|---|---|
| Request, median | 474 ms | 81 ms |
| Model load | 7.7 s | 18.0 s |
| First request after start | 0.45 s | 2.5 s in a test, 23.9 s over HTTP |

**Do not share Laya's card with Ollama.** Laya keeps 1.9 GiB when idle and 4.5 GiB after requests, because PyTorch keeps the memory it used. `gemma4-e4b-qat-128k` needs about 6.6 GiB of the 8 GiB, and Ollama moves layers to the CPU when it cannot get them.

Upstream Laya supports only NVIDIA. Three things make the image work on these cards:

- **Build with `TORCH_INDEX=rocm7.2`.** The wheel is `torch 2.14.0+rocm7.2`, and its architecture list has `gfx1030`. The image is 23 GB.
- **Patch `docker/check_torch.py`.** It knows only `cpu` and `cu*`. A ROCm build has `torch.version.cuda = None`, so the check reads it as `cpu` and stops the build. Read `torch.version.hip` first.
- **Install `libatomic1` in both Dockerfile stages.** The ROCm wheel needs it, and `python:3.11-slim` does not have it. Without it, `import torch` fails in the build.

The compose service needs these, in addition to `HSA_OVERRIDE_GFX_VERSION=10.3.0` and `HIP_VISIBLE_DEVICES=0` as in the pool:

- **Map the render node under its own name**: `/dev/dri/renderD133:/dev/dri/renderD133`. The KFD topology in `/sys` gives the host's render minor. If the container sees the node as `renderD128`, ROCm finds no GPU. Laya then falls back to the CPU and prints only a warning, and `/health` shows `"device":"cpu"`. The `/dev/dri/by-path/` name does not work in `--device`, because Docker splits the value at each colon of the PCI address.
- **`group_add: ["44", "993"]`** (video, render). The image runs as uid 10001.
- **Use a named volume for `/home/laya/.cache`**, not a bind mount. Docker makes a bind mount folder that root owns, and uid 10001 cannot write the model into it. A named volume copies the owner from the image.
- **`restart: unless-stopped`.** On the first deploy, a DNS failure at startup stopped the container, and it stayed down.

After a reboot, check that `readlink -f /dev/dri/by-path/pci-0000:18:00.0-render` still gives `renderD133`. The `card*` numbers have changed across reboots before: today `card1` is `renderD128`. `curl localhost:8300/health` must show `"device":"cuda"`.
