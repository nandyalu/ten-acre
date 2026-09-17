import json
import pytest
from backend.database import db
from backend.services import agent, agent_book


@pytest.fixture(autouse=True)
def clean_memory_notes():
    db.set_setting(agent._MEMORY_NOTES_KEY, "")
    yield
    db.set_setting(agent._MEMORY_NOTES_KEY, "")


def test_memory_notes_get_and_set():
    assert agent.get_memory_notes() == []

    agent.set_memory_notes(["Note 1", "  Note 2  ", ""])
    assert agent.get_memory_notes() == ["Note 1", "Note 2"]


def test_memory_notes_add_and_remove():
    agent.add_memory_note("Focus on high R:R setups")
    agent.add_memory_note("Focus on high R:R setups")  # duplicate ignored
    agent.add_memory_note("Avoid trading tech earnings day of report")

    notes = agent.get_memory_notes()
    assert len(notes) == 2
    assert "Focus on high R:R setups" in notes

    # Remove by text
    removed = agent.remove_memory_note("Focus on high R:R setups")
    assert removed is True
    assert agent.get_memory_notes() == ["Avoid trading tech earnings day of report"]

    # Remove by index
    removed_idx = agent.remove_memory_note(0)
    assert removed_idx is True
    assert agent.get_memory_notes() == []


def test_describe_memory_notes():
    assert agent.describe_memory_notes() == []

    agent.add_memory_note("Watch VIX above 25")
    lines = agent.describe_memory_notes()
    assert any("Your persistent memory notes across passes" in l for l in lines)
    assert any("- Watch VIX above 25" in l for l in lines)


def test_parse_decision_memory_order():
    raw = json.dumps({
        "reasoning": "Adding a long-term memory rule.",
        "orders": [
            {"side": "memory", "reason": "Always trail stops on winning trades"}
        ]
    })
    reasoning, orders = agent.parse_decision(raw)
    assert reasoning == "Adding a long-term memory rule."
    assert len(orders) == 1
    assert orders[0]["side"] == "memory"
    assert orders[0]["reason"] == "Always trail stops on winning trades"


def test_screen_and_execute_memory_orders():
    book = agent_book.Book(budget=1000.0, cash=1000.0, realized_pnl=0.0)
    prices = {}
    proposed = [
        {"side": "memory", "reason": "Hold AAPL swing target $250"}
    ]

    accepted, rejected = agent.screen(proposed, book, prices)
    assert len(accepted) == 1
    assert len(rejected) == 0

    run = agent.AgentRun()
    outcomes = agent._execute_orders(accepted, run, prices, {}, {}, {})
    assert any("recorded" in o for o in outcomes)
    assert agent.get_memory_notes() == ["Hold AAPL swing target $250"]

    # Clear memory notes via order
    clear_order = [{"side": "memory", "action": "clear"}]
    accepted, _ = agent.screen(clear_order, book, prices)
    outcomes = agent._execute_orders(accepted, run, prices, {}, {}, {})
    assert any("cleared" in o for o in outcomes)
    assert agent.get_memory_notes() == []


def test_build_prompt_includes_memory_notes():
    book = agent_book.Book(budget=1000.0, cash=1000.0, realized_pnl=0.0)
    agent.add_memory_note("Keep cash buffer at 10%")
    prompt = agent.build_prompt(book, [], {})
    assert "Your persistent memory notes across passes:" in prompt
    assert "- Keep cash buffer at 10%" in prompt
