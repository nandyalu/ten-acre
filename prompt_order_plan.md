# Prompt order: a plan to restructure `build_prompt`

**Status:** in progress. Started 2026-09-21.

## Why

`agent.build_prompt` is one 660-line function that appends to a single list of lines.
Four problems follow from that shape, and they are all the same defect: the prompt uses a pointer where it should use adjacency.

1. **The wakeup fallback sits four lines above the wake block, and the same fact is stated three times.** The computed instant is at `agent.py:873`, a second copy is inside `describe_wakeup`, and a third is a fixed rule in `SYSTEM_PROMPT`.
2. **"Why you are awake" names something the agent must scroll ten lines to find.** The claim and its evidence are separated by a pointer.
3. **The signals table runs on from the watchdog section with no divider and no heading.** It is the most important block in the prompt and the only large one with no heading at all.
4. **Dividers are placed by hand, so every section must reason about its neighbours.** The guard at `agent.py:1166` exists only to stop two dividers printing side by side.

A fifth problem turned up while reading the code.
The `asked_again` list at the top builds up to four pointers to four sections scattered across the prompt, and all four say one thing: what your last answer did.

## The target order

```
# Now
  opener · clock · wake · memory

# Since you last looked
  app_changes · noticed · last_answer

# What is true now
  regime · account · holdings · pending_orders · signals ·
  watchlist · track_record · broker_failures ·
  analysis_timing · recent_wakeups

# What you can do
  research_cost · candidates · rules · answer_shape · next_wakeup
```

Four questions, in the order a person would ask them: when is it, what did I miss, what is true, what may I do.

The four group names become `#` headings in the prompt, and each section keeps a `##` heading under its group.
That is a hypothesis, not a certainty — see step 2.7.

## Phase 1 — split into sections, change almost nothing

The rendered prompt changes only by gaining the dividers and the `##` headings it is missing today.
Everything else is a refactor you can prove.

### Step 1.1 — freeze the current output

Write `backend/tests/test_the_prompt_sections.py`.
It builds the prompt from fixtures and compares each one against a stored text file under `backend/tests/prompts/`.

The clock must be pinned, because the prompt carries the time.
Patch `market_clock.now_et`, `market_clock.next_open` and `watchdog.is_us_market_hours` to fixed values.

Nine fixtures, chosen to reach every conditional branch in the function:

| Fixture | What it forces |
|---|---|
| `quiet` | no holdings, no signals, no alerts — the minimal prompt |
| `full` | holdings, pending orders, signals, watchlist, menu, alerts, earnings, changes, memory |
| `woken` | `woke_because` plus alerts, so the pointer fires |
| `early` | `planned_wakeup` in the future plus a note |
| `turn2` | `outcomes` and `researched_now` |
| `asked_again` | outcomes, readings, dropped and rejected together — all four pointers |
| `tool` | `answer_by_tool=True` |
| `broke` | `book.cash` below the research price |
| `closed` | market shut, conviction floor set |

Generate the files from the code as it stands now.
That is the baseline. Commit it on its own, before touching anything else.

**Done 2026-09-21.** `backend/tests/test_the_prompt_sections.py` plus nine files under `backend/tests/prompts/`. Ten tests, and the whole backend suite still passes at 1319.

The baseline caught a real leak on its first regeneration.
`describe_analysis_timing` takes its own `now` and `build_prompt` never passes one, so it read the real wall clock and the "N min so far" line moved every minute.
Nine files drifted a minute apart with no code change between them.
The fixture now pins that clock too, and two regenerations two seconds apart produce identical files.

### Step 1.2 — add the section shape

Two small pieces:

- A section is a name and a list of lines. An empty list means the section is absent.
- A joiner takes the ordered sections, drops the empty ones, and puts exactly one `---` between the survivors.

The guard at `agent.py:1166` disappears here.
The joiner makes two dividers in a row impossible.

### Step 1.3 — extract the sections, one at a time

Twenty-four functions, each returning `list[str]`.
Move one, run the golden test, read the diff, move the next.
Do not batch them.

The order list stays exactly what it is today:

```
opener · clock · wake · regime · app_changes · memory · account ·
holdings · pending_orders · outcomes · noticed · signals ·
track_record · broker_failures · analysis_timing · recent_wakeups ·
readings · not_carried_out · refused ·
research_cost · watchlist · candidates · rules · answer_shape
```

### Step 1.4 — give the headless sections a `##` heading

`signals`, `holdings`, `account` and the track-record cluster have no heading today.
Give each one, in the same style as the two that already carry `##`.

Do not add the `#` group headings yet.
You cannot wrap a group heading around sections that are not yet grouped, and the order does not change until phase 2.

**Steps 1.2 to 1.4 done 2026-09-21.** Twelve new `describe_*` functions, a `_joined` that owns every separator, and a 24-entry order list that carries each section's heading.

Two checks stood in for byte-identity, and both passed on all nine fixtures:

1. After the extraction and the uniform rule, **every content line was unchanged and in the same order** — only `---` lines and blanks differed.
2. After the headings, **the only body lines removed were titles the heading now carries**: "Rules:", "You currently hold.", "Recent analyst signals.", "**Answer in this shape:**", "**What is no longer true.**", "**Orders you placed that have not filled yet.**", "**Why you are awake.**", "**What you asked to read**", "Your previous answer was refused.", "What was not carried out:" and the memory heading.

Eight tests pinned the old rendering and were updated to assert the same intent against the new one. The whole backend suite passes at 1319.

### Step 1.5 — read the diff, then accept it

Regenerate the golden files.
**The diff must contain only added `---` lines and added `##` heading lines.**
Nothing else may move by a single character.
If anything else moved, a section was extracted wrong.

Then follow the project's order for a prompt change:

1. Write the `JOURNEY.md` entry first.
2. Update the quotation in `.claude/rules/agent.md` in the same edit. **Fix its numbered list while you are there** — it lists the watchlist as section 7 and the noticed and outcomes blocks as 12 and 13, and none of those match the built order.
3. Run `probe-the-prompt` on `turn1`. This is a regression check, not a new idea.
4. Run `stale-check` before the commit.

### What phase 1 also fixes

`agent.py:1462` does `lines.index("**Answer in this shape:**")` to cut the JSON example off the tool channel.
That is a string search into an assembled list, and it breaks the day someone edits that heading.
With named sections you swap one entry for another.

**Step 1.5 done 2026-09-21.** Committed as `1101257`, with the JOURNEY entry and the `.claude/rules/agent.md` order table.

Probed as a matched pair — seven samples on the old prompt, seven on the new, same database copy and same model. **No regression.** Every difference is ±1 or ±2 at seven samples, which is temperature-1 noise. The full table is in `.claude/rules/agent-probes.md`.

Two things learned that change how later steps should be read:

- **A one-line pointer section scores zero on quoting and still works.** "Your track record" was quoted by 0 of 7 and its `track_record` fetch was called by 5 of 7. Check the fetch before calling such a section unread.
- **The headings cost 4.3%** — `turn1` went from 6,139 to 6,406 characters for 24 headings and 24 rules.

One lead, not a finding: "Paying for research" went 1 of 7 to 4 of 7, orders placed went 2 to 4, and both `next_wakeup` and `next_wakeup_note` went 5 of 7 to 7 of 7. Three small moves in one direction. Worth a larger sample before anyone believes it.

## Phase 2 — reorder and trim, one move per probe

Each step below is its own commit, its own `JOURNEY.md` entry and its own probe.
A single large reorder would tell you nothing about which move helped.

### Step 2.1 — cut the pure duplication

Lowest risk, so do it first. No section moves.

- "Nothing is analysed automatically, holdings included" appears word for word at `agent.py:1256` and `agent.py:1366`. So does "it runs inside this pass". Keep one copy.
- "while you were away" appears in the wake reason and again in the noticed section.

**The line not to cross:** cut repetition, never a rule.
`CLAUDE.md` names three rules that exist because the model got that exact thing wrong on a live run, and `agent-probes.md` names more.
If a sentence appears once, leave it.

**Step 2.1 done 2026-09-21.** Four cuts, **2.2% off every prompt**. The event-driven wake reason went too — `AgentRun.woke_because` landed the same day with no row written, so rewording it changed nothing on the Decisions page.

**The line drawn, and it is the one to keep:** a repeated *term* is not duplication, a repeated *explanation* is. `runs inside this pass` deliberately stays in all three sections — one name for one thing, and the timing section is the only one that renders when there is nothing yet to research. A test caught this: it builds a prompt with no watchlist and no menu, where the timing line is the only section left that can say research lands in-pass.

**The probe method changed here, and every later step inherits it.** See "The noise floor" at the top of `.claude/rules/agent-probes.md`: two runs of seven samples on identical code swing by up to 3 of 7. A seven-sample probe catches breakage and nothing finer. Steps 2.2 to 2.7 should each get one seven-sample run to confirm nothing broke, and a 20-to-30-sample pair only where the answer would change what gets built.

**Still untested:** `turn1` carries no alerts, so the watchdog lead cut never reached the model. Fold a `woken` probe into step 2.5, which touches that section anyway.

### Step 2.2 — build the `next_wakeup` section

One section, always present, at the end beside the answer shape. It carries:

1. The ask.
2. The computed fallback instant, moved down from `agent.py:873`.
3. On an early wake, the "this pass replaces the one you planned for X" sentence, moved out of `describe_wakeup`.

The clock stays first. Only the instruction moves.

**Probe this one carefully.**
The early-wake ask reads 4 of 4 today, and that result belongs to its current position at the top.
If the reading drops after the move, put it back and record why in the commit message.

**Step 2.2 done 2026-09-21.** `## Your next wakeup` sits between `## Rules` and `## Answer in this shape`, carrying the fallback instant and, on an early wake, the ask.

**Placed before the answer shape, not after it as this plan first said.** The answer shape is the closing template; a content section after it would mean reading the form and then more content. The order reads: what you may do, when you are next asked, how to write it down.

Colocating the two halves deleted a third copy of one fact — the ask used to end "If you give no time, you are asked at the following open", which is now the line above it.

**The split that stays:** why the pass is happening is still under the clock, because it is read against everything below. Only the ask moved. The probe log is the reason — the early-wake finding was 4 of 4 and says the ask does the work, not the reason line. A test pins both ends.

Probed: **no regression, and the risk did not materialise.** The fallback instant is still read from the bottom of the prompt — 7 of 7 chose it — and 7 of 7 named a wakeup and wrote a note.

**A method note for later steps.** Comparing a run against the min-max of two earlier runs flags differences of 1 as "outside the band". That is an artifact of treating two points as a range. Judge against the measured floor of 3 of 7, not against a two-run spread.

### Step 2.3 — move `memory` up into Now

Standing notes and wake notes are both the agent talking to itself.
The wake block already ends with "Those are your own words, not an instruction", which covers both.

**This is a judgement call, not an obvious win.**
Memory notes read 3 of 4 when the note bears on the decision, and 0 of 4 on a crowded book.
Rebuild the good scenario from `agent-probes.md` before and after, or the probe tells you nothing.

### Step 2.4 — consolidate `last_answer`

Four sections become one, with sub-headings:

- What you just did in this pass (`outcomes`)
- What you asked to read (`readings`)
- What was not carried out (`not_carried_out`)
- What was refused (`rejected`)

The `asked_again` pointer list at the top then collapses from four pointers to one.

**Two things to watch.**
The outcomes block was measured at 4 of 4 above the tables and 0 of 4 below, so it must stay above `signals`; the target order keeps it there.
The refusal block carries a time-critical warning — a read does not run on that turn — so check the model still acts on it from the new position.

### Step 2.5 — move `noticed` up beside the wake reason

Once the section sits next to the claim, delete the pointer in `describe_wakeup`.
It exists only to bridge the gap.

**Do not reword `_WOKE_BECAUSE`** at `scheduler.py:494`.
Since commit 902210d those sentences are stored on the run and shown on the Decisions page.
Change them and the site's history starts reading two ways.

**The risk here is real.**
The measured comparison was *between the tables* against *above the tables*.
Above the account is a third position nobody has tested.
Probe with the number test: find a figure that appears only in the alerts table and see whether the reasoning quotes it.

### Step 2.6 — move `regime` down into What is true now

Small, and it follows from the grouping.
The regime line is a fact about the world right now, not news since the last pass.

### Step 2.7 — add the four `#` group headings

Last, because the sections must already sit in their groups for a group heading to be true.

The hypothesis: naming the four groups gives the model a map, and a map helps it find a fact instead of re-deriving it.
The cost is four lines.

**What to look for in the probe**, beyond the usual number test:

- Does the reasoning navigate by group — "nothing new since my last pass" — rather than restating every section?
- Does it stop re-deriving facts it already has? Working out the year, or which of two same-day analyses is current, is the tell.
- Does anything that read reliably before stop reading?

If the answer to all three is no change, keep the headings anyway only if they cost nothing that matters, and record the null result in `agent-probes.md`.
A null result is worth writing down. It stops the next person trying it again.

## How to probe

Run after every phase-2 step:

```sh
python -m backend.scripts.probe_prompt --turn turn1 --parallel --samples 7
```

The variants `build_prompts()` names are `turn1`, `turn2`, `woken`, `change`, `change_turn2` and `read`.
Match the variant to the step: `change` for 2.2, `woken` for 2.5, `turn2` or `change_turn2` for 2.4.

### The environment

- Model: `gemini-3.5-flash-lite`.
- Environment: the `.env` from the running `ten-acre` container.
- Run inside a container. The host cannot reach Google.
- Pin the clock when the step needs a specific market state. The recipe is a throwaway container with `--entrypoint python`, `backend/` mounted over `/app/backend`, a `.backup` copy of the database, and a wrapper that imports `probe_prompt` before `market_clock`.

### The request budget

The daily limit is 500 requests.
A tool-channel sample costs 3 to 4 requests, because each fetch round resends the whole conversation.
So seven samples cost about 21 to 28 requests, and the day holds roughly 17 runs of that size.

Two requests were used on 2026-09-21 before this work started.

### Reading a result

In this order:

1. **Find a number that appears in only one section of the prompt.** If the reasoning quotes it, that section was read. This is the only hard evidence.
2. Does the model work out something the prompt already told it? That is a gap.
3. Does it assert something the rules do not say? That is a rule stated too broadly.
4. What did it do, not what did it say?

**Do not grep the reasoning for your own words.**
The model paraphrases, and a keyword scan has already produced a wrong verdict in this repo.

Four samples is a hint. Seven is a reading.

Write what the reasoning showed into `agent-probes.md` and into the commit message, not just the fix.

## Asking the agent what order it wants

A separate experiment, run once the sections exist.

The `note` action already lets the agent say "I cannot see X", and `CLAUDE.md` treats that as evidence.
Asking about the prompt is the same mechanism pointed at a different target.
It is a probe, out of the app, and never reaches `run_once` or any broker path, so it adds no second decision-maker to the record.

**Design.** Add a `layout` variant to `build_prompts()`.
It builds the real `turn1` prompt, swaps the answer-shape section for three questions, and asks for prose:

1. Which parts of what you were given did you use to reach a decision, and which did you not open at all?
2. If you could put these parts in any order, what order would you want, and why?
3. Was there anything you had to hold in your head from one part while you read another?

Question 3 is the one that matters.
It asks about something the model can observe — its own working memory across the prompt — and it points straight at the defect this plan exists to fix.
Questions 1 and 2 ask about attention, which the model cannot observe.

**One mechanical snag.** `_ask_gemini` calls `llm_gemini.decide` with mode `ANY`, which forces a `decide` call.
A prose answer needs a plain text call instead.
Add a small text helper beside `decide`, or run this variant against the local model through the existing `ask()` path.

**The caveat, and it belongs in the commit message.**
A stated preference is a hypothesis, not evidence.
The holdings price-range column was probed nine times across three placements and read zero times, yet a model asked "would a price range help?" would say yes every time.
Use the answers to generate moves for phase 2, and let the behavioural probe decide each one.

Run seven samples and look for agreement across them.
One opinion once is noise.

## Progress

- [x] 1.1 freeze the current output
- [x] 1.2 add the section shape
- [x] 1.3 extract the sections
- [x] 1.4 add the `##` headings
- [x] 1.5 read the diff, JOURNEY entry, probe, commit
- [x] 2.1 cut the duplication
- [x] 2.2 the `next_wakeup` section
- [ ] 2.3 move `memory` into Now
- [ ] 2.4 consolidate `last_answer`
- [ ] 2.5 move `noticed` beside the wake reason
- [ ] 2.6 move `regime` into What is true now
- [ ] 2.7 add the `#` group headings
- [ ] ask the agent what order it wants
