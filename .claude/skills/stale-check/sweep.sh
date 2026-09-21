#!/usr/bin/env bash
# Every mechanical sweep in stale-check, in one pass, in about two seconds.
#
# Sections are numbered to match the parts of SKILL.md. This finds nothing on
# its own — it prints hits for a reader to judge, and SKILL.md says how to
# judge each section. A hit is not a failure count.
#
# Widen section 4's term list whenever something is deleted from the app. The
# value of that check is entirely in whether the list names what you just
# took out.
set -uo pipefail
cd "$(git rev-parse --show-toplevel)" || exit 1

echo "### 0. CHANGED FILES — every section below is judged against this list"
git diff --name-only HEAD

echo; echo "### 2. RULES IN THE PROMPT NOT FOUND IN .claude/rules/agent.md"
python3 - <<'PY'
import re
a = open("backend/services/agent.py").read()
# Strip blockquote markers, or wrapped quotations never match.
c = re.sub(r"\s+", " ", re.sub(r"^\s*>\s?", "", open(".claude/rules/agent.md").read(), flags=re.M))
rules = [r for r in re.findall(r'"(- [^"]{10,})"', a) if not r.startswith("- {")]
print(f"({len(rules)} rules in the prompt; the rule file paraphrases some, and a paraphrase shows up here as a gap)")
for r in rules:
    key = re.sub(r"\s+", " ", r[2:]).split("{")[0].strip()[:38]
    if len(key) > 12 and key not in c:
        print("  ", r[2:88])
PY

echo; echo "### 3. FIGURES AND DATES ASSERTED IN SITE COPY"
grep -rnE '\$[0-9]|20[0-9]{2}-[0-9]{2}-[0-9]{2}' frontend/src --include=*.html | grep -v preview-view
echo "-- what the API serves, to bind against instead:"
grep -nE "^\s+[a-z_]+:" backend/api/schemas.py | sed -n '/class SettingsOut/,/^$/p'

echo; echo "### 4. REFERENCES TO REMOVED MECHANISMS (past tense is fine, present tense is a bug)"
grep -rniE "morning sweep|daily sweep|13:35 (UTC|batch|pass)|11:00 UTC|each weekday|Decide now|\`?/(analyze|track|model|horizon|candidates)\`" \
  backend/ frontend/src/ docs/ README.md CLAUDE.md .claude/rules/ \
  --include=*.py --include=*.html --include=*.ts --include=*.md \
  --exclude-dir=tests --exclude="*.spec.ts" --exclude="*-experiment.md" \
  --exclude="changelog.md" --exclude="journey.md" \
  | grep -viE "used to|no longer|is gone|removed|until 20"

echo; echo "### 5. RENAMED ROUTES, AND PAGE NAMES IN PROSE"
grep -A2 "redirectTo" frontend/src/app/app.routes.ts
grep -rniE "(Signals|Tickers|Alerts|Regime|Digest|Events) page" docs/ README.md frontend/src --include=*.md --include=*.html

echo; echo "### 6. SCHEDULED JOBS, AND ENV VARS THE CODE REQUIRES"
grep -nE "scheduler\.add_task" backend/tasks/scheduler.py
grep -n "FINAL_PASS" backend/services/market_clock.py
echo "-- env vars read by the code (check each against the docs that list them):"
grep -rhoE 'os\.(environ\.get|getenv)\("[A-Z0-9_]+"' backend/ --include=*.py \
  | grep -oE '"[A-Z0-9_]+"' | tr -d '"' | sort -u | tr '\n' ' '
echo

echo; echo "### 7. CROSS-REFERENCES AND DEAD LINKS"
python3 - <<'PY'
import re, subprocess, os, glob
j = open("JOURNEY.md").read(); c = open("docs/changelog.md").read()
dates = {"JOURNEY.md": set(re.findall(r'^\*\*(2026-\d\d-\d\d)', j, re.M)),
         "changelog.md": set(re.findall(r'^## (2026-\d\d-\d\d)', c, re.M))}
files = subprocess.run(["grep", "-rl", "--include=*.py", "--include=*.ts", "--include=*.md",
                        "-e", "JOURNEY.md", "-e", "changelog.md", "."],
                       capture_output=True, text=True).stdout.split()
for f in files:
    if "node_modules" in f or "TradingAgents" in f or ".venv" in f: continue
    for n, line in enumerate(open(f, errors="ignore"), 1):
        for tgt, known in dates.items():
            for d in re.findall(rf"{re.escape(tgt)}[^\n]{{0,30}}?(2026-\d\d-\d\d)", line):
                if d not in known: print(f"{f}:{n} -> {tgt} {d} NOT FOUND")
# docs/journey.md is a symlink to JOURNEY.md, so a relative link inside it
# resolves differently on the docs site than on GitHub — the sibling links
# there are full URLs on purpose.
for f in glob.glob("docs/*.md") + ["README.md"]:
    base = os.path.dirname(f) or "."
    for m in re.finditer(r'\[([^\]]+)\]\(([^)#]+?)(#[^)]*)?\)', open(f).read()):
        t = m.group(2)
        if t.startswith(("http", "mailto:")): continue
        if not os.path.exists(os.path.normpath(os.path.join(base, t))):
            print("DEAD", f, "->", t)
PY

echo; echo "### 2b. CLAIMS THAT A THING IS NOT BUILT (these rot fastest)"
grep -rniE "not (yet )?built|no such|does not exist|is not implemented" PLAN.md CLAUDE.md

echo; echo "### done — nothing above is a failure count. Read the hits."
