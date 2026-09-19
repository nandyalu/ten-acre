"""Order placement must be impossible outside the simulated environment.

This is the test that stands between the app and someone's real money. Every
public entry point in sandbox_broker is checked, because a guard on
place_market_order alone would still let a caller resolve a production account
id and hand it to something else.
"""
import pytest

from backend.services import sandbox_broker


@pytest.fixture(autouse=True)
def clear_account_cache():
    sandbox_broker._account_id = None
    yield
    sandbox_broker._account_id = None


@pytest.fixture
def production(monkeypatch):
    monkeypatch.setenv("WEBULL_SANDBOX", "0")


@pytest.fixture
def sandbox(monkeypatch):
    monkeypatch.setenv("WEBULL_SANDBOX", "1")
    # backend/main.py loads the developer's .env, and one naming the margin
    # account (WEBULL_ACCOUNT_CLASS) made the cash-account tests below look
    # for the wrong class. A test states the class it needs, or gets the
    # default.
    monkeypatch.delenv("WEBULL_ACCOUNT_CLASS", raising=False)


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(lambda: sandbox_broker.place_market_order("AAPL", "BUY", 1), id="place"),
        pytest.param(lambda: sandbox_broker.get_paper_account_id(), id="resolve_account"),
        pytest.param(lambda: sandbox_broker.get_balance(), id="balance"),
        pytest.param(lambda: sandbox_broker.get_positions(), id="positions"),
        pytest.param(lambda: sandbox_broker.get_order_detail("abc"), id="order_detail"),
    ],
)
def test_nothing_works_against_production_credentials(production, call):
    with pytest.raises(sandbox_broker.NotSandboxError):
        call()


def test_the_guard_is_checked_per_call_not_at_import(monkeypatch, sandbox):
    """Flipping the environment mid-process must disarm an already-imported
    module. A module-level check would leave it armed."""
    monkeypatch.setattr(sandbox_broker.quotes, "get_api_client", lambda: None)
    with pytest.raises(RuntimeError):
        sandbox_broker.place_market_order("AAPL", "BUY", 1)  # no client, but past the guard

    monkeypatch.setenv("WEBULL_SANDBOX", "0")
    with pytest.raises(sandbox_broker.NotSandboxError):
        sandbox_broker.place_market_order("AAPL", "BUY", 1)


def test_a_non_simulated_account_number_is_refused(sandbox, monkeypatch):
    """Belt to the flag's braces: an INDIVIDUAL_CASH account that isn't
    DE-numbered means the host isn't the sandbox, whatever the flag says."""
    monkeypatch.setattr(
        sandbox_broker, "_rows",
        lambda _: [{
            "account_id": "NOTASANDBOXACCOUNTID000000",
            # Any number without the DE prefix stands for a production one. The
            # real account's number used to sit here, and this repo is public.
            "account_number": "9XX00000",
            "account_class": "INDIVIDUAL_CASH",
        }],
    )
    monkeypatch.setattr(sandbox_broker.quotes, "get_api_client", lambda: object())
    monkeypatch.setitem(
        __import__("sys").modules,
        "webull.trade.trade.v2.account_info_v2",
        type("m", (), {"AccountV2": lambda _c: type("a", (), {"get_account_list": lambda s: []})()}),
    )

    assert sandbox_broker.get_paper_account_id() is None


# The sandbox host issues both prefixes, and which one a given account class
# gets is not stable across a paper reset. Requiring DEM specifically is what
# stopped the agent on 2026-09-03, so both are pinned here.
@pytest.mark.parametrize("number", ["DEM272X7", "DEL546C9"])
def test_a_simulated_account_resolves(sandbox, monkeypatch, number):
    # Naming the account is required since 2026-09-09 — an unset
    # WEBULL_ACCOUNT_ID stops order flow, so that a second container cannot
    # silently resolve the same book. See test_account_is_named_not_inferred.py.
    monkeypatch.setenv("WEBULL_ACCOUNT_ID", number)
    monkeypatch.setattr(
        sandbox_broker, "_rows",
        lambda _: [
            {"account_id": "crypto", "account_number": "DEL744J6", "account_class": "CRYPTO"},
            {"account_id": "paper", "account_number": number, "account_class": "INDIVIDUAL_CASH"},
        ],
    )
    monkeypatch.setattr(sandbox_broker.quotes, "get_api_client", lambda: object())
    monkeypatch.setitem(
        __import__("sys").modules,
        "webull.trade.trade.v2.account_info_v2",
        type("m", (), {"AccountV2": lambda _c: type("a", (), {"get_account_list": lambda s: []})()}),
    )

    assert sandbox_broker.get_paper_account_id() == "paper"


@pytest.mark.parametrize("side,quantity", [("HOLD", 1), ("BUY", 0), ("SELL", -3)])
def test_invalid_orders_are_rejected_before_any_network_call(sandbox, side, quantity):
    with pytest.raises(ValueError):
        sandbox_broker.place_market_order("AAPL", side, quantity)
