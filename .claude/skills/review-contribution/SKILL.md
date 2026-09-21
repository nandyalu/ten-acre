---
name: review-contribution
description: Review a pull request or an incoming patch against this project's
  rules — the ones a contributor could not know from reading the diff. Use it
  after the tests pass, since four invariants are enforced by tests now and
  this covers what a test cannot judge. Also worth running on your own work
  before proposing a commit.
---

# review-contribution

**The tests already say no to the mechanical mistakes.** Four invariants are pinned — the sandbox guard, the write surface, the documented prompt, and serial analysis loops — so a contribution that breaks one fails before anybody reads it, with a message naming the rule.

**This skill is for what a test cannot judge.** Every check below is a question about intent, and the answer is usually in the pull request description rather than the diff.

Start by running the suite and the `stale-check` skill. If either has something to say, that comes first; there is no point reviewing intent in a change that does not build.

---

## 1. Does it put a second decision-maker in the record?

**The question this whole project asks is what an agent does with no human hand.** A control that lets a person nudge the book means that afterwards nothing can tell which decision-maker produced a result, and a record with two authors is not evidence about either.

The test catches a new write endpoint. It cannot catch:

- A **setting** that is really a control. "Force the agent to reconsider", "skip today", "pin this ticker" — each is a person deciding, spelled as configuration.
- A **default** chosen to steer the agent rather than to be correct.
- A script in `backend/scripts/` that edits the book. Those exist for repairing data that was recorded wrongly, never for changing what was decided.
- A **prompt** change that tells the agent what to conclude rather than what is true.

Ask: after this lands, can a person change what the book does without leaving a journal entry? If yes, it is a control however it is spelled.

## 2. Does it make the experiment worth less while making the app better?

**This is the most common good-faith mistake, and the hardest to see in a diff.** A change can be well written, tested, and a real improvement to the software, and still cost the experiment its point.

- **Withholding a capability does not make a result cleaner.** It makes it an answer to a question nobody asked. When the agent lacks a tool, the honest reading is that the experiment is not set up properly yet — not that the agent should work around it.
- **A restriction exists to keep the experiment honest or the account solvent, and for no other reason.** One that exists only because nobody built the tool yet is a gap, not a rule.
- **The tie goes to the tool.** Where a tool and a restriction seem to conflict, the tool wins unless the restriction is about solvency or honesty.

The clearest live example: a position-size cap. The agent has put the whole book into one name. A cap sounds obviously prudent, and it changes what the agent may *decide* rather than correcting its arithmetic — so it needs its own reasoning and its own journal entry, not a quiet fix. Treat any change of that shape the same way.

## 3. Is it recorded in the right place, before the change?

Check the entry exists **and** that it is filed correctly. One question: does this make two periods of the experiment non-comparable? Any of behaviour, evidence or incident means `JOURNEY.md`; none of them means `docs/changelog.md`.

**Evidence and incident are the two that get filed wrong.** Telemetry feels like plumbing, and a silent bug feels like a fix. Both change what the record means.

Then read the entry itself:

- Does it say **why**, or only what? The what is already in the diff.
- Is it a sentence or two? Long-form reasoning that constrains a future edit belongs in `CLAUDE.md`, or in the `.claude/rules/` file for that area.
- If the agent's own tools changed, does the diff try to add a line to `backend/agent_changes.json`? That file and its mechanism were removed on 2026-09-21; the agent is no longer told when the app changes, so such a line belongs in JOURNEY.md instead.

## 4. Are the claims in the description ones somebody checked?

- **"Faster" or "cleaner" with no number is not a result.** If there is a number, ask how it was measured. On this hardware a single-ticker sample understates a batch by about ten percent, and a repeated identical prompt reads as 219,000 tok/s because of the prompt cache.
- **A test that has never failed has not been checked.** Ask whether the invariant was broken deliberately to watch the test catch it. All four pinned invariants were verified that way, and one of them would otherwise have passed forever while asserting nothing.
- **A measurement that cannot be re-run should not be invented.** If a number is needed and cannot be produced, say so and leave the old number with its date.

## 5. Read the prose, not only the code

Most of what has gone wrong in this repo is correct code with a stale description attached. In a contribution, look for:

- **Comments describing the old behaviour** beside changed code. A comment saying a thing was removed, next to a second copy of that thing, is how one bug survived five days here.
- **Site copy stating what this deployment has done** rather than what the software does. A date or a figure in a template is a bug even when it is currently true — the research charge and the experiment's start date were both written in as facts and are settings.
- **A reference file quoting the code.** A quotation is stale the moment the original moves.

## 6. Two things to check by hand every time

- **Did anything reach the broker that should not have?** `backend/tests/conftest.py` refuses live calls, so a newly stubbed broker function means the code now reaches further than it used to. That is information, not an obstacle.
- **Did a migration or a script touch graded rows?** Grading read a target to reach a verdict, so rewriting that target afterwards contradicts a verdict already given. Both scrub scripts leave graded signals alone deliberately.

---

## Saying no well

Most contributions that fail these checks are good-faith improvements to the software. The rejection is about the experiment, not the code, and it lands better when it says so:

- **Name the rule and the reason, not just the rule.** "No manual controls" alone sounds arbitrary. "A control puts a second decision-maker in the record, and afterwards nothing can tell which one produced a result" does not.
- **Point at where it is written down**, so the next contribution starts better informed.
- **Say what would be mergeable.** The open work is real: replay, a backtester, a watchlist ageing rule.
