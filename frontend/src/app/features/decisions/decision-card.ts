import { DecimalPipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, input, signal } from '@angular/core';

import {
  AgentEvent,
  AgentEventOrder,
  AgentOrder,
  DecisionExchange,
  DecisionTurn,
} from '../../core/models/api.models';
import { Term } from '../../shared/glossary/term';
import { readerDateKey, readerDateTime, readerTime } from '../../shared/market-time';
import { CopyButton } from '../../shared/copy-button';

/**
 * One decision pass: the prompt, the answer, and what it did — collapsed by
 * default, since a prompt runs to tens of kilobytes and a feed that opens
 * with one is a feed nobody scrolls.
 *
 * Its own component rather than a block in decisions-view.html's `@for`, so
 * a card's open/closed prompt state lives on the card itself instead of a
 * `${id}:prompt`-keyed set on the parent — the parent now renders many
 * months' worth of cards at once and has enough state of its own to track.
 */
@Component({
  selector: 'app-decision-card',
  standalone: true,
  imports: [DecimalPipe, Term, CopyButton],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './decision-card.html',
})
export class DecisionCard {
  readonly event = input.required<AgentEvent>();

  /** Which panels are open on this one card. */
  private readonly open = signal<Set<'prompt' | 'response' | 'thinking'>>(new Set());

  when(instant: string): string {
    return readerDateTime(instant);
  }

  /** When it asked to be woken next. Time only when that is the same day as
   * the pass; the day as well when it is not. A 7:55 PM pass that asked for
   * "9:30 AM" read as though it meant the morning already gone (2026-09-17),
   * and the agent may ask for up to four days ahead.
   *
   * The agent sets its own cadence, so this is a decision it made and worth
   * showing beside the orders. A pass that asked for nothing shows nothing:
   * the scheduler's fallback is not something the agent chose.
   */
  wokenAt(event: AgentEvent): string {
    if (!event.next_wakeup) return '';
    if (readerDateKey(event.next_wakeup) === readerDateKey(event.ran_at)) {
      const t = readerTime(event.next_wakeup);
      return `${t.time} ${t.zone}`;
    }
    return readerDateTime(event.next_wakeup);
  }

  /** The agent's messages to whoever maintains it.
   *
   * They ride in `orders` because that is the record of everything one pass
   * produced, but they are not orders and must not be rendered as one — a
   * note has no ticker and no quantity. */
  notesIn(event: AgentEvent): string[] {
    return event.orders.filter((o) => o.side === 'note').map((o) => o.reason);
  }

  /** Orders the broker refused.
   *
   * Defaulted rather than read straight off the event: a browser holding a
   * cached bundle can outlive the deployment it was served by, and a field
   * added on one side is undefined on the other until both catch up. A page
   * that throws in that window is worse than one missing a section. */
  failedIn(event: AgentEvent): AgentOrder[] {
    return event.failed ?? [];
  }

  /** Failed orders that were never a broker call in the first place — a
   * research the agent paid for that did not finish. Split out from
   * `failedIn` only for the two hint paragraphs below the list: the badge
   * itself already reads `order.side` to pick its own label, row by row. */
  researchFailedIn(event: AgentEvent): AgentOrder[] {
    return this.failedIn(event).filter((o) => o.side === 'research');
  }

  brokerFailedIn(event: AgentEvent): AgentOrder[] {
    return this.failedIn(event).filter((o) => o.side !== 'research');
  }

  /** Everything the pass did that was not a note. */
  tradesIn(event: AgentEvent): AgentEventOrder[] {
    return event.orders.filter((o) => o.side !== 'note');
  }

  /** What one turn itself said, before screening — its own reasoning and
   * everything it asked for, note included. See `DecisionTurn` for why this
   * is not the same claim as `tradesIn`/`notesIn`: those are what the whole
   * pass actually did; this is what one call asked for, whether or not it
   * was carried out. Absent on a turn recorded before 2026-09-15. */
  turnReasoning(turn: DecisionTurn): string {
    return turn.reasoning ?? '';
  }

  turnNotesIn(turn: DecisionTurn): string[] {
    return (turn.orders ?? []).filter((o) => o.side === 'note').map((o) => o.reason);
  }

  turnTradesIn(turn: DecisionTurn): AgentEventOrder[] {
    return (turn.orders ?? []).filter((o) => o.side !== 'note');
  }

  /** What one turn fetched before it answered. Defaulted for the same reason
   * as `failedIn`: a turn recorded before 2026-09-17 has no such key. */
  turnExchanges(turn: DecisionTurn): DecisionExchange[] {
    return turn.exchanges ?? [];
  }

  /** Every fetch across the pass, for the single-turn layout, where the one
   * turn's exchanges are the only ones there are. */
  exchangesIn(event: AgentEvent): DecisionExchange[] {
    return (event.turns ?? []).flatMap((turn) => this.turnExchanges(turn));
  }

  /** What a fetch was asked about: the ticker, and the date when one was
   * given. Empty for the fetches that take nothing. */
  fetchTarget(exchange: DecisionExchange): string {
    const ticker = exchange.args?.['ticker'];
    const date = exchange.args?.['date'];
    return [ticker ? String(ticker) : '', date ? `(${String(date)})` : '']
      .filter(Boolean)
      .join(' ');
  }

  /** One fetch as a few words, for the prompt panel's label. A `decide` here
   * is one the agent made in the same round as a fetch, which the harness
   * declines to carry out — the label says so. */
  fetchLabel(exchange: DecisionExchange): string {
    const what = [exchange.name, this.fetchTarget(exchange)].filter(Boolean).join(' ');
    return exchange.name === 'decide' ? `${what} — not carried out` : what;
  }

  /** Whether any turn of the pass was answered by the text fallback: the
   * function call to the model failed and prose was read instead. Worth a
   * line on the card, because a fallback turn otherwise looks like a turn
   * that simply fetched nothing. */
  fellBack(event: AgentEvent): boolean {
    return (event.turns ?? []).some((turn) => turn.channel === 'text-fallback');
  }

  /** Whether the harness ran the fetch. A fetch past the pass's allowance,
   * and a `decide` made beside a fetch, are answered with a refusal instead of
   * a result, and the row shows them the way a refused order is shown. */
  fetchRan(exchange: DecisionExchange): boolean {
    return !/^Not (run|carried out)\b/.test(exchange.result);
  }

  isOpen(which: 'prompt' | 'response' | 'thinking'): boolean {
    return this.open().has(which);
  }

  toggle(which: 'prompt' | 'response' | 'thinking'): void {
    const next = new Set(this.open());
    next.has(which) ? next.delete(which) : next.add(which);
    this.open.set(next);
  }

  /** A pass that asked nothing has no words to show — the market was shut, or
   * the agent was switched off. Saying so beats an empty panel. */
  asked(event: AgentEvent): boolean {
    return !!event.prompt;
  }

  did(event: AgentEvent): string {
    if (event.skipped) return event.skipped;
    if (!event.orders.length) return 'nothing';
    return event.orders.map((o) => `${o.side} ${o.ticker}`).join(', ');
  }
}
