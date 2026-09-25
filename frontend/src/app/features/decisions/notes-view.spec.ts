import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import { AgentNote } from '../../core/models/api.models';
import { AgentService } from '../../core/services/agent.service';
import { NotesView } from './notes-view';

function note(over: Partial<AgentNote> = {}): AgentNote {
  return {
    id: 1,
    ran_at: '2026-09-03T13:35:00Z',
    reason: 'I cannot see sector data.',
    ...over,
  };
}

class AgentServiceStub {
  notes: AgentNote[] = [];
  fail = false;

  async getNotes(): Promise<AgentNote[]> {
    if (this.fail) throw new Error('network error');
    return this.notes;
  }
}

describe('NotesView', () => {
  let service: AgentServiceStub;

  beforeEach(async () => {
    service = new AgentServiceStub();
    await TestBed.configureTestingModule({
      imports: [NotesView],
      providers: [{ provide: AgentService, useValue: service }, provideRouter([])],
    }).compileComponents();
  });

  async function render(): Promise<HTMLElement> {
    const fixture = TestBed.createComponent(NotesView);
    await fixture.whenStable();
    return fixture.nativeElement as HTMLElement;
  }

  it('says there are no notes yet, rather than rendering nothing', async () => {
    const el = await render();
    expect(el.querySelector('.empty')?.textContent).toContain('No notes yet');
  });

  it('shows a note with its reason', async () => {
    service.notes = [note({ reason: 'I need a position-size cap.' })];
    const el = await render();
    expect(el.querySelector('.agent-note-text')?.textContent).toContain(
      'I need a position-size cap.',
    );
  });

  it('shows every note, not just the first', async () => {
    service.notes = [note({ id: 1, reason: 'first' }), note({ id: 2, reason: 'second' })];
    const el = await render();
    const texts = Array.from(el.querySelectorAll('.agent-note-text')).map((n) => n.textContent);
    expect(texts).toEqual(['first', 'second']);
  });

  it('says the notes could not be read when the fetch fails', async () => {
    service.fail = true;
    const el = await render();
    expect(el.querySelector('.empty')?.textContent).toContain('could not be read');
  });

  it('labels a note from the evening review, with its kind, its pass and what it would have done', async () => {
    /** 2026-09-24: the review sends notes too, and each names the pass it
     * points at and the kind of gap, which a pass note never could. */
    service.notes = [
      note({
        reason: 'I could not see the sector of any holding.',
        source: 'review',
        kind: 'missing_information',
        pass_id: 42,
        would_have_done: 'trimmed the second chip name.',
      }),
    ];

    const item = (await render()).querySelector('.agent-note');

    expect(item?.querySelector('.agent-note-label')?.textContent).toContain(
      'from the evening review',
    );
    expect(item?.querySelector('.agent-note-label')?.textContent).toContain('missing information');
    expect(item?.querySelector('.agent-note-label')?.textContent).toContain('pass 42');
    expect(item?.textContent).toContain('It would have trimmed the second chip name.');
  });

  it('says nothing about a source on a pass note', async () => {
    // A snapshot written before 2026-09-24 has no `source` key at all, and
    // a pass note written since says `pass`. Neither gets a label.
    service.notes = [note(), note({ source: 'pass', kind: null, pass_id: null })];

    const labels = Array.from((await render()).querySelectorAll('.agent-note-label')).map(
      (l) => l.textContent ?? '',
    );

    expect(labels).toHaveLength(2);
    for (const label of labels) expect(label).not.toContain('evening review');
  });

  it('shows two notes that share an id', async () => {
    // Every note a review sends carries the review's id, and a pass can
    // leave two. Tracking by id would collapse them or throw.
    service.notes = [
      note({ id: 5, reason: 'first', source: 'review' }),
      note({ id: 5, reason: 'second', source: 'review' }),
    ];

    const texts = Array.from((await render()).querySelectorAll('.agent-note-text')).map(
      (n) => n.textContent,
    );

    expect(texts).toEqual(['first', 'second']);
  });
});
