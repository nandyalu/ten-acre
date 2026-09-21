---
name: stale-check
description: Run after any change to this app, before committing. Records the
  change in the right file (JOURNEY.md or docs/changelog.md)
  and sweeps the surfaces that go stale silently — the prompt quoted in .claude/rules/agent.md,
  docs, site copy, code comments, and hardcoded values that are really
  settings. Every check here caught a real bug on 2026-09-10.
model: sonnet
context: fork
agent: stale-check
---

# stale-check

**A change to this app is not finished when the code works.** It is finished when the record says what changed, and when nothing left behind still describes the app as it was.

Every check below is here because it caught something real on 2026-09-10, when four sweeps across the docs, the site, the journal and the source found roughly forty wrong statements — none of which failed a test, and several of which had been wrong for weeks.

**The failure has one shape: a change that is correct in the code and leaves a description of the old behaviour somewhere else.** None of these fail loudly. A wrong variable name, a deleted button still mentioned, a date baked into a template — all read as authoritative.

Work through the parts in order. Skip a part only when it plainly does not apply, and say so rather than skipping silently.

---

## 0. Scope the run, then sweep once

**Every sweep in this skill runs in about a second, all of them together.** The time goes on reading output and reading files, so both are what this step bounds. A run of this skill that takes minutes has spent them on round trips, not on work.

**Run all of it in one command.** Parts 2 to 7 depend on nothing from each other, so running them one at a time buys nothing and costs a round trip each.

```sh
bash .claude/skills/stale-check/sweep.sh
```

It prints the changed files, then one numbered section per part below, in about 100 lines. **The commands live in the script, not here** — read it when you need to widen a term list, which section 4 in particular expects you to do whenever something is deleted from the app.

**Then read the sections against the changed-file list, and say which you are skipping.** A section the diff cannot have touched needs no judgement — but a silent skip is indistinguishable from a check that found nothing, so name it.

| Sweep section | Judge its hits when the diff touches |
|---|---|
| 2 — the quoted prompt | `backend/services/*.py` |
| 2b — "not built" claims | `PLAN.md`, `CLAUDE.md`, or a shipped feature |
| 3 — settings stated as facts | `frontend/src/**`, `docs/**`, `README.md` |
| 4 — removed mechanisms | anything at all |
| 5 — renamed routes | `app.routes.ts`, or prose naming a page |
| 6 — schedule and environment | `backend/**/*.py` |
| 7 — cross-references | `JOURNEY.md`, `docs/changelog.md` |

**Section 2's exact half is a test** — `test_the_documented_prompt_is_the_real_prompt.py` — and it runs in part 8 whatever you skip here, so leaving the fuzzy half unread cannot let a drifted quotation through.

Parts 1 and 8 below always run. The section numbers match the parts below, which say how to judge each one.

---

## 1. Record the change in the right file

Three files, three questions. Get this wrong and the record stops being usable.

**One question decides between the first two: does this change make two periods of the experiment non-comparable?**

| File | Holds | Test |
|---|---|---|
| `JOURNEY.md` | Changes to the agent | Any one of the three below |
| `docs/changelog.md` | Everything else about the app | None of them |
| `CLAUDE.md` | What the rules are *now* | Reasoning a future edit must not undo |

The three tests. Any one is enough for `JOURNEY.md`:

- **Behaviour** — it changes what the agent is shown, what it may ask for, or what Python refuses.
- **Evidence** — it changes what the record contains or means. **Telemetry counts.** A field that is null before a date is exactly what trips up whoever reads the data later, and "it is only plumbing" is how those get filed wrong.
- **Incident** — the agent's behaviour changed without anyone intending it, so real days are contaminated. **A silent bug feels like a fix and is not.**

**Read the top of the file you are writing into, not the file.** `JOURNEY.md` and `docs/changelog.md` run to tens of thousands of tokens between them, and `head -60` of each carries the format, the voice and the recent entries — which is everything you need to add one. Read further only to check a specific claim, or to resolve a date a cross-reference names.

Rules:

- **Write the entry before making the change**, not after. A reason reconstructed later is a story about what you would like to have been thinking.
- **One or two sentences: what changed, and why.** Both files. Long-form reasoning that constrains a future edit goes in `CLAUDE.md` instead, because that is the file read before the code is touched.
- **There is no third file aimed at the agent.** `backend/agent_changes.json` was one until 2026-09-21, when it and the mechanism that showed it were removed. The agent is not told when the app changes; do not re-add a line to a file that no longer exists.

---

## 2. The quoted prompt, if the prompt or the rules moved

`.claude/rules/agent.md` quotes the prompt verbatim. The quotation was in `CLAUDE.md` until 2026-09-13. **A quotation is stale the moment the original moves.** On 2026-09-10 it had drifted three ways at once — a system message still saying "paper-trading" a day after the code dropped the word, and two whole rules missing.

**A wrong quotation here is worse than none, because this file is read instead of the code.**

**The exact half is a test now**, not a step here: `backend/tests/test_the_documented_prompt_is_the_real_prompt.py` pins the system message, the opener, and the three rules that exist because of a live failure. Run the suite and it tells you. The fuzzy half is **sweep section 2** — the rules list, which the rule file paraphrases, so it needs a person reading hits rather than an assertion.

**Read every hit; do not treat the list as a failure count.** The rule file paraphrases some rules and abbreviates others with `[...]`, so a paraphrase shows up here looking like a gap. The first run of this check flagged six, of which three were paraphrases and three were rules genuinely missing from CLAUDE.md — including two that appear only in the state that produces them (a zero balance, a conviction floor), which is exactly why reading the code had missed them.

Also re-read any sentence in `PLAN.md` or `CLAUDE.md` saying a thing **is not built** — **sweep section 2b**. Those rot fastest: one claimed an unbuilt feature that had shipped the previous day. `PLAN.md` holds the backlog and the rejected work since 2026-09-20; `CLAUDE.md` keeps only the permanent non-goals.

---

## 3. Values that are settings, stated as facts

**The single most common bug in this repo's prose.** A number or date that a deployment can change, written into a template or a doc as though it were a property of the software. It is true of this deployment, which is why it survives review, and wrong for every other one.

Caught this way: the experiment start date (every image claimed the first deployment's), `$0.05` research charge in six templates (free research is a supported mode), `$10,000` budget, and `WEBULL_ACCOUNT_ID` documented nowhere.

**Sweep section 3** lists every figure and date asserted in site copy, and under it what the API actually serves, to bind against instead.

The rule: **site copy may state what the software does, and may not state what this deployment has done.** If the value is a setting, serve it and bind it. If it cannot be bound — `index.html`'s `<title>` is parsed before Angular runs — leave a comment saying the hardcoding is deliberate, because otherwise it looks exactly like this bug.

Watch for **zero being a real setting.** `research.is_charging()` is `get_price() > 0`, so a `if (!value) return` guard keeps the default on precisely the deployment the fix was for.

---

## 4. References to things that no longer exist

**This is how the market-hours gate survived five days.** The scheduler's comment said "the market-hours gate is gone" — and it was, from the scheduler. A second copy lived one call deeper, where nobody looked.

**Sweep section 4** names every removed mechanism and looks for it across code comments, templates and docs. It searches for the 11:00 sweep, the 13:35 pass, all 23 slash commands and every manual control. Tests and the archive pages are excluded — a 13:35 timestamp in a fixture is not a claim, and the two `-experiment.md` pages describe experiments that ended.

**A hit is fine when it is explicitly past tense. A hit in the present tense is a bug.** Expect roughly ten hits on a clean tree, most of them sentences explaining that the thing was removed. **Widen the term list in `sweep.sh` whenever something else is deleted** — the value of this check is entirely in whether the list names what you just took out, so a deletion that is not added to it is a check that will never fire.

**Check dead code too.** It is where stale claims survive a sweep, because nobody reads it. `ask.py` sat unimported for nine days still telling readers to "run /analyze first". If nothing imports it, delete it — git remembers.

---

## 5. Renamed pages and routes

Old paths redirect, so **every link keeps working and nothing looks broken** — which is why dead page names outlived their pages by a fortnight. A reader told to "see the Tickers page" finds no such thing in the navigation.

**Sweep section 5** prints what was renamed, beside every page name still written into prose.

---

## 6. Claims about the schedule and the environment

Two things the docs get wrong repeatedly, both verifiable against source, both in **sweep section 6**: every scheduled job, and every environment variable the code reads.

**A time in Eastern is not a time in UTC.** The README scheduled the day's final pass at "20:55" under a column headed "Time (UTC)"; the code reads it off the Eastern close, so the table was right in winter and an hour out the rest of the year.

**Check any variable the sweep names that you do not recognise against the docs that should list it** — `docs/`, `README.md`, `.env.example`, `compose.example.yaml`. A newly required variable that appears in no doc produces a container that starts, reports healthy, and never places an order.

**A check that reads a variable is not a check that the thing works.** `/setup` verified `WEBULL_ACCOUNT_ID` held *something* and reported ready, while the sandbox had reissued its account numbers and the deployment could place no order. When adding a requirement, ask which of the two facts it tests — the value exists, or the thing the value names answers — and prefer the second.

---

## 7. Cross-references still resolve

Code comments cite journal entries by date. Moving an entry breaks them silently.

**Sweep section 7** resolves every cited date against the entries that exist, and every relative link in `docs/` and `README.md` against the files on disk. Both print nothing on a clean tree.

**`docs/journey.md` is a symlink to `JOURNEY.md`.** A relative link inside it resolves differently on the docs site than on GitHub, which is why the sibling links there are full URLs.

---

## 8. Verify

**Ask what the caller has already run on this same tree, and run only the rest.** Whoever invoked this skill has usually just run the suites; running them again proves nothing and costs three more round trips. Say what you reused.

All of it, when nothing has been run — one command, about seventeen seconds:

```sh
uv run --no-sync pytest backend/tests -q
cd frontend && npx ng build && npx ng test --watch=false && npx prettier --write "src/**/*.{ts,html,css}"
uvx zensical build
```

**`--no-sync` matters.** A plain `uv run` re-resolves and can rewrite `uv.lock` as a side effect, which then shows up as an unexplained diff in the change you are about to commit. If the lock really is stale, that is its own change: run `uv lock` deliberately and say so.

Two things about the test output:

- **The frontend test run should report zero unhandled errors.** It reported eight for weeks, dismissed each time as a known `lightweight-charts` quirk. They were not a quirk: jsdom does not implement `matchMedia`, `src/test-setup.ts` now polyfills it, and the count went to zero. **Treat any unhandled error as yours until proven otherwise** — the previous eight hid the fact that `chart-theme.ts` was throwing on every chart test.
- **`backend/tests/conftest.py` refuses any call to the live broker.** If a test suddenly hits it, the code now reaches further than it used to — that is information, not an obstacle. Stub what it names.

---

## What this skill does not do

It finds statements that contradict the code. It cannot check a **measurement** — GPU throughput, token counts, vendor pricing. Those came from real runs recorded at the time, and re-deriving one is a day of work, not a sweep. If a change makes a measurement obsolete, say so and leave the number with its date.
