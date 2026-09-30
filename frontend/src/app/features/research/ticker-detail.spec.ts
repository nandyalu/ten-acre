import { Component, input } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { ActivatedRoute, provideRouter } from '@angular/router';

import {
  AgentPosition,
  Alert,
  Lot,
  OhlcBar,
  Signal,
  TickerDetail,
  TickerEvents,
  Trade,
} from '../../core/models/api.models';
import { TickersService } from '../../core/services/tickers.service';
import { PriceChart } from '../../shared/price-chart';
import { TickerDetailPage } from './ticker-detail';

/** Stands in for the real chart. lightweight-charts draws to a canvas, which
 * jsdom does not implement, so instantiating it here throws on every render
 * and tells us nothing about the logic under test. */
@Component({
  selector: 'app-price-chart',
  template: '',
})
class PriceChartStub {
  readonly bars = input<OhlcBar[]>([]);
  readonly signals = input<Signal[]>([]);
  readonly alerts = input<Alert[]>([]);
  readonly trades = input<Trade[]>([]);
  readonly stopLevel = input<number | null>(null);
  readonly targetLevel = input<number | null>(null);
}

const EVENTS: TickerEvents = {
  ticker: 'NVDA',
  bars: [
    {
      date: '2026-08-01',
      timestamp: Date.parse('2026-08-01T00:00:00Z') / 1000,
      open: 170,
      high: 176,
      low: 169,
      close: 175,
      volume: 1000,
    },
    {
      date: '2026-08-04',
      timestamp: Date.parse('2026-08-04T00:00:00Z') / 1000,
      open: 175,
      high: 182,
      low: 174,
      close: 180,
      volume: 1200,
    },
  ],
  signals: [
    {
      id: 2,
      ticker: 'NVDA',
      signal_date: '2026-08-04',
      decision: 'Buy',
      rationale: '',
      time_horizon_text: '1-2 weeks',
      price_target: 210,
      price_at_signal: 180,
      evaluation_date: '2026-08-18',
      price_at_evaluation: null,
      outcome: null,
      evaluated_at: null,
      created_at: '2026-08-04T14:00:00Z',
      message_id: null,
      benchmark_price_at_signal: null,
      benchmark_price_at_evaluation: null,
      alpha_pct: null,
      outcome_vs_benchmark: null,
      price_target_hit: null,
      horizon: 'swing',
      model: 'gemma4-e2b-96k',
      trigger: 'sweep',
      duration_seconds: 148,
      prompt_tokens: 44100,
      completion_tokens: 4100,
      llm_calls: 23,
      entry_price: 180,
      stop_loss: 168,
      win_probability: 64,
      risk_reward: 2.5,
      expected_value_r: 0.75,
      cost_usd: null,
      cost_basis: null,
    },
    {
      id: 1,
      ticker: 'NVDA',
      signal_date: '2026-07-01',
      decision: 'Hold',
      rationale: '',
      time_horizon_text: null,
      price_target: null,
      price_at_signal: 150,
      evaluation_date: '2026-07-15',
      price_at_evaluation: 155,
      outcome: 'pass',
      evaluated_at: null,
      created_at: '2026-07-01T11:00:00Z',
      message_id: null,
      benchmark_price_at_signal: null,
      benchmark_price_at_evaluation: null,
      alpha_pct: null,
      outcome_vs_benchmark: null,
      price_target_hit: null,
      horizon: 'swing',
      model: 'gemma4-e2b-96k',
      trigger: 'sweep',
      duration_seconds: 148,
      prompt_tokens: 44100,
      completion_tokens: 4100,
      llm_calls: 23,
      entry_price: null,
      stop_loss: 140,
      win_probability: null,
      risk_reward: null,
      expected_value_r: null,
      cost_usd: null,
      cost_basis: null,
    },
  ],
  alerts: [
    {
      id: 5,
      ticker: 'NVDA',
      alert_type: 'big_move',
      message: 'NVDA moved +6.1% today.',
      created_at: '2026-08-03T14:00:00Z',
    },
  ],
  trades: [
    {
      side: 'buy',
      date: '2026-07-08',
      filled_at: '2026-07-08T14:32:00Z',
      price: 155,
      quantity: 10,
    },
  ],
  lots: [],
};

const DETAIL: TickerDetail = {
  ticker: 'NVDA',
  current_price: 180,
  price_updated_at: '2026-08-06T10:00:00Z',
  latest_signal: null,
  agent_position: null,
  inactive: false,
  inactive_reason: null,
};

/** INTC on 2026-08-20: three shares held by the auto trader with nothing
 * resting at the broker. */
const UNPROTECTED: AgentPosition = {
  quantity: 3,
  avg_cost: 91.84,
  price: 92.8,
  opened: '2026-08-19',
  held_days: 1,
  market_value: 278.4,
  unrealized_pct: 1.05,
  exits: [],
  unprotected: true,
  arm_queued: false,
};

const BRACKETED: AgentPosition = {
  ...UNPROTECTED,
  exits: [
    { kind: 'stop', price: 315.04, quantity: 2 },
    { kind: 'target', price: 377.09, quantity: 2 },
  ],
  unprotected: false,
};

class TickersServiceStub {
  detail: TickerDetail = DETAIL;
  events: TickerEvents = EVENTS;
  async getDetail(): Promise<TickerDetail> {
    return this.detail;
  }
  async getEvents(): Promise<TickerEvents> {
    return this.events;
  }
}

interface Exposed {
  timeline: () => {
    kind: string;
    date: string;
    at: number;
    title: string;
    outcome?: string | null;
  }[];
  activeSignal: () => { id: number } | null;
  stopLevel: () => number | null;
  targetLevel: () => number | null;
  stopDistancePct: () => number | null;
  targetDistancePct: () => number | null;
  restingStop: () => number | null;
  restingTarget: () => number | null;
  lots: () => Lot[];
}

describe('TickerDetailPage', () => {
  let stub: TickersServiceStub;
  let element: HTMLElement;

  beforeEach(() => {
    stub = new TickersServiceStub();
  });

  async function create(): Promise<Exposed> {
    await TestBed.configureTestingModule({
      imports: [TickerDetailPage],
      providers: [
        { provide: TickersService, useFactory: () => stub },
        {
          provide: ActivatedRoute,
          useValue: { snapshot: { paramMap: { get: () => 'NVDA' } } },
        },
        provideRouter([]),
      ],
    })
      .overrideComponent(TickerDetailPage, {
        remove: { imports: [PriceChart] },
        add: { imports: [PriceChartStub] },
      })
      .compileComponents();
    const fixture = TestBed.createComponent(TickerDetailPage);
    await fixture.whenStable();
    element = fixture.nativeElement as HTMLElement;
    return fixture.componentInstance as unknown as Exposed;
  }

  it('merges signals, alerts, and trades into one newest-first timeline', async () => {
    const c = await create();
    const kinds = c.timeline().map((e) => `${new Date(e.at).toISOString()}:${e.kind}`);
    expect(kinds).toEqual([
      '2026-08-04T14:00:00.000Z:signal',
      '2026-08-03T14:00:00.000Z:alert',
      '2026-07-08T14:32:00.000Z:trade',
      '2026-07-01T11:00:00.000Z:signal',
    ]);
  });

  it('shows the time of each entry, not only its date', async () => {
    const c = await create();
    for (const entry of c.timeline()) {
      expect(entry.date).toMatch(/\d{1,2}:\d{2}/);
    }
  });

  it('carries the graded outcome onto the timeline entry', async () => {
    const c = await create();
    const old = c.timeline().at(-1);
    expect(old?.outcome).toBe('pass');
  });

  it('shows the newest analysis beside the timeline, with a link to all of it', async () => {
    await create();
    const card = [...element.querySelectorAll('section.card')].find((el) =>
      el.querySelector('h2')?.textContent?.includes('Latest analysis'),
    );
    expect(card?.querySelector('a')?.getAttribute('href')).toBe('/signals/2');
    expect(card?.textContent).toContain('The normal morning run over the whole watchlist');
    expect(card?.textContent).toContain('$180.00');
  });

  it('draws the newest signal levels, not an older one', async () => {
    // An older signal's stop was superseded, not merely graded — drawing it
    // would put a stale line on the chart.
    const c = await create();
    expect(c.activeSignal()?.id).toBe(2);
    expect(c.stopLevel()).toBe(168);
    expect(c.targetLevel()).toBe(210);
  });

  it('measures stop and target distance from the current price', async () => {
    const c = await create();
    // Price 180, stop 168 → -6.7%; target 210 → +16.7%.
    expect(c.stopDistancePct()).toBeCloseTo(-6.67, 1);
    expect(c.targetDistancePct()).toBeCloseTo(16.67, 1);
  });

  // --- the auto trader's position -------------------------------------------

  it('warns when the auto trader holds this and nothing is resting under it', async () => {
    // The state that motivated all of this: the page showed a signal's stop
    // that looked like protection, beside a position it did not know existed.
    stub.detail = { ...DETAIL, agent_position: UNPROTECTED };
    await create();

    expect(element.textContent).toContain('nothing is resting at the broker');
  });

  it('says nothing when the position is bracketed', async () => {
    stub.detail = { ...DETAIL, agent_position: BRACKETED };
    await create();

    expect(element.textContent).not.toContain('nothing is resting at the broker');
  });

  it("shows the broker's levels apart from the analysis's", async () => {
    stub.detail = { ...DETAIL, agent_position: BRACKETED };
    const c = await create();

    expect(c.restingStop()).toBe(315.04);
    expect(c.restingTarget()).toBe(377.09);
    expect(element.textContent).toContain('live order at the broker');
  });

  it('has no resting levels when nothing is resting', async () => {
    stub.detail = { ...DETAIL, agent_position: UNPROTECTED };
    const c = await create();

    expect(c.restingStop()).toBeNull();
    expect(c.restingTarget()).toBeNull();
  });

  // --- lots ------------------------------------------------------------------

  it('lists every lot with its result, and marks an open one as undecided', async () => {
    stub.events = {
      ...EVENTS,
      lots: [
        {
          quantity: 3,
          entry: 98.41,
          entry_at: '2026-08-13',
          exit: 101.5,
          exit_at: '2026-08-19',
          pnl: 9.27,
          return_pct: 3.14,
          held_days: 6,
          signal_id: 37,
        },
        {
          quantity: 10,
          entry: 155,
          entry_at: '2026-07-08',
          exit: null,
          exit_at: null,
          pnl: null,
          return_pct: null,
          held_days: 43,
          signal_id: null,
        },
      ],
    };
    await create();

    const text = element.textContent ?? '';
    expect(text).toContain('Positions taken');
    expect(text).toContain('101.50');
    // An open lot must not show a profit — an unrealized figure in that column
    // would read as booked.
    expect(text).toContain('open');
  });

  it('leaves the lots table out entirely when nothing was ever bought', async () => {
    await create();

    expect(element.textContent).not.toContain('Positions taken');
  });

  it('offers a button to place the missing exits', async () => {
    stub.detail = { ...DETAIL, agent_position: UNPROTECTED };
    await create();

    const button = Array.from(element.querySelectorAll('button')).find((b) =>
      b.textContent?.includes('Place the exits'),
    );
    expect(button).toBeTruthy();
  });

  it('offers no such button when the position is already bracketed', async () => {
    // The button places orders. Offering it where exits already rest invites
    // doubling up, and a second stop sells the position twice.
    stub.detail = { ...DETAIL, agent_position: BRACKETED };
    await create();

    const button = Array.from(element.querySelectorAll('button')).find((b) =>
      b.textContent?.includes('Place the exits'),
    );
    expect(button).toBeUndefined();
  });

  it('says an arming is queued instead of offering the button again', async () => {
    // Pressing it twice changes nothing, and a button that looks live implies
    // the first press did not register.
    stub.detail = { ...DETAIL, agent_position: { ...UNPROTECTED, arm_queued: true } };
    await create();

    const button = Array.from(element.querySelectorAll('button')).find((b) =>
      b.textContent?.includes('Place the exits'),
    );
    expect(button).toBeUndefined();
    expect(element.textContent).toContain('Queued');
  });

  it('still warns that nothing is protecting a queued position', async () => {
    // Queued is not protected. The exits go on at the open, and the overnight
    // gap is exactly when they would have mattered.
    stub.detail = { ...DETAIL, agent_position: { ...UNPROTECTED, arm_queued: true } };
    await create();

    expect(element.textContent).toContain('nothing is resting at the broker');
  });
});
