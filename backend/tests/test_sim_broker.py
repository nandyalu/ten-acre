"""The in-process simulated broker: fills, exits, expiry, and the fifth guard."""
import ast
import datetime
import pathlib

import pytest
from sqlalchemy import StaticPool
from sqlmodel import SQLModel, create_engine

from backend.services import broker, sim_broker

# Monday 2026-10-05, 10:00 ET.
OPEN = datetime.datetime(2026, 10, 5, 14, 0)


@pytest.fixture
def sim(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    clock = {"now": OPEN}
    quote = {"price": 100.0}
    bars: list[dict] = []
    monkeypatch.setattr(sim_broker, "_engine", lambda: engine)
    monkeypatch.setattr(sim_broker, "_now", lambda: clock["now"])
    monkeypatch.setattr(sim_broker, "_quote", lambda ticker: quote["price"])
    monkeypatch.setattr(
        "backend.services.intraday.fetch_bars",
        lambda ticker, end_time=None, **_: [b for b in bars if end_time is None or b["timestamp"] <= end_time],
    )
    monkeypatch.setenv("SIM_SLIPPAGE_BPS", "10")
    return clock, quote, bars


def bar(minute: int, open_, high, low, close=None):
    return {
        "timestamp": OPEN + datetime.timedelta(minutes=minute),
        "open": open_, "high": high, "low": low, "close": close if close is not None else open_,
    }


def test_a_market_buy_fills_at_the_quote_plus_slippage(sim):
    placed = sim_broker.place_market_order("abc", "BUY", 10)
    detail = sim_broker.get_order_detail(placed["client_order_id"])
    assert detail["status"] == "FILLED"
    assert detail["filled_price"] == pytest.approx(100.10)
    assert detail["filled_quantity"] == 10
    assert sim_broker.get_positions() == {"ABC": 10}


def test_a_sell_larger_than_the_holding_is_refused(sim):
    sim_broker.place_market_order("ABC", "BUY", 5)
    with pytest.raises(ValueError, match="holds 5"):
        sim_broker.place_market_order("ABC", "SELL", 6)


def test_a_closed_market_refuses_a_market_order_and_a_bracket_but_not_a_limit(sim):
    clock, _, _ = sim
    clock["now"] = datetime.datetime(2026, 10, 3, 14, 0)  # a Saturday
    with pytest.raises(RuntimeError, match="market is closed"):
        sim_broker.place_market_order("ABC", "BUY", 1)
    with pytest.raises(RuntimeError, match="market is closed"):
        sim_broker.place_bracket_order("ABC", 1, 100.0, stop_price=95.0)
    placed = sim_broker.place_limit_order("ABC", "BUY", 1, 90.0, "GTC")
    assert sim_broker.get_order_detail(placed["client_order_id"])["status"] == "SUBMITTED"


def test_a_bracket_fills_and_a_gap_through_the_stop_fills_at_the_open_and_cancels_the_target(sim):
    clock, _, bars = sim
    placed = sim_broker.place_bracket_order("ABC", 10, 100.0, stop_price=95.0, target_price=110.0)
    stop, target = placed["exits"]
    assert sim_broker.get_order_detail(placed["client_order_id"])["status"] == "FILLED"
    bars += [bar(1, 99, 100, 98), bar(2, 93, 94, 92)]  # gaps through the stop
    clock["now"] = OPEN + datetime.timedelta(minutes=5)
    stopped = sim_broker.get_order_detail(stop["client_order_id"])
    assert stopped["status"] == "FILLED"
    assert stopped["filled_price"] == pytest.approx(93 * 0.999)
    assert sim_broker.get_order_detail(target["client_order_id"])["status"] == "CANCELLED"
    assert sim_broker.get_positions() == {}


def test_a_target_fills_at_its_level_or_the_better_open(sim):
    clock, _, bars = sim
    placed = sim_broker.place_bracket_order("ABC", 10, 100.0, stop_price=95.0, target_price=110.0)
    _, target = placed["exits"]
    bars += [bar(1, 105, 110.5, 104)]
    clock["now"] = OPEN + datetime.timedelta(minutes=3)
    assert sim_broker.get_order_detail(target["client_order_id"])["filled_price"] == 110.0


def test_no_bar_from_before_the_order_can_fill_it(sim):
    clock, _, bars = sim
    bars += [bar(-5, 80, 80, 80)]  # far below the limit, but before the order
    placed = sim_broker.place_limit_order("ABC", "BUY", 1, 90.0, "GTC")
    clock["now"] = OPEN + datetime.timedelta(minutes=2)
    assert sim_broker.get_order_detail(placed["client_order_id"])["status"] == "SUBMITTED"


def test_a_resting_stop_can_be_moved_and_cancelled(sim):
    sim_broker.place_market_order("ABC", "BUY", 10)
    legs = sim_broker.place_exit_bracket("ABC", 10, stop_price=95.0, target_price=110.0)
    stop = next(leg for leg in legs if leg["kind"] == "stop")
    assert sim_broker.replace_exit(stop["client_order_id"], "stop", 97.0)
    assert sim_broker.cancel_order(stop["client_order_id"])
    assert sim_broker.get_order_detail(stop["client_order_id"])["status"] == "CANCELLED"
    with pytest.raises(RuntimeError, match="CANCELLED"):
        sim_broker.replace_exit(stop["client_order_id"], "stop", 98.0)


def test_a_day_limit_expires_only_once_the_bars_to_the_close_are_read(sim):
    clock, _, bars = sim
    placed = sim_broker.place_limit_order("ABC", "BUY", 1, 90.0, "DAY")
    cid = placed["client_order_id"]
    # 16:05 ET, but the newest bar is 15:44: the last minutes are not read yet.
    bars += [bar(344, 99, 99, 99)]
    clock["now"] = datetime.datetime(2026, 10, 5, 20, 5)
    assert sim_broker.get_order_detail(cid)["status"] == "SUBMITTED"
    bars += [bar(359, 99, 99, 99)]  # 15:59 ET
    assert sim_broker.get_order_detail(cid)["status"] == "EXPIRED"


def test_the_facade_routes_to_the_simulator(monkeypatch):
    monkeypatch.setenv("BROKER", "sim")
    assert broker.name() == "sim"
    assert broker.is_paper()
    assert broker.get_paper_account_id() == "SIM"
    assert broker.get_balance()["account_currency_assets"][0]["unsettled_cash"] == 0.0


def test_the_simulator_cannot_reach_a_broker():
    """The fifth guard. No order leaves the process: the module imports no
    network library, no broker SDK and neither real broker module."""
    source = pathlib.Path(sim_broker.__file__).read_text()
    forbidden = ("requests", "httpx", "urllib", "socket", "http", "webull", "alpaca", "sandbox_broker", "alpaca_broker")
    for node in ast.walk(ast.parse(source)):
        names = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or "", *[alias.name for alias in node.names]]
        for name in names:
            assert not any(part in forbidden for part in name.split(".")), name
    assert "://" not in source
