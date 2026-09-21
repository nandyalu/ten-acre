---
paths:
  - "backend/scripts/probe_prompt.py"
  - ".claude/skills/probe-the-prompt/**"
---

# The probe log: what the model actually reads

`agent.md` holds the contract — what the agent is shown and what Python refuses. This file holds the evidence for whether each part of it is read, and it was split out of `agent.md` on 2026-09-20 because a session that edits the agent needs the contract, and a session that probes needs the results.

**Presence is not proof of use.** Every section below rendered correctly and passed its tests. The only question this file answers is whether the model's own reasoning shows it read them. How to run a probe and how to read a result are in the `probe-the-prompt` skill; do not copy that here.

## The verdict, in one table

| Prompt section | Samples | Verdict |
|---|---|---|
| Early wake, and the ask for a new note | 4 of 4 | **Read** |
| A worked example of a good wakeup note | 4 of 7 | **Read**, and it raised the floor |
| Persistent memory notes | 3 of 4, conditional | **Read when the note bears on the decision in front of it** |
| The holdings price-range column | 0 of 9, three placements | **Unconfirmed.** Kept |
| The levels line on a read | 4 of 4 | **Read** |
| The four analyst-report tables | 0 of 4 | **Unconfirmed.** Kept |
| The two full plans on a read | 2 of 4 | **Read**, and they caught a real contradiction |
| What a Hold means, rewritten 2026-09-19 | 4 of 4 | **Read**, and acted on |
| Size a position from its stop | 4 of 4 | **Read**, with one wrinkle |
| Limit orders, unannounced | 0 of 8, two models | **Unconfirmed** |
| Limit orders, announced by a change note | 2 of 4 | **Read** |
| Limit orders, instructed by name in the wakeup note | 1 of 4 per model | **Reachable, not reliable** |
| The fetch allowance, reworded 2026-09-19 | 0 of 4 mentioned a limit | **Fixed the rationing** |
| `ONE_ROUND_LEFT` / `NO_ROUNDS_LEFT` | 2 of 4 | **Read** |
| The `fundamentals` fetch | 1 of 4 called it, 0 of 4 quoted it | **Unconfirmed.** Kept |
| The closed-market rule and its `adjust` exemption | 6 of 7 restate it | **Read.** The behavioural claim is unproven |
| Alerts and "what you just did", above the tables | 4 of 4 after the move | **Read**, and the placement is why |
| The tool channel, end to end | 4 of 4 | **Works** |
| Every section under a `##` heading, with a rule between | 7 before, 7 after | **No regression.** Every difference is inside temperature-1 noise |

## Read, and the evidence for it

**Early wake (2026-09-13).** 4 of 4 samples kept 9:25 AM, a time that appears only in that block, and 4 of 4 wrote a new note. Only one of the four mentioned what woke it, so **the ask is what does the work, not the reason line**.

**A worked example beats a prohibition.** The rule told the agent not to spend the note on prices the next prompt already carries, and two of seven runs wrote "my cash is critically low" anyway. Two examples of a good note — *"ruled out HPE at a $59.83 entry, worth another look under $56"* — moved notes naming a concrete price level from **0 of 7 to 4 of 7**, and notes stating a condition from 3 to 5. It did not stop the balance-restating (still 2 of 7); it raised the floor of everything else.

**Persistent memory notes, on `gemini-3.5-flash-lite`, 2026-09-16, and the finding is conditional.** Turn 1 used the real live book — market closed, $43 cash, one holding with no stop resting — and 0 of 4 samples engaged with two seeded notes ("avoid adding to a losing position", "trim winners over 25% of the book") at all. Every sample instead fixed the holding's UNSET stop, the one obvious job in front of it. A second run built the scenario by hand: market pinned open (`market_clock.now_et` and `watchdog.is_us_market_hours` both patched to a fixed mid-session timestamp), $2,000 free cash, no holdings, and one note — *"Skip INTC — stopped out on it twice this month on the same breakout setup"* — naming a ticker with a live, affordable Buy signal already in the signals table. **3 of 4 samples cited the note by name, unprompted, as the reason to pass**: *"persistent memory dictates staying away from INTC after multiple stopped-out breakouts,"* *"keeping persistent memory of the INTC breakout failure rule,"* *"following persistent stop-outs on INTC per memory rules."* None of the three phrases is copied from the note, which is the paraphrase test this file uses elsewhere. The fourth sample never mentioned INTC either way — inconclusive, not a miss. **Read the two runs together: the section is read when the model has a decision in front of it that the note bears on, and skipped when something more urgent has already claimed the pass.** Do not treat a quiet run on a crowded book as evidence the feature does not work — rebuild a scenario with a real, affordable, relevant signal first. **2026-09-19, one oddity:** 1 of 4 samples appended `{"side": "memory", "action": "clear"}` with no memory note on file to clear. Harmless, and a hint that the rule reads as an instruction rather than an option.

**The levels line on a read (2026-09-19).** 4 of 4 samples told the app's computed stop from the analyst's own — *"the table contradicts the text with the Stop at $100.20"*, *"the app-computed ATR"* — and 3 of 4 chose the table's. The fourth chose the text's knowingly, which is what the line asks for; see the sizing wrinkle below for what that cost.

**What a Hold means, rewritten 2026-09-19.** A copy of the live book, the clock pinned to Friday 11:15 AM, one INTC Hold seeded with entry $107.50, stop $100.20, target $122.30, 60% and 2:1. **4 of 4 samples acted on the Hold that carried a plan.** Every sample read the analysis before acting and paraphrased the rule in its own words — *"a plan from the traders that the manager did not endorse"* — and every sample bought: two limit orders at $107.50, two at market. On the live book that Friday, 3 of 3 passes had said "no Overweight signal, stay in cash".

**Size a position from its stop, added 2026-09-19.** **4 of 4 samples sized from the stop**, each naming a loss it would accept ($150, or 1.4% to 1.7% of the book) and dividing by the stop distance: 17 to 20 shares against the table's $100.20 stop, where the live trade had been 4. One sample rejected the analyst's percentage in so many words. **The wrinkle:** the sample that chose the analyst's text stop, $105.80 and 1.6% under the entry, sized 88 shares, $9,460 of $9,995 — a 1.5% risk on paper that would have been a 95% position stopped inside a normal day. **Sizing from a stop is only as good as the stop.** Watch for it before adding any rule about it.

**The fetch allowance, reworded 2026-09-19** from "You may make up to N fetch calls … and the allowance runs out". A live pass had read the number as a budget and planned "I need to be efficient", which is the rationing `CLAUDE.md` says not to build in. **Four samples with the real ceiling: none mentioned a limit, a budget or efficiency.** They fetched 5 to 8 times in 2 or 3 rounds and every one bought INTC on the seeded plan, three by limit at $107.50. **Four more with the rounds forced to 2**, so the warnings fired: 2 of 4 quoted the one-round-left note and batched their last round around it — *"there is only one fetch round left in this pass, so I want to be thorough"* — all 4 decided cleanly after the last one, and the API accepted the extra key in the function response.

**The closed-market rule (2026-09-10).** Three probe runs wrote "the market is closed" and placed an order in the same breath, and one stated the belief behind it: *"any buys/sells placed now will not execute until market open"* — a fair guess, and wrong, because this broker refuses rather than queues. With the rule beside the order rules, six of seven samples restate it, one as *"Orders will not execute, except for `adjust` (stop/target)."* **The behavioural claim is unproven and must not be repeated as fact:** those seven placed no buys or sells, but the baseline placed none either, and across every baseline run it is 2 of 11. The measurement that matters is whether the live book's closed-market failures, six of nine to date, stop growing.

**Placement, measured (the alerts section).** "What you just did" and "What was noticed" sat between the two tables, at 42% and 47% of the prompt, and four probe runs referenced neither. Moved above the tables, the next four runs quoted the figures verbatim: `$37.38`, `40.34` and `7.9%` appear nowhere else in the prompt, which is what makes it evidence rather than an impression.

**The tool channel, confirmed live on 2026-09-17** in a local container on the same image. The API accepts `anyOf` inside `parameters_json_schema`; the thinking summary arrives beside the function call at thinking level high; 4 of 4 samples fetched (all three no-argument fetches in the first round, then one to three reads) and answered through `decide`; one sample's thinking quoted nine prices present only in fetch results. A pass costs 3 to 4 requests and 20,000 to 30,000 prompt tokens, since each round resends the conversation.

**Section headings and rules, 2026-09-21 — the first probe here run as a matched pair.** Seven samples on the prompt as it was, seven on the restructured one, same `sqlite3 .backup` copy of the live book, same `gemini-3.5-flash-lite`, both inside a container with `backend/` mounted over `/app/backend`. The restructure added a `##` heading and a `---` rule to every section and removed nothing but the titles the headings now carry, so this measures the shape alone. `turn1` went from 6,139 to 6,406 characters, **4.3% for 24 headings and 24 rules**.

| Section | Before | After | | Fetch | Before | After |
|---|---|---|---|---|---|---|
| The time | 5 of 7 | 7 of 7 | | `watchlist` | 7 of 7 | 7 of 7 |
| The market right now | 5 of 7 | 6 of 7 | | `read` | 7 of 7 | 7 of 7 |
| Your account | 6 of 7 | 7 of 7 | | `track_record` | 7 of 7 | 5 of 7 |
| What you hold | 7 of 7 | 7 of 7 | | `candidates` | 6 of 7 | 5 of 7 |
| Recent analyst signals | 6 of 7 | 6 of 7 | | `fundamentals` | 1 of 7 | 1 of 7 |
| Every ticker you track | 6 of 7 | 5 of 7 | | | | |
| Paying for research | 1 of 7 | 4 of 7 | | Named a `next_wakeup` | 5 of 7 | 7 of 7 |
| Your track record | 1 of 7 | 0 of 7 | | Wrote a `next_wakeup_note` | 5 of 7 | 7 of 7 |
| Your recent wakeups | 1 of 7 | 1 of 7 | | Placed any order | 2 of 7 | 4 of 7 |
| How long an analysis takes | 0 of 7 | 0 of 7 | | | | |

**Read this as "nothing broke", not as "headings work".** Every difference but one is ±1 or ±2 at seven samples and temperature 1, which is what this model produces run to run. The one worth another look is **"Paying for research", 1 of 7 to 4 of 7**, alongside orders placed going 2 to 4 — two of the four after-samples commissioned an INTC research. It moves with the wakeup pair below, and three small moves in the same direction are a lead, not a finding. **Do not cite any of this as evidence a heading improved anything** until someone runs it at a larger sample.

**A zero here does not mean a section was skipped, and this probe is the reason to say so.** "Your track record" scored 0 of 7 on quoting its net figure, and 5 of 7 samples called `track_record`. That section is one line and a pointer to a fetch, so its content never appears in the prompt to be quoted — the fetch is the evidence it was read. The same holds for "Every ticker you track": its `+14.0%` on INTC is what sent three after-samples to research INTC. **Before scoring a one-line pointer section as unread, check whether the model called the tool it points at.**

**"How long an analysis takes" is untested rather than ignored.** No sample needed it: research runs inside the pass, so nobody had to plan a wakeup around one. Rebuild a scenario where a pass must wait before judging it.

## Unconfirmed as read, and kept anyway

Each of these is correct, rendered, cheap, and may be read by a different model. **Do not treat the presence of one as proof the agent uses it, and do not cite one as a precedent for skipping a probe.**

**The holdings price-range column.** Probed nine times across three placements — the end of a long prose line, then the last table column, then next to Price now — and no run ever quoted it, even alone with nothing else in the prompt to read. The model reports a fixed habitual set of facts about a holding and does not widen it because a column was added.

**The four analyst-report tables on a read.** The 2026-09-15 prose version shipped unconfirmed: 2 of 2 samples on a live HOOD read never quoted the four-section block. The 2026-09-16 table version was probed again with four samples on a real NVDA read, called through `ollama-proxy` from outside the container against a copy of the live database. **Zero of four quoted anything unique to the Market, Sentiment, News or Fundamentals tables** — no ATR, no sentiment score, no current ratio, no market cap. Every figure the other samples did mention also appears in the rationale above the tables, so none of it can be pinned to the tables.

**The same four samples read the two full plans, which is the other half of the finding.** 2 of 4 explicitly named and cross-referenced `Investment plan` and `Trader's plan`, and both caught a genuine contradiction between them — the Investment plan said "Recommendation: Hold," the Trader's plan said "Action: Buy" on the same signal. Sample 2's words: *"the analysis contains a significant conflict where the 'Strategic Actions' recommends 'Hold' while the 'Trader's plan' is 'Buy' based on technicals."* **This is the mirror image of "tables get read, prose gets skimmed"**: here the prose was read and the tables were skimmed. Keep the plans.

**Limit orders, before shipping (2026-09-17).** A $2,000 book, no holdings, one Buy signal whose entry sat $7 under the current quote — a textbook case. 4 of 4 `gemma4-e4b-qat` samples bought at market for the full quantity, and 4 of 4 `gemini-3.5-flash-lite` samples did too. **0 of 8 across both models used `order_type: "limit"`**, although it was explained in the rules and in the JSON-shape example both. Re-probe after any placement change.

**Limit orders, told its own plan rather than left to invent one.** `wakeup_note` carried an explicit instruction to place a limit buy at a named price and quantity if a named breakout level held, with the current price confirming it. Both models produced the exactly correct order on 1 of 4 samples each, so **the mechanism is within reach**. **The local model's other three samples are the concerning failure**: each wrote "executing the disciplined limit buy" or similar in its own `reasoning`, then submitted an order with no `order_type` or `limit_price` at all — a plain market buy the model believed was a limit order. Gemini's misses were more honest: two openly switched to a market buy and said so, one deferred to a read. This is "keyword mention, not mechanism" sharpened — the words right, the tool call wrong — on a small sample, so treat it as a lead rather than a verdict.

**Limit orders, announced (2026-09-19).** With a change note announcing them in the prompt, **2 of 4 samples placed a limit buy** at the plan's $107.50, one for the day and one GTC, both writing a note to set the stop on the fill; the other two bought at market. One named the note as the reason: *"the rules have changed recently; I can now use limit orders"*. On 2026-09-17, without the note, 0 of 8 had reached for one.

**The `fundamentals` fetch (2026-09-19, the day it moved to Yahoo Finance).** Four samples on a copy of the live book, the clock pinned to Friday 11:15 AM, a seeded INTC Hold plan, and the change note announcing the fetch: **1 of 4 called it**, for INTC, ORCL and CRWV in one round, and **0 of 4 quoted a figure from the result**. The one caller reasoned on from the read's text and the signals table, and never mentioned a ratio or a quarter. What it saw was complete and correct, from real Yahoo and Webull data inside the container. Nobody bought that day, where 4 of 4 had that morning on the same levels, because the seeded rationale was the 09-17 text that says to wait for a pullback to the 10-day EMA or $100, and every sample cited it.
