import { Component, computed, input } from '@angular/core';
import { marked } from 'marked';

marked.use({ gfm: true, breaks: false });

/**
 * A block of Markdown, rendered.
 *
 * The prompt the agent is given, what each fetch returns and the model's own
 * thinking are all Markdown — headings, rules, tables, lists — and until
 * 2026-09-25 they were printed raw inside a `<pre>`, so a reader met a
 * signals table as a wall of pipes. Rendered, a table is a table.
 *
 * **The raw text is never lost.** The copy button beside every rendered
 * block carries the original string, so what a reader lifts out is the
 * Markdown, without a trace of this styling.
 *
 * **Angular sanitises what this binds.** `[innerHTML]` runs the HTML through
 * the DOM sanitiser before it reaches the page, which strips a script, an
 * event handler or a style block whatever the model or the prompt held.
 * `marked` itself does not sanitise, and is not asked to.
 *
 * A fixed, known shape is still parsed by hand instead — see `rationale.ts`
 * and `journal-entry.ts` — because pulling five labels out is smaller and
 * more predictable than rendering. This is for text whose shape nobody
 * controls: what the app wrote for the model and what the model wrote back.
 */
@Component({
  selector: 'app-markdown',
  template: `<div class="markdown" [innerHTML]="html()"></div>`,
})
export class Markdown {
  readonly text = input.required<string | null | undefined>();

  protected readonly html = computed(() => renderMarkdown(this.text() ?? ''));
}

/** Markdown to HTML, unsanitised. Exported for the spec; every template
 * goes through the component, whose binding is what makes it safe. */
export function renderMarkdown(markdown: string): string {
  return marked.parse(markdown, { async: false });
}
