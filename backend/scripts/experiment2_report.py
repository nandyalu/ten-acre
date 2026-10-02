"""Experiment 2's success test, computed exactly as PLAN.md words it.

**This cannot trade.** It reads each book's database, reads daily closes, and
prints the result. Nothing it computes reaches an agent.

    python -m backend.scripts.experiment2_report \\
        --books /data/b1/trading.db /data/b2/trading.db /data/b3/trading.db /data/b4/trading.db \\
        --start 2026-10-12

**The primary test.** The mean return of the agent books against the means of
groups of the same size drawn from the random control books. The agent passes
when its mean is above the 95th percentile of those group means.

**The random control books** live through the same days as the agent books, so
market luck cancels out of the comparison and what is left is selection. Each
makes the same number of buys as the mean agent book, draws its ticker from
the candidate screens of the day it buys (``candidatescreen``, across every
book), its holding period and its size from what the agent books did, and
trades at the daily close. The agent books trade at intraday quotes. That
difference is reported, not corrected.

**Three results are possible**: pass, fail and inconclusive. Before the
measurement date, six months after the start, the result is "not yet" and the
numbers are not a verdict. Fewer than 100 closed trades across the agent
books is inconclusive by the written rule.

Daily closes come through ``bars.get_bars``, so run it with
``TEN_ACRE_DATA_DIR`` on a scratch directory: the bars it fetches are cached
there, never in a book's own database.
"""
import argparse
import dataclasses
import datetime
import json
import random
import sqlite3
import statistics
from collections import defaultdict

PASS_PERCENTILE = 95
MIN_CLOSED_TRADES = 100
MEASURE_AFTER_DAYS = 182
RESAMPLES = 10_000


@dataclasses.dataclass(frozen=True)
class Trade:
    ticker: str
    side: str  # "buy" | "sell"
    quantity: float
    price: float
    day: datetime.date


@dataclasses.dataclass
class Series:
    """One book, day by day, at the close."""

    days: list[datetime.date]
    equity: list[float]
    invested: list[float]
    held: list[frozenset[str]]


# --- reading a book ----------------------------------------------------------


def read_book(path: str) -> tuple[list[Trade], list[tuple[datetime.date, float]]]:
    """Filled trades and research charges, straight from one book's database."""
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    trades = [
        Trade(t, s, float(q), float(p), _day(f))
        for t, s, q, p, f in con.execute(
            "select ticker, side, quantity, price, filled_at from agenttrade "
            "where status = 'filled' and price is not null and filled_at is not null "
            "order by filled_at, id"
        )
    ]
    charges = [(_day(at), float(amount)) for at, amount in con.execute(
        "select charged_at, amount_usd from researchcharge"
    )]
    return trades, charges


def read_screens(paths: list[str]) -> dict[datetime.date, set[str]]:
    """Every name any book's screen offered, by day."""
    universe: dict[datetime.date, set[str]] = defaultdict(set)
    for path in paths:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        for at, ticker in con.execute("select screened_at, ticker from candidatescreen"):
            universe[_day(at)].add(ticker)
    return dict(universe)


def _day(value) -> datetime.date:
    return datetime.date.fromisoformat(str(value)[:10])


# --- the arithmetic, pure ----------------------------------------------------


def series(trades, charges, closes, days, budget) -> Series:
    """Equity, invested value and the tickers held, at each day's close.

    ``closes(ticker)`` is a date -> close dict. A day with no close for a held
    ticker carries the last close forward.
    """
    out = Series([], [], [], [])
    held: dict[str, float] = defaultdict(float)
    cash = budget
    last: dict[str, float] = {}
    trades = sorted(trades, key=lambda t: t.day)
    charges = sorted(charges)
    ti = ci = 0
    for day in days:
        while ti < len(trades) and trades[ti].day <= day:
            t = trades[ti]
            sign = 1 if t.side == "buy" else -1
            held[t.ticker] += sign * t.quantity
            cash -= sign * t.quantity * t.price
            last.setdefault(t.ticker, t.price)
            ti += 1
        while ci < len(charges) and charges[ci][0] <= day:
            cash -= charges[ci][1]
            ci += 1
        value = 0.0
        names = set()
        for ticker, quantity in held.items():
            if quantity <= 1e-9:
                continue
            close = closes(ticker).get(day)
            if close is not None:
                last[ticker] = close
            value += quantity * last.get(ticker, 0.0)
            names.add(ticker)
        out.days.append(day)
        out.equity.append(cash + value)
        out.invested.append(value)
        out.held.append(frozenset(names))
    return out


def round_trips(trades, days) -> list[tuple[int, float]]:
    """(trading days held, entry cost) for every closed lot, first in first out."""
    index = {d: i for i, d in enumerate(days)}
    lots: dict[str, list[list]] = defaultdict(list)
    closed = []
    for t in sorted(trades, key=lambda t: t.day):
        if t.side == "buy":
            lots[t.ticker].append([t.quantity, t.price, t.day])
            continue
        left = t.quantity
        while left > 1e-9 and lots[t.ticker]:
            lot = lots[t.ticker][0]
            used = min(left, lot[0])
            if t.day in index and lot[2] in index:
                closed.append((max(1, index[t.day] - index[lot[2]]), used * lot[1]))
            lot[0] -= used
            left -= used
            if lot[0] <= 1e-9:
                lots[t.ticker].pop(0)
    return closed


def random_book(rng, universe, days, closes, n_buys, holds, fractions, budget) -> float:
    """The return of one random book over ``days``."""
    trades: list[Trade] = []
    cash = budget
    open_until: list[tuple[int, Trade]] = []
    starts = sorted(rng.randrange(0, len(days) - 1) for _ in range(n_buys))
    for start in starts:
        # Close what is due first, so its cash is free for this buy.
        for exit_at, buy in sorted(open_until, key=lambda x: x[0]):
            if exit_at <= start:
                price = closes(buy.ticker).get(days[exit_at])
                if price:
                    trades.append(Trade(buy.ticker, "sell", buy.quantity, price, days[exit_at]))
                    cash += buy.quantity * price
                    open_until.remove((exit_at, buy))
        names = _names_on(universe, days[start])
        if not names:
            continue
        ticker = rng.choice(sorted(names))
        price = closes(ticker).get(days[start])
        if not price:
            continue
        quantity = int(rng.choice(fractions) * budget // price)
        if quantity < 1 or quantity * price > cash:
            continue
        buy = Trade(ticker, "buy", quantity, price, days[start])
        trades.append(buy)
        cash -= quantity * price
        open_until.append((min(start + rng.choice(holds), len(days) - 1), buy))
    end = series(trades, [], closes, days, budget)
    return end.equity[-1] / budget - 1 if end.equity else 0.0


def _names_on(universe, day) -> set[str]:
    """The screens of ``day``, or of the latest earlier day that has any."""
    earlier = [d for d in universe if d <= day]
    return universe[max(earlier)] if earlier else set()


def primary(agent_returns, random_returns, rng, resamples=RESAMPLES) -> dict:
    n = len(agent_returns)
    means = sorted(statistics.fmean(rng.choices(random_returns, k=n)) for _ in range(resamples))
    threshold = means[int(len(means) * PASS_PERCENTILE / 100) - 1]
    agent_mean = statistics.fmean(agent_returns)
    below = sum(1 for m in means if m < agent_mean)
    return {
        "agent_mean": agent_mean,
        "random_group_mean_p95": threshold,
        "agent_percentile": 100 * below / len(means),
        "passes": agent_mean > threshold,
    }


def exposure_matched_spy(book: Series, spy: dict) -> float:
    """SPY held each day at the fraction of equity the book had invested the
    day before. Separates stock selection from the choice to hold cash."""
    value = 1.0
    for i in range(1, len(book.days)):
        before, today = spy.get(book.days[i - 1]), spy.get(book.days[i])
        if before and today and book.equity[i - 1] > 0:
            value *= 1 + (book.invested[i - 1] / book.equity[i - 1]) * (today / before - 1)
    return value - 1


def max_drawdown(equity) -> float:
    peak, worst = float("-inf"), 0.0
    for value in equity:
        peak = max(peak, value)
        if peak > 0:
            worst = min(worst, value / peak - 1)
    return worst


def overlap(books: list[Series]) -> float | None:
    """The mean, over days and pairs of books, of the share of held tickers two
    books both hold. Near 1, the books are one book."""
    shares = []
    for i in range(len(books)):
        for j in range(i + 1, len(books)):
            for a, b in zip(books[i].held, books[j].held):
                if a | b:
                    shares.append(len(a & b) / len(a | b))
    return statistics.fmean(shares) if shares else None


def verdict(start, end, closed_trades, result) -> str:
    if end < start + datetime.timedelta(days=MEASURE_AFTER_DAYS):
        return f"not yet: the measurement date is {start + datetime.timedelta(days=MEASURE_AFTER_DAYS)}"
    if closed_trades < MIN_CLOSED_TRADES:
        return f"inconclusive: {closed_trades} closed trades, fewer than {MIN_CLOSED_TRADES}"
    return "pass" if result["passes"] else "fail"


# --- the script --------------------------------------------------------------


def main() -> int:
    from backend.services import bars

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--books", nargs="+", required=True, help="each book's trading.db")
    parser.add_argument("--start", required=True, type=datetime.date.fromisoformat)
    parser.add_argument("--end", type=datetime.date.fromisoformat, default=datetime.date.today())
    parser.add_argument("--budget", type=float, default=10_000.0)
    parser.add_argument("--random", type=int, default=500, help="random control books")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--out", default=None, help="write the full result as JSON here")
    args = parser.parse_args()
    rng = random.Random(args.seed)

    cache: dict[str, dict] = {}

    def closes(ticker: str) -> dict:
        if ticker not in cache:
            cache[ticker] = {
                datetime.date.fromisoformat(b.date): b.close
                for b in bars.get_bars(ticker, args.start, args.end)
            }
        return cache[ticker]

    # The books' own ledgers are already in post-split units. The bars cached
    # here must be too, before any is read.
    from backend.services import corporate_actions

    books = [read_book(p) for p in args.books]
    universe = read_screens(args.books)
    traded = {t.ticker for trades, _ in books for t in trades}
    corporate_actions.check(traded.union(*universe.values()) | {"SPY"})

    days = sorted(d for d in closes("SPY") if args.start <= d <= args.end)
    if len(days) < 2:
        print("Fewer than two sessions in the window.")
        return 1

    runs = [series(t, c, closes, days, args.budget) for t, c in books]
    agent_returns = [r.equity[-1] / args.budget - 1 for r in runs]
    trips = [trip for t, _ in books for trip in round_trips(t, days)]
    holds = [h for h, _ in trips] or [5]
    fractions = [cost / args.budget for _, cost in trips] or [0.2]
    n_buys = round(statistics.fmean(sum(1 for t in tr if t.side == "buy") for tr, _ in books))
    random_returns = [
        random_book(rng, universe, days, closes, n_buys, holds, fractions, args.budget)
        for _ in range(args.random)
    ]
    result = primary(agent_returns, random_returns, rng)
    spy = closes("SPY")
    report = {
        "window": [str(days[0]), str(days[-1])],
        "verdict": verdict(args.start, args.end, len(trips), result),
        "primary": result,
        "closed_trades": len(trips),
        "buys_per_random_book": n_buys,
        "books": [
            {
                "path": path,
                "return": ret,
                "exposure_matched_spy": exposure_matched_spy(run, spy),
                "max_drawdown": max_drawdown(run.equity),
            }
            for path, ret, run in zip(args.books, agent_returns, runs)
        ],
        "plain_spy": spy[days[-1]] / spy[days[0]] - 1,
        "overlap": overlap(runs),
        "note": "Random books trade at the daily close; the agent books at intraday quotes.",
    }
    print(json.dumps(report, indent=1, default=str))
    if args.out:
        with open(args.out, "w") as f:
            json.dump(report, f, indent=1, default=str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
