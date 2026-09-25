import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { AgentEvent, AgentReflection } from '../../core/models/api.models';
import { AgentService } from '../../core/services/agent.service';
import { Term } from '../../shared/glossary/term';
import { readerDateKey, readerDateLabel } from '../../shared/market-time';
import { DecisionCard } from './decision-card';
import { ReflectionCard } from './reflection-card';

/** One calendar day within a month: its evening review, when it had one,
 * and its passes, newest first. */
export interface DecisionDay {
  key: string;
  label: string;
  /** The day's evening reviews. One a day at most, in practice. */
  reviews: AgentReflection[];
  events: AgentEvent[];
}

/** "YYYY-MM" of the UTC instant. The backend files a pass into a month by
 * its UTC time (`db._month_of`), so a review is filed the same way, and a
 * review sits in the same month as the passes it read. */
function utcMonth(instant: string): string {
  return new Date(instant).toISOString().slice(0, 7);
}

/**
 * Every decision pass, grouped by month.
 *
 * The book shows what the agent holds. This shows how it decided, which is a
 * different question and the one that is hard to reconstruct later:
 * behaviour here is mostly prompt, so a month of runs across three prompt
 * revisions cannot be told apart without the words each run actually saw.
 *
 * One dot per month with a decision pass, on the timeline. The newest month
 * is expanded (and fetched) up front; every other month stays collapsed and
 * unfetched until a reader opens it — a page covering months of daily passes
 * would otherwise ship its entire history on every visit. This is why the
 * data behind it is split the same way: snapshot_export.py writes one
 * `agent_events_<YYYYMM>.json` per month, not one file with everything in it.
 */
@Component({
  selector: 'app-decisions-view',
  standalone: true,
  imports: [DecisionCard, ReflectionCard, RouterLink, Term],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './decisions-view.html',
})
export class DecisionsView {
  private readonly agent = inject(AgentService);

  /** Every month with a decision pass or an evening review, newest first —
   * one dot each. */
  readonly months = signal<string[]>([]);
  readonly loadingMonths = signal(true);
  /** True when the month list itself could not be fetched. A single month
   * failing to load is tracked separately (failedMonths) — that is not the
   * whole page being down. */
  protected readonly failed = signal(false);

  /** Every evening review, fetched once with the month list. One flat list
   * rather than a per-month cache: a review is short beside a pass, and the
   * page files each into the month it ran in itself. */
  private readonly reviews = signal<AgentReflection[]>([]);
  /** True when the reviews could not be fetched. The passes still show; the
   * page says the reviews are missing rather than going down with them. */
  protected readonly reviewsFailed = signal(false);

  private readonly eventsByMonth = signal<Map<string, AgentEvent[]>>(new Map());
  private readonly expandedMonths = signal<Set<string>>(new Set());
  private readonly loadingMonthSet = signal<Set<string>>(new Set());
  private readonly failedMonths = signal<Set<string>>(new Set());
  /** Days a reader has explicitly closed. A day's events are already fetched
   * as part of its month, so — unlike a month — there is nothing to load on
   * expand; a day defaults to open the first time its month does, and this
   * set only ever records the ones someone chose to close. */
  private readonly collapsedDays = signal<Set<string>>(new Set());

  constructor() {
    // The reviews ride alongside the month list rather than after it: a
    // review's month may hold no pass, and the timeline needs to know that
    // before it draws its dots. A failed reviews fetch is caught here on its
    // own, so it costs the page its reviews and nothing else.
    const reviews = this.agent.getReflections().catch((): AgentReflection[] => {
      this.reviewsFailed.set(true);
      return [];
    });
    void Promise.all([this.agent.getEventMonths(), reviews])
      .then(([months, reviews]) => {
        this.reviews.set(reviews);
        const all = this.withReviewMonths(months, reviews);
        this.months.set(all);
        // The newest month with any data at all is treated as "current" —
        // simpler and more robust than comparing against today's calendar
        // month, which would still show an empty section on the first day
        // of a new month with nothing recorded in it yet.
        if (all.length) void this.expandMonth(all[0]);
      })
      .catch(() => this.failed.set(true))
      .finally(() => this.loadingMonths.set(false));
  }

  /** The month list with every review's month in it, newest first. A review
   * on a day the agent skipped every pass, or the first record of a fresh
   * month, would otherwise have no dot to sit under. */
  private withReviewMonths(months: string[], reviews: AgentReflection[]): string[] {
    const all = new Set(months);
    for (const review of reviews) all.add(utcMonth(review.ran_at));
    return Array.from(all).sort().reverse();
  }

  /** One month's reviews, newest first, since the API sends them that way. */
  reviewsFor(month: string): AgentReflection[] {
    return this.reviews().filter((review) => utcMonth(review.ran_at) === month);
  }

  isExpanded(month: string): boolean {
    return this.expandedMonths().has(month);
  }

  isLoadingMonth(month: string): boolean {
    return this.loadingMonthSet().has(month);
  }

  isMonthFailed(month: string): boolean {
    return this.failedMonths().has(month);
  }

  eventsFor(month: string): AgentEvent[] {
    return this.eventsByMonth().get(month) ?? [];
  }

  /** A month's passes and reviews, split into same-day groups — newest day
   * first, and newest pass first within each day, since eventsFor() and
   * reviewsFor() are each already ordered that way and grouping only ever
   * appends into whichever day an item belongs to. Sorted by key at the end
   * because a review can open a day no pass did, so insertion order alone
   * no longer says which day is newest. */
  daysFor(month: string): DecisionDay[] {
    const groups = new Map<string, DecisionDay>();
    const dayOf = (instant: string): DecisionDay => {
      const key = readerDateKey(instant);
      let day = groups.get(key);
      if (!day) {
        day = { key, label: readerDateLabel(instant), reviews: [], events: [] };
        groups.set(key, day);
      }
      return day;
    };
    for (const event of this.eventsFor(month)) dayOf(event.ran_at).events.push(event);
    for (const review of this.reviewsFor(month)) dayOf(review.ran_at).reviews.push(review);
    return Array.from(groups.values()).sort((a, b) => (a.key < b.key ? 1 : -1));
  }

  /** One line beside a collapsed day, so a reader can tell which days are
   * worth opening without opening each: how many passes, what they did, and
   * whether the day was reviewed. Fifteen closed days that all read the same
   * were a ladder with no rungs. Three actions at most, then a count. */
  daySummary(day: DecisionDay): string {
    const passes = day.events.length;
    const did = Array.from(
      new Set(
        day.events.flatMap((event) =>
          event.orders
            .filter((o) => o.side !== 'note' && o.side !== 'memory')
            .map((o) => `${o.side} ${o.ticker}`.trim()),
        ),
      ),
    );
    const parts: string[] = [];
    if (passes) {
      parts.push(`${passes} ${passes === 1 ? 'pass' : 'passes'}`);
      const shown = did.slice(0, 3).join(', ');
      parts.push(did.length > 3 ? `${shown} +${did.length - 3}` : shown || 'nothing');
    }
    if (day.reviews.length) parts.push(passes ? 'reviewed' : 'evening review only');
    return parts.join(' · ');
  }

  /** "2026-09" -> "September 2026". */
  monthLabel(month: string): string {
    const [year, monthNumber] = month.split('-').map(Number);
    return new Date(year, monthNumber - 1, 1).toLocaleDateString('en-US', {
      month: 'long',
      year: 'numeric',
    });
  }

  isDayExpanded(dayKey: string): boolean {
    return !this.collapsedDays().has(dayKey);
  }

  toggleDay(dayKey: string): void {
    const next = new Set(this.collapsedDays());
    next.has(dayKey) ? next.delete(dayKey) : next.add(dayKey);
    this.collapsedDays.set(next);
  }

  toggleMonth(month: string): void {
    if (this.isExpanded(month)) {
      const next = new Set(this.expandedMonths());
      next.delete(month);
      this.expandedMonths.set(next);
      return;
    }
    void this.expandMonth(month);
  }

  private async expandMonth(month: string): Promise<void> {
    const expanded = new Set(this.expandedMonths());
    expanded.add(month);
    this.expandedMonths.set(expanded);

    // Already cached from an earlier expand — nothing to fetch.
    if (this.eventsByMonth().has(month)) return;

    const loading = new Set(this.loadingMonthSet());
    loading.add(month);
    this.loadingMonthSet.set(loading);

    try {
      const events = await this.agent.getEventsForMonth(month);
      const cache = new Map(this.eventsByMonth());
      cache.set(month, events);
      this.eventsByMonth.set(cache);

      // Same "show the newest, collapse the rest" rule the months
      // themselves use, one level deeper: only the newest day in a
      // freshly-loaded month starts open. daysFor() reads eventsByMonth(),
      // which the set() above already updated, so this sees the real days.
      const days = this.daysFor(month);
      if (days.length > 1) {
        const collapsed = new Set(this.collapsedDays());
        for (const day of days.slice(1)) collapsed.add(day.key);
        this.collapsedDays.set(collapsed);
      }
    } catch {
      const failed = new Set(this.failedMonths());
      failed.add(month);
      this.failedMonths.set(failed);
    } finally {
      const done = new Set(this.loadingMonthSet());
      done.delete(month);
      this.loadingMonthSet.set(done);
    }
  }
}
