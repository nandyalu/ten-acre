import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { TestBed } from '@angular/core/testing';
import { signal } from '@angular/core';
import { provideRouter } from '@angular/router';

import { AgentBook, AgentEvent } from '../../core/models/api.models';
import { AgentService } from '../../core/services/agent.service';
import { RegimeService } from '../../core/services/regime.service';
import { ScorecardService } from '../../core/services/scorecard.service';
import { ExperimentView } from './experiment-view';

/**
 * The timeline reads the agent's real passes.
 *
 * **It used to be entirely hardcoded**, and advertised a decision pass at
 * 13:35 that was removed on 2026-09-05. The agent names every one of its own
 * times now, so the rows have to come from the record.
 */
function event(over: Partial<AgentEvent> = {}): AgentEvent {
  return {
    id: 1,
    // With the offset, as the API sends it. Without one a browser reads the
    // instant as local time and files the pass under the wrong day.
    ran_at: '2026-09-04T14:37:00Z',
    next_wakeup: null,
    reasoning: '',
    skipped: null,
    equity: 10_000,
    cash: 100,
    research_spent: 0,
    prompt: '',
    response: '',
    thinking: null,
    prompt_tokens: null,
    completion_tokens: null,
    seconds: null,
    orders: [],
    refused: [],
    failed: [],
    ...over,
  };
}

class AgentServiceStub {
  readonly events = signal<AgentEvent[]>([]);
  readonly book = signal<AgentBook | null>(null);
  readonly curve = signal([]);
  async load(): Promise<void> {}
  async loadEvents(): Promise<void> {}
  async loadCurve(): Promise<void> {}
}

class RegimeServiceStub {
  readonly regime = signal(null);
  async load(): Promise<void> {}
}

class ScorecardServiceStub {
  readonly scorecard = signal(null);
  async load(): Promise<void> {}
}

describe('ExperimentView timeline', () => {
  let agent: AgentServiceStub;

  beforeEach(async () => {
    agent = new AgentServiceStub();
    await TestBed.configureTestingModule({
      imports: [ExperimentView],
      providers: [
        { provide: AgentService, useValue: agent },
        { provide: RegimeService, useValue: new RegimeServiceStub() },
        { provide: ScorecardService, useValue: new ScorecardServiceStub() },
        // The hero's answer line links to the book, and a rendered link
        // needs a router behind it.
        provideRouter([]),
      ],
    }).compileComponents();
  });

  /** Reaches the protected members the way the template does. */
  function view(): any {
    return TestBed.createComponent(ExperimentView).componentInstance as any;
  }

  it('answers the headline on the first screen once the book is in', async () => {
    /** The scoreboard below the fold used to be the first place the figure
     * appeared, so a reader who stopped at the headline left without it. */
    agent.book.set({
      enabled: true,
      sandbox: true,
      budget: 10_000,
      cash: 6_600.74,
      invested: 3_393.88,
      market_value: 3_386.57,
      equity: 9_987.31,
      realized_pnl: -5.38,
      return_pct: -0.1,
      holdings: [],
    });
    const fixture = TestBed.createComponent(ExperimentView);
    await fixture.whenStable();

    const answer = (fixture.nativeElement as HTMLElement).querySelector('.hero .hero-answer');
    expect(answer?.textContent).toContain('Right now');
    expect(answer?.textContent).toContain('$9,987.31');
    expect(answer?.querySelector('.money--neg')).not.toBeNull();
  });

  it('keeps the guardrails inside the hero, where the footnote points', () => {
    const el = TestBed.createComponent(ExperimentView).nativeElement as HTMLElement;

    expect(el.querySelector('.hero #guardrails')).not.toBeNull();
    expect(el.querySelector('.hero app-logo')).toBeNull();
  });

  it('no longer advertises the decision pass that was removed', () => {
    const rows = [...view().today(), ...view().previous()];

    expect(rows.some((r: any) => r.text === 'It decides')).toBe(false);
  });

  it('still lists the jobs that do run on a clock', () => {
    const texts = view()
      .today()
      .map((r: any) => r.text);

    expect(texts).toContain('The market regime is read');
    expect(texts).toContain('Signals are graded, then the journal is written');
  });

  it('no longer advertises the removed morning sweep', () => {
    // Deleted on 2026-09-08, not merely idle — the agent commissions its own
    // research now, so this row would promise a pass that never happens.
    const texts = view()
      .today()
      .map((r: any) => r.text);

    expect(texts).not.toContain('The morning sweep analyses the watchlist');
  });

  it('marks a clock-driven job as not the agent’s own choice', () => {
    const regime = view()
      .today()
      .find((r: any) => r.text.startsWith('The market regime'));

    expect(regime.agent).toBe(false);
  });

  it('gives every row a key that survives a repeated summary', () => {
    // Two passes that both did nothing carry identical text. Tracking by text
    // collapsed them into one row.
    agent.events.set([
      event({ id: 1, ran_at: '2026-09-04T14:00:00Z' }),
      event({ id: 2, ran_at: '2026-09-04T15:00:00Z' }),
    ]);
    const keys = view()
      .previous()
      .map((r: any) => r.key);

    expect(new Set(keys).size).toBe(keys.length);
  });

  it('summarises what a pass did', () => {
    const summary = view().summarise(
      event({
        orders: [
          { side: 'buy', ticker: 'MARA', quantity: 4, reason: '' },
          { side: 'research', ticker: 'INTC', quantity: 0, reason: '' },
        ],
      }),
    );

    expect(summary).toBe('Bought MARA. Commissioned INTC');
  });

  it('says plainly when a pass did nothing', () => {
    expect(view().summarise(event({ orders: [] }))).toBe('It looked, and did nothing');
  });

  it('does not count a note as having acted', () => {
    // A note is the agent talking, not the agent trading. Counting it would
    // mark an idle pass as a moment something happened.
    const summary = view().summarise(
      event({ orders: [{ side: 'note', ticker: '', quantity: 0, reason: 'I need X' }] }),
    );

    expect(summary).toBe('It looked, and did nothing');
  });

  it('reports why a pass was skipped, rather than calling it idle', () => {
    expect(view().summarise(event({ skipped: 'the market was closed' }))).toBe(
      'the market was closed',
    );
  });
});

describe('ExperimentView timeline at the weekend', () => {
  let agent: AgentServiceStub;

  beforeEach(async () => {
    agent = new AgentServiceStub();
    await TestBed.configureTestingModule({
      imports: [ExperimentView],
      providers: [
        { provide: AgentService, useValue: agent },
        { provide: RegimeService, useValue: new RegimeServiceStub() },
        { provide: ScorecardService, useValue: new ScorecardServiceStub() },
      ],
    }).compileComponents();
  });

  /** Saturday 5 September 2026, mid-morning in New York. */
  function onSaturday(): any {
    vi.setSystemTime(new Date('2026-09-05T14:00:00Z'));
    return TestBed.createComponent(ExperimentView).componentInstance as any;
  }

  beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }));
  afterEach(() => vi.useRealTimers());

  it('promises no clock-driven job on a day none of them run', () => {
    // The regime read, the earnings check and grading all return early at
    // the weekend. Listing them promises three things that will not happen
    // — the same fault as the removed 13:35 row, more quietly.
    const saturday = onSaturday();

    expect(saturday.fixedBeats(new Date('2026-09-05T14:00:00Z'))).toEqual([]);
  });

  it('looks ahead to the next trading day when today is closed', () => {
    const saturday = onSaturday();

    expect(saturday.todayIsNow()).toBe(false);
    expect(saturday.todayLabel()).toContain('Monday');
  });

  it('shows the wakeup the agent asked for, even though it falls on Monday', () => {
    // Without the look-ahead this row lands on a day the page never renders,
    // and the one line saying the experiment is still running disappears for
    // the whole weekend.
    agent.events.set([
      event({ id: 20, ran_at: '2026-09-04T19:57:00Z', next_wakeup: '2026-09-07T13:30:00Z' }),
    ]);
    const rows = onSaturday().today();

    expect(rows.some((r: any) => r.text === 'It asked to be woken')).toBe(true);
  });

  it('lists Monday’s clock-driven jobs as still to come', () => {
    const texts = onSaturday()
      .today()
      .map((r: any) => r.text);

    expect(texts).toContain('The market regime is read');
    expect(
      onSaturday()
        .today()
        .every((r: any) => !r.done),
    ).toBe(true);
  });

  it('stays on today when the agent ran at the weekend', () => {
    // It may wake on a Saturday now. A pass it chose to make is worth showing
    // whenever it happened, so the look-ahead must not hide it.
    agent.events.set([event({ id: 21, ran_at: '2026-09-05T13:00:00Z' })]);

    expect(onSaturday().todayIsNow()).toBe(true);
  });
});
