#!/usr/bin/env python3
"""UserPromptSubmit hook: say how large the session is, so a new topic starts a new session.

Every step Claude takes re-reads the whole conversation. A hook cannot run /clear
or /compact, so this hook only tells two readers the size: the user sees a
one-line message, and Claude gets an instruction to suggest /clear when the new
request is not part of the current task. Below the threshold it prints nothing.

The threshold is CONTEXT_NUDGE_TOKENS (default 150000).
"""
import json
import os
import sys

THRESHOLD = int(os.environ.get("CONTEXT_NUDGE_TOKENS", "150000"))
# Session logs grow past 50 MB. The newest usage block is near the end, so read
# only the tail.
TAIL_BYTES = 4 * 1024 * 1024


def last_context_tokens(transcript_path: str) -> int | None:
    try:
        with open(transcript_path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - TAIL_BYTES))
            lines = f.read().decode("utf-8", errors="ignore").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if entry.get("type") != "assistant" or entry.get("isSidechain"):
            continue
        usage = (entry.get("message") or {}).get("usage")
        if not usage:
            continue
        return sum(
            usage.get(key) or 0
            for key in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
        )
    return None


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return
    tokens = last_context_tokens(data.get("transcript_path") or "")
    if tokens is None or tokens < THRESHOLD:
        return
    size = f"{tokens // 1000}k"
    print(json.dumps({
        "systemMessage": f"This session holds about {size} tokens of context.",
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": (
                f"Context check: this session holds about {size} tokens, and every step re-reads all of it. "
                "If the new request is not part of the current task, start your reply with one sentence: "
                "tell the user to run /clear, and offer to write a handoff note first with the handoff skill. "
                "Then answer the request. If the request continues the current task, do not mention this."
            ),
        },
    }))


if __name__ == "__main__":
    main()
