#!/usr/bin/env python3
"""PostToolUse, Stop and SessionEnd hook: write the token and prompt cache totals of this session to a file.

The VS Code panel does not run the statusLine command and does not show hook
output. The usage bar extension in .claude/vscode-usage-bar/ reads the file
that this hook writes: .claude/usage/<session_id>.json.

Session transcripts grow past 50 MB. The hook keeps its read position in the
same file and reads only the lines added since its last run.

Main-thread requests and subagent requests are counted apart. Only the main
thread sets the context size and the cache figures. Subagent tokens add to
subagent_totals, so a fan-out never looks like context growth.

The SessionEnd event marks the file ended. The extension then drops the session
from its list. Any later event, such as a resumed session, clears the mark.

graft writes its own figures to graft/.cache/session/<session_id>.json, and the
extension reads that file itself. This hook does not copy them, because hooks
on one event run in parallel and a copy could be one tool call old.
"""
import json
import os
import sys
import time
from datetime import datetime

KEEP_SECONDS = 7 * 24 * 3600
TTL_SECONDS = {"5m": 300, "1h": 3600}
TOTALS = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens")
# The transcript writes one API response as several lines, one for each content
# block, and each line repeats the usage. The hook counts a message id one time
# and keeps the latest usage for it. Subagents run in parallel, so their lines
# interleave with the main thread's, and the window must hold all of them.
RECENT_IDS = 128


def new_state(session_id: str, transcript_path: str) -> dict:
    return {
        "session_id": session_id,
        "transcript_path": transcript_path,
        "offset": 0,
        "requests": 0,
        "totals": dict.fromkeys(TOTALS, 0),
        "subagent_requests": 0,
        "subagent_totals": dict.fromkeys(TOTALS, 0),
        "recent": [],
        "context_tokens": 0,
        "last_hit_ratio": None,
        "cache_ttl": None,
        "last_request_at": None,
        "last_request_cached": False,
        "model": None,
        "ended": False,
        "ended_at": None,
        "ended_reason": None,
    }


def ttl_of(usage: dict) -> str | None:
    creation = usage.get("cache_creation") or {}
    if creation.get("ephemeral_1h_input_tokens"):
        return "1h"
    if creation.get("ephemeral_5m_input_tokens"):
        return "5m"
    return None


def epoch(timestamp) -> float | None:
    try:
        return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return None


def read_new_lines(state: dict) -> None:
    path = state["transcript_path"]
    try:
        if os.path.getsize(path) < state["offset"]:
            state.update(new_state(state["session_id"], path))
        with open(path, "rb") as f:
            f.seek(state["offset"])
            chunk = f.read()
    except OSError:
        return
    # Stop at the last complete line. Claude Code can be in the middle of a write.
    end = chunk.rfind(b"\n") + 1
    state["offset"] += end
    recent = state["recent"]
    for raw in chunk[:end].splitlines():
        if b'"usage"' not in raw:
            continue
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        message = entry.get("message") or {}
        usage = message.get("usage")
        if entry.get("type") != "assistant" or not usage:
            continue
        if message.get("model") == "<synthetic>":
            continue
        sidechain = bool(entry.get("isSidechain"))
        bucket = state["subagent_totals"] if sidechain else state["totals"]
        now = {key: usage.get(key) or 0 for key in TOTALS}
        message_id = message.get("id")
        seen = next((item for item in recent if item[0] == message_id), None) if message_id else None
        if seen is None:
            before = dict.fromkeys(TOTALS, 0)
            state["subagent_requests" if sidechain else "requests"] += 1
            recent.append([message_id, now])
            del recent[:-RECENT_IDS]
        else:
            before, seen[1] = seen[1], now
        for key in TOTALS:
            bucket[key] += now[key] - before[key]
        if sidechain:
            continue
        context = now["input_tokens"] + now["cache_read_input_tokens"] + now["cache_creation_input_tokens"]
        state["context_tokens"] = context
        # The ratio of this one request says whether the cache works now. The
        # session ratio in summarize() barely moves late in a long session.
        state["last_hit_ratio"] = now["cache_read_input_tokens"] / context if context else None
        state["cache_ttl"] = ttl_of(usage) or state["cache_ttl"]
        state["last_request_at"] = epoch(entry.get("timestamp")) or state["last_request_at"]
        state["last_request_cached"] = bool(now["cache_read_input_tokens"] or now["cache_creation_input_tokens"])
        state["model"] = message.get("model") or state["model"]


def summarize(state: dict) -> None:
    totals = state["totals"]
    all_input = totals["input_tokens"] + totals["cache_read_input_tokens"] + totals["cache_creation_input_tokens"]
    state["hit_ratio"] = totals["cache_read_input_tokens"] / all_input if all_input else None
    ttl = TTL_SECONDS.get(state["cache_ttl"])
    cached = ttl and state["last_request_at"] and state["last_request_cached"]
    state["cache_expires_at"] = state["last_request_at"] + ttl if cached else None
    state["updated_at"] = time.time()


def mark_ended(state: dict, data: dict) -> None:
    ended = data.get("hook_event_name") == "SessionEnd"
    state["ended"] = ended
    state["ended_at"] = time.time() if ended else None
    state["ended_reason"] = data.get("reason") if ended else None


def remove_old_files(usage_dir: str) -> None:
    cutoff = time.time() - KEEP_SECONDS
    for name in os.listdir(usage_dir):
        path = os.path.join(usage_dir, name)
        try:
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
        except OSError:
            pass


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return
    session_id = os.path.basename(data.get("session_id") or "")
    transcript_path = data.get("transcript_path")
    if not session_id or not transcript_path:
        return
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or os.getcwd()
    usage_dir = os.path.join(project_dir, ".claude", "usage")
    out_path = os.path.join(usage_dir, f"{session_id}.json")
    try:
        with open(out_path) as f:
            state = json.load(f)
        if state.get("transcript_path") != transcript_path:
            raise ValueError
    except (OSError, ValueError):
        state = new_state(session_id, transcript_path)
    # A file written by an older version of this hook lacks the newer keys.
    for key, value in new_state(session_id, transcript_path).items():
        state.setdefault(key, value)
    read_new_lines(state)
    summarize(state)
    mark_ended(state, data)
    os.makedirs(usage_dir, exist_ok=True)
    # Write a temporary file and rename it, so the extension never reads half a file.
    tmp_path = f"{out_path}.{os.getpid()}.tmp"
    with open(tmp_path, "w") as f:
        json.dump(state, f, indent=1)
    os.replace(tmp_path, out_path)
    remove_old_files(usage_dir)


if __name__ == "__main__":
    main()
