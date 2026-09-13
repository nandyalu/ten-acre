---
name: model-change
description: Evaluate a language model before pointing this app at it, or
  investigate a model that has started behaving worse. Six models were tested
  here and five rejected; this is the acid test that separated them, and the
  tells are not the ones a model card advertises.
---

# model-change

**Speed rules a model out. Behaviour rules it in.** Six models have been evaluated against this pipeline and five were rejected, all for the same failure: they do not error, they answer fluently, having invented the data.

The fastest model that ever fit this hardware is one of the rejected ones. Do not shortcut this.

---

## The one rule that decides it

**A model that never fetched the data will still produce a confident report.** So the test is never "did it answer" and never "was the answer plausible". It is:

> **Do the prices in its market report match the actual close?**

Open the stored report and check the numbers against the real session. `gemma4-e4b-qat-128k` was accepted partly because AAPL's actual $313.45 close appears verbatim in three runs of four. `llama3.2:3b` reported "no available market data for AAPL" and then issued a Buy anyway.

Everything below is a cheaper proxy for that question. **The proxies are for triage; the price check is the verdict.**

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

## What the five rejections cost, and what they taught

| Model | Time | Tokens | Structured-output failures | What its market report contained |
|---|---|---|---|---|
| `llama3.2:3b` @128k | 3m23s | 52k | 4 | "no available market data for AAPL" — then a Buy anyway |
| `phi4-mini` @96k | 4m10s | 49k | 4 | the raw tool call as text, plus fabricated 2023 OHLCV around $130 for a stock at $308 |
| `lfm2.5:8b` @128k | 9m06s, 7m31s | 146k, 119k | 4, 4 | prices around $188–196, then $144–150, for a stock at $313.45 |

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

## If it is a hosted endpoint

- **Read the daily token allowance before the requests-per-minute limit.** One analysis is roughly 130,000 tokens, so the allowance decides how many analyses a day you get. Two models on one free tier differed by a factor of eighty on that alone.
- **Price input and output separately, never blended.** They differ by about eight times, so a blended rate is a property of the workload's output ratio rather than of the model. Blending once produced a cost estimate 39% too low.
- Expect `429`s. The app reads the vendor's own `retry-after` header, or the wait in Gemini's error body, and waits exactly that long. A vendor that sends no budget headers, such as Gemini, needs `LLM_REQUESTS_PER_MINUTE`, `LLM_TOKENS_PER_MINUTE` and `LLM_REQUESTS_PER_DAY` set, or every analysis collects refusals.
- **On Gemini, set `TRADINGAGENTS_GOOGLE_THINKING_LEVEL`.** Without it there is no thinking to read, and reading the thinking is how every tell on this page was found.

## Before you switch

1. **Write the `JOURNEY.md` entry first.** The model is recorded on every `Signal`, and the scorecard's `by_model` breakdown is the entire point of that column — but only a dated entry says *when* the question changed.
2. **The model is a runtime setting**, so the settings page switches it without a redeploy. The environment variable is only the default for an unset setting.
3. **Remember one analysis is one sample.** The deployed model runs at temperature 1, which is its publisher's recommended setting, and the same ticker on the same day has returned opposite decisions. Two of twelve paired analyses agreed. **No single run is evidence** — which is why every count on this page is out of four or eight.

## Do not

- **Do not retest the five rejected models on speed grounds.** They were rejected on behaviour, and speed was never the constraint.
- **Do not treat a fixed defect as a reason to keep a rejected model.** `lfm2.5:8b` went from 4 structured-output failures a run to 0 once three real app defects were fixed, and it is still the wrong model here, because it makes the menu decision 2 times in 8.
- **Do not lower the temperature to make the agent's choices consistent.** That was tried on 2026-08-26, treated a symptom of a small model's capability as a sampling problem, and was reverted.
