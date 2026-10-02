"""Screened candidate proposals.

The filter is the whole point. A live pull of the raw gainers screen returned a
stock up 927% in a day; put in front of a few-thousand-dollar swing account
that is not an opportunity, it is a way to lose money. These tests pin the
floors and pin that nothing is ever followed automatically — analysis costs
about seven minutes of GPU per ticker, so the scarce resource is attention, not
ideas.
"""
import pytest

from backend.services import candidates

_real_listed = candidates._listed


def _row(symbol="AAA", price=50.0, volume=5_000_000, change=0.03, name="A Corp"):
    return {
        "symbol": symbol,
        "price": str(price),
        "volume": str(volume),
        "change_ratio": str(change),
        "name": name,
    }


@pytest.fixture(autouse=True)
def no_symbol_directory(monkeypatch):
    """Never reach Nasdaq Trader from a test. No directory filters nothing."""
    monkeypatch.setattr(candidates, "_listed", lambda: {})


@pytest.fixture
def screened(monkeypatch):
    def build(
        active=(), gainers=(), tracked=(), inactive=(),
        congress=(), trending=(), snapshots=(),
    ):
        class FakeScreener:
            def __init__(self, _client):
                pass

            def get_most_active(self, *a, **kw):
                return {"data": list(active)}

            def get_gainers_losers(self, *a, **kw):
                return {"data": list(gainers)}

        import sys, types

        module = types.ModuleType("webull.data.quotes.screener")
        module.Screener = FakeScreener
        monkeypatch.setitem(sys.modules, "webull.data.quotes.screener", module)
        monkeypatch.setattr(candidates.quotes, "get_api_client", lambda: object())
        monkeypatch.setattr(candidates.db, "get_watchlist", lambda: list(tracked))
        monkeypatch.setattr(candidates.listings, "inactive_tickers", lambda: list(inactive))
        # The two text sources hit real HTTP by design (Reddit, Yahoo) — never
        # let a test reach them for real. Empty by default, same as an
        # unconfigured Webull client, so every test written before these
        # sources existed still runs with no network at all.
        monkeypatch.setattr(candidates, "_congress_tickers", lambda: set(congress))
        monkeypatch.setattr(candidates, "_trending_tickers", lambda: set(trending))
        monkeypatch.setattr(candidates.quotes, "get_snapshots", lambda tickers, **kw: list(snapshots))
        return candidates.fetch_candidates()

    return build


def test_a_penny_stock_pump_is_filtered_out(screened):
    """The raw screen really did return a stock up 927% at under a dollar."""
    found = screened(gainers=[_row("PLAG", price=0.81, volume=210_000_000, change=9.27)])
    assert found == []


def test_an_illiquid_name_is_filtered_out(screened):
    """Too thin to get out of at any size worth taking."""
    assert screened(active=[_row("THIN", price=50.0, volume=1000)]) == []


def test_a_liquid_name_survives(screened):
    found = screened(active=[_row("NVDA", price=217.5, volume=101_000_000)])

    assert [c.ticker for c in found] == ["NVDA"]
    assert found[0].volume_m == pytest.approx(101.0)


def test_already_tracked_names_are_not_proposed(screened):
    """Proposing what is already followed is noise, and the sweep already
    covers it."""
    assert screened(active=[_row("ZBH", price=97.0)], tracked=["ZBH"]) == []


def test_names_already_tracked_are_not_proposed(screened):
    """One set covers held names too: the agent may not untrack a position it
    still owns, so everything it holds is on the watchlist."""
    assert screened(active=[_row("GOOG", price=350.0)], tracked=["GOOG"]) == []


def test_delisted_names_are_not_proposed(screened):
    """listings already knows these produce no usable data."""
    assert screened(active=[_row("AILEQ", price=50.0)], inactive=["AILEQ"]) == []


def test_the_most_liquid_come_first(screened):
    found = screened(active=[
        _row("LOW", volume=2_000_000),
        _row("HIGH", volume=90_000_000),
        _row("MID", volume=20_000_000),
    ])
    assert [c.ticker for c in found] == ["HIGH", "MID", "LOW"]


def test_a_name_on_both_screens_appears_once_as_the_liquid_one(screened):
    """Most active runs first, so a busy name is described as busy rather than
    as a mover."""
    found = screened(active=[_row("DUP")], gainers=[_row("DUP")])

    assert len(found) == 1
    assert found[0].source == "most active"


def test_the_shortlist_is_capped(screened):
    rows = [_row(f"T{i}", volume=1_000_000 + i) for i in range(30)]
    assert len(screened(active=rows)) == candidates.MAX_PROPOSED


def test_a_failing_screen_does_not_lose_the_other(screened, monkeypatch):
    """One endpoint being down should still leave a usable shortlist."""

    class HalfBroken:
        def __init__(self, _c):
            pass

        def get_most_active(self, *a, **kw):
            raise RuntimeError("503")

        def get_gainers_losers(self, *a, **kw):
            return {"data": [_row("OK")]}

    import sys, types

    module = types.ModuleType("webull.data.quotes.screener")
    module.Screener = HalfBroken
    monkeypatch.setitem(sys.modules, "webull.data.quotes.screener", module)
    monkeypatch.setattr(candidates.quotes, "get_api_client", lambda: object())
    monkeypatch.setattr(candidates.db, "get_watchlist", lambda: [])
    monkeypatch.setattr(candidates.listings, "inactive_tickers", lambda: [])

    assert [c.ticker for c in candidates.fetch_candidates()] == ["OK"]


def test_no_client_means_no_candidates(monkeypatch):
    monkeypatch.setattr(candidates.quotes, "get_api_client", lambda: None)
    monkeypatch.setattr(candidates.alpaca_broker, "is_paper", lambda: False)
    assert candidates.fetch_candidates() == []


def test_an_alpaca_deployment_screens_through_alpaca(monkeypatch):
    """An Alpaca deployment has no Webull client. Without this screen the
    agent saw no menu and could research nothing (2026-09-29)."""
    alpaca = candidates.alpaca_broker
    monkeypatch.setattr(candidates.quotes, "get_api_client", lambda: None)
    monkeypatch.setattr(alpaca, "is_paper", lambda: True)
    monkeypatch.setattr(alpaca, "most_active", lambda top: ["BUSY", "PENNY", "FUND"])
    monkeypatch.setattr(alpaca, "day_gainers", lambda top: ["MOVER", "PUMP"])
    rows = {
        "BUSY": _row("BUSY", volume=9_000_000, name=""),
        "PENNY": _row("PENNY", price=0.5, name=""),
        "MOVER": _row("MOVER", change=0.08, name=""),
        "PUMP": _row("PUMP", change=2.0, name=""),
        "TREND": _row("TREND", volume=2_000_000, name=""),
        "FUND": _row("FUND", volume=90_000_000, name=""),
    }
    monkeypatch.setattr(alpaca, "get_snapshots", lambda tickers: [rows[t] for t in tickers if t in rows])
    listed = {t: (f"{t} Inc", False) for t in rows} | {"FUND": ("A Bond ETF", True)}
    monkeypatch.setattr(candidates, "_listed", lambda: listed)
    monkeypatch.setattr(candidates.db, "get_watchlist", lambda: [])
    monkeypatch.setattr(candidates.listings, "inactive_tickers", lambda: [])
    monkeypatch.setattr(candidates, "_congress_tickers", lambda: set())
    monkeypatch.setattr(candidates, "_trending_tickers", lambda: {"TREND"})

    picked = candidates.fetch_candidates()

    assert [(c.ticker, c.source) for c in picked] == [
        ("TREND", "trending (Yahoo Finance)"), ("BUSY", "most active"), ("MOVER", "day gainers"),
    ]
    assert [c.name for c in picked] == ["TREND Inc", "BUSY Inc", "MOVER Inc"]


def test_a_pump_that_cleared_the_price_floor_is_still_filtered(screened):
    """PLAG passed every other filter at $5.81 — because the 927% pump is what
    lifted it over the $5 floor. A price floor alone cannot catch this."""
    assert screened(active=[_row("PLAG", price=5.81, volume=212_000_000, change=9.27)]) == []


def test_a_collapse_is_filtered_too(screened):
    """A stock halved in a session is equally not a one-to-two-week swing."""
    assert screened(active=[_row("CRASH", price=20.0, volume=50_000_000, change=-0.55)]) == []


def test_an_ordinary_move_is_kept(screened):
    found = screened(active=[_row("ACHR", price=6.79, volume=90_000_000, change=0.085)])
    assert [c.ticker for c in found] == ["ACHR"]


def test_a_congress_ticker_is_priced_and_screened_like_any_other(screened):
    """A bare ticker from QuiverQuant is not trusted until a real snapshot
    backs it, and then it faces the same floors as a Webull row."""
    found = screened(
        congress=["NEW1"],
        snapshots=[_row("NEW1", price=20.0, volume=5_000_000)],
    )
    assert [c.ticker for c in found] == ["NEW1"]
    assert found[0].source == "congress trade (QuiverQuant)"


def test_a_trending_ticker_below_the_volume_floor_is_still_dropped(screened):
    """The text sources feed the same funnel — they do not bypass the filter
    that is the module's whole point."""
    found = screened(trending=["THIN2"], snapshots=[_row("THIN2", volume=1000)])
    assert found == []


def test_a_webull_screen_wins_over_a_text_source_for_the_same_ticker(screened):
    """A screen already carries a verified price; a text source is only
    there for a name a price screen would never surface."""
    found = screened(active=[_row("DUP2")], congress=["DUP2"])
    assert len(found) == 1
    assert found[0].source == "most active"


def test_text_sources_keep_their_reserved_slots_against_high_volume_webull_rows(screened):
    """A congressional trade or a trending pick is almost always lower-volume
    than a Webull most-active row, so a single volume sort buried them. Each
    text source now keeps a floor of the menu for itself."""
    active = [_row(f"HV{i}", volume=200_000_000 - i) for i in range(20)]
    found = screened(
        active=active,
        congress=["C1", "C2", "C3"],
        trending=["Y1", "Y2"],
        snapshots=[
            _row("C1", volume=1_000_000), _row("C2", volume=1_100_000), _row("C3", volume=1_200_000),
            _row("Y1", volume=1_300_000), _row("Y2", volume=1_400_000),
        ],
    )
    tickers = [c.ticker for c in found]
    assert len(found) == candidates.MAX_PROPOSED
    assert {"C1", "C2", "C3"}.issubset(tickers)
    assert {"Y1", "Y2"}.issubset(tickers)


def test_a_failing_text_source_does_not_lose_the_other(screened, monkeypatch):
    def broken():
        raise RuntimeError("reddit is down")

    monkeypatch.setattr(candidates, "_congress_tickers", broken)
    found = screened(trending=["OK2"], snapshots=[_row("OK2")])
    assert [c.ticker for c in found] == ["OK2"]


def test_congress_tickers_reads_the_embedded_trade_table(monkeypatch):
    """The live shape, confirmed 2026-09-15: a plain JS array literal
    inlined in the page, not fetched separately — column 0 is the ticker,
    '-' when the trade has none."""
    page = (
        "<html><script>"
        "let recentTradesData = [['AMAT', 'Applied Materials', 'ST', 'Purchase', "
        "'$1,001 - $15,000', 'Josh Gottheimer', 'House', 'D', "
        "'2026-09-14 00:00:00', '2026-08-06 00:00:00', '-', 'House-1', -18.5, "
        "'Josh Gottheimer', 'https://example.com/x.jpg', 'G000583'], "
        "['-', 'Electronic Arts Inc', 'Stock', 'Sale (Full)', "
        "'$15,001 - $50,000', 'Angus S. King Jr.', 'Senate', 'I', "
        "'2026-09-14 06:53:00', '2026-08-05 00:00:00', '-', 'Senate-1', '-', "
        "'King, Angus', 'https://example.com/y.jpg', 'K000383']];"
        "</script></html>"
    )
    monkeypatch.setattr(candidates, "_fetch_text", lambda *a, **kw: page)
    assert candidates._congress_tickers() == {"AMAT"}


def test_congress_tickers_empty_when_the_fetch_fails(monkeypatch):
    monkeypatch.setattr(candidates, "_fetch_text", lambda *a, **kw: None)
    assert candidates._congress_tickers() == set()


def test_congress_tickers_empty_when_the_table_is_missing(monkeypatch):
    """A page redesign reads as unavailable, never as a guess."""
    monkeypatch.setattr(candidates, "_fetch_text", lambda *a, **kw: "<html>nothing here</html>")
    assert candidates._congress_tickers() == set()


def test_trending_tickers_reads_yahoo_shape(monkeypatch):
    """The live shape, confirmed 2026-09-15: {"finance": {"result":
    [{"quotes": [{"symbol": "..."}]}]}}."""
    body = '{"finance":{"result":[{"quotes":[{"symbol":"BAC"},{"symbol":"COIN"}]}]}}'
    monkeypatch.setattr(candidates, "_fetch_text", lambda *a, **kw: body)
    assert candidates._trending_tickers() == {"BAC", "COIN"}


def test_trending_tickers_empty_on_an_unexpected_shape(monkeypatch):
    monkeypatch.setattr(candidates, "_fetch_text", lambda *a, **kw: '{"surprise": true}')
    assert candidates._trending_tickers() == set()


def test_text_sources_are_fetched_as_a_browser(monkeypatch):
    """Yahoo answers Python's default User-Agent with 429 every time (2026-09-18:
    95 of 95 fetches in a day, each a minute late on the retry) and the same
    request with a browser's name with 200. QuiverQuant needed the header from
    the start, and the trending fetch went without it for three days."""
    seen: dict[str, dict | None] = {}

    def fake_fetch(url, headers=None):
        seen[url] = headers
        return '{"finance":{"result":[{"quotes":[]}]}}'

    monkeypatch.setattr(candidates, "_fetch_text", fake_fetch)
    candidates._trending_tickers()
    candidates._congress_tickers()

    assert set(seen) == {candidates._TRENDING_URL, candidates._CONGRESS_URL}
    for url, headers in seen.items():
        assert headers and headers.get("User-Agent", "").startswith("Mozilla"), url


def test_the_symbol_directory_marks_etfs_and_names(monkeypatch):
    files = {
        "nasdaqlisted": (
            "Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares\n"
            "INTC|Intel Corporation - Common Stock|Q|N|N|100|N|N\n"
            "ZXZZT|NASDAQ TEST STOCK|G|Y|N|100|N|N\n"
            "File Creation Time: 0929202611:01|||||||\n"
        ),
        "otherlisted": (
            "ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol\n"
            "GLD|SPDR Gold Shares|P|GLD|Y|40|N|GLD\n"
            "File Creation Time: 0929202612:11||||||\n"
        ),
    }
    monkeypatch.setattr(candidates, "_listed_cache", None)
    monkeypatch.setattr(
        candidates, "_fetch_text", lambda url, **kw: next(v for k, v in files.items() if k in url)
    )
    assert _real_listed() == {
        "INTC": ("Intel Corporation - Common Stock", False),
        "GLD": ("SPDR Gold Shares", True),
    }


def test_a_text_source_value_that_is_not_a_symbol_is_never_sent_to_the_vendor(screened, monkeypatch):
    """QuiverQuant's table carries foreign listings, indexes, crypto and the odd
    fund name beside real tickers. Each costs a refused request, and a name
    with a comma in it sank every batch of 100 on 2026-10-01. Only what looks
    like a US symbol is priced; a class letter such as BRK.B is kept."""
    screened()  # the fixture's patches stay in place for the rest of the test
    sent = []
    monkeypatch.setattr(
        candidates, "_congress_tickers",
        lambda: {"NEW1", "GLAS FUNDS, LP", "0700.HK", "^HSI", "BTC-USD", "BRK.B", "TOOLONG"},
    )
    monkeypatch.setattr(
        candidates.quotes, "get_snapshots",
        lambda tickers, **kw: sent.extend(tickers) or [_row("NEW1", price=20.0, volume=5_000_000)],
    )

    found = candidates.fetch_candidates()

    assert sorted(sent) == ["BRK.B", "NEW1"]
    assert [c.ticker for c in found] == ["NEW1"]


def test_the_screen_is_recorded_before_the_watchlist_is_taken_out(screened, monkeypatch):
    """Experiment 2's random books draw from what the screens offered, and two
    books with different watchlists must draw from one list."""
    recorded = []
    monkeypatch.setattr(candidates.db, "record_candidate_screen", recorded.extend)
    assert screened(active=[_row("ZBH", price=97.0), _row("AAA")], tracked=["ZBH"])
    assert {row["ticker"] for row in recorded} == {"ZBH", "AAA"}
