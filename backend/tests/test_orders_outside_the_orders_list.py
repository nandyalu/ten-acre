"""An order the model put beside ``orders`` instead of inside it.

**Every case here is a real answer from the live book**, found by reading back
through fifty stored ones after the agent's note said it had researched NVDA
and no analysis had run. Three decisions had been dropped without a word: one
research order and two notes.

A note is the agent telling the people who maintain it that something is
missing, which makes a silent drop the worst kind of loss — the message exists
to reach a human and never did.
"""
from backend.services import agent


def orders(text: str) -> list[dict]:
    return agent.parse_decision(text)[1]


# --- the three that were actually lost ------------------------------------------


def test_research_at_the_top_level_is_not_dropped():
    """Run 46, 2026-09-12. The second turn of a chained pass answered with a
    top-level "research" key and no "orders" at all, so the analysis it asked
    for never ran and only its own note mentioned it."""
    answer = """```json
    {"research": [{"ticker": "NVDA", "reason": "Price has dropped 2.4%"}],
     "next_wakeup": "2026-09-14T09:30"}
    ```"""

    assert orders(answer) == [
        {"ticker": "NVDA", "reason": "Price has dropped 2.4%", "side": "research"}
    ]


def test_a_note_at_the_top_level_is_not_dropped():
    """Run 9. A real note about being capital constrained, with an empty
    orders list beside it."""
    answer = '{"note": "Portfolio is capital constrained.", "orders": []}'

    assert orders(answer) == [{"side": "note", "reason": "Portfolio is capital constrained."}]


def test_a_memo_is_a_note():
    """Run 36. Same thing under a different name, alongside a real order."""
    answer = ('{"memo": "The current $0.79 cash cannot fund any trades.",'
              ' "orders": [{"side": "research", "ticker": "AAA"}]}')

    assert orders(answer) == [
        {"side": "research", "ticker": "AAA"},
        {"side": "note", "reason": "The current $0.79 cash cannot fund any trades."},
    ]


# --- what must NOT be salvaged --------------------------------------------------


def test_a_top_level_sell_is_ignored():
    """**The line that makes this safe.** A bare "sell": "AVGO" does not say how
    many shares, and guessing is how a parser places an order nobody asked for.
    Only the two sides that move no money are read out of the wrong place."""
    answer = '{"sell": "AVGO", "buy": "NVDA", "orders": []}'

    assert orders(answer) == []


def test_an_empty_note_is_not_an_order():
    """Runs 5, 21 and 27 carried "", "None" and "[]" — a model writing
    "nothing here", which must not become an empty note in the record."""
    for value in ('""', '"None"', '"[]"', "null", "[]"):
        answer = '{"note": %s, "orders": [{"side": "adjust", "ticker": "A"}]}' % value

        assert orders(answer) == [{"side": "adjust", "ticker": "A"}], value


# --- shapes the same model is likely to reach for ---------------------------------


def test_a_bare_ticker_string_is_a_research_order():
    assert orders('{"research": "NVDA"}') == [{"side": "research", "ticker": "NVDA"}]


def test_several_notes_in_a_list_all_arrive():
    answer = '{"notes": ["first thing", "second thing"], "orders": []}'

    assert orders(answer) == [
        {"side": "note", "reason": "first thing"},
        {"side": "note", "reason": "second thing"},
    ]


def test_orders_inside_and_outside_are_both_kept():
    answer = ('{"orders": [{"side": "sell", "ticker": "AVGO", "quantity": 29}],'
              ' "research": [{"ticker": "NVDA"}]}')

    assert orders(answer) == [
        {"side": "sell", "ticker": "AVGO", "quantity": 29},
        {"side": "research", "ticker": "NVDA"},
    ]


def test_the_normal_shape_is_untouched():
    answer = ('{"reasoning": "r", "next_wakeup": "2026-09-14T09:30",'
              ' "orders": [{"side": "research", "ticker": "NVDA"}]}')

    assert orders(answer) == [{"side": "research", "ticker": "NVDA"}]


# --- a document json refuses (2026-09-12) ----------------------------------------


def test_a_string_closed_with_an_apostrophe_is_repaired():
    """**Run 44, verbatim from the live book.** The model wrote
    `"reason": "…to evaluate the thesis'` — an apostrophe where the closing
    quote belongs — so the newline after it landed inside the string and json
    called it an invalid control character. The whole answer parsed to nothing
    and two research orders were lost, which reads in the record as an idle
    pass the agent chose.
    """
    answer = (
        '{"reasoning": "r", "orders": [\n'
        '    {"ticker": "SMCI", "side": "research", "reason": "a fresh look\'\n'
        '    },\n'
        '    {"ticker": "SMR", "side": "research", "reason": "dropped 6.6%"}\n'
        ']}'
    )

    assert orders(answer) == [
        {"ticker": "SMCI", "side": "research", "reason": "a fresh look"},
        {"ticker": "SMR", "side": "research", "reason": "dropped 6.6%"},
    ]


def test_a_trailing_comma_is_repaired():
    """Meaning-preserving by construction: removing a comma before a closing
    brace cannot change what the document says."""
    answer = '{"orders": [{"side": "note", "reason": "x"},]}'

    assert orders(answer) == [{"side": "note", "reason": "x"}]


def test_an_apostrophe_inside_a_string_is_left_alone():
    """The repair must not fire on ordinary text. A string whose content really
    ends in an apostrophe still carries its closing quote."""
    answer = '{"orders": [{"side": "note", "reason": "the analysts\' view"}]}'

    assert orders(answer) == [{"side": "note", "reason": "the analysts' view"}]


def test_a_document_that_cannot_be_repaired_is_still_a_clean_loss():
    """**The line that keeps this honest.** A pass that guesses at a malformed
    answer is worse than a pass that skips one."""
    assert orders('{"orders": [{"side": "buy", "ticker":') == []
    assert orders("not json at all") == []
