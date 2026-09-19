"""Is the LLM earning its keep?

The comparison exists to be able to answer no. A mechanical rule that buys
every Buy signal in equal weight needs no model, no GPU, and no prompt
engineering — if it wins, the agent should be switched off. These tests pin
that the comparison is honest about that, and that it refuses to draw a
conclusion from a handful of trades.
"""
import datetime

import pytest

from backend.database.models import AgentTrade, Signal
from backend.services import agent_performance


def _signal(ticker="AAA", decision="Buy", price=100.0, day=1):
    return Signal(
        ticker=ticker,
        signal_date=datetime.date(2026, 8, day),
        decision=decision,
        rationale="",
        price_at_signal=price,
        evaluation_date=datetime.date(2026, 8, day + 14),
    )


def _trade(ticker="AAA", side="buy", quantity=2, price=100.0, day=1, status="filled"):
    return AgentTrade(
        ticker=ticker,
        side=side,
        quantity=quantity,
        price=price,
        status=status,
        client_order_id=f"{ticker}{side}{day}{quantity}",
        placed_at=datetime.datetime(2026, 8, day, 14, 0),
        filled_at=datetime.datetime(2026, 8, day, 14, 0),
    )


@pytest.fixture
def world(monkeypatch):
    """Wires the module's four seams: agent trades, signals, budget, prices."""

    def build(trades=(), signals=(), prices=None, budget=1000.0):
        monkeypatch.setattr(agent_performance.db, "get_agent_trades", lambda: list(trades))
        monkeypatch.setattr(
            agent_performance.db, "get_recent_signals", lambda limit=1000: list(signals)
        )
        monkeypatch.setattr(agent_performance.agent_book, "get_budget", lambda: budget)
        monkeypatch.setattr(
            # Cached, not live: this is a read path since 2026-09-10 —
            # see positions.get_shown_price and
            # test_pages_read_cached_prices.py.
            agent_performance, "get_shown_price", lambda t: (prices or {}).get(t)
        )
        # SPY history is a separate seam; default to unavailable so tests that
        # don't care about it get two strategies instead of three.
        monkeypatch.setattr(agent_performance, "_spy_strategy", lambda budget, since: None)

    return build


def test_no_trades_means_no_comparison(world, monkeypatch):
    world()
    monkeypatch.setattr(
        agent_performance, "_agent_strategy", lambda book, trades: None
    )
    result = agent_performance.compare()

    assert result.since is None
    assert result.strategies == []
    assert "not traded yet" in result.verdict


def test_the_mechanical_rule_buys_every_buy_signal(world):
    world(signals=[_signal("AAA", "Buy", 100.0, day=1), _signal("BBB", "Buy", 50.0, day=2)],
          prices={"AAA": 110.0, "BBB": 50.0})

    rule = agent_performance._mechanical_strategy(1000.0, datetime.date(2026, 8, 1))

    # $1,000 over 5 slots is $200 a name: 2 shares of AAA, 4 of BBB.
    assert rule.trades == 2
    assert rule.equity == pytest.approx(1000.0 - 200.0 - 200.0 + 2 * 110.0 + 4 * 50.0)


def test_the_mechanical_rule_sells_on_a_sell_signal(world):
    world(
        signals=[_signal("AAA", "Buy", 100.0, day=1), _signal("AAA", "Sell", 120.0, day=3)],
        prices={"AAA": 120.0},
    )

    rule = agent_performance._mechanical_strategy(1000.0, datetime.date(2026, 8, 1))

    assert rule.trades == 2
    assert rule.invested == 0.0
    # Bought 2 at 100, sold 2 at 120: $40 better than the budget.
    assert rule.equity == pytest.approx(1040.0)


def test_the_mechanical_rule_ignores_hold_signals(world):
    """A Hold is not a trade. If the rule acted on it, it would not be a
    baseline for the agent's signal-following — it would be a different bet."""
    world(signals=[_signal("AAA", "Hold", 100.0, day=1)], prices={"AAA": 100.0})

    rule = agent_performance._mechanical_strategy(1000.0, datetime.date(2026, 8, 1))

    assert rule.trades == 0
    assert rule.equity == 1000.0


def test_the_mechanical_rule_cannot_spend_more_than_the_budget(world):
    world(
        signals=[_signal(f"T{i}", "Buy", 100.0, day=i + 1) for i in range(8)],
        prices={f"T{i}": 100.0 for i in range(8)},
    )

    rule = agent_performance._mechanical_strategy(1000.0, datetime.date(2026, 8, 1))

    assert rule.cash >= 0
    assert rule.invested <= 1000.0


def test_the_spy_baseline_is_priced_from_its_own_bars(monkeypatch):
    """SPY is never tracked, so the price cache the other read paths use never
    holds it; its last bar, which already carries today's session, is the
    current price. From 2026-09-10 to 2026-09-18 this read the cache instead
    and the site drew no market line at all."""
    from types import SimpleNamespace

    from backend.services import bars

    monkeypatch.setattr(
        bars, "get_bars",
        lambda ticker, start, **kw: [SimpleNamespace(close=100.0), SimpleNamespace(close=110.0)],
    )
    monkeypatch.setattr(agent_performance, "get_shown_price", lambda t: None)

    spy = agent_performance._spy_strategy(1000.0, datetime.date(2026, 8, 1))

    assert spy is not None
    assert spy.equity == pytest.approx(1100.0)
    assert spy.note == "10.000 shares at $100.00"


def test_signals_before_the_agent_started_are_excluded(world):
    """Comparing a strategy that ran a week against one that ran a year says
    nothing, so both baselines start on the agent's first trading day."""
    world(
        signals=[_signal("OLD", "Buy", 100.0, day=1), _signal("NEW", "Buy", 100.0, day=9)],
        prices={"OLD": 100.0, "NEW": 100.0},
    )

    rule = agent_performance._mechanical_strategy(1000.0, datetime.date(2026, 8, 5))

    assert rule.trades == 1


# --- the verdict ---------------------------------------------------------------


def _comparison(agent_equity, others, trades=20):
    return agent_performance.Comparison(
        budget=1000.0,
        since=datetime.date(2026, 8, 1),
        strategies=[
            agent_performance.Strategy("Agent", agent_equity, 0, 0, trades),
            *[agent_performance.Strategy(n, e, 0, 0, 1) for n, e in others],
        ],
    )


def test_a_short_record_refuses_to_draw_a_conclusion():
    """Three trades of hindsight is not evidence, and a confident verdict on it
    would be worse than none."""
    verdict = _comparison(1200.0, [("Mechanical", 900.0)], trades=3).verdict
    assert "too few to judge" in verdict


def test_beating_everything_is_said_plainly():
    assert "ahead of both" in _comparison(1200.0, [("A", 1100.0), ("B", 1050.0)]).verdict


def test_losing_to_everything_is_said_plainly():
    """The whole point of the comparison is being able to reach this answer."""
    verdict = _comparison(900.0, [("A", 1100.0), ("B", 1050.0)]).verdict
    assert "behind both baselines" in verdict
    assert "costing you money" in verdict


def test_a_mixed_result_names_what_was_beaten():
    verdict = _comparison(1075.0, [("A", 1100.0), ("B", 1050.0)]).verdict
    assert "beats B" in verdict
