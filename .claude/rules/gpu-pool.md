---
paths:
  - "ollama/**"
  - "backend/services/analysis.py"
---

## The pool is its own project

**The GPU pool moved to `~/projects/ollama-stack` on 2026-10-10.** Its `CLAUDE.md` has the card topology, the proxy and its routing, the `llama-pool` image and its shims, the `llama-server` flags, the `num_thread` measurements, and how Laya ran on these cards. The long GPU investigations are in its `docs/`. This file keeps only what the app decides: how many analyses run at once, how the app dispatches them, and the Modelfiles in `ollama/`.

The app reaches the pool at host port 11435 (`OLLAMA_BASE_URL`). `curl -s localhost:11435/v1/models` shows the one model the `llama-pool` serves.

## How many analyses at once

**One analysis uses up to four GPUs, for about three minutes (since 2026-09-13).** The four analysts run at the same time, each with its own request in flight. The Bull/Bear debate, the trader and the risk debate then run in turn, one request at a time. Most of the extra GPU use still comes from running *several analyses at once*.

Measured 2026-09-13, outside the app: one NVDA analysis alone took 10.4 to 11.8 min, against 16.2 min for the old sequential graph, with the analyst stage at 3.0 to 3.4 min against 8.6. Four analyses started together all finished in 14.9 min, with 7 cards busy and the host CPU at 97% mean. **The CPU limits the analyst stage**, for the same reason as the table below. The table was measured before this change and has not been measured again.

`TRADINGAGENTS_MAX_CONCURRENT_ANALYSES` is the knob that decides GPU utilization. With one deployment it should equal the backend count; anything above just queues in the proxy.

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

## Benchmarking a model for the app

**A benchmark must bypass the proxy**, because you cannot tell which card served a run. The harness is in `~/projects/ollama-stack/bench/`. The pool containers' docker-bridge IPs are reachable from the host (`docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' ollama-pool-a`), so pointing `OLLAMA_BASE_URL` at `http://<ip>:11434/v1` pins a run to one card. **And measure one model at a time.** Seven cards are not seven independent measurements — gemma4's E-series keeps its per-layer embeddings in host RAM, so every card competes for the same CPU and memory bandwidth. The same model at the same context measured 43.5, 28.0 and **69.6** tok/s depending only on how busy the rest of the pool was (2026-08-26).

## Custom context builds (`ollama/`)

`ollama/*.Modelfile` plus `ollama/build.sh`. **An earlier note here said a model has to be installed on every backend or the proxy sends some analyses to one that lacks it. That was wrong**: the pool shares one models directory, so building once reaches all of them. The script builds on the first backend and then checks the rest can see it, which is cheap and catches the day somebody gives a container its own volume. See `ollama/README.md` for the numbers.

The non-obvious part, measured 2026-08-11 on the 8 GiB cards: **the compute graph is what limits context, not the KV cache.** The cache is already `q4_0` (flash attention is on) and costs 2.6 GiB at 96k, while the compute graph at the default `num_batch 512` wants 5.1 GiB and pushes 40% of a llama-3.2-3B's layers onto the CPU. Dropping `num_batch` to 64 fits the full 128k in 6.6 GiB, entirely on the GPU, for 16% slower prefill (411 vs 490 tok/s). So a new build needs `PARAMETER num_batch`, not just `num_ctx`, and `ollama ps` must read `100% GPU` — any CPU split costs far more than the batch size ever will.

Gemma is the exception that made this confusing: `gemma4-e2b-96k` runs at 96k with the default batch because sliding-window attention keeps its compute graph small. Don't reason from it to a Llama of the same size.
