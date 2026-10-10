# The analyst experiment — history

This page is history, not the current agent. It records a second deployment that ran from 2026-08-26 to 2026-08-29, on the sandbox's margin account, beside the live agent from [the two-book experiment](https://github.com/nandyalu/ten-acre/blob/main/docs/two-book-experiment.md). It is the direct ancestor of the agent that runs today: it was the first to choose its own tickers, pay for its own research, and drop names it no longer wanted.

**Both deployments ended on 2026-09-01** and merged into the single agent of experiment 1 — see [the experiment 1 journey](https://github.com/nandyalu/ten-acre/blob/main/docs/experiment-1-journey.md) for its record.

The entries below keep their original two forms: short changelog lines first, newest first, the same as the current journal; then the longer day-by-day story of the same period, oldest first, written as it happened.

---

## The changelog entries

**2026-08-29 — two defects the first real trade exposed.** Neither changes what the agent may decide. Both fix a number Python got wrong on its behalf, and both were invisible because both produced figures that look entirely reasonable.

**Exits now come from the newest signal for a ticker, not the oldest.** `stops`, `targets` and `signal_by_ticker` were dict comprehensions over the signal list, and a dict comprehension keeps the *last* value it sees. `get_recent_signals` returns newest-first, so the oldest signal won every time a ticker had been analysed twice. On 2026-08-28 the agent bought 260 SMCI and rested the 27th's levels — a 34.16 stop and a 45.21 target — when that morning's analysis had said 34.04 and 49.51. The target was $4.30 out. Nothing reported it, because a stale level is still a real level from a real signal. `_newest_signal_per_ticker` now sorts by `(signal_date, id)` rather than trusting the caller's order, since a "keep the first one seen" fix would invert silently the day that ordering changed.

**A buy is screened at its entry limit, not at the quote.** The order leaves as a marketable limit `ENTRY_LIMIT_BUFFER_PCT` through the offer, so checking the raw quote approves an order the account cannot pay for. The same SMCI buy passed at 260 x $38.46 = $9,999.60 against $9,999.70 of cash, filled at $38.49 for $10,007.40, and left the book at **minus $7.70**. `agent_book.entry_limit_price` is what the check now uses. It **refuses rather than resizes**, like every other check there: shrinking would quietly turn the model's decision into a different one, and the record would then describe a strategy nobody chose.

**Neither addresses the concentration.** That buy was 100% of the book in one name, and the agent asked to do the same with NVDA the day before. There is no position-size cap, and adding one would be a change to what the agent may decide rather than a correction to arithmetic — so it belongs in its own entry, with its own reasoning, if it is ever added.

**2026-08-27 — the agent chooses what to stop watching, and the watchlist is capped.** A `side: "untrack"` action, a watchlist section in the prompt, and a ceiling of 12 tracked tickers.

Before this the watchlist only ever grew. `_commission_research` called `db.add_to_watchlist` and nothing in the agent could remove one — `/untrack` was manual and was the only route. That was survivable at `gemma4-e2b-96k`'s 7 minutes an analysis and stops being survivable at `gemma4:e4b-it-qat`'s 17.4: the sweep runs 11:00 UTC and `earnings_check` puts its own analyses on the same pool at 13:00, so three concurrent analyses fit about 20 tickers in the window. At the 4-6 names a run commissions, the ceiling arrives in about four days. **`_MAX_RESEARCH_PER_DAY` does not help, because the limit is cumulative rather than per-day** — the same reason a daily spending limit does not stop a subscription.

**The cap is 12, not 20.** Twenty is what the window fits with nothing going wrong. Twelve is four waves and leaves the second hour as margin for a slow run, a retry, or a morning when the live sweep is contending for the same cards.

**A held ticker can never be untracked**, and that is enforced in Python rather than asked for in the prompt. An agent that could stop watching a position it still owns would lose the analysis that finds its exit, and the daily analysis of a holding is exactly what the "you own the cost of finding your own exit" charge pays for. The same reasoning as refusing a sell of shares that are not held: the model decides what it wants, Python refuses what must not happen.

**Untrack is free and is ordered, like a sell that funds a buy.** Listing an untrack before a research frees a slot for it in the same pass, so a full watchlist is never a dead end — the agent trades one name's coverage for another's rather than waiting a day. The prompt says so explicitly, because the identical wording had to be added for sells before the model worked out it could do the same thing with cash.

**The watchlist is now in the prompt**, with its size and its cap, split into what is held and what is only being watched. Required by the action, and for the same reason holdings had to start showing their resting exits on 2026-08-25: the agent cannot sensibly drop something it cannot see, and a cap it is not shown is a rule it can only discover by being refused.

**2026-08-26 — the agent chooses what to research.** A `side: "research"` action, and a menu of screened candidates in the prompt with what an analysis costs. This is the point of the analyst experiment: the live agent is measured on decisions given a fixed watchlist, and this one decides what is worth looking at at all. Commissioning a ticker adds it to the watchlist — which *is* the commission, since the morning sweep reads the watchlist. **The charge lands when the analysis runs, not when it is commissioned.** Billing at both ends charged a commissioned ticker twice, once for asking and once for the work; `propagate_ticker` already bills every ticker the sweep touches, including the held ones nobody commissioned, so that is the single place. The cost of this is that the agent can commission slightly more than its cash on a day the sweep has not happened yet — bounded by the daily cap to cents against a four-figure budget, and a far smaller problem than double-billing.

The menu is never free-form. A model naming its own tickers invents symbols, reaches illiquid things with no price data, and picks the day's pump; `candidates.py` already screens for liquidity and excludes anything up more than 30%, which matters because a raw screen once returned a stock up 927% and a price floor does not catch that — the pump is what lifted the price over the floor.

The answer arrives **tomorrow**, not in the same pass. That is the honest shape: an analyst does not hand over a report the instant you ask, and same-breath research would let the agent act with no cost to being wrong about what was worth studying. A daily cap of 15 applies regardless of cash, because money does not model time and the sweep has to finish before the open.

The whole section only appears when research is actually charged for. A menu the agent can take from for free is just a longer watchlist somebody else chose.

---

## The day-by-day story

### 2026-08-26 — The analyst starts

A second deployment, on the sandbox's margin account, with $10,000 and a $0.05 charge for every analysis it runs. Separate database, separate Discord channel, separate book from the live agent, which continues untouched on its $1,000 cash account.

**Why a second deployment rather than changing the first.** We measure the live agent on decisions it makes from a fixed watchlist. We measure this one on what to look at, what to buy, and when to exit, under a budget that research draws from. That is most of what an analyst actually does, and they are different experiments. Running both in one book would answer neither.

**Why research costs money.** Analysis is free to the live agent, so "what is worth looking at" is not a decision it makes, and not one anyone can grade. A price makes it one. It also makes the app's central question honest: whether the model pays for itself has to include the cost of running the model.

We charge the mechanical baseline too, because it reads the same analyses. We do not charge SPY, because it reads nothing.

**Why $0.05.** Roughly what an analysis costs on a paid vendor at one debate round. On $10,000 at nine analyses a day that is a 1.13% annual hurdle — a real constraint that still leaves the test fair.

It is deliberately *not* a passthrough of cost. Local electricity for the same analysis is $0.003, seventeen times less, so any single price is a choice about scarcity rather than an accounting fact.

**What we got wrong first.** We built the plan on four debate rounds, which we believed cost about $0.10 and produced better decisions. Measuring it disproved both halves:

- **Four rounds cost 2.3x the wall clock and 2.6x the tokens.**
- **The decisions came out identical in distribution** across nine tickers — 78% Hold either way.
- **Four decisions changed and cancelled out**, two becoming more directional and two less.
- **The same ticker at the same setting returned different answers an hour apart**, which meant we could not attribute any of the changes to rounds at all.

A single earlier sample had looked like evidence. It was noise, and we had already written it into the plan as a finding before the nine-ticker run corrected it.

Three problems appeared on the first deploy. None was obvious:

- **Dockge pulls a local-only image** and fails.
- **Dockge turns a named volume into a root-owned bind mount** that a container running as `appuser` cannot write.
- **Webull allows one live trade stream per *app key***, not per account, so two deployments sharing a key cannot both have one.

**What it cannot do yet.** Choose its own tickers. The candidate menu is the next thing to build. Until it exists the watchlist is empty and the agent has nothing to decide about. That is the whole experiment, so nothing here means much until it lands.

### 2026-08-27 — The model was the problem, and the watchlist only grew

**The candidate menu shipped yesterday and the agent would not use it.** The entry above calls the menu the next thing to build. It shipped hours later, which is what writing things down as they happen looks like.

Then the agent answered "no research" on three real mornings. The feature worked. The experiment sat idle behind it.

We suspected the prompt, and we were wrong. We replayed the deployed prompt against each model directly, unchanged:

| Model | Chose research | What it picked |
|---|---|---|
| `gemma4-e2b-96k` | 2 of 4 runs | 3 names of 15, twice; nothing, twice |
| `gemma4:e4b-it-qat` | **4 of 4 runs** | 4-5 names of 15, with a reason for each |

The wording was never the problem. A 2B model follows the instruction sometimes and not reliably, and no rewrite fixes that.

**We lost a day to the wrong explanation first.** When the agent researched on only half of identical passes, we read it as a sampling problem. Gemma's recommended temperature is 1, which looked too loose for a trading decision, so we rebuilt the production model at 0.15.

That treated the model's capability as a sampling problem, and it moved the live deployment off Google's documented settings in the middle of an experiment. e4b at stock sampling then chose research 4 times of 4, which exposed the error. We reverted it, and the reasoning now sits in the Modelfile so nobody derives it again.

**So the analyst runs a model that is twice as slow, on purpose.** We measured four gemma4 variants on the 8 GiB cards:

| Model | Fits the card? | The detail that decides it |
|---|---|---|
| `gemma4:e4b-it-qat` | **Yes** — 43 of 43 layers on the GPU, 3.7 GiB at the full 131,072-token context | Reads its prompt 30% faster than the `e4b` tag, and downloads 6.1 GB instead of 9.6 |
| `gemma4:e4b` | Yes, same placement | Slower to read, and a larger download for no gain |
| `gemma4:12b` | No | Two to five of its 49 layers always run on the CPU, and the quantization-aware build does not change that |
| `gemma4:26b` | No, and far worse | All 18 GB of experts still have to be stored somewhere, despite only 4B active parameters |

An analysis goes from 7 minutes to 17.4. That buys the decision this experiment exists to measure, so it is worth paying. It also exposed a problem nobody had noticed.

**The watchlist only ever grew.** Commissioning research added a ticker. Nothing in the agent removed one. `/untrack` was manual, and it was the only way out.

At 7 minutes an analysis, nobody would have noticed for months. At 17.4 the sweep window holds about twenty tickers, and that window runs from 11:00 UTC only until the earnings check takes the same cards at 13:00. The agent picks four to six names a run, so it reaches the ceiling in about four days.

The daily research limit does not help. The limit is cumulative rather than per-day — a daily spending limit does not stop a subscription.

**So dropping a ticker is now the agent's decision too.** It now decides all five:

- what to watch
- what to research
- what to buy and sell
- where to move its exits
- what to stop watching

Untracking frees a slot the way selling frees cash, and the order matters the same way: list the untrack first and the research after it. The prompt says so plainly. The identical wording had to be added for sells before the model worked out it could reorder, so we did not assume it would generalize this time.

One thing the agent may never do is untrack something it holds. A position nobody analyses has nothing looking for its exit. Python enforces that rule rather than the prompt requesting it, as with every limit that has to hold.

**Building a feature is not the same as the model using it.** The menu had just taught us that, so we probed this one before calling it done. We gave the model a full watchlist and two tempting candidates.

It untracked and then researched, in the right order, in three runs of four, and it chose the penny stock to drop both times. The fourth run asked for two researches without untracking anything, and Python refused both. That refusal exposed a second problem: the retry's only advice was about cash, which does not help a full watchlist. We matched the advice to the refusal, and the retry then recovered in three runs of four.

**We found a crash by accident, and it was waiting for the first trade.** Rendering the new watchlist section raised a formatting error. The holdings loop used a local variable named `price`, which replaced the research-price parameter with a string, and the menu section then formatted that string as a number.

The bug needs a holding and a menu in the same prompt. The analyst holds nothing yet, so it had never fired. It would have crashed the agent's first decision pass after its first buy.

**Two things this project had written down were also wrong.** We had repeated both confidently, including earlier the same day:

- **The proxy does not hold a card for a whole analysis.** We believed it did, which would make its ten-minute timeout dangerous against a seventeen-minute run. It releases the card after a single call: ten concurrent requests against seven cards all completed, the slowest in 38 seconds.
- **The sweep has two hours before the open, not one.**

**Seven cards now split four to the live book and three here.** That is temporary. Once the Gemini Flash-Lite comparison reports, the analyst should take five and the live book two, because this is the long-running experiment and the live book runs a model less than half as slow.

The change waits for that comparison, which only reads correctly if the live deployment keeps analysing at the speed its graded signals were produced at. The watchlist ceiling of twelve then has to be measured again at five concurrent rather than scaled, because analyses on these cards compete for host memory bandwidth and the time does not divide evenly.

**Still zero decision passes and zero trades.** Everything above is preparation. Tomorrow morning is the first time the agent tests any of it.

### 2026-08-27, later — The agent bought its first research, and we stopped asking the model for prices

**The entry above ends by saying tomorrow morning is the first test. It was this morning.** The sweep ran at 11:00 UTC and the agent commissioned three analyses — NVDA, CRM and SMCI — for $0.15. That is the candidate menu working for the first time, after three mornings of "no research", and it is the reason we accepted a model twice as slow.

Everything below happened in the hours around that, so this entry is the same day as the one before it.

#### A model that looked perfect and was not

`lfm2.5:8b` arrived claiming tool calling as a strength. It is the fastest model that has ever fit these cards: 2,138 tok/s reading against `gemma4-e4b-qat-128k`'s 1,126, all 25 layers on the GPU at the full 128k, every weight on the card. On speed alone it was the obvious upgrade.

Then it invented every price in two runs out of two. AAPL closed at $313.45; it cited $188-196 in one run and $144-150 in the other — roughly the stock in 2024, then in 2023. A stale cache would be wrong the same way twice, so that is recall from training.

Three things came out of rejecting it, and the third is the one worth keeping:

- **The tell we had been relying on did not fire.** The four models rejected before this were caught by a collapse in prompt tokens, because a model that never fetched has far less to read. This one read 77-96k and invented anyway. What caught it was the **completion share** — 34-35% against the working family's 14-17%. It read enough and then talked over what it read.
- **A vendor's tool-calling claim is a reason to test, not evidence.** A benchmark asks whether a model picks the right function from a list. This pipeline asks whether it carries the returned number into a field twenty calls later.
- **The speed was real and bought nothing**, which is the fifth time that has been true here.

#### So we stopped asking models for prices at all

Rejecting a fifth model made the pattern impossible to ignore. Every one of them failed the same way, and we had been treating it as a knowledge problem — the models quote prices from two years ago, so they must need newer data or a retrieval step.

**They already had today's price.** `lfm2.5:8b` read 96,000 prompt tokens and cited nothing near the real close. Back on 2026-08-06, gemma4-e2b's market report carried the right prices in the same run where its trader wrote $2,000 for a stock at $356.62. The model does not need to know the price. It needs to copy the price it was given.

That reframing made the fix cheap rather than expensive, and two of them landed today.

**The server now constrains the answer.** Structured output on a local model asks for `json_schema` instead of function calling, so ollama constrains the sampler and a malformed answer stops being possible rather than becoming less likely. `lfm2.5:8b` went from 4 structured-output failures per run to 0 in each of two; the analyst's own model went from 1 per run to 0, and has logged 0 across every run since.

**The trader has no price field.** `TraderProposal` no longer carries entry, stop or target. The model states two distances — how far the stop sits in ATRs, and how much the trade aims to make as a multiple of what it risks — and Python computes the levels from the verified close and ATR. A field that does not exist cannot be filled from memory, which is stronger than any instruction not to.

SMCI is the first signal recorded that way, and the arithmetic is checkable from the row itself. The model asked for 1.5 ATRs and 2.0R. The stored risk is $37.85 − $34.16 = $3.69, and $3.69 ÷ 1.5 = $2.46, which is SMCI's actual ATR. The reward is $7.36, exactly twice the risk.

Compare the two analysed before the rebuild. NVDA came back with no entry at all, so its stop fell to the ATR fallback. CRM's levels were the model's own — plausible, and *checked* afterwards rather than correct by construction. SMCI's cannot be wrong in that way.

#### The market report that was not a market report

Two runs came back with a market report of 532 and 0 characters, against about 5,000 before. It looked exactly like the change we had just made.

**It was not.** The traces of every run that day showed the market analyst failing intermittently in two ways, including once before any of this existed. Reading the record is what settled it, and the trace capture built earlier the same day is the only reason there was a record to read.

The cause is one line. An analyst decides it is finished when the model returns no tool calls:

```python
if len(result.tool_calls) == 0:
    report = result.content
```

That reads two things as one. "I am finished, here is the report" and "I tried to call a tool and typed it as prose" both arrive as an empty list, so a model narrating its intent gets the narration filed as the finished report. Our own analyst had a 532-character market report reading "I will call `get_stock_data` first", and nothing logged a problem.

We built three fixes and measured them one at a time. Two survived.

| Fix | What it does | Measured | Kept |
|---|---|---|---|
| Detect | Retry when the answer is ungrounded | 4 of 4, from 0 of 3 | Yes |
| Prompt | Tell the model to call, not describe | 13 of 17 either way | **No** |
| Salvage | Execute a written-out call | Recovered the one real case in every trace | As a fallback |

**The prompt fix was the cheap idea and it did nothing.** "Call the tools. Do not describe the call you are about to make" measured 13 of 17 with it and 13 of 17 without. It is reverted, and the number to beat is written where anyone would look before proposing it again. Had we not measured it separately it would have shipped and been credited with the detection fix's result.

**Detection needed two checks, and the obvious one was weaker.** Reading the text catches a fenced JSON block naming a tool. It does not catch a model that writes "### Phase 1: Detailed Swing Trade Planning" — prose naming no tool, and indistinguishable from a report by content alone.

What gives that one away is the conversation rather than the words: if nothing ever returned a tool result and the model is not asking for one, the answer was composed from no data. With only the text check the live test recovered 3 of 6 and the detector never fired once, so those three were luck. With the structural check it is 4 of 4.

**Two things we learned about the stack.** Ollama ignores `tool_choice` — sending `required` behaves exactly like `auto` and like sending nothing — so the standard lever for forcing a call is not available here. And the model is not incapable: asked directly for AAPL's price data it called correctly every time, and asked to think aloud about its plan first it narrated every time. The prompt decides it, which is why the prompt fix looked so promising.

#### What the app now keeps

Every LLM call of every analysis is written to disk, about 0.4 MB a run. **A dataset of past runs cannot be collected afterwards**, and five rejected models make a fine-tune worth leaving the door open to, so the capture exists before anyone has decided to attempt one. `Signal.trace_id` joins a trace to the signal it produced, and that signal gets graded weeks later — which means a future training set can keep only the runs the market agreed with.

It has already paid for itself twice today, as the only way to tell an intermittent market-analyst failure from a regression we had just caused.

#### Where this leaves the experiment

Three tickers tracked, $0.15 spent, and no trades yet. The agent sees these three signals for the first time at 13:35 UTC tomorrow, and that is the first pass with the whole loop closed: choose what to research, read the answer, decide what to do about it.
