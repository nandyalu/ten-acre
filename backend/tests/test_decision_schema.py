"""The ``decide`` schema says what each side of an order takes, and the fetch
functions say what they need.

Pure. These pin the shapes the vendor is sent, and that the existing parser
reads an answer built from them, so the two channels cannot drift apart.
"""
import json

from backend.services import agent, decision_schema

EVERY_SIDE = {"buy", "sell", "adjust", "cancel", "read", "research", "untrack", "note", "memory"}


def _shape(side: str) -> dict:
    return decision_schema.SIDES[side]


def _sides_in(union: dict) -> list[str]:
    return [shape["properties"]["side"]["enum"][0] for shape in union["anyOf"]]


def test_every_side_the_agent_may_use_is_in_the_schema_once():
    sides = [shape["properties"]["side"]["enum"] for shape in decision_schema.ORDER["anyOf"]]

    assert [s[0] for s in sides] == list(decision_schema.SIDES)
    assert set(decision_schema.SIDES) == EVERY_SIDE
    assert all(len(s) == 1 for s in sides), "a side's enum holds exactly its own name"


def test_a_trade_must_say_market_or_limit():
    """The failure this schema exists for: an order the model called a limit
    order in its own words, sent with no order_type at all (2026-09-17)."""
    for side in ("buy", "sell"):
        shape = _shape(side)
        assert set(shape["required"]) == {"side", "ticker", "quantity", "order_type", "reason"}
        assert shape["properties"]["order_type"]["enum"] == ["market", "limit"]
        assert shape["properties"]["time_in_force"]["enum"] == ["day", "gtc"]
        assert shape["properties"]["quantity"]["type"] == "integer"


def test_the_sides_that_move_nothing_need_only_a_reason():
    assert _shape("note")["required"] == ["side", "reason"]
    # A memory can be a clear, which has nothing to say.
    assert _shape("memory")["required"] == ["side"]
    assert _shape("memory")["properties"]["action"]["enum"] == ["clear"]


def test_the_sides_that_name_a_ticker_require_it():
    for side in ("adjust", "cancel", "read", "research", "untrack"):
        assert "ticker" in _shape(side)["required"], side


def test_read_is_a_side_on_the_json_channel_and_a_fetch_on_the_tool_channel():
    """Two ways to read would be one too many. On the tool channel the fetch
    returns the analysis inside the same call, so the side leaves the union."""
    assert "read" in _sides_in(decision_schema.order_schema(with_read=True))
    assert "read" not in _sides_in(decision_schema.order_schema(with_read=False))
    assert set(_sides_in(decision_schema.order_schema(with_read=False))) == EVERY_SIDE - {"read"}


def test_the_declaration_production_sends_is_the_tool_channel_one():
    assert decision_schema.DECIDE["name"] == "decide"
    parameters = decision_schema.DECIDE["parameters_json_schema"]
    assert set(parameters["required"]) == {"reasoning", "orders"}
    assert "read" not in _sides_in(parameters["properties"]["orders"]["items"])
    # Everything but the orders union is the one DECISION.
    assert {k: v for k, v in parameters["properties"].items() if k != "orders"} == {
        k: v for k, v in decision_schema.DECISION["properties"].items() if k != "orders"
    }


def test_the_fetches_are_the_five_the_rules_name():
    assert decision_schema.FETCH_NAMES == ["read", "candidates", "fundamentals", "watchlist", "track_record"]
    by_name = {f["name"]: f for f in decision_schema.FETCHES}
    assert by_name["read"]["parameters_json_schema"]["required"] == ["ticker"]
    assert "date" in by_name["read"]["parameters_json_schema"]["properties"]
    assert by_name["fundamentals"]["parameters_json_schema"]["required"] == ["ticker"]
    # A fetch with nothing to say declares no parameters at all: the API
    # refuses an object schema with no properties.
    for name in ("candidates", "watchlist", "track_record"):
        assert "parameters_json_schema" not in by_name[name], name
    assert all(f["description"] for f in decision_schema.FETCHES)


def test_an_answer_built_from_the_schema_reads_through_the_existing_parser():
    """One sample per side, in the shape the schema allows, through the same
    ``parse_decision`` every answer has always gone through."""
    orders = [
        {"side": "buy", "ticker": "AAPL", "quantity": 2, "order_type": "market", "reason": "r"},
        {"side": "sell", "ticker": "INTC", "quantity": 5, "order_type": "limit",
         "limit_price": 110.5, "time_in_force": "gtc", "reason": "r"},
        {"side": "adjust", "ticker": "INTC", "stop": 105.0, "reason": "r"},
        {"side": "cancel", "ticker": "INTC", "reason": "r"},
        {"side": "read", "ticker": "NVDA", "date": "2026-09-08", "reason": "r"},
        {"side": "research", "ticker": "CRWV", "reason": "r"},
        {"side": "untrack", "ticker": "NOK", "reason": "r"},
        {"side": "note", "reason": "a screener would help"},
        {"side": "memory", "action": "clear"},
    ]
    answer = json.dumps({"reasoning": "Hold.", "next_wakeup": "2026-09-18T09:30", "orders": orders})

    reasoning, parsed = agent.parse_decision(answer)

    assert reasoning == "Hold."
    assert [o["side"] for o in parsed] == [o["side"] for o in orders]
    assert agent.parse_wakeup_note(answer) is None
    assert agent.parse_wakeup(answer) is not None


def _walk(node, path=()):
    """Every dict key in the schema is a keyword the vendors accept, or a
    property name under ``properties``."""
    if not isinstance(node, dict):
        return
    for key, value in node.items():
        assert key in decision_schema.KEYWORDS, (
            f"{'/'.join(path) or 'root'}: {key!r} is not one of the keywords every "
            "vendor in reach accepts. A keyword the vendor rejects fails the whole "
            "call, not one field — check it live before adding it."
        )
        if key == "properties":
            for name, shape in value.items():
                _walk(shape, path + (name,))
        elif key == "items":
            _walk(value, path + ("items",))
        elif key == "anyOf":
            for i, shape in enumerate(value):
                _walk(shape, path + (f"anyOf[{i}]",))


def test_only_the_safe_subset_of_json_schema_is_used():
    _walk(decision_schema.DECISION)
    _walk(decision_schema.DECIDE["parameters_json_schema"])
    for fetch in decision_schema.FETCHES:
        _walk(fetch.get("parameters_json_schema") or {}, (fetch["name"],))
