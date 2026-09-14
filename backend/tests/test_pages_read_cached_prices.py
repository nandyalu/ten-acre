"""Pages read the price cache; the agent asks the vendor.

The home page was taking 32 seconds to answer `/api/agent`, because building
the book fetches a live price per ticker and Webull's market-data endpoint is
paced at three seconds a call — a pace added on 2026-09-09 after it refused
524 calls in one day. Ten tracked tickers is half a minute, serialised.

**The split is the whole point of this file.** A page can show a price ten
minutes old; the agent cannot trade on one. If a future change routes a
decision through the cache, the agent starts placing orders at prices that no
longer exist, and nothing about that failure is loud.
"""
import pathlib
import re

import pytest

from backend.services import positions

# Modules where a price feeds a decision. Every one must ask the vendor.
DECIDES = [
    "backend/services/agent.py",
    "backend/services/analysis.py",
]

# Modules that only draw something for a reader.
DRAWS = [
    "backend/api/routes/agent.py",
    "backend/services/agent_performance.py",
    "backend/services/agent_book.py",
]


def _source(path: str) -> str:
    return pathlib.Path(path).read_text()


@pytest.mark.parametrize("path", DECIDES)
def test_a_decision_still_asks_the_vendor(path):
    """The agent trades on these numbers. A cached one is an order at a price
    that has moved."""
    source = _source(path)

    assert "get_current_price" in source, (
        f"{path} no longer fetches a live price. If a decision now reads the "
        "cache, the agent can place an order at a price that no longer exists."
    )
    assert "get_shown_price" not in source, (
        f"{path} reads the price cache. That function is for pages — see its "
        "docstring. A decision has to ask the vendor."
    )


@pytest.mark.parametrize("path", DRAWS)
def test_a_page_reads_the_cache(path):
    """Twelve seconds of vendor pacing for a figure a reader glances at."""
    source = _source(path)

    assert "get_shown_price" in source
    assert not re.search(r"\bget_current_price\b", source), (
        f"{path} fetches a live price. This is a read path — the home page "
        "spent 32 seconds on exactly this."
    )


def test_the_cached_lookup_never_fetches(monkeypatch):
    """It must not fall back to the vendor when the cache is empty. A miss
    that quietly fetches would put the pacing straight back."""
    monkeypatch.setattr(positions.db, "get_cached_price", lambda ticker: None)

    def explode(*a, **k):
        raise AssertionError("get_shown_price reached the vendor")

    monkeypatch.setattr("backend.services.quotes.get_realtime_price", explode)

    assert positions.get_shown_price("AAA") is None


def test_a_cached_price_comes_back(monkeypatch):
    class Row:
        price = 101.19

    monkeypatch.setattr(positions.db, "get_cached_price", lambda ticker: Row())

    assert positions.get_shown_price("INTC") == 101.19
