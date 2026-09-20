import { UpperCasePipe } from '@angular/common';
import { Component, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { AgentTrade } from '../../core/models/api.models';
import { EquityChart, EquityPoint } from '../../shared/equity-chart';
import { AgentService } from '../../core/services/agent.service';
import { Term } from '../../shared/glossary/term';

@Component({
  selector: 'app-book-view',
  imports: [RouterLink, UpperCasePipe, EquityChart, Term],
  templateUrl: './book-view.html',
})
export class BookView {
  private readonly agentService = inject(AgentService);
  protected readonly book = this.agentService.book;
  protected readonly trades = this.agentService.trades;
  protected readonly performance = this.agentService.performance;
  protected readonly history = this.agentService.history;

  /** Equity per trading day. Plotted against the budget rather than from zero,
   * so the line crossing its own starting level is the thing you see first —
   * that is the only question this chart answers. */
  protected readonly equityCurve = computed<EquityPoint[]>(() =>
    this.agentService.curve().map((p) => ({ time: p.date, value: p.equity })),
  );

  /** Orders still waiting on the open. Shown apart from the rest because they
   * have moved no money yet — a pending buy has not spent its cash. */
  protected readonly pending = computed(() =>
    this.trades().filter((t) => t.status === 'pending' && !t.is_stop),
  );

  /** Stops and take-profits resting at the broker. Listed apart from pending
   * orders because they are supposed to sit unfilled — that is the job. */
  protected readonly restingStops = computed(() =>
    this.trades().filter((t) => t.status === 'pending' && t.is_stop),
  );

  /** The levels protecting each holding, keyed by ticker. The exits are real
   * orders at the broker and the holdings come from our own ledger, so they
   * arrive on two endpoints and are joined here — which is the point: a
   * holding whose cell is blank has nothing resting under it, and that is
   * worth seeing on the position's own row rather than inferred from the
   * absence of a line in a card further down the page. */
  protected readonly exitLevels = computed(() => {
    const levels = new Map<string, { stop: number | null; target: number | null }>();
    for (const exit of this.restingStops()) {
      const entry = levels.get(exit.ticker) ?? { stop: null, target: null };
      if (exit.exit_kind === 'stop') entry.stop = exit.limit_price;
      if (exit.exit_kind === 'target') entry.target = exit.limit_price;
      levels.set(exit.ticker, entry);
    }
    return levels;
  });

  protected exitLevel(ticker: string, kind: 'stop' | 'target'): number | null {
    return this.exitLevels().get(ticker)?.[kind] ?? null;
  }

  /** Exits resting on something the book does not hold. Normally empty — but
   * on 2026-08-13 a bracket was placed against a position the ledger had no
   * row for, and nothing on the page would have shown it. Now that the levels
   * sit on the holding's own row, this is the only case the list still has to
   * cover, so it lists that case and nothing else. */
  protected readonly unmatchedExits = computed(() => {
    const held = new Set(this.book()?.holdings.map((h) => h.ticker) ?? []);
    return this.restingStops().filter((t) => !held.has(t.ticker));
  });

  /** True when the page's own data could not be fetched. Distinct from "there
   * is nothing yet", which is a real answer — a skeleton that never resolves
   * tells the reader nothing and looks broken. */
  protected readonly failed = signal(false);

  constructor() {
    void this.agentService.load().catch(() => this.failed.set(true));
  }

  /** Signed percentage against the budget, so the row reads the same way as
   * the equity tile above it. */
  protected returnPct(equity: number, budget: number): string {
    if (!budget) return '—';
    const pct = (equity / budget - 1) * 100;
    return `${pct >= 0 ? '+' : ''}${pct.toFixed(1)}%`;
  }

  /** Date and time, trimmed to the minute. Empty for a position still open —
   * a blank exit is the signal that it has not been sold. */
  protected when(value: string | null): string {
    return value ? value.replace('T', ' ').slice(0, 16) : '—';
  }

  protected money(value: number | null, digits = 2): string {
    if (value === null) return '—';
    return `$${value.toLocaleString('en-US', {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    })}`;
  }

  /** A gain or a loss, with its sign in the text. The `.money--pos` and
   * `.money--neg` classes add the arrow; neither the colour nor the arrow is
   * ever the only thing distinguishing the two. */
  protected signed(value: number | null): string {
    if (value === null) return '—';
    const abs = Math.abs(value).toLocaleString('en-US', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
    return `${value >= 0 ? '+$' : '−$'}${abs}`;
  }

  protected signedPct(value: number | null): string {
    if (value === null) return '—';
    return `${value >= 0 ? '+' : '−'}${Math.abs(value).toFixed(1)}%`;
  }

  protected whenPlaced(trade: AgentTrade): string {
    return (trade.filled_at ?? trade.placed_at).replace('T', ' ').slice(0, 16);
  }
}
