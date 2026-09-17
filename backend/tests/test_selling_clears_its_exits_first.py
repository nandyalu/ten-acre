"""A sell must clear its own resting exits before it goes to the broker.

**The broker refuses a sell while exits rest on the position.** A bracketed buy
leaves a stop and a target, each for the full quantity, so four shares held
already carry eight shares of sells. A third sell reads as going short and
returns OPENAPI_ORDER_NOT_SUPPORT_REVERSE_OPTION.

Until 2026-09-05 the cancel ran after the sell, so it never ran: the sell
raised, and the loop skipped past the cancel. A bracketed position could only
close through its own stop or target, and the agent could not choose to leave
one. It asked to sell MARA on 2026-09-04 and could not.
"""
import pytest

from backend.services import agent


@pytest.fixture
def broker(monkeypatch):
    """Records the order the app does things in."""
    steps = []

    monkeypatch.setattr(
        agent, "_cancel_resting_exits",
        lambda ticker: steps.append(("cancel", ticker))
        or [{"kind": "stop", "price": 9.68, "quantity": 4, "client_order_id": "stop-x"},
            {"kind": "target", "price": 12.37, "quantity": 4, "client_order_id": "target-x"}],
    )
    monkeypatch.setattr(
        agent.sandbox_broker, "place_market_order",
        lambda ticker, side, qty: steps.append(("sell", ticker, side, qty))
        or {"client_order_id": "x", "placed_at": None},
    )
    monkeypatch.setattr(
        agent.sandbox_broker, "place_exit_bracket",
        lambda ticker, qty, stop, target: steps.append(("restore", ticker, stop, target)) or [],
    )
    # The cancels report cancelled on the first check, so _await_cancels
    # returns at once and no test here pays _CANCEL_WAIT_SECONDS.
    monkeypatch.setattr(agent.sandbox_broker, "get_order_detail", lambda _id: {"status": "CANCELLED"})
    return steps


def _sell():
    return {"ticker": "MARA", "side": "sell", "quantity": 4}


def test_the_exits_are_cancelled_before_the_sell(broker):
    """The whole defect, in one assertion. The reverse order is what the broker
    refuses, and what shipped."""
    agent._place(_sell(), price=10.0, stops={}, targets={})

    assert [s[0] for s in broker] == ["cancel", "sell"]


def test_the_sell_still_goes_out(broker):
    agent._place(_sell(), price=10.0, stops={}, targets={})

    assert ("sell", "MARA", "SELL", 4) in broker


def test_a_failed_sell_puts_the_exits_back(broker, monkeypatch):
    """Between the cancel and the fill the shares have nothing under them.
    Leaving them bare would replace one defect with a worse one."""
    monkeypatch.setattr(
        agent.sandbox_broker, "place_market_order",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("broker said no")),
    )

    with pytest.raises(RuntimeError):
        agent._place(_sell(), price=10.0, stops={}, targets={})

    assert [s[0] for s in broker] == ["cancel", "restore"]


def test_the_restore_uses_the_levels_that_were_resting(broker, monkeypatch):
    """Not the signal's levels. The agent moves its stops and targets during the
    day — it moved AVGO's twice on 2026-09-04 — so the signal is stale."""
    monkeypatch.setattr(
        agent.sandbox_broker, "place_market_order",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no")),
    )

    with pytest.raises(RuntimeError):
        agent._place(_sell(), price=10.0, stops={"MARA": 1.0}, targets={"MARA": 99.0})

    restore = next(s for s in broker if s[0] == "restore")
    assert restore[2] == 9.68 and restore[3] == 12.37


def test_a_sell_with_nothing_resting_needs_no_restore(broker, monkeypatch):
    """A position bought without a bracket has no exits to put back, and a
    restore would invent an exit nobody chose."""
    monkeypatch.setattr(agent, "_cancel_resting_exits", lambda ticker: [])
    monkeypatch.setattr(
        agent.sandbox_broker, "place_market_order",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no")),
    )

    with pytest.raises(RuntimeError):
        agent._place(_sell(), price=10.0, stops={}, targets={})

    assert [s[0] for s in broker] == []


def test_a_buy_is_untouched_by_any_of_this(broker, monkeypatch):
    """A buy has no position to clear. Cancelling on one would remove the exits
    of a holding the agent is adding to."""
    monkeypatch.setattr(
        agent.sandbox_broker, "place_bracket_order",
        lambda *a, **k: {"client_order_id": "b", "placed_at": None, "exits": []},
    )

    agent._place({"ticker": "MARA", "side": "buy", "quantity": 4}, 10.0, {"MARA": 9.0}, {"MARA": 12.0})

    assert not any(s[0] == "cancel" for s in broker)


# --- arming a fresh exit clears whatever is already resting first -------------
#
# _arm_exits and adjust_exits's arm-new branch used to place a brand-new
# stop+target without checking the broker first. AVGO's bracket was refused a
# second time on unsettled cash, the fallback armed a second stop+target on
# top of the first, and the broker refused every later adjust as a position
# reversal — three times, identically — until an unrelated sell elsewhere in
# the pass cancelled the stale order as a side effect. See JOURNEY.md 2026-09-17.


def test_arming_a_fresh_exit_cancels_whatever_is_already_resting_first(monkeypatch):
    steps = []
    monkeypatch.setattr(agent, "get_current_price", lambda t: 345.0)
    monkeypatch.setattr(
        agent, "_cancel_resting_exits",
        lambda ticker: steps.append(("cancel", ticker))
        or [{"kind": "stop", "price": 9.68, "quantity": 4, "client_order_id": "stale-stop"}],
    )
    monkeypatch.setattr(agent.sandbox_broker, "get_order_detail", lambda _id: {"status": "CANCELLED"})
    monkeypatch.setattr(
        agent.sandbox_broker, "place_exit_bracket",
        lambda ticker, qty, stop, target: steps.append(("arm", ticker, stop, target)) or [],
    )

    agent._arm_exits({"ticker": "AVGO", "quantity": 8, "side": "buy"}, 340.0, 351.0)

    assert steps == [("cancel", "AVGO"), ("arm", "AVGO", 340.0, 351.0)]


def test_arming_on_a_genuinely_new_position_does_not_wait(monkeypatch):
    """Nothing resting means _cancel_resting_exits returns empty and there is
    nothing to await — the common case pays no extra latency."""
    monkeypatch.setattr(agent, "get_current_price", lambda t: 98.41)
    monkeypatch.setattr(agent, "_cancel_resting_exits", lambda ticker: [])
    monkeypatch.setattr(
        agent.sandbox_broker, "get_order_detail",
        lambda _id: pytest.fail("nothing was cancelled — there is nothing to wait for"),
    )
    armed = []
    monkeypatch.setattr(
        agent.sandbox_broker, "place_exit_bracket",
        lambda ticker, qty, stop, target: armed.append((ticker, stop, target)) or [],
    )

    agent._arm_exits({"ticker": "ZBH", "quantity": 3, "side": "buy"}, 95.30, 101.50)

    assert armed == [("ZBH", 95.30, 101.50)]


# --- waiting for the cancel to confirm before selling into it ------------------
#
# cancel_order returns as soon as the broker accepts the request, not once the
# cancel has taken effect. AVGO's sell was refused twice — 2026-09-07 and
# 2026-09-08 — because the sell went out before the cancel had actually landed.


def test_the_sell_waits_for_the_cancel_to_confirm(monkeypatch):
    """The broker is asked more than once if the first check still shows the
    exit resting — this is the whole fix, so it must be exercised directly
    rather than through a fixture that answers CANCELLED immediately."""
    steps = []
    monkeypatch.setattr(
        agent, "_cancel_resting_exits",
        lambda ticker: [{"kind": "stop", "price": 9.68, "quantity": 4, "client_order_id": "stop-x"}],
    )
    monkeypatch.setattr(
        agent.sandbox_broker, "place_market_order",
        lambda ticker, side, qty: steps.append("sell") or {"client_order_id": "x", "placed_at": None},
    )
    monkeypatch.setattr(agent, "_CANCEL_POLL_SECONDS", 0)
    statuses = iter(["SUBMITTED", "SUBMITTED", "CANCELLED"])
    monkeypatch.setattr(
        agent.sandbox_broker, "get_order_detail",
        lambda _id: steps.append("check") or {"status": next(statuses)},
    )

    agent._place(_sell(), price=10.0, stops={}, targets={})

    # Two "not yet" checks, then a "yes", then the sell — in that order.
    assert steps == ["check", "check", "check", "sell"]


def test_a_cancel_that_never_confirms_still_lets_the_sell_go_out(monkeypatch):
    """Giving up on the confirmation is not giving up on the trade. The sell
    still goes out — it may be refused, and the normal restore path covers
    that — the agent's decision to sell is never silently dropped just
    because a confirmation was slow."""
    monkeypatch.setattr(
        agent, "_cancel_resting_exits",
        lambda ticker: [{"kind": "stop", "price": 9.68, "quantity": 4, "client_order_id": "stop-x"}],
    )
    monkeypatch.setattr(agent, "_CANCEL_WAIT_SECONDS", 0)
    monkeypatch.setattr(agent.sandbox_broker, "get_order_detail", lambda _id: {"status": "SUBMITTED"})
    placed = []
    monkeypatch.setattr(
        agent.sandbox_broker, "place_market_order",
        lambda ticker, side, qty: placed.append(ticker) or {"client_order_id": "x", "placed_at": None},
    )

    agent._place(_sell(), price=10.0, stops={}, targets={})

    assert placed == ["MARA"]


def test_a_limit_sell_still_clears_its_exits_first(broker, monkeypatch):
    """The order type asked for doesn't change what the broker refuses — any
    new sell is blocked while something rests on the position."""
    monkeypatch.setattr(
        agent.sandbox_broker, "place_limit_order",
        lambda ticker, side, qty, limit_price, tif: broker.append(
            ("limit_sell", ticker, side, qty, limit_price, tif)
        ) or {"client_order_id": "x", "placed_at": None},
    )
    order = {
        "ticker": "MARA", "side": "sell", "quantity": 4,
        "order_type": "limit", "limit_price": 11.50,
    }

    agent._place(order, price=10.0, stops={}, targets={})

    assert [s[0] for s in broker] == ["cancel", "limit_sell"]
    assert ("limit_sell", "MARA", "SELL", 4, 11.50, "DAY") in broker
    # place_market_order is stubbed on the fixture and must not have been used.
    assert not any(s[0] == "sell" for s in broker)


def test_a_sell_with_nothing_to_cancel_does_not_ask_the_broker_anything(monkeypatch):
    """No resting exit means nothing to wait for — asking would just be a
    wasted round trip on every plain-market-order sell."""
    monkeypatch.setattr(agent, "_cancel_resting_exits", lambda ticker: [])
    monkeypatch.setattr(
        agent.sandbox_broker, "get_order_detail",
        lambda _id: pytest.fail("nothing was cancelled — there is nothing to check"),
    )
    monkeypatch.setattr(
        agent.sandbox_broker, "place_market_order",
        lambda ticker, side, qty: {"client_order_id": "x", "placed_at": None},
    )

    agent._place(_sell(), price=10.0, stops={}, targets={})


# --- the buying power margin ---------------------------------------------------


def test_the_affordable_count_leaves_room_for_the_margin():
    """Webull wants buying power 2% above a market order's estimated cost during
    regular hours. Without that margin the top share of every count was an order
    that could only fail — OPENAPI_DAY_BUYING_POWER_INSUFFICIENT_M_NEW, live on
    2026-09-04."""
    from backend.services import agent_book

    book = agent_book.Book(budget=10_000.0, cash=1_000.0, realized_pnl=0.0, holdings=[])

    class _Signal:
        ticker, decision, signal_date = "AAPL", "Buy", "2026-09-04"
        entry_price = stop_loss = price_target = None
        win_probability = risk_reward = expected_value_r = None
        trigger = None

    prompt = agent.build_prompt(book, [_Signal()], {"AAPL": 100.0})

    # 1000 / 100 is 10; 1000 / 102 is 9.
    assert "| 9 share(s) |" in prompt
