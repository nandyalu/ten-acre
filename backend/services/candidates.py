"""Candidate tickers to consider following, from Webull's screener.

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

Two more screens hand back bare tickers instead of a priced Webull row:
QuiverQuant's congressional stock-trade table and Yahoo Finance's public
trending-tickers feed. Both are verified against a real Webull snapshot
before they can pass the same price/volume/move floors as everything else —
a ticker pulled from a scraped page is not trusted data until priced.

Blocking (HTTP + DB) — call via asyncio.to_thread.
"""
import ast
import json
import logging
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from backend.database import db
from backend.services import listings, quotes

log = logging.getLogger("trading-experiment.candidates")

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
MAX_PROPOSED = 8


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


def _fetch_text(url: str, headers: dict | None = None) -> str | None:
    """One GET, one retry on a 429, capped read. None on any other failure.

    The retry-once-on-429 shape matches TradingAgents' reddit.py, in case
    either of these free feeds turns out to rate-limit the same way Reddit's
    search feed does from the deployed host. Neither has shown a 429 in
    testing from this box; if one starts, the fix is a source-specific fallback
    (a trawl fetch of the same page, or a slower pace), not a shared one —
    QuiverQuant's page and Yahoo's JSON endpoint have nothing else in common.
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
    body = _fetch_text(_CONGRESS_URL, headers={"User-Agent": "Mozilla/5.0"})
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
    to parse out of text — the cleanest of the sources tried so far."""
    body = _fetch_text(_TRENDING_URL)
    if body is None:
        return set()
    try:
        rows = json.loads(body)["finance"]["result"][0]["quotes"]
    except (ValueError, KeyError, IndexError, TypeError):
        log.warning("Yahoo trending feed came back in an unexpected shape")
        return set()
    return {str(r.get("symbol", "")).strip().upper() for r in rows if r.get("symbol")}


def fetch_candidates() -> list[Candidate]:
    """Screened names not already tracked, most liquid first.

    Two Webull screens, deliberately: the most active gives liquid names that
    are simply busy, and the day's gainers give names that are moving. Two
    text sources add names a price/volume screen cannot see at all — a
    congressional trade, a name suddenly searched for — but they hand back
    bare tickers, not priced rows, so they are verified against a real
    Webull snapshot before they can reach the same filters as everything
    else. Nothing here is a recommendation — it is raw material an analysis
    is spent on, and the analysis is what decides anything.
    """
    client = quotes.get_api_client()
    if client is None:
        log.info("No Webull client — cannot screen for candidates")
        return []
    from webull.data.quotes.screener import Screener

    screener = Screener(client)
    found: dict[str, Candidate] = {}
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
            if ticker not in found and ticker not in wanted:
                wanted[ticker] = source
    if wanted:
        for row in quotes.get_snapshots(list(wanted)[:100]):
            ticker = str(row.get("symbol", "")).strip().upper()
            source = wanted.get(ticker)
            candidate = _to_candidate(row, source) if source else None
            if candidate:
                found[candidate.ticker] = candidate

    # The watchlist covers every holding too: the agent may not untrack a
    # position it still owns, and Python refuses the attempt. So one set is
    # enough here — a held name is a tracked name.
    tracked = {t.upper() for t in db.get_watchlist()}
    inactive = {t.upper() for t in listings.inactive_tickers()}
    fresh = [
        c for c in found.values()
        if c.ticker not in tracked and c.ticker not in inactive
    ]
    fresh.sort(key=lambda c: c.volume, reverse=True)
    return fresh[:MAX_PROPOSED]


