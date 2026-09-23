"""A stop or target that fills is told to the agent, and adjust moves every lot.

Pass 81 on 2026-09-23 started because a stop sold 37 INTC shares, and its
prompt did not say so. A second stop filled inside the cooldown and woke
nothing. INTC was three lots, and adjust had moved the stop on one of them.
See the 2026-09-23 entry in JOURNEY.md.
"""
import asyncio
import datetime
import types

import pytest

from backend.services import agent
from backend.tasks import scheduler

UTC = datetime.timezone.utc


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    monkeypatch.setattr(scheduler.agent, "is_enabled", lambda: True)
    monkeypatch.setattr(scheduler, "wake_agent_now", lambda label=None: False)


def test_a_stop_fill_wakes_the_agent_inside_the_cooldown(monkeypatch):
    ran = []

    async def runs(label):
        ran.append(label)

    monkeypatch.setattr(scheduler, "_run_agent_pass", runs)
    monkeypatch.setattr(scheduler, "_last_agent_run", datetime.datetime.now(UTC))

    asyncio.run(scheduler._maybe_run_agent("Stop fill", cooldown=False))
    asyncio.run(scheduler._maybe_run_agent("Event-driven"))

    assert ran == ["Stop fill"]


def test_a_stop_fill_during_a_pass_does_not_start_a_second_one(monkeypatch):
    ran = []

    async def runs(label):
        ran.append(label)

    monkeypatch.setattr(scheduler, "_run_agent_pass", runs)

    async def drive():
        async with scheduler._pass_lock:
            await scheduler._maybe_run_agent("Stop fill", cooldown=False)

    asyncio.run(drive())

    assert ran == []


def _alert(kind, minutes_ago):
    return types.SimpleNamespace(
        alert_type=kind,
        created_at=datetime.datetime.now(UTC) - datetime.timedelta(minutes=minutes_ago),
    )


def test_a_fill_after_the_last_look_wakes_the_agent_when_the_pass_ends(monkeypatch):
    looked_at = datetime.datetime.now(UTC) - datetime.timedelta(minutes=5)
    monkeypatch.setattr(
        scheduler.agent.db, "get_recent_alerts",
        lambda limit=20: [_alert("big_move", 1), _alert("stop_fill", 2)],
    )
    assert scheduler._fill_after(looked_at)

    # Seen by the pass already: its last prompt came after the fill.
    monkeypatch.setattr(
        scheduler.agent.db, "get_recent_alerts", lambda limit=20: [_alert("stop_fill", 10)]
    )
    assert not scheduler._fill_after(looked_at)
    assert not scheduler._fill_after(None)


def test_the_alerts_window_starts_at_the_last_look(monkeypatch):
    now = datetime.datetime.now(UTC)
    previous = types.SimpleNamespace(
        ran_at=now - datetime.timedelta(minutes=1),
        looked_at=now - datetime.timedelta(minutes=10),
    )
    monkeypatch.setattr(agent.db, "get_agent_runs", lambda limit=1: [previous])
    monkeypatch.setattr(
        agent.db, "get_recent_alerts",
        lambda limit=200: [
            types.SimpleNamespace(created_at=now - datetime.timedelta(minutes=5), message="INTC stop"),
            types.SimpleNamespace(created_at=now - datetime.timedelta(minutes=20), message="old"),
        ],
    )

    assert [a["text"] for a in agent._recent_alerts()] == ["INTC stop"]


def _leg(id, price, quantity, kind="stop"):
    return types.SimpleNamespace(
        id=id, client_order_id=f"c{id}", exit_kind=kind, limit_price=price, quantity=quantity
    )


def test_adjust_moves_the_exit_on_every_lot(monkeypatch):
    replaced, moved = [], []
    monkeypatch.setattr(agent.quotes, "is_sandbox", lambda: True)
    monkeypatch.setattr(agent, "get_current_price", lambda t: 121.0)
    monkeypatch.setattr(
        agent.db, "get_resting_exits",
        lambda t: [_leg(1, 120.0, 37), _leg(2, 94.36, 19), _leg(3, 95.46, 15)],
    )
    monkeypatch.setattr(agent.db, "move_resting_exit", lambda i, p: moved.append((i, p)))
    monkeypatch.setattr(agent.sandbox_broker, "replace_exit", lambda *a: replaced.append(a) or True)

    result = agent.adjust_exits("INTC", 118.0, None)

    assert [r[0] for r in replaced] == ["c1", "c2", "c3"]
    assert moved == [(1, 118.0), (2, 118.0), (3, 118.0)]
    assert result["message"] == "INTC: moved stop on 71 shares from $94.36 and $95.46 and $120.00 to $118.00."


def test_the_holdings_table_shows_each_lot_s_level():
    assert agent._levels_text(None) == "UNSET"
    assert agent._levels_text({120.0: 37}) == "$120.00"
    assert agent._levels_text({95.46: 15, 94.36: 19}) == "$94.36 on 19, $95.46 on 15"


def test_a_refused_bracket_says_why_and_names_the_volatility_stop():
    order = {"ticker": "NVDA", "side": "buy", "quantity": 9}
    result = {
        "client_order_id": "x",
        "levels": (214.02, None),
        "bracket_refused": "CANT_USE_UNSETTLE_FUNDS_FOR_COMBO_ORDER",
    }

    line = agent._describe_fill(order, result, {"NVDA": 225.81}, {}, {})

    assert "CANT_USE_UNSETTLE_FUNDS_FOR_COMBO_ORDER" in line
    assert "stop $214.02" in line
    assert "because no signal in the table gave a stop" in line
    assert "NOTHING" not in line
