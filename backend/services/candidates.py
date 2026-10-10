"""Candidate tickers to consider following, from the broker's screener.

Webull's screener when a Webull client exists. Alpaca's otherwise, on an
Alpaca paper deployment, which has no Webull client at all. Without one of the
two the menu is empty, and the agent has no way to research a new name.

This proposes; it never follows anything on its own. The reason is arithmetic:
one analysis takes about seven minutes of GPU, so an eight-ticker watchlist is
already a fourteen-minute sweep across four cards. A screener that auto-added
forty names would not produce forty opportunities, it would produce a
seventy-minute sweep and a watchlist too diluted to mean anything. The scarce
resource is analysis, not ideas.

**The filter is the entire value.** Raw screener output is dominated by
micro-cap pumps — a live pull returned a stock up 927% in a day — which are the
worst possible thing to put in front of a swing-trading account of a few
thousand dollars. Price and volume floors turn the same feed into names like
INTC, NVDA and SMCI.

Two more screens hand back bare tickers instead of a priced screener row:
QuiverQuant's congressional stock-trade table and Yahoo Finance's public
trending-tickers feed. Both are verified against a real broker snapshot
before they can pass the same price/volume/move floors as everything else —
a ticker pulled from a scraped page is not trusted data until priced.

Blocking (HTTP + DB) — call via asyncio.to_thread.
"""
import ast
import csv
import datetime
import json
import logging
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from backend.database import db
from backend.services import alpaca_broker, listings, market_feed, quotes

log = logging.getLogger("ten-acre.candidates")

# A share price low enough to be a lottery ticket rather than a position, and a
# volume too thin to get out of. Both floors exist to keep the sub-dollar movers
# out; neither is a view on what is worth buying.
MIN_PRICE = 5.0
MIN_VOLUME = 1_000_000

# A day's move this large is a news event or a pump, not a setup — and a price
# floor alone does not catch it, because the pump is what lifted the price over
# the floor. A live pull surfaced PLAG at $5.81, up 927% from about $0.56, which
# passed every other filter. Applied to falls as well as rises: a stock halved
# in a session is equally not a one-to-two-week swing.
MAX_DAILY_MOVE_PCT = 30.0

# How many to ask each screen for, and how many to propose. The screens return
# a couple of hundred; the point is a shortlist somebody will actually read.
_PAGE_SIZE = 50
MAX_PROPOSED = 10

# A global volume sort buried the two text sources every time: a
# congressional trade or a searched-for ticker rarely outranks NVDA. Each
# text source gets a floor of the menu instead, filled by its own most-liquid
# names first; Webull fills whatever the floors leave unused.
_RESERVED_SLOTS = {
    "congress trade (QuiverQuant)": 3,
    "trending (Yahoo Finance)": 2,
}


@dataclass
class Candidate:
    ticker: str
    name: str
    price: float
    volume: float
    change_pct: float | None
    source: str  # which screen surfaced it

    @property
    def volume_m(self) -> float:
        return self.volume / 1_000_000


def _rows(response) -> list[dict]:
    body = response.json() if hasattr(response, "json") else response
    if isinstance(body, dict):
        return [r for r in body.get("data", []) if isinstance(r, dict)]
    return [r for r in body if isinstance(r, dict)] if isinstance(body, list) else []


def _as_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_candidate(row: dict, source: str) -> Candidate | None:
    ticker = str(row.get("symbol", "")).strip().upper()
    price = _as_float(row.get("price") or row.get("close"))
    volume = _as_float(row.get("volume"))
    if not ticker or price is None or volume is None:
        return None
    if price < MIN_PRICE or volume < MIN_VOLUME:
        return None
    change = _as_float(row.get("change_ratio"))
    if change is not None and abs(change * 100) > MAX_DAILY_MOVE_PCT:
        return None
    return Candidate(
        ticker=ticker,
        name=str(row.get("name", ""))[:40],
        price=price,
        volume=volume,
        change_pct=change * 100 if change is not None else None,
        source=source,
    )


# What a US listing's symbol looks like: one to five capital letters or digits,
# with an optional class letter such as BRK.B. QuiverQuant's table also carries
# foreign listings (0700.HK), indexes (^HSI), crypto (BTC-USD) and the odd
# fund name ("GLAS FUNDS, LP"). Each of those costs a refused request before
# Webull names it as invalid, and the fund name, with its comma, sinks a
# whole batch outright; see quotes.get_snapshots. Seen 2026-10-01.
_SYMBOL_RE = re.compile(r"[A-Z0-9]{1,5}(\.[A-Z])?")


def _looks_like_a_symbol(ticker: str) -> bool:
    return bool(_SYMBOL_RE.fullmatch(ticker))


_CONGRESS_URL = "https://www.quiverquant.com/congresstrading/"
# The page's "Recent Trades" table looks JS-rendered (the shipped HTML has an
# empty <tbody>) but the row data is not behind an API call at all — it is
# inlined as a plain JS array literal, ``let recentTradesData = [[...], ...]``,
# server-rendered on every page load. Confirmed live, 2026-09-15: 300 rows, no
# login, no trawl needed — the array's syntax (quoted strings, bare numbers)
# happens to parse as a Python literal too. The regex assumes the statement
# ends at the first literal "];", which held for every row observed; a row
# whose text ever contained that exact substring would truncate the parse, so
# a syntax error here is read as "the page changed" and produces no tickers,
# never a guess (`_fetch_text` failures already collapse to an empty set the
# same way, so no separate handling is needed here).
_CONGRESS_ARRAY_RE = re.compile(r"let recentTradesData = (\[.*?\]);", re.S)
_TRENDING_URL = "https://query1.finance.yahoo.com/v1/finance/trending/US"
# Both text sources are fetched as a browser. Yahoo answers a request that
# carries Python's default User-Agent with 429, every time: on 2026-09-18 it
# refused 95 of 95 fetches in a day, each after the 60-second retry below,
# while the same request with a browser's name got 200 from the same host in
# the same minute. So that 429 is not a rate limit, and a slower pace would
# not have helped. QuiverQuant needed the header from the start.
_BROWSER_HEADERS = {"User-Agent": "Mozilla/5.0"}


def _fetch_text(url: str, headers: dict | None = None) -> str | None:
    """One GET, one retry on a 429, capped read. None on any other failure.

    The retry-once-on-429 shape matches TradingAgents' reddit.py, in case
    either of these free feeds rate-limits the way Reddit's search feed does
    from the deployed host. **A 429 is not always that.** Yahoo's trending
    feed answered 429 to every request without a browser User-Agent from
    2026-09-17 on, and the retry only added a minute to each failure — see
    ``_BROWSER_HEADERS``. Should a real rate limit start, the fix is a
    source-specific fallback (a trawl fetch of the same page, or a slower
    pace), not a shared one — QuiverQuant's page and Yahoo's JSON endpoint
    have nothing else in common.
    """
    req = urllib.request.Request(url, headers=headers or {})
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.read(5 * 1024 * 1024).decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt == 1:
                time.sleep(60)
                continue
            log.warning("Candidate text fetch failed for %s: %s", url, exc)
            return None
        except OSError as exc:
            log.warning("Candidate text fetch failed for %s: %s", url, exc)
            return None
    return None


def _congress_tickers() -> set[str]:
    """Tickers from QuiverQuant's recent congressional-trades table."""
    body = _fetch_text(_CONGRESS_URL, headers=_BROWSER_HEADERS)
    if body is None:
        return set()
    match = _CONGRESS_ARRAY_RE.search(body)
    if not match:
        log.warning("QuiverQuant page came back without the trades table")
        return set()
    try:
        rows = ast.literal_eval(match.group(1))
    except (ValueError, SyntaxError):
        log.warning("QuiverQuant's trades table did not parse as expected")
        return set()
    # Column 0 is the ticker, "-" when the trade has none (e.g. a trust or an
    # instrument the site does not resolve to a symbol).
    return {
        str(row[0]).strip().upper()
        for row in rows
        if isinstance(row, list) and row and row[0] not in (None, "-", "")
    }


def _trending_tickers() -> set[str]:
    """Yahoo Finance's public trending-tickers feed. Plain symbols, nothing
    to parse out of text — the cleanest of the sources tried so far. Fetched
    as a browser, or Yahoo refuses it; see ``_BROWSER_HEADERS``."""
    body = _fetch_text(_TRENDING_URL, headers=_BROWSER_HEADERS)
    if body is None:
        return set()
    try:
        rows = json.loads(body)["finance"]["result"][0]["quotes"]
    except (ValueError, KeyError, IndexError, TypeError):
        log.warning("Yahoo trending feed came back in an unexpected shape")
        return set()
    return {str(r.get("symbol", "")).strip().upper() for r in rows if r.get("symbol")}


# Nasdaq Trader's symbol directory: every US-listed symbol, its name, and a
# Y/N ETF column. Nasdaq writes it once a day. Alpaca has no ETF flag on an
# asset, and a match on the name misses ETFs like GLD ("SPDR Gold Shares").
_SYMBOL_DIRECTORY = (
    "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt",
    "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt",
)
_listed_cache: tuple[datetime.date, dict[str, tuple[str, bool]]] | None = None


def _listed() -> dict[str, tuple[str, bool]]:
    """Each listed ticker's name and whether it is an ETF, read once a day.

    Empty when a file cannot be read. Then no ETF is filtered out, because a
    menu with ETFs is better than no menu. A failed read is not kept, so the
    next screen tries again.
    """
    global _listed_cache
    today = datetime.date.today()
    if _listed_cache and _listed_cache[0] == today:
        return _listed_cache[1]
    listed: dict[str, tuple[str, bool]] = {}
    for url in _SYMBOL_DIRECTORY:
        text = _fetch_text(url, headers=_BROWSER_HEADERS)
        if text is None:
            log.warning("No symbol directory, so ETFs are not filtered from the candidates")
            return {}
        for row in csv.DictReader(text.splitlines(), delimiter="|"):
            ticker = (row.get("Symbol") or row.get("ACT Symbol") or "").strip().upper()
            if ticker and row.get("Test Issue") != "Y" and row.get("ETF") in ("Y", "N"):
                listed[ticker] = ((row.get("Security Name") or "").strip(), row["ETF"] == "Y")
    _listed_cache = (today, listed)
    return listed


def _record_screen(screened: list[Candidate]) -> None:
    """Keep what the screens offered, before the watchlist is taken out, for
    Experiment 2's random control books. A failure here never costs the menu."""
    try:
        db.record_candidate_screen([
            {"ticker": c.ticker, "source": c.source, "price": c.price, "volume": c.volume}
            for c in screened
        ])
    except Exception:
        log.warning("Could not record the candidate screen", exc_info=True)


def screen() -> list[Candidate]:
    """Every name the screens offer now, before this book's own watchlist is
    taken out.

    In experiment 2 each book takes this list from the market container, so
    the books of one moment draw from one list (``market_feed``).

    Two broker screens, deliberately: the most active gives liquid names that
    are simply busy, and the day's gainers give names that are moving. Two
    text sources add names a price/volume screen cannot see at all — a
    congressional trade, a name suddenly searched for — but they hand back
    bare tickers, not priced rows, so they are verified against a real
    broker snapshot before they can reach the same filters as everything
    else. Nothing here is a recommendation — it is raw material an analysis
    is spent on, and the analysis is what decides anything.

    Each text source keeps `_RESERVED_SLOTS` of the menu for itself, most
    liquid of its own names first; the broker screens fill the rest, most
    liquid first.
    """
    answer = market_feed.ask("screen")
    if answer is not market_feed.MISSING:
        return [Candidate(**row) for row in answer or []]
    client = quotes.get_api_client()
    if client is not None:
        from webull.data.quotes.screener import Screener

        screener = Screener(client)
        snapshots = quotes.get_snapshots
        screens = (
            ("most active", lambda: screener.get_most_active("US_STOCK", page_size=_PAGE_SIZE)),
            (
                "day gainers",
                # rank_type/sort_by are enums the SDK does not validate: a wrong
                # value returns zero rows rather than an error, which is how three
                # earlier guesses failed silently.
                lambda: screener.get_gainers_losers(
                    "DAY_1", "US_STOCK", "CHANGE_RATIO", page_size=_PAGE_SIZE, direction="DESC"
                ),
            ),
        )
    elif alpaca_broker.is_paper():
        # Alpaca's screens give symbols only: most-actives has no price and
        # movers has no volume. A snapshot prices both, so every row reaches
        # the same floors as a Webull row. Its most-actives list includes ETFs,
        # which Webull's US_STOCK screen does not; `_listed` removes them below.
        snapshots = alpaca_broker.get_snapshots
        screens = (
            ("most active", lambda: snapshots(alpaca_broker.most_active(_PAGE_SIZE))),
            ("day gainers", lambda: snapshots(alpaca_broker.day_gainers(_PAGE_SIZE))),
        )
    else:
        log.info("No Webull client and no Alpaca paper keys — cannot screen for candidates")
        return []

    found: dict[str, Candidate] = {}
    for source, call in screens:
        try:
            rows = _rows(call())
        except Exception:
            log.exception("Screener call failed for %s", source)
            continue
        for row in rows:
            candidate = _to_candidate(row, source)
            # First screen to surface a name keeps it — most active runs first,
            # so a liquid name is described as liquid rather than as a mover.
            if candidate and candidate.ticker not in found:
                found[candidate.ticker] = candidate

    # Bare tickers from the text sources, first source wins, a Webull screen
    # above always wins over either (it already knows the price and volume;
    # these still need verifying). One batched snapshot prices the lot in a
    # single vendor request rather than one per ticker.
    text_sources = (
        ("congress trade (QuiverQuant)", _congress_tickers),
        ("trending (Yahoo Finance)", _trending_tickers),
    )
    wanted: dict[str, str] = {}
    for source, fetch in text_sources:
        try:
            tickers = fetch()
        except Exception:
            log.exception("Candidate text source failed for %s", source)
            continue
        for ticker in tickers:
            if ticker not in found and ticker not in wanted and _looks_like_a_symbol(ticker):
                wanted[ticker] = source
    if wanted:
        for row in snapshots(list(wanted)[:100]):
            ticker = str(row.get("symbol", "")).strip().upper()
            source = wanted.get(ticker)
            candidate = _to_candidate(row, source) if source else None
            if candidate:
                found[candidate.ticker] = candidate
    return list(found.values())


def fetch_candidates() -> list[Candidate]:
    """Screened names not already tracked, most liquid first, with
    ``_RESERVED_SLOTS`` kept for each text source. See ``screen``."""
    screened = screen()
    # The watchlist covers every holding too: the agent may not untrack a
    # position it still owns, and Python refuses the attempt. So one set is
    # enough here — a held name is a tracked name.
    tracked = {t.upper() for t in db.get_watchlist()}
    inactive = {t.upper() for t in listings.inactive_tickers()}
    # An ETF has no earnings or filings for the analysts to read, and a
    # leveraged one decays over a one-to-two-week hold.
    listed = _listed()
    _record_screen([
        c for c in screened
        if c.ticker not in inactive and not listed.get(c.ticker, ("", False))[1]
    ])
    fresh = [
        c for c in screened
        if c.ticker not in tracked and c.ticker not in inactive
        and not listed.get(c.ticker, ("", False))[1]
    ]

    picked: list[Candidate] = []
    for source, cap in _RESERVED_SLOTS.items():
        bucket = sorted(
            (c for c in fresh if c.source == source), key=lambda c: c.volume, reverse=True
        )
        picked.extend(bucket[:cap])

    webull = sorted(
        (c for c in fresh if c.source not in _RESERVED_SLOTS), key=lambda c: c.volume, reverse=True
    )
    picked.extend(webull[: MAX_PROPOSED - len(picked)])
    # An Alpaca snapshot has no company name.
    for c in picked:
        c.name = c.name or listed.get(c.ticker, ("", False))[0][:40]
    return picked


