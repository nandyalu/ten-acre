import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  computed,
  inject,
  output,
  signal,
} from '@angular/core';

import { environment } from '../../../environments/environment';
import { PassInProgress as Pass } from '../../core/models/api.models';
import { AgentService } from '../../core/services/agent.service';
import { readerDateTime, timeAgo } from '../../shared/market-time';

/** How often to ask whether a pass is running. A pass takes minutes, and a
 * research order inside one takes several more, so a quarter-minute is fine
 * grained enough without asking the API four times a minute for nothing. */
const POLL_MS = 15_000;

/**
 * The decision pass running now, on the live build only (2026-10-01).
 *
 * Nothing shows between passes. The static public site never asks: it is a
 * record, and this describes the current second — the exporter writes no file
 * for it. Read-only like the rest of the page: it reports a pass, and nothing
 * here can start or stop one.
 *
 * Emits `ended` when a pass it saw running has finished, so the page can fetch
 * the pass it just recorded.
 */
@Component({
  selector: 'app-pass-in-progress',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (pass(); as p) {
      <section class="card" aria-live="polite">
        <div class="card-head">
          <h2>A pass is running now <span class="badge badge-warn">Live</span></h2>
          <p class="hint">
            Started {{ started(p) }}. It shows on this page with its prompt and answer once it ends.
          </p>
        </div>
        <div class="card-body">
          <dl class="meta-grid">
            <div>
              <dt>What it was told on waking</dt>
              <dd>{{ p.woke_because || 'Its own chosen time' }}</dd>
            </div>
            <div>
              <dt>Now</dt>
              <dd>{{ p.turn ? 'Turn ' + p.turn + ': ' : '' }}{{ p.doing }}</dd>
            </div>
            @for (item of research(); track item.ticker) {
              <div>
                <dt>Research on {{ item.ticker }}</dt>
                <dd>Started {{ item.since }}</dd>
              </div>
            }
          </dl>
        </div>
      </section>
    }
  `,
})
export class PassInProgress {
  private readonly agent = inject(AgentService);

  /** The pass running now, or null between passes and when the API does not
   * answer: a failed poll hides the card rather than claiming a pass. */
  readonly pass = signal<Pass | null>(null);
  readonly ended = output<void>();

  readonly research = computed(() =>
    Object.entries(this.pass()?.research ?? {}).map(([ticker, since]) => ({
      ticker,
      since: timeAgo(since),
    })),
  );

  constructor() {
    if (environment.staticSite) return;
    void this.poll();
    const timer = setInterval(() => void this.poll(), POLL_MS);
    inject(DestroyRef).onDestroy(() => clearInterval(timer));
  }

  started(p: Pass): string {
    return p.started_at ? `${readerDateTime(p.started_at)} (${timeAgo(p.started_at)})` : '';
  }

  private async poll(): Promise<void> {
    const was = this.pass();
    let now: Pass | null = null;
    try {
      const reply = await this.agent.getPassInProgress();
      now = reply.running ? reply : null;
    } catch {
      now = null;
    }
    this.pass.set(now);
    if (was && !now) this.ended.emit();
  }
}
