import { HttpInterceptorFn, HttpResponse } from '@angular/common/http';
import { map } from 'rxjs';

import { environment } from '../../environments/environment';
import { OhlcBar, TickerEvents } from './models/api.models';

/**
 * Redirects every `/api/...` GET to the matching file the exporter wrote
 * (backend/services/snapshot_export.py), so none of the app's services or
 * components need to know whether they are talking to a live backend or a
 * static file. Only active when `environment.staticSite` is true — the live
 * operator build never loads this path at all.
 *
 * Two endpoints carry a query parameter that used to change what the server
 * sent back (`/signals?status=`, `/tickers/:ticker/events?days=`). The
 * exporter bakes the full dataset once instead of one file per parameter
 * value, so this interceptor picks the right file (`signals?status=`) or
 * slices the response after the fact (`events?days=`) — the one place the
 * static site does real work instead of just renaming a path.
 *
 * The two write requests (`PATCH /api/settings`, `POST /api/agent/exits/:id`)
 * are left untouched. There is no file for either, so they 404 — the same
 * outcome as the live write guard, just because nothing is listening rather
 * than because something refused.
 */
export const staticDataInterceptor: HttpInterceptorFn = (req, next) => {
  if (!environment.staticSite || req.method !== 'GET') {
    return next(req);
  }

  const { path, chartDays } = toSnapshotPath(req.urlWithParams);
  const staticReq = req.clone({ url: path, params: undefined });

  if (chartDays === undefined) {
    return next(staticReq);
  }

  const cutoff = new Date();
  cutoff.setDate(cutoff.getDate() - chartDays);
  const cutoffDate = cutoff.toISOString().slice(0, 10);

  return next(staticReq).pipe(
    map((event) => {
      if (!(event instanceof HttpResponse) || !event.body) return event;
      const body = event.body as TickerEvents;
      if (!Array.isArray(body.bars)) return event;
      return event.clone({
        body: { ...body, bars: body.bars.filter((bar: OhlcBar) => bar.date >= cutoffDate) },
      });
    }),
  );
};

/** Same-origin `/data` by default; an absolute URL when the publisher was
 * built to serve the snapshot from R2. See environments/environment.public.ts. */
const SNAPSHOT_ROOT = environment.snapshotRoot;

/** Exported for direct unit testing — see static-data.interceptor.spec.ts.
 * The mapping table is the part most likely to drift from
 * backend/services/snapshot_export.py's file names, so it is tested on its
 * own rather than only indirectly through the interceptor. */
export function toSnapshotPath(urlWithParams: string): { path: string; chartDays?: number } {
  const [pathname, query] = urlWithParams.split('?');
  const params = new URLSearchParams(query ?? '');
  const segments = pathname
    .replace(/^\/api\/?/, '')
    .split('/')
    .filter(Boolean);

  switch (segments[0]) {
    case 'agent':
      if (segments[1] === 'journey' && segments[2] === 'entries') {
        if (segments[3] === 'months') return { path: `${SNAPSHOT_ROOT}/journal_months.json` };
        const journalMonth = params.get('month');
        if (journalMonth) {
          return { path: `${SNAPSHOT_ROOT}/journal_${journalMonth.replace('-', '')}.json` };
        }
        return { path: `${SNAPSHOT_ROOT}/journey_entries.json` };
      }
      if (segments[1] === 'events') {
        if (segments[2] === 'months') return { path: `${SNAPSHOT_ROOT}/agent_events_months.json` };
        const month = params.get('month');
        // "2026-09" -> "202609", matching snapshot_export.py's file naming
        // for one calendar month's decision passes.
        if (month) return { path: `${SNAPSHOT_ROOT}/agent_events_${month.replace('-', '')}.json` };
        return { path: `${SNAPSHOT_ROOT}/agent_events.json` };
      }
      // trades, performance, history, curve, unprotected, notes, reflections
      // all follow the exporter's agent_<name>.json convention.
      return {
        path: segments[1]
          ? `${SNAPSHOT_ROOT}/agent_${segments[1]}.json`
          : `${SNAPSHOT_ROOT}/agent.json`,
      };

    case 'digest':
      return { path: `${SNAPSHOT_ROOT}/digest.json` };

    case 'regime':
      return { path: `${SNAPSHOT_ROOT}/regime.json` };

    case 'scorecard':
      return {
        path: `${SNAPSHOT_ROOT}/${segments[1] === 'calibration' ? 'calibration' : 'scorecard'}.json`,
      };

    case 'settings':
      return { path: `${SNAPSHOT_ROOT}/settings.json` };

    case 'watchlist':
      return {
        path: `${SNAPSHOT_ROOT}/${segments[1] === 'candidates' ? 'watchlist_candidates' : 'watchlist'}.json`,
      };

    case 'signals': {
      if (segments[1]) return { path: `${SNAPSHOT_ROOT}/signals/${segments[1]}.json` };
      const status = params.get('status');
      const file =
        status === 'pending'
          ? 'signals_pending'
          : status === 'resolved'
            ? 'signals_resolved'
            : 'signals_all';
      return { path: `${SNAPSHOT_ROOT}/${file}.json` };
    }

    case 'tickers': {
      const ticker = segments[1]?.toUpperCase();
      if (!ticker) return { path: `${SNAPSHOT_ROOT}/tickers.json` };
      if (segments.length === 2) return { path: `${SNAPSHOT_ROOT}/tickers/${ticker}.json` };
      if (segments[2] === 'events') {
        return {
          path: `${SNAPSHOT_ROOT}/tickers/${ticker}/events.json`,
          chartDays: Number(params.get('days')) || 180,
        };
      }
      // Anything else under /tickers/:ticker/... (e.g. the unused /chart
      // endpoint) is not a path the exporter writes — fall through to the
      // default case below rather than guessing.
      return { path: pathname };
    }

    default:
      // Not a path the exporter writes (e.g. the unused /tickers/:t/chart) —
      // fall through unchanged and let it 404 rather than guess at a shape.
      return { path: pathname };
  }
}
