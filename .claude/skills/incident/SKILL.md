---
name: incident
description: Use when a day looks wrong — the agent did nothing for a stretch,
  asked for research that never arrived, held something it had just argued for
  selling, or a number on the site does not match the book. Establishes what
  happened, whether the record for those days is still usable, and where it
  gets written down.
---

# incident

**The question is never only "what broke".** It is **"which days are contaminated, and is the record still evidence"** — because this repository's whole output is a record of what an agent decided, and a day where the app quietly changed the agent's behaviour is a day that says nothing about the agent.

Three incidents of exactly this shape surfaced on 2026-09-10 alone. None of them raised an error. Two had been running for days.

---

## 1. Say what you actually observed

Write it down before looking at anything. One sentence, no cause in it yet.

Good: "the only decision card on 2026-09-09 says the market was closed." Bad: "the scheduler is broken."

**The cause you assume first is usually the one that makes the observation stop being interesting.** The market-hours gate hid behind a message that sounded like a normal explanation.

## 2. Ask the database, not the logs

Logs rotate and a rebuilt container loses stdout. The database is the record.

**The live one is inside the container.** `data/trading.db` in a working copy is a development database seeded by test runs — it holds tickers like `AAA` and failure text about tests reaching the broker. Do not draw conclusions from it.

```sh
docker exec ten-acre python - <<'EOF'
import sqlite3
db = sqlite3.connect("/app/data/trading.db")
def q(label, sql):
    print(f"\n### {label}")
    for row in db.execute(sql).fetchall()[:20]:
        print("  ", str(row)[:200])

# Passes that did nothing at all, and the reason each gave.
q("skipped passes by day", """
  SELECT date(ran_at), substr(skipped,1,70), COUNT(*)
  FROM agentrun WHERE skipped IS NOT NULL
  GROUP BY 1, 2 ORDER BY 1 DESC""")

# Every pass, so a silent gap in the series is visible.
q("passes per day", """
  SELECT date(ran_at), COUNT(*), SUM(skipped IS NOT NULL)
  FROM agentrun GROUP BY 1 ORDER BY 1 DESC""")

# What the agent asked for.
q("research it commissioned", """
  SELECT date(ran_at), orders FROM agentrun
  WHERE orders LIKE '%research%' ORDER BY ran_at DESC""")

# What Python declined, and what the broker declined. Different facts.
q("refusals", "SELECT date(ran_at), substr(refusals,1,120) FROM agentrun WHERE refusals NOT IN ('','[]') AND refusals IS NOT NULL ORDER BY ran_at DESC")
q("broker failures", "SELECT date(ran_at), substr(failures,1,120) FROM agentrun WHERE failures NOT IN ('','[]') AND failures IS NOT NULL ORDER BY ran_at DESC")
EOF
```

**A `research` order with no `researchcharge` row and no `signal` is the shape that found the 2026-09-09 incident**: two tickers commissioned, never analysed, no charge, no error. Cross-check those three tables against each other rather than trusting any one.

## 3. Decide which of three things this is

| | What it means | Where it goes |
|---|---|---|
| **Incident** | The agent's behaviour changed without anyone intending it. Real days are affected. | `JOURNEY.md` |
| **Evidence change** | What the record contains or means changed. Telemetry counts. | `JOURNEY.md` |
| **Infrastructure fault** | Something degraded, and the agent decided exactly as it would have anyway. | `docs/changelog.md` |

**The third is the one people over-report and the first is the one they miss.** The test is whether the agent's own decisions would have been different. Webull rate-limited most of a day's market-data calls on 2026-09-09 and every path fell back to yfinance, so prices arrived and the agent decided on real numbers — infrastructure. The market-hours gate refused whole passes, so research the agent had scheduled never ran — incident.

## 4. Work out the window, exactly

Name the first and last affected day, and say how you know. "Since the change on 2026-09-05" is only true if you checked; the gate was introduced long before the change that was supposed to have removed it.

Two questions that fix the boundaries:

- **When was the code that caused it introduced?** `git log -S'<the exact string>' -- <path>` finds the commit that added a line, which is more reliable than reading the journal.
- **Does the data agree?** If the fault should have produced skipped passes, the skipped-pass count per day should change at that boundary. If it does not, the story is wrong.

## 5. Ask what the record still supports

This is the part that matters and the part usually skipped.

- **Are the decisions in that window still the agent's?** If the app suppressed an action the agent asked for, those days show an agent that did less than it chose to. Say so, so nobody later reads the quiet stretch as patience.
- **Is a stored field null for that window?** A field added later is null before its date, and someone reading the data in six months will not know why. That is an evidence change and needs its own line.
- **Do not backfill.** Re-running an old prompt now answers a different day's question at different prices. Nothing recorded is repaired by inventing what it would have said.
- **Do not rewrite graded rows.** Grading read a target to reach a verdict, so changing that target afterwards contradicts a verdict already given. Both scrub scripts leave graded signals alone on purpose.

## 6. Record it, then tell the agent

**Write the `JOURNEY.md` entry before the fix**, like any behaviour change. One or two sentences: what changed, and why. Name the window if the record is affected.

**If the agent's own tools were involved, note that the agent cannot see the fix.** It will keep working around a restriction that no longer exists, or keep asking for a thing that now exists. `backend/agent_changes.json` used to close that gap and was removed on 2026-09-21, so there is no way to tell it any more — which makes this something to weigh when judging whether the days after an incident are usable.

## 7. Then stop it happening silently again

An incident that produced no error should end with something that would have made noise:

- **A test, when the invariant is mechanical.** Four were added on 2026-09-10 for exactly this reason. Break the invariant deliberately and watch the test fail before believing it.
- **A line in the prompt, when the agent was working from a false statement.**
- **A `stale-check` term, when the cause was a description of something that no longer exists.**

---

## The three from 2026-09-10, as worked examples

**The agent was turned away whenever the market was shut.** `run_once` refused every pass outside market hours while the prompt invited the agent to wake at any hour and commission the open's research. Found from a screenshot of a fresh deployment. Days of pre-open passes did nothing at all. **Incident** — the agent did less than it chose to, and the skipped-pass counts per day show exactly which days.

**Two commissioned tickers were never analysed.** SMR and CRWV were ordered on 2026-09-08, and a day later had no signal, no charge and no error. Found by comparing `agentrun.orders` against `signal` and `researchcharge`. **Incident** — the agent spent the next day deciding around research it had ordered and never received.

**Webull rate-limited most of a day's market-data calls.** 332 refused quotes and 192 refused bar fetches, none of it visible because every path falls back to yfinance. The agent's decisions were unaffected. **Infrastructure** — a changelog line, not a journal entry.
