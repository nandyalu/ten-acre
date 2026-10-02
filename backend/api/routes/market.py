"""The market container's answers to the books of experiment 2.

Mounted only with ``MARKET_MODE=1``. ``backend/services/market_feed.py`` has
the reason and the client side. Each answer is ``{"value": ...}``, so that a
book can tell "the market container found nothing" (``null``) from "the
market container did not answer" (an error).

Each answer is kept for a short time, so books that ask at nearly the same
moment get the same answer. The times are short because the reason is the
same input for every book, not fewer vendor calls.
"""
import datetime

from fastapi import APIRouter

from backend.services import bars, candidates, corporate_actions, intraday, market_feed, positions

router = APIRouter(prefix="/api/market", tags=["market"])

# Seconds each answer is kept. Books on one schedule ask within seconds of
# each other. A screen is kept longest: the books must draw from one list.
_QUOTE_TTL = 30
_BARS_TTL = 60
_SCREEN_TTL = 300
_ACTIONS_TTL = 3600


@router.get("/quote")
def quote(ticker: str):
    ticker = ticker.upper()
    return {"value": market_feed.serve(("quote", ticker), _QUOTE_TTL, lambda: positions.get_current_price(ticker))}


@router.get("/daily")
def daily(ticker: str, start: datetime.date, today: datetime.date):
    """Bars as the source gave them, with the source's name. Webull's are raw
    and Yahoo's are adjusted, and each book applies its own splits."""
    ticker = ticker.upper()

    def fetch():
        found = bars.fetch_raw(ticker, start, today)
        return None if found is None else {"source": found[0], "rows": found[1]}

    return {"value": market_feed.serve(("daily", ticker, start, today), _BARS_TTL, fetch)}


@router.get("/minutes")
def minutes(ticker: str, count: int, end_time: datetime.datetime | None = None):
    ticker = ticker.upper()
    return {"value": market_feed.serve(
        ("minutes", ticker, count, end_time), _BARS_TTL,
        lambda: intraday.fetch_bars(ticker, count=count, end_time=end_time),
    )}


@router.get("/screen")
def screen():
    return {"value": market_feed.serve(("screen",), _SCREEN_TTL, candidates.screen)}


@router.get("/corporate_actions")
def actions(tickers: str, start: datetime.date, end: datetime.date):
    names = tuple(sorted({t.strip().upper() for t in tickers.split(",") if t.strip()}))
    return {"value": market_feed.serve(
        ("corporate_actions", names, start, end), _ACTIONS_TTL,
        lambda: corporate_actions.fetch(names, start, end),
    )}
