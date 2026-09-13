// Shows the token and prompt cache figures of the newest Claude Code session in the status bar.
// The Claude Code panel does not run a statusLine command. The hook .claude/helpers/session-usage.py
// writes the figures to .claude/usage/<session_id>.json, and this extension reads the newest file.
const vscode = require('vscode');
const fs = require('fs');
const path = require('path');

const TICK_MS = 30 * 1000;
const HIDE_AFTER_MS = 24 * 3600 * 1000;

function tokens(n) {
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `${Math.round(n / 1e3)}k`;
  return String(n);
}

function count(n) {
  return (n ?? 0).toLocaleString('en-US');
}

function newestUsageFile() {
  let newest = null;
  for (const folder of vscode.workspace.workspaceFolders ?? []) {
    const dir = path.join(folder.uri.fsPath, '.claude', 'usage');
    let names;
    try {
      names = fs.readdirSync(dir);
    } catch {
      continue;
    }
    for (const name of names) {
      if (!name.endsWith('.json')) continue;
      const file = path.join(dir, name);
      try {
        const mtime = fs.statSync(file).mtimeMs;
        if (!newest || mtime > newest.mtime) newest = { file, mtime };
      } catch {
        // The hook replaced the file during the scan. The next refresh reads it.
      }
    }
  }
  return newest;
}

function cacheState(usage, now) {
  const msLeft = usage.cache_expires_at ? usage.cache_expires_at * 1000 - now : 0;
  return { warm: msLeft > 0, minutes: Math.ceil(msLeft / 60000) };
}

function tooltip(usage, cache) {
  const totals = usage.totals ?? {};
  const rows = [
    ['Context now', `${count(usage.context_tokens)} tokens`],
    ['API requests', count(usage.requests)],
    ['Cache hit ratio', usage.hit_ratio == null ? 'no data' : `${Math.round(usage.hit_ratio * 100)}%`],
    ['Cache', cache.warm ? `warm for ${cache.minutes} more min (${usage.cache_ttl} TTL)` : 'cold: the next turn writes the context to the cache again'],
    ['Read from cache', count(totals.cache_read_input_tokens)],
    ['Written to cache', count(totals.cache_creation_input_tokens)],
    ['Uncached input', count(totals.input_tokens)],
    ['Output', count(totals.output_tokens)],
    ['graft saved', usage.graft_saved_tokens ? `about ${count(usage.graft_saved_tokens)} tokens` : 'none recorded'],
    ['Model', usage.model ?? 'unknown'],
    ['Updated', usage.updated_at ? new Date(usage.updated_at * 1000).toLocaleTimeString() : 'unknown'],
  ];
  const table = rows.map(([name, value]) => `| ${name} | ${value} |`).join('\n');
  const session = (usage.session_id ?? '').slice(0, 8);
  return new vscode.MarkdownString(`**Claude session ${session}**\n\n| | |\n|---|---:|\n${table}\n\nClick to open the usage file.`);
}

function render(item) {
  const now = Date.now();
  const newest = newestUsageFile();
  if (!newest || now - newest.mtime > HIDE_AFTER_MS) {
    item.hide();
    return;
  }
  let usage;
  try {
    usage = JSON.parse(fs.readFileSync(newest.file, 'utf8'));
  } catch {
    return;
  }
  const cache = cacheState(usage, now);
  const parts = [`$(pulse) ${tokens(usage.context_tokens ?? 0)}`];
  if (usage.hit_ratio != null) parts.push(`cache ${Math.round(usage.hit_ratio * 100)}%`);
  parts.push(cache.warm ? `warm ${cache.minutes}m` : 'cold');
  if (usage.graft_saved_tokens) parts.push(`graft ${tokens(usage.graft_saved_tokens)}`);
  item.text = parts.join(' · ');
  item.tooltip = tooltip(usage, cache);
  item.command = { title: 'Open the usage file', command: 'vscode.open', arguments: [vscode.Uri.file(newest.file)] };
  item.show();
}

function activate(context) {
  const item = vscode.window.createStatusBarItem('claudeUsageBar.session', vscode.StatusBarAlignment.Right, 100);
  item.name = 'Claude session usage';
  const refresh = () => render(item);
  const watcher = vscode.workspace.createFileSystemWatcher('**/.claude/usage/*.json');
  watcher.onDidChange(refresh);
  watcher.onDidCreate(refresh);
  watcher.onDidDelete(refresh);
  // The timer moves the cache countdown between two hook runs.
  const timer = setInterval(refresh, TICK_MS);
  context.subscriptions.push(item, watcher, vscode.workspace.onDidChangeWorkspaceFolders(refresh), { dispose: () => clearInterval(timer) });
  refresh();
}

function deactivate() {}

module.exports = { activate, deactivate };
