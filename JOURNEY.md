# The agent's journey — what we changed, and why

The app writes its own record: what it bought, what that cost, and what the agent said about it, day by day. Every sentence in it comes from a trade, a charge, or a decision pass, so it cannot drift from the book.

Read it at `/api/agent/journey`, or as files: one per month, under a folder per year, in the data volume beside the database and the logs.

```
data/journey/2026/08-August.md
data/journey/2026/09-September.md
```

The app rewrites them after grading each evening. `python -m backend.scripts.write_journey` regenerates them on demand.

Each file opens with the month in four numbers: positions opened, positions closed, research spent, and where the book started and finished. That way a file reads on its own, not only as part of a series. That is what makes them publishable later: a month is a post.

**The app generates them, and rewriting them is how they stay true.** Do not edit them. The next write discards the edit. Put commentary here instead.

**This file is the other half, and it is the half the app cannot write.** It knows the agent changed its mind. It does not know that we changed the prompt the week before, or added a research charge, or settled the debate-round count by running an experiment.

Without those causes, a month of record is a list of events nobody can learn from.

Two rules, both learned the hard way elsewhere in this project:

- **Write it when it happens, not afterwards.** A reason you reconstruct two weeks later is a story about what you would like to have been thinking.
- **Record what was wrong, not only what worked.** The entries that say "this turned out to be noise" are worth more than the ones that say "this worked". They are what stops someone proposing the same idea again in three weeks.


---

## What belongs here, and what does not

**One question decides it: does this change make two periods of the experiment non-comparable?** If yes, it goes here. If no, it goes in [the changelog](https://github.com/nandyalu/ten-acre/blob/main/docs/changelog.md).

That splits into three tests. Any one of them is enough:

- **Behaviour** — it changes what the agent is shown, what it may ask for, or what Python refuses.
- **Evidence** — it changes what the record contains or means. Telemetry counts: a field that is null before a date is exactly what trips up whoever reads the data later.
- **Incident** — the agent's behaviour changed without anyone intending it, so real days are contaminated.

Everything else is a changelog entry: setup, deployment, guards, infrastructure, site copy, docs, dependencies. Those matter, and they are not this.

**Keep an entry to a sentence or two — what changed, and why.** Long-form reasoning that constrains a future edit belongs in `CLAUDE.md`, or in the `.claude/rules/` file for that area. Those are read before the code is changed. This file answers "when did the question change", and it can only do that if it stays readable end to end.

Entries before 2026-09-11 were swept under these rules; anything that failed all three tests moved to the changelog.

## Every change to the agent, and why

**This file covers one agent: the merged agent that has run since 2026-09-01.** Before that date, two deployments ran side by side — a live bot on a fixed watchlist, and a separate analyst experiment that chose its own tickers. Both ended on 2026-09-01. Their history lives in [the two-book experiment](https://github.com/nandyalu/ten-acre/blob/main/docs/two-book-experiment.md) and [the analyst experiment](https://github.com/nandyalu/ten-acre/blob/main/docs/analyst-experiment.md).

`CLAUDE.md` and `.claude/rules/agent.md` describe what the rules are. This describes how they got that way. Add an entry here **before** changing a rule, not after.

Newest first.

**2026-09-16 — the persistent memory feature was probed, and the finding is conditional, not a flat yes or no.** Two runs against `gemini-3.5-flash-lite`, 4 samples each. Turn 1 used the real live book — market closed, $43 cash, one holding with no stop resting — and 0 of 4 samples engaged with two seeded memory notes at all; the single obvious job in front of the model (fix the unset stop) crowded everything else out, the same pattern already on record for the price-range column and the four analyst-report tables. A second run built a scenario by hand — market pinned open, $2,000 free cash, no holdings, and a memory note naming a ticker with a live, affordable Buy signal — and 3 of 4 samples cited the note by name, unprompted, as the reason to pass: *"persistent memory dictates staying away from INTC after multiple stopped-out breakouts,"* *"keeping persistent memory of the INTC breakout failure rule."* Neither phrase is copied from the note, which is the paraphrase test this file uses elsewhere. **The section is read when the model has an actual decision in front of it that the note bears on, and skipped when something more urgent has already claimed the pass.** Kept the feature; see `.claude/rules/agent.md` for the full write-up.

**2026-09-16 — persistent long-term memory notes added for the agent (`side: "memory"`).** `next_wakeup_note` only carried notes to the next immediate wakeup pass. The agent now has persistent long-term memory notes that are stored in the database (`BotSetting`) and injected into every prompt until explicitly cleared or removed. Capped at 10 notes to prevent prompt crowding.

**2026-09-16 — explicit multi-buy cash allocation guidance added to prompt rules.** The prompt rule for buying now explicitly reminds the model that when placing multiple buys in a single turn, it must allocate quantities so that `sum(quantity × price)` fits within available cash, reducing overspend refusals on multi-buy orders.

**2026-09-16 — trailing stop protection guidance reworded in `adjust` rule.** The `adjust` rule now explicitly advises the agent to raise stops on unrealized profit or UNSET positions to protect gains and manage risk as trades progress.

**2026-09-16 — watchlist untrack guidance added to prompt rules and table headers.** The rules and watchlist table header now instruct the agent to untrack watched (unheld) tickers with stale or 'never' analysed status to free watchlist slots for new research orders when the watchlist cap is reached.

**2026-09-16 — 'Day High' and 'Day Low' columns added to the tracked tickers and recent analyst signals tables.** An agent note asked for immediate session high/low context beside "Price now" to judge daily volatility and support breaks before commissioning research. Built as `day_range_today` reading today's session range from the bar cache, and displayed in both tables beside "Price now".

**2026-09-16 — the candidate menu reserves slots for congressional trades and Yahoo trending, instead of losing them to a volume sort.** Both text sources, added 2026-09-15, hand back real tickers that were then sorted into the menu by trading volume alongside the two Webull screens — and a Webull "most active" row is a volume leader by definition, so a congressional trade or a searched-for ticker almost never outranked one. Live containers ran several passes after deploy with neither source ever reaching the agent. `MAX_PROPOSED` goes from 8 to 10; `_RESERVED_SLOTS` guarantees congress up to 3 seats and trending up to 2, each filled by its own most-liquid names first, and Webull fills whatever is left. The menu line shown to the agent now also names the source (`via congress trade (QuiverQuant)`, `via trending (Yahoo Finance)`, `via most active`), so a read of the prompt can tell which screen actually surfaced a pick.

**2026-09-16 — an unguarded position wakes the agent, instead of waiting for its own next chosen time.** A buy that filled with no usable stop or target, or a failed sell that could not get its exits back, was written to the alert log and to Discord and nothing else — the agent could sit for up to four days not knowing a position of its own had nothing under it. `agent.AgentRun.unguarded` now records it, and `scheduler._run_agent_pass_locked` pulls the agent's own alarm forward the moment the pass ends, the same mechanism already used for a new change note.

**2026-09-16 — a stop or target actually firing at the broker wakes the agent, instead of only posting to Discord.** `_settle_agent_fills` told a person a resting exit had closed a position and told the agent nothing, so it could learn its own position was gone only whenever it happened to wake for some other reason. It now also calls `_maybe_run_agent`, the same trigger already used for a sharp move or an earnings date.

**2026-09-16 — the table-based read section was probed, and the two full plans read differently than the four report tables.** Four samples, a real NVDA read, called through `ollama-proxy` from outside the deployed container against a read-only copy of the live database (`TRADING_DB_PATH` env override in `backend/database/engine.py`, reverted after). 2 of 4 samples explicitly named and cross-referenced `Investment plan` and `Trader's plan`, and both caught that the two disagreed on the same signal — Investment plan said Hold, Trader's plan said Buy — a contradiction that could not have been seen before this section existed, since neither plan reached the agent at all before 2026-09-15. 0 of 4 samples quoted anything unique to the Market, Sentiment, News or Fundamentals tables. Kept the plans in full; the four tables stay unconfirmed as read, same status as the 500-char prose version they replaced. See `.claude/rules/agent.md` for the full writeup and quotes.

**2026-09-16 — a read's analyst-report section became each analyst's own summary table, in full, plus both plans in full.** The 500-char trim shipped 2026-09-15 cut each of the four reports mid-sentence, wherever the character count happened to land — no better than the rationale it sat beside, since it never reached the report's own conclusion. Every analyst prompt already ends with an instruction to append a markdown table summarising its own findings (see `TradingAgents/tradingagents/agents/analysts/`), so `analysis_reader._last_table` pulls that table — whatever the model titled it — instead of guessing a fixed name or trimming by character count. `investment_plan` and `trader_investment_plan`, already stored per signal alongside the four reports, are now included whole rather than not at all: both run under 2,000 characters and are what the model's own final call drew on. Also split into its own section, headed **What you asked to read** in bold, with a rule (`---`) before it — it used to run on directly from "recent wakeups" with no divider and read as that section's last line. Not yet re-probed against the live model; the 2026-09-15 finding (0 of 2 samples quoted the old prose excerpt) does not necessarily carry over to a table, which this repo's own findings say reads differently than prose between tables does.

**2026-09-16 — every major prompt section now has a rule (`---`) between it and the next.** The prompt had grown to fourteen sections run together with only a blank line apart; a horizontal rule is a stronger visual break, and this repo has already measured that a heading location changes what gets read, not only what renders.

**2026-09-16 — two rules with no per-pass figure moved from the rebuilt part of the prompt into `SYSTEM_PROMPT`.** "Some signals carry how good the analyst thought the bet was..." and the `note` action rule quote nothing from this pass's book or signals, so they belong with the rules that never change rather than the ones `build_prompt` reconstructs on every call. No wording changed.

**2026-09-15 — a read now carries a short take from each of the four analysts, not only the Rating and the rationale.** `market_report`, `sentiment_report`, `news_report` and `fundamentals_report` were computed and stored for every analysis and never reached the agent at all — it traded on a verdict it could not check against the evidence behind it, from a report that may have come from the same model with none of this agent's own cash or holdings in view when it was written. `analysis_reader.read` now trims each of the four to 500 characters and appends them, labeled, after the rationale; a section with nothing on record (an analysis from before this date, or a report row that never saved) is left out rather than shown empty. **Probed the same day, and shipped unconfirmed as read**: 2 of 2 samples on a live HOOD read reasoned entirely from the signals and holdings tables and quoted nothing unique to the four sections — the same pattern already seen in the change-notes section and the holdings price-range column. Kept anyway, on the same reasoning those were: correct, cheap, and this may be one small model's habit rather than a property of the fact itself. Two samples is thin evidence either way.

**2026-09-15 — holdings became a table, and a missing exit now says `UNSET` in its own column.** The prose line had grown to nine clauses; probing it (three fresh runs, one holding, nothing else in the prompt) showed the model quoting every earlier clause and never the range one, buried second-to-last. Converted to a table, the same shape the signals section already uses, with a plain `UNSET` for a missing stop or target rather than folding it into a sentence that reads the same whether one side is resting or neither is — confirmed read: the model paraphrased `UNSET | UNSET` as "no stop-loss or target orders set."

**2026-09-15 — the price-range column is shipped unconfirmed as read.** Nine probe samples across two placements — last column, then moved beside Price now — and no run ever quoted the $208.93–$233.45 shape of the number, including single-holding runs with nothing else to look at. The model reliably reports avg cost, current price, days held and the resting-exit state; the range never joins that set regardless of where it sits. Kept in the table anyway: it is correct, cheap to render, and this is one small model's behavior, not a property of the fact itself. Do not read its presence as proof the agent uses it — see `.claude/rules/agent.md`.

**2026-09-15 — each turn's own reasoning and what it asked for are now kept, not only the pass's final answer.** `_decide` always parsed every turn's reasoning to decide what happened next, then kept only the last one — every earlier turn's reasoning was computed and thrown away, so a two-turn pass published one explanation for what were really two different decisions. Directly relevant now that a pass may run 8 turns rather than 3 (see below): a reader following a long pass needs the story turn by turn, not only its ending. `agentrun.turns[i]` now also carries that turn's own `reasoning` and `orders` — what it asked for at that point, parsed the same way the backend parses every answer, not necessarily what was screened or executed. Null on every turn recorded before this date. The events page shows it beside that turn's own prompt and answer.

**2026-09-15 — a pass gets 8 act-turns now, not 3.** Three covered one ticker's happy path: commission research, act on the verdict, see the fill — nothing more. It left no room to rest a stop and target the broker refused at purchase, or to correct a mistake once the fill was seen, both real and both this app's own tools already allow. Research now measures 9-10 minutes, down from 16-20, which is most of why 3 was ever this tight: a turn that commissions research holds `_pass_lock` for the whole wait, and the old figure made even 3 of those turns a possible hour-long pass. Not shown to the model — `_MAX_ACT_TURNS` is a Python backstop, never a prompt number — so no probe-the-prompt pass is needed, but this does change what a pass can accomplish, which is a comparability line. Watching live whether the agent actually uses the extra turns or still settles a pass in 2-3.

**2026-09-15 — a `read` bundled into the one retry turn a refused order gets no longer runs silently into nothing.** Follow-on from the same-day fix below: the refusal-retry is the pass's last turn, so a read asked for there was already dropped by design — `_decide` throws away the read half and screens whatever else came with it. What was missing was telling the agent: nothing said a read would not work there, so it could spend its one chance to fix a refusal on a read that never happened, and never learn why. The "fix it" prompt now says outright that a read does not run on that turn, before the agent tries. Needs its own probe-the-prompt pass.

**2026-09-15 — an order bundled beside a `read` in the same answer is no longer carried out silently — or silently dropped.** Caught live: after an early wake, the agent's second answer asked to read INTC again and, in the same JSON, to buy 50 shares and move the exits. `_decide` splits a `read` out before screening because it changes the pass's control flow rather than the book — right, but the buy and the adjust bundled beside it vanished with no trace: not rejected, not failed, not logged anywhere the agent could see. The next turn's prompt now lists exactly what was not carried out and says why, so the agent can resend it once it has read what it asked for; a new fixed rule says the same thing up front. Needs its own probe-the-prompt pass to confirm the model reads the new section rather than skimming past it.

**2026-09-15 — a holding's line now names the low and high it has traded since purchase, when the bar cache has one.** Until now the agent saw only the buy price, the price at its last research, and the current price — three points that cannot tell a stock that dipped and came back from one that only climbed. `agent.price_range_since_purchase` reads the daily bar cache from the purchase date to today and adds "has ranged $X to $Y since you bought it" to the holdings line when a range is available. Needs its own probe-the-prompt pass before this counts as confirmed read, not only rendered.

**2026-09-15 — a batch snapshot no longer goes empty over one bad ticker.** Confirmed live within the hour of shipping the two text sources above: `ELN`, `EA` and `LBRDK` don't exist in Webull's `US_STOCK` category, and the vendor refused the whole 100-ticker request rather than just those three — `get_snapshots` had assumed an unrecognized ticker was silently dropped, which held for a single-symbol lookup but not this endpoint. It now parses the rejected symbols out of the vendor's own error text and retries once without them, instead of losing every other candidate that round.

**2026-09-15 — the candidate menu adds two text-only sources: congressional stock trades and Yahoo Finance's trending list.** Until now every proposed ticker came priced from a Webull screen; a name found only because a member of Congress traded it, or because it is suddenly being searched for, had no way in. Both new sources hand back a bare ticker, not a priced row, so each is verified against a real Webull snapshot and screened by the same price/volume/move floors as everything else before it can reach the shortlist — a ticker pulled from a scraped page is not trusted data until priced. Benzinga and MarketWatch's free feeds were tried and dropped: neither tags a headline with its ticker, and guessing one from a company name is the same invented-fact failure this app already guards against for prices.

**2026-09-15 — the signal-stop and target alerts no longer read as an order that already fired, and now name the research date.** Probed against the live book (AVGO 100% of the account, no resting stop, only a target): the alert's old wording — "AVGO at $348.50 reached the $354.72 stop from the Hold signal of 2026-09-10" — carries three different AVGO prices in one sentence with no stated direction. Across 16 probe runs (four prompt variants, four samples each) nearly every reasoning trace spent hundreds of words trying to work out whether a stop had already executed instead of noticing the real fact: nothing is resting on the position. Only one of sixteen runs placed the missing stop. The message now states direction plainly, drops the stale alert-time price, and says outright when nothing is resting on the position. Both messages also now say the level came from research on a named date and call themselves a rule firing against that old level, not a new analysis — since the agent picks its own research cadence now rather than a twice-daily sweep, the signal behind a tracked ticker can be old, and the alert must not read as though it carries a fresh verdict.

**2026-09-13 — the sentiment analyst reads Reddit through a browser, and a throttled subreddit reads as unavailable, not silent.** The logs showed the RSS feed refusing r/stocks and r/investing after the first subreddit of an analysis, and the analyst was told "no posts found" for both. Upstream `7cc478a` now marks a failed fetch unavailable, and with `REDDIT_TRAWL_URL` set the fetcher loads Reddit's search page through the trawl container, with scores, comment counts, the body of every post shown, and no post older than 7 days. Sentiment reports from before the deploy that ships this do not compare with reports after it.

**2026-09-13 — a later turn of a pass says why the agent is asked again.** The second turn of a pass repeated the reason the pass started — "A change to this app woke you" — as though it were a new wake; on 2026-09-13 the agent still read its own research result correctly, from the section below that line. A later turn now keeps the line as why the pass started, and adds why it is asked again: its orders were carried out, an analysis it asked to read is below, or part of its answer was refused.

**2026-09-13 — the four analysts in one analysis run at the same time.** They ran one after another, although no analyst reads another's report; the debates still run in turn, so each analyst's first message and what the trader reads do not change. One analysis on the local pool takes about 11 minutes, not 16, so `duration_seconds`, the analysis time the agent is shown, and the electricity estimate do not compare across this date.

**2026-09-13 — a failed analysis is retried, and one that still fails is reported as failed.** Since research began to run inside the pass on 2026-09-12, an analysis that failed was reported to the agent as finished, beside the older analysis of the same ticker; no pass met this, because the two failures in the logs are from 2026-09-10 and 2026-09-11. The app now retries an error in its own code once, waits for the model service to answer again with a doubling delay for up to an hour, and tells the agent what stopped the analysis and whether it was charged.

**2026-09-13 — an early wake no longer tells the agent that it asked to be woken.** A restart with new change notes, a sharp move, and the earnings check all woke the agent by firing its pending alarm early, and the alarm always said "You asked to be woken now". The prompt now names what woke it, shows the note it left beside the time that note was written for, and asks it to choose its next wakeup and note again, because the early pass replaces the planned one.

**2026-09-13 — a Gemini pass records its thinking, for every turn and every analysis call.** The decision pass read thinking only from OpenAI-compatible endpoints, so every Gemini pass stored an empty `thinking` column; Gemini also returns no thinking unless `TRADINGAGENTS_GOOGLE_THINKING_LEVEL` is set. What Gemini returns is a summary that Google writes of the model's thinking, not the raw reasoning an Ollama model returns, so do not compare the two texts as the same kind of evidence.

**2026-09-13 — a deployment can state its model's rate limits, and research that cannot finish inside today's requests is refused.** `LLM_REQUESTS_PER_MINUTE`, `LLM_TOKENS_PER_MINUTE` and `LLM_REQUESTS_PER_DAY` make the app wait before it sends a call the vendor would refuse. A free Gemini key allows 500 requests a day and one analysis uses about 20, so when too few requests are left, the agent is told so in the same pass and nothing is charged, instead of an analysis failing halfway.

**2026-09-12 — the agent is told, beside the order rules, that a closed market refuses a buy or a sell but still takes an `adjust`.** The clock line has always said the session is shut and that was never enough: three probe runs wrote "the market is closed" and placed an order in the same breath, one of them explaining that orders "will not execute until market open" — a fair guess, and wrong, because this broker refuses rather than queues.

**The `adjust` exemption came from the record, not from reasoning.** An earlier wording had the broker refusing adjusts too. On Labor Day run 15 adjusted two exits at 09:31 while runs 16, 17 and 18 had five buys and sells refused over the following six hours; no adjust has ever been refused in nine failures.

**The behavioural claim is unproven.** Seven probe samples with the rule placed no buys or sells, but the baseline placed none either — across every baseline run it is 2 of 11. What is established is that the correction is read: six of seven restate it in their own words. Whether the live book's closed-market failures stop growing is the measurement.

**2026-09-12 — the clock had no holidays, and a whole day of the experiment went on refusals because of it.** On Labor Day, Monday 2026-09-07, the first line of every prompt read "the market closes in 5h 28m, at 4:00 PM". The agent believed it, as it should, and placed five orders across three passes that the venue refused. The line now names the reason it is shut and when it opens again, skips holidays when working out the next open, and knows about the three half-days a year.

**Five of the six market-closed refusals in the whole record were that one day**; the sixth was a Saturday. The calendar is computed from the published NYSE rules rather than fetched or listed, so it cannot go stale.

**2026-09-12 — five decisions had been silently dropped by the parser, and three of them were the agent asking for something.** It was noticed because the agent's note said it had researched NVDA and no analysis had run. Reading back through all 52 stored answers: two notes, and three research orders. A note is the agent telling us a tool is missing, so a silent drop there loses the one message that was meant to reach a person.

Two different faults. The model sometimes puts an order **beside** `orders` rather than inside it — a top-level `"research"` or `"note"` key — and the parser only ever read `payload["orders"]`. And run 44 closed a JSON string with an apostrophe instead of a quote, so the whole answer parsed to nothing and its two research orders read in the record as an idle pass the agent chose.

**Only `research` and `note` are salvaged, and only repairs that cannot change a meaning are applied.** A bare `"sell": "AVGO"` says nothing about how many shares, and a parser that guesses there places a trade nobody chose. Runs 9, 36, 44 and 46 are therefore idle passes in the record that were not idle — the agent asked, and nothing heard it.

**2026-09-12 — the agent is told why it is awake, and can leave a note for its own future self.** Four things start a pass — its own chosen time, something noticed while it slept, the last call before the close, a change to the app — and it was told none of them; the labels existed as log lines. `next_wakeup_note` is the other half: the agent has no memory between passes, so everything it works out is otherwise gone. The prompt carries prices and positions, never conclusions.

**It is a handover, not a justification.** The rule tells it what the next prompt will already contain, so it does not spend the note restating a price. Probed across seven runs: five quoted the note back and reasoned against it — "Did AVGO break $366.16? $361.99 is slightly below" — which is the thing it could not do before.

**2026-09-12 — nothing is analysed unless the agent asks for it and pays for it.** A sharp move or a volume spike used to commission an analysis on the spot, and the pre-market earnings check did the same for anything reporting soon. Both chose what the agent should study, and both were billed to the agent's own research budget — ten such charges on the live book, none of them asked for. The watchdog reports now: what it saw is a table in the prompt, and the agent decides whether any of it is worth $0.05 and sixteen minutes.

**The timing is the argument, not the principle alone.** A pass takes about a minute and an analysis sixteen. A sharp move is exactly the moment selling may beat studying, and the agent was not asked until sixteen minutes of research it had not ordered had finished — describing a price that had moved again. It can still order that research, and since this morning it runs inside the pass.

**The agent could not see any of this before.** The alert table was never read into a prompt, and the tracked-ticker table shows the move *since the last analysis*, which cannot tell a 5% fall this morning from a 5% drift over three weeks.

**Triggered wakes are no longer gated on market hours.** That gate existed because a move worth analysing at midday is worth nothing by morning — an argument about analysis, which this removes. Moving a stop, ordering research for the open, untracking and choosing a wakeup all work at any hour. The earnings check proves the point: it runs pre-market, so under the old gate it could never have woken anyone.

**2026-09-12 — a pass is a loop now: the agent acts, sees what its own orders did, and is asked again.** Research runs *inside* the pass — it waits the sixteen minutes, then reads the verdict and the analyst's reasoning and can act on it before finishing. Trades report back the same way, including what is actually resting under a buy. A pass ends when an answer does nothing, which is also what "no action, just wake me later" looks like. Bounded at three act-turns, with one read allowance shared across them, and an answer that repeats the previous turn's orders ends the pass rather than placing them twice.

**The old shape never worked, and the evidence says so.** The prompt promised "you are asked again automatically when one you ordered lands". It was not: the pass dispatched its research and then, still holding `_pass_lock`, called the wake — which begins `if _pass_lock.locked(): return`. Every one of those wakes was dropped. Of seventeen analyses finishing across 2026-09-10 and 09-11, none produced a pass. A second fault compounded it: the cooldown was stamped before the attempt, so a pass that was skipped still suppressed every trigger for thirty minutes. Both are gone — the first by construction, since there is nobody left to wake.

**The sharp-move rule now says it only covers tracked tickers.** It was stated without the qualifier, and the watchdog only ever scanned the watchlist. The agent read it, saw ORCL down 5.4%, and reasoned that an analysis must be coming — for a ticker nothing was watching.

**2026-09-11 — `created_at` on a signal means when the analysis started, for every row.** It had meant two things at once: `record_signal` stored the finish, while the rows recovered by `backfill_signal_timestamps` carried the start decoded from `trace_id`. The split is exact and provable — rows up to 31 equal their trace start to the second, rows from 32 equal start plus `duration_seconds` — so the seam sat at 2026-09-09 with nothing marking it.

**Start, not finish, because it is the instant that was actually observed.** The old rows hold a real recorded start; correcting them to a finish would mean writing a timestamp nobody watched a clock for. The new rows can be moved to the start exactly, since every row keeps the `trace_id` that encodes it. The agent reads this field too — `_analysed_at` puts it in the prompt — so an analysis it is told about now dates from when the work began, roughly 16 minutes earlier than before on this hardware.

**2026-09-10 — the agent reported the timing contradiction itself, twice, and it was still half there after the first fix.** Two notes, at 09:30 and 15:53: "the top text says about 2 minutes, while the rules say about 20 minutes and that it lands about an hour later". The first pass at this removed "about an hour" and left "about twenty minutes" — a second hardcoded figure in the same rule. **One duration is stated in the prompt now, the measured one**, and the JSON example was still showing `"45 minutes"` under an instruction to use an ISO datetime.

**This is the note action doing exactly what it exists for.** The agent could not act on the contradiction and said so instead, precisely enough to fix — including the part the first fix missed.

**Reading is bounded now rather than rationed: six analyses and three rounds of asking, per pass.** One read was a restriction with nothing behind it but the fear of a loop, and a bound answers that directly — the analyses cap stops a pass reading the whole watchlist, the turns cap stops a model asking for one more thing every round. A person deciding whether to buy reads the research first, and often more than one piece of it. Reads no longer share the refusal retry's turn either: one is the agent gathering what it needs, the other is Python saying the decision cannot be executed.

**The principle behind it is now in CLAUDE.md, because it will decide future arguments.** Tokens spent deciding well are not waste; tokens spent guessing are. Cut the confusion, fund the deliberation — and where a limit exists only because an unbounded version might loop, bound it rather than ration it.

**Reading an analysis became expected rather than rationed.** The first wording said to read "when the reasoning would change what you do, not out of habit", which is advice to hesitate over something that costs nothing. The costs are asymmetric: reading too often spends a turn the pass had anyway, reading too rarely means acting on a single word. A signal line is a verdict; the reasoning is where the case for it lives.

**A Hold is named as the one most worth reading.** It is the decision that says least: it can mean the analyst saw nothing, or saw a case for holding a position and none for adding to it, and the word is the same either way. The agent spent a long stretch of one pass reasoning about which it had, which is the question a read answers.

**What a day on a hosted model costs, measured.** `qwen-3.8-27b` on Cerebras ran 6 analyses and 12 decision passes for **$2.54** — 1.38M prompt and 714k completion tokens, 34% completion because the model reasons out loud. That is **$0.40 an analysis** against roughly $0.04 of marginal electricity for the same six on the local pool, which is ten times, and it is a real figure rather than a projection.

**2026-09-10 — six fixes read straight out of one thinking block, two of them real bugs the model spotted before we did.** A Cerebras pass at 3:59 PM spent most of its reasoning on things the prompt should have told it, and said so in enough detail to fix each one.

**The tracked table was showing a stale analysis, and the agent noticed.** It read two INTC rows for the same day — one at $106.24, one at $100.44 — and asked "the tracked table might not be updated to latest?". It was right: that row came from `get_recent_signals(limit=1)`, which orders by `signal_date` alone, so with two analyses on one day it returned whichever row came first. The agent was being shown a price the stock had already moved 5.5% away from.

**A clock time earlier in the day silently became five minutes from now.** `"09:00"` at 3:59 PM resolved to 9 AM *that morning*, already past, and the clamp turned it into 4:04 PM. The agent avoided this by computing "1021 minutes" by hand across a paragraph of arithmetic — self-defence that cost it tokens and cost the record a legible intention. A clock time now resolves to its next occurrence.

**The prompt contradicted itself about research.** The rules said an analysis lands "about an hour from now" — a figure written in by hand — while the measured line two sections above said two minutes. It also read as though the agent had to schedule its own return: it does not, because `_run_triggered_analyses` asks it again when the analyses land. Both now say the measured time and state that the wake is automatic.

**Dates carry times, and "price now" carries its own timestamp.** A date alone cannot order two analyses of one ticker on one day, which is exactly what the agent could not resolve.

**2026-09-10 — the prompt states the year, tables the numbers, and moves its fixed rules into the system message.** The agent's own reasoning showed it working out what year it was from a date that never said, and puzzling over whether a price on a signal line was the price now or the price at the analysis. Three fixes to one prompt: the clock line carries the year, the signals and the tracked tickers are tables with named columns instead of run-on sentences, and the rules that never change moved to the system message, leaving the user message to the figures that do.

**Measured in three shapes, same data, same model, back to back.** The tables alone cut the model's output; moving the fixed rules to the system message cut it again and took a quarter off the wall clock.

| | Old | Tables | Tables + split rules |
|---|---|---|---|
| Seconds | 73.6 | 73.5 | **54.0** (−27%) |
| Prompt tokens | 3,435 | 3,570 | 3,541 (+3%) |
| Completion tokens | 2,383 | 1,952 | **1,709** (−28%) |
| Reasoning characters | 5,707 | 4,325 | **3,609** (−37%) |

**All three bought 2 CRWV.** The old and tabled runs then bought 6 MARA; the split one bought 1 SMCI instead — a different second choice, not a different thesis. **One run each at temperature 1 is one sample**, and this project has measured two of twelve paired analyses agreeing, so treat the direction as encouraging and the size as unmeasured.

**Measured rather than assumed, on the same data through the deployed model.** Both prompts were built from one database copy, so only the formatting differed, and both went to `gemma4-e4b-qat-128k` back to back. The prompt grew 3.9% and the model's output fell: **completion 2,396 to 2,006 tokens (-16%)**, reasoning 5,231 to 4,878 characters, wall clock unchanged at 73 and 75 seconds. Both runs reached the same decision — buy 2 CRWV — so the format changed how much working-out it took, not what it concluded. **One run each, at temperature 1, is one sample and not a result.**

**Every line the model receives is now one complete thought.** The rules are wrapped in the source so `agent.py` stays readable, and that wrapping was reaching the prompt — a rule arrived as five lines, four of them beginning mid-sentence. This repo already forbids hard-wrapping prose in Markdown for exactly that reason. Continuation lines are folded before the prompt is sent; the JSON example is indented by one space rather than two and is left alone.

**The rules split by whether they vary, not by importance.** A rule holding a number from this pass — the cash limit, the watchlist cap, the horizon — has to be rebuilt every time, so it stays in the user message. The rest are constant across every pass of the experiment and were being re-sent each time.

**2026-09-10 — the agent can ask to read an analysis it has already paid for, and the record now holds every turn of a pass.** It saw one line per signal — the decision and the levels — and never the reasoning, so a Hold that meant "keep a fifth of the position and defend it below 102.70" reached it as the same word as a flat Hold. Naming a date lets it compare the analysis it bought on against today's.

**Reading is free and does not count as acting.** The $0.05 charge exists so that choosing what to *study* costs something; re-reading what it already bought teaches nothing about that choice. And a pass that only read is still an idle pass, for the same reason a pass that only left a note is — otherwise "let me look at the analysis" becomes this model's way of not deciding.

**One extra turn per pass, shared with the refusal retry.** The retry has been capped at one since it was built, on the reasoning that a loop arguing with a small model would spend the market open doing it. A read is the same cost with the same risk, so the two draw on one budget: read, or retry, not both.

**`agentrun` records every turn, which it did not before.** A refusal retry rebuilt the prompt and overwrote the first one, so a two-turn pass was published as though it were one — and this site's claim is that every prompt the agent saw is on the record, word for word. That was a small gap while retries were rare; a read-then-decide pass makes it the normal shape. Passes before today hold one turn, which is what they were.

**2026-09-10 — the research rule no longer promises the next pass will be inside market hours.** It said an analysis comes back "within the hour, while the market is still open", which stopped being true the moment the agent could be woken at any hour — and it contradicted the same prompt's advice to wake early and have the open's research ready.

**2026-09-10 — the agent is asked when the market is shut, instead of being turned away at the door.** `run_once` refused every pass outside market hours, so the pre-open research the prompt invites it to do produced nothing at all — no research, no exit adjustment, not even a note. The refusal now falls on the order, which the broker rejects and reports into the next prompt.

**2026-09-09 — the model's reasoning is kept, after being generated, paid for and discarded since the day the agent started.** Roughly nine tenths of what the model produced was never recorded, so the record held every decision and none of the working-out. Passes before this date store nothing, and nothing is backfilled.

**2026-09-09 — a decision pass records what it cost: prompt tokens, completion tokens and seconds.** Nothing had ever counted them, so no question about what a prompt change costs could be answered. Stored NULL before this date and never zero, because a zero would read as a free call.

**2026-09-09 — the agent may sell whenever it judges it right, and is no longer told the money is fake.** A resting stop is a floor under a position, not a reason to leave it alone — and an agent told the stakes are imaginary is not being asked the question this experiment exists to ask.

**2026-09-09 — two tickers the agent commissioned on 2026-09-08 were never analysed, and nothing said so.** SMR and CRWV produced no signal, no charge and no error, so the agent spent the next day deciding around research it had ordered and never received.

**2026-09-08 — a signal records the time of day it was created, not only the calendar date.** Every event on a day had collapsed onto one daily candle. `Signal.created_at` is null for anything before this date.

**2026-09-08 — the compulsory morning analysis of the whole watchlist is gone.** Everything tracked was analysed and charged for at 11:00 UTC whether the agent wanted the answer or not; it now commissions every look itself, holdings included, bounded by cash rather than by a schedule.

**2026-09-08 — the failures section no longer tells the agent a retry will usually fail.** That stopped being true once the underlying bug was fixed, and the agent appears to have believed it anyway — holding a position it had just given a good reason to sell.

**2026-09-08 — the agent is told when a note it left has been acted on.** A note reached maintainers and nothing ever told the agent the ground had moved, so it kept asking for things that already existed. Entries in `backend/agent_changes.json` from the last three days now appear in the prompt.

**2026-09-08 — a sell waits for the exit cancellation to take effect, not merely to be accepted.** `cancel_order` returns before the broker has acted, and AVGO's sell landed in that gap on two consecutive days.

**2026-09-05 — the wakeup is a real alarm, fired at the second the agent asked for.** Polling for it meant a chosen time could arrive minutes late.

**2026-09-05 — the agent owns its own schedule, day and night.** The fixed 13:35 UTC pass is gone: it is woken when it asked to be woken, and at no other time.

**2026-09-05 — the wakeup checker ran every five minutes, so a chosen time could be four minutes late.** Four of eight self-scheduled wakeups waited more than four minutes, which makes the agent's own timing decisions unreadable afterwards.

**2026-09-05 — the agent could not sell anything it had bought.** Its own resting exits made every sell look like a position reversal to the broker, so the exits are now cancelled before the sell goes out.

**2026-09-03 — the agent sets its own cadence, and can finally tell the time.** It manages a book through the day instead of deciding once at the open and going quiet.

**2026-09-03 — the agent chooses when its research arrives, and is told why each analysis exists.** It had been deciding on information whose timing and origin it could neither control nor see.

**2026-09-03 — the experiment's intent is written down.** Give the agent proper tools inside reasonable restrictions and let it trade; where the two conflict the tie goes to the tool, because a restriction that exists only because nobody built the tool yet is a gap and not a rule.

**2026-09-03 — the prompt no longer claims research arrives tomorrow.** On the first trading day the agent commissioned AVGO at 13:35 and bought it an hour later, on an analysis it had been told would not exist until the morning.

**2026-09-03 — the first trading day, and three defects it exposed.** Five researches and a 29-share AVGO buy, all recorded correctly; the three faults had been live since the two-book removal and none of them touched the record.

**2026-09-03 — the simulated-account check was too narrow and stopped the agent.** It required a `DEM` prefix and the reset paper account came back as `DEL546C9`, so widening it to `DE` corrected a wrong observation rather than relaxing a guard.

**2026-09-02 — the experiment starts.** The container is deployed, the paper account is reset, and the agent is switched on with an empty book and nothing on its watchlist.

**2026-09-02 — the watchlist cap goes from 12 to 30, on a measurement.** The old number was derived rather than guessed, and both figures behind the derivation had stopped holding.

**2026-09-02 — the agent can say what it needs, and is told what went wrong.** It had been acting blind to its own refusals and broker failures, with no way to report a tool it was missing.

**2026-09-01 — everything except the agent is removed.** Both previous deployments stop and every manual control goes with them, because a person who can nudge the book puts a second decision-maker in the record and afterwards nothing can say which one produced a result.

**2026-09-01 — the agent's prompt and answer are recorded, and there is a page for them.** Behaviour here is mostly prompt, so a run whose prompt was not kept cannot be analysed afterwards.

**2026-09-01 — a negative balance no longer offers negative shares.** Every signal line had been reading "With your $-8.00 cash you can afford -1 share(s)."

**2026-09-01 — an empty or negative balance says so, instead of quoting itself as a spending limit.** "The buys you place must cost $-8.00 or less in total" is not an instruction anybody can follow.

**2026-09-01 — the watchlist section states what it costs, in dollars.** One line, after five days of the agent never once mentioning the watchlist.
