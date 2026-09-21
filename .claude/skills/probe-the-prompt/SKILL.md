---
name: probe-the-prompt
description: Send the real prompt to the real model, out of the app, and read
  the reasoning block. Use after any prompt change, and before believing a
  prompt change worked. Rendering a section is not the same as it being read —
  four sections here were correct, correctly rendered, and completely ignored.
model: sonnet
context: fork
agent: probe-the-prompt
---

# probe-the-prompt

**A prompt change is a hypothesis until the model's own reasoning confirms it.** Every prompt bug found in this project was found this way, and none of them was visible in the code, the tests, or the rendered prompt.

| What looked fine | What the reasoning showed |
|---|---|
| A rule about sharp moves | The agent applied it to a ticker nothing was watching |
| A "what was noticed" table | Not one of four runs referenced it |
| A research result handed back | Cited as "the analyst", never as its own spend |
| A change-note section | 1,600 tokens, quoted by nobody |

**The last three were correct code producing correct output.** Tests passed. The sections rendered. The model read past them.

## What this is not

It is not a test. A test asserts the prompt *contains* something. This asks whether the model *used* it. Those are different questions and only the second one matters.

## Run it

```sh
python -m backend.scripts.probe_prompt --turn turn1        # a fresh pass
python -m backend.scripts.probe_prompt --turn turn2        # after research landed in-pass
python -m backend.scripts.probe_prompt --turn turn1 --parallel   # one sample per GPU
```

It builds the prompt from a **copy** of the live database and calls the model directly. It cannot place an order: it never reaches `run_once`, `screen` or any broker path.

**On a Gemini deployment it makes the same forced `decide` call the app makes**, so `answer` in the output is the call's arguments as JSON, and `--parallel` means `--samples` at once. Run it inside a container: the host cannot reach Google (see `.claude/rules/llm-providers.md`).

## Reading the result

Do not grep the reasoning for the words you wrote. **The model paraphrases**, and a keyword scan reported "not used" for a run that had quoted the section verbatim. Ask instead:

1. **Find a number that appears in only one section of the prompt.** If the reasoning quotes it, that section was read. This is the only hard evidence. `$37.38` and `40.34` appeared nowhere but the alerts table, and that settled it.
2. **Does it reason about something it already knows?** Working out the year, or which of two same-day analyses is current, is a prompt gap and cheap to close.
3. **Does it assert something the rules do not say?** That is a rule stated too broadly.
4. **What did it do, not what did it say?** Seven runs converging on one ticker said more than any phrase did.

## What decides where a section goes

**Tables get read. Prose between tables gets skimmed.** Two sections sat at 42% and 47% of the prompt, between the two tables, and no run touched either. Moved above the tables, the next runs quoted them.

**Put a fact where it is already being read rather than repeating it.** A research result appeared twice — as a signals-table row and as a prose block — and the model reconciled the copies and kept the table. It was not ignoring the prose; it was picking the canonical copy. Marking the row fixed it. Adding emphasis to the prose had not.

**A crowded row has blind spots too — moving a fact into a table is not automatically a fix.** A holding's price-range clause was ignored at the end of a nine-clause prose line; moved into a table, still ignored as the last column; moved again next to the price columns it belongs beside, still ignored — nine probe samples, three placements, zero reads. The model reports a fixed habitual set of facts about a holding (cost, price, days held, what is resting) and does not expand that set just because a column was added. Do not assume "it's a table now" closes the loop; probe again after the move.

## Things that will mislead you

- **Sample size, and it is worse than it looks.** Temperature is 1. **Two runs of seven samples on identical code swing by up to 3 of 7** — measured 2026-09-21, the table is in `agent-probes.md`. So a seven-sample probe catches breakage and nothing finer: 7 of 7 going to 0 of 7 is a finding, 5 going to 2 is a coin. Resolving a real improvement needs roughly 20 to 30 samples an arm, which is 70 to 100 requests against a 500-a-day limit. Weigh what does not move, too: the signals that swung 0 between identical runs are the ones where a change would mean something.
- **Wall clock from a parallel run.** gemma4's E-series keeps per-layer embeddings in host RAM, so seven cards contend for the same memory bandwidth — the same model measured 43.5, 28.0 and 69.6 tok/s depending only on pool load. Compare timings only against runs made the same way.
- **The book you probe on.** A probe run on a Saturday with $8.05 cash tests how the agent *reads*, not how it *trades*. Re-run on a weekday with cash before trusting a conclusion about trading behaviour.
- **Double-sending the system prompt.** `agent._invoke` prepends `SYSTEM_PROMPT` itself. Pass the user prompt alone, or the model sees it twice.

## Afterwards

Write what the reasoning showed into the commit message, not just the fix. "Four runs ignored this section" is the evidence for a placement decision, and without it the next person moves it back.
