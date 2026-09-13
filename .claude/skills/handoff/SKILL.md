---
name: handoff
description: Use when the user asks for a handoff, says they will run /clear or /compact, starts a task that is not part of the current work, or when the context-check hook reports a large session. Writes a short note for one topic to .claude/handoffs/<topic>.md, so a fresh session continues with a few thousand tokens in place of the whole conversation. Several sessions can each keep their own note. Also use when the user says "read the handoff", "continue from the handoff", or "continue from handoff <topic>".
---

# Handoff between two sessions

**A long session is expensive because every step re-reads all of it.** A fresh session that reads a 40-line note costs a few thousand tokens. The note must hold what the next session cannot find in the code or in git.

**Each topic has its own note in `.claude/handoffs/`.** The folder is gitignored. Two sessions that work on two topics at the same time write two notes, and neither overwrites the other.

**Name a note by its topic, never by the session ID.** Claude Code gives a new session ID after each `/clear`, so the next session cannot find a note that has the old ID in its name.

## Write the note

1. **Choose the topic name.** Use two to four lowercase words joined by hyphens, for example `api-spend` or `usage-bar`.
   - If this session started from a note, use the name of that note.
   - If this session did not start from a note, run `ls .claude/handoffs/`. If a note with your name exists, read its **Goal**. If that goal is a different task, choose a different name. Another session owns that note.
2. **Write `.claude/handoffs/<topic>.md`.** Overwrite only the note of this topic. Keep it under 40 lines. Use these headings, and leave out a heading that has nothing under it:

```markdown
# Handoff: <topic> — <date and time, UTC>

## Goal
<One or two sentences: what the user wants, in their words where possible.>

## Done
- <Each finished step. Name the commit hash, or the files changed if not committed.>

## State now
- <What runs right now, and where: a background command, a container, a probe.>
- <What is uncommitted: `git status --short` summary. Say which files belong to this topic, because another session can have changes in the same tree.>

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

1. The note is in `.claude/handoffs/<topic>.md`.
2. Run `/clear`, then say "continue from handoff <topic>".

## Continue from a note

1. **Find the note.**
   - If the user names a topic, read `.claude/handoffs/<topic>.md`.
   - If the user names no topic, run `ls -t .claude/handoffs/`. If there is one note, read it. If there are two or more, show the user each topic name with the first line of its **Goal**, and ask which one to continue. Do not guess.
   - If `.claude/handoffs/` is empty, tell the user that no note exists.
2. Read the files under **Read first**, and only those.
3. Check that **State now** is still true. For example, run `git status --short` and check that a background command finished.
4. Tell the user in one or two sentences where the work stands, then do the **Next step**.

## Remove a finished note

**Delete a note only when its goal is finished and the user agrees.** The finished work is in git, so the note has no more use. A note that stays after its work is done makes the list in step 1 longer and can send a later session to old work. Do not delete the note of another topic.
