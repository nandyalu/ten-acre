"""Is the agent worth running?

The agent picks stocks with an LLM. That only earns its keep if it beats the
two things it could trivially be replaced by:

- **SPY buy-and-hold** — did picking anything beat picking nothing?
- **A mechanical follower** — a rule with no model in it that buys every Buy
  signal in equal weight and sells on a Sell signal or when the signal matures.
  If the agent cannot beat this, the model is adding cost and noise, and you
  should run the rule instead.

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
    price_now = get_shown_price("SPY")
    if not history or price_now is None:
        log.warning("No SPY history from %s — skipping the buy-and-hold baseline", since)
        return None
    entry = history[0].close
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
    """Buy every Buy signal in equal weight, sell on a Sell signal or when the
    signal matures. No model, no judgement, no memory.

    Signals are walked in date order and priced at ``price_at_signal`` — the
    price the analysis itself saw — so this is what a rule following the same
    signals would have achieved, not a rule with hindsight about entry timing.
    """
    signals = sorted(
        (s for s in db.get_recent_signals(limit=1000) if s.signal_date >= since),
        key=lambda s: s.signal_date,
    )
    cash = budget
    slot = budget / _MECHANICAL_SLOTS
    held: dict[str, tuple[float, float]] = {}  # ticker -> (shares, entry price)
    trades = 0

    for signal in signals:
        price = signal.price_at_signal
        if not price:
            continue
        if signal.decision in SELLISH_DECISIONS and signal.ticker in held:
            shares, _ = held.pop(signal.ticker)
            cash += shares * price
            trades += 1
        elif signal.decision in BUYISH_DECISIONS and signal.ticker not in held:
            shares = int(min(slot, cash) // price)
            if shares:
                cash -= shares * price
                held[signal.ticker] = (shares, price)
                trades += 1

    # Anything still open is valued at today's price, exactly as the agent's own
    # book is, so the two are compared on the same basis.
    open_value = 0.0
    for ticker, (shares, entry) in held.items():
        price_now = get_shown_price(ticker)
        open_value += shares * (price_now if price_now is not None else entry)

    # It reads the same analyses the agent does, so it pays for them too.
    # Charging the agent alone would handicap it against its own yardstick and
    # quietly break the one comparison this module exists to make. SPY reads
    # nothing and pays nothing, which is the honest asymmetry: it is the
    # "was any of this worth doing" baseline.
    researched = research.total_spent()
    note = f"equal weight, {_MECHANICAL_SLOTS} slots"
    if researched:
        note += f", less ${researched:,.2f} of research"
    return Strategy(
        name="Mechanical signal-follower",
        equity=cash + open_value - researched,
        invested=sum(shares * entry for shares, entry in held.values()),
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
