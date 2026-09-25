import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { AgentNote } from '../../core/models/api.models';
import { AgentService } from '../../core/services/agent.service';
import { Term } from '../../shared/glossary/term';
import { readerDateTime } from '../../shared/market-time';
import { noteKindLabel } from './reflection-card';

/**
 * Every note the agent has ever left, in one place.
 *
 * A note rides inside a decision pass's `orders`, so the Decisions page
 * already shows one wherever it happens to fall on that page's timeline —
 * but a note is not a decision about a ticker, it is the agent addressing
 * whoever maintains it, and CLAUDE.md calls it primary evidence about the
 * experiment's gaps. Reading "everything it has ever asked for" meant
 * opening every month on the Decisions page and picking the note cards out
 * by eye. This pulls them into their own list instead.
 *
 * Since 2026-09-24 the evening review sends notes too: the same message, to
 * the same reader, from the other place the agent can speak from. A review
 * note also says which pass it points at and what kind of gap it names, and
 * the list says which of the two places each note came from.
 *
 * The list is tracked by position, not by `id`. Two notes can share an id:
 * a pass can leave two, and every note a review sends carries the review's
 * own id.
 *
 * A drill-down from Decisions rather than a seventh link in the main nav —
 * the nav is deliberately six curated destinations (see app.ts), and a note
 * is a detail of a decision pass the same way a ticker or an analysis is a
 * detail of a research pass.
 *
 * One flat fetch, not the Decisions page's month-by-month paging: a note is
 * a short line of text, so even the whole history of them is a small
 * payload, unlike a month of full prompts and responses.
 */
@Component({
  selector: 'app-notes-view',
  standalone: true,
  imports: [RouterLink, Term],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './notes-view.html',
})
export class NotesView {
  private readonly agent = inject(AgentService);

  readonly notes = signal<AgentNote[]>([]);
  readonly loading = signal(true);
  readonly failed = signal(false);

  constructor() {
    void this.agent
      .getNotes()
      .then((notes) => this.notes.set(notes))
      .catch(() => this.failed.set(true))
      .finally(() => this.loading.set(false));
  }

  when(instant: string): string {
    return readerDateTime(instant);
  }

  /** Whether the evening review sent this note. Absent `source` means a pass
   * note: a snapshot written before 2026-09-24 has no such key. */
  fromReview(note: AgentNote): boolean {
    return note.source === 'review';
  }

  kindLabel(note: AgentNote): string {
    return noteKindLabel(note.kind);
  }
}
