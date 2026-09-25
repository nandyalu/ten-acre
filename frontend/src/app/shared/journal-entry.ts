/**
 * One journal day, split into the parts it is written in.
 *
 * The API sends each day as the Markdown `journey.to_markdown()` renders
 * for it, and the page was printing that raw inside a `<pre>`: a reader saw
 * `- ` before every event, `> ` before the agent's own words, asterisks
 * around a milestone, and a three-line preamble ("2026-09-23 to 2026-09-23
 * — 1 days") written for a whole document and meaningless for one day.
 *
 * **Parsed rather than rendered as Markdown**, for the same reason as
 * `rationale.ts`: the shape is fixed and known. A day is a book line, a list
 * of events, the agent's reasoning as a quotation, and any milestone; a quiet
 * day is one bold line. Pulling those out is smaller than a Markdown
 * renderer and degrades safely — a line nobody predicted comes through as a
 * plain paragraph rather than disappearing.
 */
export type JournalBlock =
  /** "Book $9,987.96 (-0.1% against the starting balance)." */
  | { kind: 'book'; equity: string; changePct: number | null }
  /** "- Bought 10 COIN at $195.58", one item per line. */
  | { kind: 'events'; items: string[] }
  /** "> The market is closed for the day…" — the agent's own words. */
  | { kind: 'quote'; text: string }
  /** "**First position opened: COIN.**" */
  | { kind: 'milestone'; text: string }
  /** "**2026-09-23** — nothing bought or sold." */
  | { kind: 'quiet'; text: string }
  | { kind: 'text'; text: string };

const BOOK =
  /^Book \$([\d,]+\.\d{2})(?: \(([+-]?\d+(?:\.\d+)?)% against the starting balance\))?\.$/;

/** The document preamble's closing figure. Kept, because on a quiet day it is
 * the only place the day's equity appears. */
const STANDS_AT = /^The book stands at \$([\d,]+\.\d{2})\.$/;

/** The rest of the preamble: a date range and a research total that repeat
 * what the day's own lines already say. */
const PREAMBLE = [
  /^\d{4}-\d{2}-\d{2} to \d{4}-\d{2}-\d{2} — /,
  /^Research cost \$[\d,.]+ over that time\.$/,
];

const QUIET = /^\*\*\d{4}-\d{2}-\d{2}(?: to \d{4}-\d{2}-\d{2})?\*\* — (.+)$/;
const BOLD_LINE = /^\*\*(.+)\*\*$/;
const INLINE_BOLD = /\*\*([^*]+)\*\*/g;

export function parseJournalEntry(markdown: string): JournalBlock[] {
  const blocks: JournalBlock[] = [];
  let standsAt: string | null = null;
  let events: string[] = [];
  let quote: string[] = [];

  const flush = (): void => {
    if (events.length) {
      blocks.push({ kind: 'events', items: events });
      events = [];
    }
    if (quote.length) {
      blocks.push({ kind: 'quote', text: quote.join('\n') });
      quote = [];
    }
  };

  for (const raw of markdown.split('\n')) {
    const line = raw.trim();
    if (!line) {
      flush();
      continue;
    }
    if (line.startsWith('#') || PREAMBLE.some((p) => p.test(line))) continue;

    const stands = line.match(STANDS_AT);
    if (stands) {
      standsAt = stands[1];
      continue;
    }
    if (line.startsWith('- ')) {
      if (quote.length) flush();
      events.push(line.slice(2).replace(INLINE_BOLD, '$1'));
      continue;
    }
    if (line.startsWith('>')) {
      if (events.length) flush();
      quote.push(line.replace(/^>\s?/, ''));
      continue;
    }
    // A line straight after a quotation, with no blank between, continues
    // it: the reasoning is written with one `>` and may run to several lines.
    if (quote.length) {
      quote.push(line);
      continue;
    }
    flush();

    const book = line.match(BOOK);
    if (book) {
      blocks.push({
        kind: 'book',
        equity: book[1],
        changePct: book[2] === undefined ? null : Number(book[2]),
      });
      continue;
    }
    const quiet = line.match(QUIET);
    if (quiet) {
      blocks.push({ kind: 'quiet', text: sentence(quiet[1]) });
      continue;
    }
    const bold = line.match(BOLD_LINE);
    if (bold) {
      blocks.push({ kind: 'milestone', text: bold[1] });
      continue;
    }
    blocks.push({ kind: 'text', text: line.replace(INLINE_BOLD, '$1') });
  }
  flush();

  if (standsAt !== null && !blocks.some((b) => b.kind === 'book')) {
    blocks.unshift({ kind: 'book', equity: standsAt, changePct: null });
  }
  return blocks;
}

/** "nothing bought or sold." → "Nothing bought or sold." Only the first
 * letter, so a ticker later in the line keeps its case. */
function sentence(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** The shapes `journey.py` writes an event in, each cut to the two words a
 * closed day's row has room for. */
const EVENT_SHAPES: [RegExp, (m: RegExpMatchArray) => string][] = [
  [/^Bought \S+ (\S+)/, (m) => `bought ${m[1]}`],
  [/^Sold \S+ (\S+)/, (m) => `sold ${m[1]}`],
  [/^Spent \$[\d,.]+ on research/, () => 'research'],
  [/^Did not run/, () => 'did not run'],
];

/** A day's events in a few words: "bought COIN, research". Each distinct
 * action once, so a day that bought the same name twice says it once. */
export function summariseEvents(items: string[]): string {
  const words = items.map((item) => {
    for (const [shape, short] of EVENT_SHAPES) {
      const match = item.match(shape);
      if (match) return short(match);
    }
    return item.split(/\s+/).slice(0, 3).join(' ').toLowerCase();
  });
  return Array.from(new Set(words)).join(', ');
}

/** One line for a closed day's row: where the book stood, and what happened.
 * Empty for a day the parser found nothing in. */
export function summariseEntry(blocks: JournalBlock[]): string {
  const parts: string[] = [];
  let milestones = 0;
  for (const block of blocks) {
    switch (block.kind) {
      case 'book': {
        const change =
          block.changePct === null
            ? ''
            : ` ${block.changePct >= 0 ? '+' : '−'}${Math.abs(block.changePct).toFixed(1)}%`;
        parts.push(`$${block.equity}${change}`);
        break;
      }
      case 'events':
        parts.push(summariseEvents(block.items));
        break;
      case 'quiet':
        parts.push(block.text.charAt(0).toLowerCase() + block.text.slice(1).replace(/\.$/, ''));
        break;
      case 'milestone':
        // Counted, not listed: the first trade's day carries three at once.
        milestones += 1;
        break;
      default:
        break;
    }
  }
  if (milestones) parts.push(milestones === 1 ? 'a milestone' : `${milestones} milestones`);
  return parts.filter(Boolean).join(' · ');
}
