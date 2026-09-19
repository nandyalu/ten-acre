# Finding your edge

The app produces two records: what the analyses said, and what the agent did with them. This page is about reading both without fooling yourself.

Nothing here is financial advice. It is a discipline for finding out, with evidence, whether any of this works.

## Read the scorecard before you believe anything

Until the Scorecard shows a real sample, treat every signal as a hypothesis.

- **Wait for about 20 resolved signals** before drawing a conclusion. With five data points a 60% win rate is noise.
- **The vs-SPY column is the one that matters.** An absolute win rate of 70% in a bull market can be worse than useless — holding SPY and doing nothing would have beaten it. The signals beat the market only if the vs-SPY rate is above about 50% *and* the average alpha is positive.
- **Look for asymmetry by decision type.** Small local models often lean bullish. If Buys grade at 65% against SPY and Sells at 30%, the useful conclusion is that the Buys carry information and the Sells do not. That is a different finding from "it is 50/50".
- **Look for asymmetry by ticker.** The analysts lean on news and sentiment volume, so the model may simply be better at a widely covered name than at a thin small cap.
- **The by-model breakdown is why every signal records its model.** Switching models teaches you nothing if the win rates blend together.

## A single analysis is one sample

The model runs at temperature 1, which is what Gemma's own documentation recommends. The same ticker on the same day has come back Underweight and then Overweight.

That is not a defect and it is not a reason to tune the sampling down. It means **no single signal is evidence.** Only the aggregate is.

## Read the decision passes, not only the outcomes

The Scorecard grades the analyses. The **Events** page shows what the agent did with them: the prompt it saw and the answer it gave, word for word.

Those are different questions, and the second is the experiment.

- **Did it use what it was shown?** The prompt names the candidates, the watchlist cost, the resting exits, its own closed trades. A section it never refers to is a section that is not working.
- **Did it buy on a Hold, and on what?** The small local model did, repeatedly, on the word alone, and the prompt forbade it for a while. Since 2026-09-19 the prompt says what the word means instead: the analyst would keep a position they already had, and a Hold that carries an entry, a stop, a target and a chance is a plan to judge on those numbers after reading it. The question is now whether it read the plan or bought the word.
- **Did it concentrate?** It has put 100% of the book into one name. There is no position-size cap, deliberately: adding one would change what the agent may decide, and that belongs in the journal as its own entry rather than as a quiet fix.

## Let the regime scale your reading, not your trading

The 🟢/🟡/🔴 line is a blunt instrument, and blunt instruments are hard to fool. The agent sees it at the top of its prompt.

- **🟢 Risk-on** — the normal case.
- **🟡 Mixed** — expect more Holds, and check whether the agent notices.
- **🔴 Risk-off** — the classifier encodes a historical pattern: elevated VIX, price under the 200-day average, an inverted yield curve. That pattern is exactly the environment where buying dips stops working. An agent still opening new longs into it is telling you something about the agent.

## Review the failures, not the wins

After a graded FAIL, open the signal and read the analyst reports behind it. Find which analyst got it wrong — news, technicals, sentiment, fundamentals.

Patterns emerge fast. "Sentiment-driven Buys on thinly covered names keep failing" is the kind of finding that no aggregate win rate will hand you.

Write what you find in `JOURNEY.md`, at the root of the repo. **Record what was wrong, not only what worked** — the entries saying "this turned out to be noise" are what stop the same idea being proposed again in three weeks.

## The loop, compressed

> Let it run → read the decision pass and the prompt behind it → grade the signals → wait for a real sample → read the failures → change one thing, write down why first → repeat.

The discipline is not skipping the measuring steps. That, and not the model, is where an edge would come from.
