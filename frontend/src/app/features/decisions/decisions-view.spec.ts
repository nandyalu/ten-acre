import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import { AgentEvent, AgentReflection } from '../../core/models/api.models';
import { AgentService } from '../../core/services/agent.service';
import { DecisionsView } from './decisions-view';

/** A pass from 2026-09-01, the first day prompts were kept. */
function event(over: Partial<AgentEvent> = {}): AgentEvent {
  return {
    id: 1,
    // The API stamps the offset. Without it a browser reads the instant as
    // local time, which is the bug `readerDateTime` was written to fix.
    ran_at: '2026-09-01T13:35:00Z',
    next_wakeup: null,
    reasoning: 'Reducing overhead as cash is negative.',
    skipped: null,
    equity: 9999.4,
    cash: -8,
    research_spent: 0.6,
    prompt: 'You manage a $10,000 account…',
    response: '{"reasoning": "…", "orders": []}',
    thinking: null,
    prompt_tokens: null,
    completion_tokens: null,
    seconds: null,
    orders: [{ side: 'untrack', ticker: 'CRM', quantity: 0, reason: 'No shares held.' }],
    refused: [],
    failed: [],
    ...over,
  };
}

/** An evening review on the same day as `event()`, a few hours after it,
 * and inside its UTC month. It ran and reported one gap. */
function review(over: Partial<AgentReflection> = {}): AgentReflection {
  return {
    id: 7,
    ran_at: '2026-09-01T20:30:00Z',
    since: '2026-08-31T20:30:00Z',
    passes: 3,
    notes: [
      {
        kind: 'missing_information',
        pass_id: 1,
        what_was_missing: 'I could not see the sector of any holding.',
        what_you_would_have_done: 'trimmed the second chip name.',
      },
    ],
    wakeup_note: 'Watch INTC at the open.',
    memory_changes: [{ action: 'add', text: 'Two chip names is one too many.' }],
    applied: ['Memory: added "Two chip names is one too many."'],
    thinking: 'the review thinking',
    prompt: 'the review prompt, every pass of the day',
    turn2_prompt: 'the second prompt, asking for the revision',
    response: '{"notes": [...]}',
    revision: '{"wakeup_note": "Watch INTC at the open."}',
    model: 'gemini',
    channel: 'tool',
    prompt_tokens: 4000,
    completion_tokens: 300,
    seconds: 12.5,
    skipped: null,
    ...over,
  };
}

/** The newest month is expanded (and fetched) as soon as the page loads —
 * every test seeds exactly that one month, since that is what a reader sees
 * without clicking anything. A separate spec covers opening an older one. */
class AgentServiceStub {
  months: string[] = ['2026-09'];
  eventsByMonth: Record<string, AgentEvent[]> = { '2026-09': [] };
  reviews: AgentReflection[] = [];
  failMonths = false;
  failReviews = false;

  async getEventMonths(): Promise<string[]> {
    if (this.failMonths) throw new Error('network error');
    return this.months;
  }

  async getEventsForMonth(month: string): Promise<AgentEvent[]> {
    return this.eventsByMonth[month] ?? [];
  }

  async getReflections(): Promise<AgentReflection[]> {
    if (this.failReviews) throw new Error('network error');
    return this.reviews;
  }
}

describe('DecisionsView', () => {
  let service: AgentServiceStub;

  beforeEach(async () => {
    service = new AgentServiceStub();
    await TestBed.configureTestingModule({
      imports: [DecisionsView],
      providers: [{ provide: AgentService, useValue: service }, provideRouter([])],
    }).compileComponents();
  });

  /** The component clears its loading signals in `.finally()`s, so the
   * skeleton is still on screen until every chained promise the stub returns
   * has settled — `whenStable()` drains that whole chain, nested calls
   * included, in one wait. */
  async function render(): Promise<HTMLElement> {
    const fixture = TestBed.createComponent(DecisionsView);
    await fixture.whenStable();
    return fixture.nativeElement as HTMLElement;
  }

  function clickButtonContaining(el: HTMLElement, text: string): void {
    const button = Array.from(el.querySelectorAll('button')).find((b) =>
      b.textContent?.includes(text),
    );
    button?.click();
  }

  it('hides the transcript until it is asked for', async () => {
    // A prompt runs to tens of kilobytes. A feed that opens with one is a feed
    // nobody scrolls.
    service.eventsByMonth['2026-09'] = [event()];

    const el = await render();

    expect(el.querySelector('.markdown')).toBeNull();
    expect(el.querySelector('.verbatim')).toBeNull();
    expect(el.textContent).toContain('Show the transcript');
  });

  it('opens the prompt, rendered, beside the answer, verbatim', async () => {
    /** One disclosure since 2026-09-25. It was three — the prompt, the
     * answer and the thinking — and the answer panel repeated the JSON the
     * turn's own reasoning and orders are read from. The prompt is Markdown
     * and renders; the answer is JSON and stays as it is. */
    service.eventsByMonth['2026-09'] = [event()];
    const fixture = TestBed.createComponent(DecisionsView);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    clickButtonContaining(el, 'Show the transcript');
    await fixture.whenStable();

    const cols = el.querySelector('.transcript-cols');
    expect(cols?.querySelector('.markdown')?.textContent).toContain('You manage a $10,000 account');
    expect(cols?.querySelector('.verbatim')?.textContent).toContain('"orders": []');
    expect(el.textContent).not.toContain('Show the answer');
    expect(el.textContent).not.toContain('Show the thinking');
  });

  it('keeps the copy button on the raw text, not the rendering', async () => {
    service.eventsByMonth['2026-09'] = [event({ prompt: '## A heading\n\n**bold**' })];
    const fixture = TestBed.createComponent(DecisionsView);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    clickButtonContaining(el, 'Show the transcript');
    await fixture.whenStable();

    expect(el.querySelector('.markdown h2')?.textContent).toBe('A heading');
    const copy = el.querySelector('.transcript-col app-copy-button button');
    expect(copy?.getAttribute('aria-label')).toBe("Copy this turn's prompt");
  });

  it('lists what the pass actually did', async () => {
    service.eventsByMonth['2026-09'] = [event()];

    const el = await render();

    expect(el.textContent).toContain('untrack');
    expect(el.textContent).toContain('CRM');
  });

  it('shows a refusal with its reason', async () => {
    // The interesting half. "1 rejected" says nothing; the reason is a finding.
    service.eventsByMonth['2026-09'] = [
      event({
        orders: [],
        refused: [
          {
            ticker: 'NVDA',
            side: 'buy',
            quantity: 2,
            reason: null,
            why: 'costs more than the cash left',
          },
        ],
      }),
    ];

    expect((await render()).textContent).toContain('costs more than the cash left');
  });

  it('says a pass kept no prompt rather than showing an empty panel', async () => {
    // Every pass before 2026-09-01. The prompt cannot be reconstructed, and a
    // blank panel would read as a bug.
    service.eventsByMonth['2026-09'] = [event({ prompt: null, response: null })];

    const el = await render();

    expect(el.textContent).toContain('kept no prompt');
    expect(el.textContent).not.toContain('Show the transcript');
  });

  it('reports a pass that did nothing as nothing, not as blank', async () => {
    service.eventsByMonth['2026-09'] = [event({ orders: [], refused: [] })];

    expect((await render()).textContent).toContain('nothing');
  });

  it('says why a pass was skipped', async () => {
    service.eventsByMonth['2026-09'] = [event({ skipped: 'the market was shut', orders: [] })];

    expect((await render()).textContent).toContain('the market was shut');
  });

  it('says the feed is empty rather than rendering nothing at all', async () => {
    service.months = [];

    expect((await render()).textContent).toContain('No decision passes recorded yet');
  });

  it('says the decision record could not be read when the month list itself fails', async () => {
    service.failMonths = true;

    expect((await render()).textContent).toContain('could not be read');
  });

  it('links to the notes page, so a note is not only found by scrolling the timeline', async () => {
    const el = await render();
    const link = Array.from(el.querySelectorAll('a')).find((a) =>
      a.textContent?.includes('every note'),
    );
    expect(link?.getAttribute('href')).toBe('/decisions/notes');
  });

  it('shows a note apart from the orders, and not as an order', async () => {
    // A note has no ticker and no quantity. Rendered in the orders list it
    // would read as a trade in a stock called "".
    service.eventsByMonth['2026-09'] = [
      event({
        orders: [
          { side: 'buy', ticker: 'AAPL', quantity: 2, reason: 'cheap' },
          { side: 'note', ticker: '', quantity: 0, reason: 'I cannot see sector data.' },
        ],
        refused: [],
        failed: [],
      }),
    ];

    const el = await render();

    expect(el.querySelector('.agent-note')?.textContent).toContain('I cannot see sector data.');
    expect(el.querySelector('.orders')?.textContent).not.toContain('I cannot see sector data.');
    expect(el.querySelector('.orders')?.textContent).toContain('AAPL');
  });

  it('links every ticker to its own page', async () => {
    // The ticker is the one word on a decision row a reader wants to follow:
    // the chart, the analyses and the lots behind the order are all there.
    service.eventsByMonth['2026-09'] = [
      event({
        orders: [{ side: 'buy', ticker: 'AAPL', quantity: 2, reason: 'cheap' }],
        refused: [{ side: 'buy', ticker: 'MSFT', quantity: 9, reason: null, why: 'no cash' }],
        failed: [],
      }),
    ];

    const el = await render();

    const hrefs = Array.from(el.querySelectorAll('.orders a')).map((a) => a.getAttribute('href'));
    expect(hrefs).toContain('/research/ticker/AAPL');
    expect(hrefs).toContain('/research/ticker/MSFT');
  });

  it('says whether an order named a price, and what an adjust moved', async () => {
    /** 2026-09-20: `order_type`, `limit_price`, `time_in_force`, `stop` and
     * `target` were parsed, acted on and dropped before anything wrote them
     * down, so every buy read as a market order and an adjust said only which
     * ticker it touched. */
    service.eventsByMonth['2026-09'] = [
      event({
        orders: [
          {
            side: 'buy',
            ticker: 'AAPL',
            quantity: 5,
            reason: 'waiting for my price',
            order_type: 'limit',
            limit_price: 330,
            time_in_force: 'gtc',
          },
          {
            side: 'adjust',
            ticker: 'NVDA',
            quantity: 0,
            reason: 'NVDA: moved stop from $330.00 to $334.16.',
            stop: 334.16,
          },
          { side: 'sell', ticker: 'MSFT', quantity: 1, reason: 'out', order_type: 'market' },
        ],
        refused: [],
        failed: [],
      }),
    ];

    const rows = Array.from((await render()).querySelectorAll('.orders li'));

    expect(rows[0]?.textContent).toContain('limit $330.00');
    expect(rows[0]?.textContent).toContain('stays past today');
    expect(rows[1]?.textContent).toContain('stop to $334.16');
    // The outcome line is what knows the level it moved from — an order says
    // only where the agent wanted it.
    expect(rows[1]?.textContent).toContain('moved stop from $330.00 to $334.16');
    expect(rows[2]?.textContent).toContain('market order');
    expect(rows[2]?.textContent).not.toContain('expires');
  });

  it('says nothing about the kind of order on a pass recorded before it was kept', async () => {
    // A null `order_type` means "not recorded", never "market". Printing
    // "market order" here would invent a decision the agent never stated.
    service.eventsByMonth['2026-09'] = [
      event({
        orders: [{ side: 'buy', ticker: 'AAPL', quantity: 2, reason: 'cheap' }],
        refused: [],
        failed: [],
      }),
    ];

    const row = (await render()).querySelector('.orders li');

    expect(row?.textContent).toContain('AAPL');
    expect(row?.textContent).not.toContain('market order');
  });

  it('tells a broker failure apart from a refusal', async () => {
    // The two mean different things and the page has to say so: one is the
    // agent's arithmetic being wrong, the other is the world declining an
    // order it formed correctly.
    service.eventsByMonth['2026-09'] = [
      event({
        orders: [],
        refused: [
          { side: 'buy', ticker: 'MSFT', quantity: 9, reason: null, why: 'not enough cash' },
        ],
        failed: [
          { side: 'buy', ticker: 'NVDA', quantity: 1, reason: null, why: 'unsettled funds' },
        ],
      }),
    ];

    const text = (await render()).textContent ?? '';

    expect(text).toContain('not enough cash');
    expect(text).toContain('unsettled funds');
    expect(text).toContain('broker said no');
  });

  it('shows the pass time on the reader clock, with that zone named', async () => {
    service.eventsByMonth['2026-09'] = [event()];
    const el = await render();

    const head = el.querySelector('.card-head strong')?.textContent ?? '';
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    const instant = new Date('2026-09-01T13:35:00Z');

    const time = new Intl.DateTimeFormat('en-US', {
      hour: 'numeric',
      minute: '2-digit',
      timeZone: zone,
    }).format(instant);
    const label =
      new Intl.DateTimeFormat('en-US', { timeZone: zone, timeZoneName: 'short' })
        .formatToParts(instant)
        .find((p) => p.type === 'timeZoneName')?.value ?? '';

    expect(head).toContain(time);
    expect(head).toContain(label);
  });

  it('names the day of the next wakeup when it is not the day of the pass', async () => {
    /** A 7:55 PM pass that asked for "9:30 AM" read as the morning already
     * gone (2026-09-17). The agent may ask for up to four days ahead, so the
     * day is part of the answer whenever it differs. */
    service.eventsByMonth['2026-09'] = [
      event({ ran_at: '2026-09-17T23:55:00Z', next_wakeup: '2026-09-18T13:30:00Z' }),
    ];
    const el = await render();
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    const weekday = new Intl.DateTimeFormat('en-US', { weekday: 'short', timeZone: zone }).format(
      new Date('2026-09-18T13:30:00Z'),
    );

    const head = el.querySelector('.card-head')?.textContent ?? '';
    expect(head).toContain(`asked to be woken at ${weekday}`);
  });

  it('shows only the time of the next wakeup when it is the same day', async () => {
    // Fifteen minutes apart, so the two land on one calendar day in any zone.
    service.eventsByMonth['2026-09'] = [
      event({ ran_at: '2026-09-17T13:35:00Z', next_wakeup: '2026-09-17T13:50:00Z' }),
    ];
    const el = await render();
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    const wakeup = new Date('2026-09-17T13:50:00Z');
    const time = new Intl.DateTimeFormat('en-US', {
      hour: 'numeric',
      minute: '2-digit',
      timeZone: zone,
    }).format(wakeup);
    const weekday = new Intl.DateTimeFormat('en-US', { weekday: 'short', timeZone: zone }).format(
      wakeup,
    );

    const head = el.querySelector('.card-head')?.textContent ?? '';
    expect(head).toContain(`asked to be woken at ${time}`);
    expect(head).not.toContain(`asked to be woken at ${weekday}`);
  });

  // --- the month timeline itself ---------------------------------------

  it('draws one dot per month and expands only the newest one', async () => {
    service.months = ['2026-09', '2026-08'];
    service.eventsByMonth = {
      '2026-09': [event({ id: 1, reasoning: 'september pass' })],
      '2026-08': [event({ id: 2, reasoning: 'august pass' })],
    };

    const el = await render();

    // 2 months + the 1 day inside September, the only expanded month.
    expect(el.querySelectorAll('.tl').length).toBe(3);
    expect(el.textContent).toContain('september pass');
    expect(el.textContent).not.toContain('august pass');
  });

  it('fetches and shows an older month only once its dot is clicked', async () => {
    service.months = ['2026-09', '2026-08'];
    service.eventsByMonth = {
      '2026-09': [event({ id: 1, reasoning: 'september pass' })],
      '2026-08': [event({ id: 2, reasoning: 'august pass' })],
    };
    const fixture = TestBed.createComponent(DecisionsView);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.textContent).not.toContain('august pass');

    clickButtonContaining(el, 'August 2026');
    await fixture.whenStable();

    expect(el.textContent).toContain('august pass');
  });

  it('groups two passes on the same calendar day under one day header', async () => {
    service.eventsByMonth['2026-09'] = [
      event({ id: 1, ran_at: '2026-09-08T20:00:00Z', reasoning: 'evening pass' }),
      event({ id: 2, ran_at: '2026-09-08T13:35:00Z', reasoning: 'afternoon pass' }),
    ];

    const el = await render();

    expect(el.querySelectorAll('.tl-day').length).toBe(1);
    expect(el.textContent).toContain('evening pass');
    expect(el.textContent).toContain('afternoon pass');
  });

  it('gives two passes on different days their own headers', async () => {
    service.eventsByMonth['2026-09'] = [
      event({ id: 1, ran_at: '2026-09-08T13:35:00Z' }),
      event({ id: 2, ran_at: '2026-09-01T13:35:00Z' }),
    ];

    const el = await render();

    expect(el.querySelectorAll('.tl-day').length).toBe(2);
  });

  it('shows a day expanded by default, as soon as its month opens', async () => {
    service.eventsByMonth['2026-09'] = [event({ reasoning: 'september pass' })];

    const el = await render();

    expect(el.textContent).toContain('september pass');
  });

  it('opens only the newest day by default when a month has more than one', async () => {
    service.eventsByMonth['2026-09'] = [
      event({ id: 1, ran_at: '2026-09-08T13:35:00Z', reasoning: 'newest day pass' }),
      event({ id: 2, ran_at: '2026-09-01T13:35:00Z', reasoning: 'older day pass' }),
    ];

    const el = await render();

    expect(el.textContent).toContain('newest day pass');
    expect(el.textContent).not.toContain('older day pass');
  });

  it('opening the collapsed older day reveals its cards', async () => {
    service.eventsByMonth['2026-09'] = [
      event({ id: 1, ran_at: '2026-09-08T13:35:00Z' }),
      event({ id: 2, ran_at: '2026-09-01T13:35:00Z', reasoning: 'older day pass' }),
    ];
    const fixture = TestBed.createComponent(DecisionsView);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.textContent).not.toContain('older day pass');

    clickButtonContaining(el, 'Tuesday 1 September');
    await fixture.whenStable();

    expect(el.textContent).toContain('older day pass');
  });

  it('collapses and reopens a day on click, independently of its month', async () => {
    service.eventsByMonth['2026-09'] = [event({ reasoning: 'september pass' })];
    const fixture = TestBed.createComponent(DecisionsView);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.textContent).toContain('september pass');

    clickButtonContaining(el, 'Tuesday 1 September');
    await fixture.whenStable();
    expect(el.textContent).not.toContain('september pass');

    clickButtonContaining(el, 'Tuesday 1 September');
    await fixture.whenStable();
    expect(el.textContent).toContain('september pass');
  });

  it('collapses an expanded month back on a second click, without losing the cached events', async () => {
    service.months = ['2026-09', '2026-08'];
    service.eventsByMonth = { '2026-09': [], '2026-08': [event({ reasoning: 'august pass' })] };
    const fixture = TestBed.createComponent(DecisionsView);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    clickButtonContaining(el, 'August 2026');
    await fixture.whenStable();
    expect(el.textContent).toContain('august pass');

    clickButtonContaining(el, 'August 2026');
    await fixture.whenStable();
    expect(el.textContent).not.toContain('august pass');

    clickButtonContaining(el, 'August 2026');
    await fixture.whenStable();
    expect(el.textContent).toContain('august pass');
  });

  it('shows a one-turn pass as turn 1, with what it fetched before it decided', async () => {
    /** 2026-09-20: the turn blocks needed two turns to appear, so a pass that
     * fetched an analysis and then bought on it showed the buy with no sign of
     * the fetch behind it — and the same pass was drawn two different ways
     * depending on a count no reader could see. */
    service.eventsByMonth['2026-09'] = [
      event({
        orders: [{ side: 'buy', ticker: 'AAPL', quantity: 2, reason: 'the read convinced me' }],
        refused: [],
        failed: [],
        turns: [
          {
            prompt: 'the only prompt',
            response: '{"reasoning":"buying","orders":[]}',
            thinking: null,
            reasoning: 'The analysis holds up.',
            orders: [{ side: 'buy', ticker: 'AAPL', quantity: 2, reason: 'the read convinced me' }],
            exchanges: [{ name: 'read', args: { ticker: 'AAPL' }, result: 'AAPL said Buy' }],
          },
        ],
      }),
    ];

    const el = await render();

    const turns = Array.from(el.querySelectorAll('.turn'));
    expect(turns).toHaveLength(1);
    expect(turns[0]?.textContent).toContain('Turn 1 of 1');
    expect(turns[0]?.textContent).toContain('fetched read');
    expect(turns[0]?.textContent).toContain('The analysis holds up.');
    expect(turns[0]?.textContent).toContain('AAPL');
  });

  it('keeps the flat layout for a pass that kept no turn at all', async () => {
    // A pass from before 2026-09-10, and a single-turn pass that fetched
    // nothing, store no turn — `_turns_worth_keeping` does not keep one. The
    // pass's own summary is all there is, and it still has to render.
    service.eventsByMonth['2026-09'] = [
      event({
        reasoning: 'Nothing worth doing.',
        orders: [{ side: 'untrack', ticker: 'CRM', quantity: 0, reason: 'No shares held.' }],
        refused: [],
        failed: [],
      }),
    ];

    const el = await render();

    expect(el.querySelector('.turn')).toBeNull();
    expect(el.textContent).toContain('Nothing worth doing.');
    expect(el.querySelector('.orders')?.textContent).toContain('CRM');
  });

  describe('a pass that took more than one turn', () => {
    /** Before 2026-09-10 only the last turn was kept: a refusal retry rebuilt
     * the prompt and overwrote the first, so a two-turn pass was published as
     * though it were one — against a site whose claim is that every prompt the
     * agent saw is on the record, word for word. */
    const twoTurns = () =>
      event({
        turns: [
          {
            prompt: 'the first prompt',
            response: '{"orders":[{"side":"read","ticker":"INTC"}]}',
            thinking: null,
          },
          {
            prompt: 'the second prompt, carrying the analysis',
            response: '{"orders":[]}',
            thinking: null,
          },
        ],
      });

    it('shows every turn, not only the last', async () => {
      service.eventsByMonth['2026-09'] = [twoTurns()];
      const fixture = TestBed.createComponent(DecisionsView);
      await fixture.whenStable();
      const el = fixture.nativeElement as HTMLElement;

      clickButtonContaining(el, 'Show the transcript');
      await fixture.whenStable();

      expect(el.textContent).toContain('the first prompt');
      expect(el.textContent).toContain('the second prompt, carrying the analysis');
      expect(el.textContent).toContain('2 turns');
    });

    it('keeps a turn’s orders inside that turn', async () => {
      /** 2026-09-20: the turns were a flat run of labels and lists, and
       * `.orders` has a wider top margin than `.turn-label` — so a turn's
       * orders sat nearer the NEXT turn's label than their own and read as
       * though they belonged to it. */
      service.eventsByMonth['2026-09'] = [
        event({
          turns: [
            {
              prompt: 'the first prompt',
              response: '{"orders":[]}',
              thinking: null,
              reasoning: 'Buying the dip.',
              orders: [{ side: 'buy', ticker: 'AAPL', quantity: 2, reason: 'cheap' }],
            },
            {
              prompt: 'the second prompt',
              response: '{"orders":[]}',
              thinking: null,
              reasoning: 'Nothing more to do.',
              orders: [],
            },
          ],
        }),
      ];

      const el = await render();

      const turns = Array.from(el.querySelectorAll('.turn'));
      expect(turns).toHaveLength(2);
      expect(turns[0]?.textContent).toContain('Turn 1 of 2');
      expect(turns[0]?.querySelector('.orders')?.textContent).toContain('AAPL');
      // The second turn asked for nothing, so nothing of the first must have
      // drifted into it.
      expect(turns[1]?.querySelector('.orders')).toBeNull();
    });

    /** Why the pass happened at all, added 2026-09-21. The agent has been
     * told since 2026-09-12 and the page never was, so a reader could see
     * what it did and not what asked it. */
    describe('the wake reason', () => {
      const WOKE = 'A resting stop or target closed one of your positions on its own.';

      /** `twoTurns` above carries no per-turn `reasoning`, which is what the
       * card reads to decide it can draw turn blocks at all — without it the
       * pass falls to the flat layout. These turns carry one. */
      const twoBlocks = (over: Partial<AgentEvent> = {}) =>
        event({
          turns: [
            {
              prompt: 'the first prompt',
              response: '{"orders":[]}',
              thinking: null,
              reasoning: 'Reading INTC before deciding.',
              orders: [],
            },
            {
              prompt: 'the second prompt, carrying the analysis',
              response: '{"orders":[]}',
              thinking: null,
              reasoning: 'Nothing more to do.',
              orders: [],
            },
          ],
          ...over,
        });

      it('opens the first turn with what woke the pass, in the words the agent read', async () => {
        service.eventsByMonth['2026-09'] = [twoBlocks({ woke_because: WOKE })];

        const el = await render();

        const first = el.querySelector('.turn .wake-reason');
        expect(first?.textContent).toContain(WOKE);
        // A quotation, because the sentence is the prompt's and in the second
        // person: the "you" is the agent, not the reader.
        expect(first?.querySelector('q')?.textContent).toBe(WOKE);
      });

      it('is the first thing under the turn label', async () => {
        service.eventsByMonth['2026-09'] = [twoBlocks({ woke_because: WOKE })];

        const el = await render();

        const first = el.querySelector('.turn');
        const children = Array.from(first?.children ?? []);
        expect(children[0]?.className).toContain('turn-label');
        expect(children[1]?.className).toContain('wake-reason');
      });

      it("labels the wake for the reader, not with the prompt's own heading", async () => {
        /** The prompt heads the sentence "Why you are awake", and so did the
         * page until 2026-09-25 — a reader took the "you" for themselves. */
        service.eventsByMonth['2026-09'] = [twoBlocks({ woke_because: WOKE })];

        const el = await render();

        const first = el.querySelector('.turn .wake-reason')?.textContent ?? '';
        expect(first).toContain('What woke it');
        expect(first).not.toContain('Why you are awake');
      });

      it("says why a later turn was asked again, from that turn's own prompt", async () => {
        /** Until 2026-09-25 every turn repeated the wake sentence, so a pass
         * that researched ORCL and was asked again read as though it had
         * been woken twice for one reason. The real reason is in the later
         * turn's prompt, written by the backend from what is in that prompt. */
        service.eventsByMonth['2026-09'] = [
          event({
            woke_because: WOKE,
            turns: [
              {
                prompt: 'the first prompt',
                response: '{"orders":[{"side":"research","ticker":"ORCL"}]}',
                thinking: null,
                reasoning: 'Commissioning fresh research on ORCL.',
                orders: [{ side: 'research', ticker: 'ORCL', quantity: 0, reason: 'dip' }],
              },
              {
                prompt:
                  'the second prompt\n\n**Why you are asked again.** This is the same pass, not a new wake: the orders in your last answer have been carried out; and you asked to read an analysis. All of it is under "What your last answer did" above.',
                response: '{"orders":[]}',
                thinking: null,
                reasoning: 'ORCL came back Underweight; nothing to do.',
                orders: [],
              },
            ],
          }),
        ];

        const el = await render();

        const turns = Array.from(el.querySelectorAll('.turn'));
        const second = turns[1]?.querySelector('.wake-reason')?.textContent ?? '';
        expect(second).toContain('Why it was asked again');
        expect(second).toContain('its orders were carried out; and it asked to read an analysis');
        expect(second).toContain('(research ORCL)');
        expect(second).not.toContain(WOKE);
        expect(second).not.toContain('your last answer');
      });

      it('draws no line on a later turn when nothing on record says why it was asked', async () => {
        /** A pass from before 2026-09-13 has no such sentence in its prompt
         * and, here, no orders on the turn before. Saying "asked again" with
         * no reason would be a label with nothing behind it. */
        service.eventsByMonth['2026-09'] = [twoBlocks({ woke_because: WOKE })];

        const el = await render();

        const turns = Array.from(el.querySelectorAll('.turn'));
        expect(turns[0]?.querySelector('.wake-reason')).not.toBeNull();
        expect(turns[1]?.querySelector('.wake-reason')).toBeNull();
      });

      it('draws no line at all when no reason is on record', async () => {
        /** Every pass before 2026-09-21 stored none, and a skipped pass never
         * reaches the model. An empty label would read as a reason nobody
         * gave rather than one nobody kept. */
        service.eventsByMonth['2026-09'] = [twoBlocks()];

        const el = await render();

        expect(el.querySelectorAll('.turn')).toHaveLength(2);
        expect(el.querySelector('.wake-reason')).toBeNull();
      });

      it('shows it on the flat layout too, which has no turn blocks', async () => {
        /** A pass from before 2026-09-15, or a single-turn pass that fetched
         * nothing, is drawn as one flat body. It still had a wake. */
        service.eventsByMonth['2026-09'] = [event({ turns: [], woke_because: WOKE })];

        const el = await render();

        expect(el.querySelector('.turn')).toBeNull();
        expect(el.querySelector('.wake-reason')?.textContent).toContain('What woke it');
        expect(el.querySelector('.wake-reason')?.textContent).toContain(WOKE);
      });
    });

    it('summarises a closed day beside its date, so a reader knows which to open', async () => {
      /** Fifteen closed days that all read the same were a ladder with no
       * rungs. The newest day opens on its own; the older one is closed and
       * carries its passes and what they did. */
      service.eventsByMonth['2026-09'] = [
        event({ id: 2, ran_at: '2026-09-02T13:35:00Z', orders: [] }),
        event({ id: 1, ran_at: '2026-09-01T13:35:00Z' }),
      ];

      const el = await render();

      const summaries = Array.from(el.querySelectorAll('.tl-summary')).map((s) =>
        s.textContent?.trim(),
      );
      expect(summaries).toEqual(['1 pass · untrack CRM']);
    });

    it("shows every turn's thinking, not only the last", async () => {
      /** Until 2026-09-13 the thinking panel showed the last turn's alone,
       * so a two-turn pass looked as though it had thought once. */
      const pass = twoTurns();
      pass.turns![0].thinking = 'the first thought';
      pass.turns![1].thinking = 'the second thought';
      pass.thinking = 'the second thought';
      service.eventsByMonth['2026-09'] = [pass];
      const fixture = TestBed.createComponent(DecisionsView);
      await fixture.whenStable();
      const el = fixture.nativeElement as HTMLElement;

      clickButtonContaining(el, 'Show the transcript');
      await fixture.whenStable();

      // Each turn's thinking sits in that turn's own row, beside its prompt.
      const rows = Array.from(el.querySelectorAll('.transcript-turn'));
      expect(rows).toHaveLength(2);
      expect(rows[0]?.textContent).toContain('Turn 1 of 2');
      expect(rows[0]?.textContent).toContain('the first thought');
      expect(rows[0]?.textContent).not.toContain('the second thought');
      expect(rows[1]?.textContent).toContain('the second thought');
    });

    it('shows a single prompt when the pass had one turn', async () => {
      service.eventsByMonth['2026-09'] = [event({ turns: [] })];
      const fixture = TestBed.createComponent(DecisionsView);
      await fixture.whenStable();
      const el = fixture.nativeElement as HTMLElement;

      clickButtonContaining(el, 'Show the transcript');
      await fixture.whenStable();

      expect(el.textContent).not.toContain('turns.');
      expect(el.querySelectorAll('.transcript-turn')).toHaveLength(1);
      expect(el.querySelector('.markdown')?.textContent).toContain('You manage a $10,000 account');
    });

    it("shows each turn's own reasoning and what it asked for, not only the pass's final one", async () => {
      /** 2026-09-15: `event().reasoning` is only ever the last turn's — the
       * one before it explained why it read INTC, and that explanation was
       * parsed, used, and thrown away. With more act-turns now allowed, a
       * reader needs the turn-by-turn story, not only the ending. */
      const pass = event({
        reasoning: 'Bought on the read.',
        turns: [
          {
            prompt: 'the first prompt',
            response:
              '{"reasoning": "Reading INTC before deciding.", "orders": [{"side": "read", "ticker": "INTC"}]}',
            thinking: null,
            reasoning: 'Reading INTC before deciding.',
            orders: [{ side: 'read', ticker: 'INTC', quantity: 0, reason: '' }],
          },
          {
            prompt: 'the second prompt, carrying the analysis',
            response:
              '{"reasoning": "Bought on the read.", "orders": [{"side": "buy", "ticker": "INTC", "quantity": 4}]}',
            thinking: null,
            reasoning: 'Bought on the read.',
            orders: [{ side: 'buy', ticker: 'INTC', quantity: 4, reason: '' }],
          },
        ],
      });
      service.eventsByMonth['2026-09'] = [pass];
      const fixture = TestBed.createComponent(DecisionsView);
      await fixture.whenStable();
      const el = fixture.nativeElement as HTMLElement;

      // No button click: the per-turn story must be visible without it —
      // hidden behind "Show the prompt" is exactly the bug this fixes.
      expect(el.textContent).toContain('Reading INTC before deciding.');
      expect(el.textContent).toContain('Bought on the read.');
      expect(el.querySelectorAll('.orders')[0]?.textContent).toContain('read');
      expect(el.querySelectorAll('.orders')[0]?.textContent).toContain('INTC');
    });

    it('shows what a turn fetched before it answered, and the result under the prompt', async () => {
      /** 2026-09-17: on the tool channel a read or the candidate screen comes
       * back inside the same call, not as a further turn, so a one-turn pass
       * can carry fetches. The summary is visible; the full text is under
       * "Show the prompt", beside the prompt it was fetched for. */
      const pass = event({
        turns: [
          {
            prompt: 'the only prompt',
            response: '{"reasoning": "Holding after reading.", "orders": []}',
            thinking: null,
            reasoning: 'Holding after reading.',
            orders: [],
            exchanges: [
              {
                name: 'read',
                args: { ticker: 'NVDA', date: '2026-09-08' },
                result: "NVDA's analysis of 2026-09-08 said Hold: defend below 102.70",
              },
              { name: 'candidates', args: {}, result: '- CRWV: CoreWeave at $95.00' },
              {
                name: 'read',
                args: { ticker: 'SMR' },
                result:
                  'Not run: the fetch allowance for this pass is spent. Decide with what you have.',
              },
            ],
          },
        ],
      });
      service.eventsByMonth['2026-09'] = [pass];
      const fixture = TestBed.createComponent(DecisionsView);
      await fixture.whenStable();
      const el = fixture.nativeElement as HTMLElement;

      // One row per fetch, in the style of an order row, with a refused one
      // marked the way a refused order is.
      const rows = Array.from(el.querySelectorAll('.orders li'));
      expect(rows[0]?.textContent).toContain('fetched read');
      expect(rows[0]?.textContent).toContain('NVDA (2026-09-08)');
      expect(rows[0]?.querySelector('.status-icon--ok')).toBeTruthy();
      expect(rows[1]?.textContent).toContain('fetched candidates');
      expect(rows[2]?.textContent).toContain('fetched read');
      expect(rows[2]?.textContent).toContain('SMR');
      expect(rows[2]?.querySelector('.side--refused')).toBeTruthy();
      expect(rows[2]?.textContent).toContain('Not run: the fetch allowance for this pass is spent');
      expect(el.textContent).not.toContain('defend below 102.70');

      clickButtonContaining(el, 'Show the transcript');
      await fixture.whenStable();

      expect(el.textContent).toContain('Fetched read NVDA (2026-09-08)');
      expect(el.textContent).toContain('defend below 102.70');
      expect(el.textContent).toContain('CRWV: CoreWeave');
    });

    it('flags a pass the text fallback answered, and only that pass', async () => {
      /** 2026-09-17: a fallback turn is otherwise identical to a tool turn
       * that fetched nothing, and the reason was only in the container log. */
      const fallen = event({
        id: 1,
        turns: [
          {
            prompt: 'the prompt',
            response: '{"orders": []}',
            thinking: null,
            channel: 'text-fallback',
          },
        ],
      });
      const fine = event({
        id: 2,
        turns: [
          { prompt: 'the prompt', response: '{"orders": []}', thinking: null, channel: 'tool' },
        ],
      });
      service.eventsByMonth['2026-09'] = [fallen, fine];

      const el = await render();
      const cards = Array.from(el.querySelectorAll('.card'));

      expect(cards[0]?.textContent).toContain('Answered through the text fallback');
      expect(cards[1]?.textContent).not.toContain('Answered through the text fallback');
    });

    it('says nothing about fetching on a pass that fetched nothing', async () => {
      service.eventsByMonth['2026-09'] = [twoTurns()];
      const el = await render();

      expect(el.textContent).not.toContain('fetched read');
      expect(el.textContent).not.toContain('fetched candidates');
    });

    it('survives a snapshot written before turns existed', async () => {
      // The static site serves whatever the last export left on disk, and a
      // file from before 2026-09-10 has no `turns` key at all.
      const old = event();
      delete (old as { turns?: unknown }).turns;
      service.eventsByMonth['2026-09'] = [old];

      const el = await render();

      expect(el.textContent).toContain('Reducing overhead');
    });
  });

  // --- the evening review ------------------------------------------------

  describe('the evening review', () => {
    /** Added 2026-09-24. Once a trading day, after the close, the agent reads
     * every pass since its last review and speaks twice: to the maintainer,
     * then to itself. It sits inside the day it ran on, above the passes it
     * read. */

    it('shows a review inside its day, above the passes', async () => {
      service.eventsByMonth['2026-09'] = [event({ reasoning: 'the afternoon pass' })];
      service.reviews = [review()];

      const el = await render();

      // One day, not a second one for the review.
      expect(el.querySelectorAll('.tl-day').length).toBe(1);
      const cards = Array.from(el.querySelectorAll('.day-cards > *'));
      expect(cards[0]?.tagName.toLowerCase()).toBe('app-reflection-card');
      expect(cards[0]?.textContent).toContain('Evening review');
      expect(cards[1]?.textContent).toContain('the afternoon pass');
    });

    it('lists what it reported: the kind, the pass, the gap and what it would have done', async () => {
      service.eventsByMonth['2026-09'] = [event()];
      service.reviews = [review()];

      const text = (await render()).querySelector('app-reflection-card')?.textContent ?? '';

      expect(text).toContain('3 passes reviewed since');
      expect(text).toContain('missing information');
      expect(text).toContain('pass 1');
      expect(text).toContain('I could not see the sector of any holding.');
      expect(text).toContain('It would have trimmed the second chip name.');
      expect(text).toContain('Memory: added "Two chip names is one too many."');
      expect(text).toContain('Watch INTC at the open.');
      expect(text).toContain('replaced the note the last pass left');
    });

    it('says there is nothing to report when the review reported nothing', async () => {
      // Most days. The expected answer, and it must read as one rather than
      // as an empty list.
      service.eventsByMonth['2026-09'] = [event()];
      service.reviews = [review({ notes: [], wakeup_note: null, applied: [] })];

      const text = (await render()).querySelector('app-reflection-card')?.textContent ?? '';

      expect(text).toContain('Nothing to report');
      expect(text).toContain('was kept as it was');
      expect(text).not.toContain('Memory:');
    });

    it('shows only the reason on a skipped review', async () => {
      service.eventsByMonth['2026-09'] = [event()];
      service.reviews = [
        review({
          skipped: 'no pass since the last review',
          notes: [],
          prompt: null,
          response: null,
          thinking: null,
        }),
      ];

      const text = (await render()).querySelector('app-reflection-card')?.textContent ?? '';

      expect(text).toContain('no pass since the last review');
      expect(text).not.toContain('Nothing to report');
      expect(text).not.toContain('passes reviewed');
      expect(text).not.toContain('Show the transcript');
    });

    it('hides both prompts, both answers and the thinking until asked for', async () => {
      service.eventsByMonth['2026-09'] = [event()];
      service.reviews = [review()];
      const fixture = TestBed.createComponent(DecisionsView);
      await fixture.whenStable();
      const el = fixture.nativeElement as HTMLElement;
      const card = () => el.querySelector('app-reflection-card') as HTMLElement;

      expect(card().textContent).not.toContain('the review prompt');
      expect(card().textContent).not.toContain('the review thinking');

      // One disclosure opens the whole conversation: the prompts on the
      // left, the thinking and the answers on the right.
      const button = Array.from(card().querySelectorAll('button')).find((b) =>
        b.textContent?.includes('Show the transcript'),
      );
      button?.click();
      await fixture.whenStable();

      const [asked, produced] = Array.from(card().querySelectorAll('.transcript-col'));
      expect(asked?.textContent).toContain('the review prompt, every pass of the day');
      expect(asked?.textContent).toContain('the second prompt, asking for the revision');
      expect(produced?.textContent).toContain('the review thinking');
      expect(produced?.textContent).toContain('{"notes": [...]}');
      expect(produced?.textContent).toContain('{"wakeup_note": "Watch INTC at the open."}');
    });

    it('shows a review on a day with no pass in the month', async () => {
      // A day the agent skipped every pass still gets its review.
      service.eventsByMonth['2026-09'] = [event({ ran_at: '2026-09-08T13:35:00Z' })];
      service.reviews = [review({ ran_at: '2026-09-01T20:30:00Z' })];
      const fixture = TestBed.createComponent(DecisionsView);
      await fixture.whenStable();
      const el = fixture.nativeElement as HTMLElement;

      expect(el.querySelectorAll('.tl-day').length).toBe(2);

      // The older day starts collapsed, the same as a day of passes.
      clickButtonContaining(el, 'Tuesday 1 September');
      await fixture.whenStable();

      expect(el.querySelector('app-reflection-card')?.textContent).toContain('Evening review');
    });

    it('adds a month to the timeline when only a review is in it', async () => {
      service.months = ['2026-09'];
      service.eventsByMonth = { '2026-09': [event()] };
      service.reviews = [review(), review({ id: 9, ran_at: '2026-08-14T20:30:00Z' })];
      const fixture = TestBed.createComponent(DecisionsView);
      await fixture.whenStable();
      const el = fixture.nativeElement as HTMLElement;

      const months = Array.from(el.querySelectorAll('.tl-month')).map((m) => m.textContent);
      expect(months).toEqual(['September 2026', 'August 2026']);
      // Only September is open, so only its review shows yet.
      expect(el.querySelectorAll('app-reflection-card').length).toBe(1);

      clickButtonContaining(el, 'August 2026');
      await fixture.whenStable();

      expect(el.textContent).not.toContain('No decision passes in August 2026');
      expect(el.querySelectorAll('app-reflection-card').length).toBe(2);
    });

    it('shows the review alone when nothing else is on record', async () => {
      service.months = [];
      service.eventsByMonth = {};
      service.reviews = [review()];

      const el = await render();

      expect(el.querySelector('.empty')).toBeNull();
      expect(el.querySelector('app-reflection-card')?.textContent).toContain('Evening review');
    });

    it('keeps the passes when the reviews cannot be read', async () => {
      // The record a reader came for is the passes. A reviews fetch that
      // fails costs the page its reviews and nothing else.
      service.eventsByMonth['2026-09'] = [event()];
      service.failReviews = true;

      const el = await render();

      expect(el.querySelector('.empty')).toBeNull();
      expect(el.textContent).toContain('Reducing overhead');
      expect(el.textContent).toContain('The evening reviews did not load');
      expect(el.querySelector('app-reflection-card')).toBeNull();
    });
  });
});
