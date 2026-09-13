#!/usr/bin/env python3
"""PreToolUse hook for Bash: refuse a command that waits in the foreground.

When Claude waits in the chat, it checks again and again, and each check is a
new step that re-reads the whole session. A background command wakes Claude
once, when it exits. This hook refuses three shapes of foreground wait:

- an `until` or `while` loop that contains `sleep`
- a `sleep` of 30 seconds or more
- `watch`

A short `sleep` outside such a loop still runs, because rate-limit pacing needs
it. A command with run_in_background set always runs.
"""
import json
import re
import sys

# Matches the shell's `sleep 60` and `sleep 2m`, and Python's `time.sleep(60)`.
SLEEP = re.compile(r"\bsleep[\s(]+(\d+(?:\.\d+)?)([smh]?)\b")
UNIT_SECONDS = {"": 1, "s": 1, "m": 60, "h": 3600}
LOOP = re.compile(r"\b(until|while)\b")
WATCH = re.compile(r"(^|[;&|(]\s*)watch\s")

REASON = (
    "Refused: this command waits in the foreground. Each check in the chat re-reads the whole session. "
    "Run it again with run_in_background: true, as one command that exits when the work is done "
    "(put the until-loop inside it, and bound it with timeout). Claude Code wakes you when it exits. "
    "Then end your turn. The wait-in-background skill has recipes for this project."
)


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return
    if data.get("tool_name") != "Bash":
        return
    tool_input = data.get("tool_input") or {}
    if tool_input.get("run_in_background"):
        return
    command = tool_input.get("command") or ""
    sleeps = [float(m.group(1)) * UNIT_SECONDS[m.group(2)] for m in SLEEP.finditer(command)]
    loop_wait = bool(sleeps) and bool(LOOP.search(command))
    long_sleep = any(seconds >= 30 for seconds in sleeps)
    if loop_wait or long_sleep or WATCH.search(command):
        print(REASON, file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
