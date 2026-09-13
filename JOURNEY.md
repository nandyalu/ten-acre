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
