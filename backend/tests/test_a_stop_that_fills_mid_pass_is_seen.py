"""A stop that fires while a pass is running.

On 2026-09-24 INTC's stop filled eleven seconds after the pass took the lock.
The pass settled once, at the start, and that settle was correct and instantly
stale: every later turn was shown fifteen shares the account no longer held,
and the agent ordered ``adjust INTC stop 119.50`` against an order that had
already paid out. Webull answered ``OPENAPI_ORDER_CANT_NOT_BE_REPLACE — Order
can not be modified``, and the agent spent its next turn guessing at unsettled
cash and broker API limits, because nothing it could see said "already filled".

Two things must hold now: every turn settles before it reads the book, and a
replace refused because the exit is finished says so in those words. See the
2026-09-24 entry in JOURNEY.md.
"""
import types

import pytest

from backend.services import agent
from backend.tests.test_agent import _book


def _exit(client_order_id="stop-1", quantity=15.0, limit_price=120.50):
    return types.SimpleNamespace(
        id=1,
        client_order_id=client_order_id,
        exit_kind="stop",
        quantity=quantity,
        limit_price=limit_price,
    )


def test_every_turn_settles_before_it_reads_the_book(monkeypatch):
    calls = []

    monkeypatch.setattr(agent.quotes, "is_sandbox", lambda: True)
    monkeypatch.setattr(agent.watchdog, "is_us_market_hours", lambda: True)
    monkeypatch.setattr(agent, "is_enabled", lambda: True)
    monkeypatch.setattr(agent, "_recent_signals", lambda: [])
    monkeypatch.setattr(agent.db, "get_recent_signals", lambda limit=200: [])
    monkeypatch.setattr(agent.agent_book, "closed_trades", lambda decisions=None: [])
    monkeypatch.setattr(agent.db, "get_watchlist", lambda: [])
    monkeypatch.setattr(agent, "_price_map", lambda _t: {})
    monkeypatch.setattr(
        agent.agent_book, "build_book",
        lambda price_lookup=None: calls.append("book") or _book(),
    )
    monkeypatch.setattr(agent, "record_exit_fill", lambda fill: calls.append("recorded"))
    monkeypatch.setattr(agent, "_candidate_menu", lambda: None)
    monkeypatch.setattr(
        agent, "adjust_exits",
        lambda *a, **kw: {"ok": True, "message": "INTC: moved stop to $119.50"},
    )

    # The stop fires between turn 0 and turn 1 — the turn boundary the old
    # code never looked across.
    fills = [
        [],
        [{
            "ticker": "INTC", "side": "sell", "quantity": 15.0, "price": 120.44,
            "was_stop": True, "exit_kind": "stop", "status": "filled",
            "client_order_id": "stop-1", "limit_price": 120.50, "reason": "stop",
        }],
    ]
    monkeypatch.setattr(
        agent, "settle_pending",
        lambda: calls.append("settle") or (fills.pop(0) if fills else []),
    )

    # Two turns: the first acts, so the pass asks again, and the second does
    # nothing and ends it.
    answers = [
        ("moving the stop", [{"ticker": "INTC", "side": "adjust", "stop": 119.50}], []),
        ("held", [], []),
    ]
    monkeypatch.setattr(agent, "_decide", lambda *a, **kw: answers.pop(0))

    run = agent.run_once()

    assert calls.count("settle") >= 2, calls
    assert calls[0] == "settle"
    # The fill the pass found itself is still written as an alert, so the next
    # turn's prompt names it, and carried out for the scheduler to announce.
    assert "recorded" in calls
    assert [f["ticker"] for f in run.fills_seen] == ["INTC"]


def test_a_replace_refused_because_the_exit_filled_says_so(monkeypatch):
    monkeypatch.setattr(agent.quotes, "is_sandbox", lambda: True)
    monkeypatch.setattr(agent, "get_current_price", lambda t: 123.03)
    monkeypatch.setattr(agent, "get_atr", lambda t: 2.0)
    monkeypatch.setattr(agent.db, "get_resting_exits", lambda t: [_exit()])

    def refuse(*_a):
        raise RuntimeError(
            "HTTP Status: 417, Code: OPENAPI_ORDER_CANT_NOT_BE_REPLACE, "
            "Msg: Order can not be modified"
        )

    monkeypatch.setattr(agent.sandbox_broker, "replace_exit", refuse)
    monkeypatch.setattr(
        agent.sandbox_broker, "get_order_detail",
        lambda client_order_id: {"status": "FILLED", "filled_price": "120.44"},
    )

    result = agent.adjust_exits("INTC", stop=119.50, target=None)

    assert not result["ok"]
    assert "already filled at $120.44" in result["message"]
    assert "OPENAPI_ORDER_CANT_NOT_BE_REPLACE" not in result["message"]


def test_a_replace_that_fails_for_an_unknown_reason_keeps_the_brokers_words(monkeypatch):
    """The broker's own words stand whenever the order is still live, or
    cannot be read at all. A second failure here must not hide the first."""
    monkeypatch.setattr(agent.quotes, "is_sandbox", lambda: True)
    monkeypatch.setattr(agent, "get_current_price", lambda t: 123.03)
    monkeypatch.setattr(agent, "get_atr", lambda t: 2.0)
    monkeypatch.setattr(agent.db, "get_resting_exits", lambda t: [_exit()])

    def refuse(*_a):
        raise RuntimeError("some other broker failure")

    def unreadable(_client_order_id):
        raise RuntimeError("could not read the order")

    monkeypatch.setattr(agent.sandbox_broker, "replace_exit", refuse)
    monkeypatch.setattr(agent.sandbox_broker, "get_order_detail", unreadable)

    result = agent.adjust_exits("INTC", stop=119.50, target=None)

    assert "some other broker failure" in result["message"]


def test_a_fill_the_pass_settled_itself_is_still_announced(monkeypatch):
    """_settle_agent_fills only ever sees a *pending* order. Once the pass
    settles a fill itself, that watchdog will never find it, so the pass has
    to carry it out. A limit buy is the case that also needs a fresh decision:
    it never brackets, so its shares land with nothing resting under them, and
    a fill on the pass's last turn is one the pass never saw."""
    from backend.tasks import scheduler

    posted, woken = [], []
    stop = {
        "ticker": "INTC", "quantity": 15.0, "price": 120.44, "was_stop": True,
        "status": "filled", "limit_price": 120.50, "exit_kind": "stop", "reason": "stop",
    }
    buy = {
        "ticker": "CRWV", "side": "buy", "quantity": 20.0, "price": 86.83,
        "was_stop": False, "status": "filled", "limit_price": 88.05,
    }
    monkeypatch.setattr(
        scheduler.agent, "run_once",
        lambda woke_because=None, should_stop=None: types.SimpleNamespace(
            next_wakeup=None, fills_seen=[stop, buy], unguarded=[],
            acted=False, rejected=[], failed=[], notes=[], looked_at=None,
        ),
    )
    monkeypatch.setattr(scheduler, "wake_agent_now", lambda label=None: woken.append(label))

    async def posts(*a, **kw):
        posted.append(a[0] if a else kw.get("embed"))

    monkeypatch.setattr(scheduler, "notify", posts)

    scheduler._run_agent_pass_locked("test")

    assert any("INTC" in str(p) for p in posted)
    assert any("CRWV" in str(p) for p in posted)
    assert woken == ["Limit buy filled"]
