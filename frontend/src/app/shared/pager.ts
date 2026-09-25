import { Component, input } from '@angular/core';

import { Paged } from './paged-rows';

/**
 * Previous and next under a table, with where the reader is in the list.
 *
 * Rendered only once the list runs past one page. A table of three rows
 * gains nothing from a control that says "1–3 of 3", and every control on
 * this site has to earn its place — nothing here changes what the agent
 * does, but a page that looks like a tool reads like one.
 */
@Component({
  selector: 'app-pager',
  template: `
    @if (of().pageCount() > 1) {
      <nav class="pager" aria-label="Pages">
        <span class="pager-status">
          {{ of().from() }}–{{ of().to() }} of {{ of().total() }} {{ noun() }}
        </span>
        <span class="pager-buttons">
          <button
            type="button"
            class="btn btn-sm"
            [disabled]="of().page() === 1"
            (click)="of().goTo(of().page() - 1)"
          >
            Previous
          </button>
          <span class="pager-page">Page {{ of().page() }} of {{ of().pageCount() }}</span>
          <button
            type="button"
            class="btn btn-sm"
            [disabled]="of().page() === of().pageCount()"
            (click)="of().goTo(of().page() + 1)"
          >
            Next
          </button>
        </span>
      </nav>
    }
  `,
})
export class Pager {
  readonly of = input.required<Paged>();
  /** What the rows are, so the count reads "1–10 of 66 orders". */
  readonly noun = input('rows');
}
