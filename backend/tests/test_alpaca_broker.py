"""The Alpaca paper broker: its four guards, and the shapes its callers read.

The guard tests read the source, like ``test_every_order_passes_the_sandbox_guard``,
so a mock cannot satisfy them. The rest replace ``_request`` with a fake
Alpaca that answers from a dict.
"""
import ast
import datetime
import pathlib
import re

import pytest

from backend.services import alpaca_broker, broker

SOURCE = pathlib.Path(alpaca_broker.__file__)
BACKEND = SOURCE.parents[1]
MUTATING = {"POST", "PATCH", "DELETE"}


def _mutating_requests(func) -> bool:
    for call in ast.walk(func):
        if (
            isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "_request"
            and call.args and isinstance(call.args[0], ast.Constant) and call.args[0].value in MUTATING
        ):
            return True
    return False


def _guards(func) -> bool:
    return any(
        isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id == "_assert_sandbox"
        for c in ast.walk(func)
    )


def test_every_function_that_changes_an_order_asserts_paper_first():
    tree = ast.parse(SOURCE.read_text())
    unguarded = [
        f.name for f in ast.walk(tree)
        if isinstance(f, ast.FunctionDef) and _mutating_requests(f) and not _guards(f)
    ]
    assert not unguarded, f"These change orders without _assert_sandbox(): {unguarded}"


def test_only_the_paper_host_is_written_anywhere():
    """A live host in the source is the one edit that would make this real money."""
    for path in BACKEND.rglob("*.py"):
        if "tests" in path.parts:
            continue
        text = path.read_text(errors="ignore")
        assert "://api.alpaca.markets" not in text, path
        if re.search(r"https?://[\w.-]*alpaca\.markets", text):
            assert path == SOURCE, f"{path} reaches Alpaca; only alpaca_broker.py may"


def test_the_guard_refuses_another_host_and_missing_keys(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_API_SECRET", "s")
    alpaca_broker._assert_sandbox()
    monkeypatch.setattr(alpaca_broker, "_HOST", "https://elsewhere.example")
    with pytest.raises(alpaca_broker.NotSandboxError):
        alpaca_broker._assert_sandbox()
    monkeypatch.setattr(alpaca_broker, "_HOST", alpaca_broker.PAPER_HOST)
    monkeypatch.delenv("ALPACA_API_SECRET")
    with pytest.raises(alpaca_broker.NotSandboxError):
        alpaca_broker._assert_sandbox()


ACCOUNT = {
    "id": "acct-1", "account_number": "PA123", "multiplier": "1",
    "shorting_enabled": False, "cash": "1000", "buying_power": "800",
}


@pytest.fixture
def alpaca(monkeypatch):
    """A fake Alpaca. ``state`` holds the account, the orders and every request."""
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_API_SECRET", "s")
    monkeypatch.setenv("ALPACA_ACCOUNT_NUMBER", "PA123")
    monkeypatch.delenv("ALPACA_ACCOUNT_CLASS", raising=False)
    monkeypatch.setattr(alpaca_broker, "_account_id", None)
    state = {"account": dict(ACCOUNT), "orders": {}, "positions": [], "sent": []}

    def request(method, path, **kwargs):
        state["sent"].append((method, path, kwargs.get("json")))
        if path == "/v2/account":
            return state["account"]
        if path == "/v2/positions":
            return state["positions"]
        if path == "/v2/orders:by_client_order_id":
            cid = kwargs["params"]["client_order_id"]
            return next((o for o in state["orders"].values() if o["client_order_id"] == cid), None)
        if path.startswith("/v2/orders/") and method == "GET":
            return state["orders"][path.rsplit("/", 1)[1]]
        if path == "/v2/orders" and method == "POST":
            body = kwargs["json"]
            return {**body, "id": "o1", "status": "accepted", "legs": [
                {"type": "limit", "client_order_id": "leg-t"},
                {"type": "stop", "client_order_id": "leg-s"},
            ] if body.get("order_class") in ("bracket", "oco") else []}
        return {}

    monkeypatch.setattr(alpaca_broker, "_request", request)
    return state


def test_the_account_must_pass_every_guard(alpaca, monkeypatch):
    assert alpaca_broker.get_paper_account_id() == "acct-1"
    for field, value in [
        ("account_number", "123"),            # not a paper number
        ("account_number", "PA999"),          # not the named account
        ("multiplier", "4"),                  # a margin account, and cash was asked for
        ("shorting_enabled", True),           # long-only
    ]:
        alpaca["account"] = {**ACCOUNT, field: value}
        assert alpaca_broker.get_paper_account_id(refresh=True) is None, field
    alpaca["account"] = {**ACCOUNT, "multiplier": "4"}
    monkeypatch.setenv("ALPACA_ACCOUNT_CLASS", "margin")
    assert alpaca_broker.get_paper_account_id(refresh=True) == "acct-1"


def test_an_unnamed_account_stops_order_flow(alpaca, monkeypatch):
    monkeypatch.setenv("ALPACA_ACCOUNT_NUMBER", " ")
    assert alpaca_broker.get_paper_account_id(refresh=True) is None
    with pytest.raises(RuntimeError, match="No simulated account"):
        alpaca_broker.place_market_order("AAPL", "BUY", 1)


def test_a_bracket_is_gtc_and_returns_its_legs(alpaca):
    placed = alpaca_broker.place_bracket_order("AAPL", 2, 100.0, 95.0, 110.0)
    sent = alpaca["sent"][-1][2]
    assert sent["order_class"] == "bracket" and sent["time_in_force"] == "gtc"
    assert sent["limit_price"] == "100.50"  # the marketable-limit buffer
    assert {e["kind"]: e["client_order_id"] for e in placed["exits"]} == {"target": "leg-t", "stop": "leg-s"}
    alpaca_broker.place_bracket_order("AAPL", 2, 100.0, 95.0, None)
    assert alpaca["sent"][-1][2]["order_class"] == "oto"


def test_a_sell_larger_than_the_holding_is_refused_before_the_broker(alpaca):
    alpaca["positions"] = [{"symbol": "AAPL", "qty": "1", "asset_class": "us_equity"}]
    with pytest.raises(ValueError, match="holds 1"):
        alpaca_broker.place_market_order("AAPL", "SELL", 2)
    assert not any(m == "POST" for m, _, _ in alpaca["sent"])


def test_the_ledger_id_follows_a_replace_to_the_order_that_rests_now(alpaca):
    alpaca["orders"] = {
        "a": {"id": "a", "client_order_id": "cid", "status": "replaced", "replaced_by": "b"},
        "b": {"id": "b", "client_order_id": "cid2", "status": "filled",
              "filled_avg_price": "95.1", "filled_qty": "2", "symbol": "AAPL", "side": "sell"},
    }
    detail = alpaca_broker.get_order_detail("cid")
    assert (detail["status"], detail["filled_price"], detail["filled_quantity"]) == ("FILLED", "95.1", "2")
    assert detail["client_order_id"] == "cid"
    alpaca_broker.replace_exit("cid", "stop", 96.0)
    assert alpaca["sent"][-1][:2] == ("PATCH", "/v2/orders/b")


@pytest.mark.parametrize("status, word", [
    ("new", "SUBMITTED"), ("held", "SUBMITTED"), ("partially_filled", "PARTIAL_FILLED"),
    ("canceled", "CANCELLED"), ("expired", "EXPIRED"), ("rejected", "REJECTED"),
])
def test_statuses_arrive_in_the_words_the_callers_test_for(alpaca, status, word):
    alpaca["orders"] = {"a": {"id": "a", "client_order_id": "cid", "status": status}}
    assert alpaca_broker.get_order_detail("cid")["status"] == word


def test_unsettled_cash_is_cash_above_buying_power_on_a_cash_account(alpaca):
    assert alpaca_broker.get_balance()["account_currency_assets"][0]["unsettled_cash"] == 200.0


def test_the_selector_picks_by_name_and_refuses_a_typo(monkeypatch):
    monkeypatch.setenv("BROKER", "alpaca")
    assert broker._impl() is alpaca_broker
    monkeypatch.setenv("BROKER", "webul")
    with pytest.raises(ValueError, match="BROKER"):
        broker.name()
    monkeypatch.delenv("BROKER")
    assert broker.name() == "webull"


def test_a_snapshot_reads_as_a_screener_row(monkeypatch):
    asked = {}
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT04:00:00Z")

    def market_data(path, **params):
        asked.update(params)
        return {"AAA": {
            "latestTrade": {"p": 11.0},
            "dailyBar": {"c": 10.9, "v": 2_500_000, "t": today},
            "prevDailyBar": {"c": 10.0},
        }, "GONE": {  # delisted: Alpaca still answers with its last bar
            "latestTrade": {"p": 50.0},
            "dailyBar": {"c": 50.0, "v": 9_000_000, "t": "2026-08-04T04:00:00Z"},
            "prevDailyBar": {"c": 50.0},
        }}

    monkeypatch.setattr(alpaca_broker, "_market_data", market_data)
    [row] = alpaca_broker.get_snapshots(["AAA", "^GSPC", "BTC-USD", "AAA"])
    # One malformed symbol makes Alpaca refuse the whole batch.
    assert asked["symbols"] == "AAA"
    assert asked["feed"] == "delayed_sip"
    assert row["symbol"] == "AAA" and row["price"] == 11.0 and row["volume"] == 2_500_000
    assert row["change_ratio"] == pytest.approx(0.1)

