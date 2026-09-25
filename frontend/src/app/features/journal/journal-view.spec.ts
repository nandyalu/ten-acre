import { TestBed } from '@angular/core/testing';
import { signal } from '@angular/core';

import { Digest, JourneyEntry } from '../../core/models/api.models';
import { AgentService } from '../../core/services/agent.service';
import { DigestService } from '../../core/services/digest.service';
import { JournalView } from './journal-view';

/** The newest month is expanded (and fetched) as soon as the page loads —
 * every test seeds exactly that one month, matching decisions-view.spec.ts's
 * own convention for the same reason. */
class AgentServiceStub {
  months: string[] = ['2026-09'];
  entriesByMonth: Record<string, JourneyEntry[]> = { '2026-09': [] };

  async getJournalMonths(): Promise<string[]> {
    return this.months;
  }

  async getJournalEntriesForMonth(month: string): Promise<JourneyEntry[]> {
    return this.entriesByMonth[month] ?? [];
  }
}

function digest(over: Partial<Digest> = {}): Digest {
  return {
    week_start: '2026-09-01',
    resolved: [],
    new_signals: [],
    alerts: [],
    win_rate_30d: [0, 0],
    win_rate_all: [0, 0],
    book_lines: [],
    ...over,
  };
}

class DigestServiceStub {
  readonly digest = signal<Digest | null>(null);
  async load(): Promise<void> {}
}

describe('JournalView', () => {
  let service: AgentServiceStub;
  let digestService: DigestServiceStub;

  beforeEach(async () => {
    service = new AgentServiceStub();
    digestService = new DigestServiceStub();
    await TestBed.configureTestingModule({
      imports: [JournalView],
      providers: [
        { provide: AgentService, useValue: service },
        { provide: DigestService, useValue: digestService },
      ],
    }).compileComponents();
  });

  async function render(): Promise<HTMLElement> {
    const fixture = TestBed.createComponent(JournalView);
    await fixture.whenStable();
    return fixture.nativeElement as HTMLElement;
  }

  function clickButtonContaining(el: HTMLElement, text: string): void {
    const button = Array.from(el.querySelectorAll('button')).find((b) =>
      b.textContent?.includes(text),
    );
    button?.click();
  }

  it('shows the day and its generated note', async () => {
    service.entriesByMonth['2026-09'] = [
      { date: '2026-09-08', markdown: '## 2026-09-08\n- Bought 260 SMCI at $38.49.' },
    ];

    const el = await render();

    expect(el.textContent).toContain('Bought 260 SMCI');
  });

  it('drops the markdown heading, because the day row already shows the date', async () => {
    // Printing both reads as a stutter.
    service.entriesByMonth['2026-09'] = [
      { date: '2026-09-08', markdown: '## 2026-09-08\n- Bought 260 SMCI.' },
    ];

    const text = (await render()).textContent ?? '';

    expect(text).not.toContain('##');
    expect(text).not.toContain('2026-09-08');
  });

  it('renders the day as its parts rather than as raw markdown', async () => {
    service.entriesByMonth['2026-09'] = [
      {
        date: '2026-09-08',
        markdown:
          '## 2026-09-08\nBook $9,987.96 (-0.1% against the starting balance).\n' +
          '- Bought 10 COIN at $195.58\n\n> Holding COIN into the open.\n\n**First position opened: COIN.**',
      },
    ];

    const el = await render();
    const text = el.textContent ?? '';

    expect(el.querySelector('.journal-events li')?.textContent).toContain('Bought 10 COIN');
    expect(el.querySelector('blockquote')?.textContent).toContain('Holding COIN');
    expect(el.querySelector('.journal-milestone')?.textContent).toContain('First position opened');
    expect(el.querySelector('.journal-book .money--neg')?.textContent).toContain('−0.1%');
    expect(text).not.toContain('**');
    expect(text).not.toContain('> ');
  });

  it('keeps a day on which nothing happened', async () => {
    // "Nothing happened" is a fact about the day, not a gap in the record.
    service.entriesByMonth['2026-09'] = [
      { date: '2026-09-08', markdown: '## 2026-09-08\n**2026-09-08** — nothing bought or sold.' },
    ];

    expect((await render()).textContent).toContain('Nothing bought or sold');
  });

  it('says the journal is empty rather than rendering a blank page', async () => {
    service.months = [];

    expect((await render()).textContent).toContain('Nothing recorded yet');
  });

  it('draws one row per month and expands only the newest one', async () => {
    service.months = ['2026-09', '2026-08'];
    service.entriesByMonth = {
      '2026-09': [{ date: '2026-09-08', markdown: '## 2026-09-08\nseptember day' }],
      '2026-08': [{ date: '2026-08-28', markdown: '## 2026-08-28\naugust day' }],
    };

    const el = await render();

    expect(el.textContent).toContain('september day');
    expect(el.textContent).not.toContain('august day');
  });

  it('fetches and shows an older month only once its row is clicked', async () => {
    service.months = ['2026-09', '2026-08'];
    service.entriesByMonth = {
      '2026-09': [{ date: '2026-09-08', markdown: '## 2026-09-08\nseptember day' }],
      '2026-08': [{ date: '2026-08-28', markdown: '## 2026-08-28\naugust day' }],
    };
    const fixture = TestBed.createComponent(JournalView);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.textContent).not.toContain('august day');

    clickButtonContaining(el, 'August 2026');
    await fixture.whenStable();

    expect(el.textContent).toContain('august day');
  });

  it('opens only the newest day by default when a month has more than one', async () => {
    service.entriesByMonth['2026-09'] = [
      { date: '2026-09-08', markdown: '## 2026-09-08\nnewest day content' },
      { date: '2026-09-01', markdown: '## 2026-09-01\nolder day content' },
    ];

    const el = await render();

    expect(el.textContent).toContain('newest day content');
    expect(el.textContent).not.toContain('older day content');
  });

  it('summarises a closed day beside its date, so a reader knows which to open', async () => {
    service.entriesByMonth['2026-09'] = [
      { date: '2026-09-08', markdown: '## 2026-09-08\nnewest day content' },
      {
        date: '2026-09-01',
        markdown:
          '## 2026-09-01\nBook $9,944.15 (-0.6% against the starting balance).\n- Bought 10 COIN at $195.58\n- Spent $0.05 on research.',
      },
    ];

    const el = await render();

    const summaries = Array.from(el.querySelectorAll('.tl-summary')).map((s) =>
      s.textContent?.trim(),
    );
    expect(summaries).toEqual(['$9,944.15 −0.6% · bought COIN, research']);
  });

  it('opening a collapsed day reveals its entry', async () => {
    service.entriesByMonth['2026-09'] = [
      { date: '2026-09-08', markdown: '## 2026-09-08\nnewest day content' },
      { date: '2026-09-01', markdown: '## 2026-09-01\nolder day content' },
    ];
    const fixture = TestBed.createComponent(JournalView);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.textContent).not.toContain('older day content');

    clickButtonContaining(el, 'Tuesday 1 September');
    await fixture.whenStable();

    expect(el.textContent).toContain('older day content');
  });

  // --- the weekly digest, placed among the days it summarises ------------

  it('inserts the digest between the day newer than its week_start and the day at or before it', async () => {
    service.entriesByMonth['2026-09'] = [
      { date: '2026-09-08', markdown: '## 2026-09-08\ncontent' },
      { date: '2026-09-01', markdown: '## 2026-09-01\ncontent' },
    ];
    digestService.digest.set(digest({ week_start: '2026-09-05' }));

    const el = await render();
    const text = el.textContent ?? '';

    const newestLabel = text.indexOf('Tuesday 8 September');
    const digestHeading = text.indexOf('The week to Saturday 5 September');
    const olderLabel = text.indexOf('Tuesday 1 September');

    expect(newestLabel).toBeGreaterThan(-1);
    expect(digestHeading).toBeGreaterThan(newestLabel);
    expect(olderLabel).toBeGreaterThan(digestHeading);
  });

  it('places the digest at the end of the month when its week_start predates every recorded day', async () => {
    // Still September — a week_start in a different month entirely belongs
    // to that month instead (see the next test) — just earlier than either
    // day actually recorded this month.
    service.entriesByMonth['2026-09'] = [
      { date: '2026-09-08', markdown: '## 2026-09-08\ncontent' },
      { date: '2026-09-05', markdown: '## 2026-09-05\ncontent' },
    ];
    digestService.digest.set(digest({ week_start: '2026-09-01' }));

    const el = await render();
    const text = el.textContent ?? '';

    const olderLabel = text.indexOf('Saturday 5 September');
    const digestHeading = text.indexOf('The week to Tuesday 1 September');

    expect(olderLabel).toBeGreaterThan(-1);
    expect(digestHeading).toBeGreaterThan(olderLabel);
  });

  it('does not show the digest inside a month its week_start does not belong to', async () => {
    service.entriesByMonth['2026-09'] = [
      { date: '2026-09-08', markdown: '## 2026-09-08\ncontent' },
    ];
    // Belongs in August, which is not part of this test's month list at all.
    digestService.digest.set(digest({ week_start: '2026-08-20' }));

    const el = await render();

    expect(el.textContent).not.toContain('The week to');
  });
});
