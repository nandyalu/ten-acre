# Claude usage bar

This VS Code extension shows the token, prompt cache, cost and graft figures of the live Claude Code sessions. The Claude Code panel does not run the `statusLine` command from `.claude/settings.json`, so this extension shows the figures in its place.

## What it shows

- **The status bar item** shows the session whose figures changed last: the context size, the cache hit ratio of the last request, the minutes until the cache expires, the tokens graft saved, and the input cost. When more than one session is live, it also shows the count. The item turns to the warning color when the context passes `claudeUsageBar.warnTokens`. The default is 150,000, the same line the context-nudge hook uses.
- **The hover** lists every live session in one table.
- **The details pane** opens on a click, or with the command *Claude usage: show sessions*. It shows one card per live session: a context meter with the warning line, tiles for the cache, the cost, graft and the request counts, and under "All figures" a table with the rest. "Open the usage file" shows the raw JSON.

## How it works

1. The hook `.claude/helpers/session-usage.py` runs after each tool call, at the end of each turn, and when a session ends. In `.claude/settings.json` it sits in its own hook group under `PostToolUse`, `Stop` and `SessionEnd`. Keep it that way: `graft init` replaces every group that holds a graft command, and a project command in that group is lost with it.
2. The hook reads the new lines of the session transcript and writes the totals to `.claude/usage/<session_id>.json`. Main-thread requests and subagent requests are counted apart. A `SessionEnd` event marks the file ended.
3. graft writes its own figures, the tokens it saved and its estimate of the input cost, to `graft/.cache/session/<session_id>.json`.
4. The extension reads both files for every session that is not ended and was written in the last 24 hours. It refreshes when either file changes, and every 30 seconds.

## Install

Run these commands from the repository root:

```sh
cd .claude/vscode-usage-bar
npx --yes @vscode/vsce package --allow-missing-repository --skip-license -o /tmp/claude-usage-bar.vsix
code --install-extension /tmp/claude-usage-bar.vsix
```

Then run **Developer: Reload Window** in VS Code.

## Limits

- The hook cannot see the `prompt_cache` object of the status line, so the pane does not show cache misses or their causes. Run `/usage` in the panel to see those.
- The input cost is graft's estimate, not a bill.
- A session that ends without a `SessionEnd` event, such as a killed process, stays in the list for up to 24 hours.
- The hook deletes a usage file that is older than seven days.
