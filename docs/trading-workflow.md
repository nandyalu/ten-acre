# The daily workflow

**You do not trade this. You read it.**

The agent decides and the app records. Your job is to notice what it is doing, and to write down anything you change about it. That is the whole routine.

There is one exception where a person acts, and it is at the bottom of this page.

## Morning

Two posts arrive before the US market opens.

1. **The regime line** (12:45 UTC) — one sentence of context: `🟢 Market regime: Risk-on · VIX 16.7 (normal) · SPY 8.3% above its 200-day avg · 10Y–3M spread +0.87% (normal)`. The agent sees this same line at the top of its prompt.
2. **Earnings** (13:00 UTC) — a tracked ticker reporting within two days gets a fresh analysis. Positions move most around earnings.

## The decision pass

**Whenever the agent asked to be woken next. This is the post to read.**

The agent names its own next wakeup at the end of every pass, so this does not land at a fixed time — naming none means it is asked again at the following open, and a final pass always runs fifteen minutes before the close regardless. It says what the agent bought, sold, adjusted, commissioned and untracked, and it quotes its own reasoning. On a pass it did nothing, it says nothing.

Then open the **Events** page. The Discord post is the summary; Events has the prompt the agent actually saw and the answer it actually gave, word for word.

**That is the pair worth spending time on.** Behaviour here is mostly prompt. A month of runs across three prompt revisions is three experiments with no way to tell them apart, unless somebody kept the text.

Read for a few specific things:

- **Did it use what it was shown?** The prompt names the candidates, the watchlist cost, the resting exits, its own track record. A pass that ignores a section is telling you that section is not working.
- **Did the arithmetic hold?** It has been given cash it could not spend and a share count it could not afford. Python refuses those now, but a refusal is still a signal that the prompt is misleading it.
- **Did it change its mind, and does the reason survive reading?** A reversal with a stated cause is fine. A reversal with no cause is temperature 1, and one analysis is one sample.

## During market hours

The watchdog posts only when something crosses a threshold. Every alert except one is information, not a prompt to act.

- **📊 big move / unusual volume** — something happened, and an analysis usually follows. The agent reads its result at the next decision pass, not now.
- **🛑 stop breach** — a holding is well below the agent's average cost. Nobody sells it for you. The agent will see it tomorrow, and the resting stop may fire first.
- **🎯 target touch** — a holding reached the target from its signal.

## Evening

- **Graded signals** (21:30 UTC) — for example: `NVDA Buy from 2026-06-17: PASS ($450.12 → $470.30, +4.5%) · vs SPY +2.1%: PASS (alpha +2.4%) · target $480.00: not hit`. These matter more than fresh signals: they are the only messages that say whether any of this is working.
- **The journal** is rewritten after grading. The **Journey** page shows the last ten days.

## Weekly

The 🗞️ digest on Friday is the review loop.

- **Win rate trend** — is the last 30 days better or worse than all-time?
- **Read the failures**, not the wins. Open the signal, read what the analysis actually argued, and see what it missed.
- **The Scorecard's breakdowns** by decision and by ticker are the raw material. Below about 20 resolved signals a win rate is noise — three wins in four reads as 75% and means nothing.

## When you change something

**Write it in `JOURNEY.md` first, at the root of the repo. Then make the change.** The date, what changed, and why.

The why is the part that stops the same idea being re-proposed in three weeks, and it is the part nobody can reconstruct later. A reason written two weeks after the fact is a story about what you would like to have been thinking.

## The one time you act

**⚠️ an unguarded position.** The agent holds shares and nothing is resting at the broker to close them — usually because Webull refused the bracket against unsettled cash and the fallback arming also failed.

Open the ticker page and press the button that rests the exits. It places the stop and target the agent already chose, under shares it already owns. It decides nothing and it can only reduce exposure.
