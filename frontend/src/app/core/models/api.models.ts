// Mirrors bot/api/schemas.py field-for-field, including the snake_case
// naming — kept as-is (no camelCase translation layer) since the API is
// same-origin, internal-only, and a mapping layer would add nothing here.

export interface OhlcBar {
  date: string;
  /** Unix seconds, UTC. Added 2026-09-08 alongside the intraday bar cache —
   * `date` alone cannot place a bar within a day, and the chart needs a real
   * timestamp to draw a sub-day candle at its true time rather than lumped
   * onto whichever daily candle its date falls on. */
  timestamp: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface Signal {
  id: number;
  ticker: string;
  signal_date: string;
  decision: string;
  rationale: string;
  time_horizon_text: string | null;
  price_target: number | null;
  price_at_signal: number;
  evaluation_date: string;
  price_at_evaluation: number | null;
  outcome: 'pass' | 'fail' | null;
  evaluated_at: string | null;
  // When the analysis started, to the second. Null on rows that predate the
  // column and had no trace to recover it from — `signal_date` alone is a
  // calendar date and cannot place a signal within a day, which matters
  // because several analyses a day is normal here.
  created_at: string | null;
  message_id: string | null;
  benchmark_price_at_signal: number | null;
  benchmark_price_at_evaluation: number | null;
  alpha_pct: number | null;
  outcome_vs_benchmark: 'pass' | 'fail' | null;
  price_target_hit: boolean | null;
  horizon: string | null; // "swing" | "position"
  model: string | null; // the LLM that produced it; null on rows predating the column
  // Why this analysis ran. A signal produced because the stock just moved is
  // the analyst reacting to a move already in the price; a scheduled one is
  // not reacting to anything. Null on rows predating the column.
  trigger: 'sweep' | 'commissioned' | 'move' | 'earnings' | 'manual' | null;
  // What the run cost. Null means it wasn't measured, never that it was free.
  duration_seconds: number | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  llm_calls: number | null;
  // The trade plan, from TradingAgents' trader stage. Null means the model did
  // not state it — never zero.
  entry_price: number | null;
  stop_loss: number | null;
  win_probability: number | null; // 0-100
  risk_reward: number | null;
  expected_value_r: number | null;
  /** What the run cost. NULL when it was never measured — a zero would read as
   * a free analysis rather than an unrecorded one. */
  cost_usd: number | null;
  /** "vendor" (a list-price estimate) or "electricity" (a self-hosted run's
   * GPU draw). Not the same kind of dollar, so they are never added up. */
  cost_basis: string | null; // signed, in R-multiples
}

export interface SignalDetail extends Signal {
  reports: Record<string, string>;
  /** What the agent did with this call. Separate from the signal's own
   * grade: one says whether the analysis was right over its horizon, the other
   * what the exit rules made of it. */
  agent_trades: AgentTradeRow[];
}

export interface DecisionStats {
  total: number;
  passes: number;
  vs_benchmark_total: number;
  vs_benchmark_passes: number;
  avg_move_pct: number | null;
}

export interface Scorecard {
  resolved: number;
  pending: number;
  passes: number;
  vs_benchmark_total: number;
  vs_benchmark_passes: number;
  avg_alpha_pct: number | null;
  target_total: number;
  target_hits: number;
  by_decision: Record<string, DecisionStats>;
  by_model: Record<string, DecisionStats>;
  by_ticker: Record<string, [number, number]>;
}

export interface TickerSummary {
  ticker: string;
  current_price: number | null;
  price_updated_at: string | null;
  latest_signal: Signal | null;
  /** No market data any more — delisted, halted, or a wrong symbol. Every
   * fetch path skips it, so the UI has to say why the price is stale. */
  inactive: boolean;
  inactive_reason: string | null;
}

export interface CalibrationBand {
  label: string;
  total: number;
  passes: number;
  stated_pct: number | null;
  actual_pct: number | null;
  /** Claimed minus actual, in percentage points. Positive is overconfident. */
  gap: number | null;
}

/** Whether the model's stated confidence is worth anything. `sorts_outcomes`
 * is null until the book is large enough to answer it honestly. */
export interface Calibration {
  resolved: number;
  passes: number;
  stated_pct: number | null;
  actual_pct: number | null;
  gap: number | null;
  sorts_outcomes: boolean | null;
  verdict: string;
  bands: CalibrationBand[];
}

export interface RestingExit {
  kind: string; // "stop" | "target"
  price: number;
  quantity: number;
}

/** What the agent holds in one ticker. `exits` are the orders the broker
 * will actually execute, which is a different claim from the signal's stop and
 * target shown beside them. */
export interface AgentPosition {
  quantity: number;
  avg_cost: number;
  price: number | null;
  opened: string | null;
  held_days: number | null;
  market_value: number | null;
  unrealized_pct: number | null;
  exits: RestingExit[];
  unprotected: boolean;
  /** An arming already waiting for the next open. */
  arm_queued: boolean;
}

/** One lot's life in the agent's book, matched FIFO. `pnl` is null while it is
 * open — its result is not decided yet. */
export interface Lot {
  quantity: number;
  entry: number;
  entry_at: string;
  exit: number | null;
  exit_at: string | null;
  pnl: number | null;
  return_pct: number | null;
  held_days: number;
  signal_id: number | null;
}

export interface TickerDetail {
  ticker: string;
  current_price: number | null;
  price_updated_at: string | null;
  latest_signal: Signal | null;
  agent_position: AgentPosition | null;
  inactive: boolean;
  inactive_reason: string | null;
}

export interface ActionResult {
  message: string;
}

export interface Alert {
  id: number;
  ticker: string;
  alert_type: string;
  message: string;
  created_at: string;
}

/** One filled buy or sell by the agent, for the ticker timeline. */
export interface Trade {
  side: 'buy' | 'sell';
  date: string;
  /** The real fill time, to the second. When the app noticed the fill by
   * polling the broker, not a fill time the broker itself reports. */
  filled_at: string;
  price: number;
  quantity: number;
}

/** Everything that happened to one ticker, from GET /api/tickers/{t}/events.
 * The chart overlays and the timeline are this same data drawn two ways. */
export interface TickerEvents {
  ticker: string;
  bars: OhlcBar[];
  signals: Signal[];
  alerts: Alert[];
  trades: Trade[];
  lots: Lot[];
}

export interface Digest {
  week_start: string;
  resolved: Signal[];
  new_signals: Signal[];
  alerts: Alert[];
  win_rate_30d: [number, number];
  win_rate_all: [number, number];
  book_lines: string[];
}

export interface Regime {
  as_of: string;
  vix: number | null;
  spy_price: number | null;
  spy_ma200: number | null;
  curve_spread_pct: number | null;
  spy_vs_ma_pct: number | null;
  label: string;
  emoji: string;
}

/** One requirement, and how to satisfy it. Carries no configured value —
 * `ready` says whether a thing is set, never what it is set to. */
export interface SetupCheck {
  key: string;
  label: string;
  ready: boolean;
  /** False for the optional ones: Discord, FRED. */
  blocking: boolean;
  detail: string;
  /** The literal lines to paste into .env. Empty once the check passes, and
   * empty for anything fixed in the app rather than the environment. */
  fix: string;
  /** For the one requirement fixed in the app: a route to link to. A path,
   * never a value. */
  action_path: string;
  action_label: string;
}

export interface SetupStatus {
  /** Every blocking check passes. */
  ready: boolean;
  /** The agent has never been switched on here. */
  first_run: boolean;
  checks: SetupCheck[];
}

export interface Settings {
  /** The day THIS deployment's experiment began, "YYYY-MM-DD". Served rather
   * than compiled in, so a second container does not announce itself as being
   * on day eight of the first one's run. See shared/experiment.ts. */
  experiment_start: string;
  horizon: string; // "swing" | "position"
  llm_model: string;
  /** Everything the LLM endpoint serves. Empty when it couldn't be reached —
   * the settings page then falls back to a text field. Read-only: it isn't
   * part of SettingsPatch. */
  llm_model_choices: string[];
  alert_move_pct: number;
  alert_stop_pct: number;
  alert_volume_mult: number;
  alerts_enabled: boolean;
  agent_enabled: boolean;
  agent_budget: number;
  /** When the static snapshot behind this page was taken, ISO 8601.
   * Present only on the published copy — the live app's data is live and
   * there is nothing to date. See backend/services/snapshot_export.py. */
  snapshot_generated_at?: string;
  /** What one analysis costs the agent here. Served rather than compiled in —
   * it was "$0.05" in six templates, so a deployment charging something else
   * stated a price it does not charge. Zero means research is free, which is a
   * real setting. See shared/research-price.ts. */
  research_price: number;
  /** The conviction floor. Zero means off, which is the default. */
  agent_min_win_probability: number;
  agent_min_risk_reward: number;
  /** True on the published copy, where the backend refuses every write. The
   * shell reads it to drop the Settings link. Presentation only — the refusal
   * is middleware, so a hidden link and a typed URL get the same answer. */
  public: boolean;
}

export type SettingsPatch = Partial<Omit<Settings, 'llm_model_choices'>>;

export interface AgentHolding {
  ticker: string;
  quantity: number;
  avg_cost: number;
  price: number | null;
  market_value: number | null;
  cost_basis: number;
  unrealized_pnl: number | null;
}

/** An auto-trader holding with no exit resting at the broker. Its own type
 * because it is an absence, and nothing on a dashboard draws attention to an
 * absence on its own. */
export interface UnprotectedPosition {
  ticker: string;
  quantity: number;
  avg_cost: number;
  held_days: number | null;
}

export interface AgentEquityPoint {
  date: string;
  equity: number;
  cash: number;
  market_value: number;
}

export interface AgentBook {
  enabled: boolean;
  /** False means the app holds production credentials, where the agent refuses
   * to trade at all. The page says so rather than showing an idle agent. */
  sandbox: boolean;
  budget: number;
  cash: number;
  invested: number;
  market_value: number;
  equity: number;
  realized_pnl: number;
  return_pct: number;
  holdings: AgentHolding[];
}

export interface AgentTrade {
  id: number;
  ticker: string;
  side: string;
  quantity: number;
  price: number | null; // null until the order fills
  placed_at: string;
  filled_at: string | null;
  status: string; // "pending" | "filled" | "rejected"
  /** A protective stop resting at the broker. It is meant to sit pending
   * indefinitely, so it is listed apart from orders awaiting a fill. */
  is_stop: boolean;
  /** Where a resting exit is armed. NULL on a market order, whose price is
   * only known once it fills. */
  limit_price: number | null;
  /** Which leg of the bracket: "stop" | "target". NULL on a market order. */
  exit_kind: string | null;
  reason: string | null;
  signal_id: number | null;
}

export interface AgentOrder {
  ticker: string;
  side: string;
  quantity: number;
  reason: string | null;
  why: string | null;
}

export interface Strategy {
  name: string;
  equity: number;
  invested: number;
  cash: number;
  trades: number;
  note: string;
}

/** The agent against the two things it could be replaced by. If the mechanical
 * follower wins, the model is costing money for nothing. */
export interface AgentComparison {
  budget: number;
  since: string | null;
  verdict: string;
  strategies: Strategy[];
}

/** A screened ticker worth considering. Never followed automatically — each
 * one added costs about seven minutes of GPU on every later sweep. */
export interface Candidate {
  ticker: string;
  name: string;
  price: number;
  volume: number;
  change_pct: number | null;
  source: string;
}

/** One lot's life: bought, and sold or still held. Exit and P/L stay null
 * while it is open — an unrealized number there would read as booked. */
export interface AgentTradeRow {
  ticker: string;
  quantity: number;
  entry: number;
  entry_at: string;
  exit: number | null;
  exit_at: string | null;
  pnl: number | null;
  return_pct: number | null;
  held_days: number;
  is_open: boolean;
}

/** One order a decision pass produced. Buys and sells also land in
 * `AgentTrade`; an untrack, a research and an adjust move no shares and
 * appear only here. */
export interface AgentEventOrder {
  side: string;
  ticker: string;
  quantity: number;
  reason: string;
}

/** One decision pass, with the words that produced it.
 *
 * `prompt` and `response` are the point of the Events page: the counts and the
 * one-line reasoning describe a decision, and these two are it. Both are null
 * for passes before 2026-09-01 and cannot be backfilled. */
/** One exchange inside a decision pass.
 *
 * `reasoning` and `orders` are this turn's own — what the model said and
 * asked for on this specific call, parsed the same way the backend parses
 * every answer. Not the same thing as `AgentEvent.orders`: that is what
 * actually happened across the whole pass, after screening; this is what one
 * turn asked for, whether or not it was carried out. A read shows up here
 * honestly as a read, even though a read never reaches `screen`.
 *
 * Optional because a run recorded before 2026-09-15 has turns with no such
 * keys at all, and a published snapshot can be older still. */
export interface DecisionTurn {
  prompt: string;
  response: string;
  thinking: string | null;
  reasoning?: string;
  orders?: AgentEventOrder[];
}

export interface AgentEvent {
  id: number;
  ran_at: string;
  /** When the agent asked to be woken next. Null means it asked for nothing. */
  next_wakeup: string | null;
  reasoning: string;
  skipped: string | null;
  equity: number | null;
  cash: number | null;
  research_spent: number | null;
  prompt: string | null;
  response: string | null;
  /** The model's own reasoning on the way to `response` — most of what the
   * pass generated, and most of what it cost. Null on every pass before
   * 2026-09-09, when it was generated and discarded. */
  thinking: string | null;
  /** Every turn of the pass, oldest first. `prompt`/`response` above are the
   * LAST turn — the one the orders were screened from — so a reader that only
   * knows those still sees the decision it should.
   *
   * Empty on a single-turn pass and on every pass before 2026-09-10. A pass
   * has more than one turn when the agent asked to read an analysis, or when
   * a refused order earned it a correction.
   *
   * Optional because a published snapshot file written before 2026-09-10 has
   * no such key at all, and the static site serves whatever the last export
   * left on disk. */
  turns?: DecisionTurn[];
  /** What the pass cost the model — the provider's own counts and the wall
   * clock of the calls. Null on a pass that never asked, and on every pass
   * before 2026-09-09, when nothing counted them. */
  prompt_tokens: number | null;
  completion_tokens: number | null;
  seconds: number | null;
  orders: AgentEventOrder[];
  /** What Python declined before anything was sent. */
  refused: AgentOrder[];
  /** What the broker declined after it was sent. Kept apart from `refused`
   * because the two mean different things: one says the agent's arithmetic was
   * wrong, the other says it formed the order correctly and the world would
   * not take it. */
  failed: AgentOrder[];
}

/** One message the agent left for whoever maintains it — a tool it lacks, a
 * number it cannot see, a rule that contradicts another. Its own shape
 * rather than `AgentEventOrder` reused: a note has no ticker and no
 * quantity, so giving it either here would invite rendering it as an order. */
export interface AgentNote {
  id: number;
  ran_at: string;
  reason: string;
}

/** One day of the generated journal, as markdown. */
export interface JourneyEntry {
  date: string;
  markdown: string;
}
