---
paths:
  - "ollama/**"
  - "docs/gpu-*.md"
  - "backend/services/analysis.py"
---

<!-- Moved from CLAUDE.md on 2026-09-13. This file loads when Claude reads a file that matches paths. -->

## Ollama pool topology (deployed ≠ the repo template)

`dockge/ollama-pool.compose.yaml` describes **two** backends (`ollama-pool`, `ollama-pool-b`) behind an nginx round-robin named `ollama-lb`. That is stale. What actually runs (verified 2026-08-06):

- **Seven** backends: `ollama-pool-a` … `ollama-pool-g`, one AMD card each (three added 2026-08-26). The cards are **RX 6600 (gfx1032, 8 GiB)**, not gfx1030 as an earlier note here claimed. They report as gfx1030 only because every pool container sets `HSA_OVERRIDE_GFX_VERSION=10.3.0` — ROCm's support for gfx1032 is unofficial and the override is what makes them work. Anyone adding cards who trusts the old note would omit it and spend a day on it.

  Each container is pinned to one card at the device level — `/dev/dri/card0` plus `renderD128` for `-a`, `card1`/`renderD129` for `-b`, and so on — which is why `HIP_VISIBLE_DEVICES=0` is correct in every one: it means "the only card I can see", not "card zero". `-e`…`-g` follow the same pattern on `card4`…`card6` with `renderD132`…`renderD134`.

  **Every pool container bind-mounts the same host directory** at `/root/.ollama/models` (`/opt/stacks/ollama-gpus/ollama/models`), so a model pulled or built through any one of them is immediately visible to all. There is no per-backend model state to keep in sync, and adding a card needs no model work at all.
- `ollama-proxy` (image `ollama-proxy:local`) replaced nginx. It is a small FastAPI app: least-active-connections routing, `CONCURRENCY_PER_BACKEND=1`, `WAIT_TIMEOUT=600` (queues rather than 503s), and a `/healthz` endpoint reporting per-backend health and active count. Still on host port 11435.

**One analysis occupies exactly one GPU.** TradingAgents' graph is internally sequential — analysts run one after another, then the debate, then the trader — so a single `propagate()` never has more than one LLM request in flight. Extra GPUs are only used by running *several analyses at once*.

That is about concurrency, not stickiness. **An earlier note here said the proxy scatters one analysis's ~20 calls across whichever backends are idle. That is no longer true**: the proxy polls each backend's `/api/ps` and prefers a free backend that already holds the requested model warm, so a *sequential* run stays on one card. Concurrent runs still spread, which is the intent.

Benchmarking still has to bypass the proxy, for a different reason: you cannot tell which card served a run. The pool containers' docker-bridge IPs are reachable from the host (`docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' ollama-pool-a`), so pointing `OLLAMA_BASE_URL` at `http://<ip>:11434/v1` pins a run to one card. **And measure one model at a time.** Seven cards are not seven independent measurements — gemma4's E-series keeps its per-layer embeddings in host RAM, so every card competes for the same CPU and memory bandwidth. The same model at the same context measured 43.5, 28.0 and **69.6** tok/s depending only on how busy the rest of the pool was (2026-08-26).

That makes `TRADINGAGENTS_MAX_CONCURRENT_ANALYSES` the knob that decides GPU utilization. With one deployment it should equal the backend count; anything above just queues in the proxy.

**One deployment runs, so it gets all seven.** Set `TRADINGAGENTS_MAX_CONCURRENT_ANALYSES=7`. The two-deployment arithmetic that used to live here — a 4/3 split, then a planned 2/5 — is gone with the second deployment.

**Seven concurrent is the right setting, and fourteen buys nothing.** Both were measured on 2026-09-02 with the same fourteen tickers, twice each:

| | 14 at once | **7 at once** |
|---|---|---|
| Wall clock for 14 analyses | 42.8 min | **42.5 min** |
| Median per analysis | 34.0 min | **18.6 min** |
| Tokens per analysis | 129,844 | 129,251 |
| CPU / mean GPU busy | 97% / 65% | 98% / 63% |
| VRAM peak / pool power | 5.52 GiB / 701 W | 5.45 GiB / 700 W |

**Identical throughput, half the latency, identical machine load.** The CPU saturates before the GPUs do — the cards idle around 37% of the time at either setting — because gemma4's E-series keeps its per-layer embeddings in host RAM. Stacking a second analysis onto a card that is already waiting on the CPU does not make that card produce more.

So `TRADINGAGENTS_MAX_CONCURRENT_ANALYSES=7`, and `_MAX_WATCHLIST = 30` on the 3.05 min/analysis throughput against the 120-minute window.

**Two failures in 28, both at 14 concurrent, and neither was capacity.** The model asked for an indicator that does not exist — `macd_histogram`, `boll_upper` — and the vendor router raised rather than telling the model the valid names. Since 2026-09-02 the error goes back to the model instead.

**Overshooting the sum costs latency, not failures, and an earlier note here was wrong about why.** It said `WAIT_TIMEOUT=600` is ten minutes against an eight-minute analysis, so a third wave of queued work would start timing out. That misreads the proxy: it holds a backend for **one LLM call**, releasing it in the response streamer's `finally`, not for a whole analysis. A call is a minute or two, so the timeout has enormous margin. Verified 2026-08-27 — ten concurrent requests against seven backends all succeeded, the slowest in 38.4 seconds. Match the sum to the backend count to keep the cards busy, not to avoid an error that cannot happen.

**A card holds one model at a time.** Loading `gemma4-e4b-qat-128k` on a backend already holding `gemma4-e2b-96k` evicts the smaller model, even though 1.9 GiB and 3.7 GiB would both fit in the 8 GiB. So two deployments running different models will occasionally reload one on a card the other just used. The warm-model preference keeps a sequential run on its own card, so this only bites at wave boundaries, and it costs one load — 6 to 40 seconds against a 7-to-17-minute analysis. Not worth engineering around.

Every multi-ticker caller must go through `analysis.run_analyses()`, which dispatches with `asyncio.gather` and lets the shared semaphore do the bounding. A `for ticker in …: await run_analysis_and_notify(ticker)` loop looks correct and silently pins the whole sweep to one GPU — that bug shipped in the daily sweep, the watchdog triggers, and the earnings check.

To check which backends served a run: `docker logs --since 24h ollama-pool-a | grep "starting runner"` (an idle backend has no recent entries), or `curl localhost:11435/healthz`.

## Custom context builds (`ollama/`)

`ollama/*.Modelfile` plus `ollama/build.sh`. **An earlier note here said a model has to be installed on every backend or the proxy sends some analyses to one that lacks it. That was wrong**: the pool shares one models directory, so building once reaches all of them. The script builds on the first backend and then checks the rest can see it, which is cheap and catches the day somebody gives a container its own volume. See `ollama/README.md` for the numbers.

The non-obvious part, measured 2026-08-11 on the 8 GiB cards: **the compute graph is what limits context, not the KV cache.** The cache is already `q4_0` (flash attention is on) and costs 2.6 GiB at 96k, while the compute graph at the default `num_batch 512` wants 5.1 GiB and pushes 40% of a llama-3.2-3B's layers onto the CPU. Dropping `num_batch` to 64 fits the full 128k in 6.6 GiB, entirely on the GPU, for 16% slower prefill (411 vs 490 tok/s). So a new build needs `PARAMETER num_batch`, not just `num_ctx`, and `ollama ps` must read `100% GPU` — any CPU split costs far more than the batch size ever will.

Gemma is the exception that made this confusing: `gemma4-e2b-96k` runs at 96k with the default batch because sliding-window attention keeps its compute graph small. Don't reason from it to a Llama of the same size.
