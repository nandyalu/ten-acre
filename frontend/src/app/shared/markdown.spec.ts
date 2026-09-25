import { TestBed } from '@angular/core/testing';
import { Component } from '@angular/core';

import { Markdown, renderMarkdown } from './markdown';

/** A slice of a real prompt: a section heading, a rule, and a table. */
const PROMPT = `## What you hold

---

| Ticker | Shares | Stop |
|---|---|---|
| COIN | 17 | $182.29 |

**Why you are awake.** You asked to be woken around now.`;

@Component({
  imports: [Markdown],
  template: `<app-markdown [text]="text" />`,
})
class Host {
  text = PROMPT;
}

describe('renderMarkdown', () => {
  it('turns a prompt into headings, tables and bold', () => {
    const html = renderMarkdown(PROMPT);

    expect(html).toContain('<h2>What you hold</h2>');
    expect(html).toContain('<hr>');
    expect(html).toContain('<table>');
    expect(html).toContain('<td>$182.29</td>');
    expect(html).toContain('<strong>Why you are awake.</strong>');
  });

  it('keeps an angle bracket that is not a tag as text', () => {
    // The prompt writes placeholders like this, and a placeholder must not
    // vanish as though it were markup.
    expect(renderMarkdown('at <today 15:55> Eastern')).toContain('&lt;today 15:55&gt;');
  });
});

describe('Markdown', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({ imports: [Host] }).compileComponents();
  });

  it('renders the Markdown into the page', async () => {
    const fixture = TestBed.createComponent(Host);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('.markdown h2')?.textContent).toBe('What you hold');
    expect(el.querySelectorAll('.markdown table td')).toHaveLength(3);
  });

  it('strips a script the text carried, because Angular sanitises the binding', async () => {
    const fixture = TestBed.createComponent(Host);
    fixture.componentInstance.text = 'before <script>alert(1)</script> after';
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('script')).toBeNull();
    expect(el.textContent).toContain('before');
    expect(el.textContent).toContain('after');
  });
});
