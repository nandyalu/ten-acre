#!/usr/bin/env node
// Status line for a terminal session: graft's lines first, then one line of token, prompt cache and cost figures.
// The VS Code panel does not run a statusLine command. The extension in .claude/vscode-usage-bar/ shows the same figures there.
const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');

const GRAY = '\x1b[38;5;244m';
const TEXT = '\x1b[38;5;251m';
const WARN = '\x1b[38;5;214m';
const RESET = '\x1b[0m';
const SEP = `${GRAY} · ${RESET}`;

function tokens(n) {
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `${Math.round(n / 1e3)}k`;
  return String(n);
}

// graft's own first line already shows the tokens that graft saved, so this line does not repeat them.
function usageLine(input) {
  const parts = [];
  const current = input.context_window?.current_usage;
  if (current) {
    const context = (current.input_tokens ?? 0) + (current.cache_read_input_tokens ?? 0) + (current.cache_creation_input_tokens ?? 0);
    parts.push(`${TEXT}${tokens(context)} tokens${RESET}`);
  }
  const cache = input.prompt_cache;
  if (cache?.caching_observed) {
    if (cache.hit_ratio != null) parts.push(`${TEXT}cache ${Math.round(cache.hit_ratio * 100)}% hit${RESET}`);
    // Claude Code sends `warm` only when an event runs the script, so compare the expiry with the clock again.
    const msLeft = cache.expires_at ? cache.expires_at * 1000 - Date.now() : 0;
    if (cache.warm && msLeft > 0) {
      parts.push(`${TEXT}warm ${Math.ceil(msLeft / 60000)}m${RESET}`);
    } else {
      const rewrite = cache.recache_tokens_if_cold ? `, next turn writes ${tokens(cache.recache_tokens_if_cold)}` : '';
      parts.push(`${WARN}cold${rewrite}${RESET}`);
    }
    if (cache.misses) {
      const cause = cache.last_miss_cause?.causes?.[0];
      parts.push(`${WARN}${cache.misses} ${cache.misses === 1 ? 'miss' : 'misses'}${cause ? ` (${cause})` : ''}${RESET}`);
    }
  }
  const cost = input.cost?.total_cost_usd;
  if (cost != null) parts.push(`${TEXT}~$${cost.toFixed(2)}${RESET}`);
  const fiveHour = input.rate_limits?.five_hour?.used_percentage;
  if (fiveHour != null) parts.push(`${TEXT}5h ${Math.round(fiveHour)}%${RESET}`);
  return parts.length ? `${GRAY}⛁ ${RESET}${parts.join(SEP)}` : '';
}

const raw = fs.readFileSync(0, 'utf8');
let input = {};
try {
  input = JSON.parse(raw);
} catch {
  /* print graft's lines only */
}
const lines = [];
const graft = spawnSync(process.execPath, [path.join(__dirname, 'graft-statusline.cjs')], { input: raw, encoding: 'utf8', timeout: 3000 });
if (graft.stdout?.trim()) lines.push(graft.stdout.replace(/\n+$/, ''));
const usage = usageLine(input);
if (usage) lines.push(usage);
process.stdout.write(lines.join('\n'));
