"""Pydantic response models for the web API — an explicit contract decoupled
from the SQLModel table classes and internal dataclasses, so the frontend
gets a stable shape to mirror as TS interfaces regardless of DB schema
changes. Every model uses ``from_attributes`` so a route can validate
directly off a dataclass/SQLModel instance (including dataclass
``@property`` values, which ``from_attributes`` reads via ``getattr`` same
as a plain field) instead of hand-building dicts.
"""
from datetime import date, datetime, timezone

from pydantic import BaseModel, ConfigDict, field_serializer, model_validator


class Schema(BaseModel):
    @field_serializer("*", when_used="json")
    def _stamp_utc(self, value):
        """Mark every naive datetime as UTC on the way out.

        Everything this app stores is UTC — every writer calls
        ``datetime.now(timezone.utc)``. SQLite has no timezone type, so the
        value comes back naive and serializes as ``2026-09-03T11:35:39`` with
        no offset.

        **A browser parses that as local time**, not UTC. The instant is then
        wrong by the reader's own offset, which is invisible to anyone on UTC
        and an hours-long error for everyone else. It stayed hidden because
        the frontend also formatted in local time and printed a fixed "UTC"
        label, so the two errors cancelled and the number looked right.

        Stamping the offset here is what makes the instant true, and is the
        precondition for rendering any timestamp in the reader's own zone.

        Plain ``date`` fields are deliberately untouched. A calendar date has
        no zone, and attaching one moves it across midnight for every reader
        west of UTC — "graded 3 September" would read as the 2nd.
        """
        if type(value) is datetime and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


class OrmModel(Schema):
    model_config = ConfigDict(from_attributes=True)


class OhlcBarOut(OrmModel):
    date: str
    # Unix seconds, UTC — added 2026-09-08 alongside the intraday bar cache.
    # `date` alone cannot place a bar within a day, and lightweight-charts
    # needs a real timestamp (not a "YYYY-MM-DD" string) to draw sub-day
    # candles at all. Kept alongside `date` rather than replacing it, since
    # `date` is still what a human-readable label wants.
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float


class SignalOut(OrmModel):
    id: int
    ticker: str
    signal_date: date
    decision: str
    rationale: str
    time_horizon_text: str | None
    price_target: float | None
    price_at_signal: float
    evaluation_date: date
    price_at_evaluation: float | None
    outcome: str | None
    evaluated_at: datetime | None
    message_id: str | None
    benchmark_price_at_signal: float | None
    benchmark_price_at_evaluation: float | None
    alpha_pct: float | None
    outcome_vs_benchmark: str | None
    price_target_hit: bool | None
    horizon: str | None
    model: str | None
    # Why this analysis ran: "sweep", "commissioned", "move", "earnings",
    # "manual". NULL on rows written before it was recorded.
    trigger: str | None = None
    duration_seconds: float | None
    prompt_tokens: int | None
    completion_tokens: int | None
    llm_calls: int | None
    entry_price: float | None
    stop_loss: float | None
    win_probability: float | None
    risk_reward: float | None
    expected_value_r: float | None
    # When the analysis started, to the second. NULL on rows that predate the
    # column and had no trace_id to recover it from — see
    # backend/scripts/backfill_signal_timestamps.py. `signal_date` alone is a
    # calendar date and cannot place a signal within a day on a chart.
    created_at: datetime | None = None
    # What this run cost. NULL when it was never measured — a zero would read
    # as a free analysis rather than an unrecorded one.
    cost_usd: float | None = None
    # "vendor" (a list-price estimate) or "electricity" (a self-hosted run's
    # GPU draw). Kept apart because they are not the same kind of dollar.
    cost_basis: str | None = None

    @model_validator(mode="after")
    def _price_the_run(self):
        """Derived here rather than at every call site, and never stored.

        Prices change; a dollar figure written into the row would freeze one
        day's rate card into the record and quietly stop matching. The tokens
        are the fact, the cost is the reading.
        """
        from backend.services import llm_cost

        cost = llm_cost.estimate(
            self.model, self.prompt_tokens, self.completion_tokens, self.duration_seconds
        )
        if cost is not None:
            self.cost_usd = round(cost.usd, 4)
            self.cost_basis = cost.basis
        return self


class SignalDetailOut(SignalOut):
    reports: dict[str, str] = {}
    # What the auto trader did with this call, if anything. Kept apart from the
    # signal's own grade on purpose: one says whether the analysis was right
    # over its horizon, the other what the exit rules made of it.
    agent_trades: list["AgentTradeRowOut"] = []


class DecisionStatsOut(OrmModel):
    total: int
    passes: int
    vs_benchmark_total: int
    vs_benchmark_passes: int
    avg_move_pct: float | None


class ScorecardOut(OrmModel):
    resolved: int
    pending: int
    passes: int
    vs_benchmark_total: int
    vs_benchmark_passes: int
    avg_alpha_pct: float | None
    target_total: int
    target_hits: int
    by_decision: dict[str, DecisionStatsOut]
    by_model: dict[str, DecisionStatsOut]
    by_ticker: dict[str, tuple[int, int]]


class TickerSummaryOut(Schema):
    ticker: str
    current_price: float | None
    price_updated_at: datetime | None
    latest_signal: SignalOut | None
    # True when the ticker has stopped producing market data (delisted, halted,
    # or simply wrong). Every fetch path skips it, so without saying so the
    # dashboard would just show a stale price and no explanation.
    inactive: bool = False
    inactive_reason: str | None = None


class RestingExitOut(OrmModel):
    kind: str  # "stop" | "target"
    price: float
    quantity: float


class AgentPositionOut(OrmModel):
    """What the auto trader holds in one ticker, and the orders resting on it.

    ``exits`` are what the broker will actually execute, which is not the same
    claim as the signal's stop and target shown beside them.
    """

    quantity: float
    avg_cost: float
    price: float | None
    opened: date | None
    held_days: int | None
    market_value: float | None
    unrealized_pct: float | None
    exits: list[RestingExitOut]
    unprotected: bool
    arm_queued: bool = False


class LotOut(OrmModel):
    book: str  # always "agent" — the other two books ended 2026-09-01
    quantity: float
    entry: float
    entry_at: date
    exit: float | None
    exit_at: date | None
    pnl: float | None  # NULL while the lot is open — its result is not decided
    return_pct: float | None
    held_days: int
    signal_id: int | None


class TickerDetailOut(Schema):
    ticker: str
    current_price: float | None
    price_updated_at: datetime | None
    latest_signal: SignalOut | None
    # The agent's position. `real_position` and `paper_position` sat here until
    # 2026-09-03, naming two classes deleted with their books on 2026-09-01.
    # Pydantic cannot build a model whose annotation names a missing class, so
    # every ticker detail request raised before it ran — the page was 500 for
    # two days. The route had already stopped passing them and the frontend had
    # already stopped reading them; only the annotations were left.
    agent_position: AgentPositionOut | None = None
    inactive: bool = False
    inactive_reason: str | None = None


class TradeOut(Schema):
    """One filled buy or sell by the agent, for the ticker timeline.

    Only filled orders appear. A pending or rejected order marks no point on a
    chart, and drawing one would show a trade at a price that was never paid.
    """

    side: str  # "buy" | "sell"
    date: date
    # The real fill time, to the second — added 2026-09-08 alongside the
    # intraday bar cache, so a trade can be placed on the chart at the
    # moment it actually happened rather than smeared across `date`'s whole
    # day. This is when the app noticed the fill by polling the broker, not
    # a fill time the broker itself reports — see AgentTrade.filled_at.
    filled_at: datetime
    price: float
    quantity: float


class TickerEventsOut(Schema):
    """Everything that happened to one ticker, in one call.

    The chart overlays and the timeline below it are the same events drawn two
    ways, so fetching them separately would let the two views disagree while
    one request was still in flight.
    """

    ticker: str
    bars: list[OhlcBarOut]
    signals: list[SignalOut]
    alerts: list[AlertOut]
    trades: list[TradeOut]
    # Lots, FIFO-matched across all three books: what was bought, what became
    # of it, and what it made or lost. ``trades`` above is the raw fills.
    lots: list[LotOut] = []


class ActionResultOut(Schema):
    message: str


class AlertOut(OrmModel):
    id: int
    ticker: str
    alert_type: str
    message: str
    created_at: datetime


class DigestOut(OrmModel):
    week_start: date
    resolved: list[SignalOut]
    new_signals: list[SignalOut]
    alerts: list[AlertOut]
    win_rate_30d: tuple[int, int]
    win_rate_all: tuple[int, int]
    book_lines: list[str]


class RegimeOut(OrmModel):
    as_of: date
    vix: float | None
    spy_price: float | None
    spy_ma200: float | None
    curve_spread_pct: float | None
    spy_vs_ma_pct: float | None
    label: str
    emoji: str


class SetupCheckOut(Schema):
    """One requirement, and how to satisfy it.

    Carries no value of anything: ``ready`` says whether a thing is
    configured, never what it is configured to. See
    backend/services/setup_check.py — this payload is safe to publish, and
    stays that way only if no field here ever holds a secret.
    """

    key: str
    label: str
    ready: bool
    blocking: bool  # False for the optional ones: Discord, FRED
    detail: str
    fix: str  # the literal lines to paste, empty once satisfied
    # For the one requirement fixed in the app rather than the environment.
    # A route, never a value — the no-secrets rule is unaffected.
    action_path: str = ""
    action_label: str = ""


class SetupStatusOut(Schema):
    ready: bool  # every blocking check passes
    first_run: bool  # the agent has never been switched on
    checks: list[SetupCheckOut] = []


class SettingsOut(Schema):
    # The day THIS deployment's experiment began. Served rather than compiled
    # into the frontend so a second container does not announce itself as
    # being on day eight of the first one's run — see
    # backend/services/experiment.py.
    experiment_start: date
    horizon: str  # "swing" | "position"
    llm_model: str  # the LLM every analysis runs on
    # Everything the LLM endpoint currently serves. Empty when it couldn't be
    # reached, which the UI shows as a plain text field rather than a dropdown
    # of one — see backend/services/analysis.py list_models().
    llm_model_choices: list[str]
    alert_move_pct: float
    alert_stop_pct: float
    alert_volume_mult: float
    alerts_enabled: bool
    agent_enabled: bool
    agent_budget: float
    # What one analysis costs the agent. Served rather than written into the
    # frontend bundle, which had it as "$0.05" in six places — so a deployment
    # charging something else, or charging nothing, told its readers a price
    # it does not charge. Zero is a supported mode, not an unset value:
    # research.is_charging() is get_price() > 0.
    research_price: float
    # The conviction floor. Zero means off, which is the default — see
    # backend/services/agent.py on why it stays off until calibration earns it.
    agent_min_win_probability: float
    agent_min_risk_reward: float
    # True on the published copy, where every write is refused. The frontend
    # reads it to drop Settings from the nav and hide the arm-exits button —
    # presentation only. The refusal itself is middleware, so a hidden button
    # and a typed URL get the same answer.
    public: bool = False
    # True when this deployment runs the autonomous-analyst experiment and has
    # no real book or local paper book to show. Presentation only — it decides
    # nothing about whether orders are simulated.


class SettingsPatchIn(Schema):
    horizon: str | None = None
    llm_model: str | None = None
    alert_move_pct: float | None = None
    alert_stop_pct: float | None = None
    alert_volume_mult: float | None = None
    alerts_enabled: bool | None = None
    agent_enabled: bool | None = None
    agent_budget: float | None = None
    agent_min_win_probability: float | None = None
    agent_min_risk_reward: float | None = None


class AgentHoldingOut(Schema):
    ticker: str
    quantity: float
    avg_cost: float
    price: float | None
    market_value: float | None
    cost_basis: float
    unrealized_pnl: float | None


class AgentBookOut(Schema):
    enabled: bool
    # False means the app is pointed at production credentials, where the agent
    # refuses to trade at all. The dashboard says so loudly rather than showing
    # an idle agent with no explanation.
    sandbox: bool
    budget: float
    cash: float
    invested: float
    market_value: float
    equity: float
    realized_pnl: float
    return_pct: float
    holdings: list[AgentHoldingOut]


class AgentTradeOut(OrmModel):
    id: int
    ticker: str
    side: str
    quantity: float
    price: float | None  # NULL until the order fills
    placed_at: datetime
    filled_at: datetime | None
    status: str
    is_stop: bool
    limit_price: float | None  # where a resting exit is armed; NULL on a market order
    exit_kind: str | None  # "stop" | "target"; NULL on a market order
    reason: str | None
    signal_id: int | None


class CalibrationBandOut(OrmModel):
    label: str
    total: int
    passes: int
    stated_pct: float | None
    actual_pct: float | None
    gap: float | None  # claimed minus actual; positive is overconfident


class CalibrationOut(OrmModel):
    """Whether the model's stated confidence is worth anything.

    ``sorts_outcomes`` is the question that decides whether the number is
    usable at all: a probability that does not separate winners from losers
    cannot inform a threshold, however well centered it is. NULL until two
    bands hold signals.
    """

    resolved: int
    passes: int
    stated_pct: float | None
    actual_pct: float | None
    gap: float | None
    sorts_outcomes: bool | None
    verdict: str
    bands: list[CalibrationBandOut]


class UnprotectedPositionOut(Schema):
    """An auto-trader holding with no exit resting at the broker.

    Named as its own thing because it is an absence, and an absence is what
    nothing on a dashboard ever draws attention to on its own.
    """

    ticker: str
    quantity: float
    avg_cost: float
    held_days: int | None


class AgentEquityPointOut(OrmModel):
    date: date
    equity: float
    cash: float
    market_value: float


class AgentOrderOut(Schema):
    ticker: str
    side: str
    quantity: float
    reason: str | None = None
    why: str | None = None  # why it was rejected or failed, when it was


class AgentEventOrderOut(Schema):
    side: str
    ticker: str
    quantity: float = 0
    reason: str = ""
    # What the order asked for beyond its size — see ``agent._order_detail``.
    # On a buy or a sell: the order type, the price a limit named, and how
    # long it may wait. On an adjust: the levels it moves the exits to.
    #
    # All null on an order recorded before 2026-09-20, when these were parsed,
    # acted on and dropped, and on a plain market order, which names no price
    # of its own. A reader must treat a null as "not recorded", never as
    # "market".
    order_type: str | None = None
    limit_price: float | None = None
    time_in_force: str | None = None
    stop: float | None = None
    target: float | None = None


class AgentNoteOut(Schema):
    """One message the agent left for whoever maintains it.

    Its own shape rather than ``AgentEventOrderOut`` reused: a note has no
    ticker and no quantity, and giving it one here would let a future caller
    render it as if it were an order by mistake, the same trap the frontend's
    own ``notesIn``/`tradesIn`` split exists to avoid.
    """

    id: int
    ran_at: datetime
    reason: str


class AgentEventOut(Schema):
    """One decision pass, with the words that produced it.

    ``prompt`` and ``response`` are what the page exists for: the counts and
    the one-line reasoning describe a decision, and these two are it. Both are
    null for every pass before 2026-09-01 and cannot be reconstructed.
    """

    id: int
    ran_at: datetime
    # When the agent asked to be woken next. Null means it asked for nothing —
    # the scheduler's fallback is not a decision the agent gets credited with.
    next_wakeup: datetime | None = None
    # What started this pass, in the sentence the agent was shown — its own
    # chosen time, something noticed while it slept, the last call before the
    # close, a change to the app. Null on a skipped pass, which never reaches
    # the model, and on every pass before 2026-09-21, when nothing stored it.
    woke_because: str | None = None
    reasoning: str = ""
    skipped: str | None = None
    equity: float | None = None
    cash: float | None = None
    research_spent: float | None = None
    prompt: str | None = None
    response: str | None = None
    # The model's own reasoning behind the answer. NULL before 2026-09-09,
    # when it was generated and discarded.
    thinking: str | None = None
    # Every turn of the pass, oldest first. ``prompt``/``response`` above are
    # the LAST turn — the one the accepted orders were screened from — so a
    # reader that only knows those still sees the decision it should.
    #
    # Empty on a pass with one turn, and on every pass before 2026-09-10:
    # those held one turn and it is already above.
    turns: list[dict] = []
    # What the pass cost the model: the provider's own counts and the wall
    # clock of the calls. NULL on a pass that never asked, and on one from
    # before 2026-09-09, when nothing counted them.
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    seconds: float | None = None
    orders: list[AgentEventOrderOut] = []
    # What Python declined before anything was sent.
    refused: list[AgentOrderOut] = []
    # What the broker declined after it was sent. Kept apart from `refused`
    # because the two mean different things: one says the agent's arithmetic
    # was wrong, the other says it formed the order correctly and the world
    # would not take it.
    failed: list[AgentOrderOut] = []


class JourneyEntryOut(Schema):
    """One day of the generated journal, as markdown."""

    date: date
    markdown: str


class StrategyOut(OrmModel):
    name: str
    equity: float
    invested: float
    cash: float
    trades: int
    note: str


class AgentComparisonOut(Schema):
    """The agent against the two things it could be replaced by. The verdict is
    plain language on purpose — the point is being able to read "switch it off"
    without doing arithmetic."""

    budget: float
    since: date | None
    verdict: str
    strategies: list[StrategyOut]


class CandidateOut(OrmModel):
    """A screened ticker worth considering. Never followed automatically —
    each one added costs about seven minutes of GPU on every later sweep."""

    ticker: str
    name: str
    price: float
    volume: float
    change_pct: float | None
    source: str


class AgentTradeRowOut(OrmModel):
    """One lot's life. Exit and P/L stay null while the position is open — an
    unrealized number here would read as a booked one."""

    ticker: str
    quantity: float
    entry: float
    entry_at: datetime
    exit: float | None
    exit_at: datetime | None
    pnl: float | None
    return_pct: float | None
    held_days: int
    is_open: bool


# SignalDetailOut names AgentTradeRowOut before it is defined.
SignalDetailOut.model_rebuild()
