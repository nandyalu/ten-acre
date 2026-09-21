import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import { AgentEvent } from '../../core/models/api.models';
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

/** The newest month is expanded (and fetched) as soon as the page loads —
 * every test seeds exactly that one month, since that is what a reader sees
 * without clicking anything. A separate spec covers opening an older one. */
class AgentServiceStub {
  months: string[] = ['2026-09'];
  eventsByMonth: Record<string, AgentEvent[]> = { '2026-09': [] };
  failMonths = false;

  async getEventMonths(): Promise<string[]> {
    if (this.failMonths) throw new Error('network error');
    return this.months;
  }

  async getEventsForMonth(month: string): Promise<AgentEvent[]> {
    return this.eventsByMonth[month] ?? [];
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

  it('hides the prompt until it is asked for', async () => {
    // A prompt runs to tens of kilobytes. A feed that opens with one is a feed
    // nobody scrolls.
    service.eventsByMonth['2026-09'] = [event()];

    const el = await render();

    expect(el.querySelector('.verbatim')).toBeNull();
    expect(el.textContent).toContain('Show the prompt');
  });

  it('shows the prompt verbatim once opened', async () => {
    service.eventsByMonth['2026-09'] = [event()];
    const fixture = TestBed.createComponent(DecisionsView);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    clickButtonContaining(el, 'Show the prompt');
    await fixture.whenStable();

    expect(el.querySelector('.verbatim')?.textContent).toContain('You manage a $10,000 account');
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
    expect(el.textContent).not.toContain('Show the prompt');
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

      clickButtonContaining(el, 'Show the prompt');
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

      clickButtonContaining(el, 'Show the thinking');
      await fixture.whenStable();

      expect(el.textContent).toContain('the first thought');
      expect(el.textContent).toContain('the second thought');
      expect(el.textContent).toContain('Turn 1 of 2 — thinking');
    });

    it('shows a single prompt when the pass had one turn', async () => {
      service.eventsByMonth['2026-09'] = [event({ turns: [] })];
      const fixture = TestBed.createComponent(DecisionsView);
      await fixture.whenStable();
      const el = fixture.nativeElement as HTMLElement;

      clickButtonContaining(el, 'Show the prompt');
      await fixture.whenStable();

      expect(el.textContent).not.toContain('turns.');
      expect(el.querySelector('.verbatim')?.textContent).toContain('You manage a $10,000 account');
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

      clickButtonContaining(el, 'Show the prompt');
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
});
