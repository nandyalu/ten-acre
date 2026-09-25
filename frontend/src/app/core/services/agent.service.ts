import { HttpClient } from '@angular/common/http';
import { Injectable, inject, signal } from '@angular/core';
import { firstValueFrom } from 'rxjs';

import {
  AgentBook,
  AgentComparison,
  AgentEquityPoint,
  AgentTrade,
  AgentTradeRow,
  ActionResult,
  UnprotectedPosition,
  AgentEvent,
  AgentNote,
  AgentReflection,
  JourneyEntry,
} from '../models/api.models';

@Injectable({ providedIn: 'root' })
export class AgentService {
  private readonly http = inject(HttpClient);

  private readonly _book = signal<AgentBook | null>(null);
  private readonly _trades = signal<AgentTrade[]>([]);
  private readonly _performance = signal<AgentComparison | null>(null);
  private readonly _history = signal<AgentTradeRow[]>([]);
  private readonly _curve = signal<AgentEquityPoint[]>([]);
  private readonly _unprotected = signal<UnprotectedPosition[]>([]);
  private readonly _events = signal<AgentEvent[]>([]);
  private readonly _journey = signal<JourneyEntry[]>([]);
  readonly book = this._book.asReadonly();
  readonly trades = this._trades.asReadonly();
  readonly performance = this._performance.asReadonly();
  readonly history = this._history.asReadonly();
  readonly curve = this._curve.asReadonly();
  readonly unprotected = this._unprotected.asReadonly();
  readonly events = this._events.asReadonly();
  readonly journey = this._journey.asReadonly();

  /** Place the missing exits on a position the agent already holds. Rests a
   * stop and a take-profit under shares that are already owned, which is the
   * one action that can only reduce exposure — it opens nothing. */
  async armExits(ticker: string): Promise<ActionResult> {
    return firstValueFrom(this.http.post<ActionResult>(`/api/agent/exits/${ticker}`, {}));
  }

  /** Just the holdings with nothing resting under them. The Overview page
   * needs this without needing the whole book, and loading four other
   * endpoints to answer it would make the landing page wait on all of them. */
  async loadUnprotected(): Promise<void> {
    this._unprotected.set(
      await firstValueFrom(this.http.get<UnprotectedPosition[]>('/api/agent/unprotected')),
    );
  }

  /** Decision passes with their prompts. Its own call, not part of load():
   * a prompt is tens of kilobytes and only the Decisions page shows one. */
  async loadEvents(limit = 30): Promise<void> {
    this._events.set(
      await firstValueFrom(this.http.get<AgentEvent[]>(`/api/agent/events?limit=${limit}`)),
    );
  }

  /** Every "YYYY-MM" with at least one decision pass, newest first — the
   * dots on the Decisions page's timeline. Returned rather than stored on a
   * signal here: unlike load()/loadEvents(), the Decisions page keeps its
   * own per-month cache instead of one flat list, since it fetches months
   * one at a time as a reader opens them. */
  async getEventMonths(): Promise<string[]> {
    return firstValueFrom(this.http.get<string[]>('/api/agent/events/months'));
  }

  /** One month's decision passes, newest first. */
  async getEventsForMonth(month: string): Promise<AgentEvent[]> {
    return firstValueFrom(this.http.get<AgentEvent[]>('/api/agent/events', { params: { month } }));
  }

  /** Every note the agent has ever left, newest first. Its own call rather
   * than a signal here: unlike load(), the Notes page is the only reader and
   * fetches once on its own, the same way getEventMonths() does. */
  async getNotes(): Promise<AgentNote[]> {
    return firstValueFrom(this.http.get<AgentNote[]>('/api/agent/notes'));
  }

  /** The system message the next pass gets — the rules that never change
   * between passes. The current one, not the one a past pass saw. */
  async getSystemPrompt(): Promise<string> {
    const reply = await firstValueFrom(this.http.get<{ text: string }>('/api/agent/system-prompt'));
    return reply.text;
  }

  /** Every evening review, newest first. One flat fetch like getNotes(), not
   * the Decisions page's month-by-month paging: a review is short beside a
   * pass, and one a day is a small payload for months. The limit matches the
   * one the snapshot exporter uses, so the live page and the published copy
   * show the same reviews. */
  async getReflections(limit = 200): Promise<AgentReflection[]> {
    return firstValueFrom(
      this.http.get<AgentReflection[]>(`/api/agent/reflections?limit=${limit}`),
    );
  }

  async loadJourney(days = 10): Promise<void> {
    this._journey.set(
      await firstValueFrom(
        this.http.get<JourneyEntry[]>(`/api/agent/journey/entries?days=${days}`),
      ),
    );
  }

  /** Every "YYYY-MM" with at least one recorded day, newest first — the
   * Journal page's own month timeline, same shape as getEventMonths(). */
  async getJournalMonths(): Promise<string[]> {
    return firstValueFrom(this.http.get<string[]>('/api/agent/journey/entries/months'));
  }

  /** One month's journal entries, newest first. */
  async getJournalEntriesForMonth(month: string): Promise<JourneyEntry[]> {
    return firstValueFrom(
      this.http.get<JourneyEntry[]>('/api/agent/journey/entries', { params: { month } }),
    );
  }

  async load(): Promise<void> {
    const [book, trades, performance, history, curve, unprotected] = await Promise.all([
      firstValueFrom(this.http.get<AgentBook>('/api/agent')),
      firstValueFrom(this.http.get<AgentTrade[]>('/api/agent/trades')),
      firstValueFrom(this.http.get<AgentComparison>('/api/agent/performance')),
      firstValueFrom(this.http.get<AgentTradeRow[]>('/api/agent/history')),
      firstValueFrom(this.http.get<AgentEquityPoint[]>('/api/agent/curve')),
      firstValueFrom(this.http.get<UnprotectedPosition[]>('/api/agent/unprotected')),
    ]);
    this._book.set(book);
    this._trades.set(trades);
    this._performance.set(performance);
    this._history.set(history);
    this._curve.set(curve);
    this._unprotected.set(unprotected);
  }
}
