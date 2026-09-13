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
