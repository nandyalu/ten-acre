# Fourteen analyses at once: what the seven-card pool actually does

Measured 2026-09-02. Two rounds of fourteen concurrent analyses — two on each of the seven RX 6600 cards — on `gemma4-e4b-qat-128k`, the model the experiment runs.

The question: **can a card carry two analyses, and what does that buy?**

## Bottom line

**Yes, and it roughly quadruples throughput.** 26 of 28 analyses finished, and neither failure was the hardware.

**The CPU is the bottleneck, not the GPUs.** The cards averaged 36–61% busy while the host CPU sat at 99.8% median. Adding cards would not help this machine; the constraint is that gemma4's E-series keeps its per-layer embeddings in host RAM, so every analysis competes for the same CPU and memory bandwidth.

**There is room on the cards, and that is the surprise.** Two analyses used 5.5 GiB of each card's 8.0 GiB. VRAM was never close to the limit.

**32 GB of RAM is what makes this possible.** The run peaked at 20.7 GB with 10.6 GB still free. On the old 16 GB it would not have fit.

| Concurrency | Per analysis | Throughput | Source |
|---|---|---|---|
| 1 | 13.2 min | 13.2 min each | earlier measurement |
| 2 | 17.4 min | 8.7 min each | earlier measurement |
| **14** | **33.6 min** | **3.2 min each** | this run |

Each analysis takes **2.5x longer** than it would alone. Fourteen run at once. The net is **4.1x more analyses per hour**.

Read the two columns separately. Latency got worse and throughput got much better, and which one matters depends on whether you are waiting for one answer or filling a two-hour window.

## What was run, and why it bypassed the proxy

Fourteen tickers, two per card, pinned to each backend's own docker-bridge IP. Two rounds on the same fourteen names.

**Pinned rather than through `ollama-proxy`**, for two reasons. The proxy runs `CONCURRENCY_PER_BACKEND=1`, so fourteen requests through it would run seven and queue seven — that measures the queue, not the card. And a proxied run cannot be attributed: a slow card is invisible.

**One process rather than fourteen.** The real sweep runs as asyncio tasks in one process. Fourteen separate Python processes would each carry their own copy of the app, and the RAM figure would then describe the harness instead of the deployment. `config["backend_url"]` is a per-graph key, so one process can still pin every task to a different card.

Nothing was recorded. The harness calls `graph.propagate` directly rather than `analysis.propagate_ticker`, so no signal was stored and no research was charged.

**One thing to check before repeating this: ollama must serve two requests per backend in parallel.** It does here, unconfigured — two concurrent requests to one backend completed in 5.9s wall against 8.9s of summed work. If a future version queues them instead, this whole measurement changes and `OLLAMA_NUM_PARALLEL` is the knob.

## Per round

| | Round 1 | Round 2 |
|---|---|---|
| Wall clock | 42.8 min | 40.6 min |
| Succeeded | 14/14 | 12/14 |
| Fastest analysis | 16.5 min | 19.6 min |
| Median | 34.0 min | 33.4 min |
| Slowest | 42.8 min | 40.6 min |
| Analysis-minutes done | 466 | 380 |
| Overlap achieved | 10.9x | 9.4x |

"Overlap" is analysis-minutes divided by wall clock. The theoretical ceiling is 14x. Reaching 10.9x means the machine kept about 78% of the fourteen slots genuinely busy; the rest is the tail, where a few long analyses run while the others have finished.

**The spread is wide and it is not the cards.** The fastest analysis took 16.5 minutes and the slowest 42.8, on the same hardware in the same round. An analysis is 16–23 LLM calls whose lengths depend on what the tools return, so a name with more news is simply more work.

## What one analysis costs

Across all 26 that finished:

| | Mean | Range |
|---|---|---|
| Wall clock | 33.6 min | 16.5 – 42.8 min |
| LLM calls | 19.4 | 16 – 23 |
| Prompt tokens | 111,068 | 74,216 – 143,643 |
| Completion tokens | 21,810 | 17,675 – 24,951 |
| Combined | 132,878 | — |
| Tokens per second | 71 | 51.7 – 112.4 |

**Completion share is 16.4%**, against gemma4's healthy 14–17%. This is the number that catches a model which never fetched its data and answered anyway: the four models rejected in August ran at 34–35% completion, having read far less and talked over it. Nothing like that happened here.

Total for the run: **3.45 million tokens** in 83 minutes of wall clock.

### Per card

| Card | Runs | Median | Mean tok/s |
|---|---|---|---|
| `ollama-pool-a` | 4 | 34.1 min | 67.5 |
| `ollama-pool-b` | 4 | 32.0 min | 71.4 |
| `ollama-pool-c` | 4 | 33.0 min | 68.3 |
| `ollama-pool-d` | 4 | 39.9 min | 61.5 |
| `ollama-pool-e` | 4 | 33.1 min | 58.9 |
| `ollama-pool-f` | 3 | 25.4 min | 88.3 |
| `ollama-pool-g` | 3 | 19.6 min | 86.6 |

**Do not read `-f` and `-g` as faster cards.** They each lost an analysis to the failure below, so their remaining runs finished into a less contended machine. That is the same trap the earlier benchmarking notes warn about: a card's measured speed here is mostly a function of how busy everything else was.

## The machine, while it ran

Sampled every two seconds for 91 minutes.

| | |
|---|---|
| CPU | **99.8% median**, 75.7% mean, 100% max |
| RAM used | 20.7 GB peak, of 31 GB |
| RAM still free at the worst moment | 10.6 GB |
| Peak pool power | **701 W** across seven cards |

| Card | Busy mean | VRAM peak | Power peak | Junction temp peak |
|---|---|---|---|---|
| card0 | 49% | 5.51 GiB | 101 W | 90 °C |
| card1 | 47% | 5.49 GiB | 100 W | 95 °C |
| card2 | 48% | 5.50 GiB | 100 W | **97 °C** |
| card3 | 61% | 5.53 GiB | 100 W | 79 °C |
| card4 | 52% | 5.50 GiB | 100 W | 93 °C |
| card5 | 46% | 5.43 GiB | 100 W | 94 °C |
| card6 | 36% | 5.41 GiB | 100 W | 85 °C |

**The CPU line and the GPU line are the finding.** The host CPU is pinned at ~100% for the whole run while no card averages above 61% busy. The cards are waiting on the CPU, not the other way round. Buying an eighth card would change nothing.

**Two cautions the numbers raise.**

*Heat.* Junction temperatures reached 97 °C on card2 and 93–95 °C on three others. That is inside the RX 6600's limit but it is a sustained hour at the top of the range, in a case with seven cards. Worth watching before making a 40-minute fourteen-way sweep a daily habit.

*Power.* 701 W from the cards alone, sustained. Add the host and this is a real draw for a home server, and it is 40 minutes every morning rather than a burst.

## The two failures, and what they argue for

Both were the same thing, and **neither was the hardware.**

```
RuntimeError: No available vendor for 'get_indicators'
```

The model asked for a technical indicator by a name that does not exist. Across the run it invented five: `macd_histogram`, `macd_hist`, `boll_upper`, `boll_lower`, and a malformed `macd`. The vendor rejected each with a message that **lists every valid name**:

```
Indicator `macd_histogram` is not supported. Please choose from:
['close_50_sma', 'close_200_sma', 'close_10_ema', 'macd', 'macds',
 'macdh', 'rsi', 'boll', 'boll_ub', 'boll_lb', 'atr', 'vwma', 'mfi']
```

Three of the five recovered. Two propagated and **killed a 40-minute analysis outright.**

**The fix is not a better model.** The correct name was in the error message the whole time. `macd_histogram` is `macdh`; `boll_upper` is `boll_ub`. A model shown that message would retry and succeed. Instead the exception escaped and forty minutes of work was discarded.

This is the strongest possible argument for feeding tool errors back to the model rather than raising them. It costs one extra call and saves an entire analysis.

### The other noise worth knowing about

**59 Reddit `429 Too Many Requests`**, across `r/stocks` and `r/investing`. Expected — the sentiment analyst read Reddit's public RSS with no API key, and at the time a throttled subreddit read as "no posts found" rather than failing. Since 2026-09-13 it reads as unavailable. But 59 in 83 minutes is far more than a normal sweep produces, and at fourteen concurrent the sentiment analyst is getting materially less data than it would at three. **This is a real cost of high concurrency that has nothing to do with the GPUs.**

**79 `macro_data unavailable`**, because `FRED_API_KEY` is unset on this host. Not a concurrency problem; it means the news analyst inferred rates and the yield curve from headlines instead of reading the series.

## The variance nobody should skip

The same fourteen tickers ran twice, on the same day, minutes apart.

**Two of twelve comparable analyses reached the same decision.**

| Ticker | Round 1 | Round 2 |
|---|---|---|
| AAPL | Overweight | Underweight |
| ADBE | Underweight | Sell |
| AMD | Overweight | Sell |
| AMZN | Sell | Sell |
| CRM | Underweight | Sell |
| GOOG | Sell | Sell |
| INTC | Sell | Underweight |
| MSFT | Underweight | **Buy** |
| NFLX | Overweight | Buy |
| NVDA | Buy | Overweight |
| ORCL | Hold | **Buy** |
| QCOM | Underweight | **Overweight** |

Three flipped from bearish to bullish outright.

**This is temperature 1, which is Gemma's own recommended setting and what the experiment has always run at.** It is not new, it is not a defect, and it is not a reason to tune sampling down — an earlier attempt to do exactly that treated a symptom of a smaller model's capability as a sampling problem and was reverted.

What it means is simple and worth stating plainly on the site: **one analysis is one sample.** A single signal is not evidence about a stock, and the scorecard's by-model breakdown over many resolved signals is the only honest way to compare anything.

It also sets a floor on what any prompt change can be credited with. A change that moves one morning's decision has moved nothing detectable.

## What to set

**`TRADINGAGENTS_MAX_CONCURRENT_ANALYSES=14`** is safe on this hardware. VRAM, RAM and thermals all have margin, and 26 of 28 succeeded with the two failures unrelated to load.

**`_MAX_WATCHLIST` can rise well above 12.** The sweep runs at 11:00 UTC and has until `earnings_check` at 13:00 — 120 minutes. At 3.2 minutes per analysis sustained, that is about 37 analyses. Allowing for the tail, **30 is a defensible number** against the 12 that ships today.

**Do not just set it to 37.** Two reasons. The measured rate comes from a batch of exactly fourteen dispatched at once; a longer queue behaves slightly differently as analyses start and finish unevenly. And a sweep that overruns into 13:00 collides with `earnings_check`, which puts its own analyses on the same pool. Raise it to 30, watch one week of actual sweep durations, then decide.

## What this run does not tell you

**Whether fourteen beats seven.** This measured 1 vs 2 vs 14, and there is no seven-concurrent number. Since the CPU is already pinned at 100% at fourteen, seven may deliver most of the same throughput at roughly half the per-analysis latency — which would be strictly better. **This is the obvious next measurement and it is cheap: one round of seven, about 25 minutes.**

**Whether the heat is sustainable.** Two 40-minute rounds is not a month of daily sweeps in a warm room.

**Anything about a different model.** gemma4's E-series is unusual in keeping per-layer embeddings in host RAM, and that property is the entire reason the CPU is the bottleneck here. A conventional model of the same size would load these numbers differently and the conclusion could invert.

## Reproducing it

The harness is in `ollama-stack/bench/` — gitignored, because it hardcodes seven container names, their bridge IPs and 8 GiB cards. `run14.py` does the run, `sampler.py` records the machine, `report.py` turns both into the tables above.

The three traps that produced wrong answers in earlier benchmarking all still apply, and two of them apply here:

- **Never measure several models at once, one per card.** The cards are not independent; they share a CPU.
- **Never read a single card's speed from a contended run.** See `-f` and `-g` above.
- **Never measure prefill on a short prompt**, and never repeat an identical one — the prompt cache answers the second in 0.03 s.
