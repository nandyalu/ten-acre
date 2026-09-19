"""The ``fundamentals`` fetch renders what Yahoo Finance and Webull send, and
says so when a block is missing. Pure: the payloads are the shapes the two
vendors return, and no call leaves the test.
"""
from types import SimpleNamespace

import pandas as pd

from backend.services import fundamentals

# ``Ticker.info`` for INTC on 2026-09-19, cut to the keys that matter here.
INFO = {
    "longName": "Intel Corporation",
    "sector": "Technology",
    "industry": "Semiconductors",
    "financialCurrency": "USD",
    "marketCap": 574070980608,
    "trailingPE": None,
    "forwardPE": 52.66527,
    "pegRatio": 1.36,
    "priceToBook": 6.256121,
    "trailingEps": -2.09,
    "profitMargins": -0.19794,
    "returnOnEquity": -0.10715,
    "returnOnAssets": 0.0141199995,
    "debtToEquity": 48.997,
    "currentRatio": 1.604,
    "freeCashflow": 4866375168,
    "beta": 2.231,
    "fiftyTwoWeekHigh": 120.5,
}

# ``Ticker.quarterly_income_stmt``: rows down, period ends across, newest
# first, five quarters deep, with a row the table does not show.
QUARTERS = pd.DataFrame(
    {
        pd.Timestamp("2026-06-30"): [16128000000.0, 6509000000.0, -11033000000.0, -2.16, 0.21],
        pd.Timestamp("2026-03-31"): [13577000000.0, 5347000000.0, -3728000000.0, -0.73, 0.21],
        pd.Timestamp("2025-12-31"): [13674000000.0, 4943000000.0, -591000000.0, -0.12, 0.21],
        pd.Timestamp("2025-09-30"): [13653000000.0, 5218000000.0, 4063000000.0, 0.9, 0.21],
        pd.Timestamp("2025-06-30"): [12859000000.0, 3542000000.0, -2918000000.0, -0.67, 0.21],
    },
    index=["Total Revenue", "Gross Profit", "Net Income", "Diluted EPS", "Tax Rate For Calcs"],
)

RATING = {
    "symbol": "INTC", "category": "US_STOCK", "number": "38", "strong_buy": "6",
    "buy": "9", "hold": "20", "sell": "2", "under_perform": "1",
    "effective_start_date": "2026-09-10T06:24:56.038+0000",
}


def test_the_profile_names_the_company_and_lists_its_ratios():
    text = fundamentals.profile_table("INTC", INFO)

    assert text.startswith(
        "INTC is Intel Corporation, in Technology, Semiconductors. Its ratios as "
        "Yahoo Finance reports them, money in USD; a margin, a return or a growth "
        "rate is a fraction of one:"
    )
    assert "| Market cap | 574,070,980,608 |" in text
    assert "| PE, forward | 52.67 |" in text
    # Four significant digits, so a small fraction is not rounded away.
    assert "| Return on assets | 0.01412 |" in text
    assert "| Profit margin | -0.1979 |" in text
    assert "| Free cash flow | 4,866,375,168 |" in text
    # A ratio the vendor left empty has no row, and a key the table does not
    # show is not printed.
    assert "PE, trailing" not in text
    assert "fiftyTwoWeekHigh" not in text and "120.5" not in text


def test_a_stub_profile_says_so():
    # yfinance answers an unknown symbol with a stub dict, not an error.
    assert fundamentals.profile_table("ZZZZ", {"trailingPegRatio": None}) == (
        "No company profile on record for ZZZZ."
    )
    assert fundamentals.profile_table("ZZZZ", None) == "No company profile on record for ZZZZ."
    assert fundamentals.profile_table("ZZZZ", {"longName": "Zed Corp", "sector": "Energy"}) == (
        "ZZZZ is Zed Corp, in Energy. No ratios on record for it at Yahoo Finance."
    )


def test_the_quarters_are_a_table_newest_first():
    text = fundamentals.quarters_table("INTC", QUARTERS)

    assert text.startswith(
        "INTC's last 4 quarters from Yahoo Finance, newest first, each headed by "
        "the quarter's end date, as the vendor reports them:"
    )
    assert "| Metric | 2026-06-30 | 2026-03-31 | 2025-12-31 | 2025-09-30 |" in text
    assert "| Revenue | 16,128,000,000 | 13,577,000,000 | 13,674,000,000 | 13,653,000,000 |" in text
    assert "| Net income | -11,033,000,000 | -3,728,000,000 | -591,000,000 | 4,063,000,000 |" in text
    assert "| EPS, diluted | -2.16 | -0.73 | -0.12 | 0.9 |" in text
    # Four quarters, not five; the rows the table does not show stay out; a
    # row the vendor did not send is left out rather than shown empty.
    assert "2025-06-30" not in text
    assert "Tax Rate" not in text
    assert "Operating income" not in text


def test_a_missing_quarter_value_is_a_dash_and_columns_stay_aligned():
    frame = QUARTERS.copy()
    frame.at["Gross Profit", pd.Timestamp("2026-03-31")] = float("nan")

    text = fundamentals.quarters_table("INTC", frame)

    assert "| Gross profit | 6,509,000,000 | — | 4,943,000,000 | 5,218,000,000 |" in text


def test_an_empty_statement_says_so():
    assert fundamentals.quarters_table("INTC", pd.DataFrame()) == (
        "No quarterly income statement on record for INTC."
    )
    assert fundamentals.quarters_table("INTC", None) == (
        "No quarterly income statement on record for INTC."
    )
    only_unshown = pd.DataFrame({pd.Timestamp("2026-06-30"): [0.21]}, index=["Tax Rate For Calcs"])
    assert fundamentals.quarters_table("INTC", only_unshown) == (
        "No quarterly income statement on record for INTC."
    )


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


class _Stock:
    """``yf.Ticker`` as the test needs it: two properties, one of which
    can fail."""

    statement_fails = False

    def __init__(self, symbol):
        assert symbol == "INTC"

    @property
    def info(self):
        return INFO

    @property
    def quarterly_income_stmt(self):
        if self.statement_fails:
            raise RuntimeError("HTTP 500")
        return QUARTERS


def _webull(monkeypatch, rating=RATING):
    class _Instrument:
        def __init__(self, client):
            pass

        def get_analyst_rating(self, symbol, category="US_STOCK"):
            return _Response([rating])

    import sys
    monkeypatch.setitem(sys.modules, "webull.data.quotes.instrument", SimpleNamespace(Instrument=_Instrument))
    monkeypatch.setattr(fundamentals.quotes, "market_data_request", lambda call, what: call())


def test_describe_reads_yahoo_twice_and_webull_once(monkeypatch):
    monkeypatch.setattr(fundamentals.yf, "Ticker", _Stock)
    _webull(monkeypatch)

    text = fundamentals.describe("intc", client=object())

    assert "INTC is Intel Corporation, in Technology, Semiconductors." in text
    assert "| EPS, diluted | -2.16 | -0.73 | -0.12 | 0.9 |" in text
    assert "Analysts on INTC: 38 in total" in text


def test_a_yahoo_read_that_fails_is_one_line_and_the_rest_still_comes_back(monkeypatch):
    monkeypatch.setattr(_Stock, "statement_fails", True)
    monkeypatch.setattr(fundamentals.yf, "Ticker", _Stock)
    _webull(monkeypatch)

    text = fundamentals.describe("INTC", client=object())

    assert "| PE, forward | 52.67 |" in text
    assert "The quarterly income statement could not be fetched from Yahoo Finance." in text
    assert "Analysts on INTC: 38 in total" in text


def test_no_vendor_still_answers_from_yahoo(monkeypatch):
    """The rating is the only block on Webull, so a deployment without the
    vendor still gets the ratios and the quarters."""
    monkeypatch.setattr(fundamentals.yf, "Ticker", _Stock)
    monkeypatch.setattr(fundamentals.quotes, "get_api_client", lambda: None)

    text = fundamentals.describe("INTC")

    assert "| PE, forward | 52.67 |" in text
    assert "| Revenue | 16,128,000,000 |" in text
    assert text.endswith(
        "The analyst rating for INTC is not available: the market data vendor is not configured."
    )


def test_no_ticker_is_said_plainly():
    assert fundamentals.describe("") == "You asked for fundamentals but named no ticker."
