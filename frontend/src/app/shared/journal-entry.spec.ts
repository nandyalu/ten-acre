import { parseJournalEntry, summariseEntry, summariseEvents } from './journal-entry';

/** A real day, as `/api/agent/journey/entries?month=` served it on
 * 2026-09-24. The preamble at the top is written for a whole document and
 * repeats what the day's own lines say. */
const BUSY = `#

2026-09-24 to 2026-09-24 — 1 days, 1 position(s) opened.
Research cost $0.05 over that time.
The book stands at $9,987.96.

## 2026-09-24
Book $9,987.96 (-0.1% against the starting balance).
- Bought 10 COIN at $195.58
- Spent $0.05 on research.

> The market is closed for the day. COIN position is held with stop resting at $182.29.

**First position opened: COIN.**
`;

const QUIET = `#

2026-09-23 to 2026-09-23 — 1 days, 0 position(s) opened.
The book stands at $9,944.15.

**2026-09-23** — nothing bought or sold.
`;

describe('parseJournalEntry', () => {
  it('splits a busy day into its book line, events, quotation and milestone', () => {
    const blocks = parseJournalEntry(BUSY);

    expect(blocks.map((b) => b.kind)).toEqual(['book', 'events', 'quote', 'milestone']);
  });

  it('reads the equity and the return off the book line', () => {
    const [book] = parseJournalEntry(BUSY);

    expect(book).toEqual({ kind: 'book', equity: '9,987.96', changePct: -0.1 });
  });

  it('lists each event without its list marker', () => {
    const events = parseJournalEntry(BUSY)[1];

    expect(events).toEqual({
      kind: 'events',
      items: ['Bought 10 COIN at $195.58', 'Spent $0.05 on research.'],
    });
  });

  it("quotes the agent's own words without the > marker", () => {
    const quote = parseJournalEntry(BUSY)[2];

    expect(quote.kind).toBe('quote');
    expect((quote as { text: string }).text).toMatch(/^The market is closed/);
  });

  it('strips the asterisks off a milestone', () => {
    const milestone = parseJournalEntry(BUSY)[3];

    expect(milestone).toEqual({ kind: 'milestone', text: 'First position opened: COIN.' });
  });

  it('drops the document preamble, whose date range is meaningless for one day', () => {
    const text = JSON.stringify(parseJournalEntry(BUSY));

    expect(text).not.toContain('1 days');
    expect(text).not.toContain('over that time');
    expect(text).not.toContain('##');
  });

  it('keeps the equity of a quiet day, which the preamble is the only place it appears', () => {
    const blocks = parseJournalEntry(QUIET);

    expect(blocks).toEqual([
      { kind: 'book', equity: '9,944.15', changePct: null },
      { kind: 'quiet', text: 'Nothing bought or sold.' },
    ]);
  });

  it('continues a quotation onto the lines that follow it without a blank', () => {
    const [quote] = parseJournalEntry('> First line.\nSecond line.');

    expect(quote).toEqual({ kind: 'quote', text: 'First line.\nSecond line.' });
  });

  it('passes a line it does not recognise through as a paragraph', () => {
    // Degrades safely: a shape nobody predicted still reaches the reader.
    expect(parseJournalEntry('Nothing has happened yet.')).toEqual([
      { kind: 'text', text: 'Nothing has happened yet.' },
    ]);
  });
});

describe('summariseEvents', () => {
  it('cuts each event to a verb and a ticker, once each', () => {
    expect(
      summariseEvents([
        'Bought 10 COIN at $195.58',
        'Bought 7 COIN at $205.44',
        'Sold 4 INTC at $97.00 — lost $4.28 over 0 day(s)',
        'Spent $0.05 on research.',
        'Did not run: the agent is switched off',
      ]),
    ).toBe('bought COIN, sold INTC, research, did not run');
  });

  it('keeps the first words of an event it does not recognise', () => {
    expect(summariseEvents(['Something else entirely happened here'])).toBe(
      'something else entirely',
    );
  });
});

describe('summariseEntry', () => {
  it('reads a busy day as its book and its events', () => {
    expect(summariseEntry(parseJournalEntry(BUSY))).toBe(
      '$9,987.96 −0.1% · bought COIN, research · a milestone',
    );
  });

  it('reads a quiet day as its book and the quiet line', () => {
    expect(summariseEntry(parseJournalEntry(QUIET))).toBe('$9,944.15 · nothing bought or sold');
  });
});
