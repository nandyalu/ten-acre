import { DecimalPipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, input, signal } from '@angular/core';

import { AgentReflection, ReflectionNote } from '../../core/models/api.models';
import { readerDateKey, readerDateTime, readerTime } from '../../shared/market-time';
import { CopyButton } from '../../shared/copy-button';

/** A review note's kind, in words: `missing_information` reads as "missing
 * information". Shared with the Notes page, which shows the same notes
 * pulled out of every review. An unknown kind is shown with its underscores
 * replaced rather than dropped, so a value the backend adds later still
 * reads as a kind and not as a hole. */
export function noteKindLabel(kind: string | null | undefined): string {
  return (kind ?? 'other').replace(/_/g, ' ');
}

type Panel = 'prompts' | 'answers' | 'thinking';

/**
 * One evening review: what the agent told whoever maintains it, what it
 * changed in its own notes, and the words that produced both.
 *
 * Modelled on `DecisionCard`: a bordered card with the report always visible
 * and the two prompts, the two answers and the thinking behind the same kind
 * of disclosure, since a review prompt carries every pass of the day and is
 * longer than any one of them.
 */
@Component({
  selector: 'app-reflection-card',
  standalone: true,
  imports: [DecimalPipe, CopyButton],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './reflection-card.html',
})
export class ReflectionCard {
  readonly review = input.required<AgentReflection>();

  /** Which panels are open on this one card. */
  private readonly open = signal<Set<Panel>>(new Set());

  when(instant: string): string {
    return readerDateTime(instant);
  }

  /** Where the review started reading. Time only when that is the same day
   * as the review, the day as well when it is not. The last review is
   * usually the evening before, so the day is nearly always part of it. */
  sinceLabel(review: AgentReflection): string {
    if (readerDateKey(review.since) === readerDateKey(review.ran_at)) {
      const t = readerTime(review.since);
      return `${t.time} ${t.zone}`;
    }
    return readerDateTime(review.since);
  }

  kindLabel(kind: string | null | undefined): string {
    return noteKindLabel(kind);
  }

  /** Defaulted rather than read straight off the review, for the reason the
   * decision card defaults `failed`: a browser holding a cached bundle can
   * outlive the deployment that served it, and a page that throws in that
   * window is worse than one missing a list. */
  notesIn(review: AgentReflection): ReflectionNote[] {
    return review.notes ?? [];
  }

  appliedIn(review: AgentReflection): string[] {
    return review.applied ?? [];
  }

  isOpen(which: Panel): boolean {
    return this.open().has(which);
  }

  toggle(which: Panel): void {
    const next = new Set(this.open());
    next.has(which) ? next.delete(which) : next.add(which);
    this.open.set(next);
  }

  /** A review that never asked has no words to show. */
  asked(review: AgentReflection): boolean {
    return !!review.prompt;
  }
}
