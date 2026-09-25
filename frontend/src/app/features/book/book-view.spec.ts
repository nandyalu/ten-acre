import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { signal } from '@angular/core';

import { AgentBook, AgentEquityPoint, AgentTrade } from '../../core/models/api.models';
import { AgentService } from '../../core/services/agent.service';
import { BookView } from './book-view';

/** ZBH and GOOG as the book actually stood on 2026-08-13. */
const BOOK: AgentBook = {
  enabled: true,
  sandbox: true,
  budget: 1000,
  cash: 313.85,
  invested: 686.15,
  market_value: 982.1,
  equity: 1295.95,
  realized_pnl: 0,
  return_pct: 29.6,
  holdings: [
    {
      ticker: 'ZBH',
      quantity: 3,
      avg_cost: 98.41,
      price: 98.29,
      market_value: 294.87,
      cost_basis: 295.23,
      unrealized_pnl: -0.36,
    },
    {
      ticker: 'GOOG',
      quantity: 2,
      avg_cost: 343.66,
      price: 344.0,
      market_value: 688.0,
      cost_basis: 687.32,
      unrealized_pnl: 0.68,
    },
  ],
};

function exit(
  id: number,
  ticker: string,
  kind: 'stop' | 'target',
  limit_price: number,
): AgentTrade {
  return {
    id,
    ticker,
    side: 'sell',
    quantity: 3,
    price: null,
    placed_at: '2026-08-13T17:02:00',
    filled_at: null,
    status: 'pending',
    is_stop: true,
    limit_price,
    exit_kind: kind,
    reason: `${kind === 'stop' ? 'stop-loss' : 'take-profit'} resting at $${limit_price}`,
    signal_id: null,
  };
}

/** A filled market order, the kind the trade log is mostly made of. */
function fill(id: number, ticker: string, side: 'buy' | 'sell'): AgentTrade {
  return {
    id,
    ticker,
    side,
    quantity: 4,
    price: 98.07,
    placed_at: '2026-09-15T16:57:00',
    filled_at: '2026-09-15T16:57:00',
    status: 'filled',
    is_stop: false,
    limit_price: null,
    exit_kind: null,
    reason: `${side} ${ticker} on a fresh signal`,
    signal_id: null,
  };
}

class AgentServiceStub {
  readonly book = signal<AgentBook | null>(BOOK);
  readonly trades = signal<AgentTrade[]>([]);
  readonly performance = signal(null);
  readonly history = signal([]);
  readonly curve = signal<AgentEquityPoint[]>([]);
  async load(): Promise<void> {}
}

describe('BookView', () => {
  let service: AgentServiceStub;

  beforeEach(async () => {
    service = new AgentServiceStub();
    await TestBed.configureTestingModule({
      imports: [BookView],
      providers: [{ provide: AgentService, useValue: service }, provideRouter([])],
    }).compileComponents();
  });

  function holdingRow(fixture: { nativeElement: unknown }, ticker: string): string[] {
    const rows = Array.from(
      (fixture.nativeElement as HTMLElement).querySelectorAll('table tbody tr'),
    );
    const row = rows.find((r) => r.textContent?.trim().startsWith(ticker));
    return Array.from(row?.querySelectorAll('td') ?? []).map((c) => c.textContent!.trim());
  }

  it('puts each holding’s resting stop and target on its own row', async () => {
    service.trades.set([
      exit(1, 'ZBH', 'stop', 95.3),
      exit(2, 'ZBH', 'target', 101.5),
      exit(3, 'GOOG', 'stop', 315.04),
      exit(4, 'GOOG', 'target', 377.09),
    ]);
    const fixture = TestBed.createComponent(BookView);
    await fixture.whenStable();

    expect(holdingRow(fixture, 'ZBH')).toContain('$95.30');
    expect(holdingRow(fixture, 'ZBH')).toContain('$101.50');
    // Joined by ticker across two endpoints, so a mismatch would show one
    // position wearing another's levels.
    expect(holdingRow(fixture, 'GOOG')).toContain('$315.04');
    expect(holdingRow(fixture, 'GOOG')).not.toContain('$95.30');
  });

  it('shows a dash where nothing is protecting the position', async () => {
    // The failure that started this: a bracket the broker refused left the
    // position naked, and the page said nothing at all.
    service.trades.set([exit(1, 'ZBH', 'stop', 95.3), exit(2, 'ZBH', 'target', 101.5)]);
    const fixture = TestBed.createComponent(BookView);
    await fixture.whenStable();

    const goog = holdingRow(fixture, 'GOOG');
    expect(goog.filter((c) => c === '—').length).toBeGreaterThanOrEqual(2);
  });

  it('lists an exit resting on shares the book does not hold', async () => {
    service.trades.set([exit(9, 'VERI', 'stop', 1.1)]);
    const fixture = TestBed.createComponent(BookView);
    await fixture.whenStable();

    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('Exits with no holding');
    expect(text).toContain('VERI');
  });

  it('says nothing about unmatched exits when every one has a holding', async () => {
    service.trades.set([exit(1, 'ZBH', 'stop', 95.3)]);
    const fixture = TestBed.createComponent(BookView);
    await fixture.whenStable();

    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).not.toContain('Exits with no holding');
  });

  it('draws the equity curve once there are points', async () => {
    service.curve.set([
      { date: '2026-08-13', equity: 1004.04, cash: 17.45, market_value: 986.59 },
      { date: '2026-08-14', equity: 1006.48, cash: 17.45, market_value: 989.03 },
    ]);
    const fixture = TestBed.createComponent(BookView);
    await fixture.whenStable();

    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('app-equity-chart')).toBeTruthy();
    expect(el.textContent).not.toContain('No curve yet');
  });

  // --- the trade log, paged and filtered in place ------------------------

  function tradeLog(el: HTMLElement): HTMLElement {
    const section = Array.from(el.querySelectorAll('section.card')).find((s) =>
      s.querySelector('h2')?.textContent?.includes('Trade log'),
    );
    return section as HTMLElement;
  }

  it('shows the trade log ten orders at a time, newest page first', async () => {
    service.trades.set(Array.from({ length: 23 }, (_, i) => fill(i + 1, 'INTC', 'buy')));
    const fixture = TestBed.createComponent(BookView);
    await fixture.whenStable();

    const log = tradeLog(fixture.nativeElement);

    expect(log.querySelectorAll('tbody tr')).toHaveLength(10);
    expect(log.querySelector('.pager')?.textContent).toContain('1–10 of 23 orders');
  });

  it('moves to the next page of orders on Next', async () => {
    service.trades.set(Array.from({ length: 23 }, (_, i) => fill(i + 1, 'INTC', 'buy')));
    const fixture = TestBed.createComponent(BookView);
    await fixture.whenStable();
    const log = tradeLog(fixture.nativeElement);

    const next = Array.from(log.querySelectorAll('button')).find((b) =>
      b.textContent?.includes('Next'),
    );
    next?.click();
    await fixture.whenStable();

    expect(log.querySelector('.pager')?.textContent).toContain('11–20 of 23 orders');
  });

  it('filters the trade log in place as the reader types', async () => {
    service.trades.set([fill(1, 'INTC', 'buy'), fill(2, 'COIN', 'buy'), fill(3, 'COIN', 'sell')]);
    const fixture = TestBed.createComponent(BookView);
    await fixture.whenStable();
    const log = tradeLog(fixture.nativeElement);

    const box = log.querySelector<HTMLInputElement>('input[type="search"]')!;
    box.value = 'coin';
    box.dispatchEvent(new Event('input'));
    await fixture.whenStable();

    const tickers = Array.from(log.querySelectorAll('tbody tr td:first-child')).map((c) =>
      c.textContent?.trim(),
    );
    expect(tickers).toEqual(['COIN', 'COIN']);
  });

  it('says when nothing matches the filter rather than showing an empty table', async () => {
    service.trades.set([fill(1, 'INTC', 'buy')]);
    const fixture = TestBed.createComponent(BookView);
    await fixture.whenStable();
    const log = tradeLog(fixture.nativeElement);

    const box = log.querySelector<HTMLInputElement>('input[type="search"]')!;
    box.value = 'ZZZZ';
    box.dispatchEvent(new Event('input'));
    await fixture.whenStable();

    expect(log.querySelector('table')).toBeNull();
    expect(log.textContent).toContain('No order matches “ZZZZ”');
  });

  it('hides the pager while the log fits on one page', async () => {
    service.trades.set([fill(1, 'INTC', 'buy')]);
    const fixture = TestBed.createComponent(BookView);
    await fixture.whenStable();

    expect(tradeLog(fixture.nativeElement).querySelector('.pager')).toBeNull();
  });

  it('says the curve has not started rather than drawing an empty chart', async () => {
    // Before the first fill there is nothing to plot, and an axis with no line
    // on it reads as a bug.
    const fixture = TestBed.createComponent(BookView);
    await fixture.whenStable();

    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('app-line-chart')).toBeNull();
    expect(el.textContent).toContain('No curve yet');
  });
});
