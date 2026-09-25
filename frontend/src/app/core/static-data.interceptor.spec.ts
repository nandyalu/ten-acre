import { HttpClient, provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { firstValueFrom } from 'rxjs';

import { environment } from '../../environments/environment';
import { staticDataInterceptor, toSnapshotPath } from './static-data.interceptor';

// --- the path-mapping table: one assertion per file the exporter writes ------

describe('toSnapshotPath', () => {
  const cases: [string, string][] = [
    ['/api/agent', '/data/agent.json'],
    ['/api/agent/trades', '/data/agent_trades.json'],
    ['/api/agent/performance', '/data/agent_performance.json'],
    ['/api/agent/history', '/data/agent_history.json'],
    ['/api/agent/curve', '/data/agent_curve.json'],
    ['/api/agent/unprotected', '/data/agent_unprotected.json'],
    ['/api/agent/notes', '/data/agent_notes.json'],
    // The exporter writes one file for every review, so the limit is dropped.
    ['/api/agent/reflections?limit=200', '/data/agent_reflections.json'],
    ['/api/agent/events?limit=30', '/data/agent_events.json'],
    ['/api/agent/events/months', '/data/agent_events_months.json'],
    ['/api/agent/events?month=2026-09', '/data/agent_events_202609.json'],
    ['/api/agent/journey/entries/months', '/data/journal_months.json'],
    ['/api/agent/journey/entries?month=2026-09', '/data/journal_202609.json'],
    ['/api/agent/journey/entries?days=10', '/data/journey_entries.json'],
    ['/api/digest', '/data/digest.json'],
    ['/api/regime', '/data/regime.json'],
    ['/api/scorecard', '/data/scorecard.json'],
    ['/api/scorecard?ticker=AAPL', '/data/scorecard.json'],
    ['/api/scorecard/calibration', '/data/calibration.json'],
    ['/api/settings', '/data/settings.json'],
    ['/api/watchlist', '/data/watchlist.json'],
    ['/api/watchlist/candidates', '/data/watchlist_candidates.json'],
    ['/api/signals?limit=50', '/data/signals_all.json'],
    ['/api/signals?status=pending&limit=50', '/data/signals_pending.json'],
    ['/api/signals?status=resolved&limit=50', '/data/signals_resolved.json'],
    ['/api/signals/42', '/data/signals/42.json'],
    ['/api/tickers', '/data/tickers.json'],
    ['/api/tickers/AAPL', '/data/tickers/AAPL.json'],
    // Route params arrive lower-case if a reader hand-types a URL — the
    // exporter always names ticker files upper-case.
    ['/api/tickers/aapl', '/data/tickers/AAPL.json'],
  ];

  for (const [input, expected] of cases) {
    it(`maps ${input} to ${expected}`, () => {
      expect(toSnapshotPath(input).path).toBe(expected);
    });
  }

  it('maps a ticker events request to the widest snapshot and carries the requested day count', () => {
    const result = toSnapshotPath('/api/tickers/AAPL/events?days=30');
    expect(result.path).toBe('/data/tickers/AAPL/events.json');
    expect(result.chartDays).toBe(30);
  });

  it('defaults chartDays to 180 when the caller does not pass one, matching the live endpoint default', () => {
    expect(toSnapshotPath('/api/tickers/AAPL/events').chartDays).toBe(180);
  });

  it('leaves an unrecognised path unchanged rather than guessing at a shape', () => {
    expect(toSnapshotPath('/api/tickers/AAPL/chart?days=90').path).toBe('/api/tickers/AAPL/chart');
  });
});

// --- the interceptor itself: only active when environment.staticSite is true -

describe('staticDataInterceptor', () => {
  let http: HttpClient;
  let backend: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(withInterceptors([staticDataInterceptor])),
        provideHttpClientTesting(),
      ],
    });
    http = TestBed.inject(HttpClient);
    backend = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    backend.verify();
    environment.staticSite = false;
  });

  it('passes requests through unchanged when staticSite is off (the live build)', async () => {
    environment.staticSite = false;
    const promise = firstValueFrom(http.get('/api/agent'));
    backend.expectOne('/api/agent').flush({ equity: 1 });
    expect(await promise).toEqual({ equity: 1 });
  });

  it('redirects to the snapshot file when staticSite is on', async () => {
    environment.staticSite = true;
    const promise = firstValueFrom(http.get('/api/agent'));
    backend.expectOne('/data/agent.json').flush({ equity: 1 });
    expect(await promise).toEqual({ equity: 1 });
  });

  it('never redirects a write request, so it fails instead of silently mutating a snapshot file', async () => {
    environment.staticSite = true;
    const promise = firstValueFrom(http.patch('/api/settings', { horizon: 'position' }));
    backend.expectOne('/api/settings').flush({}, { status: 404, statusText: 'Not Found' });
    await expect(promise).rejects.toBeTruthy();
  });

  it('slices the bars array to the requested day range, leaving everything else untouched', async () => {
    environment.staticSite = true;
    const promise = firstValueFrom(http.get('/api/tickers/AAPL/events?days=1'));

    const today = new Date().toISOString().slice(0, 10);
    const longAgo = '2000-01-01';
    backend.expectOne('/data/tickers/AAPL/events.json').flush({
      ticker: 'AAPL',
      bars: [
        { date: longAgo, timestamp: 0, open: 1, high: 1, low: 1, close: 1, volume: 1 },
        { date: today, timestamp: 0, open: 2, high: 2, low: 2, close: 2, volume: 2 },
      ],
      signals: ['unrelated-to-the-day-window'],
    });

    const result = (await promise) as { bars: { date: string }[]; signals: string[] };
    expect(result.bars.map((b) => b.date)).toEqual([today]);
    expect(result.signals).toEqual(['unrelated-to-the-day-window']);
  });
});
