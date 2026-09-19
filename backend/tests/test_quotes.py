"""Unit tests for the pure parts of backend/services/quotes.py.

Split out of test_ask_quotes.py on 2026-09-10, when backend/services/ask.py
was deleted -- nothing had imported it since the Discord commands were
removed on 2026-09-01, and it still told the reader to "run /analyze first".
"""
import pytest

from backend.services import quotes
from backend.services.quotes import extract_price


def test_extract_price_shapes():
    assert extract_price([{"symbol": "AAPL", "price": "333.26"}]) == 333.26
    assert extract_price({"snapshots": [{"last_price": 12.5}]}) == 12.5
    assert extract_price({"data": [{"close": "9.99"}]}) == 9.99
    assert extract_price({"symbol": "AAPL", "price": 101.0}) == 101.0  # bare dict


def test_extract_price_rejects_junk():
    assert extract_price([]) is None
    assert extract_price(None) is None
    assert extract_price([{"symbol": "AAPL"}]) is None
    assert extract_price([{"price": "not-a-number"}]) is None
    assert extract_price([{"price": 0}]) is None  # zero/negative quotes are unusable
    assert extract_price("weird") is None


def test_rejected_symbols_parses_webulls_bracket_list():
    exc = Exception(
        "ServerException:HTTP Status: 417, Code: INVALID_SYMBOL, "
        "Msg: The symbols does not exist in the category. [ELN, EA, LBRDK].,"
        " RequestID: abc"
    )
    assert quotes._rejected_symbols(exc) == {"ELN", "EA", "LBRDK"}


def test_rejected_symbols_empty_for_an_unrelated_error():
    assert quotes._rejected_symbols(Exception("connection reset")) == set()


class _FakeResponse:
    def __init__(self, rows):
        self._rows = rows

    def json(self):
        return self._rows


def test_get_snapshots_retries_once_without_the_rejected_symbols(monkeypatch):
    """A batch of 100 must not go empty because one text-source ticker turns
    out not to exist in Webull's category -- see the 2026-09-15 JOURNEY.md
    entry: ELN/EA/LBRDK sank a live 100-ticker request this way."""
    monkeypatch.setattr(quotes, "_get_market_data", lambda: object())
    calls = []

    def fake_request(call, what):
        calls.append(what)
        if len(calls) == 1:
            raise Exception(
                "Code: INVALID_SYMBOL, Msg: The symbols does not exist "
                "in the category. [ELN, EA, LBRDK]."
            )
        return _FakeResponse([{"symbol": "AAPL", "price": 100.0}])

    monkeypatch.setattr(quotes, "market_data_request", fake_request)
    rows = quotes.get_snapshots(["AAPL", "ELN", "EA", "LBRDK"])
    assert rows == [{"symbol": "AAPL", "price": 100.0}]
    assert len(calls) == 2
    assert "4 ticker" in calls[0]
    assert "1 ticker" in calls[1]  # AAPL alone, after the three rejects are dropped


@pytest.fixture(autouse=True)
def forget_rejected_symbols(monkeypatch):
    """Each test starts as a fresh process, with nothing remembered."""
    monkeypatch.setattr(quotes, "_not_in_category", {})


def test_get_snapshots_never_sends_a_rejected_symbol_again(monkeypatch):
    """The retry above fixed one call. The next call used to send the same
    symbols again, and the same six names sank the candidate screen's batch
    every 15 minutes from 2026-09-10 to 2026-09-18, about 300 refused
    requests. A rejection is remembered for the life of the process."""
    monkeypatch.setattr(quotes, "_get_market_data", lambda: object())
    calls = []

    def fake_request(call, what):
        calls.append(what)
        if len(calls) == 1:
            raise Exception(
                "Code: INVALID_SYMBOL, Msg: The symbols does not exist "
                "in the category. [BK, MOG.A]."
            )
        return _FakeResponse([{"symbol": "AAPL", "price": 100.0}])

    monkeypatch.setattr(quotes, "market_data_request", fake_request)
    quotes.get_snapshots(["AAPL", "BK", "MOG.A"])
    assert len(calls) == 2

    rows = quotes.get_snapshots(["AAPL", "BK", "MOG.A"])
    assert rows == [{"symbol": "AAPL", "price": 100.0}]
    assert len(calls) == 3  # one request, not a refusal and a retry
    assert "1 ticker" in calls[2]  # BK and MOG.A were never sent

    # A batch made only of remembered names costs no request at all.
    assert quotes.get_snapshots(["BK", "MOG.A"]) == []
    assert len(calls) == 3


def test_get_snapshots_gives_up_on_an_unrelated_failure(monkeypatch):
    monkeypatch.setattr(quotes, "_get_market_data", lambda: object())

    def fake_request(call, what):
        raise Exception("connection reset")

    monkeypatch.setattr(quotes, "market_data_request", fake_request)
    assert quotes.get_snapshots(["AAPL"]) == []


def test_get_snapshots_returns_empty_with_no_client_configured(monkeypatch):
    monkeypatch.setattr(quotes, "_get_market_data", lambda: None)
    assert quotes.get_snapshots(["AAPL"]) == []
