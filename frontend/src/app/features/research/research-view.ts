import { Component, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { Signal } from '../../core/models/api.models';
import { SignalsService } from '../../core/services/signals.service';
import { TickersService } from '../../core/services/tickers.service';
import { WatchlistService } from '../../core/services/watchlist.service';
import { DecisionBadge } from '../../shared/decision-badge';
import { Term } from '../../shared/glossary/term';
import { readerDateKey, readerTime } from '../../shared/market-time';
import { charging, researchPriceLabel } from '../../shared/research-price';

type StatusFilter = '' | 'pending' | 'resolved';

/**
 * What the agent studies, and what each study concluded.
 *
 * This merges two pages that were always one subject. Tickers listed what was
 * being watched; Signals listed the analyses of them. Split, a reader had to
 * hold "what a fresh look on one of these names costs" on one page and
 * "here is what that money bought" on another.
 *
 * Nothing here adds or removes a ticker. The agent commissions research to add
 * a name and untracks to drop one. Since 2026-09-08 nothing here runs on a
 * schedule: the agent decides for itself when a name is worth a fresh $0.05
 * look, holdings included, rather than every tracked name being charged and
 * analysed automatically each morning. The candidate list at the bottom is
 * the same menu it is given to choose from (in its prompt, or, on a Gemini
 * deployment since 2026-09-17, when it asks for it) — it is here to be looked
 * at, not acted on.
 */
@Component({
  selector: 'app-research-view',
  imports: [RouterLink, DecisionBadge, Term],
  templateUrl: './research-view.html',
})
export class ResearchView {
  /** The charge this deployment actually makes, and whether it makes one.
   * Written into the template as "$0.05" until 2026-09-10. */
  protected readonly researchPriceLabel = researchPriceLabel;
  protected readonly charging = charging;
  private readonly tickersService = inject(TickersService);
  private readonly signalsService = inject(SignalsService);
  private readonly watchlistService = inject(WatchlistService);

  protected readonly tickers = this.tickersService.tickers;
  protected readonly loading = this.tickersService.loading;
  protected readonly candidates = this.watchlistService.candidates;
  protected readonly signals = this.signalsService.signals;

  protected readonly statusFilter = signal<StatusFilter>('');

  /** Newest analysis per ticker is already on the watchlist rows, so the feed
   * below is every analysis in date order — the same name appearing twice is
   * the interesting case, not a duplicate to collapse. */
  protected readonly feed = computed<Signal[]>(() => this.signals());

  /** True when the page's own data could not be fetched. Distinct from "there
   * is nothing yet", which is a real answer — a skeleton that never resolves
   * tells the reader nothing and looks broken. */
  protected readonly failed = signal(false);

  constructor() {
    void this.tickersService.load().catch(() => this.failed.set(true));
    void this.reloadSignals().catch(() => this.failed.set(true));
    // Not awaited with the rest: it calls the screener and is slower, and the
    // page should show what is already tracked first.
    void this.watchlistService.loadCandidates().catch(() => {});
  }

  protected async setFilter(status: StatusFilter): Promise<void> {
    this.statusFilter.set(status);
    await this.reloadSignals();
  }

  private async reloadSignals(): Promise<void> {
    const status = this.statusFilter();
    await this.signalsService.load({ status: status || undefined, limit: 50 });
  }

  protected volumeM(volume: number): string {
    return `${(volume / 1_000_000).toFixed(0)}M`;
  }

  /**
   * The calendar day an analysis ran, on the reader's clock.
   *
   * Taken from `created_at` rather than `signal_date` so the day and the time
   * below it are the same instant in the same zone. Falls back to
   * `signal_date` on a row with no timestamp to recover one from.
   */
  protected analysedDay(s: Signal): string {
    return s.created_at ? readerDateKey(s.created_at) : s.signal_date;
  }

  /**
   * The time of day, or an empty string when the row has no timestamp.
   *
   * **The date alone cannot separate two analyses of one day**, and several a
   * day is the normal case: eight on 2026-09-10, nine on 2026-09-08. Without
   * this a reader saw eight rows all reading 2026-09-10 and no way to tell
   * which came first.
   */
  protected analysedTime(s: Signal): string {
    if (!s.created_at) return '';
    const t = readerTime(s.created_at);
    return `${t.time} ${t.zone}`;
  }
}
