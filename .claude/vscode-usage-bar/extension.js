// Shows the token, prompt cache, cost and graft figures of the live Claude Code sessions.
// The status bar item shows the session whose figures changed last. The hover lists every
// live session, and a click opens a details pane with all of them.
//
// The Claude Code panel does not run a statusLine command. The hook .claude/helpers/session-usage.py
// writes the figures to .claude/usage/<session_id>.json, graft writes its own figures to
// graft/.cache/session/<session_id>.json, and this extension reads both.
const vscode = require('vscode');
const fs = require('fs');
const path = require('path');

const TICK_MS = 30 * 1000;
const HIDE_AFTER_MS = 24 * 3600 * 1000;
const DETAILS_COMMAND = 'claudeUsageBar.showDetails';
const DEFAULT_WARN_TOKENS = 150000;

function tokens(n) {
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `${Math.round(n / 1e3)}k`;
  return String(n);
}

function percent(ratio) {
  return ratio == null ? null : `${Math.round(ratio * 100)}%`;
}

function dollars(micros) {
  return micros == null ? null : `$${(micros / 1e6).toFixed(2)}`;
}

function ago(ms) {
  const minutes = Math.round(ms / 60000);
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes} min ago`;
  return `${Math.round(minutes / 60)} h ago`;
}

function readJson(file) {
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch {
    return null; // Missing, or a hook was replacing it. The next refresh reads it.
  }
}

function warnTokens() {
  return vscode.workspace.getConfiguration('claudeUsageBar').get('warnTokens', DEFAULT_WARN_TOKENS);
}

// One plain object per session, with everything the bar, the hover and the pane show.
function summarize(usage, graft, meta) {
  const msLeft = usage.cache_expires_at ? usage.cache_expires_at * 1000 - meta.now : 0;
  const totals = usage.totals ?? {};
  const sub = usage.subagent_totals ?? {};
  return {
    id: usage.session_id ?? '',
    short: (usage.session_id ?? '').slice(0, 8),
    folder: meta.folder,
    file: meta.file,
    mtime: meta.mtime,
    updatedAgo: ago(meta.now - meta.mtime),
    model: usage.model ?? 'unknown',
    context: usage.context_tokens ?? 0,
    lastHit: usage.last_hit_ratio ?? null,
    sessionHit: usage.hit_ratio ?? null,
    warm: msLeft > 0,
    warmMinutes: Math.ceil(msLeft / 60000),
    ttl: usage.cache_ttl ?? null,
    requests: usage.requests ?? 0,
    cacheRead: totals.cache_read_input_tokens ?? 0,
    cacheWritten: totals.cache_creation_input_tokens ?? 0,
    uncached: totals.input_tokens ?? 0,
    output: totals.output_tokens ?? 0,
    subagentRequests: usage.subagent_requests ?? 0,
    subagentInput: (sub.input_tokens ?? 0) + (sub.cache_read_input_tokens ?? 0) + (sub.cache_creation_input_tokens ?? 0),
    subagentOutput: sub.output_tokens ?? 0,
    graftSaved: graft.savedTokens ?? 0,
    graftReads: graft.graftReads ?? 0,
    sourceReads: graft.sourceReads ?? 0,
    costMicros: graft.inputCostMicros ?? null,
    billed: graft.inputTokensBilled ?? null,
  };
}

// Every session that is not ended and was written in the last 24 hours, newest first.
function loadSessions(now) {
  const sessions = [];
  for (const folder of vscode.workspace.workspaceFolders ?? []) {
    const root = folder.uri.fsPath;
    let names;
    try {
      names = fs.readdirSync(path.join(root, '.claude', 'usage'));
    } catch {
      continue;
    }
    for (const name of names) {
      if (!name.endsWith('.json')) continue;
      const file = path.join(root, '.claude', 'usage', name);
      let mtime;
      try {
        mtime = fs.statSync(file).mtimeMs;
      } catch {
        continue;
      }
      if (now - mtime > HIDE_AFTER_MS) continue;
      const usage = readJson(file);
      if (!usage || usage.ended) continue;
      const graft = readJson(path.join(root, 'graft', '.cache', 'session', `${usage.session_id}.json`)) ?? {};
      sessions.push(summarize(usage, graft, { file, mtime, folder: folder.name, now }));
    }
  }
  return sessions.sort((a, b) => b.mtime - a.mtime);
}

function barText(s, total) {
  const parts = [`$(pulse) ${tokens(s.context)}`];
  if (s.lastHit != null) parts.push(`cache ${percent(s.lastHit)}`);
  parts.push(s.warm ? `warm ${s.warmMinutes}m` : 'cold');
  if (s.graftSaved) parts.push(`graft ${tokens(s.graftSaved)}`);
  if (s.costMicros != null) parts.push(dollars(s.costMicros));
  if (total > 1) parts.push(`${total} sessions`);
  return parts.join(' · ');
}

function hover(sessions) {
  const rows = sessions.map((s, i) => [
    `${i === 0 ? '$(pulse) ' : ''}${s.short} · ${s.folder}`,
    tokens(s.context),
    percent(s.lastHit) ?? 'no data',
    s.warm ? `warm ${s.warmMinutes}m` : 'cold',
    s.graftSaved ? tokens(s.graftSaved) : 'none',
    dollars(s.costMicros) ?? 'no data',
    s.updatedAgo,
  ]);
  const md = new vscode.MarkdownString([
    `**Claude sessions** · ${sessions.length} live`,
    '',
    '| Session | Context | Cache | State | graft saved | Input cost | Updated |',
    '|---|---:|---:|---|---:|---:|---|',
    ...rows.map((cells) => `| ${cells.join(' | ')} |`),
    '',
    'Click to open the details pane.',
  ].join('\n'));
  md.supportThemeIcons = true;
  return md;
}

let panel = null;
let sessions = [];

function postSessions() {
  if (panel) panel.webview.postMessage({ type: 'sessions', sessions, warnTokens: warnTokens() });
}

function showPanel(context) {
  if (panel) {
    panel.reveal();
  } else {
    panel = vscode.window.createWebviewPanel('claudeUsageBar.details', 'Claude sessions', vscode.ViewColumn.Beside, {
      enableScripts: true,
      retainContextWhenHidden: true,
    });
    panel.webview.html = pageHtml();
    panel.webview.onDidReceiveMessage(
      (message) => {
        // Open only a file this extension listed itself. The pane cannot name another one.
        if (message?.type === 'open' && sessions.some((s) => s.file === message.file)) {
          vscode.commands.executeCommand('vscode.open', vscode.Uri.file(message.file));
        }
      },
      undefined,
      context.subscriptions,
    );
    panel.onDidDispose(() => { panel = null; }, undefined, context.subscriptions);
  }
  postSessions();
}

function render(item) {
  const now = Date.now();
  sessions = loadSessions(now);
  if (!sessions.length) {
    item.hide();
    postSessions();
    return;
  }
  const current = sessions[0];
  item.text = barText(current, sessions.length);
  item.tooltip = hover(sessions);
  item.backgroundColor = current.context >= warnTokens() ? new vscode.ThemeColor('statusBarItem.warningBackground') : undefined;
  item.show();
  postSessions();
}

function nonce() {
  const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
  let out = '';
  for (let i = 0; i < 32; i++) out += chars[Math.floor(Math.random() * chars.length)];
  return out;
}

// The pane is one page that draws itself from the messages the extension posts.
// Every color is an editor theme variable, so the page follows light and dark themes.
function pageHtml() {
  const n = nonce();
  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'nonce-${n}'; script-src 'nonce-${n}';">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Claude sessions</title>
<style nonce="${n}">
  :root { color-scheme: light dark; }
  body {
    margin: 0;
    padding: 16px 20px 32px;
    font-family: var(--vscode-font-family);
    font-size: var(--vscode-font-size);
    color: var(--vscode-foreground);
    background: var(--vscode-editor-background);
  }
  .page-head { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; margin-bottom: 16px; }
  h1 { margin: 0; font-size: 1.3em; font-weight: 600; }
  #summary, #empty { color: var(--vscode-descriptionForeground); }
  .card {
    margin-bottom: 14px;
    padding: 14px 16px;
    border: 1px solid var(--vscode-widget-border, var(--vscode-panel-border));
    border-radius: 6px;
    background: var(--vscode-editorWidget-background);
  }
  .card-head { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 6px 12px; margin-bottom: 12px; }
  .card-title { display: flex; align-items: center; gap: 8px; font-weight: 600; }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--vscode-descriptionForeground); }
  .dot-live { background: var(--vscode-charts-green); }
  .session-id { font-family: var(--vscode-editor-font-family); }
  .folder { color: var(--vscode-descriptionForeground); font-weight: 400; }
  .badge {
    padding: 1px 7px;
    border-radius: 10px;
    font-size: 0.8em;
    font-weight: 500;
    background: var(--vscode-badge-background);
    color: var(--vscode-badge-foreground);
  }
  .card-meta { display: flex; gap: 12px; font-size: 0.92em; color: var(--vscode-descriptionForeground); }
  .meter { margin-bottom: 12px; }
  .meter-head { display: flex; justify-content: space-between; margin-bottom: 4px; }
  .meter-label { color: var(--vscode-descriptionForeground); }
  .meter-value { font-weight: 600; font-variant-numeric: tabular-nums; }
  .meter-track { position: relative; height: 8px; border-radius: 4px; background: var(--vscode-scrollbarSlider-background); }
  .meter-fill { height: 100%; border-radius: 4px; background: var(--vscode-charts-blue); transition: width 0.3s; }
  .meter-over .meter-fill { background: var(--vscode-charts-orange); }
  .meter-tick { position: absolute; top: -3px; width: 2px; height: 14px; background: var(--vscode-foreground); opacity: 0.5; }
  .meter-foot { margin-top: 4px; font-size: 0.9em; color: var(--vscode-descriptionForeground); }
  .meter-over .meter-foot { color: var(--vscode-editorWarning-foreground); }
  .tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 8px; margin-bottom: 10px; }
  .tile {
    padding: 10px 12px;
    border: 1px solid var(--vscode-widget-border, var(--vscode-panel-border));
    border-radius: 5px;
    background: var(--vscode-editor-background);
  }
  .tile-good { border-left: 3px solid var(--vscode-charts-green); }
  .tile-warn { border-left: 3px solid var(--vscode-charts-orange); }
  .tile-label { margin-bottom: 4px; font-size: 0.85em; color: var(--vscode-descriptionForeground); }
  .tile-value { font-size: 1.35em; font-weight: 600; font-variant-numeric: tabular-nums; }
  .tile-note { margin-top: 3px; font-size: 0.85em; color: var(--vscode-descriptionForeground); }
  details { margin-top: 6px; }
  summary { cursor: pointer; color: var(--vscode-textLink-foreground); }
  .facts { margin: 8px 0; border-collapse: collapse; font-size: 0.92em; }
  .facts th { padding: 2px 16px 2px 0; text-align: left; font-weight: 400; color: var(--vscode-descriptionForeground); }
  .facts td { padding: 2px 0; font-variant-numeric: tabular-nums; word-break: break-all; }
  button.link { padding: 0; border: none; background: none; font: inherit; color: var(--vscode-textLink-foreground); cursor: pointer; }
  button.link:hover { text-decoration: underline; }
</style>
</head>
<body>
<div class="page-head"><h1>Claude sessions</h1><span id="summary"></span></div>
<main id="sessions"></main>
<p id="empty" hidden>No live session. The first tool call of a session writes its figures.</p>
<script nonce="${n}">
  const vscode = acquireVsCodeApi();
  const root = document.getElementById('sessions');
  const summary = document.getElementById('summary');
  const empty = document.getElementById('empty');

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }
  function tokens(n) {
    if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M';
    if (n >= 1e3) return Math.round(n / 1e3) + 'k';
    return String(n);
  }
  function count(n) { return (n ?? 0).toLocaleString('en-US'); }
  function percent(r) { return r == null ? 'no data' : Math.round(r * 100) + '%'; }
  function dollars(m) { return m == null ? 'no data' : '$' + (m / 1e6).toFixed(2); }

  function tile(label, value, note, tone) {
    const box = el('div', 'tile' + (tone ? ' tile-' + tone : ''));
    box.append(el('div', 'tile-label', label), el('div', 'tile-value', value));
    if (note) box.append(el('div', 'tile-note', note));
    return box;
  }

  // The bar fills toward the warning line. The scale grows past it only when the context does.
  function meter(s, warn) {
    const scale = Math.max(s.context, Math.round(warn / 0.75));
    const over = s.context >= warn;
    const wrap = el('div', 'meter' + (over ? ' meter-over' : ''));
    const head = el('div', 'meter-head');
    head.append(el('span', 'meter-label', 'Context'), el('span', 'meter-value', count(s.context) + ' tokens'));
    const track = el('div', 'meter-track');
    const fill = el('div', 'meter-fill');
    fill.style.width = Math.min(100, (s.context / scale) * 100).toFixed(1) + '%';
    const tick = el('div', 'meter-tick');
    tick.style.left = ((warn / scale) * 100).toFixed(1) + '%';
    tick.title = 'Warning line: ' + count(warn) + ' tokens';
    track.append(fill, tick);
    const note = over
      ? 'Warning: above the ' + tokens(warn) + ' line. Every step re-reads all of it. Run /clear when the topic changes.'
      : 'Warning line at ' + tokens(warn) + ' tokens.';
    wrap.append(head, track, el('div', 'meter-foot', note));
    return wrap;
  }

  function facts(pairs) {
    const table = el('table', 'facts');
    for (const [name, value] of pairs) {
      const row = el('tr');
      row.append(el('th', null, name), el('td', null, value));
      table.append(row);
    }
    return table;
  }

  function card(s, warn, isCurrent, wasOpen) {
    const box = el('section', 'card');
    const head = el('header', 'card-head');
    const title = el('div', 'card-title');
    title.append(el('span', 'dot' + (isCurrent ? ' dot-live' : '')), el('span', 'session-id', s.short), el('span', 'folder', s.folder));
    if (isCurrent) title.append(el('span', 'badge', 'last active'));
    const meta = el('div', 'card-meta');
    meta.append(el('span', null, s.model), el('span', null, 'updated ' + s.updatedAgo));
    head.append(title, meta);

    const tiles = el('div', 'tiles');
    tiles.append(
      tile('Cache hit, last request', percent(s.lastHit), 'whole session ' + percent(s.sessionHit)),
      tile('Cache', s.warm ? 'warm ' + s.warmMinutes + ' min' : 'cold', s.ttl ? s.ttl + ' TTL' : 'TTL unknown', s.warm ? 'good' : 'warn'),
      tile('Input cost', dollars(s.costMicros), s.billed != null ? count(s.billed) + ' billed tokens, graft estimate' : 'graft estimate'),
      tile('graft saved', s.graftSaved ? tokens(s.graftSaved) : 'none', s.graftReads + ' graft reads, ' + s.sourceReads + ' source reads'),
      tile('Requests', count(s.requests), s.subagentRequests ? '+ ' + count(s.subagentRequests) + ' by subagents' : 'no subagents'),
    );

    const details = el('details');
    details.dataset.id = s.id;
    details.open = wasOpen;
    details.append(el('summary', null, 'All figures'));
    details.append(facts([
      ['Read from cache', count(s.cacheRead) + ' tokens'],
      ['Written to cache', count(s.cacheWritten) + ' tokens'],
      ['Uncached input', count(s.uncached) + ' tokens'],
      ['Output', count(s.output) + ' tokens'],
      ['Subagent input', count(s.subagentInput) + ' tokens'],
      ['Subagent output', count(s.subagentOutput) + ' tokens'],
      ['Session', s.id],
      ['Usage file', s.file],
    ]));
    const open = el('button', 'link', 'Open the usage file');
    open.addEventListener('click', () => vscode.postMessage({ type: 'open', file: s.file }));
    details.append(open);

    box.append(head, meter(s, warn), tiles, details);
    return box;
  }

  function render(state) {
    // A refresh rebuilds the cards. Keep the tables the reader opened open.
    const opened = new Set(Array.from(root.querySelectorAll('details[open]'), (d) => d.dataset.id));
    root.replaceChildren();
    const list = state.sessions || [];
    summary.textContent = list.length === 1 ? '1 live session' : list.length + ' live sessions';
    empty.hidden = list.length > 0;
    list.forEach((s, i) => root.append(card(s, state.warnTokens, i === 0, opened.has(s.id))));
  }

  window.addEventListener('message', (event) => {
    if (event.data && event.data.type === 'sessions') {
      vscode.setState(event.data);
      render(event.data);
    }
  });
  const saved = vscode.getState();
  if (saved) render(saved);
</script>
</body>
</html>`;
}

function activate(context) {
  const item = vscode.window.createStatusBarItem('claudeUsageBar.session', vscode.StatusBarAlignment.Right, 100);
  item.name = 'Claude session usage';
  item.command = DETAILS_COMMAND;
  const refresh = () => render(item);
  const usageWatcher = vscode.workspace.createFileSystemWatcher('**/.claude/usage/*.json');
  const graftWatcher = vscode.workspace.createFileSystemWatcher('**/graft/.cache/session/*.json');
  for (const watcher of [usageWatcher, graftWatcher]) {
    watcher.onDidChange(refresh);
    watcher.onDidCreate(refresh);
    watcher.onDidDelete(refresh);
  }
  // The timer moves the cache countdown and the "updated" ages between two hook runs.
  const timer = setInterval(refresh, TICK_MS);
  context.subscriptions.push(
    item,
    usageWatcher,
    graftWatcher,
    vscode.commands.registerCommand(DETAILS_COMMAND, () => showPanel(context)),
    vscode.workspace.onDidChangeWorkspaceFolders(refresh),
    vscode.workspace.onDidChangeConfiguration((e) => {
      if (e.affectsConfiguration('claudeUsageBar')) refresh();
    }),
    { dispose: () => clearInterval(timer) },
  );
  refresh();
}

function deactivate() {}

module.exports = { activate, deactivate };
