"""SQLModel table definitions. Alembic (backend/database/alembic/) owns schema migrations —
tables are never created via ``SQLModel.metadata.create_all`` at runtime.
"""
import datetime

from sqlmodel import Field, SQLModel


class WatchlistTicker(SQLModel, table=True):
    ticker: str = Field(primary_key=True)


class BotSetting(SQLModel, table=True):
    """Generic key/value store — covers channel_id and anything added later
    without a schema change."""

    key: str = Field(primary_key=True)
    value: str


class Signal(SQLModel, table=True):
    """One row per completed TradingAgents analysis. ``outcome`` stays NULL
    until ``evaluation_date`` has passed and a follow-up price is fetched.

    ``message_id`` is the Discord message the analysis embed was posted as —
    reacting ✅ to that message executes the signal as a paper trade.
    Benchmark fields are filled at evaluation time alongside ``outcome``;
    they stay NULL on rows resolved before the columns existed or when the
    SPY window couldn't be fetched.

    ``horizon`` records which trade horizon the run used ("swing" or
    "position", see backend/services/signals.py HORIZONS). It decides how long
    to wait before grading and how wide the Hold band is, so a scorecard that
    mixed horizons would be comparing two different questions. NULL means a row
    predating the column.

    ``model`` records the LLM that produced the signal, so one model's track
    record can be read apart from another's — the only way trying a new model
    answers anything. NULL means a row predating the column, which in practice
    means gemma4-e2b-96k.

    ``duration_seconds`` through ``llm_calls`` are what the run cost. A
    self-hosted model is billed in GPU time and a cloud one in tokens, so
    choosing between them needs both, per run, from the provider's own
    accounting — see backend/services/llm_usage.py. All nullable: NULL means
    the run wasn't measured, never that it was free.

    The trade-plan fields (``entry_price`` through ``expected_value_r``) come
    from the trader stage rather than the final decision text — see
    backend/services/signals.py. They are the exit level and the quality of the
    bet, which the portfolio manager's own output doesn't carry. All nullable:
    NULL means "the model didn't state it", never zero, so a missing stop can
    never be read as a stop at $0.
    """

    id: int | None = Field(default=None, primary_key=True)
    ticker: str = Field(index=True)
    signal_date: datetime.date
    decision: str
    rationale: str
    time_horizon_text: str | None = None
    price_target: float | None = None
    price_at_signal: float
    evaluation_date: datetime.date = Field(index=True)
    price_at_evaluation: float | None = None
    outcome: str | None = None  # "pass" | "fail"
    evaluated_at: datetime.datetime | None = None
    message_id: str | None = Field(default=None, index=True)
    benchmark_price_at_signal: float | None = None
    benchmark_price_at_evaluation: float | None = None
    alpha_pct: float | None = None  # ticker % move − benchmark % move over the window
    outcome_vs_benchmark: str | None = None  # "pass" | "fail"
    price_target_hit: bool | None = None  # price touched the target within the window
    horizon: str | None = Field(default=None, index=True)  # "swing" | "position"
    model: str | None = Field(default=None, index=True)  # the LLM that produced it
    duration_seconds: float | None = None  # how long the graph took, queue time excluded
    prompt_tokens: int | None = None  # summed over every LLM call in the run
    completion_tokens: int | None = None
    llm_calls: int | None = None  # tells "endpoint reported no usage" from "run died early"
    # Names the trace file holding every LLM call of this run, when
    # LLM_TRACE_DIR is set. It is what turns a directory of traces into a
    # training set: joining here lets a filter keep only the runs the
    # Scorecard eventually graded a pass. See docs/model-training.md.
    trace_id: str | None = Field(default=None, index=True)
    # What caused this analysis: "sweep", "commissioned", "move", "earnings",
    # "manual". A signal produced because the stock just moved sharply is the
    # analyst reacting to a move already in the price; a scheduled one is not
    # reacting to anything. The agent is shown this, because how much weight a
    # signal deserves depends on it. NULL means the run predates the column —
    # never that it had no cause.
    trigger: str | None = None
    entry_price: float | None = None  # the level the trader proposed entering at
    stop_loss: float | None = None  # the level at which the thesis is wrong
    win_probability: float | None = None  # 0-100, the model's own estimate
    risk_reward: float | None = None  # reward ÷ risk, computed from entry/stop/target
    expected_value_r: float | None = None  # p×rr − (1−p), in R-multiples; signed
    # When the analysis *started*, to the second — added 2026-09-08 so a signal
    # can be placed on an intraday chart instead of smeared across whichever
    # daily candle its date falls on. `signal_date` alone was never enough for
    # that; it is a calendar date with no time of day.
    #
    # **Start, not finish, and it meant both until 2026-09-11.** record_signal
    # stored the finish while backfilled rows held the start, roughly sixteen
    # minutes apart on this hardware. The start won because it is the instant
    # actually observed — trace_id encodes it — where a finish has to be
    # computed. backend/scripts/backfill_signal_timestamps.py resets every row
    # that carries a trace_id. NULL means no trace existed to recover one
    # from, never a guessed time.
    created_at: datetime.datetime | None = None


class SignalReport(SQLModel, table=True):
    """Full analyst/researcher text attached to a signal (market, news,
    sentiment, fundamentals reports, investment plans) — the raw material for
    /ask and post-mortems. One row per (signal, report_type)."""

    id: int | None = Field(default=None, primary_key=True)
    signal_id: int = Field(foreign_key="signal.id", index=True)
    report_type: str  # final_state key, e.g. "market_report"
    content: str


class AgentTrade(SQLModel, table=True):
    """One order the autonomous agent placed on the Webull simulated account.

    This table is the agent's book of record, not the broker's. The simulated
    account is funded with $1,000,000 while the agent runs on a budget of a
    few hundred, so its buying power says nothing about what the agent may
    spend — cash has to be derived from these rows (budget − buys + sells).
    The broker's own positions are read only to check this ledger against, and
    a disagreement means a bug here.

    ``status`` is the broker's, not ours: a market order placed outside
    session hours sits ``pending`` until the open, and only a ``filled`` row
    may count toward cash or holdings. ``price`` stays NULL until then — a
    market order has no price at submission, and guessing one would put a
    fictional cost basis in the book.

    ``reason`` is the model's own words for why it placed this trade, kept so
    a losing streak can be read back rather than merely counted.
    """

    id: int | None = Field(default=None, primary_key=True)
    ticker: str = Field(index=True)
    side: str  # "buy" | "sell"
    quantity: float
    price: float | None = None  # fill price; NULL until the order fills
    placed_at: datetime.datetime
    filled_at: datetime.datetime | None = None
    client_order_id: str = Field(unique=True, index=True)  # ours, echoed by the broker
    broker_order_id: str | None = None
    status: str = Field(default="pending", index=True)  # "pending" | "filled" | "rejected"
    # A protective stop resting at the broker, not an order waiting to fill.
    # It is *meant* to sit pending indefinitely, so the UI and the exit path
    # both need to tell it apart from a market order that never filled.
    is_stop: bool = Field(default=False)
    # Where a resting exit is armed. ``price`` cannot carry it: that column is
    # the fill price and stays NULL until the order actually triggers, so
    # without this the level a position is protected at is knowable only by
    # reading it back out of ``reason``'s display text.
    limit_price: float | None = None
    # Which leg of the bracket this is: "stop" or "target". ``is_stop`` says
    # only that a row is a resting exit — both legs carry it — so without this
    # the two are told apart by reading ``reason``'s wording.
    exit_kind: str | None = None
    reason: str | None = None  # the model's stated rationale
    signal_id: int | None = Field(default=None, foreign_key="signal.id", index=True)


class ExitArmRequest(SQLModel, table=True):
    """A request to place the missing exits on a position, waiting for the open.

    The broker accepts a standalone order at any hour but refuses a *combo* —
    an OCO pair or a bracket — outside 9:30-16:00 ET, because linking legs
    needs the routing session that only runs during regular hours. So noticing
    an unprotected position in the evening used to mean remembering to come
    back in the morning.

    This is the remembering. The request is recorded when it is made and acted
    on at the next open, which turns "come back at 9:30" into a decision the
    person already made.

    ``status`` is ours, not the broker's: pending until a pass acts on it, then
    done or failed with the reason on ``message``.
    """

    id: int | None = Field(default=None, primary_key=True)
    ticker: str = Field(index=True)
    requested_at: datetime.datetime
    status: str = Field(default="pending", index=True)  # "pending" | "done" | "failed"
    completed_at: datetime.datetime | None = None
    message: str | None = None  # what happened, for the page that shows it


class AgentRun(SQLModel, table=True):
    """One decision pass, and what the agent said about it.

    The reasoning used to go to Discord and evaporate. That left the record
    with trades but no account of the days between them — and "it sat still
    for six days and then bought" is a story in which the sitting still is the
    interesting part. A book you can only read on the days money moved is a
    ledger, not a history.

    ``skipped`` holds why a pass did nothing at all: the market was shut, the
    agent was switched off, the model returned nothing readable. Those are
    different from "it considered the day and chose to wait", and a journey
    that conflated them would credit the agent with patience it never showed.
    """

    id: int | None = Field(default=None, primary_key=True)
    ran_at: datetime.datetime = Field(index=True)
    # When the agent asked to be woken next. NULL means it asked for nothing,
    # which is different from asking for the default — the scheduler's fallback
    # is a fact about the scheduler, not a decision the agent made.
    next_wakeup: datetime.datetime | None = None
    reasoning: str = ""
    placed: int = 0
    rejected: int = 0
    failed: int = 0
    adjusted: int = 0
    skipped: str | None = None
    # **The prompt the model was given and the answer it returned, verbatim.**
    # Counts and a one-line reasoning describe a decision; these are the
    # decision. Behaviour here is mostly prompt, so a month of runs across
    # three prompt revisions cannot be read apart afterwards unless each run
    # kept the words it was actually shown.
    #
    # Both are NULL for every run before 2026-09-01, and cannot be backfilled:
    # the prompt is assembled from a book, a watchlist and a signal list that
    # have all since moved.
    prompt: str | None = None
    response: str | None = None
    # **Every turn of the pass, as JSON**, oldest first: a list of
    # {"prompt", "response", "thinking"}. ``prompt`` and ``response`` above
    # remain the LAST turn, which is the one the accepted orders were screened
    # from and what every existing reader expects.
    #
    # A pass has had more than one turn since the refusal retry was built, and
    # only the last was ever kept — the retry rebuilt the prompt and overwrote
    # the first, so a two-turn pass was published as though it were one. That
    # was rare while a retry was the only second turn. It stopped being rare on
    # 2026-09-10, when the agent gained a way to ask to read an analysis.
    #
    # NULL for every run before that date, which is honest: those passes held
    # one turn, and it is in ``prompt``/``response``.
    turns: str | None = None
    # Every order the pass produced, as JSON: side, ticker, quantity, reason.
    # ``placed`` counts them and ``agenttrade`` holds the buys and sells, but
    # an untrack, a research and an adjust move no shares and leave no row
    # there — so without this the page cannot show what the agent actually did.
    orders: str | None = None
    # What the agent tried and was refused, as JSON: ticker, side, quantity
    # and the reason, for each.
    #
    # Counts alone throw away the interesting half. "3 rejected" says nothing;
    # "it tried to buy $1,944 against $1,000 of cash" is a finding, and it is
    # the kind that explains a whole week of the agent behaving oddly. The
    # placed orders need no copy here — they are in agenttrade — but a refusal
    # leaves no other trace at all.
    refusals: str | None = None
    # What the broker refused, as JSON: ticker, side, quantity and the reason.
    #
    # Separate from ``refusals``, which is what *Python* declined before an
    # order was ever sent. These two mean different things to the agent: a
    # screening refusal says its arithmetic was wrong, and a broker failure
    # says the world would not take the order it correctly formed. Only the
    # count was kept until 2026-09-02, which made a failure invisible to the
    # next morning's prompt — so the agent proposed the same thing again, was
    # refused again, and nothing in the record explained the loop.
    failures: str | None = None
    # The agent's own messages to whoever maintains it, as a JSON list.
    #
    # Nothing reads these automatically and nothing acts on them. They are
    # evidence about the prompt and the tool set — the thing this experiment
    # exists to produce — and until there was somewhere to put them the only
    # way to learn the agent was stuck was to infer it from twenty prompts.
    notes: str | None = None
    # The book as it stood when the pass finished, so the narrative never has
    # to recompute a past day's equity from prices that have since moved.
    equity: float | None = None
    cash: float | None = None
    research_spent: float | None = None
    # The model's own reasoning behind the answer — why it decided, not just
    # what it decided. Ollama returns it beside the content on /v1 and it was
    # discarded until 2026-09-09: generated, paid for, and thrown away. It is
    # most of what the model produces, and on one replayed pass ran to 4,034
    # characters against a 606-character answer. NULL on a pass that never
    # asked, on every pass before that date, and on any provider whose client
    # does not return it — see agent._invoke.
    thinking: str | None = None
    # What the model calls cost, summed across a retry: the provider's own
    # counts and the wall clock of the calls themselves, not of the whole
    # pass. Signal has carried the same three for an analysis since the cost
    # telemetry landed; a decision pass recorded its prompt and answer in full
    # and not one token of what they cost until 2026-09-09. The agent wakes
    # several times a day and every prompt change moves this number, so it is
    # a recurring cost the record could not see.
    #
    # NULL rather than 0 when nothing reported them — a zero would read as a
    # free call, which is the same rule Signal's columns follow.
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    seconds: float | None = None


class ResearchCharge(SQLModel, table=True):
    """What the agent paid to have one ticker analysed.

    Research is free to the agent today, so there is no pressure to choose
    what to look at — it is handed a watchlist. Charging for it makes "what is
    worth researching" a decision that can be graded, and makes the question
    the app exists to answer honest: whether the model earns its keep should
    include the cost of running the model.

    Stored rather than derived from a price times a count. The price is a
    setting and settings change; a charge is something that happened, and
    re-pricing history every time the setting moves would rewrite a book that
    has already been reported.

    ``signal_id`` is NULL when the analysis produced no signal — a delisted
    ticker, a failed run. The work was still done and is still charged, which
    is the point: research you paid for and learned nothing from is the normal
    case, not an accounting error.
    """

    id: int | None = Field(default=None, primary_key=True)
    ticker: str = Field(index=True)
    charged_at: datetime.datetime = Field(index=True)
    amount_usd: float
    signal_id: int | None = Field(default=None, foreign_key="signal.id")
    note: str | None = None


class Alert(SQLModel, table=True):
    """Sent-alert log for the intraday watchdog. ``dedupe_key`` is what stops
    a 15-minute loop from repeating itself: per ticker per day for
    moves/volume/stops, once ever per signal for target touches.
    """

    id: int | None = Field(default=None, primary_key=True)
    ticker: str = Field(index=True)
    alert_type: str  # "big_move" | "volume" | "stop_loss" | "target"
    dedupe_key: str = Field(unique=True, index=True)
    message: str
    created_at: datetime.datetime


class TickerStatus(SQLModel, table=True):
    """Tickers that have stopped producing market data, so every fetch path can
    skip them instead of asking again.

    A delisted symbol does not fail cleanly. yfinance keeps answering for the
    shell — AILEQ returned five bars in two months, all at $0.000001 — which
    looks to a cache like a ticker that is merely behind, so it refetches
    forever — and the agent can still pay $0.05 to have a company with no
    market analysed, which is what this flag exists to prevent.

    ``inactive`` is set automatically once no fresh bar has appeared for
    ``STALE_AFTER_TRADING_DAYS`` (see backend/services/listings.py), and cleared
    if the ticker ever produces one again — a halt can lift, and a manual
    override should not be needed for that. ``checked_at`` throttles the
    once-a-day recheck that makes recovery possible.
    """

    ticker: str = Field(primary_key=True)
    inactive: bool = False
    reason: str | None = None
    # Last bar seen, and when we last asked. Both NULL until the first check.
    last_bar_date: datetime.date | None = None
    checked_at: datetime.datetime | None = None
    # Set by a person, and never overwritten by detection. Lets a ticker be
    # force-ignored (or force-kept) when the heuristic gets it wrong.
    manual: bool = False
    # Which Webull category this ticker answers on — "US_STOCK" or "US_ETF".
    # Webull's market-data calls need it and the app cannot know it up front,
    # so it is learned by trying one and then the other. That belongs here
    # rather than in memory: it is a stable fact about the instrument, and
    # kept only in a process-local dict it was relearned on every restart —
    # at a cost of one extra request per ticker each time, on a host that
    # restarted 22 times on 2026-09-09 while being rate-limited for exactly
    # that kind of surplus request. NULL until the ticker has answered once.
    webull_category: str | None = None


class DailyBar(SQLModel, table=True):
    """Cached daily OHLCV, one row per (ticker, date).

    Only *completed* sessions are stored. A bar for the day in progress keeps
    changing, so caching it would serve a frozen mid-session snapshot as though
    it were a close — see backend/services/bars.py.

    Every yfinance history call in the app reads through this table. Before it
    existed the intraday watchdog refetched roughly a month of bars per ticker
    every 15 minutes to use two closes and a volume average, and the chart, the
    ATR, and signal grading each fetched the same bars again independently.
    Past bars never change, so refetching them is pure waste and pure
    rate-limit risk.
    """

    ticker: str = Field(primary_key=True)
    date: datetime.date = Field(primary_key=True)
    open: float
    high: float
    low: float
    close: float
    volume: float


class IntradayBar(SQLModel, table=True):
    """1-minute OHLCV, one row per (ticker, timestamp). Added 2026-09-08 so a
    signal, an alert, or a trade can be placed at the moment it actually
    happened instead of smeared across one daily candle.

    Sourced from Webull's history-bar endpoint (backend/services/intraday.py),
    which — unlike yfinance's hard 8-day cap on 1-minute data — returns real
    history arbitrarily far back, paged by ``end_time``. Backfilled once to
    the experiment's first trading day (2026-09-02) and kept current by the
    15-minute alert watchdog tick, the same cadence that already refreshes
    ``TickerPrice``.

    ``timestamp`` is UTC, to the minute, matching the bar's own start time as
    Webull reports it. Finer than any current use needs — a chart showing 5
    or 15-minute candles aggregates these rows at read time rather than
    storing every resolution separately, the same reasoning ``DailyBar``
    already applies to storing only completed sessions once.
    """

    ticker: str = Field(primary_key=True)
    timestamp: datetime.datetime = Field(primary_key=True)
    open: float
    high: float
    low: float
    close: float
    volume: float


class TickerPrice(SQLModel, table=True):
    """Last-known price cache, one row per ticker. Dashboard reads (the
    ticker list/detail routes) serve from this table instead of a live quote
    call. Every live fetch anywhere in the app (get_current_price, the
    watchdog's daily snapshot, the frontend's manual refresh) writes its
    result here, so the cache builds up passively over time."""

    ticker: str = Field(primary_key=True)
    price: float
    fetched_at: datetime.datetime
    source: str | None = None  # "webull" | "yfinance", for debugging


