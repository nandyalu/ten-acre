# Making a small model use today's prices

Five small models have been tested against this app's analysis pipeline and all five failed the same way. Each one invented prices. This page is about what it would take to fix that, in the order the work is actually worth doing.

## First, the premise is wrong, and that changes everything

It is natural to read the failures as a knowledge-cutoff problem. The models quote prices from two or three years ago, so it looks as though they need newer training data, or a retrieval step that hands them the current number.

**They already get the current number.** The app fetches live prices, puts them in the prompt, and the models still invent. Two pieces of evidence say so plainly:

- **`lfm2.5:8b` read 77,000 to 96,000 prompt tokens per run and cited no price within 10% of the real close.** It received the data. It did not use it.
- **On 2026-08-06, `gemma4-e2b`'s market report carried the right prices throughout while its trader stage wrote $2,000 for a stock at $356.62.** The correct figure was in the same run, a few steps earlier.

So retraining on newer data fixes nothing. Prices change every day, and no training schedule keeps up with that. **The model does not need to know the price. It needs to copy the price it was given into the right field.**

That reframes the whole problem. It is not about knowledge. It is about grounding and instruction-following, and those have much cheaper fixes than training.

## What actually fails, in three separate ways

The five rejected models do not fail identically, and the differences decide which fix applies.

| Failure | What it looks like | Which models |
|---|---|---|
| **Never calls the tool** | Prints the tool call as text and writes plausible output beneath it | `llama3.2:3b`, `phi4-mini`, kotakneo, alma-trader |
| **Calls it, ignores the answer** | Reads 77-96k prompt tokens, then quotes prices from training | `lfm2.5:8b` |
| **Loses the number between steps** | The market report is correct; the trader stage invents | `gemma4-e2b` at the default context |

The third is the one to understand first, because the good models still do it occasionally and it is not a model defect at all. **The trader stage was never given a price.** The app fixed it by computing a snapshot in Python and putting it in the trader's prompt. No model change was involved.

There is a general lesson in that. Before training anything, check whether the model was given what you are blaming it for not knowing.

## The fix ladder

Work down this list. Each step costs more than the one above it, and the cheap steps may remove the need for the expensive ones.

### 1. Constrain the decoding, so invalid output is impossible

**Done, 2026-08-27, and it worked.** The results are at the end of this section; the reasoning follows.

This is the highest-value change and it needs no training at all.

Today the app asks for structured output through **function calling**: it binds a JSON schema as a tool and asks the model to call it. `capabilities.py` sets `preferred_structured_method="function_calling"` as the default, so every local model gets that path. It is the hardest one for a small model, because the model must produce a correctly-named tool call with correctly-typed arguments entirely on its own.

**A different method already exists and is unused.** `StructuredMethod` allows `json_schema`, which sends `response_format={"type": "json_schema", ...}`. Ollama supports this and turns it into grammar-constrained decoding: the sampler is not allowed to emit a token that would break the schema. A malformed answer stops being possible rather than becoming less likely.

The experiment is small:

1. Set `preferred_structured_method="json_schema"` for the local-model pattern in `capabilities.py`.
2. Re-run the tool-calling test from `bench/` in the separate `ollama-stack` project on a rejected model.
3. Compare the structured-output failure count against the same model's previous run.

`lfm2.5:8b` failed at four stages in each of two runs. If constrained decoding takes that to zero, the model is worth re-testing on everything else — and it is the fastest model that has ever fit this hardware.

**What this cannot fix.** A grammar forces the shape of the answer, not its truth. A model can emit a perfectly-formed `{"entry_price": 188.46}` for a stock at $313.45. Step 2 is what addresses that.

#### What it changed

`LocalCompatibleChatOpenAI` now defaults structured output to `json_schema`, and the `ollama` provider uses that class. The capability table resolves by model ID and a local model's ID is whatever someone named the build, so no pattern can recognise one — but the client class already knows the endpoint is local.

Ollama honours it. Asked to "answer at length in prose" with a schema attached, `lfm2.5:8b` returned strict schema-matching JSON.

| Model | Before (`function_calling`) | After (`json_schema`) |
|---|---|---|
| `lfm2.5:8b` | 4 failures, 4 failures | **0, 0** |
| `gemma4-e4b-qat-128k` (production) | 1 failure, 1 failure | **0** |

Both models now cite AAPL's exact close. `lfm2.5:8b` went from **0 of 6 and 0 of 3** figures near the real price to **6 of 7**, and its run time dropped from 7.5-9.1 minutes to 5.9, because a structured-output failure costs a whole free-text retry.

**A model whose capability entry says it cannot do `json_schema` keeps function calling**, and the `tool_choice` suppression that path needs is unchanged. Those entries were written from real API refusals.

**Read this as fixing the format, not the truth.** `lfm2.5:8b` still fabricated prices in its first constrained run; what stopped that was step 2.

**And it constrains structure, not ranges.** A constrained run produced a well-formed answer carrying `overall_score: 52` for a field declared `le=10`, which Pydantic then rejected. The grammar guarantees the right fields with the right types; it does not guarantee a number inside its bounds. Two things follow. Bounds still have to be validated in Python, and a field whose meaning a model can misread — a 0-10 score answered as a percentage — is worth naming so the misreading is impossible. `stop_atr_multiple` is named that way for exactly this reason.

### 2. Take the numbers away from the model

**Done, 2026-08-27.** The model should decide direction and conviction. It should not be the thing that types a price.

The app already does some of this and it works:

- **`build_verified_market_snapshot`** computes the price in Python from the same OHLCV, and the trader prompt forbids recalled prices.
- **`_trade_plan_levels`** discards a level too far from the traded price.
- **`_levels_on_the_wrong_side`** discards a stop above the price or a target below it.
- **`_resolve_stop_loss`** derives a stop from 2×ATR(14) when the model's stop is unusable.

The next step is to stop asking for absolute numbers at all. Let the model answer in relative terms and compute the rest:

| Instead of asking for | Ask for | Python computes |
|---|---|---|
| `stop_loss: 90.76` | `stop_atr_multiple: 2.0` | price − 2×ATR |
| `price_target: 92.00` | `target_r_multiple: 2.5` | entry + 2.5×risk |
| `entry_price: 91.00` | `entry: "market"` or `"pullback"` | the live quote, or a computed level |

A model cannot fabricate a price it was never asked to produce. This is the change that would make a weak model usable, and it needs no training either — only a schema change and a prompt change.

#### What it changed

`TraderProposal` no longer has `entry_price`, `stop_loss` or `target_price`. The schema the model is shown carries two distances instead:

- `stop_atr_multiple` — how far the stop sits from the entry, in ATRs. Bounded 0.25 to 10.
- `target_r_multiple` — how much the trade aims to make as a multiple of what it risks. Bounded 0.25 to 20.

`verified_levels_basis` returns the close and ATR as numbers rather than markdown, and `resolve_levels` turns the multiples into prices. `build_verified_market_snapshot` still renders the same figures for the model to reason over; the difference is that Python now computes with them.

**The rendered output is unchanged on purpose.** Downstream consumers parse `**Entry Price**`, `**Stop Loss**` and `**Target Price**` out of the markdown, so moving who computes a number must not move where it appears. Verified: with a close of $313.45 and an ATR of $7.18, a proposal of 2.0 ATRs and 2.5R renders entry 313.45, stop 299.09, target 349.35, and the app's parsers read all three exactly as before.

Python refuses what cannot be defended, on the same rule as everywhere else in this app:

- **Hold gets no levels.** There is no trade to place them around.
- **No verified close or ATR means no levels**, rather than guessed ones.
- **Direction follows the action.** A long stops below and targets above; a short reverses. Backwards would store a stop that triggers the moment it is placed.
- **A stop wider than the price is refused**, since it puts the level at or below zero.
- **An implausible multiple costs the levels, not the proposal.** Past about 6 ATRs a stop is not managing risk, and past 10R a target is not a plan. The schema bounds stay loose deliberately: a run answered 10.75, a tight schema cap rejected the whole proposal, and the reasoning and win probability went out with the one number that was unusable.

#### Confirmed in live runs

Both models returned a proposal with **no price field of any kind**. `lfm2.5:8b` gave 1.5 ATRs and 3R; `gemma4-e4b-qat-128k` correctly gave neither on a Hold, which takes no levels. The production model's structured-output failures stayed at 0 and its market report came back at 4,533 characters, citing AAPL's exact close.

#### A pre-existing failure this surfaced

Two step 2 runs produced a market report of 532 and 0 characters, against roughly 5,000 before. It looked like a regression and is not one. Reading the traces of every run that day shows the market analyst occasionally failing in two ways, **including once before any of these changes existed**:

- It **prints the tool call as text** — a fenced `{"tool_calls": [...]}` block in the answer — and never calls it. This is the failure that disqualified four models, appearing intermittently in `gemma4-e4b-qat-128k`.
- It **returns an empty answer**, which is what `lfm2.5:8b` did.

It happened in 3 of 8 traced runs. Neither step causes it, and neither fixes it: these analysts bind real tools, so `json_schema` structured output does not apply to them. It is worth its own investigation, and the trace capture is what makes that investigation possible.

**The lesson for anyone measuring here is the one this page keeps repeating.** A run-level metric moved, the obvious cause was the change just made, and the traces said otherwise. Check the record before attributing.

#### Fixed, 2026-08-27

Three fixes were built and measured separately. Two survived.

| | What it does | Result |
|---|---|---|
| **Detect** | Retry when the answer is ungrounded | **4 of 4**, from a 0-of-3 baseline. Kept |
| **Prompt** | Tell the model to call, not describe | 13 of 17 either way. **Reverted** |
| **Salvage** | Execute a written-out call | Recovers the one real case in every trace. Kept as a fallback |

**Ollama ignores `tool_choice`.** Sending `required` behaves exactly like `auto` and like sending nothing, measured on the same model, so the standard lever for forcing a call is unavailable here. That is a real gap and worth knowing before designing around it.

**The model is not incapable.** Given "Get me AAPL's price data from 2026-07-27 to 2026-08-27" it made a correct call every time; given "think aloud about your plan first, in detail, before doing anything" it narrated every time. The prompt decides it — which is why the prompt fix looked so promising and is exactly why measuring it mattered.

**Detection needed two checks, and the obvious one was the weaker.** Reading the text catches a fenced JSON block naming a bound tool. It does not catch a model that writes "### Phase 1: Detailed Swing Trade Planning" — prose naming no tool, indistinguishable from a report by content. What gives that away is the conversation: **if nothing ever returned a tool result and the model is not asking for one, the answer was composed from no data.** With the text check alone the live test recovered 3 of 6 and the detector never fired, so those three were luck. With the structural check it is 4 of 4.

**Salvage is deliberately the fallback, not the first move.** Retrying leaves the model's behaviour visible; executing a written-out call accepts a shape the API never sent, so a model that keeps narrating keeps working and nobody notices. The order is retry, then salvage, then a loud error.

It costs something real, and the cost should be stated: the model loses the ability to name a level for a reason nobody encoded, such as a support line it saw in the chart. Whether that ability was ever worth anything here is testable against the Scorecard.

### 3. Improve the tools before improving the model

Small models fail on wide interfaces. Two changes usually help more than a fine-tune:

- **Fewer fields per call.** A schema with twelve optional fields gives a small model twelve chances to go wrong. Split it into two calls with five each.
- **Fewer tools per decision.** A model choosing among three tools is far more reliable than one choosing among twelve.

This is also where **MCP** belongs, and it is worth being precise about what it does. MCP standardizes how a tool is described and called. It does not make a model better at calling tools. Adopting it is a portability decision, not an accuracy fix, and it will not move any number in the harness.

### 4. Fine-tune, last

Only reach here if steps 1 to 3 leave a model that is still worth rescuing.

**What a fine-tune can teach:**

- The exact tool-call format this pipeline expects.
- The habit of copying a retrieved figure into a field rather than recalling one.
- The house answer style, which shortens completions and cuts cost.

**What it cannot teach:** today's prices. Training on prices would need retraining every day, and the model would still be wrong between runs. If you find yourself planning that, go back to the top of this page.

## The training data, if you get to step 4

The goal is not to teach finance. It is to teach the shape of a correct run. That makes this a **distillation** job: take a model that already drives the loop and teach a faster one to imitate it.

`gemma4:e4b-it-qat` is the teacher. It scores 0 to 1 structured-output failures per run and cites real prices. `lfm2.5:8b` is the obvious student, because it is 2.5x faster and fails only on grounding.

### What one training example is

One example is a single LLM call from a real analysis:

- the full prompt, including the system message and the tool definitions
- the tool calls the model made, with arguments
- the tool results it received
- the final structured answer

An analysis makes about 21 such calls. A nine-ticker sweep therefore produces roughly 190 examples a day.

### How much you need

| Goal | Rough sample count |
|---|---|
| Fix tool-call formatting | 500 to 1,000 |
| Fix format and grounding together | 2,000 to 5,000 |
| Change reasoning style | more than this approach is suited to |

At 190 examples a day, a month of sweeps gives about 5,700. That is enough, and it costs nothing extra to collect because the analyses run anyway.

### The app now records this, and it is off by default

`backend/services/llm_traces.py` writes one JSON line per LLM call. **Set `LLM_TRACE_DIR` to a writable path to turn it on.** Unset means nothing is recorded, so a deployment that does not want the disk cost pays nothing.

One trace file is one analysis, named `<ticker>-<run id>.jsonl` under a folder per day. Each line holds the messages that went in, the tool calls the model made, the tool results it received, and what it answered.

Measured on two real `gemma4-e4b-qat-128k` runs of AAPL:

| | Run 1 | Run 2 |
|---|---|---|
| Calls captured | 21 | 21 |
| Calls where the model called a tool | 12 | — |
| Tool results captured | 43 | — |
| File size | 336 KB | 468 KB |

Call it **0.4 MB an analysis**, which at nine a day is roughly **3.6 MB a day and 110 MB a month**. That is small enough that retention is not worth designing yet. The spread between two runs of the same ticker on the same day is 40%, so treat any single measurement here as an estimate.

**`Signal.trace_id` is the part that matters.** It joins a trace to the signal it produced, and that signal gets graded weeks later against what the market did. **That lets you train on the runs that turned out to be right**, rather than on every run the teacher happened to produce. It is the advantage this app has and a public dataset does not.

### Getting the data out

`backend/scripts/export_training_set.py` turns traces into the `messages` format every fine-tuning tool accepts:

```sh
python -m backend.scripts.export_training_set --out train.jsonl
python -m backend.scripts.export_training_set --graded-pass --model gemma4-e4b-qat-128k --out train.jsonl
```

The second command is the distillation set this page describes: the runs a working model produced, keeping only the ones the market later agreed with. Filters stack, and the script reports what it skipped and why, so an empty result explains itself rather than looking like a bug.

### Filtering matters more than volume

Train only on runs that pass the checks this app already has:

- zero structured-output failures
- every price in the market report within 10% of the real close
- a decision the Scorecard later graded as correct

The last filter is the strongest and the slowest to accumulate, because grading needs the trade horizon to pass. Start with the first two.

## Tools, and one hardware problem

**Unsloth** is the reasonable choice for the training itself. It does LoRA fine-tuning with much lower memory use than plain transformers, and an 8B model at 4-bit fits in about 12 to 16 GB of VRAM.

**The cards in this machine will not do it.** The pool is seven RX 6600s at 8 GiB each, and they are AMD. Two separate problems follow:

- **8 GiB is below what an 8B LoRA needs.** Seven cards do not help, because a single training job cannot be split across them without more setup than the job is worth.
- **Unsloth targets CUDA.** ROCm support has been partial and changing, so check its current state before planning around it rather than trusting this page.

So training means renting an NVIDIA GPU. A LoRA run on this dataset size is hours, not days, on a single 24 GB card. That is a modest cost, and it is worth stating that inference stays local afterwards: you train in the cloud once and serve the result on the pool.

## How to know whether any of it worked

The harness already exists, in `bench/` of the separate `ollama-stack` project on this machine, and it is four steps in increasing cost. Run them in order and stop at the first failure.

| Step | Script | Question |
|---|---|---|
| 1 | `fit.py` | Does it fit on an 8 GiB card, and at what context? |
| 2 | `prefill.py` | How fast does it read? |
| 3 | `menu_choice.py` | Does it choose what to research? |
| 4 | `full_analysis.py` | Does it drive the tool-calling loop with real prices? |

Three rules learned from running it, all of which cost a wrong conclusion first:

- **Speed proves nothing.** Every rejected model was faster than the one in production. `lfm2.5:8b` was the fastest ever measured here and still unusable.
- **Run behavioral tests more than once.** `menu_choice.py` reported 0 of 4 for `lfm2.5:8b`; a second batch at identical settings gave 2 of 4.
- **Check the completion share, not only the prompt tokens.** Four rejected models were caught by a collapse in prompt tokens. `lfm2.5:8b` read plenty and was caught instead by talking over it: 34-35% completion against the working family's 14-17%.

## Where to start

If you want to attempt this, do it in this order:

1. **Switch local models to `json_schema` structured output** and re-run step 4 on `lfm2.5:8b`. Hours of work, and it may end the project on its own.
2. **Move price fields out of the schema** and compute them in Python from multiples. Days of work, and it removes the failure rather than reducing it.
3. **Add trace capture to `UsageTracker`.** Do this early even if you never train, because a month of traces cannot be collected retroactively.
4. **Only then consider a LoRA**, with a rented NVIDIA card and a dataset filtered by graded outcome.

The honest summary is that steps 1 and 2 are likely to be enough, and that they are cheap. The reason to write down the training path anyway is that somebody will propose it first, since it is the more interesting idea — and it is the one most likely to spend weeks solving a problem that a schema change would have removed.
