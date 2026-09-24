"""Shared fixtures.

The daily bar cache sits between every caller and yfinance, so a test that used
to patch ``yfinance`` now has two seams to control: the network fetch and the
cache table. ``fake_bar_cache`` replaces the table with an in-memory dict so a
test never depends on what happens to be cached in the real database, and never
writes to it.
"""
import datetime

import pytest

from backend.database.models import TickerStatus
from backend.services import bars, listings, quotes


@pytest.fixture(autouse=True)
def isolated_ticker_status(monkeypatch):
    """Keep ticker status in memory for every test.

    Autouse and unconditional, because this state is written from deep inside
    the fetch path — bars.refresh records whether a ticker still trades — so a
    test that never mentions listings can still persist a row. One did: a
    "NOTREAL" symbol got marked inactive in the developer's real database, and
    the next run of that same test saw it, skipped the fetch it was asserting
    on, and failed. A test must not be able to reach the real database at all.
    """
    store: dict[str, TickerStatus] = {}

    def get_status(ticker):
        return store.get(ticker)

    def set_status(ticker, inactive=None, reason=None, last_bar_date=None, manual=None):
        row = store.setdefault(ticker, TickerStatus(ticker=ticker))
        if inactive is not None:
            row.inactive = inactive
            row.reason = reason
        if last_bar_date is not None:
            row.last_bar_date = last_bar_date
        if manual is not None:
            row.manual = manual
        row.checked_at = datetime.datetime.now(datetime.timezone.utc)

    def inactive_rows():
        return [row for row in store.values() if row.inactive]

    # The Webull category lives on the same row and is written from the same
    # kind of depth — inside a market-data call — so it needs the same
    # isolation. Without these two the quote and bar paths reach the real
    # database looking for a column the developer's copy may not have yet.
    def get_category(ticker):
        row = store.get(ticker)
        return row.webull_category if row else None

    def set_category(ticker, category):
        store.setdefault(ticker, TickerStatus(ticker=ticker)).webull_category = category

    monkeypatch.setattr(listings.db, "get_ticker_status", get_status)
    monkeypatch.setattr(listings.db, "set_ticker_status", set_status)
    monkeypatch.setattr(listings.db, "get_inactive_tickers", inactive_rows)
    monkeypatch.setattr(listings.db, "get_webull_category", get_category)
    monkeypatch.setattr(listings.db, "set_webull_category", set_category)
    # quotes.category_for imports db lazily, so it resolves the module rather
    # than a name bound at import time — patching the module attribute above
    # is enough to cover it, but the memo in front of it has to start empty or
    # one test's learned category would leak into the next.
    monkeypatch.setattr(quotes, "_category_cache", {})
    return store


@pytest.fixture(autouse=True)
def isolated_request_count(monkeypatch):
    """Keep the day's model-request count in memory for every test.

    **The suite was writing it to the developer's real database, and enough
    runs in one day made two tests fail.** `llm_throttle` persists the count
    under `llm_requests_today` so it survives a restart, which is right in the
    app and wrong here: the count only ever goes up, so on 2026-09-21 it
    reached 476 of 500 and `_research_and_report` began refusing — correctly —
    to start an analysis that could not finish inside what was left. Two tests
    in `test_the_pass_chains_its_own_results.py` then failed for a reason that
    had nothing to do with either of them, and passing or failing depended on
    how many times the suite had already been run that day.

    Same reasoning as `isolated_ticker_status` above: a test must not be able
    to reach the real database at all. Kept in memory rather than stubbed to
    zero, so a test that means to exercise the counter still can.
    """
    from backend.services import llm_throttle

    # Keyed by bucket as well as day, like the real settings: the decision
    # model keeps its own count since 2026-09-23.
    store: dict[tuple[str | None, str], int] = {}

    monkeypatch.setattr(
        llm_throttle, "_load_day_count",
        lambda day, bucket=None: store.get((bucket, day), 0),
    )
    monkeypatch.setattr(
        llm_throttle, "_save_day_count",
        lambda day, count, bucket=None: store.__setitem__((bucket, day), count),
    )
    return store


@pytest.fixture(autouse=True)
def isolated_research_charges(monkeypatch):
    """Keep research charges in memory for every test.

    **`agent_book.build_book` subtracts every charge in the database from the
    cash**, through `research.total_spent`. The developer's `data/trading.db`
    is a copy of the live book, with $3.45 of real charges, so 19 book and
    performance tests saw $996.55 where they expected $1,000. They failed on
    every checkout that held a copy of a live database, and passed on an
    empty one. Same reasoning as `isolated_ticker_status` above. Kept in
    memory, so a test that records a charge still sees it.
    """
    import itertools
    import types

    from backend.services import research

    rows: list = []
    ids = itertools.count(1)

    def record(ticker, amount_usd, charged_at, note=None):
        row = types.SimpleNamespace(
            id=next(ids), ticker=ticker, amount_usd=amount_usd, charged_at=charged_at, note=note
        )
        rows.append(row)
        return row.id

    monkeypatch.setattr(research.db, "record_research_charge", record)
    monkeypatch.setattr(research.db, "get_research_charges", lambda: list(rows))
    return rows


@pytest.fixture(autouse=True)
def isolated_agent_runs(monkeypatch):
    """Keep the agent's pass records in memory for every test.

    **The prompt reads earlier passes**: the last note, the recent wakeups
    and the broker failures of the last three passes. So a test of the
    prompt read the developer's `data/trading.db`. On 2026-09-24 that copy
    had no `agentrun.looked_at` column yet, and 22 tests failed on a
    migration that had nothing to do with them. On a copy of the live book
    they would pass, with real failures and notes in every prompt. Same
    reasoning as `isolated_ticker_status` above. A test that sets its own
    runs still patches `get_agent_runs` itself, after this.
    """
    import itertools

    from backend.database import db
    from backend.database.models import AgentRun

    rows: list = []
    ids = itertools.count(1)

    def record(*args, **kwargs):
        if args:
            kwargs["ran_at"] = args[0]
        row = AgentRun(id=next(ids), **kwargs)
        rows.append(row)
        return row.id

    def get(limit=None):
        newest = sorted(rows, key=lambda row: row.ran_at, reverse=True)
        return newest if limit is None else newest[:limit]

    monkeypatch.setattr(db, "record_agent_run", record)
    monkeypatch.setattr(db, "get_agent_runs", get)
    return rows


@pytest.fixture
def fake_bar_cache(monkeypatch):
    """In-memory stand-in for the ``dailybar`` table. Yields the backing store
    so a test can seed or inspect it."""
    store: dict[tuple[str, datetime.date], dict] = {}

    def upsert(ticker, rows):
        for row in rows:
            store[(ticker, row["date"])] = row
        return len(rows)

    def coverage(ticker):
        dates = sorted(date for cached_ticker, date in store if cached_ticker == ticker)
        return (dates[0], dates[-1]) if dates else (None, None)

    def read(ticker, start, end=None):
        return [
            type("Row", (), row)
            for (cached_ticker, date), row in sorted(store.items(), key=lambda kv: kv[0][1])
            if cached_ticker == ticker and date >= start and (end is None or date <= end)
        ]

    monkeypatch.setattr(bars.db, "upsert_daily_bars", upsert)
    monkeypatch.setattr(bars.db, "get_bar_coverage", coverage)
    monkeypatch.setattr(bars.db, "get_daily_bars", read)
    # In-process bookkeeping is module state; a leaked entry would make an
    # unrelated test skip a fetch it expects.
    monkeypatch.setattr(bars, "_last_fetch", {})
    monkeypatch.setattr(bars, "_earliest_attempt", {})
    return store


@pytest.fixture(autouse=True)
def never_pace_the_market_data_endpoint(monkeypatch):
    """Skip the real 3-second gap between Webull market-data requests.

    The pace exists because Webull refused 524 of one day's calls (see
    docs/changelog.md, 2026-09-09); it is not something any test needs to sit
    through, and leaving it in added 54 seconds to a 5-second suite. The
    pacing arithmetic itself is covered directly in test_market_data_pacing.py
    with the real function and its own numbers.
    """
    from backend.services import quotes

    monkeypatch.setattr(quotes, "_claim_a_slot", lambda: None)


@pytest.fixture(autouse=True)
def never_reach_the_live_broker(monkeypatch):
    """Fail any test that would send an order to Webull.

    On 2026-08-27 a test run placed real bracket orders on the sandbox account
    for ``AAA`` — the fixture ticker — at a $10.05 limit, which is the $10.00
    fixture price with ``ENTRY_LIMIT_BUFFER_PCT`` applied. They sat unfilled
    until the market closed and Webull cancelled them, which arrived as a
    pile of cancellation notices hours later. Nothing in the app recorded
    them, because the app never placed them: the suite did.

    The account is a sandbox, so the money was never real. What was real is
    that a test reached the internet and put an order on a broker, and the
    only reason anyone noticed was the notifications.

    Patching at ``get_api_client`` covers every path at once — placing,
    cancelling, quotes, positions, order history — so a new test cannot open a
    route this fixture does not know about. A test that genuinely needs broker
    behaviour must patch the specific function it needs, which is what every
    existing one already does.
    """
    def refuse(*args, **kwargs):
        raise AssertionError(
            "This test tried to reach the live Webull API. Tests must never "
            "place, cancel, or read real orders — patch the broker function "
            "the test needs instead. See never_reach_the_live_broker."
        )

    for module in ("backend.services.quotes", "backend.services.broker",
                   "backend.services.sandbox_broker"):
        try:
            monkeypatch.setattr(f"{module}.get_api_client", refuse, raising=False)
        except Exception:
            pass
