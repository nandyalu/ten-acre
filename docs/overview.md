# How it works

## The moving parts

```
┌─────────────────────────────────────────────────────────────────┐
│ FastAPI app (backend/app.py)                                    │
│                                                                 │
│  Scheduled tasks · the JSON API · the dashboard                 │
└──────┬──────────────┬───────────────┬───────────────────────────┘
       │              │               │
       ▼              ▼               ▼
 TradingAgents   Rule-based       SQLite (data/trading.db)
 (the LLM        market logic     signals · outcomes · reports
 pipeline)       (no LLM):        the agent's book and its runs
 analysis        watchdog,        alerts · bar cache · settings
 pipeline        regime, grading,
                 the agent
       │              │
       ▼              ▼
  A local pool    Market data: Webull first for quotes and
  or a hosted     daily bars when configured, yfinance as
  API endpoint    the fallback and for the earnings calendar
```

Two kinds of intelligence run here, and the bot keeps them separate on purpose.

- **The LLM pipeline** (TradingAgents) runs the analysis. This work is expensive and slow. Four analysts — market/technical, news, sentiment, and fundamentals — feed a bull-vs-bear research debate. A trader drafts a plan, and a risk team picks one of five decisions: **Buy, Overweight, Hold, Underweight, Sell**. The rationale usually includes a price target and a time horizon.
- **Rule-based logic** handles everything that must be fast, cheap, and reliable: it grades outcomes, watches prices intraday, classifies the market regime, derives a stop from volatility, and screens the agent's orders against a running book. No model is involved, so none of this can hallucinate.

**The division between them is the load-bearing part.** The model decides what and how much; Python refuses what cannot be executed as stated, and **never resizes**. Shrinking an order would quietly turn the agent's decision into a different one, and the record would then describe a strategy nobody chose.

## Where things are served

One process serves four things on the same port:

| Path | What |
|---|---|
| `/` | The dashboard. Start here. |
| `/docs` | These pages. |
| `/api/…` | The JSON API. |
| `/api/docs` | The interactive API reference, generated from the code. |

The API reference used to sit at `/docs` and hid this site completely, because FastAPI registers that route before the docs are mounted.

## Discord and the dashboard

The two are for different jobs, and neither is a lesser version of the other.

**Discord tells you when something happens.** The agent decided, a stop filled, a target was touched, a position is unguarded. It arrives where you already are, and it is short.

**Discord takes no orders.** There are no slash commands — the app had twenty-three and all were removed on 2026-09-01. A control that lets a person nudge the book puts a second decision-maker in the record.

**The dashboard tells you what is happening and what already happened.** It is where the price chart, the analysis, the signals, the alerts, and the agent's fills sit on one time axis — plus the Decisions page, which keeps the prompt the agent saw and the answer it gave, word for word.

The dashboard needs no Discord account, and Discord needs no dashboard. Run either, or both.

## The signal lifecycle

1. **Born** — An analysis runs, either because the agent itself commissioned it (a fresh look at a name, held or merely watched, at $0.05) or because a move-triggered or earnings-driven event asked for one. The app stores the decision, the full rationale, all analyst reports, the price at that moment, the parsed time horizon, and the trade plan (see below).
2. **Read** — The agent sees it at its own next decision pass — nothing runs on a fixed clock time any more — alongside up to eleven others from the last three days. It may act on it or ignore it.
3. **Watched** — While the signal matures, the intraday watchdog sends an alert if the price touches the signal's target or reaches a stop level on a held position.
4. **Graded** — When the time horizon arrives, the bot fetches the full price window since the signal and grades it three ways:
   - **Absolute**: A Buy passes if the price rose at all. A Sell passes if the price fell. A Hold passes if the price stayed inside the horizon's band.
   - **vs SPY**: A Buy must have beaten SPY over the same window. A Sell must have lagged SPY — selling something that then underperformed the market was the right call. A Hold passes if alpha stayed inside the same band.
   - **Target**: Did the price touch the target at any point in the window (using the window's high and low, not just the endpoint)?
5. **Aggregated** — Every graded signal feeds `/scorecard` (win rates by decision type and ticker) and the weekly digest's rolling trend.

**A signal is graded on its horizon, never on when a trade in it was closed.**
That distinction matters once the auto trader is running. If a stop firing resolved the signal, every losing trade would settle early while every winner ran to full term — you would stop measuring the prediction and start measuring your stop placement, with nothing saying so. The vs-SPY grade needs a fixed window for the same reason.
So the signal keeps its own verdict, the trade keeps its own result, and the signal detail page shows both. A signal that passes beside a trade that lost money is not a contradiction: it means the call was right and the stop was too tight, which is a different problem from a bad call.

The vs-SPY grade exists because an absolute grade can mislead you in a trending market.
For example, a Buy that gained 1% while SPY gained 5% "passed" on the absolute grade, but it cost you money against the obvious alternative.

## The trade plan

Every signal carries five numbers beyond the decision itself, taken from the trader stage of the analysis:

| Field | What it means |
|---|---|
| Entry price | The level the trader proposed entering at |
| Stop loss | The level at which the thesis is wrong |
| Win probability | The model's own estimate that the thesis plays out, 0 to 100 |
| Risk / reward | Reward divided by risk, from the entry, stop, and target |
| Expected value | `p × rr − (1 − p)`, in R-multiples. Positive means the bet pays at the stated probability |

The last two are computed in Python from the levels, not asserted by the model, so they always agree with the levels shown beside them.
The win probability is the one number the model produces rather than derives, which makes it the one worth checking against your own scorecard before trusting.

Any of these can be absent — the model states them only when it has a view.
Absent is stored as null, never as zero, so a missing stop can never be read as a stop at $0.

**A signal made while the market is shut is priced at the last completed close, not at the last trade.**
Pre-market sessions open at 4:00 in New York, so the quote a broker reports before the open can be a thin print with a wide spread — hours before the market agrees what the stock is worth.
Every level on the plan would then be drawn against a number that was never really the price, and the auto trader would buy at the open into a different one.
A completed close is what the whole market settled on, which is the same reasoning the bar cache follows: today's bar is never stored, because it is still moving.

A level is also dropped when it cannot describe anything that has not already happened.
The model often proposes buying a pullback, and draws its stop and target around the entry it hoped for rather than the price on the screen; when the pullback never comes, the whole plan sits below the market.
A target the price has already passed would count as reached the moment it was stored, and a stop above the price would trigger the same way, so both are discarded, along with the risk/reward and expected value computed from them.
The win probability survives, because the model estimates it rather than deriving it from the levels.
A third of the first forty signals held a level like this.

When the trader names no stop on a Buy, the bot substitutes the same 2×ATR(14) level the sizing field shows, so every actionable Buy has an exit.

## Is the confidence worth anything?

The win probability is the one number a signal asserts rather than works out.
Risk/reward is arithmetic on the levels, and expected value is arithmetic on the probability and the risk/reward together — so an inflated probability quietly inflates every figure computed downstream of it.

The Scorecard page now checks it. Resolved signals are grouped by the confidence the model claimed, and each group's claim is set against how often it was actually right.

Two ways for the number to be wrong, and the page distinguishes them:

- **Overconfident** — the claim is higher than the outcome. This is the direction that costs money, because every expected value on the dashboard reads positive when it is not.
- **Undiscriminating** — the claims can be right on average and still useless, if the model's confident calls do no better than its doubtful ones. A number that does not sort outcomes cannot inform a decision, however well centered it is.

It refuses a verdict under twenty graded signals, and it refuses to say whether the number sorts outcomes under the same threshold.
That second refusal is deliberate. On the first nineteen graded signals the bands ran 40%, 83%, and 50%, and comparing only the ends of that returns "yes, it sorts" from a shape that plainly does not.

Signals where the model stated no probability are skipped rather than counted as zero — declining to make a claim is not a claim of 0%.
The grade used is the absolute one, not vs SPY: "the thesis plays out" is a claim about the stock, and the benchmark grade asks a second question the model was never answering.

## Comparing two models

**The paired comparison sweep was removed on 2026-09-01.** One model runs now. The script that ran a second model over the same tickers is gone, along with the two settings that started and stopped it.

`Signal.model` stays, and so does the Scorecard's **by model** table. Which model produced a row is a fact about that row, and the table fills on its own whenever the configured model changes.

**What that costs is worth stating.** A comparison run on the same day, at the same prices, from the same news, is the only clean way to compare two models — the market moves more between two days than the models differ. Switching the model now means the days either side of the switch differ in both the model and the market, and nothing separates them.

So a model switch is readable only in aggregate, over enough resolved signals on each side, and the by-model table is where to read it.

## Stop alerts

Two alerts watch a held position, and they answer different questions:

- **Thesis broken** (`signal_stop`) — price reached the level the analysis named. The decision to exit was made at entry; this is the reminder to carry it out.
- **Below your cost** (`stop_loss`) — price is a set percentage below what you actually paid. A backstop for the account, unrelated to any thesis, and the only one available for a position with no signal behind it.

Either can fire without the other, and each fires at most once, so neither hides the other.

## Trade horizon

Every analysis runs at one of two horizons, set on the settings page.
The horizon reaches the analysis prompts, so it changes what the model looks at, and it sets both grading parameters.

| | Swing | Position |
|---|---|---|
| Intended hold | 1 to 2 weeks | Around 6 months |
| Graded after | 14 days | 30 days |
| Hold passes within | ±4% | ±10% |
| The analysis weights | Near-term momentum, technical setup, immediate catalysts | Durable trends and fundamentals |
| Market analyst prefers | 10 EMA, MACD, RSI, Bollinger, ATR, VWMA, MFI | The full indicator set, including the 50 and 200 SMA |

The two bands differ because both scale with the length of the window.
A ±10% band over six months is reasonably tight; over two weeks it is so wide that almost every Hold passes, and the grade stops telling you anything.

Each signal records the horizon it was made under, so changing the setting never re-grades older signals by new rules.
It does mean a scorecard covering both horizons is comparing two different questions — start fresh after a switch.

## The analysis model

Every analysis runs on one LLM, chosen on the settings page, from whatever the configured endpoint serves.
The default is the model the stack's `TRADINGAGENTS_DEEP_THINK_LLM` names, so leaving the setting alone changes nothing.

Each signal records the model that produced it, and the scorecard grows a "By model" table as soon as a second model has resolved signals.
That table is the only reason to switch: a new model's win rate has to be readable next to the old one's, not blended into it.
Give it enough signals to mean something — a win rate under 20 resolved signals is noise whichever model produced it.

Two things go wrong more often than a bad answer does:

- **A model with a small context window fails partway through.** The analysis carries the whole tool-call history, and once that no longer fits, the reasoning loop stops terminating and the run dies with a `GraphRecursionError`. This is why the default is a custom `gemma4-e2b-96k` build rather than stock `gemma4:e2b` — same weights, 96k of context instead of 8k.
- **A model that answers in another language produces signals nothing can read.** The decision, the price levels, and the trade plan are all parsed out of the text. A rationale in Indonesian is not wrong, it is simply invisible to every stage after it.

Watch the logs after a switch, not just the win rate.

### What a run costs

Every signal also records how long its analysis took, how many LLM calls it made, and how many tokens went in and came back out.
The Discord embed carries a short version in its footer; the signal detail page shows all of it.

This is here for one decision: whether to keep self-hosting or move to a cloud provider.
The two are not billed in the same currency — a local model costs GPU time on hardware you already own, a hosted one costs tokens on an invoice, and providers charge several times more for output tokens than input ones.
Recording both, per run, from the provider's own accounting rather than an estimate, is what makes the comparison an arithmetic problem instead of a guess.

A run that was never measured stores nothing rather than zero, and shows nothing rather than a free run.

Each signal now shows what its run **cost**, and the two kinds of cost are kept apart because they are not the same kind of dollar:

- A **vendor** figure is estimated at list price, input and output priced separately. They differ by roughly eight times, so a single blended rate is a property of the workload's output ratio rather than of the model — blending one derived from a 9%-output run into a 16%-output history understated it by 39%.
- A **self-hosted** figure is the electricity the GPU actually drew, at 100 W for the length of the run. Reporting $0.00 would be the wrong kind of true: it reads as free, when it is really paid for in GPU time.

Neither is stored. Prices change, and a dollar figure written into the row would freeze one day's rate card into the record and quietly stop matching. The tokens are the fact; the cost is the reading.

A vendor figure is an estimate, not the invoice. Two billed readings so far came in at 78% and 103% of list.

## The auto trader

A simulated Webull account the model trades on its own, inside a budget you set (default $10,000).
It runs on its own schedule, not a fixed one.

Every decision pass ends with the agent naming when it wants to be asked again — five minutes to four days out. Naming nothing means it is asked at the following open, and a final pass always runs five minutes before the close, whatever it asked for, so no position goes into the night unreviewed.
And whenever an intraday analysis is triggered during market hours, by an unusual move or a volume spike, it decides again on the spot, at most once every 30 minutes.

The split is deliberate. A pass the agent scheduled itself is a plan it already made — it can wake before the open to see what has gone stale and commission a fresh look, or through the day to reassess a position.
An intraday trigger is the opposite: it arrives because the market just moved, while the market is open, and a move worth analyzing at midday is worth nothing by the next morning.

**No order can reach a real account.** The app holds sandbox credentials only, and the order path checks four things before every single order: that the sandbox flag is set, that the account number is marked simulated, that the account is the individual-cash one, and that it is the specific account this deployment was told it owns.
There is no sync of a real brokerage account at all. It was removed on 2026-09-01 along with the book it fed.

The fourth check is the newest and the least obvious. The first three narrow the sandbox's accounts to one — but they narrow to the *same* one for any deployment applying the same rule, so two containers would trade a single book and afterwards nothing could say which of them placed an order. `WEBULL_ACCOUNT_ID` is a person writing down which book this container owns, and it has no default: unset, the app places nothing.

### What a decision pass records

Each pass stores what it cost and what the model was thinking: the seconds it took, the tokens in and out, and **the model's own reasoning before it answered**.

That last part used to be generated, billed and thrown away, and it was about 85% of the output tokens on a reasoning model — so the record held the decision and none of the argument for it. The decision is what the agent did; the reasoning is the only evidence about *why*, and it is the thing a reader actually wants when a trade looks strange.

### How a position gets its exits

Every buy is placed as a **bracket**: the purchase, a stop where the analysis says the thesis is wrong, and a take-profit at its price target, all in one order.
The broker holds the two exits inactive and activates them the moment the buy fills, so whichever fills later cancels the other, and both are enforced whether or not this app is running.

The single order is the point. Placing the exits afterwards leaves a window — however short — in which the shares are owned and nothing is protecting them, and that window is not theoretical: on 13 August 2026 two positions were bought and neither got its exits, because the broker read the sell orders as short sales against shares that had not settled yet.
A bracket cannot fail that way, because there is no "afterwards".

Either level may be missing, since the analysis states them only when it has a view, and the app discards one that would execute the instant it was placed — a target the price has already passed, or a stop it has already fallen through.

**A discarded stop is replaced, not simply dropped.** When the stated stop cannot be used, the agent derives one from the stock's own volatility at the moment it buys — 2×ATR below the purchase price, which cannot come back on the wrong side by construction.
This matters because days pass between a signal and the purchase. NOK was bought at $10.47 against a $10.56 stop and INTC at $91.84 against a $94.00 stop; both stocks had fallen through their own stop in the meantime, so both levels were correctly discarded and both positions opened with nothing under them.
The signal-recording stage already substitutes an ATR stop, but only for Buy and Overweight decisions — and the agent buys on Hold signals too, which is exactly how those two slipped through.

A target can still be missing, and a position with no exit at all is still possible if the stock has too little history to compute a range.
When that happens the app **says so**: it records an alert, the Overview lists it under "Needs a decision", and the Auto trader page shows a dash.
The ticker's own page carries a button that places the missing exits, so fixing it is a click rather than a Python shell.
Pressed while the market is shut, it queues the request and the app places the orders at the next open — noticing the problem at 11pm is worth something, and it should not depend on remembering again at 9:30.
Before that, the failure was completely silent — no alert, no ledger row — and the only way to discover it was to look at the broker.

The entry is a limit order priced just through the market rather than a market order.
Webull refuses a market order inside a bracket, and the limit turns out to be the better instrument anyway: a market order's slippage is unbounded, which is worth avoiding when the budget is enforced to the dollar.

One case falls back to the older, slower method — buying first and arming the exits once the fill confirms.
A broker will not accept a bracket while the cash paying for it is unsettled, and selling one holding to fund another is something the agent is explicitly allowed to do, so this happens in normal use.
The trade still goes through and still gets its exits; it is exposed for a few seconds in between.

The model chooses what to buy and how much. The app only refuses orders that cannot be executed as stated — spending cash that is not there, selling shares that are not held, fractional shares.
An order that costs more than the cash left is dropped rather than resized, because resizing would quietly turn its decision into a different one.

### The equity curve

Cash plus the market value of what it holds, one point per trading day since its first fill.

It is rebuilt each time you open the page, from the ledger and the closing price of each past day, rather than read from a stored daily snapshot.
That is what lets it cover every day the agent has traded instead of starting on the day the chart was added — and it cannot drift, because a stored snapshot would be a second copy of a number the ledger already implies, with nothing to say which one was right when they disagreed.

A holding that printed no bar that day keeps its previous close rather than counting as nothing, so a missing quote does not draw a cliff and recover from it the next day.

Today's point uses the last price the app saved, which is at most about fifteen minutes old. The page does not wait for a live quote. Before the open, the curve ends at the last completed session.

### Moving the exits as the analysis changes

Every holding is re-analysed each morning, and the agent can act on what that says about a position it already owns — not only by selling it, but by moving the stop and the take-profit.

This closes a real gap. Exits used to be fixed when a position opened and left untouched until it closed, so GOOG spent a week with a $377.09 take-profit while each morning's analysis put the end of the move at $345.00 — a level the position would never have reached. A stop from a week-old analysis protects a thesis that no longer exists.

The agent decides; the app enforces what is possible. A stop must be below the current price and a target above it, or the order would execute the moment it was placed, and a level that fails that is refused rather than quietly corrected.

Two details make it safe to do repeatedly:

- **Levels are replaced, not cancelled and re-placed.** Cancelling first would leave a window with nothing resting under the position, which is the state most of this design exists to avoid.
- **A level already where it was asked for is left alone**, so a run that changes nothing costs no orders against a rate-limited API.

A holding with nothing resting on it yet is armed instead, so one action means "set my exits to these" whether or not there are any.

The prompt names what is currently resting on each position, and says plainly when nothing is — the agent cannot move an exit it cannot see, nor notice one that was never placed.

### The conviction floor

Two settings decide how good a signal has to look before the agent may open a position on it: a minimum chance of working, and a minimum risk/reward.

**Both default to zero, meaning off, and that default is the point rather than caution.**
The chance of working is the model's own claim, and until the Scorecard's calibration says that claim is honest *and* that it sorts outcomes, a threshold on it is a threshold on a number that may mean nothing.
Filtering by it would feel like discipline while being arbitrary. Check the calibration first, then set the floor.

Three details that follow from what the floor is for:

- **Selling is never blocked by it.** A Sell the model has no confidence in is still a reason to close a position you hold. The floor governs opening a position, not knowing about one — which is why low-conviction signals still reach the agent's prompt rather than being hidden from it.
- **A signal that states no number counts as failing.** Asking for at least 60% confidence and then accepting a signal that claims nothing would make the floor avoidable by not answering.
- **Buying a ticker with no recent analysis at all is refused** whenever a floor is set. That is the plainest case the floor exists to stop.

It is enforced in Python, not only stated in the prompt. A rule that lives only in the prompt is a request.

### Is the model earning its keep?

The agent picks stocks with an LLM, which is only worth doing if it beats the two things it could be replaced by tomorrow.
The Auto trader page measures it against both, from the day it placed its first order, with the same budget:

- **SPY buy-and-hold** — did picking anything beat picking nothing?
- **A mechanical signal-follower** — a rule that buys every Buy signal in equal weight and sells on a Sell signal. No model, no GPU, no prompt.

The second is the one that matters. If a rule with nothing in it beats the agent, the model is costing you money and attention for no return, and the page says so in those words.

It refuses to draw any conclusion under ten trades. Three trades of hindsight is not evidence, and a confident verdict on it would be worse than no verdict at all.

### Watching the trades

The Auto trader page lists every lot the agent has bought and what became of it: entry price and time, quantity, exit price and time, days held, and the profit or loss.

A position still open shows no exit and no profit, because its result is not decided yet — but the days held keep counting, which is what tells you a thesis has outlived its window.
The unrealized figure for those lives in the holdings table above it, so a blank in this table always means "not booked" rather than "nothing happened".

Selling part of a position splits it: the shares sold appear as a closed row with their own result, and the rest stays open. Both come from the same FIFO matching the book uses, so the two can never disagree about the same shares.

### Starting it over

`/agent action:reset confirm:True` closes every open position, cancels the exits resting under them, and erases the agent's record so it begins again with the full budget and no history.

**Reset when the agent has changed, not when it has lost money.**
A new prompt, new rules, or exits it did not have before all mean its record describes a system that no longer exists, and comparing the new one against it measures a mixture.
Resetting after a bad week is a different thing entirely: it is how you arrive a year later with no evidence at all, which is precisely what the baselines exist to prevent.

The order is not arbitrary, and the command will refuse rather than do half of it:

- **Closing a position needs a market order, so the market has to be open.** Started after the close it would cancel the exits, fail to sell, and leave the positions unguarded overnight — so it checks the hours before touching anything.
- **Exits are cancelled one position at a time, immediately before that position is sold.** Cancelling all of them up front means one failed sell leaves the whole book unprotected instead of the single position being closed.
- **The record is only erased once the broker confirms the account is empty.** Erasing it first would leave the app believing it holds nothing while the account still held stock, which is the one disagreement nothing downstream can recover from.

If you would rather reset the simulated account from Webull's own site, do that first and then run the command: it asks the broker before it does anything, so an account already flat needs nothing sold and the market's hours stop mattering.

### What it remembers

Each morning the agent is shown how its own past trades turned out: how many closed, how many made money, the net result, the average holding period, and the last six with what the analyst had said at entry.
Once it has bought on a Hold signal twice, it is told how that worked out specifically.

Without this it woke every day with a book and no idea that the last thing it bought had lost money.
A model that cannot see its outcomes cannot avoid repeating them — and neither can you tell whether it is improving.

It is also shown what share of the account each position is, how long it has been held, and the day's market regime. Those are stated, not enforced: allocation is its decision, but it was making that decision blind.

Two details worth knowing when reading the page:

- **The simulated account holds far more than the budget.** Webull funds it with $1,000,000, so the budget is enforced by the app, from the agent's own filled orders, and the account's buying power is never consulted.
- **A pending order has moved no money.** Orders placed while the market is shut fill at the next open, and until they do the cash is still counted as available.
- **Resting exits are not pending orders.** A stop or take-profit is meant to sit unfilled, so they appear on their holding's own row rather than among the orders still waiting to fill. An exit listed on its own means it is resting on shares the book does not show, which is a disagreement worth looking into.

Fills are reported over a live event stream, so a stop that triggers is recorded and posted within a second.
The fifteen-minute poll behind it is deliberately kept: a stream that silently stops looks exactly like a quiet market, so the stream is a speed improvement over a guarantee rather than a replacement for it.

Why the agent tends to wait for the open: Webull rejects a market order outright before the session starts, and the clock line in its prompt says so — a wakeup before the open is still useful for reading what has gone stale and commissioning research, just not for placing an order that will fill.

## Finding new tickers

The broker's screener suggests candidates: liquid names you do not already follow, over $5 and over a million shares traded, that have not moved more than 30% in the day.
That last filter is the one that matters. A raw screen is full of the day's pumps — one returned a stock up 927% — and the price floor alone does not catch them, because the pump is what lifted the price over the floor.

Nothing is followed automatically. An analysis costs about nineteen minutes of GPU and $0.05 out of the agent's own budget every time it orders one, so watching a ticker is a real, recurring cost rather than a free option — which makes adding one a decision rather than a default.
See them on the Research page or in the weekly digest post.

## The daily schedule (all times UTC, weekdays unless noted)

A handful of jobs still run on a clock; everything else is the agent's own choice — see "The auto trader" above.

| Time | What happens |
|---|---|
| 12:45 | **Regime snapshot** — VIX, SPY vs its 200-day average, and the 10Y–3M yield spread, shown as 🟢/🟡/🔴 |
| 13:00 | **Earnings check** — runs a fresh analysis for any tracked ticker that reports within 2 days |
| 13:30–20:00 (9:30–16:00 ET) | **Watchdog**, every 15 minutes — flags a move of 5% or more, volume at 2x the average or more, a stop breach, or a target touch. Big moves and volume spikes also trigger an immediate analysis, at most one per ticker per day |
| 15:55 ET | **Final pass** — the agent is asked once more before the close, whatever it asked for, so no position goes into the night unreviewed |
| 21:30 | **Daily grading** — grades and posts matured signals, then rewrites the journal. Stays after the close because grading reads the day's closing price |
| Fri 23:00 | **Weekly digest** — the week's outcomes, the win-rate trend, alerts, and the book |

## Data sources

- **Webull OpenAPI**: gives real-time snapshot quotes for every "price right now" check — the agent's fills, alert checks, book values. This needs a stock-quotes market-data subscription on the account. Without that subscription, or after any failure, the app falls back to yfinance automatically. Webull also takes the agent's orders, **on the sandbox only** — the agent refuses to run when the app holds production credentials.
  Since 2026-09-08 Webull is also tried **first** for daily history, and it serves the 1-minute bars behind the intraday chart. Its history endpoint pages back with no real depth ceiling — over 2,000 bars deep in testing.
- **yfinance**: the fallback for daily bars, and the sole source of the earnings calendar, the VIX and treasury-yield indices, and the quote fallback. It is kept rather than removed because it is what already produces this app's "possibly delisted" false positives and its rate-limit refusals, and a Webull outage must not take the whole daily cache down with it.

## Storage

Everything lives in one SQLite file, `data/trading.db` (a Docker volume in production).
Schema changes ship as Alembic migrations, and these migrations apply themselves at container start.
Notable tables: `signal` (one row per analysis, including its grades and what the run cost), `signalreport` (the full analyst text), `agenttrade` (the agent's orders, its book of record), `agentrun` (every decision pass, with the prompt and the answer verbatim), `alert` (watchdog dedupe and history), and `dailybar` (the bar cache, pure cache — dropping it costs only a refetch).
