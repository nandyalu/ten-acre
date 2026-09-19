---
name: model-change
description: Evaluate a language model before pointing this app at it, or
  investigate a model that has started behaving worse. Seven models were
  tested here and six rejected; this is the acid test that separated them, and
  the tells are not the ones a model card advertises.
---

# model-change

**Speed rules a model out. Behaviour rules it in.** Seven models have been evaluated against this pipeline and six were rejected, all for the same failure: they do not error, they answer fluently, having invented the data.

The fastest model that ever fit this hardware is one of the rejected ones. Do not shortcut this.

---

## Before you pull anything

Three ways to burn a download and a build without learning anything:

- **A quantizer's own repo needs a fork, not stock Ollama.** `XHToken/Spark-X2.5-4B-GGUF` states plainly that "Spark-X2.5 support is provided by XHToken/llama.cpp" — a fork, not the upstream project this pool's `ollama/ollama:rocm` image ships. Our pool cannot load its `spark2_5` architecture at any size. Read the model card's own quick-start before pulling: if it names a fork or a branch, stock Ollama will not load it.
- **A "-MTP-" file is a draft head, not a model.** `agentionai/Qwen3.8-Flash-Next-MTP-ROCmFP4-FAST-GGUF` is 2.28 GiB of multi-token-prediction weights for speculative decoding — it does nothing loaded alone, and its target model needs the same unmerged fork problem above (`qwen4exp`, `ROCmFPx` quant types). "MTP" or "draft" in the name is the tell.
- **"Mini" is marketing, not a VRAM number.** `Nex-N2.5-mini` is 35B total params (Qwen3.5-MoE, 8 of 256 experts active) and ships only as raw safetensors for a two-GPU, 160 GB server — not published for Ollama at all. Check the real total parameter count and whether Ollama serves the model before the name talks you out of checking.

None of these three needed a test run to reject. A few minutes reading the model card settled all of them.

## Run these three checks in order

Each check costs more than the one before it. Stop at the first failure — do not run a later, costlier check on a model that already failed a cheaper one.

1. **Output check (one call, under a minute).** Run `backend/scripts/probe_prompt.py` against the candidate. Confirm it returns a real answer in the right shape: no empty response, no raw tool call printed as text, no refusal. This step only asks "does the model respond at all" — it does not check the content. A model that fails here is rejected now; do not spend a full analysis on it. See "Keep it off the live container" for how to point the script at the candidate without touching `ten-acre`.

   **Read the reasoning, not just the shape — a model can pass the shape check and still be caught here.** `empero-ai/Qwen3.8-4B-Distill-GGUF` (Q8_0) returned well-formed JSON in both samples on 2026-09-15, no refusal, no raw tool call. One sample still copied the prompt's own JSON-schema *example* — `{"ticker": "MSFT", "side": "adjust", "stop": 410.5, "reason": "why"}`, illustrative filler in the format instructions — into a real order, keeping the ticker and the stop price and inventing a reason ("Reconfirm the long-range support floor") for a stock the account does not hold, does not track, and that never appears in the real data it was shown. Rejected here, before the 20-minute real-analysis run it would otherwise have cost. See "the schema-example tell" below.
2. **Real analysis check (one full run, several minutes).** Run one real analysis end to end, outside the container (`TradingAgents/scripts/batch_analyze.py` or the equivalent), and apply **"The one rule that decides it," below: do the prices in the report match the actual close?** A model that invents prices is rejected here, however well it wrote the rest of the report. Do not proceed to step 3 on a model that fails this check — the menu test below only tells you whether a model chooses to research, and that answer means nothing from a model that researches and then reports numbers that are not real.
3. **Candidate-menu test (four to eight full runs).** Only after steps 1 and 2 both pass, run the candidate-menu test described below. This step is the most expensive of the three, and it is the one this pipeline actually depends on: the agent's whole job is choosing what to research.

---

## The one rule that decides it

**A model that never fetched the data will still produce a confident report.** So the test is never "did it answer" and never "was the answer plausible". It is:

> **Do the prices in its market report match the actual close?**

Open the stored report and check the numbers against the real session. `gemma4-e4b-qat-128k` was accepted partly because AAPL's actual $313.45 close appears verbatim in three runs of four. `llama3.2:3b` reported "no available market data for AAPL" and then issued a Buy anyway.

Everything below is a cheaper proxy for that question. **The proxies are for triage; the price check is the verdict.**

## The schema-example tell

**A model can invent data from the prompt's own scaffolding, not only from its training.** The price check above catches a model that reports numbers it never fetched. This catches a narrower thing: a model that copies the illustrative example inside the prompt's JSON-format instructions into a real answer, keeping the example's ticker and numbers and inventing a reason around them.

`empero-ai/Qwen3.8-4B-Distill-GGUF` did this on 2026-09-15: the prompt's format section showed `{"ticker": "MSFT", "side": "adjust", "stop": 410.5, "reason": "why"}` as a template, and the model's real order list carried the same ticker and the same stop price forward, dressed in a fabricated rationale, for a stock nowhere in the real account data.

**Check every ticker in the model's orders against the tickers the prompt actually showed it** — the watchlist table and the candidate list, not the format example lower down. A ticker in an order that appears nowhere else in the prompt is not a bold pick. It is the same failure as inventing a price, caught one step earlier and one step cheaper, because the output check that finds it costs one call instead of one full analysis.

## The token tells

Per-run usage is recorded on every `Signal` — `prompt_tokens`, `completion_tokens`, `llm_calls`, `duration_seconds` — and comes from the provider's own `usage` block, never an estimate. Read them from the signal detail page or the database.

**Prompt-token collapse.** A model that never retrieved anything has far less to read.

| | Prompt tokens |
|---|---|
| A working model on this pipeline | **99–174k** |
| The four models that never fetched | **42–52k** |

**Completion share, which is the better tell.** `lfm2.5:8b` broke the rule above — it spent 77–96k prompt tokens and still invented every price. It read enough and then talked over it.

| | Completion share |
|---|---|
| A working model | **14–17%** |
| `lfm2.5:8b` | **34–35%** |

**This tell does not transfer to a reasoning model, and assuming it does will reject a good one.** A model that emits a thinking trace counts it as completion. `qwen-3.8-27b` measured **31%** on INTC on 2026-09-10 — inside the range that disqualified `lfm2.5:8b` — while every price in its report was real: 106.24 against the true price, a 50-SMA at 99.62, RSI 63.36, 140.3M shares. Its prompt tokens were **243k**, nearly double the local model's, so it read more rather than less.

**When the model reasons, fall back to the verdict**: check the prices against the close. The share is a proxy, and a proxy calibrated on models that do not think out loud.

**Structured-output failures.** One per run is normal for the Gemma e4b family and both builds do it, recovering by retrying as free text. Four per run disqualified `llama3.2:3b` and `phi4-mini`. Treat a count above one, or a failure that does not recover, as a regression.

## The candidate-menu test

**This agent's whole job is choosing what to research**, so a model that will not make that choice is useless here however well it writes.

Run the same real prompt four times and count how many times it commissioned research with a stated reason:

| Model | Chose research |
|---|---|
| `gemma4:e4b-it-qat` | **4 of 4**, 4–5 names of 15 with a reason each |
| `gemma4:e2b` | 2 of 4 — and answered "no research" three real mornings running |
| `lfm2.5:8b` | 2 of 8 |

This is why the deployed model is about twice as slow as the one it replaced. Speed was never the reason.

## What the rejections cost, and what they taught

| Model | Time | Tokens | Structured-output failures | What its market report contained |
|---|---|---|---|---|
| `llama3.2:3b` @128k | 3m23s | 52k | 4 | "no available market data for AAPL" — then a Buy anyway |
| `phi4-mini` @96k | 4m10s | 49k | 4 | the raw tool call as text, plus fabricated 2023 OHLCV around $130 for a stock at $308 |
| `lfm2.5:8b` @128k | 9m06s, 7m31s | 146k, 119k | 4, 4 | prices around $188–196, then $144–150, for a stock at $313.45 |
| `empero-ai/Qwen3.8-4B-Distill-GGUF` (Q8_0) | rejected at the output check, no full run needed | 3.1k prompt / 232–466 completion (single-call, not a full analysis) | 0 | not a price fabrication — copied the prompt's JSON-schema example (ticker `MSFT`, stop `410.5`) into a real order with an invented reason, for a stock never mentioned in the real data |

`lfm2.5:8b` is the one to read carefully. Its two runs cited prices from **different years** — roughly 2024, then 2023 — which rules out a stale cache and leaves recall from training. It is the fastest model that has ever fit this hardware: 2,138 tok/s prefill, all 25 layers on the GPU at 128k in 6.1 GiB.

**Its model card claims tool calling as a strength and it declares the `tools` capability. Both are true and neither predicts anything here.** A tool-calling benchmark measures whether a model picks the right function from a list. This pipeline needs it to carry a returned number into a structured field twenty calls later. **Treat a vendor's tool-calling claim as a reason to test, never as evidence.**

## Measuring speed without fooling yourself

- **Use a long, cache-busted prompt.** A 30-token prompt measures per-request overhead, not prefill, and once ranked the better model *below* the worse one. Repeating an identical prompt is as bad: Ollama's cache returns the second in 0.03s, which reads as 219,000 tok/s. Use several thousand tokens with a unique prefix per run.
- **Measure one model at a time.** Seven cards are not seven independent measurements. The same model at the same context measured 43.5, 28.0 and **69.6** tok/s depending only on how busy the rest of the pool was.
- **A single-ticker sample understates a batch by about ten percent.**
- **Bypass the proxy** to pin a run to one card: point `OLLAMA_BASE_URL` at a backend's docker-bridge IP.

## If it is a local build

- **`ollama ps` must read `100% GPU`.** Any CPU split costs far more than any other setting.
- **Set `PARAMETER num_batch`, not only `num_ctx`.** The compute graph is what limits context, not the KV cache: at the default `num_batch 512` the graph wants 5.1 GiB and pushes 40% of a small model's layers onto the CPU. Dropping it to 64 fits the full 128k entirely on the GPU for 16% slower prefill.
- Gemma is the exception that makes this confusing — sliding-window attention keeps its graph small, so it runs at 96k with the default batch. Do not reason from it to a Llama of the same size.
- **A hybrid linear-attention build can get the same exception for a different reason.** `empero-ai/Qwen3.8-4B-Distill-GGUF` (Q8_0, three Gated DeltaNet layers for every full-attention layer) loaded at 100% GPU using 6.5 GiB at its full 131,072-token context with no `num_batch` override at all — the same free ride as Gemma's sliding window, on a dense-sized Qwen build that would otherwise need tuning. Check `ollama ps` yourself before assuming a Gated-DeltaNet or other linear-attention hybrid needs the same `num_batch` fix a plain dense transformer does.

## If it is a hosted endpoint

- **Read the daily token allowance before the requests-per-minute limit.** One analysis is roughly 130,000 tokens, so the allowance decides how many analyses a day you get. Two models on one free tier differed by a factor of eighty on that alone.
- **Price input and output separately, never blended.** They differ by about eight times, so a blended rate is a property of the workload's output ratio rather than of the model. Blending once produced a cost estimate 39% too low.
- Expect `429`s. The app reads the vendor's own `retry-after` header, or the wait in Gemini's error body, and waits exactly that long. A vendor that sends no budget headers, such as Gemini, needs `LLM_REQUESTS_PER_MINUTE`, `LLM_TOKENS_PER_MINUTE` and `LLM_REQUESTS_PER_DAY` set, or every analysis collects refusals.
- **On Gemini, set `TRADINGAGENTS_GOOGLE_THINKING_LEVEL`.** Without it there is no thinking to read, and reading the thinking is how every tell on this page was found.

## Keep it off the live container

`ten-acre` is the real deployment: one agent, one book, one settings record. A candidate test is not real trading, so it must never touch that container or its state.

- **Never point the deployed app's settings page at a candidate to test it.** That switches the real agent's model. Per `CLAUDE.md`, a model switch needs a `JOURNEY.md` entry first — it is not a quiet test-and-revert.
- **Never `docker exec` into `ten-acre` to pull, build, or run a candidate.** Pull and build the model through one Ollama pool backend instead (`ollama-pool-a` … `-g`, or `ollama/build.sh`). Every backend bind-mounts the same model directory, so one pull reaches all seven cards, and the deployed app can later see the model with no change to the app itself.
- **Run the candidate-menu test and the price check from a process outside the container.** Follow the pattern of `backend/scripts/probe_prompt.py` and `TradingAgents/scripts/batch_analyze.py`: a local script, or `docker compose run --rm`, pointed at the candidate through its own `TRADINGAGENTS_LLM_PROVIDER` and `OLLAMA_BASE_URL`, against its own throwaway database. Never point a test run at the deployed database — one stray `Signal` in the real record breaks the `by_model` scorecard this skill exists to read.
- **A test run never calls a broker.** `probe_prompt.py` states the standard directly: "This cannot trade." A candidate test builds a prompt and reads a report. It must not reach `run_once`, `screen`, or any broker path.

**The concrete recipe for each check, confirmed 2026-09-15:**

- **Stage 1, `probe_prompt.py`, needs a watchlist and a model setting, and it reads them from the database — but "the database" here is this checkout's own `data/trading.db`, not the deployed container's.** Confirm the two are different files before writing to either: `docker inspect ten-acre --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{"\n"}}{{end}}'` shows the container's data volume (`/opt/stacks/trading-experiment/data`), which is not this checkout's `data/` directory. Once confirmed separate, seeding the local one is exactly the "own throwaway database" this section asks for:
  ```python
  from backend.database import db
  from backend.services import analysis
  db.add_to_watchlist("AAPL")
  analysis.set_model("candidate-tag:latest")
  ```
  Reset both when done: `db.remove_from_watchlist(...)` for every ticker added, and `db.set_setting("llm_model", "")` to return the setting to unset (`analysis.get_model()` treats an empty string as "use the env default," the same as never having set it).
- **Stage 2, `batch_analyze.py`, needs no database at all** — every setting comes from an environment variable, so there's nothing to seed or reset:
  ```sh
  TRADINGAGENTS_LLM_PROVIDER=ollama \
  TRADINGAGENTS_DEEP_THINK_LLM=candidate-tag:latest \
  TRADINGAGENTS_QUICK_THINK_LLM=candidate-tag:latest \
  TRADINGAGENTS_LLM_BACKEND_URL=http://localhost:11435/v1 \
  uv run python scripts/batch_analyze.py --tickers AAPL
  ```
  (run from `TradingAgents/`; `localhost:11435` is the shared pool proxy, not any one backend).
- **Pull and tag a candidate on one pool backend directly**, e.g. `docker exec ollama-pool-a ollama pull hf.co/<org>/<repo>:<quant>`, then `ollama create <tag> -f <Modelfile>` on that same backend for a custom context/`num_batch`. The shared model directory makes the build visible to the proxy and every other backend with no further action. Remove a rejected candidate's tag the same way: `docker exec ollama-pool-a ollama rm <tag>`.

## Before you switch

1. **Write the `JOURNEY.md` entry first.** The model is recorded on every `Signal`, and the scorecard's `by_model` breakdown is the entire point of that column — but only a dated entry says *when* the question changed.
2. **The model is a runtime setting**, so the settings page switches it without a redeploy. The environment variable is only the default for an unset setting.
3. **Remember one analysis is one sample.** The deployed model runs at temperature 1, which is its publisher's recommended setting, and the same ticker on the same day has returned opposite decisions. Two of twelve paired analyses agreed. **No single run is evidence** — which is why every count on this page is out of four or eight.

## Do not

- **Do not retest a rejected model on speed grounds.** Every rejection here was on behaviour, and speed was never the constraint.
- **Do not treat a fixed defect as a reason to keep a rejected model.** `lfm2.5:8b` went from 4 structured-output failures a run to 0 once three real app defects were fixed, and it is still the wrong model here, because it makes the menu decision 2 times in 8.
- **Do not lower the temperature to make the agent's choices consistent.** That was tried on 2026-08-26, treated a symptom of a small model's capability as a sampling problem, and was reverted.
- **Do not test a candidate inside or through `ten-acre`.** Not `docker exec`, not the settings page, not the deployed database. See "Keep it off the live container" above.
- **Do not run the candidate-menu test before the output check and the real analysis check both pass.** Four to eight full runs are wasted on a model that a one-minute output check, or one real analysis, would already have rejected. See "Run these three checks in order" above.
