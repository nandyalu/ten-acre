"""Unit tests for the pure parts of backend/services/quotes.py.

Split out of test_ask_quotes.py on 2026-09-10, when backend/services/ask.py
was deleted -- nothing had imported it since the Discord commands were
removed on 2026-09-01, and it still told the reader to "run /analyze first".
"""
import pytest

from backend.services import quotes

# conftest replaces get_api_client for every test; the real one is kept here,
# at import time, for the tests that are about it.
_REAL_GET_API_CLIENT = quotes.get_api_client
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


def test_get_snapshots_leaves_out_a_value_that_cannot_travel_in_the_list(monkeypatch):
    """QuiverQuant handed over "GLAS FUNDS, LP" as a ticker on 2026-10-01. In a
    comma-separated list of 100 it read as the 101st symbol, Webull answered
    ILLEGAL_PARAMETER rather than INVALID_SYMBOL, and the retry that strips
    bad names never ran, so the whole batch was lost."""
    sent = []

    class FakeMarketData:
        def get_snapshot(self, symbols, category):
            sent.append(symbols)
            return _FakeResponse([{"symbol": s, "price": 1.0} for s in symbols.split(",")])

    monkeypatch.setattr(quotes, "_get_market_data", lambda: FakeMarketData())
    monkeypatch.setattr(quotes, "market_data_request", lambda call, what: call())

    rows = quotes.get_snapshots(["AAPL", "GLAS FUNDS, LP", "", "MSFT", "BRK.B"])

    assert sent == ["AAPL,MSFT,BRK.B"]
    assert [r["symbol"] for r in rows] == ["AAPL", "MSFT", "BRK.B"]


def test_a_second_thread_arriving_during_client_init_waits_for_the_client(monkeypatch):
    """Until 2026-10-02 the done flag was set before the client was built, so a
    thread arriving in that window read None and `_get_market_data` kept that
    None for the life of the process. The watchdog and the snapshot export
    start in the same second, so the live container ran without a market-data
    client, and without any batch snapshot, until its next restart."""
    import sys
    import threading
    import time
    import types

    class FakeApiClient:
        def __init__(self, key, secret, region):
            pass

        def add_endpoint(self, region, endpoint):
            pass

    class FakeInitializer:
        @staticmethod
        def initializer(client):
            time.sleep(0.3)  # the window the second thread used to fall into

    monkeypatch.setitem(sys.modules, "webull.core.client", types.SimpleNamespace(ApiClient=FakeApiClient))
    monkeypatch.setitem(
        sys.modules, "webull.core.http.initializer.client_initializer",
        types.SimpleNamespace(ClientInitializer=FakeInitializer),
    )
    monkeypatch.setenv("WEBULL_APP_KEY", "k")
    monkeypatch.setenv("WEBULL_APP_SECRET", "s")
    monkeypatch.setattr(quotes, "_api_client", None)
    monkeypatch.setattr(quotes, "_api_client_done", False)
    monkeypatch.setattr(quotes, "get_api_client", _REAL_GET_API_CLIENT)

    seen = {}
    first = threading.Thread(target=lambda: seen.update(first=quotes.get_api_client()))
    first.start()
    time.sleep(0.05)
    seen["second"] = quotes.get_api_client()
    first.join()

    assert isinstance(seen["first"], FakeApiClient)
    assert seen["second"] is seen["first"], "the second caller must wait, not read the gap"


def test_market_data_asked_for_before_the_client_exists_is_not_lost_for_good(monkeypatch):
    import sys
    import types

    class FakeMarketData:
        def __init__(self, client):
            self.client = client

    monkeypatch.setitem(
        sys.modules, "webull.data.quotes.market_data", types.SimpleNamespace(MarketData=FakeMarketData)
    )
    monkeypatch.setattr(quotes, "_market_data", None)
    answers = iter([None, "client"])
    monkeypatch.setattr(quotes, "get_api_client", lambda: next(answers))

    assert quotes._get_market_data() is None
    later = quotes._get_market_data()
    assert isinstance(later, FakeMarketData) and later.client == "client"
    assert quotes._get_market_data() is later
