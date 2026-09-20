---
paths:
  - "backend/services/analysis.py"
  - "backend/services/llm_usage.py"
  - "backend/services/llm_cost.py"
  - "TradingAgents/tradingagents/agents/**"
  - "TradingAgents/tradingagents/dataflows/**"
  - "TradingAgents/tradingagents/graph/**"
---

## A tool error goes to the model, not to the logs

**A raising tool used to end the analysis.** On 2026-09-02 that discarded two complete forty-minute runs: the model asked for an indicator called `macd_histogram` when the real name is `macdh`, and the error listing all thirteen valid names went to the logs instead of to the model.

Two things were wrong and the second was worse.

**A caller error counted as vendor ill-health.** The bad name raised a plain `ValueError`, the router caught it generically and recorded it against the circuit breaker. Five bad names opened yfinance's circuit at the third, and for the next five minutes *every* `get_indicators` call failed with "No available vendor" — including the valid ones. `CircuitBreaker`'s own docstring says only transient errors should open it.

So `BadVendorArgumentError` now marks "the request was wrong, the vendor is fine". The router **does not touch the breaker** for one and **does not fall through to the next vendor** — every vendor rejects the same invalid argument, so trying them in turn only wastes requests and buries the message.

**Every `ToolNode` sets `handle_tool_errors`.** The model is handed the message and calls again, which is how a tool-calling model is meant to recover, at a cost of one call against an analysis of twenty.

**The handler returns the message and nothing else.** No suggestion of what to try instead. A model told "that failed, try something else" invents a plausible substitute, and an invented answer that reads as data is exactly what disqualified four models in August. The vendor's message already names the valid values; anything past that is us guessing.

`backend/tests/test_tool_errors_reach_the_model.py` holds the guarantee in this repo, so a submodule bump that reverts it fails here rather than in a live run.

## Per-run cost telemetry

`backend/services/llm_usage.py` counts what each analysis spends: wall-clock seconds, LLM calls, and prompt/completion tokens, stored on `Signal` and shown in the Discord footer and the signal detail page. It exists to make the self-host-vs-cloud decision arithmetic rather than guesswork, which is why the token split by direction matters (cloud output tokens cost several times input ones).

Two things about it are easy to get wrong:

- **The counts come from the provider's `usage` block, never a tokenizer estimate.** An estimate is wrong by exactly the amount that matters once it is multiplied by a price per million.
- **`UsageTracker` attaches to the two LLM client objects, not to a `propagate()` argument** — `propagate()` accepts no callbacks, and every agent, the debate, the reflector, and the signal processor all share `graph.deep_thinking_llm` / `graph.quick_thinking_llm`. Attaching there is what makes the count cover the whole run. A per-run tracker is safe because `_build_graph` already builds a fresh graph per analysis.

An unmeasured run stores NULL, never 0 — a zero would read as a free run.

## The model invents price levels (important)

`gemma4-e2b` reasons acceptably in prose but does not reliably carry concrete figures into structured numeric fields. On 2026-08-06, 3 of 8 signals came back with fabricated entry / stop / target levels:

| Ticker | Real price | Model's entry |
|---|---|---|
| GOOG | $356.62 | $2,000.00 |
| VERI | $1.26 | $4.50 |
| VERI | $1.25 | $30.00 |

The numbers look like **prices the model remembers from training** — $2,000 is roughly pre-split GOOG, $30 roughly VERI's 2021 range. Two runs on the same stock the same day produced entries 24× apart, so it is invention, not stale data and not another ticker bleeding in. The market analyst's own report had the right prices throughout; the *trader* stage was simply never given a price.

**The root fix landed 2026-08-27: the model is no longer asked for a price.** `TraderProposal` has no `entry_price`, `stop_loss` or `target_price` field at all. It states two distances — `stop_atr_multiple` and `target_r_multiple` — and `resolve_levels` computes the prices from the verified close and ATR that `verified_levels_basis` returns as numbers. A field that does not exist cannot be filled from memory, which is stronger than any instruction not to.

The rendered markdown is unchanged, because `analysis._trade_plan_levels` parses the level lines out of it. Only who computes the number moved.

**The defenses below all stay.** They now catch a different class of error — a bad multiple, a missing basis, a level that survived one check and not another — rather than a remembered price, and they are what makes the new arithmetic safe to trust:

1. **The trader still receives the deterministic snapshot.** `build_verified_market_snapshot` (computed in Python from the same OHLCV, never by a model) goes into the trader prompt, which is what the model reasons over when choosing how much room the trade needs.
2. **`analysis._trade_plan_levels` discards levels far from the traded price**, using `max_level_deviation_pct` per horizon (swing 35%, position 70%). `risk_reward` and `expected_value_r` go out with them, since TradingAgents computes both *from* those levels. `win_probability` survives — it is the model's own estimate, not a derivation.

Defense 2 is the one that must never be removed. A prompt cannot make a 2B model reliable, and a fabricated stop is worse than no stop: the watchdog arms an alert at a price the stock may never reach, or fires one immediately.

3. **`analysis._levels_on_the_wrong_side` drops a stop at or above the traded price and a target at or below it.** Defense 2 asks only how far a level is from the price, never which side of it the level is on, and the gap between those two questions is wide: 14 of the first 42 signals — a third of the book — held a level on the wrong side while every one of them sat inside the deviation tolerance. The shape is always the same. The model proposes a pullback entry, draws its stop and target around *that* entry, and the pullback never comes: ZBH on 2026-08-12 wanted to buy $91.00 with a $90.76 stop and a $92.00 target while the stock traded at $97.89. All three levels are within 8% of the price and the plan is internally coherent — it is only the entry that never happened. Levels are read from the traded price forward, so a target under it is reached the instant it is stored and a stop over it triggers the same way. Nulling the stop also hands the signal to the ATR fallback in `_resolve_stop_loss`, which is how a Buy still ends up with a usable exit. Sell-ish decisions are exempt: this app is long-only, takes no action on them, and their levels point the other way by design.

Two one-time scripts cleared bad levels from rows written before defenses 2 and 3 existed: `scrub_implausible_levels.py` and `clear_wrong_side_levels.py`. Commit `99dc4f9` deleted both on 2026-09-01. If you write a cleanup like them again, leave graded signals alone. Grading read the target to decide `price_target_hit`, so a change to the target afterwards contradicts a verdict already given.
