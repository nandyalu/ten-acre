import { Component, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { AgentEvent, AgentHolding } from '../../core/models/api.models';
import { AgentService } from '../../core/services/agent.service';
import { RegimeService } from '../../core/services/regime.service';
import { ScorecardService } from '../../core/services/scorecard.service';
import { EquityChart, EquityPoint } from '../../shared/equity-chart';
import { ClockTime, marketTime, readerTime } from '../../shared/market-time';
import { dayNumber, startedOn } from '../../shared/experiment';
import { charging, researchPriceLabel } from '../../shared/research-price';

/** One row of the two-day timeline. */
interface Beat {
  /** Stable across renders. Two passes can carry the same words — "nothing"
   * twice in a morning — and tracking by text collapses them into one row. */
  key: string;
  t: ClockTime;
  text: string;
  done: boolean;
  act: boolean;
  /** The agent chose this moment. The fixed jobs did not. */
  agent: boolean;
}

/**
 * The front door.
 *
 * Written for someone who has never heard of this. It gives the premise before
 * it gives a number, because a return of +2.4% means nothing until you know
 * what it is a return on and who chose the trades.
 *
 * The order is the design: what this is, what it may and may not do, that no
 * real money is involved, then the money, then what it did most recently in
 * its own words, then whether any of it is working.
 *
 * A visitor who stops after the first screen still leaves knowing what was
 * being tried. That is why the premise is prose and not a stat tile.
 */
@Component({
  selector: 'app-experiment-view',
  imports: [RouterLink, EquityChart],
  templateUrl: './experiment-view.html',
})
export class ExperimentView {
  /** The charge this deployment actually makes, and whether it makes one.
   * Written into the template as "$0.05" until 2026-09-10. */
  protected readonly researchPriceLabel = researchPriceLabel;
  protected readonly charging = charging;
  private readonly agentService = inject(AgentService);
  private readonly regimeService = inject(RegimeService);
  private readonly scorecardService = inject(ScorecardService);

  protected readonly book = this.agentService.book;
  protected readonly curve = this.agentService.curve;
  protected readonly regime = this.regimeService.regime;
  protected readonly scorecard = this.scorecardService.scorecard;
  protected readonly events = this.agentService.events;

  protected readonly loading = signal(true);
  protected readonly failed = signal(false);

  protected readonly curvePoints = computed<EquityPoint[]>(() =>
    this.curve().map((p) => ({ time: p.date, value: p.equity })),
  );

  /** Biggest position first. Someone glancing at the table wants to know where
   * the money actually is, not which ticker sorts first. */
  protected readonly holdings = computed<AgentHolding[]>(() =>
    [...(this.book()?.holdings ?? [])].sort(
      (a, b) => (b.market_value ?? b.cost_basis) - (a.market_value ?? a.cost_basis),
    ),
  );

  /** Which day of the experiment this is, and the date it began.
   *
   * Counted from the start date rather than from the first fill. A day the
   * agent chose to do nothing is still a day of the experiment, and a counter
   * that waits for the first purchase hides exactly those days. */
  protected readonly dayNumber = dayNumber();
  protected readonly startedOn = startedOn();

  /** The most recent pass that did something.
   *
   * A quiet day is the common case, and leading with "it did nothing" three
   * days running says less about the experiment than the last real decision
   * does. The date on it keeps the gap visible either way. */
  protected readonly lastAction = computed<AgentEvent | null>(
    () => this.events().find((e) => e.orders.length > 0) ?? this.events()[0] ?? null,
  );

  protected readonly graded = computed(() => {
    const s = this.scorecard();
    if (!s || s.resolved === 0) return null;
    return {
      resolved: s.resolved,
      passes: s.passes,
      vsBenchmark: s.vs_benchmark_passes,
      vsBenchmarkTotal: s.vs_benchmark_total,
      enough: s.resolved >= 20,
    };
  });

  private readonly now = new Date();

  /** The trading day an instant falls in, as YYYY-MM-DD in New York.
   *
   * Grouped by the market's day rather than UTC, because the agent picks its
   * own times now and may wake in the evening. A pass at 8pm in New York is
   * past midnight UTC, and a UTC grouping would file it under the next
   * trading day — a day whose prices it never saw.
   */
  private marketDay(d: Date): string {
    return new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York' }).format(d);
  }

  private isTradingDay(d: Date): boolean {
    const weekday = new Intl.DateTimeFormat('en-US', {
      weekday: 'short',
      timeZone: 'America/New_York',
    }).format(d);
    return weekday !== 'Sat' && weekday !== 'Sun';
  }

  /** The jobs the app runs on a clock, whatever the agent decides.
   *
   * **"It decides" used to sit here at 13:35 and no longer does.** That pass
   * was removed on 2026-09-05: the agent names every one of its own times now,
   * so its passes are read from the record below rather than predicted here.
   *
   * **The 11:00 "morning sweep" row is gone too, as of 2026-09-08.** It used
   * to analyse the whole watchlist automatically, whether the agent wanted
   * the answer or not; that forced pass is deleted, not just idle, so
   * showing it here would promise something that no longer happens. The
   * agent's own research passes are in the record below instead.
   */
  private fixedBeats(on: Date): Beat[] {
    // **Every one of these returns early at the weekend**, so listing them on a
    // Saturday promises four things that will not happen. That is the same
    // fault as the 13:35 row this replaced, in a quieter form.
    if (!this.isTradingDay(on)) return [];
    const rows: [number, number, string][] = [
      [12, 45, 'The market regime is read'],
      [13, 0, 'Anything reporting earnings gets a fresh look'],
      [21, 30, 'Signals are graded, then the journal is written'],
    ];
    return rows.map(([h, m, text]) => {
      const t = marketTime(h, m, on);
      return {
        key: `fixed-${h}-${m}`,
        t,
        text,
        act: false,
        agent: false,
        done: t.instant.getTime() <= this.now.getTime(),
      };
    });
  }

  /** What one pass did, in a few words. */
  private summarise(event: AgentEvent): string {
    if (event.skipped) return event.skipped;
    const orders = (event.orders ?? []).filter((o) => o.side !== 'note');
    if (!orders.length) return 'It looked, and did nothing';
    const seen = new Map<string, string[]>();
    for (const o of orders) {
      const list = seen.get(o.side) ?? [];
      if (o.ticker && !list.includes(o.ticker)) list.push(o.ticker);
      seen.set(o.side, list);
    }
    const verb: Record<string, string> = {
      buy: 'Bought',
      sell: 'Sold',
      adjust: 'Moved the exits on',
      research: 'Commissioned',
      untrack: 'Dropped',
    };
    return [...seen]
      .map(([side, tickers]) => `${verb[side] ?? side} ${tickers.join(', ')}`)
      .join('. ');
  }

  /** Every pass the agent ran on this trading day, at the times it chose. */
  private agentBeats(day: string): Beat[] {
    return (this.events() ?? [])
      .filter((e) => e.ran_at && this.marketDay(new Date(e.ran_at)) === day)
      .map((e) => ({
        key: `run-${e.id}`,
        t: readerTime(e.ran_at),
        text: this.summarise(e),
        act: !e.skipped && (e.orders ?? []).some((o) => o.side !== 'note'),
        agent: true,
        done: true,
      }));
  }

  /** The one wakeup the agent has asked for and not yet had.
   *
   * **The most interesting row on the page**, because it is the only one that
   * is a stated intention rather than a record. Nothing schedules the agent
   * but this, so it is also what says the experiment is still running.
   */
  private pendingBeat(day: string): Beat[] {
    const newest = (this.events() ?? [])[0];
    if (!newest?.next_wakeup) return [];
    const when = new Date(newest.next_wakeup);
    if (when.getTime() <= this.now.getTime()) return [];
    if (this.marketDay(when) !== day) return [];
    return [
      {
        key: 'pending',
        t: readerTime(when),
        text: 'It asked to be woken',
        act: false,
        agent: true,
        done: false,
      },
    ];
  }

  private beats(on: Date): Beat[] {
    const day = this.marketDay(on);
    return [...this.fixedBeats(on), ...this.agentBeats(day), ...this.pendingBeat(day)].sort(
      (a, b) => a.t.instant.getTime() - b.t.instant.getTime(),
    );
  }

  /** The day the second column describes.
   *
   * Today while the market trades, or while the agent has run — it may wake at
   * a weekend now, and a pass it chose to make is worth showing whenever it
   * happened.
   *
   * Otherwise the next trading day. **A closed Saturday holds nothing**: no
   * fixed job runs, and the wakeup the agent has asked for falls on Monday, so
   * a column headed "today" would be empty while the one line that says the
   * experiment is still running sat on a day nobody could see.
   */
  private focusDay(): Date {
    if (this.isTradingDay(this.now)) return this.now;
    if (this.agentBeats(this.marketDay(this.now)).length) return this.now;
    const d = new Date(this.now);
    do {
      d.setUTCDate(d.getUTCDate() + 1);
    } while (!this.isTradingDay(d));
    return d;
  }

  protected readonly today = computed(() => this.beats(this.focusDay()));

  /** The first thing on the focus day that has not happened yet, for the
   * one-line "Next" a narrow screen shows above the money. Null once the
   * day's rows are all done, when there is nothing to say. */
  protected readonly nextBeat = computed<Beat | null>(
    () => this.today().find((beat) => !beat.done) ?? null,
  );

  /** What the second column is: the day in progress, or the one being waited
   * for. The heading has to say which, or a Monday's rows read as today's. */
  protected readonly todayIsNow = computed(
    () => this.marketDay(this.focusDay()) === this.marketDay(this.now),
  );

  /** The previous weekday. Monday looks back to Friday: a timeline whose first
   * half is a closed market says nothing about the experiment. */
  protected readonly previous = computed(() => this.beats(this.previousWeekday()));

  private previousWeekday(): Date {
    const d = new Date(this.now);
    do {
      d.setUTCDate(d.getUTCDate() - 1);
    } while (d.getUTCDay() === 0 || d.getUTCDay() === 6);
    return d;
  }

  /** Named in New York, matching the day the rows are grouped by. Labelling a
   * day in one zone and filling it from another puts a row under the wrong
   * heading for anyone far enough east or west. */
  private dayLabel(d: Date): string {
    return new Intl.DateTimeFormat('en-US', {
      weekday: 'long',
      day: 'numeric',
      month: 'long',
      timeZone: 'America/New_York',
    }).format(d);
  }

  protected readonly previousLabel = computed(() => this.dayLabel(this.previousWeekday()));

  protected readonly todayLabel = computed(() => this.dayLabel(this.focusDay()));

  constructor() {
    void this.load();
  }

  private async load(): Promise<void> {
    try {
      await Promise.all([
        this.agentService.load(),
        this.agentService.loadEvents(20),
        // Neither of these is allowed to fail the page. The regime is context
        // and the scorecard is a summary; the money above them still stands.
        this.regimeService.load().catch(() => {}),
        this.scorecardService.load().catch(() => {}),
      ]);
    } catch {
      this.failed.set(true);
    } finally {
      this.loading.set(false);
    }
  }

  protected clock(t: ClockTime): string {
    return `${t.time} ${t.zone}`;
  }

  protected signed(value: number, digits = 1): string {
    return `${value >= 0 ? '+' : '−'}${Math.abs(value).toFixed(digits)}`;
  }

  protected money(value: number, digits = 0): string {
    return value.toLocaleString('en-US', {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    });
  }

  protected signedMoney(value: number): string {
    return `${value >= 0 ? '+$' : '−$'}${this.money(Math.abs(value), 2)}`;
  }

  protected pct(pnl: number | null, basis: number): string {
    if (pnl === null || !basis) return '—';
    return this.signed((pnl / basis) * 100) + '%';
  }

  protected when(iso: string): string {
    const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000);
    if (days === 0) return 'today';
    if (days === 1) return 'yesterday';
    return `${days} days ago`;
  }
}
