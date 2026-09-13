---
name: wait-in-background
description: Use before starting anything that takes longer than about a minute — the test suites, an Angular build, a docker build, a container restart, a probe run, a GPU benchmark, or an analysis the agent commissioned. Start it in the background as one command that exits when the work is done, then end the turn. Never check on it again and again from the chat.
---

# Wait in the background

**Each check from the chat is a new step, and each step re-reads the whole session.** Before 2026-09-13, about 360 commands in this project's session logs waited with `sleep` or an `until` loop in the chat. Each check cost the size of the session, often 500k tokens, to learn "not done yet".

A background command costs two steps: one to start it, and one when Claude Code wakes you because it exited.

A `PreToolUse` hook (`.claude/helpers/no-foreground-polling.py`) refuses a foreground `until` or `while` loop with `sleep`, a `sleep` of 30 seconds or more, and `watch`. This skill says what to do in their place.

## The procedure

1. **Write one command that exits when the work is done.** Put the wait loop inside the command. Bound it with `timeout`, so a hang cannot run forever.
2. **Keep the output short.** End with `| tail -20`, or print one summary line. The output enters the session, and every later step re-reads it.
3. **Run it with `run_in_background: true`.**
4. **End the turn.** Tell the user in one sentence what runs and what you will report. Do not check on it before it exits.
5. **When Claude Code wakes you, read the result once and report it.**

If the user asks for the status before the command exits, check one time. Do not start a loop.

If a condition changes over time and you must react to each change, such as each new log line, use the `Monitor` tool with an until-loop.

## Recipes for this project

The dashboard runs on port 8125. `docker ps` is the authority if that changed.

**Backend tests**

```sh
cd /home/kr/projects/trading-helper && timeout 1200 uv run pytest -q backend/tests 2>&1 | tail -15
```

**Frontend tests and build**

```sh
cd /home/kr/projects/trading-helper/frontend && timeout 1200 npx ng test --watch=false 2>&1 | tail -30
cd /home/kr/projects/trading-helper/frontend && timeout 1200 npx ng build 2>&1 | tail -15
```

**Docker image build**

```sh
cd /home/kr/projects/trading-helper && timeout 3600 docker build -t trading-experiment:local . 2>&1 | tail -20
```

**The container answers again after a restart**

```sh
timeout 300 sh -c 'until curl -sf localhost:8125/api/settings >/dev/null; do sleep 5; done' && echo "dashboard is up"
```

**A new analysis or a new decision pass is recorded.** An analysis takes about 18 minutes on the GPU pool. No log line marks the finish, so the script waits for a new row id. Set `WHAT` to `signals` for an analysis, or to `agent/events` for a decision pass.

```sh
WHAT=signals timeout 7200 python3 - <<'EOF'
import json, os, time, urllib.request
url = f"http://localhost:8125/api/{os.environ['WHAT']}?limit=20"
def rows():
    return json.load(urllib.request.urlopen(url, timeout=30))
seen = {row["id"] for row in rows()}
while True:
    new = [row for row in rows() if row["id"] not in seen]
    if new:
        break
    time.sleep(60)
for row in new:
    print({k: row.get(k) for k in ("id", "ticker", "decision", "model", "duration_seconds", "ran_at", "orders")
           if k in row})
EOF
```

**The GPU pool is idle again.** `/healthz` returns one entry per backend, each with an `active` count.

```sh
timeout 3600 python3 - <<'EOF'
import json, time, urllib.request
while True:
    pool = json.load(urllib.request.urlopen("http://localhost:11435/healthz", timeout=30))
    if all(backend["active"] == 0 for backend in pool.values()):
        break
    time.sleep(30)
print("pool idle")
EOF
```

**A probe run** (see the `probe-the-prompt` skill)

```sh
cd /home/kr/projects/trading-helper && timeout 1800 uv run python -m backend.scripts.probe_prompt --turn turn1 --parallel 2>&1 | tail -40
```
