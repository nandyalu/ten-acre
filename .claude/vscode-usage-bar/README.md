# Claude usage bar

This VS Code extension shows the token and prompt cache figures of the newest Claude Code session in the status bar. The Claude Code panel does not run the `statusLine` command from `.claude/settings.json`, so this extension shows the figures in its place.

## How it works

1. The hook `.claude/helpers/session-usage.py` runs after each tool call and at the end of each turn.
2. The hook reads the new lines of the session transcript and writes the totals to `.claude/usage/<session_id>.json`.
3. The extension reads the newest file in `.claude/usage/` and shows it in the status bar.

The status bar shows the context size, the cache hit ratio, the minutes until the cache expires, and the tokens that graft saved. Put the pointer on the item to see all the figures. Click the item to open the usage file.

## Install

Run these commands from the repository root:

```sh
cd .claude/vscode-usage-bar
npx --yes @vscode/vsce package --allow-missing-repository --skip-license -o /tmp/claude-usage-bar.vsix
code --install-extension /tmp/claude-usage-bar.vsix
```

Then run **Developer: Reload Window** in VS Code.

## Limits

- The hook cannot see the `prompt_cache` object of the status line, so the bar does not show cache misses or their causes. Run `/usage` in the panel to see those.
- The bar shows one session: the one whose file changed last.
- The hook deletes a usage file that is older than seven days.
