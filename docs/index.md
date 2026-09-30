# Ten Acre

**One question, asked in public: what does an autonomous AI agent do with $10,000?**

*Ten Acre* — a small plot with a fence around it, and one worker left alone to farm it. That is the setup exactly.

Ten Acre is a self-hosted experiment. An agent gets a simulated brokerage account, a fixed budget, and a bill for every piece of research it orders. It decides what to study, what to buy, what to sell, and when to give up on a name. Nobody helps it.

A web dashboard and a Discord channel report what it did.

**No real money is involved and no order can reach a real account.** The broker is Webull's sandbox or Alpaca's paper host, and nothing else.

## What runs on the agent's own schedule

Nothing is analysed automatically, holdings included. A **research** order commissions a fresh look at a name — new or already tracked — and costs $0.05 out of the same money the agent trades with. Four analysts — market, news, sentiment, and fundamentals — feed a bull-versus-bear debate. A trader drafts a plan, and a risk team picks one decision: **Buy, Overweight, Hold, Underweight, or Sell**.

At every pass the agent reads its book, its signals, its own track record, and the bill it is running up, and answers with orders.

**It also says when to wake it next.** It sets its own cadence through the session, so it can take a profit, cut a loss, or commission research while the move it is reading is still happening. Naming no time means it is asked again at the following open — a fallback, not a plan — and a final pass always runs fifteen minutes before the close, whatever it asked for, so no position goes into the night unreviewed.

The app records each decision with its price and time horizon, then grades it automatically once that horizon arrives — against reality, against SPY, and against the analysis's own price target.

## Nobody can nudge it

There is no button that adds a ticker, starts an analysis, or places a trade. There are no Discord commands.

A control that lets a person nudge the book puts a second decision-maker in the record. Afterwards, nothing can tell which one produced a result — and a record with two authors is not evidence about either.

Changes go in the journal first, with a date and a reason, and then get made by hand.

## Where to go

**Running it yourself?** Start at **[Run it yourself](deploying.md)** — prerequisites, every environment variable, and what goes wrong.

Once the container is up, the app takes over from the documentation: a deployment that is not ready sends you to its own `/setup` page, which names each missing requirement and shows the exact lines to paste. It reports whether a thing is configured and never what it is configured to, so nothing on it can leak a key.

<div class="grid cards" markdown>

- **[Run it yourself](deploying.md)** — deploy your own copy: what you need, what it costs, and troubleshooting.
- **[Credentials](setup.md)** — how to get each one, and what happens if you skip it.
- **[How it works](overview.md)** — architecture, the signal lifecycle, the daily schedule, and data sources.
- **[The dashboard](dashboard.md)** — the web app: the overview, the chart with the analysis drawn on it, and the record of every decision pass.
- **[What Discord posts](discord.md)** — the scheduled posts, the alerts, and the one alert that asks for a person.
- **[The daily workflow](trading-workflow.md)** — how to read the experiment: the decision pass, the grades, the weekly review.
- **[Finding your edge](finding-your-edge.md)** — how to read the scorecard without fooling yourself.

</div>

## Ground rules

- **The app never places a real order.** Every order goes to Webull's sandbox or to Alpaca paper. On Webull the agent refuses to run without the sandbox flag. The Alpaca module knows only the paper host.
- Signals come from a small local model. Treat each one as a structured second opinion, not as a fact.
- **A single analysis is one sample.** The model runs at temperature 1, so the same ticker on the same day has returned opposite decisions.
- Nothing here is financial advice.
