---
paths:
  - "backend/services/analysis.py"
  - "backend/services/llm_*.py"
  - "backend/services/setup_check.py"
  - "TradingAgents/tradingagents/llm_clients/**"
  - "TradingAgents/tradingagents/graph/trading_graph.py"
  - "compose.example.yaml"
  - "docs/free-tier-gemini.md"
  - "ollama/**"
---

<!-- Moved from CLAUDE.md on 2026-09-13. This file loads when Claude reads a file that matches paths. -->

## LLM provider switching

`TradingAgents/tradingagents/llm_clients/` is a full multi-provider abstraction (ollama, google, openai, anthropic, azure, bedrock, etc.) — switching providers is a config change, not a code change.

**Two naming layers, and confusing them wastes an afternoon.** The app reads `TRADINGAGENTS_LLM_PROVIDER`, `TRADINGAGENTS_LLM_BACKEND_URL`, `TRADINGAGENTS_DEEP_THINK_LLM` and `TRADINGAGENTS_QUICK_THINK_LLM`. The short names below are **compose-level aliases only** — `dockge/trading-experiment.compose.yaml` and `compose.example.yaml` map them, and nothing else does. Anywhere the app itself prints advice (the `/setup` page's `fix` lines, an error message), use the long names, because that text is read by someone who may not be using either compose file. A `LLM_BACKEND_URL` shipped on the setup page on 2026-09-10 for exactly this reason and did nothing at all.

The aliases in the compose files:

- `LLM_PROVIDER` → `TRADINGAGENTS_LLM_PROVIDER` (defaults to `ollama`)
- `LLM_MODEL` → both think stages (they share one value; splitting them needs a small code change in `TradingAgents/tradingagents/graph/trading_graph.py`)
- `GOOGLE_API_KEY` and `OPENAI_COMPATIBLE_API_KEY` pass straight through

**`compose.example.yaml` is tracked; `dockge/` is not.** The `dockge/` copy is this machine's working template and is gitignored, so anything written for a self-hoster must point at `compose.example.yaml` — README and two docs pages pointed at the untracked one until 2026-09-10.

**The model is also a runtime setting.** `analysis.get_model()/set_model()` store it in `BotSetting` under `llm_model` and `_build_graph()` applies it to both think stages, so the settings page switches models without a redeploy. That matters for the record: flipping the env var and redeploying would change the model between one morning and the next. The env var above is only the default for an unset setting. `analysis.model_choices()` lists what the endpoint actually serves, via the OpenAI-compatible `/v1/models` route (ollama serves it too), and an unreachable endpoint degrades to a free-text field rather than blocking a save. Every `Signal` records `model`, and the scorecard's `by_model` breakdown is the point of the whole mechanism — switching models teaches you nothing if the win rates blend.

**The model to run is `gemma4-e4b-qat-128k` (measured 2026-08-26/27).** It is what the fresh 2026-09-01 deployment should start on, because the agent chooses its own research now and the candidate-menu decision is what e4b does and e2b does not. `gemma4:e4b-it-qat` (8.0B raw, 4B effective) runs **100% on the GPU at the full 131,072-token context**, using 3.7 GiB of the 8 GiB card. Built here as `gemma4-e4b-qat-128k`. `gemma4:12b` does not fit at any context — two to five layers always land on the CPU, and the QAT build does not rescue it — and `gemma4:26b` (MoE, 4B active) is far worse. Full numbers in [ollama/README.md](../../ollama/README.md).

**Take the QAT tag, not the default one.** `gemma4:e4b-it-qat` beats plain `gemma4:e4b` where it has been measured repeatably: 1,122 tok/s prefill against 861 (30% faster, and prefill is what sets run time), 6.1 GB on disk against 9.6, slightly less VRAM, same 43/43 placement, same menu behaviour (4 of 4 each). Quantization-aware training puts the 4-bit rounding inside the training loop rather than applying it to a finished model, so it should also be the more accurate of the two. `gemma4:e4b-it-q4_K_M` is not a third option — same manifest id as `gemma4:e4b`.

**Measure prefill on a long, cache-busted prompt.** A 30-token prompt measures per-request overhead, not prefill, and ranked QAT *below* plain e4b — the exact opposite of the truth. Repeating an identical prompt is as bad: Ollama's prompt cache returns the second one in 0.03s, which reads as 219,000 tok/s. Use several thousand tokens with a unique prefix per run.

The reason to switch off e2b is not speed; e4b is about **twice as slow** (13m12s alone, 17m26s when two run together, against e2b's 7m15s). It is that e4b uses the candidate menu and e2b does not. On the identical deployed prompt, both e4b builds chose research in 4 runs of 4, picking 4-5 names of 15 with a stated reason each; e2b managed 2 of 4, and had answered "no research" three real mornings running. It also passes the tool-calling test that disqualified four earlier models: 18-26 calls and **99-174k prompt tokens against the baseline's 103k**, which is the right direction — every model that failed here was faster because it never fetched the data. The prices are real too: AAPL's actual $313.45 close appears verbatim in three of four runs.

**One structured-output failure per run is normal for the e4b family** — both builds logged one, at the Sentiment Analyst or Trader stage, and both recovered by retrying as free text. Do not read that as a QAT defect; plain `gemma4:e4b` does it too. It is a different thing from the four-per-run failures that disqualified `llama3.2:3b` and `phi4-mini`. Treat a count above one, or a failure that does not recover, as a regression.

**The same ticker on the same day gave Underweight, then Overweight.** Plain e4b gave Underweight, then Hold. That is temperature 1 — Gemma's recommended setting, which `gemma4-e2b-96k` has been running at in production all along — so it is not new with e4b and not a reason to tune sampling down. It does mean a single analysis is one sample, and the Scorecard's `by_model` breakdown is the only honest way to compare two models.

**Sampling stays at Gemma's published values** — temperature 1 / top_k 64 / top_p 0.95 (<https://ollama.com/library/gemma4>). `gemma4-e2b-96k` briefly ran at 0.15/20/0.9 on 2026-08-26 to make the agent's research choice consistent; that treated a symptom of the 2B model's capability as a sampling problem and is reverted. Separately and still true: the app talks to Ollama over `/v1/chat/completions`, which **silently ignores `temperature` in the request body**, so `TRADINGAGENTS_TEMPERATURE` does nothing and a Modelfile is the only channel that reaches the model.

**Earlier and rejected models are recorded in [ollama/README.md](../../ollama/README.md)**, and the `model-change` skill holds the test that separated them. `gemma4-e2b-96k` ran from 2026-08-11 to 2026-09-01. Five models were rejected in August for broken tool calling. The `lfm2.5:8b` rejection was later traced to defects in this app, which casts doubt on the other four. Treat a vendor's tool-calling claim as a reason to test, never as evidence. `qwen3:latest` stays the known-good slow fallback. Do not run stock `gemma4:e2b` at its default context: its tool-call history is cut off and its reasoning loop does not end (`GraphRecursionError`).

Analysis speed is what makes the 1-2 week trade horizon practical — signals have to be produced faster than they expire — so treat a regression here as a correctness problem, not a performance one.

**Gemini capacity note (2026-07-30):** `gemini-3.5-flash` returned 100% persistent `503 UNAVAILABLE` ("high demand") over ~12h straight — looked like a tier/capacity issue with that specific just-GA'd model, not a transient blip. `gemini-3.1-flash-lite` worked reliably (16/16 calls succeeded), ran a full analysis in <1 min vs Ollama's ~15 min, at roughly $0.02–0.08/analysis. If revisiting Gemini, start with `flash-lite`, not `3.5-flash`.

### Gemini's thinking and rate limits (2026-09-13)

**Gemini thinks only at a stated level.** At its default level, `gemini-3.5-flash-lite` returned zero thinking tokens. Set `TRADINGAGENTS_GOOGLE_THINKING_LEVEL` to `low`, `medium` or `high`; on one small trading question they cost 386, 490 and 702 thinking tokens. `analysis._build_graph` turns on `include_thoughts` for every Gemini client, and `llm_content` reads the thinking in a callback, before TradingAgents' Google client flattens the answer to a string. What comes back is Google's summary of the thinking, not the raw reasoning an Ollama model returns.

**The throttle wraps `client.models.generate_content` for Gemini**, because that client has no `create`. Gemini sends no budget headers, so a deployment states its limits: `LLM_REQUESTS_PER_MINUTE`, `LLM_TOKENS_PER_MINUTE`, `LLM_REQUESTS_PER_DAY` and `LLM_DAY_TIMEZONE`. The day's count lives in `BotSetting` under `llm_requests_today`, so a restart does not start it again at zero. Research that cannot finish inside the day's remaining requests is refused in `agent._research_and_report`, before it runs or is charged. **Do not move that check into the analysis itself:** `run_analyses` swallows an analysis failure, so the agent would never learn why its research did not come back.

**Do not test Gemini from the host shell.** The host's Python resolves Google to an IPv6 address that does not route here, and the call hangs in `SYN-SENT` with no error (found 2026-09-13). The containers use IPv4. Run a check in a throwaway container from `trading-experiment:local` with `backend/` mounted read-only over `/app/backend`.

**Google's model list is counted as a request and cached for six hours.** The setup check and the settings page read it, and both load often. Google probably does not count a model list against a key's limits: one timed list call at 05:39 UTC on 2026-09-13 did not appear in the console's per-minute or daily counts. One call is not proof, so `_google_models` still goes through `llm_throttle.count_request()` until a full day of page loads with no agent calls confirms it. **Once confirmed, remove that call**, because otherwise the app's daily count runs a few requests higher than Google's. A failed list is not asked for again for five minutes either way.

### What Gemini actually bills (measured 2026-08-22 and 2026-08-25)

**`gemini-2.5-flash-lite` is retired.** It still appears in the models list, and calling it returns `404 NOT_FOUND`: "no longer available to new users. Please update your code to use `models/gemini-3.5-flash-lite`". Both `gemini-3.5-flash-lite` and `gemini-3.1-flash-lite` work on a current key, so 3.1 remains the fallback the capacity note above points at.

Speed is not in doubt and is the reason to care: a Flash-Lite analysis takes **1.2-1.6 minutes** against gemma4-e2b-96k's **7-10**, on the same tickers the same morning. A nine-ticker sweep is ~11 minutes of wall clock against ~85 of GPU.

The cost is less settled.

**Always price input and output separately. Never use a blended rate.**

List is $0.30 per 1M input and $2.50 per 1M output — an 8x gap — so a blended figure is a function of the completion share, not a property of the model:

| Completion share | Implied blended rate |
|---|---|
| 8.5% (a Flash-Lite sweep) | $0.487 / 1M |
| 16.0% (gemma4's history) | $0.652 / 1M |
| 25% | $0.850 / 1M |

This is not academic. An earlier revision of this file derived $0.399/1M from a single Flash-Lite run and applied it to gemma4's 11.49M-token history, which runs at 16% completion. That produced **$4.59** where the correct split-rate answer is **$7.49** — 39% low, and it replaced a figure that had been right. Blending was the entire error.

**Two measurements, and they disagree.** Priced at list, with rates split:

| | 22 Aug — 1 analysis | 25 Aug — 9 analyses |
|---|---|---|
| Prompt / completion | 90,713 / 9,629 | 911,876 / 84,189 |
| Completion share | 9.6% | 8.5% |
| List cost | $0.051 | $0.484 |
| Billed | $0.04 | $0.50 |
| **Billed vs list** | **78%** | **103%** |

The first reading looked like an implicit-caching discount and was written up here as one. The second says otherwise, and three explanations fit without being distinguishable from the dashboard: a single $0.04 line item rounds coarsely and may simply have displayed $0.0513 as $0.04; caching may behave differently for one analysis than for nine dispatched together; or the $0.50 may carry trailing usage that had not settled on the 22nd.

**So estimate at list.** It sat within 3% of the larger sample and does not rest on caching behaviour nobody here has verified.

Projections for the current 9-ticker watchlist, at the 25 Aug measurement: **$0.056/analysis, $0.50/sweep, $2.50/week, $10.58/month.**

Note the month figure **exceeds the $10/month billing cap** on the account, and that ceiling is now the binding constraint rather than a footnote. There is no comparison sweep any more, so pointing the model at Gemini means every analysis goes there — the full sweep, every day. Adding tickers trips the cap sooner. Intraday triggers are the cheap part.

**Against self-hosting.** Running the whole history to date on Flash-Lite — 9,656,261 prompt + 1,837,356 completion — would have cost **$7.49** at list, against **$0.26** of marginal GPU electricity (11.76 GPU-hours x 100 W at $0.22/kWh). That is **29x**.

But marginal is the right comparison only because the host is a shared home server that would be powered anyway. Its wall meter read 22.48 kWh for 21 days of August — **$4.95, at an average draw of 44 W** — of which the analyses were 5%. Load the idle 42 W onto the trading bot and the two costs converge; leave it as the shared overhead it is, and self-hosting wins by an order of magnitude. Cost is therefore not the reason to move to a vendor. Speed is.

Also worth carrying forward: **a single-ticker sample understates the average.** The 22 Aug GOOG run was 100k tokens; the nine-ticker sweep averaged 111k, with several tickers at 106-137k. Estimating a sweep from one small run was optimistic by about 10% before any pricing question.

**The comparison that would have settled this never finished.** It ran from 2026-08-25 and the experiment ended on 2026-09-01 with the reset, so the numbers above are what exists: two measurements a week apart that disagree by 25% on billing, and a clear answer on speed. Re-derive them before switching, and expect to have to run the switch itself as the measurement now that a paired comparison is no longer possible.
