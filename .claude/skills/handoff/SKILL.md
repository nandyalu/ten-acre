---
name: handoff
description: Use when the user asks for a handoff, says they will run /clear or /compact, starts a task that is not part of the current work, or when the context-check hook reports a large session. Writes a short note to .claude/handoff.md so a fresh session continues with a few thousand tokens in place of the whole conversation. Also use when the user says "read the handoff" or "continue from the handoff".
---

# Handoff between two sessions

**A long session is expensive because every step re-reads all of it.** A fresh session that reads a 40-line note costs a few thousand tokens. The note must hold what the next session cannot find in the code or in git.

## Write the note

Overwrite `.claude/handoff.md`. The file is gitignored. Keep it under 40 lines. Use these headings, and leave out a heading that has nothing under it:

```markdown
# Handoff — <date and time, UTC>

## Goal
<One or two sentences: what the user wants, in their words where possible.>

## Done
- <Each finished step. Name the commit hash, or the files changed if not committed.>

## State now
- <What runs right now, and where: a background command, a container, a probe.>
- <What is uncommitted: `git status --short` summary.>

## Decisions and why
- <A choice the user made or agreed to, and the reason. The next session must not argue it again.>

## Open questions
- <What still needs the user's answer.>

## Next step
<The exact next action, with the command or the file and line.>

## Read first
- <At most five files or skills the next session needs, most important first.>
```

Rules for the content:

- **Write facts the next session cannot find.** Do not copy code, diffs, or long command output. Name the file and line.
- **Name the user's decisions.** A decision in the note stops the next session from asking again.
- **Say what was verified and what was not.** "Tests pass" only if they ran and passed.

## After you write it

Tell the user in two sentences:

1. The note is in `.claude/handoff.md`.
2. Run `/clear`, then say "continue from the handoff".

## Continue from a note

1. Read `.claude/handoff.md`.
2. Read the files under **Read first**, and only those.
3. Check that **State now** is still true. For example, run `git status --short` and check that a background command finished.
4. Tell the user in one or two sentences where the work stands, then do the **Next step**.
