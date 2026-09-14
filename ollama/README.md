# Ollama Modelfiles

Custom context builds for the models the analysis runs on, plus `build.sh` to install one on every backend in the pool.

```sh
./build.sh kotakneo-128k.Modelfile
```

One build reaches every backend. All seven pool containers bind-mount the same host directory at `/root/.ollama/models`, so a model built through any one of them is immediately visible to all. `build.sh` builds on the first backend and then checks the rest can see it — cheap, and it catches the day somebody gives a container its own volume.

Once built, the model appears in the settings page's model dropdown and in `/model` on its own — both read the endpoint's live list.

## Why these builds exist

A stock model loads at whatever context its Modelfile asks for, and the pool's backends default to 8192 (`OLLAMA_CONTEXT_LENGTH`). An analysis carries the whole tool-call history, so at 8k the history gets truncated out from under the reasoning loop: the loop stops terminating and the run either dies with a `GraphRecursionError` or, worse, invents the tool output it can no longer see. Both 3B trading models did the latter — they answered the market analyst by writing Python that looked like the data-fetching tool instead of calling it.

## What actually limits the context (measured 2026-08-11)

The cards are 8 GiB (RX 6600, **gfx1032** — they report as gfx1030 only because every pool container sets `HSA_OVERRIDE_GFX_VERSION=10.3.0`), of which ollama will use about 7.5 GiB.

The KV cache is not the constraint. It is already quantized (`OLLAMA_KV_CACHE_TYPE=q4_0` with flash attention on) and costs only 2.6 GiB at 96k. **The compute graph is the constraint, and it scales with `num_batch`:**

| Context | `num_batch` | Total | Placement |
|---|---|---|---|
| 128k | 512 (default) | 13 GB | 40% CPU / 60% GPU |
| 96k | 512 (default) | 10 GB | 23% CPU / 77% GPU |
| 128k | 128 | 8.0 GB | 4% CPU / 96% GPU |
| 96k | 256 | 7.4 GB | 100% GPU |
| 112k | 128 | 6.8 GB | 100% GPU |
| **128k** | **64** | **6.6 GB** | **100% GPU** |

Any CPU split is a failure, not a compromise. A single layer on the CPU costs far more than the batch size ever will.

**`num_batch` is not always the answer.** That table is a llama-3.2-3B, where the compute graph dominates. `phi4-mini` (3.8B, phi3) behaves differently: at 128k it needs 8.9 GiB and spills 12% to the CPU, and halving `num_batch` from 64 to 32 changes nothing at all, because there the KV cache is what does not fit. Its ceiling is 96k at 7.3 GiB — the same context gemma4-e2b-96k runs at, which makes the two directly comparable.

| Model | Largest that fits fully on an 8 GiB card | What limits it |
|---|---|---|
| llama-3.2-3B builds | 128k, `num_batch 64` | compute graph |
| `phi4-mini` | 96k, `num_batch 64` | KV cache |
| `gemma4-e2b-96k` | 96k, default batch | sliding-window attention keeps both small |

So the first thing a new build needs is a fit probe, not an assumption about which knob matters.

The small batch is cheap. Prefill on a 35,000-token prompt:

| Config | Prefill |
|---|---|
| 96k / batch 256 | 490 tok/s |
| 112k / batch 128 | 458 tok/s |
| 128k / batch 64 | 411 tok/s |

Full context costs 16% of prefill speed and *saves* 0.8 GB of VRAM against the 96k build, so both files here use 128k with `num_batch 64`.

## Checking a build

`ollama ps` after a load is the whole test:

```sh
docker exec ollama-pool-a sh -c "ollama run kotakneo-128k hi >/dev/null 2>&1; ollama ps"
```

`PROCESSOR` must read `100% GPU`. `build.sh` prints this for you.

## Benchmarking a build

The scripts that produced every number in this file live in `ollama-stack/bench/`, which is **local to this machine and not tracked** — they hardcode seven container names, their docker-bridge IPs, and 8 GiB cards, so they would not run anywhere else. Four steps, cheapest first: `fit.py` (placement), `prefill.py` (read speed), `menu_choice.py` (does it use the candidate menu), `full_analysis.py` (tool-calling and real prices). That directory's README has the commands.

The rules below are not machine-specific, which is why they are here rather than there. **Each one produced a wrong answer on this project before it was written down.**

**Do not measure through the proxy.** It routes each request on its own, preferring a backend that already holds the model warm. That is right for production and useless for measurement: you cannot tell which card served a run, and a model can land beside another and run mostly on the CPU.

Pin the run to one card instead. The pool containers are reachable from the host by their docker-bridge IPs:

```sh
ip=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' ollama-pool-c)
OLLAMA_BASE_URL="http://$ip:11434/v1" ...
```

**Do not measure several models at once, one per card.** Seven cards look like seven independent measurements. They are not, for gemma4: the E-series keeps its per-layer embeddings mapped in host RAM — 4.8 GiB of e2b's 7.2 GiB lives there — so every card's generation reads across the PCIe bus and competes for the same CPU and memory bandwidth. Measured 2026-08-26, `gemma4-e2b-96k` at 96k gave 43.5 tok/s with the pool full, 28.0 tok/s in a different full wave, and **69.6 tok/s alone**. The first two are not errors and the spread between them is not noise; they simply answer a different question.

So: use the full pool to find out whether a model loads at all, and at what context. Then re-measure each finalist alone for the numbers you will decide on. A serial pass over five configurations takes a few minutes and is the only kind that compares.

The parallel pass is still worth keeping, because it is what a nine-ticker sweep actually looks like. Just label it as such.

**Do not measure prefill on a short prompt, and never repeat one.** These two go together and both inflate a figure that looks fine. On a 30-token prompt, `prompt_eval_rate` is almost entirely fixed per-request overhead: measured that way `gemma4:e4b-it-qat` came out at 167 tok/s against plain `gemma4:e4b`'s 372, the exact reverse of the truth, which is 1,122 against 861. And Ollama serves a repeated prompt from cache in about 0.03 seconds, which reads as 219,000 tok/s. Use several thousand tokens, with a unique prefix on every run, and take a median of three — real measurements land within 1% of each other.

**Run every behavioural test more than once.** Gemma's recommended temperature is 1, so a single run is one sample. Two runs is what corrected the conclusion about QAT's structured-output failure described below; one run had pointed the wrong way.

## How big a gemma4 fits on an 8 GiB card (measured 2026-08-26)

Every figure below was taken with the model alone on one card, reading ollama's own `eval_count` and `prompt_eval_count` rather than timing a wall clock. Generation rates were taken at temperature 0; prefill rates at each model's own sampling, which does not affect prefill.

| Model | Raw params | Prefill tok/s | Gen tok/s | Placement | Largest context that fits fully |
|---|---|---|---|---|---|
| `gemma4-e2b-96k` | 5.1B | **1,585** | **69.6** | 100% GPU, 36/36 | 128k (1.9 GiB at 96k) |
| `gemma4:e4b-it-qat` | 8.0B | 1,122 | 43.7 | 100% GPU, 43/43 | **128k, 3.7 GiB** |
| `gemma4:e4b` | 8.0B | 861 | 43.0 | 100% GPU, 43/43 | 128k, 3.8 GiB |
| `gemma4:12b-it-qat` | 12B | ~144 | 18.6 | 96% GPU, 47/49 | none — spills at every context |
| `gemma4:12b` | 12B | ~104 | 14.8 | 90% GPU, 44/49 | none — spills at every context |
| `gemma4:26b` (MoE, 4B active) | 26B | ~8 | 5.8 | 72% GPU | none |
| `lfm2.5:8b` (MoE) — **rejected** | 8.5B | **2,138** | **107** | 100% GPU, 25/25 | 128k, 6.1 GiB |

The four-figure prefill rows use a 6,794-token prompt with a unique prefix per run, three runs each, landing within 0.2% of one another — see the prompt-length and cache rules above, both of which this table got wrong on the first pass. The `~` rows are the discredited short-prompt figures, kept only because those models are ruled out on placement anyway.

**Read the prefill column, not the generation column.** An analysis is about 86% prompt tokens, so prefill is what sets the run time.

Four things here are not obvious:

- **Prefer the QAT tag over the default one.** `gemma4:e4b-it-qat` is 30% faster at prefill than `gemma4:e4b`, uses slightly less VRAM, downloads 6.1 GB instead of 9.6 GB, and matches it on the menu decision (4 of 4 each). Quantization-aware training puts the 4-bit rounding inside the training loop instead of applying it to a finished model, so it should also be the *more* accurate of the two. Those are the settled parts; see the caveat under "It drives the tool-calling loop" below for the one place the two runs differed. (And `gemma4:e4b-it-q4_K_M` is not a third option — it resolves to the same manifest id as `gemma4:e4b`.)

- **12B is out on placement, not on size, and QAT does not rescue it.** At 7.6 GB it nearly fits, and "nearly" costs everything. The QAT build is smaller (7.2 GB) and gets closer — 47/49 layers against 44/49 — but two layers on the CPU still halve it against e4b, and dropping the context to 4k does not close the gap. Near-misses on an 8 GiB card are not worth chasing; the next model down that fits entirely beats the one that almost does.
- **The 26B MoE is out by a wide margin, and its "4B active" does not help.** All 18 GB of experts still have to be somewhere, and on an 8 GiB card most of them are in host RAM. It also segfaults at init when other large models are loading on other cards, which is a runtime bug (`Gemma4Assistant requires ctx_other to be set`) and not a memory limit — it loads fine alone.
- **The fastest model that fits is not usable, and speed did not warn us.** `lfm2.5:8b` beats everything here — 2,138 tok/s prefill against `gemma4-e4b-qat-128k`'s 1,126, 107 tok/s generation against 44, all 25 layers on the GPU at the full 128k, and every weight on the card with nothing mapped to host RAM. It then fabricated every price in two runs out of two and failed structured output at four stages. See "A fast model that fails" below before proposing it again.

- **Context is nearly free on the E-series.** e4b costs 3.5 GiB at 64k and 3.8 GiB at 128k. Sliding-window attention is why, and it is why neither E build needs the `num_batch` tuning the Llama builds above depend on.

## Why `gemma4-e4b-qat-128k` is the recommended analyst model, and not the faster e2b

**Not yet switched.** The build exists on the pool and the measurements below support it, but the analyst still runs `gemma4-e2b-96k`. Read the timing section at the end before changing it.


Speed was never the question that mattered here. e4b is slower, and it makes a decision e2b does not make.

The analyst is given a menu of screened candidates and may pay to have one analysed. For three real mornings running, e2b answered with no research at all — the whole feature sat unused. Run against the identical deployed prompt, four times each:

| Model | Chose from the menu | What it picked |
|---|---|---|
| `gemma4-e2b-96k` | 2 of 4 | 3 of 15, twice; nothing, twice |
| `gemma4:e4b` | **4 of 4** | 4-5 of 15, with a stated reason each |
| `gemma4:e4b-it-qat` | **4 of 4** | 4-5 of 15, with a stated reason each |

Both failure modes matter and they pull opposite ways: ignoring the menu wastes the feature, and taking everything on it is not a choice and defeats the price. e4b did neither.

### It drives the tool-calling loop

This is the test four earlier models failed, so a menu result alone would not have been enough. One full AAPL analysis, 2026-08-26:

Two AAPL runs of each build. Runs marked *paired* were dispatched together on separate cards, which is why both are slower — a busy pool costs both models about the same.

| | `gemma4-e2b-96k` (baseline) | `gemma4-e4b-128k` | `gemma4-e4b-qat-128k` |
|---|---|---|---|
| Time, alone | 7m15s | 14m14s | **13m12s** |
| Time, paired | — | 18m24s | **17m26s** |
| LLM calls | 21 | 21, 26 | 18, 19 |
| Prompt tokens | 103k | 131.8k, 173.8k | 99.2k, 125.4k |
| Prompt share | 86% | 83%, 83% | 78%, 81% |
| Structured-output failures | 0 | 0, 1 | 1, 1 |
| Decision | — | Underweight, Hold | Underweight, Overweight |

**The tokens went up, and that is the point.** Every model that failed this pipeline was *faster*, because it did half the work: `llama3.2:3b` and `phi4-mini` each spent 42-45k prompt tokens against gemma4's 103k, having never fetched the data there was to reason over. Both e4b builds sit in the same band as the baseline or well above it. Treat a sharp drop in prompt tokens as the symptom; a rise means the loop ran.

**The prices are real**, which is the other half of the test, because a failing model invents them confidently. AAPL closed at $313.45. Across the runs, 7 of 9, 12 of 14 and 8 of 9 of the figures in the market report sit within 10% of it, and **$313.45 appears verbatim in three of the four runs**. The recurring outlier, 51.54, is an indicator value my regex swept up rather than a price.

**One structured-output failure per run is normal for this family, and is not the QAT build's fault.** That was the worry after the first pair: QAT logged one and plain e4b logged none. The second pair settled it — plain e4b logged one too, at the same Sentiment Analyst stage. Both recover by retrying as free text. This is a different thing from what disqualified `llama3.2:3b` and `phi4-mini`, which failed at **four** stages per run and answered with data they never fetched. Watch the count across real sweeps; treat a rise above one, or a failure that does not recover, as a regression.

**What genuinely does not settle is the decision itself.** The same ticker on the same day gave Underweight then Overweight (QAT), and Underweight then Hold (plain e4b). That is Gemma's recommended temperature of 1 doing exactly what it is meant to do, and `gemma4-e2b-96k` has been running at it in production all along — so this is not new with e4b, and it is not an argument for tuning the temperature down (see "Sampling" below for why that reasoning failed once already). It is an argument for reading any single analysis as one sample, and for the Scorecard's `by_model` breakdown being the only honest way to compare two models.

### The cost, stated plainly

**13-17 minutes per analysis against 7.** Use the paired 17.4 minutes, because two analyses really do run together. The sweep starts at 11:00 UTC and has until `earnings_check` at 13:00 puts its own analyses on the same pool — **two hours**. At three concurrent that is about **20 tickers**, against roughly 51 on e2b. Six tickers is two waves and comfortable.

The ceiling is reached by the watchlist growing, not by any single morning. Commissioning a ticker adds it to the watchlist and nothing in the agent ever removes one, so at the 4-6 names a run it picks, the sweep hits 20 tickers in about four days. Fixing that needs a watchlist cap or an ageing rule — a code change, not a setting. It is the thing to build before the analyst has been commissioning for a week.

Split the seven backends **4 (live) / 3 (analyst)** rather than 5/2, so the analyst needs fewer waves. Overshooting the sum is not dangerous — the proxy holds a backend for one call, not one analysis, so extra work queues for seconds rather than timing out (ten concurrent requests against seven backends all succeeded, slowest 38.4s) — but matching the sum to the backend count is what keeps every card busy.

All three builds keep Gemma's published standard sampling — temperature 1, top_k 64, top_p 0.95, per <https://ollama.com/library/gemma4>. Changing the model and the sampling in one step would leave no way to tell which one moved the result, and the recommended values are not a starting point to tune away from.

## `lfm2.5:8b`: rejected, then fixed

The most useful entry in this file, because the rejection was wrong and the record of why is worth more than the verdict was.

Run the whole harness before believing any of it. This model passed the first two steps better than anything else measured here and failed the last two — and then every failure turned out to be a defect in this app rather than in the model.

| Step | First verdict | After the fixes |
|---|---|---|
| 1. Fit | **Best measured.** 25 of 25 layers on the GPU at 128k, 6.1 GiB | unchanged |
| 2. Prefill | **Best measured.** 2,138 tok/s against e4b-qat's 1,126 | unchanged |
| 3. Candidate menu | Fails. 2 runs of 8 against e4b-qat's 4 of 4 | **still fails** |
| 4. Tool calling | Fails. 4 structured-output failures a run, every price invented | **passes** |

Two AAPL runs before the fixes and two after:

| | Before | After |
|---|---|---|
| Structured-output failures | 4, 4 | **0, 0** |
| Figures near the real close | 0 of 6, 0 of 3 | **7 of 9, 5 of 6** |
| What it cited | $188-196, then $144-150 | 315.18 against a 315.20 close |
| Market report | 532 and 0 chars | 5,319 and 4,358 chars |
| Prompt share | 66%, 65% | 74%, 74% |

**Three fixes did it, and none is specific to this model:** `json_schema` structured output, removing every price field from `TraderProposal`, and rescaling a sentiment score the model answered on a 0-100 scale. The same three took `gemma4-e4b-qat-128k` from one structured-output failure a run to none.

Three things here are worth carrying to the next model.

**The prompt-token tell does not always fire.** The four models rejected before this one were caught by a collapse to 42-45k prompt tokens against gemma4's 103k. lfm2.5 spent 77-96k and still invented everything. The **completion share** caught it instead: 34-35% against the gemma4 family's 14-17%. It read enough and then talked over what it read.

**Prices from two different years prove invention.** Run 1 cited roughly AAPL in 2024 and run 2 roughly AAPL in 2023. A stale cache would be wrong the same way twice.

**A vendor's tool-calling claim is a reason to test, not evidence.** This model's card names tool calling as a strength and it declares the `tools` capability. Both are true. A tool-calling benchmark asks whether a model picks the right function from a list; this pipeline asks whether it carries the returned number into a structured field twenty calls later. Those are different questions.

### Where it stands now

**Viable for the live bot, at 2.5x the speed.** The live deployment reads a fixed watchlist, so the menu weakness does not apply to it. Every `Signal` records its model, so the Scorecard's `by_model` breakdown is what settles whether the decisions are as good — which is the only honest comparison and the reason that breakdown exists.

**Not the analyst's model.** It chose from the candidate menu in 2 runs of 8 at its shipped sampling, against `gemma4:e4b-it-qat`'s 4 of 4. That is a judgement failure rather than a format one, so none of the three fixes touches it, and choosing what to research is the entire analyst experiment.

Raising the temperature to 1 reached 4 of 4, and neither of the two runs that scored was usable: one researched 11 of the 15 names, which is not a choice, and another named a ticker that was not on the menu at all.

**Watch the prompt share.** 74% against gemma4's 82-84%: it still reads less and talks more, and that ratio is what caught the fabrication in the first place. It sits inside the healthy band and the fixes did not move it.

**The lesson is about the harness, not the model.** Four earlier models were rejected on the same evidence and never retested. This one was rejected, and the rejection turned out to be measuring our own defects. Any of those four may deserve another run now — the docs say not to retest them without a fix for tool calling, and there is now a fix for tool calling.

## `qwen3.5:9b` and NeoHorse-1-9B: both pass, neither is switched (measured 2026-09-14)

**Both models use real prices and choose from the menu. Both are too slow to replace `gemma4-e4b-qat-128k`.** NeoHorse-1-9B is TokenRhythm's fine-tune of Qwen3.5-9B for tool use, so this test compares the base model with its agent-tuned version. The fine-tune thinks about twice as long and is not more accurate here.

Both builds are 64k. `qwen35-9b-64k.Modelfile` builds on `qwen3.5:9b`. `neohorse-9b-64k.Modelfile` builds on `hf.co/TokenRhythm/NeoHorse-1-9B-GGUF:Q4_K_M`. The Hugging Face pull has no renderer, no parser and no sampling, so the NeoHorse build copies all three from `qwen3.5:9b`. The two builds differ only in their weights. The sampling is also the one on the NeoHorse model card.

### Fit and prefill

| Model | Context | `num_batch` | Placement |
|---|---|---|---|
| `qwen3.5:9b` | 131,072 | default, 128, 64 | 17-19% CPU |
| `qwen3.5:9b` | 98,304 | 128 | 11% CPU |
| `qwen3.5:9b` | **65,536** | default | **100% GPU**, 6.3 GB |
| NeoHorse | **65,536** | default | **100% GPU**, 6.1 GB |
| NeoHorse | 131,072 | 64 | 100% GPU, 6.9 GB |

**64k is enough for this pipeline.** The largest call in 1,019 traced calls from the live deployment is about 49,500 characters, which is about 12,000 tokens. NeoHorse fits at 128k and `qwen3.5:9b` does not, probably because the library copy also holds a vision model. Both builds use 64k so that the comparison stays fair.

Prefill on one card, 6,792-token prompt: `qwen35-9b-64k` 500 tok/s, `neohorse-9b-64k` 496 tok/s. That is less than half of e4b-qat's 1,122.

### Candidate menu

Four runs of each model on the prompt from 2026-09-14, through the proxy, both models at the same time:

| Model | Chose research | What it picked |
|---|---|---|
| `qwen35-9b-64k` | **4 of 4** | 1-2 names, with a reason each |
| `neohorse-9b-64k` | **4 of 4** | 1-2 names, with a reason each |

Every answer stated correctly that the market was closed. The reasons quote figures from the prompt, such as TNON's +11.9% and AAPL's +5.4%.

### Full analysis

Two AAPL runs of each model before the open on 2026-09-14. Each wave ran one Qwen and one NeoHorse analysis at the same time, through the proxy, with the four analysts in parallel. AAPL's last close was $332.27.

| | Qwen run 1 | Qwen run 2 | NeoHorse run 1 | NeoHorse run 2 |
|---|---|---|---|---|
| Time, paired | 26.5 min | 23.7 min | 50.2 min | 51.3 min |
| LLM calls | 20 | 16 | 17 | 17 |
| Prompt tokens | 165,736 | 90,634 | 128,154 | 134,421 |
| Completion tokens | 38,446 | 34,806 | 74,048 | 99,376 |
| Completion share | 19% | 28% | 37% | 43% |
| Structured-output failures | 0 | 0 | 0 | 0 |
| Figures near the close | 10 of 11 | 7 of 8 | 8 of 9 | 12 of 16 |
| $332.27 in the report | yes | yes | yes | yes |
| Decision | Hold | Hold | Hold | Hold |

**The prices are real in all four runs.** The exact close appears in every market report. The figure 62.84 appears in all four and is an indicator value, not a price.

**The completion share does not apply to these models.** Both are thinking models, and their thinking counts as completion. NeoHorse's 43% is higher than the 34-35% that rejected `lfm2.5:8b`, but every price in its reports is real.

**Qwen run 2 read less than usual.** Its 90,634 prompt tokens are below the 99-174k of a working model. They are still far above the 42-52k of the models that never fetched data, and its prices are real. Its market report is 1,743 characters, against 5,454 in run 1.

**NeoHorse is slow because it writes too much.** In run 1, one risk-debate answer passed 13,000 tokens at 22 tok/s, so that one call took more than ten minutes. The builds set no output limit.

### Writing takes the time, not reading

The pool logs for these runs hold 83 requests:

| | Tokens | Time | Speed |
|---|---|---|---|
| Reading the prompt | 566,811 | 19.7 min | 480 tok/s |
| Writing the answer | 225,958 | 169.5 min | 22.2 tok/s |

**Writing took 90% of the model time.** The notes above say that prefill sets the run time, because an analysis is about 86% prompt tokens. That is true of the token count, not of the time. For a thinking model, the number of tokens it writes decides the run time. This has not been measured for `gemma4-e4b-qat-128k`, which thinks less.

### How this was run, and three traps

- **`propagate_ticker` charges the research budget.** A test inside `trading-experiment` puts a charge in the live agent's ledger. These runs used a copy of `trading.db`, made with SQLite's backup call.
- **The app finds its database from the location of its own code.** A symlink to `backend/` opens the repository's `data/trading.db`, because the path goes up through the real folder. The code must be a real copy beside the `data` folder.
- **The repository's `.venv` held an old, non-editable copy of `tradingagents`.** It did not have the parallel analysts or the trawl fetcher. Put `TradingAgents/` first on `PYTHONPATH` for a run on the host, or reinstall the package.

## Sampling: use Gemma's published values

`temperature 1 / top_k 64 / top_p 0.95`, for every build here. Google publishes those as the standard configuration for all use cases (<https://ollama.com/library/gemma4>), so they are not a default left alone out of caution — they are the documented setting.

**On 2026-08-26 `gemma4-e2b-96k` briefly ran at `0.15 / 20 / 0.9`, and the reasoning behind that was wrong.** The observation was real: the agent chose research on only about half of identical passes, and turning the temperature down made it consistent. The inference did not follow. Running the same prompt against e4b at Gemma's stock sampling produced a research decision 4 times out of 4 — so the inconsistency was the 2B model's capability, not the sampling. Lowering the temperature suppressed the symptom while moving production off the documented configuration in the middle of an experiment. It is reverted.

**One finding from that day is independent and still holds.** The app reaches Ollama through the OpenAI-compatible `/v1/chat/completions` endpoint, and that endpoint silently ignores `temperature` in the request body: via `/v1` the same prompt answered differently 2-3 times in 5 whatever temperature was asked for, while the native `/api/chat` with `options.temperature=0` was identical 4 times of 4. `TRADINGAGENTS_TEMPERATURE` therefore does nothing. If sampling ever does need to change, the Modelfile is the only channel that reaches the model.
