"""The shape of the agent's answer, as one JSON schema.

Since 2026-09-17 a Gemini deployment answers by calling a function named
``decide`` (see ``llm_gemini``). This schema is that function's parameters, so
the server refuses a malformed answer before it is generated. The fields are
the ones the JSON channel has always asked for, so ``agent.parse_decision``
reads both channels alike.

**Every side is its own object, joined with ``anyOf``.** A flat object with
every field optional cannot say that a buy needs a quantity and a note does
not. The live failure this exists to stop is that kind of gap: on 2026-09-17
the model wrote "limit" in its own reason text and sent an order with no
``order_type`` field at all, a market buy it believed was a limit order. Here
``order_type`` is required on a buy and a sell, so a market order is a choice
the model makes, not a default it falls into.

**The schema constrains shape, never truth.** ``agent.screen`` still refuses an
unaffordable buy, a sell of shares not held, a full watchlist and a stop on
the wrong side of the price. Nothing here replaces those checks, and a bound a
schema could state, such as a minimum quantity, is left to Python: the smaller
the schema, the less there is for a vendor to reject.

Only ``type``, ``enum``, ``description``, ``properties``, ``required``,
``items`` and ``anyOf`` are used. A test keeps it that way, because a keyword
a vendor does not support fails the whole call, not the one field.

The descriptions are read by the model, so they are prompt text: short, and
never contradicting the rules in the system message.
"""

_TICKER = {"type": "string", "description": "The ticker symbol, in capitals."}
_REASON = {"type": "string", "description": "Why, in a sentence."}


def _side(
    name: str,
    description: str,
    properties: dict,
    required: list[str],
    reason_required: bool = True,
) -> dict:
    """One order shape. ``side`` is fixed to one value, so a buy's fields
    cannot be filled in under another side's name."""
    return {
        "type": "object",
        "description": description,
        "properties": {
            "side": {"type": "string", "enum": [name]},
            **properties,
            "reason": _REASON,
        },
        "required": ["side", *required, *(["reason"] if reason_required else [])],
    }


def _trade(name: str, description: str) -> dict:
    return _side(
        name,
        description,
        {
            "ticker": _TICKER,
            "quantity": {"type": "integer", "description": "Whole shares, at least 1."},
            "order_type": {
                "type": "string",
                "enum": ["market", "limit"],
                "description": (
                    "market trades now at the going price. limit trades only at "
                    "limit_price or better, and needs limit_price."
                ),
            },
            "limit_price": {
                "type": "number",
                "description": "The price for a limit order. Leave it out for a market order.",
            },
            "time_in_force": {
                "type": "string",
                "enum": ["day", "gtc"],
                "description": (
                    "day is gone at the close if it never filled. gtc waits until it "
                    "fills or you cancel it. Default day."
                ),
            },
        },
        ["ticker", "quantity", "order_type"],
    )


SIDES: dict[str, dict] = {
    "buy": _trade(
        "buy",
        "Buy shares. Every buy in the list must fit, added up, inside the cash "
        "stated in the message.",
    ),
    "sell": _trade("sell", "Sell shares you hold. Listed before a buy, its cash funds that buy."),
    "adjust": _side(
        "adjust",
        "Move the stop and/or the target resting under a position you hold, "
        "without trading it. Give a stop, a target, or both.",
        {
            "ticker": _TICKER,
            "stop": {
                "type": "number",
                "description": "The new stop price. Must be below the current price.",
            },
            "target": {
                "type": "number",
                "description": "The new take-profit price. Must be above the current price.",
            },
        },
        ["ticker"],
    ),
    "cancel": _side(
        "cancel",
        "Withdraw a limit order of yours that has not filled. Never touches a "
        "resting stop or target.",
        {"ticker": _TICKER},
        ["ticker"],
    ),
    "read": _side(
        "read",
        "Read the reasoning behind an analysis on record. Free. Only the reads "
        "run from an answer that asks for one; you are shown the result and "
        "asked again.",
        {
            "ticker": _TICKER,
            "date": {
                "type": "string",
                "description": "YYYY-MM-DD, for a particular analysis rather than the newest.",
            },
        },
        ["ticker"],
    ),
    "research": _side(
        "research",
        "Pay for a fresh analysis of a ticker. It runs inside this pass and you "
        "are shown what it found.",
        {"ticker": _TICKER},
        ["ticker"],
    ),
    "untrack": _side(
        "untrack",
        "Stop watching a ticker you do not hold, to free a watchlist slot.",
        {"ticker": _TICKER},
        ["ticker"],
    ),
    "note": _side(
        "note",
        "A message to the people who maintain you: a number you cannot see, a "
        "tool you lack, a rule that contradicts another. Not a decision.",
        {},
        [],
    ),
    "memory": _side(
        "memory",
        "A long-term note shown in every future prompt until cleared. Put the "
        "note in reason, or use action clear to remove every memory note.",
        {
            "action": {
                "type": "string",
                "enum": ["clear"],
                "description": "clear removes every memory note. Leave it out to add one.",
            },
        },
        [],
        reason_required=False,
    ),
}

ORDER = {"anyOf": list(SIDES.values())}

DECISION = {
    "type": "object",
    "properties": {
        "reasoning": {
            "type": "string",
            "description": "One or two sentences: what you decided and why.",
        },
        "next_wakeup": {
            "type": "string",
            "description": (
                "When to ask you next: an ISO datetime in Eastern time, such as "
                "2026-09-11T09:00. Leave it out to be asked at the following open."
            ),
        },
        "next_wakeup_note": {
            "type": "string",
            "description": (
                "What you want to remember at that wakeup, that the next prompt "
                "will not tell you."
            ),
        },
        "orders": {
            "type": "array",
            "items": ORDER,
            "description": (
                "Everything you want carried out, in the order to execute it. "
                "Empty to hold everything."
            ),
        },
    },
    "required": ["reasoning", "orders"],
}

def order_schema(with_read: bool) -> dict:
    """The order union for one channel.

    On the tool channel ``read`` is a fetch function, not a side, so it leaves
    the union: two ways to read would be one too many, and the fetch is the
    one that returns the analysis inside the same call.
    """
    return {"anyOf": [shape for side, shape in SIDES.items() if with_read or side != "read"]}


def decide_declaration(with_read: bool) -> dict:
    """The ``decide`` function, in the shape Google's SDK takes
    (``types.FunctionDeclaration(**declaration)``). ``parameters_json_schema``
    is plain JSON Schema, which is why ``anyOf`` above is spelled the JSON way."""
    orders = {**DECISION["properties"]["orders"], "items": order_schema(with_read)}
    return {
        "name": "decide",
        "description": (
            "Your answer for this turn. Call it once, with every order you want "
            "carried out, in the order to execute them. An empty orders list holds "
            "everything."
        ),
        "parameters_json_schema": {
            **DECISION,
            "properties": {**DECISION["properties"], "orders": orders},
        },
    }


# What production sends: reads are a fetch there (below), not a side.
DECIDE = decide_declaration(with_read=False)

# **The fetch functions (2026-09-17).** Each runs at once and its result goes
# back to the model inside the same call, so the model reads, then decides.
# They exist because the prompt carried every one of these on every pass,
# whether or not the pass needed them, and the model called the candidate
# list noise. A fetch with no arguments declares no parameters at all: the
# API refuses an object schema with no properties.
_ONE_TICKER = {"type": "object", "properties": {"ticker": _TICKER}, "required": ["ticker"]}

FETCHES: list[dict] = [
    {
        "name": "read",
        "description": (
            "The reasoning behind an analysis on record: the rationale, the "
            "investment plan, the trader's plan and each analyst's summary. Free; "
            "you already paid for the analysis. The newest unless you give a date."
        ),
        "parameters_json_schema": {
            "type": "object",
            "properties": {
                "ticker": _TICKER,
                "date": {
                    "type": "string",
                    "description": "YYYY-MM-DD, for a particular analysis rather than the newest.",
                },
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "candidates",
        "description": (
            "Screened names you may research that you do not track yet: liquid, "
            "actively traded, not a pump. A research of a new ticker must name one "
            "of these. Nothing on them has been analysed."
        ),
    },
    {
        "name": "fundamentals",
        "description": (
            "A ticker's sector and industry, its key ratios (market cap, PE, PEG, "
            "price to book, margins, return on equity, debt to equity, free cash "
            "flow), its last four quarters of revenue, income and EPS, and the "
            "analyst consensus counts. From the data vendors, not from an analysis."
        ),
        "parameters_json_schema": _ONE_TICKER,
    },
    {
        "name": "watchlist",
        "description": (
            "Every ticker you track, with its price now, when it was last analysed "
            "and at what price, how far it has moved since, and what that analysis "
            "said."
        ),
    },
    {
        "name": "track_record",
        "description": (
            "Your closed trades: how many, how many made money, the net result, the "
            "average hold, and the last few one by one with what the analyst said "
            "at entry."
        ),
    },
]
FETCH_NAMES = [f["name"] for f in FETCHES]

# The whole vocabulary. A test walks the schema and refuses anything else, so
# a keyword the vendor may not support cannot slip in with a later edit.
KEYWORDS = frozenset({"type", "enum", "description", "properties", "required", "items", "anyOf"})
