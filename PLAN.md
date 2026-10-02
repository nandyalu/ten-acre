# Plan: what to build next

Written 2026-09-19. Review on 2026-09-26, after a week of live passes under the Hold rule that changed on 2026-09-19.

This file records the decisions made on 2026-09-19 and the order of the work that follows from them. The decisions are made. Do not reopen one without new evidence from the record. The reasoning that led to each one is in the "Rejected" section at the end, so a future session does not repeat the argument.

## Experiment 2: many books, a simulated broker, and a test written first

Written 2026-10-02. Agreed with the maintainer. Not started.

**The question does not change.** Can an AI agent trade profitably when it has real tools? Experiment 1 cannot answer it. It has one book and about one trade a week. The same model gave Underweight and then Overweight on one ticker on one day. Thus the result of one book is one sample from a wide distribution, and years of it cannot separate skill from luck. Experiment 2 changes how we measure. It does not change what we ask.

**It stays in this repo. There is no fork.** `backend/services/broker.py` already sends each call to the module that `BROKER` names, and Alpaca came in that way. Each step below goes in behind a setting, so the live book runs unchanged until experiment 1 ends. A git tag keeps the code of experiment 1.

### Experiment 1 ends first

1. Let experiment 1 run to the reflection measurement on 2026-10-09.
2. Write a JOURNEY.md entry that closes it. Give the final record: equity, trades, analyses bought, notes.
3. Tag a release with the `release` skill. The tag is the record of the code that produced experiment 1.

### The order of work

Each step can merge while the live book runs.

1. **A simulated broker, behind `BROKER=sim`. Built 2026-10-02**, and checked against real Alpaca minute bars the same day. It is a third module beside the Webull and Alpaca modules, and it supplies the same functions that `broker.py` exposes.
   - **Why.** Many JOURNEY.md entries are broker defects, not agent behaviour: a sell that looked like a reversal (2026-09-05), a cancel that was accepted but not yet done (2026-09-08), combo orders, the holiday clock. A paper broker does not give real fills either. A simulator is also the base for steps 3 and 4.
   - **Fills.** A market order fills at the live quote from `quotes.py`, plus a fixed slippage in basis points that is a setting. A limit order, a resting stop and a resting target fill when a later price crosses them. A stop fills at the worse of its own price and the price that crossed it, so a gap down costs what it costs on a real exchange.
   - **Rules.** Long only, whole shares, no more cash than the book holds. A closed market refuses a buy or a sell and accepts an `adjust`, as now. Python refuses and never resizes, as now.
   - **Splits.** Handled by `corporate_actions` (item 9 under "Later"), which moves the simulator's orders to the new units with everything else.
   - **A fifth guard.** The sim module imports no broker SDK and opens no network connection. A test checks both. Add the guard to `CLAUDE.md` beside the four.
   - **Done when** a test drives a bracket buy, an `adjust`, a stop hit, a target hit and a sell through `BROKER=sim`.
2. **Replay. Built 2026-10-02** (`backend/scripts/replay.py`). This is item 1 under "Later", moved here.
   - Store every input of each pass: the prompt, every fetch result, every quote it read.
   - Stamp each pass with a hash of its system prompt and with the exact model ID.
   - A replay runs a stored pass again with a different prompt or a different model, and serves each fetch from the store. A fetch that the store does not hold returns "not available in replay", and the replay records the call.
   - Replay only dates after the knowledge cutoff of the model under test. Before that date, the model can know the outcome.
   - **Done when** it answers the first question that waits for it: does the 2026-09-19 prompt buy INTC on 2026-09-14 at $100?
3. **One container per book. Built 2026-10-02, in place of a `book_id` on every table.** `compose.experiment2.example.yaml` runs four containers from one image, each with its own data volume. Each book therefore has its own database, cash, positions, wakeups, memory notes, research charges and evening review, and no book can read another, by construction. No table changes and the live database is not migrated. The books share one API key, and each throttle counts only its own book's requests, so the per-day brake is per book. Each container has its own dashboard; `backend/scripts/experiment2_report.py` reads all of them for the result.
4. **Random control books. Built 2026-10-02** (`backend/scripts/experiment2_report.py`, which also applies the success test). No LLM. They cost nothing to run.
   - Build them after the fact, from the bar cache, for the same dates as the agent books. They live through the same market, so market luck cancels out of the comparison, and what remains is selection.
   - Each random book makes the same number of trades as the mean agent book. It takes its tickers from the candidate list of that day, and its holding periods from the distribution of the agent books. It holds no more cash than an agent book.
   - Every screen is stored in `candidatescreen` since 2026-10-02, before the watchlist is taken out, so every book of one day draws from one list.
   - A random trade enters and exits at the daily close. The agent trades at intraday quotes. Report this difference beside the result. Do not correct for it.
   - Build at least 500.
5. **Fetches for raw data. Built 2026-10-02.** The agent has `read`, `candidates`, `fundamentals`, `watchlist`, `track_record` and `ask_analyst`. Each of these gives it what someone else concluded, or a list. Add:
   - `bars`: the daily history of a ticker, through `bars.get_bars()`.
   - `news`: the items from Google News, Finnhub and SEC 8-K that `news_sources.py` already fetches for the analysis.
   - The TradingAgents analysis stays as research the agent may buy. It is no longer the only way to look at a stock.
   - A fetch is a prompt change. Write the JOURNEY.md entry, update `.claude/rules/agent.md`, and run `probe-the-prompt`, as `CLAUDE.md` says.
6. **One source of market data. Built 2026-10-02** (`backend/services/market_feed.py`). The same image runs as a market container with `MARKET_MODE=1`. It holds the Webull keys and runs no agent. Each book sets `MARKET_DATA_URL` and takes its quotes, daily and minute bars, candidate screens and corporate actions from it.
   - **Why.** The same input for every book. Without it, two books can see two prices for one ticker at one moment, and the books without Webull keys fall back to Yahoo's delayed close. Fewer vendor calls are a side effect, not the reason.
   - The market container keeps each answer for a short time (30 seconds for a quote, 5 minutes for a screen), so books that ask at nearly the same moment get the same answer.
   - A book that cannot reach it fetches its own data, and counts the fetch in `marketfetch`. The report lists those days for each book.
   - Not routed through it: `news`, `fundamentals` and the regime figures. Real-time alerts wait for evidence that the 15-minute poll costs something.
7. **Choose the model.** See "The model" below.
8. **Write the test, freeze, start.** Copy "The success test" below into a JOURNEY.md entry, word for word. Set a new `EXPERIMENT_START_DATE`. From that date to the measurement date, the prompt and the model do not change. A defect fix that does not change what the agent sees is permitted, with a JOURNEY.md entry.

### The success test, written before the start

**When.** Six months after the start. Before that date, no result is a verdict. If the agent books close fewer than 100 trades in total, the result is "inconclusive" by this rule.

**Primary.** Take the mean return of the agent books, from the start to the measurement date. Build the distribution of the mean return of N random books: draw 10,000 groups of N from the random pool, where N is the number of agent books. The agent passes if its mean is above the 95th percentile of that distribution.

**Secondary.** Report each of these. None of them changes the primary verdict.

- **Exposure-matched SPY.** A shadow book holds SPY each day at the fraction of equity that the agent book has invested that day. This separates stock selection from the choice to hold cash.
- **Plain SPY, buy and hold.** This is the headline number for the site.
- **Maximum drawdown** of each book.
- **Calibration** of the win probability the analysis states, on the Scorecard page, with its existing threshold of 20 graded signals.
- **Overlap.** The mean, over all days, of the share of tickers that two agent books both hold. If the books hold the same names, the real number of books is close to one. Report the overlap beside the primary result.

**Three results are possible:** pass, fail and inconclusive. All three are valid. In six months, inconclusive is the likely one, and the site says so if it is.

### The model

- **All agent books run one model.** One book for each model gives one sample per model again, which is the problem this experiment removes.
- **Pin the exact model ID.** Use no "latest" alias. A model that changes during the run changes what the experiment tests.
- **Gemini first.** `gemini-3.8-flash` on `GOOGLE_API_KEY_PAID`. The decision pass already talks to Google's SDK in `backend/services/llm_gemini.py`. Opus 5.5 needs a new client beside it. Test it only if the Gemini result leaves a reason to.
- **How to choose:**
  1. Run each candidate through the `model-change` skill. A model that fails the tool-calling test is out.
  2. Replay the recorded passes from September on each candidate (step 2). Those dates are after the knowledge cutoff, and the outcomes are known now. This is thin evidence. It is better than a guess.
  3. Calculate the monthly cost: books × passes per day × prompt tokens per pass. For example, 10 × 6 × 15k–51k gives 1M to 3M input tokens a day, before prompt caching. The fixed system prompt caches. Use the current price list, not a remembered price.
  4. Choose the strongest model whose monthly cost fits the budget.
- **A model comparison comes later, as a second group.** It needs at least 8 books for each model. It never uses one book for each model.

### What does not change

- **No manual control, and no second decision-maker.** Each book has one author. The books do not talk to each other, so the "second agent" rejected on 2026-09-19 does not apply.
- **Python refuses and never resizes.**
- **No shorting.** The simulator refuses a sell that is larger than the position.
- **No analysis the agent did not order.** The random books order none.
- **The four guards stay** for Webull and Alpaca, with no change.

### Open, for the maintainer

- **Decided 2026-10-02: the budget is $50 a month.** At the estimate of 2026-10-02, $10.50 to $14 for each book each month on `gemini-3.8-flash` with 24% of input cached, that pays for 3 or 4 books. The live book runs on that model for the week of 2026-10-05 (JOURNEY.md), and the cost of that week sets the number of books. Four books make the primary test less sensitive than eight, but the test stays valid: the random groups are drawn at the same size.
- Whether the site keeps a page for experiment 1 after experiment 2 starts.

## The record on 2026-09-19

| | |
|---|---|
| Decision passes since 2026-09-10 | 39 |
| Trades | 1. INTC, 4 shares, stopped out in 100 minutes |
| Analyses bought | 20: 14 Hold, 4 Underweight, 1 Buy, 1 Overweight |
| Notes to the maintainer | 2, both on 2026-09-10, both the same timing complaint |
| Prompt tokens per pass, JSON channel | 3k to 5k |
| Prompt tokens per pass, tool channel | 15k to 51k |

The harness was the bottleneck for these nine days, not the agent. The pipeline said Hold in 14 of 20 analyses, and the prompt said a Hold was not a reason to buy, so the agent sat in cash. That rule changed on 2026-09-19. Nothing in this plan ships until the week of watching below has been read.

## The week of watching: 2026-09-19 to 2026-09-26

Read these four counts from the Decisions page and the `agentrun` table before you build anything.

- **Buys on a Hold that carried a plan.** Count the passes that bought on one and the passes that read one and passed. The 2026-09-19 probe bought in 4 of 4 samples. If the live book buys on every Hold with a plan, the rule has swung too far. If it never buys, the rule did not land.
- **Position size against the stop.** The same probe had one sample put 95% of the book on a stop 1.6% under the entry. If that happens live, the fix is a fact line shown to the agent, "this buy is X% of the book", not a cap. A cap needs its own JOURNEY.md entry, as CLAUDE.md says.
- **Limit orders against market orders.** The change note announcing limit orders reached the agent on 2026-09-19. Count how many buys named a limit price.
- **The fetch counts** from step 1 below, which start now because they are read-only.

## Step 1: the tool-usage audit

**Built 2026-09-26** as `backend/scripts/fetch_audit.py`. The "quoted" column counts a pass when its reasoning carries a figure that only the fetch's result held. A laya judge was tried first and failed: every call scored between 0.56 and 0.77. The first run, on nine tool-channel passes from 2026-09-17 to 2026-09-25:

| Fetch | Passes that called it | Calls per calling pass | Passes that quoted it |
|---|---|---|---|
| `read` | 8 | 2.4 | 3 |
| `candidates` | 1 | 1.0 | 0 |
| `fundamentals` | 0 | - | 0 |
| `watchlist` | 9 | 1.3 | 3 |
| `track_record` | 9 | 1.3 | 2 |

Nine passes are fewer than the twenty the rule below needs, so nothing is removed yet. `fundamentals` is on course to be: nine of nine passes had it and none called it.

**What it is.** For every tool-channel pass, count the calls to each fetch by name, and whether a figure from the fetch's result appears in the turn's reasoning. The data is in `agentrun.turns[i].exchanges`, stored since 2026-09-17, and `agentrun.turns[i].reasoning`, stored since 2026-09-15.

**Why it comes first.** Five features have shipped "unconfirmed as read": the price-range column, the four analyst tables, `fundamentals`, limit orders, and the change-note section. Adding a sixth before counting the five is the wrong order. The audit also tells the reflection call in step 2 which tools to ask about.

**How.** A script under `backend/scripts/` that reads the database and prints one table: fetch name, passes that called it, calls per pass, passes whose reasoning quotes a figure from it. Read the reasoning for the last column; do not grep for the fetch's own words, because the model paraphrases.

**Decision rule.** After twenty tool-channel passes, a fetch that no pass called is removed, with a JOURNEY.md entry. A fetch that is called and never quoted stays, marked unconfirmed, the status the analyst tables already have.

## Step 2: the end-of-day reflection

**Built 2026-09-24**, in `backend/services/reflection.py`, and shipped in v0.3.0 so it runs for the first time on 2026-09-25. The contract as built is in `.claude/rules/agent.md` under "The evening review". Two things differ from the design below, and both come from the removal of the change-note mechanism on 2026-09-21: the review is shown the decision pass's fixed rules under "if it is in these rules, you have it" instead of the last seven days of change notes, and the decision pass learns the review exists from one sentence added to the fixed rules, with the next prompt labelling a note that came from the review. The measurements two weeks out are unchanged.

The agent reviews its own day once, after the close. It speaks to the maintainer first, then to itself. It cannot act.

### Decisions made

- **Two turns in one conversation, the maintainer first.** One obvious job crowds out the rest in every probe on record, so the two questions are not asked together. The maintainer note comes first so the self-review does not make it an afterthought.
- **Turn 2 sees turn 1.** The context is already there, and the guard against working around a reported gap needs something to point at. The probe below checks whether turn 1 primes turn 2 toward workarounds.
- **Both turns run daily to start.** The memory churn count after two weeks decides whether turn 2 moves to weekly. Weekly from day one would hide the churn we want to see.
- **The reflection's wakeup note replaces the final pass's note in the morning prompt.** Two notes on one subject would be two copies, and the model reconciles copies and keeps one. The record keeps both.
- **The reflection has its own table.** It is not a pass. The passes table feeds the next wakeup and the last note, and a reflection must not count there.
- **Memory notes get a date and a source.** The source is `pass` or `reflection`. The review in turn 2 needs the date to judge whether a note has earned its place.

### When it runs

Once per trading day, after the final pass and after the evening grading, so the day's outcomes are in the record before the agent reads its day. Skip weekends, holidays, and any day with no pass. Two requests, both counted against the throttle.

### What it sees

- The date, and a line saying the session has closed.
- Equity at today's close against yesterday's, and the realized and unrealized change.
- Every pass of the day, oldest first. For each: why it woke, then each turn's reasoning, the fetches by name and arguments, the orders as parsed, the outcomes, the refusals, and the broker failures. Reasoning, not prompts: a prompt runs to 50k tokens, and the stored per-turn reasoning runs to a few hundred characters.
- The trades that filled today, and the positions that closed today with their result.
- The signals the evening grading scored today, with the analyst's verdict and what the price did over the horizon.
- The memory notes, each with its date and source.
- The note or notes the final pass left for the morning.
- The change notes from the last seven days, so it does not ask again for something already built. A decision pass sees three passes' worth; that is not enough here.

It does not see live prices, the candidate menu, or any fetch function.

### Turn 1: to the maintainer

One forced function call, `report`. Its one field is a list of notes. Each note has a kind, the pass it points at, what was missing, and what the agent would have done with it.

- The kinds: `missing_tool`, `missing_information`, `rule_conflict`, `other`.
- The pass is required. A note with no pass behind it is invented.
- An empty list is a valid answer and the expected one on most days.

### Turn 2: to itself

One forced function call, `revise`, with two fields, both optional.

- `wakeup_note`: the note the morning pass should see, or nothing to keep the final pass's note.
- `memory`: a list of changes. Each is `add`, `rewrite` or `remove`, with an index for the last two and a text for the first two. An empty list keeps memory as it is.

The prompt for this turn opens by showing what the agent reported in turn 1.

### What neither turn may do

No buy, sell, adjust, cancel, research or untrack. No change to the next wakeup time. The final pass chose the alarm, and a reflection that moves it is a decision pass in disguise.

### The guards, written into the prompt

Each guard answers one way the reflection goes wrong.

1. **A rule from one trade.** Memory notes are powerful: on 2026-09-16, 3 of 4 samples followed "skip INTC" and passed on a live signal. So the prompt says: a lesson from one trade is a hypothesis, and you write it as one, with the count. "INTC stopped me once on a breakout; watch for it" is a note. "Avoid INTC breakouts" is a rule you have not earned.
2. **Outcome bias.** A good decision can lose. The prompt says: judge a decision by what you knew when you made it, and give the reason behind a lesson, not only the result.
3. **Memory churn.** Memory holds ten notes and evicts the oldest silently. The prompt says: adding is rare. Review the notes you have first. Is each still true? Should two merge? Should one go?
4. **Working around a gap.** The prompt says: a tool you lack is the maintainer's to build. Do not write a memory note that substitutes for something you reported in turn 1.

And in both turns: nothing to report is a valid answer.

### Storage and the record

- A new table for reflections: the day, both prompts, both responses, the thinking, the parsed notes, the wakeup note, the memory changes, the tokens and the seconds, and the channel.
- The morning pass reads its wakeup note from whichever came last, the final pass or the reflection. The pass's own note stays on its row.
- Memory notes change shape from a list of strings to a list of `{text, written, source}`. A row from before the change reads as text with no date, the same convention `_encode_wakeup_notes` uses.
- Discord gets a post when turn 1 is not empty or memory changed. The dashboard shows every reflection beside the day's passes.
- Tool channel first, with the same text fallback the decision pass has.

### Before it ships

1. **Two JOURNEY.md entries.** Turn 2 changes what the next pass is shown, which is the behavior test. Turn 1 is a new record type, which is the evidence test.
2. **Somewhere to tell the decision pass an evening review exists**, so a morning pass does not find its note changed with nothing saying why. `backend/agent_changes.json` was that channel and was removed on 2026-09-21 with the rest of the change-note mechanism. **Answered 2026-09-24:** the fixed rule about the wakeup note ends "After the close you review your day, and that review may rewrite this note before the next pass; the next prompt says so when it does", the memory rule ends "Your evening review may rewrite or remove one", and the next prompt labels a revised note as the review's.
3. **Probe it.** Build the reflection prompt from a copy of the live database, four samples, and read the reasoning. Three checks, one per guard:
   - Does every turn-1 note name a real pass and a real moment, or does it invent one?
   - Does turn 2 write a rule from the one INTC trade, or a hypothesis with a count?
   - Does turn 2 ever write a memory note that works around something turn 1 reported?
4. **The `stale-check` skill**, then commit.

### Probed on 2026-09-19, before the build

Four samples on `gemini-3.5-flash-lite`, from a throwaway container on the deployed image, against a copy of the live database. The window was the whole week of 2026-09-14 to 2026-09-19, not one day, so the model had a trade to look at. The result is in `data/probe/reflection-20260920T020728.json` beside the script that made it. Two forced calls per sample, `report` then `revise`, in one conversation; 4 of 4 called both.

| Check | Result |
|---|---|
| Turn-1 notes that name a real pass and a real moment | 1 note in 4 samples, on pass 28, the INTC pullback plan. No sample invented a tool. |
| Turn-1 notes that ask for something already built | 1 of 1. Limit orders, announced in the 2026-09-19 change note. The other 3 samples reasoned from the change notes that every gap they felt was already answered, and reported nothing. |
| Memory notes written as a rule from one trade | 0 of 3. All three carry the count, "stopped me out once". |
| Memory notes that work around a reported gap | 0 of 4. Sample 1 drafted one, quoted the rule, and dropped it. |
| Wakeup notes rewritten to drop the restated cash balance | 4 of 4, each citing the rule. The decision pass has restated the balance since 2026-09-12 and no prompt change stopped it. |
| Samples that read the change notes | 4 of 4, by their own reasoning. In the decision pass the same section was quoted by nobody. |
| Cost per sample | 2 requests, about 20k prompt tokens and 2k to 3k completion, 13 to 17 seconds. |

Four changes to the design follow from it.

- **The example in the memory rule must not name a ticker in the record.** The probe's example read "INTC stopped me out once on a breakout entry", and all three memory notes opened with those words. The content was real, the $97.00 stop and the four shares, but the shape was copied. Use a made-up ticker.
- **Add a guard against restating a rule the prompt already carries.** Two of the three memory notes end in "size from the distance to the stop and ATR", which is the 2026-09-19 sizing rule. A memory slot that repeats a rule is a slot lost. The prompt should say: memory is for what you learned, not for what you are told.
- **Empty memory pulls an add.** Two samples called an empty memory "not ideal" or weighed it against "a blank memory". The churn count is the measurement, but the wording should say plainly that most reviews add nothing.
- **This week is weak evidence for turn 1.** Every gap the agent felt this week had a change note answering it, because the maintainer answered daily. An empty report is the honest answer for such a week, and it says nothing about whether the model can find a gap nobody has answered. Re-probe turn 1 on a week that has one.

### Measure two weeks after it ships

| Count | What it decides |
|---|---|
| Turn-1 notes that led to a change, against notes that were wishes | A bad ratio ends turn 1 |
| Memory adds and removes per week | More than one or two a week moves turn 2 to weekly |
| Morning passes that paraphrase the revised wakeup note | Whether the revision is read at all |

Use the paraphrase test from the `probe-the-prompt` skill for the third count. A keyword match is not a read.

## Step 3: the `ask_analyst` fetch

**Built 2026-09-26**, with a JOURNEY.md entry. **Not probed yet**: the probe below needs a container with the Gemini key, and it waits for the maintainer.

**What it is.** A fetch beside `read`. The agent sends one question about a stored analysis. The app answers from the full reports, which the agent never sees in full, and the exchange is recorded like any other fetch.

**What keeps it a tool.** The answer comes only from the stored reports, and the answer says so when the reports do not hold it. `analysis.answer_question` already has that instruction in its system prompt. Keep it. A grounded answer over data the agent cannot fit in its prompt is retrieval. An ungrounded one is a second opinion, which is the thing rejected below.

**Cost.** One round per question, inside the fetch ceiling, plus one quick-think model call per answer.

**When.** After the audit in step 1, so the count of `read` calls says whether the agent uses what it can already see.

**Probe.** Does the agent call it, and does a figure from the answer reach its reasoning?

## Later, in this order

1. **Replay. Moved to step 2 of Experiment 2 on 2026-10-02.** Run a past pass again against a changed prompt. Prompt changes land daily and the market gives one trade a week, so this is the only way to learn faster than the market. The first question is waiting: would the 2026-09-19 prompt have bought INTC on 2026-09-14 at $100?
2. **Built 2026-09-26: Google News, Finnhub and SEC 8-K, with a laya grade on each item.** See `news_sources.py` and the JOURNEY.md entry. **The laya grade was shelved on 2026-09-27**: only a mention score separated on headlines, and no pair question could grade the fetch audit. `LAYA_URL` stays unset, and the sources run without it. **News and sentiment sources for the analysis**, in `TradingAgents/tradingagents/dataflows/`. The pipeline says Hold most of the time, and the agent can only act on what the analysts say. A person who picks trades reads news, MACD-style signals and sentiment; the sentiment analyst tries to give the model the same, but Reddit alone is thin and unreliable, so the analysis is poorer for it. Benzinga and MarketWatch's free feeds were tried for candidate *discovery* on 2026-09-15 and dropped there, because a headline names a company and not a ticker, and to guess one is to invent a fact. **That objection does not apply to a news source that feeds an analysis**: the ticker is known before the fetch, so to resolve it to a company name for the query is a one-time, free lookup — Webull and yfinance both return one from a profile call — and not a guess made after the fact. This is submodule work.
3. **Measured 2026-09-26, nothing trimmed:** `python -m backend.scripts.prompt_cost` prints the tables to decide from. **The tool-channel prompt cost.** A tenfold rise per pass is free on the free tier and not free anywhere else. After the audit says which fetches matter, trim what each returns.
4. **Half built 2026-09-26: a read now lists every other analysis of the ticker.** The probe samples are still owed. **The ambient signals table, and more probe samples for the analyst snapshot.** The table shows the last analysis for every tracked ticker with no way to reach the four analysts behind it. The snapshot added on 2026-09-15 fixed this only for a `read`, and 2 of 2 probe samples ignored it. The step 1 audit answers the first half of this; the second half needs more samples.
5. **A ticker-discovery fetch.** The news and sentiment sources in item 2 feed an analysis that is already running. The same sources can also let the agent choose what to research, as a fetch it calls when it wants one, not as standing prompt text that costs tokens every pass. This depends on item 2 and is a second use of it, not a restatement.
6. **The backtester.** Grade the strategy over history, as a third baseline beside SPY and the mechanical follower. Write it against the bar cache. It shares the replay machinery in item 1, so it comes after it.
7. **The watchlist ageing rule.** Nine names of thirty. Not pressing.
8. **Built 2026-09-26: Alpaca paper, behind `BROKER=alpaca`.** Proved on paper with the market closed; fills and an OCO on a real position wait for `backend/scripts/alpaca_paper_check.py` during a session. See `.claude/rules/alpaca.md`. **A broker a self-hoster can sign up for.** Webull OpenAPI needs a funded brokerage account, a separate access application, and one of three regions. That is a real barrier for anyone who wants to run their own experiment, and the reason to move is portability, not risk. Alpaca paper keys need an email address and nothing else. The 12 functions in `backend/services/sandbox_broker.py` are already the interface, so a second module with the same names and an env selector is the whole change. Three things are not free: the four guards are Webull-specific and Alpaca needs its own written into CLAUDE.md, not a deletion; `place_bracket_order` and `place_exit_bracket` must be checked against Alpaca OTOCO on a paper cash account before this is committed to; and `compose.example.yaml`, the Webull block in `backend/services/setup_check.py`, and `docs/` move with it.

9. **Built 2026-10-02 as `corporate_actions`, on the experiment-2 branch: splits and spin-offs are applied to bars, ledger, simulated orders and signals.** Split-adjusted bars, or a break guard in every reader of the bar cache. The cache stores raw bars. CTVA read $77.65 on 2026-09-30 and $12.57 on 2026-10-01, and the four stock cells showed it as `DOWN: 50d -84.3%` until `trend._break` learned to withhold a row across a one-session break. The ATR stop, the range since purchase and the grading read the same bars and do not know. Found by the probe of 2026-10-01; see `.claude/rules/agent-probes.md`.

## Rejected on 2026-09-19. Do not reopen without new evidence.

**A second agent the main agent talks to.** Three reasons, all from the record.

- The agent already commissions a debate: bull and bear researchers, a research manager, a trader, three risk debaters and a portfolio manager. What it lacks is a follow-up question, and that is step 3.
- The same model at temperature 1 agreed with itself in 2 of 12 paired analyses. A second copy adds randomness, not a view, and afterward nothing can say which voice a decision came from. CLAUDE.md's "second decision-maker in the record" applies to a second model as much as to a human hand, whenever the second voice can change the outcome.
- One trade in nine days cannot measure it, and a dialogue is prose, which this repo has measured gets skimmed.

**Feeding turn-1 notes back into the agent's prompt.** That would make the reflection a self-training loop. Memory notes are the agent's channel to itself. Turn-1 notes are its channel to the maintainer, and since 2026-09-21 there is no channel back — the change-note mechanism that answered them is gone.

**Letting the reflection change the next wakeup time.** The final pass chose the alarm. A reflection that moves it has become a decision pass.

## Checklist for 2026-09-26

- [x] Read the four counts from the week of watching. None changes a rule. The pipeline gave one Hold in six analyses, and that one carried no plan, so the Hold rule was not tested. One buy in five named a limit price.
- [x] Run the step 1 audit script and read its table. Built and run 2026-09-26, on nine tool-channel passes, fewer than the twenty the rule needs. The table is under step 1.
- [x] Decide whether the sizing fact line is needed, from the live record. Not needed: the largest new position was about 36% of the book with its stop about 7% under the entry.
- [x] Write the two JOURNEY.md entries for the reflection, then build it. Done 2026-09-24.
- [x] Probe the reflection with the three checks, and record the result in `.claude/rules/agent-probes.md`. The pre-build probe is there; the real prompt was probed on 2026-09-24 before the release.
- [x] Set the two-week measurement date from the ship date: **2026-10-09**, two weeks after the first review on 2026-09-25.

## Rejected on 2026-10-01. Do not reopen without new evidence.

**A trained price-and-volume classifier with a confidence score, and a conflict matrix that sizes positions from it.** Proposed 2026-10-01 as a secondary signal beside TradingAgents: two LightGBM models per ticker on five years of daily bars, a calibrated confidence, and a matrix that set the position size from the model's and the analysts' agreement. In one line each: the matrix has Python cut or refuse an order because a second model disagrees, which is a second decision-maker and a resize, both forbidden by `CLAUDE.md`; a per-ticker model on five years of daily bars overfits and its calibrated confidence is an unverified figure that reads as data; injecting the score into the analysis puts it in prose the probes found read 0 of 4 times. What was built instead is four facts about the stock, shown as cells, with nothing in Python deciding from them. The first probe found those cells unconfirmed as read, 0 of 13. Three more looks the same night found them read on the candidate table, where the picks followed the cells, and reachable but not reliable elsewhere (`.claude/rules/agent-probes.md`); the next step is a scenario where the trend and the analyst disagree and the trade is open, not a model.
