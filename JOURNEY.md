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

## Every change to the agent, and why

**This file covers experiment 2: four books, one agent each, on the in-process simulated broker.** The design and the success test are in [PLAN.md](https://github.com/nandyalu/ten-acre/blob/main/PLAN.md). Experiment 1, the single agent that traded Webull's sandbox from 2026-09-01, has its own page, [the experiment 1 journey](https://github.com/nandyalu/ten-acre/blob/main/docs/experiment-1-journey.md). A rule file or a code comment that cites a JOURNEY.md entry by a date from before experiment 2 means an entry there, unless the entry is below.

`CLAUDE.md` and `.claude/rules/agent.md` describe what the rules are. This describes how they got that way. Add an entry here **before** changing a rule, not after.

Newest first.

**2026-10-02 — the agent gets two fetches of raw data, `bars` and `news`.** `bars` returns a ticker's completed daily sessions, open, high, low, close and volume, for up to 250 sessions. `news` returns the items the analysis's news analyst reads: Yahoo, Google News, Finnhub and the company's SEC 8-K filings, for up to the last 30 days. Until now every fetch gave the agent what someone else concluded, or a list, and the question of the experiment is whether the agent can trade with real tools. The fixed rule that names the fetches names the two new ones. The analysis stays as research the agent may buy. This ships on the experiment-2 branch and does not reach experiment 1.

**2026-10-02 — each turn of a decision pass now records the system message it was sent, and a hash of it.** The turn record gets `system` and `system_sha`. Before, a replay of an old turn could not know which fixed rules the turn saw, and the prompt in the code today is not the prompt of last week. Every turn before this date has neither field. The agent sees nothing new. Experiment 2 needs the field, because it sends the same turn again.
