"""One source of market data for all the books of experiment 2.

**The reason is experimental control, not efficiency.** Each book is its own
container (PLAN.md). If each book fetches its own quotes, bars and screens, two
books can see two prices for one ticker at one moment, and a difference in
their results can then come from their inputs and not from their decisions.

The same image runs in two roles:

- **The market container** has ``MARKET_MODE=1``. It holds the Webull keys,
  runs no agent and no scheduler, and answers ``/api/market/*``
  (``backend/api/routes/market.py``). It keeps each answer for a short time, so
  books that ask at nearly the same moment get the same answer.
- **A book** has ``MARKET_DATA_URL``. Each fetch entry point asks the market
  container first, through ``ask``. When the market container answers, the book
  uses that answer, also when the answer is "nothing". When it cannot be
  reached, the book fetches its own data, as before.

Each answer is counted in ``marketfetch`` with its source, "market" or "own",
so the report can show the days on which a book's inputs can differ.

Without ``MARKET_DATA_URL``, ``ask`` returns ``MISSING`` and counts nothing.
Experiment 1 runs that way.
"""
import logging
import os
import threading
import time

import requests

log = logging.getLogger("ten-acre.market_feed")

# Seconds. The market container paces its Webull calls, so four books that
# ask for new tickers at the same time can wait in a queue there.
_TIMEOUT = 60

MISSING = object()


def is_server() -> bool:
    """True when this container is the market container."""
    return (os.environ.get("MARKET_MODE") or "").strip().lower() in ("1", "true", "yes", "on")


def _url() -> str:
    return (os.environ.get("MARKET_DATA_URL") or "").strip().rstrip("/")


def _count(kind: str, source: str) -> None:
    from backend.database import db

    try:
        db.count_market_fetch(kind, source)
    except Exception:
        log.warning("Could not count a %s fetch from %s", kind, source, exc_info=True)


def ask(kind: str, **params):
    """The market container's answer for ``kind``, decoded from JSON.

    ``MISSING`` when no ``MARKET_DATA_URL`` is set, or when the market
    container cannot be reached or gives an error. Then the caller fetches its
    own data. ``None`` is an answer, not a failure.
    """
    url = _url()
    # The market container never asks itself: that would wait on its own lock.
    if not url or is_server():
        return MISSING
    try:
        response = requests.get(f"{url}/api/market/{kind}", params=params, timeout=_TIMEOUT)
        response.raise_for_status()
        value = response.json()["value"]
    except Exception as exc:
        log.warning("The market container did not answer a %s fetch (%s); fetching it here", kind, exc)
        _count(kind, "own")
        return MISSING
    _count(kind, "market")
    return value


# --- the market container's side ---------------------------------------------

_memo: dict[tuple, tuple[float, object]] = {}
_locks: dict[tuple, threading.Lock] = {}
_locks_guard = threading.Lock()


def serve(key: tuple, ttl: float, fetch):
    """``fetch()``, or the answer it gave for ``key`` less than ``ttl`` seconds ago.

    One lock for each key: a second book that asks while the first book's
    fetch runs waits for that fetch. It does not start a second vendor call,
    and it gets the same answer.
    """
    with _locks_guard:
        lock = _locks.setdefault(key, threading.Lock())
    with lock:
        now = time.monotonic()
        hit = _memo.get(key)
        if hit is not None and now - hit[0] < ttl:
            return hit[1]
        value = fetch()
        _memo[key] = (now, value)
        # No answer is kept for longer than an hour, so the memo cannot grow
        # without limit.
        for old in [k for k, (at, _) in _memo.items() if now - at > 3600]:
            del _memo[old]
            with _locks_guard:
                _locks.pop(old, None)
        return value
