"""Experiment 2's books take their market data from one market container.

Each test runs both roles in one process: the book calls a fetch entry point,
and its request goes through the real route and the real JSON encoding to the
market container's side, which runs the same entry point with a faked vendor.
"""
import datetime
import threading
import time

import pytest
import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes import market
from backend.services import (
    alpaca_broker, bars, candidates, corporate_actions, intraday, market_feed, positions, quotes,
)


@pytest.fixture
def feed(monkeypatch):
    """A book with MARKET_DATA_URL, wired to the market routes in-process.

    Returns the list of (kind, source) counts the book recorded.
    """
    app = FastAPI()
    app.include_router(market.router)
    client = TestClient(app)
    counts = []
    monkeypatch.setattr("backend.database.db.count_market_fetch", lambda kind, source: counts.append((kind, source)))
    monkeypatch.setattr(market_feed, "_memo", {})
    monkeypatch.setenv("MARKET_DATA_URL", "http://market:8080/")

    def get(url, params=None, timeout=None):
        assert url.startswith("http://market:8080/api/market/")
        # While the request is served, this process is the market container.
        with monkeypatch.context() as m:
            m.setenv("MARKET_MODE", "1")
            return client.get(url.removeprefix("http://market:8080"), params=params)

    monkeypatch.setattr(market_feed.requests, "get", get)
    return counts


def test_without_a_url_nothing_is_asked_or_counted(monkeypatch):
    monkeypatch.delenv("MARKET_DATA_URL", raising=False)
    monkeypatch.setattr(market_feed.requests, "get", lambda *a, **k: pytest.fail("asked"))
    monkeypatch.setattr("backend.database.db.count_market_fetch", lambda *a: pytest.fail("counted"))
    assert market_feed.ask("quote", ticker="ABC") is market_feed.MISSING


def test_a_market_container_that_cannot_be_reached_leaves_the_book_to_fetch_its_own(monkeypatch):
    counts = []
    monkeypatch.setenv("MARKET_DATA_URL", "http://market:8080")
    monkeypatch.setattr("backend.database.db.count_market_fetch", lambda kind, source: counts.append((kind, source)))

    def refuse(*a, **k):
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(market_feed.requests, "get", refuse)
    monkeypatch.setattr(positions.db, "set_cached_price", lambda *a, **k: None)
    monkeypatch.setattr(quotes, "get_realtime_price", lambda ticker: 42.0)
    assert positions.get_current_price("ABC") == 42.0
    assert counts == [("quote", "own")]


def test_a_quote_comes_from_the_market_container(feed, monkeypatch):
    cached = []
    monkeypatch.setattr(positions.db, "set_cached_price", lambda t, p, source: cached.append((t, p, source)))
    monkeypatch.setattr(quotes, "get_realtime_price", lambda ticker: 101.5)
    assert positions.get_current_price("ABC") == 101.5
    # The market container caches its own copy as "webull"; the book as "market".
    assert ("ABC", 101.5, "market") in cached
    assert feed == [("quote", "market")]


def test_nothing_from_the_market_container_is_an_answer_not_a_failure(feed, monkeypatch):
    """Else one book could take a Yahoo price that the others did not see."""
    monkeypatch.setattr(positions.db, "set_cached_price", lambda *a, **k: None)
    monkeypatch.setattr(quotes, "get_realtime_price", lambda ticker: None)
    monkeypatch.setattr(positions, "yf_retry", lambda call: (_ for _ in ()).throw(RuntimeError("yahoo down")))
    assert positions.get_current_price("ABC") is None
    assert feed == [("quote", "market")]


def test_daily_bars_keep_their_dates_and_the_book_applies_its_own_splits(feed, monkeypatch):
    day = datetime.date(2026, 10, 1)
    row = {"date": day, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10.0}
    monkeypatch.setattr(bars, "_fetch_from_webull", lambda t, s, today: [row])
    seen = []
    monkeypatch.setattr(corporate_actions, "adjust_raw", lambda t, rows: seen.append(rows) or rows)
    assert bars._fetch_history("ABC", day, day) == [row]
    assert seen == [[row]]  # raw Webull rows are adjusted in the book
    assert feed == [("daily", "market")]


def test_yahoo_bars_from_the_market_container_are_not_adjusted_again(feed, monkeypatch):
    day = datetime.date(2026, 10, 1)
    row = {"date": day, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10.0}
    monkeypatch.setattr(bars, "_fetch_from_webull", lambda t, s, today: None)
    monkeypatch.setattr(bars, "_fetch_from_yfinance", lambda t, s: [row])
    monkeypatch.setattr(corporate_actions, "adjust_raw", lambda t, rows: pytest.fail("adjusted twice"))
    assert bars._fetch_history("ABC", day, day) == [row]


def test_minute_bars_keep_their_timestamps(feed, monkeypatch):
    stamp = datetime.datetime(2026, 10, 1, 14, 31)
    bar = {"timestamp": stamp, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 5.0}
    asked = []
    monkeypatch.setattr(quotes, "_get_market_data", lambda: None)
    monkeypatch.setattr(alpaca_broker, "is_paper", lambda: True)
    monkeypatch.setattr(intraday, "_fetch_from_alpaca", lambda t, count, end: asked.append((count, end)) or [bar])
    end = datetime.datetime(2026, 10, 1, 15, 0)
    assert intraday.fetch_bars("ABC", count=30, end_time=end) == [bar]
    assert asked == [(30, end.replace(tzinfo=datetime.timezone.utc))]
    assert feed == [("minutes", "market")]


def test_every_book_gets_one_screen(feed, monkeypatch):
    calls = []
    monkeypatch.setattr(quotes, "get_api_client", lambda: None)
    monkeypatch.setattr(alpaca_broker, "is_paper", lambda: True)
    monkeypatch.setattr(alpaca_broker, "most_active", lambda n: calls.append(n) or ["ABC"])
    monkeypatch.setattr(alpaca_broker, "day_gainers", lambda n: [])
    monkeypatch.setattr(alpaca_broker, "get_snapshots", lambda tickers: [
        {"symbol": t, "price": 20.0, "volume": 5_000_000, "change_ratio": 0.01} for t in tickers
    ])
    monkeypatch.setattr(candidates, "_congress_tickers", set)
    monkeypatch.setattr(candidates, "_trending_tickers", set)
    first = candidates.screen()
    second = candidates.screen()
    assert [c.ticker for c in first] == ["ABC"] and first == second
    assert len(calls) == 1  # the second book got the kept answer
    assert feed == [("screen", "market"), ("screen", "market")]


def test_corporate_actions_come_back_as_rows(feed, monkeypatch):
    monkeypatch.setattr(alpaca_broker, "is_paper", lambda: True)
    monkeypatch.setattr(alpaca_broker, "corporate_actions", lambda tickers, start, end: [{
        "type": "forward_splits", "id": "x1", "symbol": "ABC", "ex_date": "2026-10-05",
        "new_rate": 10, "old_rate": 1,
    }])
    start, end = datetime.date(2026, 1, 1), datetime.date(2026, 10, 2)
    (action,) = corporate_actions.fetch(["abc"], start, end)
    assert (action.id, action.ticker, action.kind, action.ex_date, action.ratio) == (
        "x1", "ABC", "split", datetime.date(2026, 10, 5), 10.0,
    )


def test_a_second_book_waits_for_the_first_fetch_and_gets_its_answer(monkeypatch):
    monkeypatch.setattr(market_feed, "_memo", {})
    calls = []

    def slow():
        calls.append(1)
        time.sleep(0.05)
        return len(calls)

    answers = []
    threads = [threading.Thread(target=lambda: answers.append(market_feed.serve(("k",), 30, slow))) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert answers == [1, 1, 1, 1]
    assert market_feed.serve(("k",), 0, slow) == 2  # an expired answer is fetched again
