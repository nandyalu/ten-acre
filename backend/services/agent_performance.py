"""Is the agent worth running?

The agent picks stocks with an LLM. That only earns its keep if it beats the
two things it could trivially be replaced by:

- **SPY buy-and-hold** — did picking anything beat picking nothing?
- **A mechanical follower** — a rule with no model in it that buys every Buy
  signal a free slot can pay for, in equal weight, and holds until a Sell
  signal or the signal's own maturity date, whichever comes first. If the agent
  cannot beat this, the model is adding cost and noise, and you should run the
  rule instead.

  **The rule can only follow signals the agent paid for**, since nothing is
  analysed on a schedule. So it isolates the agent's judgement, not its choice
  of what to research — the maturity exit is what stops the agent's research
  habits leaking into the rule's exits too. SPY is the baseline that owes
  nothing to either.

The second is the decisive one, and it is why this module exists. Everything it
needs is already stored — the signals carry the price they were made at, the
price they were graded at, and the SPY prices over the same window — so both
baselines are computed from history rather than simulated forward. They start
the day the agent placed its first order, because comparing a strategy that ran
for a week against one that ran for a year says nothing.

Everything here is blocking (DB + prices) — call via asyncio.to_thread.
"""
import datetime
import logging
from dataclasses import dataclass, field

from backend.database import db
from backend.services import agent_book, research
from backend.services.positions import get_shown_price
from backend.services.signals import BUYISH_DECISIONS, SELLISH_DECISIONS

log = logging.getLogger("ten-acre.agent_performance")

# How many ways the mechanical follower splits its budget. Equal weight across
# a handful of names is the plainest rule that is still a strategy — one
# position at a time would be a concentration bet, and twenty would be an index.
_MECHANICAL_SLOTS = 5


@dataclass
class Strategy:
    """One line of the comparison."""

    name: str
    equity: float
    invested: float
    cash: float
    trades: int
    note: str = ""

    def return_pct(self, budget: float) -> float:
        return (self.equity / budget - 1) * 100 if budget else 0.0


@dataclass
class Comparison:
    budget: float
    since: datetime.date | None
    strategies: list[Strategy] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        """Plain-language answer to "is the LLM earning its keep". Deliberately
        refuses to answer on a short record: three trades of hindsight is not
        evidence, and a confident verdict on it would be worse than none."""
        if self.since is None:
            return "The agent has not traded yet."
        agent = next((s for s in self.strategies if s.name == "Agent"), None)
        if agent is None or agent.trades < 10:
            traded = agent.trades if agent else 0
            return (
                f"Only {traded} agent trade(s) so far — too few to judge. "
                "Ten or more before this means anything."
            )
        others = [s for s in self.strategies if s.name != "Agent"]
        beaten = [s for s in others if agent.equity > s.equity]
        if len(beaten) == len(others):
            return "The agent is ahead of both baselines."
        if not beaten:
            return (
                "The agent is behind both baselines — the model is costing you "
                "money against rules that need no model at all."
            )
        return f"The agent beats {', '.join(s.name for s in beaten)} but not the rest."


def _first_trade_date(trades) -> datetime.date | None:
    filled = [t for t in trades if t.status == "filled"]
    return min((t.filled_at or t.placed_at).date() for t in filled) if filled else None


def _agent_strategy(book: agent_book.Book, trades) -> Strategy:
    filled = [t for t in trades if t.status == "filled"]
    return Strategy(
        name="Agent",
        equity=book.equity,
        invested=book.invested,
        cash=book.cash,
        trades=len(filled),
    )


def _spy_strategy(budget: float, since: datetime.date) -> Strategy | None:
    """The whole budget into SPY on the agent's first trading day, held.

    Fractional shares, deliberately, unlike the mechanical follower's whole
    ones. This baseline answers "what would the market have returned", not
    "what could I have executed" — and at a $773 share price a $1,000 budget
    buys one share and leaves 23% in cash, so a whole-share benchmark would
    quietly credit the market with a quarter less than it made. The follower
    stays whole-share because it is a strategy you would actually run.
    """
    from backend.services import bars

    history = bars.get_bars("SPY", since, include_today=True)
    if not history:
        log.warning("No SPY history from %s — skipping the buy-and-hold baseline", since)
        return None
    # **Priced from its own last bar, not the price cache (2026-09-18).** The
    # cache holds what the watchdog fetches, which is every tracked ticker, and
    # SPY is never tracked — so ``get_shown_price("SPY")`` was None from
    # 2026-09-10, when this read moved off the vendor, and the site drew no
    # market line at all for eight days. ``include_today`` above already
    # carries the session in progress, so the last bar is as fresh as the cache
    # would have been, from a request this function was already making.
    entry = history[0].close
    price_now = history[-1].close
    shares = budget / entry
    return Strategy(
        name="SPY buy-and-hold",
        equity=shares * price_now,
        invested=budget,
        cash=0.0,
        trades=1,
        note=f"{shares:.3f} shares at ${entry:,.2f}",
    )


def _mechanical_strategy(budget: float, since: datetime.date) -> Strategy:
    """Buy on a Buy signal while a slot is free, and hold until a Sell signal
    or the signal's own maturity, whichever comes first. No model, no
    judgement, no memory.

    **It holds at most five names, and a slot is what the rule is worth now
    divided by the slots still free** — so the budget is always fully deployed
    when there are signals to deploy it into, and a rule that has doubled its
    money puts twice as much into its next position. A fixed fraction of the
    *starting* budget cannot do both: it either leaves the winnings in cash
    forever or, with no count to stop it, opens a sixth and a tenth position as
    the cash grows. Dividing the cash by the free slots needs no count of its
    own and self-corrects — five free slots make each one a fifth.

    Open positions count as one slot each rather than being marked to market,
    which is the one simplification here: an unrealized gain is not redeployed
    until the position closes and the gain is really in the cash. A signal
    arriving with all five slots taken is missed, not queued, the way a fully
    invested account misses one.

    **Shares are fractional.** Whole shares would make the five weights merely
    approximately equal — a $7 name would fill its slot and a $333 name would
    leave a fifth of it in cash — and equal weight is the whole of what this
    rule is.

    Signals are walked in date order and priced at ``price_at_signal`` — the
    price the analysis itself saw — so this is what a rule following the same
    signals would have achieved, not a rule with hindsight about entry timing.

    **The maturity exit is what keeps the rule independent of the agent.**
    Nothing has been analysed on a schedule since 2026-09-08: the agent decides
    what gets a fresh look, so a name it stops revisiting produces no Sell
    signal at all. A rule that sold only on a Sell signal would then hold that
    position for as long as the agent's attention stayed elsewhere, and its
    exits would be a reading of the agent's research habits rather than of the
    signals. Every buy therefore also exits at its own signal's
    ``evaluation_date``, at the ``price_at_evaluation`` the grader recorded —
    a date the analysis fixed when it was written, and a price neither the rule
    nor the agent chose. A signal still maturing has no graded price yet, so
    its position stays open and is marked at today's price.
    """
    signals = sorted(
        (s for s in db.get_recent_signals(limit=1000) if s.signal_date >= since),
        key=lambda s: s.signal_date,
    )
    cash = budget
    # ticker -> (shares, entry price, the signal that opened it). The signal is
    # kept because only its own maturity closes the position: a later signal on
    # the same ticker is a different call with a different horizon.
    held: dict[str, tuple[float, float, object]] = {}
    trades = 0

    # Two streams walked as one: the signals on the day they were made, and the
    # forced exits on the day each one matures. A maturity sorts ahead of a
    # signal on the same date, so the slot and the cash it frees are available
    # to that date's buys.
    events = [(s.signal_date, 1, "signal", s) for s in signals]
    events += [
        (s.evaluation_date, 0, "mature", s)
        for s in signals
        if s.decision in BUYISH_DECISIONS and s.price_at_evaluation
    ]
    events.sort(key=lambda e: (e[0], e[1]))

    for _date, _first, kind, signal in events:
        if kind == "mature":
            position = held.get(signal.ticker)
            # Identity, not ticker: the position may have been opened by an
            # earlier signal whose own maturity has not arrived yet.
            if position and position[2] is signal:
                shares, _entry, _opened_by = held.pop(signal.ticker)
                cash += shares * signal.price_at_evaluation
                trades += 1
            continue
        price = signal.price_at_signal
        if not price:
            continue
        if signal.decision in SELLISH_DECISIONS and signal.ticker in held:
            shares, _entry, _opened_by = held.pop(signal.ticker)
            cash += shares * price
            trades += 1
        elif signal.decision in BUYISH_DECISIONS and signal.ticker not in held:
            free_slots = _MECHANICAL_SLOTS - len(held)
            if free_slots <= 0 or cash <= 0:
                continue
            # The cash split evenly over the slots still free. With nothing
            # held that is a fifth of the book; with four names held it is
            # whatever one slot's worth of cash is left, which is the same
            # number arrived at from the other end.
            slot = cash / free_slots
            shares = slot / price
            cash -= shares * price
            held[signal.ticker] = (shares, price, signal)
            trades += 1

    # Anything still open is valued at today's price, exactly as the agent's own
    # book is, so the two are compared on the same basis.
    open_value = 0.0
    for ticker, (shares, entry, _opened_by) in held.items():
        price_now = get_shown_price(ticker)
        open_value += shares * (price_now if price_now is not None else entry)
    invested = sum(shares * entry for shares, entry, _opened_by in held.values())

    # It reads the same analyses the agent does, so it pays for them too.
    # Charging the agent alone would handicap it against its own yardstick and
    # quietly break the one comparison this module exists to make. SPY reads
    # nothing and pays nothing, which is the honest asymmetry: it is the
    # "was any of this worth doing" baseline.
    researched = research.total_spent()
    note = f"{_MECHANICAL_SLOTS} equal slots, exits at a Sell or the horizon"
    if researched:
        note += f", less ${researched:,.2f} of research"
    return Strategy(
        name="Mechanical signal-follower",
        equity=cash + open_value - researched,
        invested=invested,
        cash=cash - researched,
        trades=trades,
        note=note,
    )


def compare() -> Comparison:
    """The agent against both baselines, over the agent's own lifetime."""
    trades = db.get_agent_trades()
    budget = agent_book.get_budget()
    since = _first_trade_date(trades)
    if since is None:
        return Comparison(budget=budget, since=None)

    book = agent_book.build_book(price_lookup=get_shown_price)
    strategies = [_agent_strategy(book, trades), _mechanical_strategy(budget, since)]
    spy = _spy_strategy(budget, since)
    if spy is not None:
        strategies.append(spy)
    return Comparison(budget=budget, since=since, strategies=strategies)
