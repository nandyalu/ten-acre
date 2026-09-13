---
name: stale-check
description: Run after any change to this app, before committing. Records the
  change in the right file (JOURNEY.md, docs/changelog.md, agent_changes.json)
  and sweeps the surfaces that go stale silently — the prompt quoted in .claude/rules/agent.md,
  docs, site copy, code comments, and hardcoded values that are really
  settings. Every check here caught a real bug on 2026-09-10.
---

# stale-check

**A change to this app is not finished when the code works.** It is finished when the record says what changed, and when nothing left behind still describes the app as it was.

Every check below is here because it caught something real on 2026-09-10, when four sweeps across the docs, the site, the journal and the source found roughly forty wrong statements — none of which failed a test, and several of which had been wrong for weeks.

**The failure has one shape: a change that is correct in the code and leaves a description of the old behaviour somewhere else.** None of these fail loudly. A wrong variable name, a deleted button still mentioned, a date baked into a template — all read as authoritative.

Work through the parts in order. Skip a part only when it plainly does not apply, and say so rather than skipping silently.

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

Rules:

- **Write the entry before making the change**, not after. A reason reconstructed later is a story about what you would like to have been thinking.
- **One or two sentences: what changed, and why.** Both files. Long-form reasoning that constrains a future edit goes in `CLAUDE.md` instead, because that is the file read before the code is touched.
- **If the agent's own tools changed, add a line to `backend/agent_changes.json` too.** That is the one-line version aimed at the agent, and it wakes the agent on the next restart. A docs reshuffle does not need one.

---

## 2. The quoted prompt, if the prompt or the rules moved

`.claude/rules/agent.md` quotes the prompt verbatim. The quotation was in `CLAUDE.md` until 2026-09-13. **A quotation is stale the moment the original moves.** On 2026-09-10 it had drifted three ways at once — a system message still saying "paper-trading" a day after the code dropped the word, and two whole rules missing.

**A wrong quotation here is worse than none, because this file is read instead of the code.**

**The exact half is a test now**, not a step here: `backend/tests/test_the_documented_prompt_is_the_real_prompt.py` pins the system message, the opener, and the three rules that exist because of a live failure. Run the suite and it tells you. What is left below is the fuzzy half — the rules list, which the rule file paraphrases, so it needs a person reading hits rather than an assertion.

```sh
python3 - <<'EOF'
import re
a = open("backend/services/agent.py").read()
# Strip blockquote markers, or wrapped quotations never match.
c = re.sub(r"\s+", " ", re.sub(r"^\s*>\s?", "", open(".claude/rules/agent.md").read(), flags=re.M))
rules = [r for r in re.findall(r'"(- [^"]{10,})"', a) if not r.startswith("- {")]
print(f"{len(rules)} rules in the prompt. Not found in .claude/rules/agent.md by opening words:")
for r in rules:
    key = re.sub(r"\s+", " ", r[2:]).split("{")[0].strip()[:38]
    if len(key) > 12 and key not in c:
        print("  ", r[2:88])
EOF
```

**Read every hit; do not treat the list as a failure count.** The rule file paraphrases some rules and abbreviates others with `[...]`, so a paraphrase shows up here looking like a gap. The first run of this check flagged six, of which three were paraphrases and three were rules genuinely missing from CLAUDE.md — including two that appear only in the state that produces them (a zero balance, a conviction floor), which is exactly why reading the code had missed them.

Also re-read any CLAUDE.md sentence saying a thing **is not built**. Those rot fastest — one claimed an unbuilt feature that had shipped the previous day.

---

## 3. Values that are settings, stated as facts

**The single most common bug in this repo's prose.** A number or date that a deployment can change, written into a template or a doc as though it were a property of the software. It is true of this deployment, which is why it survives review, and wrong for every other one.

Caught this way: the experiment start date (every image claimed the first deployment's), `$0.05` research charge in six templates (free research is a supported mode), `$10,000` budget, and `WEBULL_ACCOUNT_ID` documented nowhere.

```sh
# Figures and dates asserted in site copy
grep -rnE '\$[0-9]|20[0-9]{2}-[0-9]{2}-[0-9]{2}' frontend/src --include=*.html | grep -v preview-view

# What the API actually serves, to bind against
grep -nE "^\s+[a-z_]+:" backend/api/schemas.py | sed -n '/class SettingsOut/,/^$/p'
```

The rule: **site copy may state what the software does, and may not state what this deployment has done.** If the value is a setting, serve it and bind it. If it cannot be bound — `index.html`'s `<title>` is parsed before Angular runs — leave a comment saying the hardcoding is deliberate, because otherwise it looks exactly like this bug.

Watch for **zero being a real setting.** `research.is_charging()` is `get_price() > 0`, so a `if (!value) return` guard keeps the default on precisely the deployment the fix was for.

---

## 4. References to things that no longer exist

**This is how the market-hours gate survived five days.** The scheduler's comment said "the market-hours gate is gone" — and it was, from the scheduler. A second copy lived one call deeper, where nobody looked.

Sweep for every removed mechanism by name, across code comments, templates and docs:

```sh
# Removed: the 11:00 sweep, the 13:35 pass, all 23 slash commands, every manual control.
# Tests and the archive pages are excluded — a 13:35 timestamp in a fixture is
# not a claim, and the two -experiment.md pages describe experiments that ended.
grep -rniE "morning sweep|daily sweep|13:35 (UTC|batch|pass)|11:00 UTC|each weekday|Decide now|\`?/(analyze|track|model|horizon|candidates)\`" \
  backend/ frontend/src/ docs/ README.md CLAUDE.md .claude/rules/ \
  --include=*.py --include=*.html --include=*.ts --include=*.md \
  --exclude-dir=tests --exclude="*.spec.ts" --exclude="*-experiment.md" \
  --exclude="changelog.md" --exclude="journey.md" \
  | grep -viE "used to|no longer|is gone|removed|until 20"
```

**A hit is fine when it is explicitly past tense. A hit in the present tense is a bug.** Expect roughly ten hits on a clean tree, most of them sentences explaining that the thing was removed. Widen the term list whenever something else is deleted — the value of this check is entirely in whether the list names what you just took out.

**Check dead code too.** It is where stale claims survive a sweep, because nobody reads it. `ask.py` sat unimported for nine days still telling readers to "run /analyze first". If nothing imports it, delete it — git remembers.

---

## 5. Renamed pages and routes

Old paths redirect, so **every link keeps working and nothing looks broken** — which is why dead page names outlived their pages by a fortnight. A reader told to "see the Tickers page" finds no such thing in the navigation.

```sh
grep -A2 "redirectTo" frontend/src/app/app.routes.ts        # what was renamed
grep -rniE "(Signals|Tickers|Alerts|Regime|Digest|Events) page" docs/ README.md frontend/src --include=*.md --include=*.html
```

---

## 6. Claims about the schedule and the environment

Two things the docs get wrong repeatedly, both verifiable against source:

```sh
# Every scheduled job, against every doc that lists times
grep -nE "scheduler\.add_task" backend/tasks/scheduler.py
grep -n "FINAL_PASS" backend/services/market_clock.py
```

**A time in Eastern is not a time in UTC.** The README scheduled the day's final pass at "20:55" under a column headed "Time (UTC)"; the code reads it off the Eastern close, so the table was right in winter and an hour out the rest of the year.

```sh
# Env vars the code requires, against the docs that list them
grep -rhoE 'os\.(environ\.get|getenv)\("[A-Z0-9_]+"' backend/ --include=*.py | grep -oE '"[A-Z0-9_]+"' | tr -d '"' | sort -u
grep -rn "WEBULL_ACCOUNT_ID" docs/ README.md .env.example compose.example.yaml
```

A newly required variable that appears in no doc produces a container that starts, reports healthy, and never places an order.

**A check that reads a variable is not a check that the thing works.** `/setup` verified `WEBULL_ACCOUNT_ID` held *something* and reported ready, while the sandbox had reissued its account numbers and the deployment could place no order. When adding a requirement, ask which of the two facts it tests — the value exists, or the thing the value names answers — and prefer the second.

---

## 7. Cross-references still resolve

Code comments cite journal entries by date. Moving an entry breaks them silently.

```sh
python3 - <<'EOF'
import re, subprocess
j = open("JOURNEY.md").read(); c = open("docs/changelog.md").read()
dates = {"JOURNEY.md": set(re.findall(r'^\*\*(2026-\d\d-\d\d)', j, re.M)),
         "changelog.md": set(re.findall(r'^## (2026-\d\d-\d\d)', c, re.M))}
files = subprocess.run(["grep","-rl","--include=*.py","--include=*.ts","--include=*.md",
                        "-e","JOURNEY.md","-e","changelog.md","."], capture_output=True, text=True).stdout.split()
for f in files:
    if "node_modules" in f or "TradingAgents" in f or ".venv" in f: continue
    for n, line in enumerate(open(f, errors="ignore"), 1):
        for tgt, known in dates.items():
            for d in re.findall(rf"{re.escape(tgt)}[^\n]{{0,30}}?(2026-\d\d-\d\d)", line):
                if d not in known: print(f"{f}:{n} -> {tgt} {d} NOT FOUND")
EOF

# Relative links in docs
python3 -c "
import re, os, glob
for f in glob.glob('docs/*.md') + ['README.md']:
    base = os.path.dirname(f) or '.'
    for m in re.finditer(r'\[([^\]]+)\]\(([^)#]+?)(#[^)]*)?\)', open(f).read()):
        t = m.group(2)
        if t.startswith(('http','mailto:')): continue
        if not os.path.exists(os.path.normpath(os.path.join(base, t))): print('DEAD', f, '->', t)
"
```

**`docs/journey.md` is a symlink to `JOURNEY.md`.** A relative link inside it resolves differently on the docs site than on GitHub, which is why the sibling links there are full URLs.

---

## 8. Verify

```sh
uv run pytest backend/tests -q
cd frontend && npx ng build && npx ng test --watch=false && npx prettier --write "src/**/*.{ts,html,css}"
uvx zensical build
```

Two things about the test output:

- **The frontend test run should report zero unhandled errors.** It reported eight for weeks, dismissed each time as a known `lightweight-charts` quirk. They were not a quirk: jsdom does not implement `matchMedia`, `src/test-setup.ts` now polyfills it, and the count went to zero. **Treat any unhandled error as yours until proven otherwise** — the previous eight hid the fact that `chart-theme.ts` was throwing on every chart test.
- **`backend/tests/conftest.py` refuses any call to the live broker.** If a test suddenly hits it, the code now reaches further than it used to — that is information, not an obstacle. Stub what it names.

---

## What this skill does not do

It finds statements that contradict the code. It cannot check a **measurement** — GPU throughput, token counts, vendor pricing. Those came from real runs recorded at the time, and re-deriving one is a day of work, not a sweep. If a change makes a measurement obsolete, say so and leave the number with its date.
