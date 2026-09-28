import { TestBed } from '@angular/core/testing';

import { AgentMemoryNote } from '../../core/models/api.models';
import { AgentService } from '../../core/services/agent.service';
import { MemoryPanel } from './memory-panel';

class AgentServiceStub {
  notes: AgentMemoryNote[] = [];
  fail = false;

  async getMemory(): Promise<AgentMemoryNote[]> {
    if (this.fail) throw new Error('network error');
    return this.notes;
  }
}

describe('MemoryPanel', () => {
  let service: AgentServiceStub;

  beforeEach(async () => {
    service = new AgentServiceStub();
    await TestBed.configureTestingModule({
      imports: [MemoryPanel],
      providers: [{ provide: AgentService, useValue: service }],
    }).compileComponents();
  });

  async function render(): Promise<HTMLElement> {
    const fixture = TestBed.createComponent(MemoryPanel);
    await fixture.whenStable();
    return fixture.nativeElement as HTMLElement;
  }

  it('says the agent holds no memory rather than rendering nothing', async () => {
    const el = await render();
    expect(el.textContent).toContain('It holds no memory notes now.');
  });

  it('shows every note in order, with where and when it was written', async () => {
    service.notes = [
      { text: 'An old note.', written: null, source: null },
      { text: 'Watch cash for NVDA.', written: '2026-09-21', source: 'pass' },
      { text: 'Two chip names is one too many.', written: '2026-09-25', source: 'reflection' },
    ];
    const el = await render();
    const items = Array.from(el.querySelectorAll('li')).map((li) =>
      Array.from(li.querySelectorAll('p')).map((p) => p.textContent?.trim()),
    );
    expect(items).toEqual([
      ['An old note.'],
      ['Watch cash for NVDA.', 'Written 2026-09-21, from a decision pass'],
      ['Two chip names is one too many.', 'Written 2026-09-25, from the evening review'],
    ]);
  });

  it('says the notes could not be read when the fetch fails', async () => {
    service.fail = true;
    const el = await render();
    expect(el.textContent).toContain('The memory notes could not be read.');
  });
});
