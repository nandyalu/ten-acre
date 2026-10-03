"""An analysis reads the agent's own book.

Without it the Trader, the Portfolio Manager and the risk analysts are told the
holdings are unknown, and cannot tell adding to a position from opening one.
"""
from backend.services import agent_book, analysis
from backend.services.agent_book import Book, Holding


def test_each_holding_and_the_free_cash_reach_the_portfolio(monkeypatch):
    book = Book(budget=10_000.0, cash=4_321.987, realized_pnl=0.0,
                holdings=[Holding(ticker="NVDA", quantity=12.0, avg_cost=230.5)])
    monkeypatch.setattr(agent_book, "build_book", lambda: book)

    portfolio = analysis.portfolio_context()

    assert portfolio.cash == 4_321.99
    block = portfolio.render("NVDA")
    assert "Current position in NVDA: 12 units, average price 230.50" in block
    assert "Cash available: 4,321.99 USD" in block


def test_a_flat_book_is_flat_not_unknown(monkeypatch):
    book = Book(budget=10_000.0, cash=10_000.0, realized_pnl=0.0)
    monkeypatch.setattr(agent_book, "build_book", lambda: book)

    assert "No current position in AMD" in analysis.portfolio_context().render("AMD")
