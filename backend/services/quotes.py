"""Real-time quote source. When WEBULL_APP_KEY/WEBULL_APP_SECRET are set the
Webull OpenAPI snapshot endpoint is used (point it at the sandbox with
WEBULL_SANDBOX=1); otherwise — or on any per-call failure — callers fall back
to yfinance's delayed quote in backend/services/positions.py. Historical bars everywhere
else stay on yfinance; Webull only upgrades the "price right now" path.

The client is built lazily on first use so the bot runs unchanged with no
keys configured. US_STOCK is tried first and US_ETF second (SPY etc.), with
the working category cached per ticker.
"""
import logging
import os
import re
import threading
import time

log = logging.getLogger("trading-experiment.quotes")

_SANDBOX_ENDPOINT = "api.sandbox.webull.com"

_api_client = None
_api_client_done = False
_market_data = None
_init_done = False
# A process-local memo in front of TickerStatus.webull_category, so a warm
# process costs no query per request and a cold one costs no extra vendor
# request. Before 2026-09-09 this dict was the only copy, and every restart
# threw it away — see category_for below.
_category_cache: dict[str, str] = {}

_PRICE_KEYS = ("price", "last_price", "last", "close", "pre_close")
_LIST_KEYS = ("snapshots", "data", "result", "list")

# --- Pacing the market-data endpoint ---------------------------------------
#
# Webull publishes no rate limit for market data — neither the API index nor
# the Market Data API Overview carries a number — so these are derived from
# what actually failed here on 2026-09-09, when the watchdog fired about
# twenty requests in a six-second burst every fifteen minutes and Webull
# refused 524 of the day's calls. See docs/changelog.md, 2026-09-09.
#
# One pace shared by every market-data caller (quotes here, history bars in
# backend/services/intraday.py), because they share one limit. Order history
# has had its own pause since 2026-08-07 for the same reason; this endpoint
# had nothing.
_PACE_FLOOR_SECONDS = 3.0
_PACE_CEILING_SECONDS = 10.0
# Widen fast, narrow slowly. A burst that trips the limit should back off at
# once; recovering in small steps keeps the next burst from tripping it again
# the moment one call succeeds.
_PACE_WIDEN_SECONDS = 3.5
_PACE_NARROW_SECONDS = 0.5
_MARKET_DATA_ATTEMPTS = 3

_pace_lock = threading.Lock()
_pace_seconds = _PACE_FLOOR_SECONDS
_last_request_at = 0.0


def category_for(ticker: str) -> str | None:
    """The Webull category this ticker last answered on, or None if it has
    never answered — in which case the caller probes US_STOCK then US_ETF.

    Reads the process memo first and the database behind it, so learning
    survives a restart. The import is local because this module is imported
    by paths that must work with no database configured at all.
    """
    if ticker in _category_cache:
        return _category_cache[ticker]
    from backend.database import db

    # Same reasoning as the write below: an unreadable row costs the probe
    # request it was meant to save, never the quote itself.
    try:
        stored = db.get_webull_category(ticker)
    except Exception:
        log.warning("Could not read the Webull category for %s", ticker, exc_info=True)
        return None
    if stored:
        _category_cache[ticker] = stored
    return stored


def remember_category(ticker: str, category: str) -> None:
    """Record the category a ticker answered on, in memory and on disk.

    Writes only on a change, so the ordinary case — every request for an
    already-known ticker — costs nothing.
    """
    if _category_cache.get(ticker) == category:
        return
    _category_cache[ticker] = category
    from backend.database import db

    # The memo is updated first and the write can fail without taking the
    # quote with it. This line used to be a dict assignment that could not
    # fail; now it reaches a database, and a caller asking for a price should
    # not lose it because the app could not write down a detail about how it
    # was fetched. Failing here costs the persistence and nothing else — the
    # process still remembers, exactly as it did before 2026-09-09.
    try:
        db.set_webull_category(ticker, category)
    except Exception:
        log.warning("Could not store the Webull category for %s", ticker, exc_info=True)


def rate_limited(exc: Exception) -> bool:
    """Whether Webull refused this request for being too busy, rather than for
    any reason the caller could fix by asking differently."""
    text = str(exc).lower()
    return "429" in text or "too_many_requests" in text or "too many requests" in text


def _claim_a_slot() -> None:
    """Wait until the shared gap since the last market-data request has passed,
    then take the next slot.

    Blocking is safe: every route in this app is a plain ``def`` and runs in
    the threadpool, the decision pass is wrapped in ``asyncio.to_thread``, and
    the dashboard reads the price cache rather than fetching live. Nothing that
    sleeps here sits on the event loop.
    """
    global _last_request_at
    while True:
        with _pace_lock:
            now = time.monotonic()
            ready_at = _last_request_at + _pace_seconds
            if now >= ready_at:
                _last_request_at = now
                return
            wait = ready_at - now
        time.sleep(wait)


def _widen_pace() -> None:
    global _pace_seconds
    with _pace_lock:
        _pace_seconds = min(_PACE_CEILING_SECONDS, _pace_seconds + _PACE_WIDEN_SECONDS)


def _narrow_pace() -> None:
    global _pace_seconds
    with _pace_lock:
        _pace_seconds = max(_PACE_FLOOR_SECONDS, _pace_seconds - _PACE_NARROW_SECONDS)


def market_data_request(call, what: str):
    """Run one Webull market-data request under the shared pace, retrying while
    Webull says it is too busy.

    Raises the vendor's own exception once ``_MARKET_DATA_ATTEMPTS`` are spent,
    which is what sends the caller to yfinance. Anything that is not a rate
    limit is raised immediately — a wrong argument does not improve on a
    second attempt, and retrying one only spends the quota this exists to
    protect.
    """
    for attempt in range(1, _MARKET_DATA_ATTEMPTS + 1):
        _claim_a_slot()
        try:
            response = call()
        except Exception as exc:
            if not rate_limited(exc):
                raise
            _widen_pace()
            if attempt == _MARKET_DATA_ATTEMPTS:
                log.warning(
                    "Webull is rate limiting %s — gave up after %d attempts, now pacing at %.1fs",
                    what, attempt, _pace_seconds,
                )
                raise
            log.info(
                "Webull rate limited %s (attempt %d of %d) — pacing at %.1fs",
                what, attempt, _MARKET_DATA_ATTEMPTS, _pace_seconds,
            )
            continue
        _narrow_pace()
        return response


def is_sandbox() -> bool:
    """Whether the client talks to Webull's simulated environment.

    Read from the environment rather than remembered from client construction
    so it answers the same before the client is built, and so a caller that
    needs to refuse an action in sandbox never depends on
    the client having been initialized first."""
    return os.environ.get("WEBULL_SANDBOX", "").lower() in ("1", "true", "yes")


def get_api_client():
    """Shared, token-initialized Webull ApiClient;
    None when keys aren't configured or initialization failed."""
    global _api_client, _api_client_done
    if _api_client_done:
        return _api_client
    _api_client_done = True
    app_key = os.environ.get("WEBULL_APP_KEY")
    app_secret = os.environ.get("WEBULL_APP_SECRET")
    if not app_key or not app_secret:
        log.info("Webull keys not configured — real-time quotes fall back to yfinance")
        return None
    try:
        from webull.core.client import ApiClient
        from webull.core.http.initializer.client_initializer import ClientInitializer

        # The SDK's token manager logs access-token values at INFO — keep
        # credential material out of routine logs.
        logging.getLogger("webull").setLevel(logging.WARNING)

        api_client = ApiClient(app_key, app_secret, "us")
        if is_sandbox():
            api_client.add_endpoint("us", _SANDBOX_ENDPOINT)
            log.info("Webull client enabled (sandbox endpoint)")
        else:
            log.info("Webull client enabled (production endpoint)")
        # Exchanges app key/secret for the x-access-token production requires
        # (asks the endpoint first — sandbox reports tokens disabled and skips).
        # The SDK's DataClient/TradeClient would do this too, but they also
        # force-install a log file in cwd, so initialize directly.
        ClientInitializer.initializer(api_client)
        _api_client = api_client
    except Exception:
        log.exception("Webull client init failed — falling back to yfinance")
        _api_client = None
    return _api_client


def _get_market_data():
    global _market_data, _init_done
    if _init_done:
        return _market_data
    _init_done = True
    api_client = get_api_client()
    if api_client is None:
        return None
    from webull.data.quotes.market_data import MarketData

    _market_data = MarketData(api_client)
    return _market_data


def payload_rows(payload) -> list[dict]:
    """Unwrap a Webull response to its list of row dicts, tolerating the
    shapes used across endpoints: a bare list, a dict wrapping the list under
    one of ``_LIST_KEYS``, or a single dict standing in for a one-row list."""
    if isinstance(payload, dict):
        for key in _LIST_KEYS:
            if isinstance(payload.get(key), list):
                payload = payload[key]
                break
        else:
            payload = [payload]
    if not isinstance(payload, list):
        return []
    return [row for row in payload if isinstance(row, dict)]


def extract_price(payload) -> float | None:
    """Pull a usable price out of a snapshot response; prices may arrive as
    strings."""
    rows = payload_rows(payload)
    if not rows:
        return None
    first = rows[0]
    for key in _PRICE_KEYS:
        value = first.get(key)
        if value in (None, ""):
            continue
        try:
            price = float(value)
        except (TypeError, ValueError):
            continue
        if price > 0:
            return price
    return None


def get_realtime_price(ticker: str) -> float | None:
    """Best-effort Webull snapshot quote; None when unconfigured or failing
    (callers fall back to yfinance)."""
    market_data = _get_market_data()
    if market_data is None:
        return None
    from webull.data.common.category import Category

    known = category_for(ticker)
    categories = [known] if known else [Category.US_STOCK.name, Category.US_ETF.name]
    for category in categories:
        try:
            response = market_data_request(
                lambda: market_data.get_snapshot(ticker, category), f"a quote for {ticker}"
            )
            price = extract_price(response.json())
        except Exception as exc:
            message = str(exc)
            # Any 401 flavor (bad creds, dead token, missing market-data
            # subscription) won't fix itself this session — disable instead
            # of two failing calls per quote until the next restart.
            if "unauthorized" in message.lower() or "invalid_token" in message.lower():
                global _market_data
                _market_data = None
                if "subscribe" in message.lower():
                    log.error(
                        "Webull says the account lacks a market-data subscription — "
                        "subscribe to stock quotes in the OpenAPI console, then restart. "
                        "Falling back to yfinance."
                    )
                else:
                    log.error("Webull rejected the credentials — disabling Webull quotes until restart")
                return None
            log.warning("Webull snapshot failed for %s/%s: %s", ticker, category, exc)
            # A rate limit stops the whole attempt; any other error still tries
            # the next category. "Too busy" says nothing about whether this
            # ticker is a stock or an ETF, and before 2026-09-09 treating it as
            # if it did meant a rate-limited ticker cost two requests instead
            # of one — the failure doubling the traffic that caused it. Other
            # errors keep falling through, because a wrong category can surface
            # as one.
            if rate_limited(exc):
                return None
            continue
        if price is not None:
            remember_category(ticker, category)
            return price
    return None


_INVALID_SYMBOL_RE = re.compile(r"INVALID_SYMBOL.*?\[([^\]]+)\]")


def _rejected_symbols(exc: Exception) -> set[str]:
    """The symbols Webull named as not existing in the category, if that is
    what this exception says — parsed from the vendor's own message text,
    e.g. "...does not exist in the category. [ELN, EA, LBRDK]." The SDK
    returns no typed field for this, only the message (see webull.md)."""
    match = _INVALID_SYMBOL_RE.search(str(exc))
    if not match:
        return set()
    return {s.strip() for s in match.group(1).split(",") if s.strip()}


def get_snapshots(tickers: list[str], category: str = "US_STOCK") -> list[dict]:
    """Batched snapshot for a list of tickers, one vendor request for up to
    100 symbols. Returns rows shaped like the screener's — same
    symbol/price/volume/change_ratio keys (confirmed against Webull's own
    docs, 2026-09-15) — so a caller can hand them straight to
    ``candidates._to_candidate``.

    **The whole batch is refused, not just the bad symbol, when one ticker
    does not exist in the category** — confirmed live 2026-09-15 (`ELN`,
    `EA`, `LBRDK` sank a 100-ticker request with one `INVALID_SYMBOL` error).
    A text source hands over bare tickers with no exchange-listing check
    behind them, so this is routine here, unlike a Webull-screened candidate.
    The names Webull calls out are stripped out and the request retried once
    rather than losing every other ticker in the batch to one bad symbol.

    Unlike ``get_realtime_price`` this does not fall back across categories:
    it exists for candidate discovery, where a symbol that fails one category
    is dropped rather than chased, the same way an invalid screener row is.
    """
    market_data = _get_market_data()
    if market_data is None or not tickers:
        return []
    remaining = list(dict.fromkeys(tickers))
    for _ in range(2):
        if not remaining:
            return []
        try:
            response = market_data_request(
                lambda: market_data.get_snapshot(",".join(remaining), category),
                f"a batch snapshot for {len(remaining)} ticker(s)",
            )
            return payload_rows(response.json())
        except Exception as exc:
            bad = _rejected_symbols(exc)
            if not bad:
                log.warning(
                    "Webull batch snapshot failed for %d ticker(s)", len(remaining), exc_info=True,
                )
                return []
            log.info("Webull rejected %d invalid symbol(s), retrying without them: %s", len(bad), sorted(bad))
            remaining = [t for t in remaining if t not in bad]
    return []
