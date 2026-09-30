"""1-minute intraday bars, sourced from Webull's history-bar endpoint.

Built 2026-09-08 so a signal, an alert, or a trade can be placed on a chart
at the moment it actually happened. yfinance's own 1-minute history is
capped at 8 days by Yahoo, confirmed live that day; Webull's history-bar
endpoint has no such ceiling in practice when paged with ``end_time``.

Pure — the Webull client is a fake, and nothing here touches a real network
call or a real database beyond the in-memory fakes below.
"""
import datetime

import pytest

from backend.services import intraday


class _FakeResponse:
    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body


class _FakeMarketData:
    """Records every call and answers from a queue of canned pages, the same
    shape real bars come back in: newest bar first, ISO 8601 with a UTC
    offset, OHLCV as strings."""

    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    def get_history_bar(self, symbol, category, timespan, **kwargs):
        self.calls.append({"symbol": symbol, "category": category, "timespan": timespan, **kwargs})
        if not self.pages:
            return _FakeResponse([])
        return _FakeResponse(self.pages.pop(0))


def _bar(time, price=100.0, volume=1000):
    return {
        "time": time, "open": str(price), "high": str(price + 1),
        "low": str(price - 1), "close": str(price), "volume": str(volume),
    }


@pytest.fixture
def market_data(monkeypatch):
    """Installs a fake Webull client and clears the shared category cache,
    which otherwise leaks between tests via the real quotes module."""
    monkeypatch.setattr(intraday.quotes, "_category_cache", {})

    def _install(pages):
        fake = _FakeMarketData(pages)
        monkeypatch.setattr(intraday.quotes, "_get_market_data", lambda: fake)
        return fake

    return _install


# --- fetch_bars ------------------------------------------------------------


def test_no_client_means_no_bars(monkeypatch):
    monkeypatch.setattr(intraday.quotes, "_get_market_data", lambda: None)
    monkeypatch.setattr(intraday.alpaca_broker, "is_paper", lambda: False)

    assert intraday.fetch_bars("AAPL") is None


def _alpaca_bar(t: str, price: float) -> dict:
    return {"t": t, "o": price, "h": price + 1, "l": price - 1, "c": price, "v": 100}


def test_without_webull_minutes_come_from_alpaca_regular_session_only(monkeypatch):
    monkeypatch.setattr(intraday.quotes, "_get_market_data", lambda: None)
    monkeypatch.setattr(intraday.alpaca_broker, "is_paper", lambda: True)
    asked = {}

    def get_minute_bars(ticker, count, end=None):
        asked.update(ticker=ticker, count=count, end=end)
        return [  # newest first, as Alpaca sends them with sort=desc
            _alpaca_bar("2026-09-29T20:00:00Z", 13.0),  # 16:00 ET, after hours
            _alpaca_bar("2026-09-29T19:59:00Z", 12.0),  # 15:59 ET
            _alpaca_bar("2026-09-29T13:30:00Z", 11.0),  # 09:30 ET
            _alpaca_bar("2026-09-29T13:29:00Z", 10.0),  # 09:29 ET, pre-market
        ]

    monkeypatch.setattr(intraday.alpaca_broker, "get_minute_bars", get_minute_bars)
    end = datetime.datetime(2026, 9, 29, 21, 0)
    bars = intraday.fetch_bars("AAPL", count=4, end_time=end)

    assert asked == {"ticker": "AAPL", "count": 4, "end": end.replace(tzinfo=datetime.timezone.utc)}
    assert [b["timestamp"] for b in bars] == [
        datetime.datetime(2026, 9, 29, 13, 30), datetime.datetime(2026, 9, 29, 19, 59),
    ]
    assert bars[0]["close"] == 11.0


def test_alpaca_never_answers_for_daily_bars(monkeypatch):
    """Daily bars fall back to yfinance in bars.py; only minutes come from Alpaca."""
    from webull.data.common.timespan import Timespan

    monkeypatch.setattr(intraday.quotes, "_get_market_data", lambda: None)
    monkeypatch.setattr(intraday.alpaca_broker, "is_paper", lambda: True)
    monkeypatch.setattr(
        intraday.alpaca_broker, "get_minute_bars", lambda *a, **k: pytest.fail("minute bars asked for a daily fetch"),
    )

    assert intraday.fetch_bars("AAPL", timespan=Timespan.D) is None


def test_a_failed_alpaca_call_reads_as_could_not_ask(monkeypatch):
    monkeypatch.setattr(intraday.quotes, "_get_market_data", lambda: None)
    monkeypatch.setattr(intraday.alpaca_broker, "is_paper", lambda: True)

    def refuse(*a, **k):
        raise RuntimeError("Alpaca refused GET /v2/stocks/AAPL/bars: HTTP 429")

    monkeypatch.setattr(intraday.alpaca_broker, "get_minute_bars", refuse)

    assert intraday.fetch_bars("AAPL") is None


def test_a_page_is_parsed_and_sorted_oldest_first(market_data):
    market_data([[
        _bar("2026-09-08T19:31:00.000+0000", price=102.0),
        _bar("2026-09-08T19:30:00.000+0000", price=101.0),
    ]])

    bars = intraday.fetch_bars("AAPL")

    assert [b["timestamp"] for b in bars] == [
        datetime.datetime(2026, 9, 8, 19, 30),
        datetime.datetime(2026, 9, 8, 19, 31),
    ]
    assert bars[0]["close"] == 101.0


def test_a_dict_wrapped_body_is_read_the_same_as_a_bare_list(market_data):
    market_data([{"data": [_bar("2026-09-08T19:30:00.000+0000")]}])

    bars = intraday.fetch_bars("AAPL")

    assert len(bars) == 1


def test_an_unreadable_bar_is_skipped_not_fatal(market_data):
    market_data([[
        _bar("2026-09-08T19:30:00.000+0000"),
        {"time": "2026-09-08T19:31:00.000+0000", "open": "not a number"},
    ]])

    bars = intraday.fetch_bars("AAPL")

    assert len(bars) == 1


def test_an_empty_first_category_falls_through_to_the_next(monkeypatch, market_data):
    fake = market_data([[], [_bar("2026-09-08T19:30:00.000+0000")]])
    monkeypatch.setattr(intraday.quotes, "_category_cache", {})  # force both categories tried

    bars = intraday.fetch_bars("SPY")

    assert len(bars) == 1
    assert len(fake.calls) == 2


def test_a_failed_call_falls_through_to_the_next_category(monkeypatch, market_data):
    fake = market_data([_bar("2026-09-08T19:30:00.000+0000")])  # placeholder, replaced below

    def get_history_bar(symbol, category, timespan, **kwargs):
        fake.calls.append(category)
        if category == "US_STOCK":
            raise RuntimeError("boom")
        return _FakeResponse([_bar("2026-09-08T19:30:00.000+0000")])

    fake.get_history_bar = get_history_bar

    bars = intraday.fetch_bars("SPY")

    assert len(bars) == 1
    assert fake.calls == ["US_STOCK", "US_ETF"]


def test_end_time_is_converted_to_milliseconds_utc(market_data):
    fake = market_data([[_bar("2026-09-01T14:00:00.000+0000")]])

    intraday.fetch_bars("AAPL", end_time=datetime.datetime(2026, 9, 8, 12, 0))

    sent = fake.calls[0]["end_time"]
    expected = int(datetime.datetime(2026, 9, 8, 12, 0, tzinfo=datetime.timezone.utc).timestamp() * 1000)
    assert sent == expected


# --- backfill ----------------------------------------------------------------


def test_backfill_upserts_every_page(market_data, monkeypatch):
    written = []
    monkeypatch.setattr(intraday.db, "upsert_intraday_bars", lambda ticker, bars: written.append(bars) or len(bars))
    market_data([
        [_bar("2026-09-08T19:00:00.000+0000")],
        [_bar("2026-09-01T19:00:00.000+0000")],  # at/under EXPERIMENT_START -> stop
    ])

    total = intraday.backfill("AAPL")

    assert total == 2
    assert len(written) == 2


def test_backfill_stops_once_it_reaches_since(market_data, monkeypatch):
    monkeypatch.setattr(intraday.db, "upsert_intraday_bars", lambda ticker, bars: len(bars))
    fake = market_data([
        [_bar("2026-09-05T19:00:00.000+0000")],
        [_bar("2026-09-01T19:00:00.000+0000")],
        [_bar("2026-08-01T19:00:00.000+0000")],  # must never be requested
    ])

    intraday.backfill("AAPL", since=datetime.datetime(2026, 9, 2))

    assert len(fake.calls) == 2


def test_backfill_stops_on_a_stalled_page_rather_than_looping_forever(market_data, monkeypatch):
    """Same oldest timestamp twice means end_time isn't moving the window —
    a real bug, and infinite paging is a worse failure than an incomplete
    backfill."""
    monkeypatch.setattr(intraday.db, "upsert_intraday_bars", lambda ticker, bars: len(bars))
    same_page = [_bar("2026-09-05T19:00:00.000+0000")]
    market_data([same_page, list(same_page), list(same_page), list(same_page)])

    total = intraday.backfill("AAPL", since=datetime.datetime(2026, 9, 1))

    assert total == 1  # only the first page was ever accepted


def test_backfill_gives_up_after_the_page_cap(market_data, monkeypatch):
    monkeypatch.setattr(intraday.db, "upsert_intraday_bars", lambda ticker, bars: len(bars))
    monkeypatch.setattr(intraday, "_MAX_BACKFILL_PAGES", 3)
    # Every page one day older than the last, never reaching `since`.
    pages = [
        [_bar(f"2026-09-{8 - i:02d}T19:00:00.000+0000")] for i in range(5)
    ]
    market_data(pages)

    total = intraday.backfill("AAPL", since=datetime.datetime(2020, 1, 1))

    assert total == 3  # stopped at the cap, not the (unreachable) since date


def test_a_dead_client_mid_backfill_stops_cleanly(market_data, monkeypatch):
    monkeypatch.setattr(intraday.db, "upsert_intraday_bars", lambda ticker, bars: len(bars))
    market_data([[_bar("2026-09-05T19:00:00.000+0000")]])  # one page, then nothing

    total = intraday.backfill("AAPL", since=datetime.datetime(2020, 1, 1))

    assert total == 1


# --- capture_recent ------------------------------------------------------------


def test_capture_recent_asks_for_the_capture_window(market_data, monkeypatch):
    monkeypatch.setattr(intraday.db, "upsert_intraday_bars", lambda ticker, bars: len(bars))
    fake = market_data([[_bar("2026-09-08T19:30:00.000+0000")]])

    written = intraday.capture_recent("AAPL")

    assert written == 1
    assert fake.calls[0]["count"] == str(intraday._CAPTURE_MINUTES)


def test_capture_recent_writes_nothing_when_the_fetch_fails(monkeypatch):
    monkeypatch.setattr(intraday.quotes, "_get_market_data", lambda: None)
    monkeypatch.setattr(intraday.alpaca_broker, "is_paper", lambda: False)
    monkeypatch.setattr(
        intraday.db, "upsert_intraday_bars",
        lambda *a: pytest.fail("must not write when there is nothing to write"),
    )

    assert intraday.capture_recent("AAPL") == 0


# --- aggregate_bars ------------------------------------------------------------


def _minute_bar(minute_iso, price, volume=100.0):
    from backend.database.models import IntradayBar
    return IntradayBar(
        ticker="AAPL", timestamp=datetime.datetime.fromisoformat(minute_iso),
        open=price, high=price + 0.5, low=price - 0.5, close=price, volume=volume,
    )


def test_a_bucket_takes_its_open_from_the_first_bar_and_close_from_the_last():
    bucket = intraday.aggregate_bars(
        [
            _minute_bar("2026-09-08T09:30:00", 100.0),
            _minute_bar("2026-09-08T09:31:00", 101.0),
            _minute_bar("2026-09-08T09:32:00", 99.0),
        ],
        interval_minutes=5,
    )

    assert len(bucket) == 1
    assert bucket[0].open == 100.0
    assert bucket[0].close == 99.0
    assert bucket[0].high == 101.5  # 101.0 + the fixture's +0.5
    assert bucket[0].low == 98.5  # 99.0 - the fixture's -0.5
    assert bucket[0].volume == 300.0


def test_a_bucket_is_labelled_by_its_floored_start_not_its_first_bar():
    """A bar starting at 9:31 belongs to the 9:30 bucket, not a 9:31 one — a
    consistent grid regardless of which minute the data happens to start on."""
    bucket = intraday.aggregate_bars([_minute_bar("2026-09-08T09:33:00", 100.0)], interval_minutes=5)

    assert bucket[0].timestamp == datetime.datetime(2026, 9, 8, 9, 30)


def test_bars_split_into_separate_buckets_stay_separate():
    bars = intraday.aggregate_bars(
        [_minute_bar("2026-09-08T09:30:00", 100.0), _minute_bar("2026-09-08T09:36:00", 105.0)],
        interval_minutes=5,
    )

    assert [b.timestamp.minute for b in bars] == [30, 35]


def test_no_bars_means_no_buckets():
    assert intraday.aggregate_bars([], interval_minutes=5) == []


# --- get_chart_bars ------------------------------------------------------------


@pytest.fixture
def chart_sources(monkeypatch):
    """Stands in for both bar sources get_chart_bars stitches together."""
    from backend.services import bars as daily_bars

    state = {"coverage": (None, None), "intraday": [], "daily": []}
    monkeypatch.setattr(intraday.db, "get_intraday_coverage", lambda ticker: state["coverage"])
    monkeypatch.setattr(intraday.db, "get_intraday_bars", lambda ticker, start, end=None: state["intraday"])
    monkeypatch.setattr(
        daily_bars, "get_bars",
        lambda ticker, start, end=None, include_today=False, today=None: state["daily"],
    )
    return state


class _DailyBar:
    def __init__(self, date, price):
        self.date, self.open, self.high, self.low, self.close, self.volume = (
            date, price, price + 1, price - 1, price, 1000.0
        )


def test_no_intraday_coverage_falls_back_to_daily_bars_entirely(chart_sources):
    chart_sources["coverage"] = (None, None)
    chart_sources["daily"] = [_DailyBar("2026-09-01", 300.0), _DailyBar("2026-09-02", 301.0)]

    got = intraday.get_chart_bars("AAPL", days=7)

    assert [b.timestamp.date().isoformat() for b in got] == ["2026-09-01", "2026-09-02"]


def test_daily_bars_fill_only_before_the_intraday_coverage_starts(chart_sources, monkeypatch):
    """The AAPL smoke test's own shape: older days come from the daily cache,
    the covered stretch comes from aggregated intraday, joined seamlessly."""
    fixed_now = datetime.datetime(2026, 9, 8, 20, 0)

    class _FixedDatetime(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now.replace(tzinfo=tz) if tz else fixed_now

    monkeypatch.setattr(datetime, "datetime", _FixedDatetime)
    chart_sources["coverage"] = (datetime.datetime(2026, 9, 5, 13, 30), fixed_now)
    chart_sources["daily"] = [_DailyBar("2026-09-01", 300.0), _DailyBar("2026-09-02", 301.0)]
    chart_sources["intraday"] = [_minute_bar("2026-09-05T13:30:00", 305.0)]

    got = intraday.get_chart_bars("AAPL", days=10)

    assert [b.timestamp.date().isoformat() for b in got] == ["2026-09-01", "2026-09-02", "2026-09-05"]


def test_fine_resolution_never_reaches_further_back_than_the_policy_cap(chart_sources):
    """Even with full coverage back to day one, a request wider than
    _MAX_FINE_DAYS gets daily bars for the excess, not more intraday detail."""
    old_cap = intraday._MAX_FINE_DAYS
    try:
        intraday._MAX_FINE_DAYS = 5
        chart_sources["coverage"] = (datetime.datetime(2020, 1, 1), datetime.datetime.now())
        seen_starts = []

        def fake_get_intraday_bars(ticker, start, end=None):
            seen_starts.append(start)
            return []

        intraday.db.get_intraday_bars = fake_get_intraday_bars
        intraday.get_chart_bars("AAPL", days=30)

        assert seen_starts[0] >= datetime.datetime.now() - datetime.timedelta(days=6)
    finally:
        intraday._MAX_FINE_DAYS = old_cap
