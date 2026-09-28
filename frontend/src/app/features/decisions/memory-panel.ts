import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';

import { AgentMemoryNote } from '../../core/models/api.models';
import { AgentService } from '../../core/services/agent.service';
import { Term } from '../../shared/glossary/term';

/**
 * The memory notes the agent holds now: the list its next prompt shows.
 *
 * Each pass's own memory orders show inside that pass on the Decisions page.
 * This shows what they left behind, which no pass shows on its own: a note
 * written in one pass and cleared in another is two lines on two different
 * days. Until 2026-09-28 only a database query could show this list.
 *
 * Its own fetch, and its own failure: a missing list costs the page this
 * card and nothing else.
 */
@Component({
  selector: 'app-memory-panel',
  standalone: true,
  imports: [Term],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './memory-panel.html',
})
export class MemoryPanel {
  private readonly agent = inject(AgentService);

  readonly notes = signal<AgentMemoryNote[]>([]);
  readonly loading = signal(true);
  readonly failed = signal(false);

  constructor() {
    void this.agent
      .getMemory()
      .then((notes) => this.notes.set(notes))
      .catch(() => this.failed.set(true))
      .finally(() => this.loading.set(false));
  }

  /** Where the note came from and when. Empty on a note stored before
   * 2026-09-24, which kept neither. */
  origin(note: AgentMemoryNote): string {
    const from =
      note.source === 'reflection'
        ? 'from the evening review'
        : note.source === 'pass'
          ? 'from a decision pass'
          : '';
    return [note.written ? `Written ${note.written}` : '', from].filter(Boolean).join(', ');
  }
}
