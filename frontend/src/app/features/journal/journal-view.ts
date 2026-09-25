import { DatePipe, NgTemplateOutlet } from '@angular/common';
import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';

import { JourneyEntry } from '../../core/models/api.models';
import { AgentService } from '../../core/services/agent.service';
import { DigestService } from '../../core/services/digest.service';
import { JournalBlock, parseJournalEntry, summariseEntry } from '../../shared/journal-entry';

/**
 * What happened, in the app's own words: the week in summary, then every day
 * the agent has run, grouped by month on the same timeline the Decisions
 * page uses.
 *
 * Every sentence in both comes from a trade, a charge, a decision pass or a
 * graded signal, so neither can drift from the book. The half the app cannot
 * write — why *we* changed something — lives in JOURNEY.md and is not shown
 * here.
 *
 * A journal entry already is one calendar day, so this is the same
 * month-then-day timeline as decisions-view.ts one level shallower: a day's
 * row holds one card, not a list of them.
 */
@Component({
  selector: 'app-journal-view',
  standalone: true,
  imports: [DatePipe, NgTemplateOutlet],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './journal-view.html',
})
export class JournalView {
  private readonly agent = inject(AgentService);
  private readonly digestService = inject(DigestService);

  readonly digest = this.digestService.digest;

  readonly months = signal<string[]>([]);
  readonly loadingMonths = signal(true);
  /** True when the month list itself could not be fetched. A single month
   * failing to load is tracked separately (failedMonths). */
  protected readonly failed = signal(false);

  private readonly entriesByMonth = signal<Map<string, JourneyEntry[]>>(new Map());
  private readonly expandedMonths = signal<Set<string>>(new Set());
  private readonly loadingMonthSet = signal<Set<string>>(new Set());
  private readonly failedMonths = signal<Set<string>>(new Set());
  /** Days a reader has explicitly closed — see decisions-view.ts's
   * collapsedDays for why this tracks closed rather than open. */
  private readonly collapsedDays = signal<Set<string>>(new Set());

  constructor() {
    // Never allowed to fail the page. The digest is a summary of what is
    // below it; a week that cannot be summarised should still show its days.
    void this.digestService.load().catch(() => {});

    void this.agent
      .getJournalMonths()
      .then((months) => {
        this.months.set(months);
        if (months.length) void this.expandMonth(months[0]);
      })
      .catch(() => this.failed.set(true))
      .finally(() => this.loadingMonths.set(false));
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

  entriesFor(month: string): JourneyEntry[] {
    return this.entriesByMonth().get(month) ?? [];
  }

  /** Where the weekly digest belongs among this month's days — the array
   * index (0..length) to insert it before, or null if the digest's own week
   * falls in a different month entirely.
   *
   * `week_start` is not a calendar-week boundary; gather_digest() computes
   * it fresh as "7 days before whenever this was fetched." So its natural
   * spot on the timeline is wherever that rolling cutoff falls among the
   * days actually recorded: right before the first (newest-first) day at or
   * before it, everything newer sitting above the digest and everything
   * older below it. If no day in the month is that old, it belongs at the
   * end — every day here is more recent than the digest's own cutoff. */
  digestInsertIndexFor(month: string): number | null {
    const digest = this.digest();
    if (!digest || digest.week_start.slice(0, 7) !== month) return null;
    const days = this.entriesFor(month);
    const index = days.findIndex((day) => day.date <= digest.week_start);
    return index === -1 ? days.length : index;
  }

  isDayExpanded(date: string): boolean {
    return !this.collapsedDays().has(date);
  }

  toggleDay(date: string): void {
    const next = new Set(this.collapsedDays());
    next.has(date) ? next.delete(date) : next.add(date);
    this.collapsedDays.set(next);
  }

  /** "2026-09" -> "September 2026". */
  monthLabel(month: string): string {
    const [year, monthNumber] = month.split('-').map(Number);
    return new Date(year, monthNumber - 1, 1).toLocaleDateString('en-US', {
      month: 'long',
      year: 'numeric',
    });
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
    if (this.entriesByMonth().has(month)) return;

    const loading = new Set(this.loadingMonthSet());
    loading.add(month);
    this.loadingMonthSet.set(loading);

    try {
      const entries = await this.agent.getJournalEntriesForMonth(month);
      const cache = new Map(this.entriesByMonth());
      cache.set(month, entries);
      this.entriesByMonth.set(cache);

      // Same "show the newest, collapse the rest" rule as
      // decisions-view.ts's own day default: only the newest day in a
      // freshly-loaded month starts open.
      if (entries.length > 1) {
        const collapsed = new Set(this.collapsedDays());
        for (const entry of entries.slice(1)) collapsed.add(entry.date);
        this.collapsedDays.set(collapsed);
      }
    } catch {
      const failedSet = new Set(this.failedMonths());
      failedSet.add(month);
      this.failedMonths.set(failedSet);
    } finally {
      const done = new Set(this.loadingMonthSet());
      done.delete(month);
      this.loadingMonthSet.set(done);
    }
  }

  rate(passes: number, total: number): string {
    return total ? `${passes}/${total} (${Math.round((passes / total) * 100)}%)` : 'n/a';
  }

  /** Parsed once per entry, not once per change-detection pass. Keyed on the
   * entry object itself, so a month refetched from the API parses afresh. */
  private readonly parsed = new WeakMap<JourneyEntry, JournalBlock[]>();

  /** The day's markdown as the parts it is written in. The `##` date heading
   * is dropped along the way: the day's own row already shows that date, and
   * printing both reads as a stutter. */
  blocks(entry: JourneyEntry): JournalBlock[] {
    let blocks = this.parsed.get(entry);
    if (!blocks) {
      blocks = parseJournalEntry(entry.markdown);
      this.parsed.set(entry, blocks);
    }
    return blocks;
  }

  /** One line beside a closed day, so a reader can tell which days are
   * worth opening: where the book stood and what happened. Same reason as
   * the Decisions page's own day summary. */
  entrySummary(entry: JourneyEntry): string {
    return summariseEntry(this.blocks(entry));
  }

  /** "COIN: 17 @ $199.64 → $199.21 (-0.2%)" as the ticker and the rest, so
   * the digest's holdings take the same shape as a day's book line. */
  holdingLine(line: string): { ticker: string; rest: string } {
    const at = line.indexOf(': ');
    if (at === -1) return { ticker: line, rest: '' };
    return { ticker: line.slice(0, at), rest: line.slice(at + 2) };
  }

  /** "−0.1%", with the sign in the text. The `.money--pos` and `.money--neg`
   * classes add the arrow; neither colour nor arrow is ever the only thing
   * separating a gain from a loss. */
  signedPct(value: number): string {
    return `${value >= 0 ? '+' : '−'}${Math.abs(value).toFixed(1)}%`;
  }
}
