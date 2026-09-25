import { signal } from '@angular/core';

import { PagedRows } from './paged-rows';

interface Row {
  ticker: string;
  side: string;
}

function rows(n: number): Row[] {
  return Array.from({ length: n }, (_, i) => ({
    ticker: i % 3 === 0 ? 'INTC' : 'COIN',
    side: i % 2 === 0 ? 'buy' : 'sell',
  }));
}

describe('PagedRows', () => {
  it('shows one page at a time and counts where the reader is', () => {
    const list = new PagedRows(
      () => rows(23),
      (r) => r.ticker,
      10,
    );

    expect(list.rows()).toHaveLength(10);
    expect(list.pageCount()).toBe(3);
    expect(list.from()).toBe(1);
    expect(list.to()).toBe(10);

    list.goTo(3);

    expect(list.rows()).toHaveLength(3);
    expect(list.from()).toBe(21);
    expect(list.to()).toBe(23);
  });

  it('filters on the haystack, ignoring case', () => {
    const list = new PagedRows(
      () => rows(23),
      (r) => `${r.ticker} ${r.side}`,
      10,
    );

    list.search('intc');

    expect(list.total()).toBe(8);
    expect(list.rows().every((r) => r.ticker === 'INTC')).toBe(true);
  });

  it('returns to the first page when the filter changes', () => {
    // The page a reader was on was a page of the old list.
    const list = new PagedRows(
      () => rows(23),
      (r) => r.ticker,
      10,
    );
    list.goTo(3);

    list.search('COIN');

    expect(list.page()).toBe(1);
  });

  it('never leaves the reader on a page past the end', () => {
    // Clamped rather than reset: the source shrinking under a reader is the
    // static site refreshing, and page 3 of 2 would show nothing at all.
    const source = signal(rows(23));
    const list = new PagedRows(
      () => source(),
      (r) => r.ticker,
      10,
    );
    list.goTo(3);

    source.set(rows(12));

    expect(list.page()).toBe(2);
    expect(list.rows()).toHaveLength(2);
  });

  it('reports zero rows, not a page 1 of 1 with a first row of 1', () => {
    const list = new PagedRows(
      () => rows(5),
      (r) => r.ticker,
      10,
    );

    list.search('nothing matches this');

    expect(list.total()).toBe(0);
    expect(list.from()).toBe(0);
    expect(list.to()).toBe(0);
    expect(list.pageCount()).toBe(1);
  });
});
