import { computed, signal, Signal } from '@angular/core';

/**
 * What a pager or a filter box needs from a list, with the row type left out,
 * so one `<app-pager>` and one `<app-table-search>` serve every table.
 */
export interface Paged {
  readonly query: Signal<string>;
  readonly page: Signal<number>;
  readonly pageCount: Signal<number>;
  readonly total: Signal<number>;
  readonly from: Signal<number>;
  readonly to: Signal<number>;
  readonly pageSize: number;
  goTo(page: number): void;
  search(query: string): void;
}

/**
 * A list a reader can filter and page through in place.
 *
 * The whole list is already in the browser — the trade log, the positions and
 * the analyses each arrive as one response, and the static site cannot answer
 * a query at all — so filtering and paging happen here rather than on the
 * server. One text box matches against whatever `haystack` returns for a
 * row, so each table decides what a reader may search by.
 *
 * `page` is clamped to the pages that exist. A filter that shrinks the list
 * must never leave the reader on a page past its end, showing nothing.
 */
export class PagedRows<T> implements Paged {
  readonly query = signal('');
  private readonly requested = signal(1);

  constructor(
    private readonly source: () => T[],
    private readonly haystack: (row: T) => string,
    readonly pageSize = 10,
  ) {}

  readonly filtered = computed(() => {
    const needle = this.query().trim().toLowerCase();
    const rows = this.source();
    if (!needle) return rows;
    return rows.filter((row) => this.haystack(row).toLowerCase().includes(needle));
  });

  readonly total = computed(() => this.filtered().length);

  readonly pageCount = computed(() => Math.max(1, Math.ceil(this.total() / this.pageSize)));

  readonly page = computed(() => Math.min(Math.max(1, this.requested()), this.pageCount()));

  readonly rows = computed(() => {
    const start = (this.page() - 1) * this.pageSize;
    return this.filtered().slice(start, start + this.pageSize);
  });

  /** 1-based position of the first row on the page, or 0 when nothing matches. */
  readonly from = computed(() => (this.total() ? (this.page() - 1) * this.pageSize + 1 : 0));

  readonly to = computed(() => Math.min(this.page() * this.pageSize, this.total()));

  goTo(page: number): void {
    this.requested.set(page);
  }

  /** A new filter starts from the first page: the page a reader was on was a
   * page of the old list. */
  search(query: string): void {
    this.query.set(query);
    this.requested.set(1);
  }
}
