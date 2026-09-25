import { DecimalPipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, input, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

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
import { Markdown } from '../../shared/markdown';

/** The line over a turn block: what woke the pass, or why it asked again. */
export interface WakeLine {
  label: string;
  text: string;
  /** True when `text` is the prompt's own sentence, in the second person. */
  quoted: boolean;
}

/** The sentence `build_prompt` puts in every later turn of a pass. The
 * clauses between "not a new wake: " and ". All of it" are `_asked_again`'s,
 * joined with "; and ". */
const ASKED_AGAIN =
  /\*\*Why you are asked again\.\*\* This is the same pass, not a new wake: (.+?)\. All of it is under/;

/** `_asked_again`'s four clauses, each turned to the third person. */
const ASKED_AGAIN_CLAUSES: Record<string, string> = {
  'the orders in your last answer have been carried out': 'its orders were carried out',
  'you asked to read an analysis': 'it asked to read an analysis',
  'part of your last answer went with a read, so it was not carried out':
    'part of its answer went with a read and was not carried out',
  'part of your last answer was refused': 'part of its answer was refused',
};

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
  imports: [DecimalPipe, RouterLink, Term, CopyButton, Markdown],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './decision-card.html',
})
export class DecisionCard {
  readonly event = input.required<AgentEvent>();

  /** Whether this card's transcript is open: the prompt and every fetch on
   * one side, the thinking and the answer on the other, turn by turn.
   * One disclosure since 2026-09-25. It was three — the prompt, the answer
   * and the thinking each behind a button — and the answer panel showed the
   * same JSON the turn's own reasoning and orders above are read from. */
  readonly transcriptOpen = signal(false);

  toggleTranscript(): void {
    this.transcriptOpen.update((open) => !open);
  }

  /** The turns the transcript draws, one per model call. A pass recorded
   * before turns were kept, or a single-turn pass that fetched nothing,
   * has none on record and only the pass-level prompt, answer and thinking;
   * those become the one turn there was, so the transcript has one shape. */
  turnsShown(event: AgentEvent): DecisionTurn[] {
    const turns = event.turns ?? [];
    if (turns.length) return turns;
    return [
      { prompt: event.prompt ?? '', response: event.response ?? '', thinking: event.thinking },
    ];
  }

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

  /** What started the pass, in the sentence the agent was shown. Empty on a
   * skipped pass and on every pass before 2026-09-21, when nothing stored it.
   *
   * Defaulted rather than read straight off the event, for the same reason as
   * `failedIn`: a browser holding a cached bundle can outlive the deployment
   * that served it, and a page that throws in that window is worse than one
   * missing a line. */
  wokeBecause(event: AgentEvent): string {
    return event.woke_because ?? '';
  }

  /** The line over a turn block that says why the model was called at all.
   *
   * On the first turn it is what woke the pass, in the sentence the agent
   * was shown. Quoted, because it is the prompt's words in the second person
   * and not the page's. The prompt heads that sentence "Why you are awake",
   * and the page used the same heading until 2026-09-25; a reader took the
   * "you" for themselves.
   *
   * On a later turn it is why the same pass asked again: its orders were
   * carried out, it asked to read an analysis, part of its answer was
   * refused. Until 2026-09-25 every turn repeated the wake sentence, so a
   * two-turn pass read as though it had been woken twice for one reason.
   * Null when nothing is on record, so no line is drawn — an empty label
   * would read as a reason nobody gave rather than one nobody kept. */
  wakeLine(event: AgentEvent, index: number): WakeLine | null {
    if (index === 0) {
      const woke = this.wokeBecause(event);
      return woke ? { label: 'What woke it', text: woke, quoted: true } : null;
    }
    const text = this.askedAgain(event, index);
    return text ? { label: 'Why it was asked again', text, quoted: false } : null;
  }

  /** Why turn `index` of the pass was asked, in the third person.
   *
   * Read from that turn's own prompt: `build_prompt` writes a "Why you are
   * asked again" sentence there from whichever of the outcomes, the readings
   * and the refusals are really in the prompt, so it is the one account of
   * the reason that cannot drift from what the model saw. Each clause is
   * turned from "your last answer" into "its", and a clause this page does
   * not know is quoted as written rather than dropped. The previous turn's
   * own orders are named in brackets, because "its orders were carried out"
   * says less than "research ORCL".
   *
   * A prompt with no such sentence, from before 2026-09-13, falls back to
   * the previous turn's orders when it had any, and to nothing otherwise. */
  askedAgain(event: AgentEvent, index: number): string {
    const turns = event.turns ?? [];
    const turn = turns[index];
    const previous = turns[index - 1];
    if (!turn || !previous) return '';

    const match = (turn.prompt ?? '').match(ASKED_AGAIN);
    const clauses = match
      ? match[1]
          .split('; and ')
          .map((clause) => ASKED_AGAIN_CLAUSES[clause.trim()] ?? `“${clause.trim()}”`)
      : [];
    const did = this.turnTradesIn(previous).map((o) =>
      [o.side, o.ticker].filter(Boolean).join(' '),
    );
    if (!clauses.length) {
      if (!did.length) return '';
      clauses.push('its orders were carried out');
    }
    const detail = did.length ? ` (${did.join(', ')})` : '';
    return `Same pass, asked again after turn ${index}: ${clauses.join('; and ')}${detail}.`;
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

  /** `memory` is held out alongside `note` for the same reason: both are the
   * agent writing a sentence, not moving anything, and neither has a ticker
   * or a quantity — rendered here they read as a trade in a stock called "".
   * A memory note has no display of its own on this page yet, and had none
   * before either: `_orders_json` never recorded one, so nothing is lost that
   * the page used to show. */
  turnTradesIn(turn: DecisionTurn): AgentEventOrder[] {
    return (turn.orders ?? []).filter((o) => o.side !== 'note' && o.side !== 'memory');
  }

  /** Whether any turn of the pass left a note.
   *
   * The turn blocks print each note where it was written; this decides
   * whether the paragraph explaining what a note *is* prints once under them.
   * A pass that left a note in three turns must not explain it three times. */
  anyTurnNoted(event: AgentEvent): boolean {
    return (event.turns ?? []).some((turn) => this.turnNotesIn(turn).length > 0);
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
    return [this.fetchTicker(exchange), this.fetchDate(exchange)].filter(Boolean).join(' ');
  }

  /** The two halves of `fetchTarget`, so the row can link the ticker and
   * leave the date as plain text beside it. */
  fetchTicker(exchange: DecisionExchange): string {
    const ticker = exchange.args?.['ticker'];
    return ticker ? String(ticker) : '';
  }

  fetchDate(exchange: DecisionExchange): string {
    const date = exchange.args?.['date'];
    return date ? `(${String(date)})` : '';
  }

  /** What an order asked for beyond its ticker and its size, in a few words.
   *
   * On a buy or a sell: whether it traded at the going price or named one,
   * and how long a named price may wait. On an adjust: which exits moved and
   * to what. Empty for every other side, and for any order recorded before
   * 2026-09-20 — the fields existed and were acted on, and nothing wrote them
   * down, so an absent `order_type` means "not recorded", never "market".
   *
   * Where the old level came from is the pass's own outcome line in `reason`
   * ("AAPL: moved stop from $330.00 to $334.16"), not this: an order is what
   * the agent asked for, and it never states what the exit was resting at. */
  orderDetail(order: AgentEventOrder): string {
    const parts: string[] = [];
    if (order.side === 'adjust') {
      if (order.stop != null) parts.push(`stop to ${this.money(order.stop)}`);
      if (order.target != null) parts.push(`target to ${this.money(order.target)}`);
    }
    if (order.side === 'buy' || order.side === 'sell') {
      const kind = (order.order_type ?? '').toLowerCase().trim();
      if (kind === 'limit' && order.limit_price != null) {
        parts.push(`limit ${this.money(order.limit_price)}`);
        // Only a limit order waits. A market order is filled or rejected at
        // once, so saying when it would expire would describe nothing.
        parts.push(
          (order.time_in_force ?? '').toLowerCase().trim() === 'gtc'
            ? 'stays past today'
            : 'expires at the close',
        );
      } else if (kind) {
        parts.push(`${kind} order`);
      }
    }
    return parts.join(' · ');
  }

  private money(value: number): string {
    return `$${value.toLocaleString('en-US', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    })}`;
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
