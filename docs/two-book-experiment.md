# The two-book experiment — history

This page is history, not the current agent. It records the agent that ran from 2026-08-13 to 2026-08-25, as one half of what this project called the two-book experiment: a real-portfolio book that mirrored a live Webull account, run beside this agent, which traded a fixed watchlist it did not choose for itself.

**Both books ended on 2026-09-01.** The commit before that removal is tagged `v1-two-book-experiment`. The agent below merged with [the analyst experiment](https://github.com/nandyalu/ten-acre/blob/main/docs/analyst-experiment.md) into the single agent of experiment 1 — see [the experiment 1 journey](https://github.com/nandyalu/ten-acre/blob/main/docs/experiment-1-journey.md) for its record.

Newest first, the same as the current journal.

---

**2026-08-25 — the agent may move its own exits.** A `side: "adjust"` action,
with a new stop, target or both, on something already held. Before this, exits
were fixed when a position opened and untouched until it closed, so re-reading
a holding every morning taught the agent nothing it could act on short of
selling: GOOG spent a week with a $377.09 take-profit while each day's analysis
put the end of the move at $345.00. Python still refuses a level that would
execute on placement, and a level already where it was asked for is skipped
rather than re-sent.

**2026-08-25 — holdings now show what is resting under them.** The prompt lists
each position's live stop and target, and says `NOTHING is resting to close it`
when there is none. Added with the adjust action, and required by it: the agent
cannot sensibly move an exit it cannot see.

**2026-08-25 — signals are filtered to the configured model.** Running a second
model for comparison puts two signals per ticker in the table, sometimes
disagreeing. Without the filter the agent traded on the mixture, folding an
experiment into the live book.

**2026-08-25 — a conviction floor, switched off.** Minimum chance of working
and minimum risk/reward, both defaulting to zero. Off deliberately: the chance
of working is the model's own claim, and until the Scorecard's calibration says
it is honest *and* that it sorts outcomes, a threshold on it is arbitrary
discipline. A signal stating no number fails the floor rather than passing it,
or the floor could be dodged by not answering.

**2026-08-13 — buys go out as brackets.** Previously the exits were armed after
the buy returned, which meant they were validated while the account still held
nothing and read as a new short. Two positions were bought that day and neither
got its exits.

**2026-08-13 — an ATR stop is derived when the stated one is unusable.** Both
of that day's unprotected positions were bought days after their signal, by
which time the price had fallen through the stated stop and the level was
correctly discarded — leaving nothing. `record_signal` already substituted an
ATR stop, but only for Buy and Overweight, and the agent buys on Holds too.

**2026-08-13 — an unguarded position is announced.** It used to be silent: no
alert, no ledger row, and the only way to find out was to look at the broker.
