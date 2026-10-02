# The site

The web dashboard is where you look at what happened. Discord is where you hear that something happened. See [How it works](overview.md) for why they are separate.

Open it at the root of whatever host and port the container serves, for example `http://localhost:8080/`.

**Each page answers one question.** The old table had thirteen, one per data source — Tickers, Signals, Alerts, Regime and Digest each stood alone. That shape suited an operator and left a reader to work out which page held the answer. Alerts, the regime line and the weekly digest no longer have pages of their own: each is context for something else and now sits inside it.

| Page | The question it answers |
|---|---|
| The experiment (`/`) | What is this, and how is it going? |
| The book (`/book`) | What did it do with the money? |
| Decisions (`/decisions`) | Why did it do that? |
| Notes (`/decisions/notes`) | What has it told us it cannot do? |
| Research (`/research`) | What did it study, and was it right? |
| Scorecard (`/scorecard`) | Is it any good? |
| Journal (`/journal`) | What have we changed, and when? |

Three more explain the experiment rather than report it, and sit in the footer: `/idea`, `/method` and `/glossary`. `/settings` and `/setup` are operator pages and are absent from the published build entirely.

Old paths redirect rather than 404. Links to them exist in Discord posts and in the journal, and breaking them loses the trail back to whatever was being discussed.

## Notes

**Every message the agent has left for the people who maintain it**, newest first, each linking to the decision pass it came from.

The agent has a `note` action for the case where something is stopping it deciding well — a number it cannot see, a tool it does not have, a rule that contradicts another. Nothing acts on a note automatically. It is a message, not a request.

**These are evidence, not complaints.** The experiment asks what an AI agent does when it is given real tools, so the agent saying "I cannot see X" is the experiment reporting a missing tool. Collecting them on one page is what makes the pattern visible: one note is an observation, and the same note four times is a gap in the design.

A note also never counts as acting. A pass that left only a note is still an idle pass, because otherwise "I need better data" would stand in for the decision the agent owed.

## Publishing it

`PUBLIC_MODE=1` makes the backend refuse every write, so the site can be put on a public domain — behind a Cloudflare tunnel, say — without exposing the settings that choose the model and the budget, or the endpoint that places exit orders.

It does two things, and the second matters more than the first.

**Every write is refused.** This is middleware, not a per-route check and not a hidden button. A per-route check protects only the routes somebody remembered to annotate. Hiding a link stops nobody who can type a URL. The middleware refuses any method other than GET, HEAD or OPTIONS, which covers the route added next year by someone who never read this page.

**Nothing runs.** No scheduler, no Discord, no trade stream. The published site is a second container over the same database, and if it also ran the scheduler there would be two agents deciding on one book — duplicate research commissions paying twice for the same look, duplicate decision passes, two sets of orders at the broker against one ledger. **None of that arrives as an HTTP request**, so refusing writes would not have stopped any of it. So PUBLIC_MODE means one thing said two ways: this copy does not act.

Point the tunnel at the public container, not the private one. `compose.example.yaml` carries it as a commented-out second service, bound to loopback so a tunnel reaches it and the local network does not. Mount the volume read-write for it: SQLite writes its `-wal` and `-shm` sidecars even to read, and a read-only mount fails to open the database at all. The guarantee is PUBLIC_MODE, not the mount flag.

**This site itself no longer runs that second container.** It is a fully static export pushed to Cloudflare Pages — files with no server, no database connection and no credentials anywhere near them. The read-only container remains supported and is the simpler option to self-host; the static route exists because a live backend on a public path kept developing new ways to leak, however carefully it was gated, and removing the backend removed the whole class of problem rather than patching it again.

The frontend reads the flag only to drop the Settings link and the arm-exits button. A failure to read it leaves both showing — the backend still refuses, so the worst case is a dead end rather than a hole.

Run the private copy without the variable and it behaves exactly as before.

## The experiment

The landing page is written for someone who has never heard of this. It gives the premise before it gives a number, because a return of +2.4% means nothing until you know what it is a return on and who chose the trades.

The order is the design: what this is, then that nobody can nudge it, then the money, then the last thing the agent decided in its own words, then whether any of it is working.

That last card is deliberately cautious. Below about twenty resolved signals it prints the count and says "not enough to say anything yet" rather than a percentage — three wins in four reads as 75% and means nothing.

The headline figure comes from the book rather than the copy. Hardcoding it and reading it from the API in the same view is how a headline comes to contradict the number under it.

## Refused, and "broker said no"

**These are two different things and the Decisions page keeps them apart.** A reader who takes them for the same thing misses the more interesting half.

| | What it means | What it says about the agent |
|---|---|---|
| **refused** | Python declined the order before it was ever sent | Its own arithmetic was wrong — more cash than it holds, more shares than it owns, a stop that would trigger on placement |
| **broker said no** | The order was formed correctly and Webull would not take it | Nothing about the agent. Unsettled cash, a closed session, a symbol the broker will not trade |

The distinction is load-bearing in two places.

**A refused order is dropped, never resized.** Shrinking it would quietly turn the agent's decision into a different one, and the record would then describe a strategy nobody chose. The refusal is fed back once and the agent is asked again, which is how it learns it may sell to fund a buy.

**A broker failure is carried into the next few prompts.** Only a count was stored until 2026-09-02, which made a failure invisible the next morning — the agent formed the same order, was refused again, and nothing in the record explained the repetition.

## What needs attention

**Needs attention** is the only block on the page that is a call to action, so it is the only one with a colored border. It lists three things:

- A ticker the agent holds whose newest analysis says get out.
- A stop that was reached, a target that was touched, or a position that fell below the agent's cost — within the last three days.
- **A holding with nothing resting at the broker to close it.** This one is an absence rather than an event, which is exactly why it needs saying: an unprotected position looks identical to a protected one until you go looking for the missing order. It is also the only row on this page that asks a person to act.

That last row is read live rather than taken from the alert log, and the difference matters. Every other alert records a moment — a price was reached — and stays true afterwards, so showing it for three days is right. An unguarded position is a state: once the exits are placed the alert is stale, and nothing retracts it. So the alert stays in the log as a record of what happened, and the block above is driven by the live check, which clears itself the moment the exits go on.

A big move or a volume spike is information, not a prompt, so those stay off this list and sit lower down the book page with the rest of the alerts. When there is nothing to decide, the page says so plainly rather than showing an empty box.

Below that: the agent's account — its equity, and the percentage return against the $10,000 it started with — plus the signal win rate and how many signals are still maturing. The win rate shows a raw count instead of a percentage until about 20 signals have resolved, because three wins in four reads as 75% and means nothing.

**New opportunities** lists Buy signals from the last week the agent has **not** acted on, with the model's confidence and the expected value of the bet. It is not a to-do list — nobody here places a trade. It is what the analyses found and the agent left alone, which is half of reading how it behaves. **Never analyzed** lists watchlisted tickers with no signal yet.

## Ticker detail

This is the page the rest of the app exists to fill in.

At the top, the numbers that decide what happens next: the agent's position, the stop, the target, and the model's confidence. The stop and target show how far away they are — and say "breached" or "reached" once price has crossed them, rather than reporting a distance that no longer means anything.

### The chart

The price chart carries the analysis on it, because a price chart on its own answers almost nothing. You can see that a stock fell; you cannot see whether the bot called it, whether you were holding, or whether an alert fired.

| Mark | Meaning |
|---|---|
| Green arrow below the bar | A Buy or Overweight signal |
| Red arrow above the bar | A Sell or Underweight signal |
| Yellow circle | A Hold signal |
| Blue square | A fill by the agent |
| Purple dot | A watchdog alert |
| Red dashed line | The stop level from the signal in force |
| Green dashed line | Its price target |

Entries sit below the bar and exits above it, so a Buy signal and the fill that followed it do not overlap. The stop and target lines come from the **newest** signal only — an older signal's levels were superseded, not merely graded, and drawing them would put a stale line on the chart.

The chart opens on 30 days, which is the window a swing signal is actually judged over. Use the range buttons for more context; the chart always fits the full range you asked for.

### What happened

Signals, alerts, and fills in one list, newest first. They live in three different tables and carry different fields; putting them in three lists would leave you to interleave them by eye.

Each signal row shows the entry price, the stop, the target, and the confidence, plus a pass or fail badge once it has been graded. Click through for the full rationale and every analyst report.

### Positions and lots

The position tile carries **resting stop** and **resting target** beside it.
Those are different numbers from the stop and target above them, and the distinction matters: the signal's levels are what the analysis proposed, while the resting ones are orders sitting at the broker that will execute whether or not this app is running.
They disagree often — a level the app discarded, an ATR-derived fallback, or a bracket the broker refused.

When the agent holds a stock with **nothing** resting under it, the page says so in a warning rather than leaving you to notice two empty cells — and offers a button to fix it.

**Place the exits now** rests a stop and a take-profit under shares the agent already holds.
It uses the same levels a fresh buy would get: the newest signal's, screened against the current price, with a volatility-derived stop when the stated one cannot be used.
It never invents a target, because a made-up exit price on a real position is worse than none — it looks decided.

It refuses rather than duplicating when exits are already resting, which is why the button only appears when there are none: two stops on one position sell it twice, and the second sale is a short.

**Outside market hours it queues instead of failing.** Webull accepts a standalone order at any time but refuses a linked pair — an OCO or a bracket — outside 9:30–16:00 ET, because tying the legs together needs a routing session that only runs then.
So pressing the button in the evening records the request, and the app places the orders on the first pass after the next open, then posts what happened.
Press it again and nothing changes: one pending request per position, because a second press is someone checking it registered, not a second instruction.

A queued position is **not** a protected one, and the warning stays up to say so. The exits go on at the open, and an overnight gap is exactly when they would have mattered.

**Positions taken** lists every lot the agent bought in this ticker: entry price and date, quantity, exit price and date, days held, and the profit or loss.
An open lot shows no exit and no profit, because its result is not decided yet, but the days held keep counting.
Selling part of a position splits it — the shares sold get their own result and the rest stays open — and the matching is the same FIFO walk the position tiles use, so the two can never disagree about the same shares.

### What is not on this page

There is no button to run an analysis, add the ticker, drop it, or record a trade. The agent chooses what it watches and decides on its own when a name is worth a fresh $0.05 look, so a hand-added ticker would cost it nothing and read, later, as a name it chose.

The one button is **Place the exits now**, described above. It decides nothing.

## The rest

The table at the top says which question each page answers. This one says what each page holds.

| Page | What it holds |
|---|---|
| The book (`/book`) | The simulated account: budget, cash, its equity curve, exits it has moved, holdings with the stop and target actually resting at the broker, every position taken with its entry, exit and profit, every order with the reason it gave, and every watchdog alert, filterable by type. **No exit resting** is the alert that is not about price at all: the agent holds a position and nothing is at the broker to close it. The two stop alerts read differently on purpose. "Thesis broken" means price reached the level the analysis named, and "Below your cost" means price fell a set percentage under what was paid. Either can happen without the other |
| Decisions (`/decisions`) | **Every decision pass, with the prompt the agent saw and the answer it gave, word for word.** Behaviour here is mostly prompt, so this is the page that makes a month of runs readable afterwards. The notes it has left for its maintainers are under `/decisions/notes` |
| Research (`/research`) | What the agent watches, the candidate menu it may commission from with the four stock cells the agent reads beside each name, and every analysis, filterable by pending or resolved. Read-only |
| Scorecard (`/scorecard`) | Win rates overall, by decision, by model, and by ticker, plus whether the model's stated confidence matches how often it is right |
| Journal (`/journal`) | The generated journal, a month to a page: what it bought, what that cost, what got graded, and the changes we made to the agent |
| Settings (`/settings`) | Trade horizon, analysis model, alert thresholds, and the agent with its budget and conviction floor. An operator page, absent from the published build |

The market regime, VIX, SPY against its 200-day average and the yield curve, sits on the experiment page (`/`). The old Tickers, Signals, Alerts, Events, Journey, Digest and Regime pages are gone, and their paths redirect to the pages above.
