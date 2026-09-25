import { Component, input } from '@angular/core';

import { Paged } from './paged-rows';

/**
 * A filter box for a table. Typing narrows the list in place; nothing is
 * fetched, and the whole record stays one step away in an empty box.
 *
 * The placeholder is the label: it names what the box matches on, which is
 * the one thing a reader has to know. A visually hidden copy keeps it for a
 * screen reader once the placeholder is typed over.
 */
@Component({
  selector: 'app-table-search',
  template: `
    <label class="table-search">
      <span class="sr-only">{{ label() }}</span>
      <input
        #box
        type="search"
        autocomplete="off"
        [placeholder]="label()"
        [value]="of().query()"
        (input)="of().search(box.value)"
      />
    </label>
  `,
})
export class TableSearch {
  readonly of = input.required<Paged>();
  readonly label = input('Filter');
}
