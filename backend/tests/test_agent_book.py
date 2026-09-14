"""The agent's budget accounting.

The simulated account holds $1,000,000 and the agent is given $1,000, so every
one of these tests is really the same test: the budget is the app's number, and
nothing the broker says can raise it.

Pure — trades are constructed in memory and db.get_agent_trades is replaced.
"""
import datetime

import pytest

from backend.database.models import AgentTrade
from backend.services import agent_book


def _trade(ticker="AAPL", side="buy", quantity=2, price=100.0, status="filled", order_id=None):
    return AgentTrade(
        ticker=ticker,
        side=side,
        quantity=quantity,
        price=price,
        status=status,
        client_order_id=order_id or f"{ticker}{side}{quantity}{price}{status}",
        placed_at=datetime.datetime(2026, 8, 11, 14, 30),
        filled_at=datetime.datetime(2026, 8, 11, 14, 30) if status == "filled" else None,
    )


@pytest.fixture
def book_of(monkeypatch):
    def build(trades, budget=1000.0, prices=None):
        monkeypatch.setattr(agent_book.db, "get_agent_trades", lambda: trades)
        monkeypatch.setattr(agent_book, "get_budget", lambda: budget)
        lookup = (lambda t: (prices or {}).get(t)) if prices is not None else None
        return agent_book.build_book(price_lookup=lookup)

    return build


def test_an_empty_book_is_all_cash(book_of):
    book = book_of([])
    assert book.cash == 1000.0
    assert book.equity == 1000.0
    assert book.holdings == []


def test_a_buy_moves_cash_into_a_holding(book_of):
    book = book_of([_trade(quantity=2, price=100.0)], prices={"AAPL": 110.0})

    assert book.cash == 800.0
    assert len(book.holdings) == 1
    holding = book.holdings[0]
    assert holding.quantity == 2
    assert holding.avg_cost == 100.0
    assert holding.market_value == 220.0
    assert holding.unrealized_pnl == 20.0
    assert book.equity == 1020.0
    assert book.return_pct == pytest.approx(2.0)


def test_a_sell_returns_cash_and_realizes_pnl(book_of):
    book = book_of(
        [
            _trade(quantity=2, price=100.0, order_id="b1"),
            _trade(side="sell", quantity=2, price=120.0, order_id="s1"),
        ]
    )

    assert book.cash == pytest.approx(1040.0)
    assert book.realized_pnl == pytest.approx(40.0)
    assert book.holdings == []


def test_a_pending_order_does_not_move_cash(book_of):
    """A market order placed after the close fills at the next open. Counting
    it early would let the same dollar be spent twice in one evening."""
    book = book_of([_trade(quantity=5, price=None, status="pending")])

    assert book.cash == 1000.0
    assert book.holdings == []


def test_an_unpriced_holding_counts_at_cost_not_zero(book_of):
    """Treating it as worthless would understate equity and could talk the
    agent into selling what it can still see."""
    book = book_of([_trade(quantity=2, price=100.0)], prices={})

    assert book.holdings[0].market_value is None
    assert book.market_value == 200.0
    assert book.equity == 1000.0


def test_the_budget_is_not_the_brokers_balance(book_of):
    """The account has $1,000,000. The agent has what the setting says."""
    book = book_of([], budget=1000.0)
    assert book.cash == 1000.0


# --- validation ---------------------------------------------------------------


def _order(ticker="AAPL", side="buy", quantity=1):
    return {"ticker": ticker, "side": side, "quantity": quantity}


def test_a_buy_beyond_cash_is_refused(book_of):
    book = book_of([])
    rejection = agent_book.validate(_order(quantity=10), book, price=200.0)

    assert rejection is not None
    assert "only $1,000.00 is uninvested" in rejection.why


def test_a_buy_within_cash_is_allowed(book_of):
    book = book_of([])
    assert agent_book.validate(_order(quantity=4), book, price=200.0) is None


def test_selling_more_than_held_is_refused(book_of):
    book = book_of([_trade(quantity=2, price=100.0)])
    rejection = agent_book.validate(_order(side="sell", quantity=5), book, price=100.0)

    assert rejection is not None
    assert "no shorting" in rejection.why


def test_selling_what_is_held_is_allowed(book_of):
    book = book_of([_trade(quantity=2, price=100.0)])
    assert agent_book.validate(_order(side="sell", quantity=2), book, price=100.0) is None


def test_a_buy_with_no_price_is_refused(book_of):
    """Without a price the cost can't be checked, and an unchecked buy is how
    the budget gets exceeded."""
    book = book_of([])
    assert agent_book.validate(_order(), book, price=None) is not None


@pytest.mark.parametrize(
    "order",
    [
        {"ticker": "", "side": "buy", "quantity": 1},
        {"ticker": "AAPL", "side": "hold", "quantity": 1},
        {"ticker": "AAPL", "side": "buy", "quantity": 0},
        {"ticker": "AAPL", "side": "buy", "quantity": -2},
        {"ticker": "AAPL", "side": "buy", "quantity": 1.5},
    ],
)
def test_malformed_orders_are_refused(book_of, order):
    assert agent_book.validate(order, book_of([]), price=10.0) is not None


# --- closed round trips (what the agent learns from) ---------------------------


def test_a_buy_then_sell_becomes_one_closed_trade(monkeypatch):
    trades = [
        _trade(quantity=2, price=100.0, order_id="b1"),
        _trade(side="sell", quantity=2, price=120.0, order_id="s1"),
    ]
    closed = agent_book.closed_trades(trades)

    assert len(closed) == 1
    assert closed[0].entry == 100.0
    assert closed[0].exit == 120.0
    assert closed[0].pnl == pytest.approx(40.0)
    assert closed[0].return_pct == pytest.approx(20.0)
    assert closed[0].won


def test_an_open_position_is_not_a_closed_trade():
    assert agent_book.closed_trades([_trade(quantity=2, price=100.0)]) == []


def test_lots_are_matched_fifo_so_realized_pnl_agrees_with_the_book():
    """If this diverged from compute_position, the history shown to the model
    would contradict the realized figure shown beside it."""
    trades = [
        _trade(quantity=2, price=100.0, order_id="b1"),
        _trade(quantity=2, price=200.0, order_id="b2"),
        _trade(side="sell", quantity=3, price=150.0, order_id="s1"),
    ]
    closed = agent_book.closed_trades(trades)

    # FIFO: 2 from the $100 lot, 1 from the $200 lot.
    assert [(t.quantity, t.entry) for t in closed] == [(2.0, 100.0), (1.0, 200.0)]
    assert sum(t.pnl for t in closed) == pytest.approx(100.0 - 50.0)


def test_the_signal_decision_is_carried_from_the_buy_not_the_sell():
    """"You bought this on a Hold" is the pattern worth naming, so the decision
    has to come from the order that opened the position."""
    buy = _trade(quantity=1, price=100.0, order_id="b1")
    buy.signal_id = 7
    sell = _trade(side="sell", quantity=1, price=90.0, order_id="s1")
    sell.signal_id = 9

    closed = agent_book.closed_trades([buy, sell], decisions={7: "Hold", 9: "Sell"})

    assert closed[0].signal_decision == "Hold"


def test_pending_orders_are_not_counted_as_round_trips():
    trades = [
        _trade(quantity=2, price=100.0, order_id="b1"),
        _trade(side="sell", quantity=2, price=None, status="pending", order_id="s1"),
    ]
    assert agent_book.closed_trades(trades) == []


# --- trade history (open and closed in one walk) -------------------------------


def test_a_still_held_lot_appears_as_open():
    """The point of the table: a position you are still in has no exit and no
    booked P/L, but it has been held for a number of days."""
    rows = agent_book.trade_history([_trade(quantity=3, price=100.0)])

    assert len(rows) == 1
    row = rows[0]
    assert row.is_open
    assert row.exit is None and row.exit_at is None
    assert row.pnl is None and row.return_pct is None
    assert row.entry == 100.0 and row.quantity == 3


def test_days_held_on_an_open_lot_counts_to_today():
    import datetime

    trade = _trade(quantity=1, price=10.0)
    trade.filled_at = datetime.datetime.now() - datetime.timedelta(days=6)

    assert agent_book.trade_history([trade])[0].held_days == 6


def test_a_closed_lot_carries_its_exit_and_pnl():
    rows = agent_book.trade_history([
        _trade(quantity=2, price=100.0, order_id="b1"),
        _trade(side="sell", quantity=2, price=120.0, order_id="s1"),
    ])

    assert len(rows) == 1
    row = rows[0]
    assert not row.is_open
    assert row.exit == 120.0 and row.exit_at is not None
    assert row.pnl == pytest.approx(40.0)
    assert row.return_pct == pytest.approx(20.0)


def test_a_partly_sold_position_shows_both_halves():
    """Selling half leaves half still held — one closed row and one open one,
    from the same lot."""
    rows = agent_book.trade_history([
        _trade(quantity=10, price=100.0, order_id="b1"),
        _trade(side="sell", quantity=4, price=110.0, order_id="s1"),
    ])

    closed = [r for r in rows if not r.is_open]
    still_open = [r for r in rows if r.is_open]
    assert [r.quantity for r in closed] == [4.0]
    assert [r.quantity for r in still_open] == [6.0]
    assert closed[0].pnl == pytest.approx(40.0)


def test_history_is_oldest_first():
    import datetime

    old = _trade(ticker="OLD", quantity=1, price=10.0, order_id="o")
    old.filled_at = datetime.datetime(2026, 8, 1, 10, 0)
    new = _trade(ticker="NEW", quantity=1, price=10.0, order_id="n")
    new.filled_at = datetime.datetime(2026, 8, 9, 10, 0)

    assert [r.ticker for r in agent_book.trade_history([new, old])] == ["OLD", "NEW"]


def test_closed_trades_is_the_history_without_the_open_rows():
    """One FIFO walk feeds both, so the table and the agent's own record cannot
    disagree about the same shares."""
    trades = [
        _trade(quantity=2, price=100.0, order_id="b1"),
        _trade(side="sell", quantity=2, price=120.0, order_id="s1"),
        _trade(ticker="OPEN", quantity=1, price=50.0, order_id="b2"),
    ]

    assert len(agent_book.trade_history(trades)) == 2
    assert [r.ticker for r in agent_book.closed_trades(trades)] == ["AAPL"]


def test_trades_are_attributable_to_the_signal_that_prompted_them(monkeypatch):
    """The signal's grade says whether the analysis was right over its horizon;
    the trade says what the exit rules made of it. Seeing both is what makes a
    PASS beside a losing trade legible as "the call was right, the stop was too
    tight"."""
    buy = _trade(ticker="ZBH", quantity=10, price=97.83, order_id="b1")
    buy.signal_id = 28
    other = _trade(ticker="GNW", quantity=2, price=9.84, order_id="b2")
    other.signal_id = 26
    monkeypatch.setattr(agent_book.db, "get_agent_trades", lambda: [buy, other])

    rows = agent_book.trades_for_signal(28)

    assert [r.ticker for r in rows] == ["ZBH"]
    assert rows[0].is_open


def test_the_exit_is_matched_by_fifo_not_by_signal_id(monkeypatch):
    """A sell carries no signal_id — it is the stop or take-profit firing — so
    the lot it closes is found by the FIFO match."""
    buy = _trade(ticker="ZBH", quantity=10, price=97.83, order_id="b1")
    buy.signal_id = 28
    stop = _trade(ticker="ZBH", side="sell", quantity=10, price=95.30, order_id="s1")
    stop.signal_id = None
    monkeypatch.setattr(agent_book.db, "get_agent_trades", lambda: [buy, stop])

    rows = agent_book.trades_for_signal(28)

    assert len(rows) == 1
    assert rows[0].exit == 95.30
    assert rows[0].pnl == pytest.approx(-25.30)


def test_a_signal_nothing_was_traded_on_has_no_rows(monkeypatch):
    monkeypatch.setattr(agent_book.db, "get_agent_trades", lambda: [])
    assert agent_book.trades_for_signal(99) == []


# --- the equity curve ----------------------------------------------------------


class _Trade:
    """A filled agent order, in the shape equity_curve reads."""

    def __init__(self, ticker, side, quantity, price, day, status="filled"):
        self.ticker, self.side, self.quantity, self.price = ticker, side, quantity, price
        self.filled_at = datetime.datetime.fromisoformat(f"{day}T15:00:00")
        self.placed_at = self.filled_at
        self.status = status


def _bars(monkeypatch, closes: dict[str, dict[str, float]], sessions: list[str]):
    """Stub the bar cache: SPY supplies the calendar, the rest supply closes."""

    class Bar:
        def __init__(self, date, close):
            self.date, self.close = date, close

    def get_bars(ticker, start, include_today=False, today=None):
        # include_today sends a live vendor request for each ticker. The curve
        # is a page, so it must never ask for one.
        assert not include_today, "the curve asked the vendor for today's bar"
        if ticker == "SPY":
            return [Bar(d, 100.0) for d in sessions]
        return [Bar(d, c) for d, c in sorted(closes.get(ticker, {}).items())]

    from backend.services import bars

    monkeypatch.setattr(bars, "get_bars", get_bars)
    monkeypatch.setattr(agent_book, "_session_has_opened", lambda today: False)


def test_the_curve_covers_every_session_not_only_the_trading_days(monkeypatch):
    """A daily snapshot table would only ever draw the days since it was
    switched on. Rebuilding from the ledger draws the whole history."""
    _bars(
        monkeypatch,
        {"ZBH": {"2026-08-13": 100.0, "2026-08-14": 110.0, "2026-08-17": 90.0}},
        ["2026-08-13", "2026-08-14", "2026-08-17"],
    )
    monkeypatch.setattr(agent_book, "get_budget", lambda: 1000.0)

    curve = agent_book.equity_curve([_Trade("ZBH", "buy", 3, 100.0, "2026-08-13")])

    assert [p.date.isoformat() for p in curve] == ["2026-08-13", "2026-08-14", "2026-08-17"]
    # 700 cash + 3 shares marked at each day's close.
    assert [round(p.equity, 2) for p in curve] == [1000.0, 1030.0, 970.0]


def test_a_sale_moves_the_value_into_cash(monkeypatch):
    _bars(
        monkeypatch,
        {"ZBH": {"2026-08-13": 100.0, "2026-08-14": 110.0}},
        ["2026-08-13", "2026-08-14"],
    )
    monkeypatch.setattr(agent_book, "get_budget", lambda: 1000.0)

    curve = agent_book.equity_curve([
        _Trade("ZBH", "buy", 3, 100.0, "2026-08-13"),
        _Trade("ZBH", "sell", 3, 110.0, "2026-08-14"),
    ])

    assert curve[-1].market_value == 0.0
    assert round(curve[-1].cash, 2) == 1030.0
    assert round(curve[-1].equity, 2) == 1030.0


def test_a_holding_with_no_bar_that_day_keeps_its_last_close(monkeypatch):
    """A missing quote is not a position worth zero — that would draw a cliff
    on the chart and recover from it the next day."""
    _bars(
        monkeypatch,
        {"ZBH": {"2026-08-13": 100.0, "2026-08-17": 90.0}},  # nothing on the 14th
        ["2026-08-13", "2026-08-14", "2026-08-17"],
    )
    monkeypatch.setattr(agent_book, "get_budget", lambda: 1000.0)

    curve = agent_book.equity_curve([_Trade("ZBH", "buy", 3, 100.0, "2026-08-13")])

    assert round(curve[1].equity, 2) == 1000.0


def test_an_unfilled_order_is_not_on_the_curve(monkeypatch):
    """A pending buy has moved no money, so it cannot have moved equity."""
    _bars(monkeypatch, {}, ["2026-08-13"])
    monkeypatch.setattr(agent_book, "get_budget", lambda: 1000.0)

    assert agent_book.equity_curve([_Trade("ZBH", "buy", 3, 100.0, "2026-08-13", "pending")]) == []


def test_no_fills_means_no_curve(monkeypatch):
    _bars(monkeypatch, {}, ["2026-08-13"])
    assert agent_book.equity_curve([]) == []


def test_todays_point_uses_the_price_cache(monkeypatch):
    """A live bar for today costs three seconds a ticker at the vendor's pace.
    On 2026-09-14 it made /api/agent/curve take 12 seconds."""
    _bars(monkeypatch, {"ZBH": {"2026-08-13": 100.0}}, ["2026-08-13"])
    monkeypatch.setattr(agent_book, "_session_has_opened", lambda today: True)
    monkeypatch.setattr(agent_book, "get_shown_price", lambda ticker: 120.0)
    monkeypatch.setattr(agent_book, "get_budget", lambda: 1000.0)

    curve = agent_book.equity_curve(
        [_Trade("ZBH", "buy", 3, 100.0, "2026-08-13")], today=datetime.date(2026, 8, 14)
    )

    assert [p.date.isoformat() for p in curve] == ["2026-08-13", "2026-08-14"]
    # 700 cash + 3 shares at the cached 120.
    assert round(curve[-1].equity, 2) == 1060.0


def test_a_holding_with_no_cached_price_today_keeps_its_last_close(monkeypatch):
    _bars(monkeypatch, {"ZBH": {"2026-08-13": 100.0}}, ["2026-08-13"])
    monkeypatch.setattr(agent_book, "_session_has_opened", lambda today: True)
    monkeypatch.setattr(agent_book, "get_shown_price", lambda ticker: None)
    monkeypatch.setattr(agent_book, "get_budget", lambda: 1000.0)

    curve = agent_book.equity_curve(
        [_Trade("ZBH", "buy", 3, 100.0, "2026-08-13")], today=datetime.date(2026, 8, 14)
    )

    assert round(curve[-1].equity, 2) == 1000.0


def test_a_stored_bar_for_today_is_not_drawn_twice(monkeypatch):
    """After the close, the bar cache stores today's bar, and it wins over the
    price cache."""
    _bars(
        monkeypatch,
        {"ZBH": {"2026-08-13": 100.0, "2026-08-14": 110.0}},
        ["2026-08-13", "2026-08-14"],
    )
    monkeypatch.setattr(agent_book, "_session_has_opened", lambda today: True)
    monkeypatch.setattr(agent_book, "get_shown_price", lambda ticker: 120.0)
    monkeypatch.setattr(agent_book, "get_budget", lambda: 1000.0)

    curve = agent_book.equity_curve(
        [_Trade("ZBH", "buy", 3, 100.0, "2026-08-13")], today=datetime.date(2026, 8, 14)
    )

    assert [p.date.isoformat() for p in curve] == ["2026-08-13", "2026-08-14"]
    assert round(curve[-1].equity, 2) == 1030.0


@pytest.mark.parametrize(
    "now, expected",
    [
        ("2026-09-14T09:29", False),  # Monday, a minute before the open
        ("2026-09-14T09:30", True),  # Monday, at the open
        ("2026-09-07T12:00", False),  # Labor Day
        ("2026-09-12T12:00", False),  # Saturday
    ],
)
def test_todays_session_starts_at_the_open(now, expected):
    from zoneinfo import ZoneInfo

    moment = datetime.datetime.fromisoformat(now).replace(tzinfo=ZoneInfo("America/New_York"))
    assert agent_book._session_has_opened(moment.date(), now=moment) is expected
