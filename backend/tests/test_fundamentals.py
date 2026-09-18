"""The ``fundamentals`` fetch renders the vendor's documented payloads, and
says so when one is missing. Pure: the payloads are the shapes Webull's
docs describe, and no call leaves the test.
"""
from types import SimpleNamespace

from backend.services import fundamentals

INDICATORS = {
    "currency": "USD",
    "values": {
        "roe": [
            {"fiscal_year": 2026, "fiscal_period": 1, "value": "0.31"},
            {"fiscal_year": 2026, "fiscal_period": 2, "value": "0.33"},
            {"fiscal_year": 2025, "fiscal_period": 4, "value": "0.29"},
        ],
        "net_margin": [
            {"fiscal_year": 2026, "fiscal_period": 2, "value": "0.25"},
            {"fiscal_year": 2026, "fiscal_period": 1, "value": "0.24"},
        ],
        "cap_surplus_ps": [{"fiscal_year": 2026, "fiscal_period": 2, "value": "1.0"}],
    },
}

INDUSTRY = {
    "fiscal_year": 2026,
    "fiscal_period": 2,
    "industry_name": "Semiconductors",
    "type": "PE_TTM",
    "data": [
        {"symbol": "NVDA", "name": "NVIDIA", "rank": 1, "value": "48.2"},
        {"symbol": "AMD", "name": "Advanced Micro Devices", "rank": 2, "value": "41.0"},
        {"symbol": "AVGO", "name": "Broadcom", "rank": 3, "value": "35.5"},
        {"symbol": "QCOM", "name": "Qualcomm", "rank": 4, "value": "22.1"},
        {"symbol": "TXN", "name": "Texas Instruments", "rank": 5, "value": "21.7"},
        {"symbol": "MU", "name": "Micron", "rank": 6, "value": "18.3"},
        {"symbol": "ADI", "name": "Analog Devices", "rank": 7, "value": "17.9"},
        {"symbol": "INTC", "name": "Intel", "rank": 8, "value": "15.2"},
    ],
}

RATING = {
    "symbol": "INTC", "category": "US_STOCK", "number": "38", "strong_buy": "6",
    "buy": "9", "hold": "20", "sell": "2", "under_perform": "1",
    "effective_start_date": "2026-09-10T06:24:56.038+0000",
}


def test_indicators_are_a_table_newest_report_first():
    text = fundamentals.indicators_table("INTC", INDICATORS)

    assert text.startswith("INTC's financial indicators, newest report first, in USD")
    assert "| Metric | 2026 Q2 | 2026 Q1 | 2025 Q4 |" in text
    assert "| Return on equity | 0.33 | 0.31 | 0.29 |" in text
    # A metric missing a report shows a dash there rather than shifting.
    assert "| Net margin | 0.25 | 0.24 | — |" in text
    # Only the documented metrics the model reads; the rest of the payload is
    # not printed.
    assert "cap_surplus_ps" not in text


def test_an_empty_indicator_payload_says_so():
    assert fundamentals.indicators_table("INTC", {}) == "No financial indicators on record for INTC."
    assert fundamentals.indicators_table("INTC", {"values": {"roe": []}}) == (
        "No financial indicators on record for INTC."
    )


def test_the_industry_table_shows_the_leaders_and_the_ticker_itself():
    text = fundamentals.industry_table("INTC", INDUSTRY)

    assert text.startswith("INTC ranks 8 of 8 in Semiconductors by PE_TTM:")
    assert "| 1 | NVDA | NVIDIA | 48.2 |" in text
    assert "| 6 | MU | Micron | 18.3 |" in text
    assert "| 7 | ADI |" not in text, "six peers are shown, then the ticker's own row"
    assert "| 8 | INTC (this one) | Intel | 15.2 |" in text


def test_a_ticker_missing_from_its_industry_table_is_said_not_invented():
    text = fundamentals.industry_table("ZZZZ", INDUSTRY)

    assert text.startswith("ZZZZ is not in the vendor's Semiconductors table (8 names, by PE_TTM):")
    assert "(this one)" not in text
    assert fundamentals.industry_table("INTC", {}) == "No industry comparison on record for INTC."


def test_the_rating_is_counts_with_a_date():
    assert fundamentals.rating_line("INTC", RATING) == (
        "Analysts on INTC: 38 in total — strong buy 6, buy 9, hold 20, sell 2, "
        "underperform 1 (as of 2026-09-10)."
    )
    assert fundamentals.rating_line("INTC", {}) == "No analyst rating on record for INTC."


class _Response:
    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body


def test_describe_makes_the_three_calls_and_survives_one_failing(monkeypatch):
    """A block that fails is one line; the other two still come back."""
    def paced(call, what):
        if what == "industry comparison":
            raise RuntimeError("429")
        return call()
    monkeypatch.setattr(fundamentals.quotes, "market_data_request", paced)

    class _Fundamentals:
        def __init__(self, client):
            pass

        def get_financials_indicators(self, symbol, category="US_STOCK", type=None, count=None):
            assert (symbol, type, count) == ("INTC", "QUARTERLY", 4)
            return _Response(INDICATORS)

        def get_industry_comparison(self, symbol, category="US_STOCK", sort_by=None):
            raise AssertionError("never reached: the paced call fails first")

    class _Instrument:
        def __init__(self, client):
            pass

        def get_analyst_rating(self, symbol, category="US_STOCK"):
            return _Response([RATING])

    import sys
    monkeypatch.setitem(sys.modules, "webull.data.quotes.fundamentals", SimpleNamespace(Fundamentals=_Fundamentals))
    monkeypatch.setitem(sys.modules, "webull.data.quotes.instrument", SimpleNamespace(Instrument=_Instrument))

    text = fundamentals.describe("intc", client=object())

    assert "| Return on equity | 0.33 | 0.31 | 0.29 |" in text
    assert "The industry comparison could not be fetched from the vendor." in text
    assert "Analysts on INTC: 38 in total" in text


def test_no_vendor_and_no_ticker_are_said_plainly(monkeypatch):
    monkeypatch.setattr(fundamentals.quotes, "get_api_client", lambda: None)

    assert fundamentals.describe("INTC") == (
        "Fundamentals for INTC are not available: the market data vendor is not configured."
    )
    assert fundamentals.describe("") == "You asked for fundamentals but named no ticker."
