"""Splits and spin-offs are applied once, to every number about the stock."""
import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from backend.database.models import (
    AgentTrade, CorporateAction, DailyBar, IntradayBar, Signal, SimOrder, TickerPrice, WatchlistTicker,
)
from backend.services import corporate_actions as ca

EX = datetime.date(2026, 10, 5)
BEFORE = datetime.datetime(2026, 10, 1, 15, 0)
AFTER = datetime.datetime(2026, 10, 5, 15, 0)


@pytest.fixture
def engine(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(ca, "_engine", lambda: engine)
    alerts = []
    monkeypatch.setattr("backend.database.db.record_alert", lambda *a: alerts.append(a))
    engine.alerts = alerts
    return engine


def _trade(ticker, side, qty, price, at, cid, status="filled", **kw):
    return AgentTrade(ticker=ticker, side=side, quantity=qty, price=price, placed_at=at,
                      filled_at=at if status == "filled" else None, client_order_id=cid, status=status, **kw)


def _book(engine, ticker="ABC"):
    with Session(engine) as s:
        s.add(_trade(ticker, "buy", 10, 1000.0, BEFORE, "b1"))
        s.add(_trade(ticker, "sell", 10, None, BEFORE, "stop1", status="pending", is_stop=True, limit_price=900.0))
        s.add(SimOrder(client_order_id="sb1", ticker=ticker, side="BUY", order_type="market", quantity=10,
                       status="FILLED", placed_at=BEFORE, checked_through=BEFORE, filled_price=1000.0, filled_quantity=10))
        s.add(SimOrder(client_order_id="ss1", ticker=ticker, side="SELL", order_type="stop", quantity=10,
                       stop_price=900.0, time_in_force="GTC", status="SUBMITTED", placed_at=BEFORE, checked_through=BEFORE))
        s.add(Signal(ticker=ticker, signal_date=BEFORE.date(), decision="Buy", rationale="r", price_at_signal=1000.0,
                     entry_price=1000.0, stop_loss=900.0, price_target=1200.0, evaluation_date=EX + datetime.timedelta(days=10)))
        s.add(DailyBar(ticker=ticker, date=BEFORE.date(), open=1000, high=1010, low=990, close=1000, volume=5))
        s.add(IntradayBar(ticker=ticker, timestamp=BEFORE, open=1000, high=1000, low=1000, close=1000, volume=5))
        s.add(TickerPrice(ticker=ticker, price=1000.0, fetched_at=BEFORE))
        s.commit()


def test_alpaca_rows_become_actions():
    found = ca._from_alpaca([
        {"type": "forward_splits", "id": "a", "symbol": "NVDA", "ex_date": "2024-06-10", "new_rate": 10, "old_rate": 1},
        {"type": "reverse_splits", "id": "b", "symbol": "XYZ", "ex_date": "2026-01-02", "new_rate": 1, "old_rate": 10},
        {"type": "spin_offs", "id": "c", "source_symbol": "CTVA", "new_symbol": "VYLR", "ex_date": "2026-10-01",
         "new_rate": 1, "source_rate": 1},
    ])
    assert [(a.ticker, a.kind, a.ratio, a.child) for a in found] == [
        ("NVDA", "split", 10.0, None), ("XYZ", "split", 0.1, None), ("CTVA", "spin_off", 1.0, "VYLR"),
    ]


def test_a_split_moves_every_number_from_before_the_ex_date_once(engine):
    _book(engine)
    with Session(engine) as s:
        s.add(CorporateAction(id="split1", ticker="ABC", kind="split", ex_date=EX, ratio=10.0))
        s.commit()
    told = ca.apply("split1", EX)
    assert "10-for-1" in told and "100 shares" in told
    with Session(engine) as s:
        buy = s.exec(select(AgentTrade).where(AgentTrade.client_order_id == "b1")).one()
        stop = s.exec(select(AgentTrade).where(AgentTrade.client_order_id == "stop1")).one()
        assert (buy.quantity, buy.price) == (100, 100.0)
        assert (stop.quantity, stop.limit_price) == (100, 90.0)
        sim_stop = s.exec(select(SimOrder).where(SimOrder.client_order_id == "ss1")).one()
        assert (sim_stop.quantity, sim_stop.stop_price) == (100, 90.0)
        assert sim_stop.checked_through >= ca._ex_open(EX) - datetime.timedelta(minutes=1)
        sig = s.exec(select(Signal)).one()
        assert (sig.entry_price, sig.stop_loss, sig.price_target, sig.price_at_signal) == (100.0, 90.0, 120.0, 100.0)
        assert s.exec(select(DailyBar)).all() == []  # dropped, so it refetches
        assert s.exec(select(IntradayBar)).one().close == 100.0
        assert s.get(TickerPrice, "ABC").price == 100.0
    assert engine.alerts and engine.alerts[0][1] == "corporate_action"
    assert ca.apply("split1", EX) is None  # never twice


def test_a_raw_row_from_before_an_applied_split_arrives_adjusted(engine):
    with Session(engine) as s:
        s.add(CorporateAction(id="r", ticker="ABC", kind="split", ex_date=EX, ratio=0.1, price_factor=10.0,
                              applied_at=AFTER))
        s.commit()
    rows = ca.adjust_raw("ABC", [
        {"date": BEFORE.date(), "open": 1, "high": 1, "low": 1, "close": 1, "volume": 100},
        {"date": EX, "open": 10, "high": 10, "low": 10, "close": 10, "volume": 10},
    ])
    assert rows[0]["close"] == 10 and rows[0]["volume"] == 10
    assert rows[1]["close"] == 10


def test_a_spin_off_moves_part_of_the_cost_of_what_is_still_held(engine, monkeypatch):
    with Session(engine) as s:
        s.add(_trade("PAR", "buy", 10, 100.0, BEFORE, "b1"))
        s.add(_trade("PAR", "sell", 4, 110.0, BEFORE + datetime.timedelta(hours=1), "s1"))
        s.add(CorporateAction(id="spin1", ticker="PAR", kind="spin_off", ex_date=EX, ratio=0.5, child="KID"))
        s.commit()
    closes = {"PAR": 75.0, "KID": 50.0}  # parent's share: 75 / (75 + 0.5 x 50) = 0.75
    monkeypatch.setattr("backend.services.bars.get_bars",
                        lambda t, a, b: [SimpleNamespace(date=EX.isoformat(), close=closes[t])])
    told = ca.apply("spin1", EX)
    assert "You now hold 3 KID" in told
    with Session(engine) as s:
        rows = {t.client_order_id: t for t in s.exec(select(AgentTrade)).all()}
        assert (rows["b1"].quantity, rows["b1"].price) == (4, 100.0)  # the sold part, as it was
        kept = next(t for cid, t in rows.items() if cid.startswith("b1-spin"))
        assert (kept.quantity, kept.price) == (6, 75.0)
        assert (rows["spin-spin1"].ticker, rows["spin-spin1"].quantity, rows["spin-spin1"].price) == ("KID", 3, 50.0)
        # Cash is conserved: the buys cost what they cost before.
        assert 4 * 100 + 6 * 75 + 3 * 50 == 10 * 100
        assert s.get(WatchlistTicker, "KID") is not None


def test_a_spin_off_pauses_the_parents_exits_until_its_close_is_known(engine, monkeypatch):
    with Session(engine) as s:
        s.add(_trade("PAR", "buy", 10, 100.0, BEFORE, "b1"))
        s.add(SimOrder(client_order_id="stop", ticker="PAR", side="SELL", order_type="stop", quantity=10,
                       stop_price=90.0, time_in_force="GTC", status="SUBMITTED", placed_at=BEFORE, checked_through=BEFORE))
        s.add(CorporateAction(id="spin2", ticker="PAR", kind="spin_off", ex_date=EX, ratio=1.0, child="KID"))
        s.commit()
    monkeypatch.setattr("backend.services.bars.get_bars", lambda t, a, b: [])
    assert "paused" in ca.apply("spin2", EX)
    with Session(engine) as s:
        assert s.exec(select(SimOrder)).one().status == "HELD"
    closes = {"PAR": 60.0, "KID": 20.0}  # 60 / 80 = 0.75
    monkeypatch.setattr("backend.services.bars.get_bars",
                        lambda t, a, b: [SimpleNamespace(date=EX.isoformat(), close=closes[t])])
    ca.apply("spin2", EX)
    with Session(engine) as s:
        stop = s.exec(select(SimOrder).where(SimOrder.client_order_id == "stop")).one()
        assert (stop.status, stop.stop_price) == ("SUBMITTED", pytest.approx(67.5))
