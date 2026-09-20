"""A tool error is a message to the model, not the end of the analysis.

**This exists because of a measured loss.** The 14-way concurrency run on
2026-09-02 discarded two complete forty-minute analyses to a typo. The model
asked for an indicator called ``macd_histogram``; the real name is ``macdh``,
and the error said so, listing every valid name. It went to the logs instead of
to the model that could have acted on it.

Worse, the error counted as vendor ill-health. After three bad names yfinance's
circuit breaker opened, and for the next five minutes *every* indicator call
failed — including the correct ones.
"""
import pytest

from tradingagents.dataflows import interface
from tradingagents.dataflows.errors import (
    BadVendorArgumentError,
    VendorError,
    VendorRateLimitError,
)
from tradingagents.graph.trading_graph import _return_error_to_the_model


@pytest.fixture(autouse=True)
def clean_breaker():
    """Each test starts with every circuit closed — the breaker is module-level
    state and a test that inherited an open circuit would pass for the wrong
    reason."""
    interface._circuit_breaker._failures.clear()
    interface._circuit_breaker._open_since.clear()
    yield
    interface._circuit_breaker._failures.clear()
    interface._circuit_breaker._open_since.clear()


# --- the type ------------------------------------------------------------------


def test_it_is_both_a_vendor_error_and_a_value_error():
    """A VendorError so the router can react to it by behaviour; a ValueError
    so callers that already catch ValueError keep working."""
    e = BadVendorArgumentError("nope", valid=["a", "b"])
    assert isinstance(e, VendorError)
    assert isinstance(e, ValueError)
    assert e.valid == ["a", "b"]


def test_the_valid_list_is_optional():
    assert BadVendorArgumentError("nope").valid == []


# --- what the router does with it ------------------------------------------------


def test_a_bad_indicator_name_raises_with_the_valid_names_in_it():
    with pytest.raises(BadVendorArgumentError) as caught:
        interface.route_to_vendor("get_indicators", "AAPL", "macd_histogram", "2026-09-01", 30)

    assert "macdh" in str(caught.value)
    assert "macdh" in caught.value.valid


def test_a_bad_argument_never_opens_the_circuit():
    """The bug that cost the two analyses. The breaker exists to skip a vendor
    that is *down*, and its own docstring says only transient errors should open
    it. yfinance was healthy the whole time."""
    for _ in range(6):  # twice the threshold
        with pytest.raises(BadVendorArgumentError):
            interface.route_to_vendor("get_indicators", "AAPL", "nonsense", "2026-09-01", 30)

    assert interface._circuit_breaker.is_open("yfinance") is False
    assert interface._circuit_breaker._failures == {}


def test_a_transient_failure_still_opens_the_circuit(monkeypatch):
    """The breaker must keep working for what it is actually for."""
    def always_throttled(*a, **kw):
        raise VendorRateLimitError("slow down")

    monkeypatch.setattr(interface, "_try_vendor", always_throttled)
    monkeypatch.setattr(interface, "_resolve_vendor_chain", lambda *a, **kw: ["yfinance"])

    for _ in range(3):
        # A chain where every vendor is throttled reports the vendors, not the
        # instrument (upstream v0.5.0). That answer is not what this test is
        # about — the breaker's count is — but it must not be an exception.
        out = interface.route_to_vendor("get_indicators", "AAPL", "rsi", "2026-09-01", 30)
        assert out.startswith("DATA_UNAVAILABLE")

    assert interface._circuit_breaker.is_open("yfinance") is True


def test_a_bad_argument_is_not_retried_against_other_vendors(monkeypatch):
    """Every vendor rejects the same invalid argument. Falling through wastes
    requests and buries the message that says what the valid values are."""
    tried = []

    def record(vendor, method, args, kwargs):
        tried.append(vendor)
        raise BadVendorArgumentError("no such thing", valid=["real"])

    monkeypatch.setattr(interface, "_try_vendor", record)
    monkeypatch.setattr(interface, "_resolve_vendor_chain",
                        lambda *a, **kw: ["yfinance", "alpha_vantage", "local"])

    with pytest.raises(BadVendorArgumentError):
        interface.route_to_vendor("get_indicators", "AAPL", "bogus", "2026-09-01", 30)

    assert tried == ["yfinance"]


# --- what the model is handed ----------------------------------------------------


def test_the_model_is_given_the_message():
    text = _return_error_to_the_model(
        BadVendorArgumentError("Indicator macd_histogram is not supported. "
                               "Please choose from: ['macdh', 'rsi']")
    )
    assert "TOOL ERROR" in text
    assert "macdh" in text


def test_it_never_suggests_what_to_do_instead():
    """A model told "that failed, try something else" invents a plausible
    substitute, and an invented answer that reads as data is the exact failure
    that disqualified four models in August. The vendor's message already names
    the valid values; anything beyond it is us guessing."""
    text = _return_error_to_the_model(BadVendorArgumentError("nope"))
    lowered = text.lower()
    for invented in ("try ", "instead", "you could", "consider", "suggest"):
        assert invented not in lowered


def test_every_tool_node_hands_errors_back(monkeypatch):
    """A node that missed the setting kills the analysis on the first raising
    tool, which is the whole failure being fixed here."""
    import inspect

    from tradingagents.graph import trading_graph

    src = inspect.getsource(trading_graph.TradingAgentsGraph._create_tool_nodes)
    assert src.count("ToolNode(") == src.count("handle_tool_errors=")
