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


def _signal(ticker="AAA", decision="Buy", price=100.0, day=1, graded_at=None):
    """``graded_at`` is the price the grader recorded on the evaluation date.
    Leaving it None is a signal still maturing, which is the common case."""
    return Signal(
        ticker=ticker,
        signal_date=datetime.date(2026, 8, day),
        decision=decision,
        rationale="",
        price_at_signal=price,
        evaluation_date=datetime.date(2026, 8, day + 14),
        price_at_evaluation=graded_at,
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


def test_the_mechanical_rule_sells_at_maturity_without_a_sell_signal(world):
    """The agent decides what gets re-analysed, so a name it stops revisiting
    never produces a Sell. Without this exit the rule would hold that position
    forever, and its exits would measure the agent's research habits rather
    than the signals."""
    world(
        signals=[_signal("AAA", "Buy", 100.0, day=1, graded_at=130.0)],
        prices={"AAA": 500.0},  # Never reached: the position is closed by 8/15.
    )

    rule = agent_performance._mechanical_strategy(1000.0, datetime.date(2026, 8, 1))

    assert rule.trades == 2
    assert rule.invested == 0.0
    # Bought 2 at 100 on 8/1, sold 2 at the graded 130 on 8/15.
    assert rule.equity == pytest.approx(1060.0)


def test_a_sell_signal_beats_the_maturity_date_to_it(world):
    """Whichever comes first. The Sell lands on 8/3, eleven days before the
    buy's own evaluation date, so the position is already gone when it
    matures — and the maturity must not sell shares twice."""
    world(
        signals=[
            _signal("AAA", "Buy", 100.0, day=1, graded_at=130.0),
            _signal("AAA", "Sell", 120.0, day=3),
        ],
        prices={"AAA": 500.0},
    )

    rule = agent_performance._mechanical_strategy(1000.0, datetime.date(2026, 8, 1))

    assert rule.trades == 2
    assert rule.equity == pytest.approx(1040.0)


def test_a_signal_still_maturing_stays_open(world):
    """No graded price yet means no exit date has arrived. The position is
    marked at today's price, exactly as the agent's own book is."""
    world(signals=[_signal("AAA", "Buy", 100.0, day=1)], prices={"AAA": 150.0})

    rule = agent_performance._mechanical_strategy(1000.0, datetime.date(2026, 8, 1))

    assert rule.trades == 1
    assert rule.invested == pytest.approx(200.0)
    assert rule.equity == pytest.approx(1000.0 - 200.0 + 2 * 150.0)


def test_maturity_frees_its_slot_for_the_same_day(world):
    """A maturity is walked before that date's own signals, so the cash it
    releases is available to a buy made the same day — the same ordering the
    agent gets when it sells to fund a buy."""
    signals = [_signal(f"T{i}", "Buy", 100.0, day=1, graded_at=100.0) for i in range(5)]
    # Lands on the day the five above mature, with the budget fully committed
    # until they do.
    signals.append(_signal("LATE", "Buy", 100.0, day=15))
    world(signals=signals, prices={"LATE": 100.0})

    rule = agent_performance._mechanical_strategy(1000.0, datetime.date(2026, 8, 1))

    assert rule.invested == pytest.approx(200.0)  # LATE was affordable.


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


def test_it_never_holds_more_than_five_names(world):
    """Five slots, and a sixth signal is missed rather than opening a sixth
    position. Nothing else caps the count — before the cap existed, a rule
    whose cash had grown simply opened more positions."""
    world(
        signals=[_signal(f"T{i}", "Buy", 90.0, day=i + 1) for i in range(6)],
        prices={f"T{i}": 90.0 for i in range(6)},
    )

    rule = agent_performance._mechanical_strategy(1000.0, datetime.date(2026, 8, 1))

    assert rule.trades == 5
    assert rule.cash == pytest.approx(0.0)  # Fully deployed, nothing idle.


def test_the_slots_stay_equal_at_any_share_price(world):
    """Fractional shares are what makes "equal weight" literally true. With
    whole shares the $7 name would fill its slot and the $333 name would leave
    most of one in cash, and the five weights would only be roughly equal."""
    world(
        signals=[
            _signal("AAA", "Buy", 90.0, day=1),
            _signal("BBB", "Buy", 333.0, day=2),
            _signal("CCC", "Buy", 7.0, day=3),
        ],
        prices={"AAA": 90.0, "BBB": 333.0, "CCC": 7.0},
    )

    rule = agent_performance._mechanical_strategy(1000.0, datetime.date(2026, 8, 1))

    # $1,000/5, then $800/4, then $600/3 — one slot of $200 every time.
    assert rule.invested == pytest.approx(600.0)
    assert rule.cash == pytest.approx(400.0)


def test_a_slot_grows_with_what_the_rule_is_worth(world):
    """A fifth of the money it has now, not a fifth of what it started with.
    A fixed fraction of the starting budget would either leave the winnings in
    cash forever or, with no cap, open a sixth position with them."""
    world(
        signals=[
            _signal("AAA", "Buy", 100.0, day=1, graded_at=300.0),  # Matures 8/15.
            _signal("BBB", "Buy", 100.0, day=16),
        ],
        prices={"BBB": 100.0},
    )

    rule = agent_performance._mechanical_strategy(1000.0, datetime.date(2026, 8, 1))

    # $200 into AAA became $600 at maturity, so the book is $1,400 and the
    # next slot is $280 — not the $200 it would have been on day one.
    assert rule.invested == pytest.approx(280.0)
    assert rule.equity == pytest.approx(1400.0)


def test_an_unrealized_gain_is_not_redeployed_until_it_is_cash(world):
    """The one simplification: an open position counts as one slot, not as
    what it is now worth. Redeploying a gain the rule cannot yet spend would
    be marking to market to size the next bet."""
    world(
        signals=[
            _signal("AAA", "Buy", 100.0, day=1),  # Still maturing, up 5x.
            _signal("BBB", "Buy", 100.0, day=2),
        ],
        prices={"AAA": 500.0, "BBB": 100.0},
    )

    rule = agent_performance._mechanical_strategy(1000.0, datetime.date(2026, 8, 1))

    # $800 over the four free slots, not a share of AAA's paper gain.
    assert rule.invested == pytest.approx(400.0)


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
