"""CLAUDE.md quotes the prompt. A quotation is stale the moment the original
moves, and this one had drifted three ways at once by 2026-09-10.

It quoted a system message still saying "paper-trading" a day after the code
dropped the word, and its rules list was missing two rules the agent actually
reads. **A wrong quotation there is worse than no quotation**, because that
file is what gets read instead of the code — by a contributor, and by me.

The drift was found by hand during a sweep. These tests catch it on the commit
that causes it.

**Only the mechanical half lives here.** CLAUDE.md deliberately paraphrases
some rules and abbreviates others with `[...]`, so a whole-list comparison
produces false positives and belongs in the `stale-check` skill, where a
person reads the hits. What is asserted here is exact: two verbatim strings,
and the three rules that must never be trimmed.
"""
import pathlib
import re

from backend.services import agent

# The quotation moved from CLAUDE.md to .claude/rules/agent.md on 2026-09-13, so
# a session loads it only when it reads the agent's code. Both files are read,
# so a quotation may live in either one.
_ROOT = pathlib.Path(__file__).resolve().parents[2]
CLAUDE_MD = "\n".join((_ROOT / p).read_text() for p in ("CLAUDE.md", ".claude/rules/agent.md"))
# Strip the blockquote markers before flattening. The quotations are wrapped
# markdown blockquotes, so "> " sits at the start of every continuation line
# and a naive whitespace collapse leaves it mid-sentence. Without this the
# test fails on a clean tree, which is how it was caught.
FLAT = re.sub(r"\s+", " ", re.sub(r"^\s*>\s?", "", CLAUDE_MD, flags=re.M))


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def test_the_quoted_system_message_is_the_real_one():
    """CLAUDE.md quotes SYSTEM_PROMPT verbatim under 'The rules, verbatim'."""
    # The identity paragraph, not the whole message. The fixed rules moved into
    # SYSTEM_PROMPT on 2026-09-10, and CLAUDE.md abbreviates several of them
    # with [...] — quoting all thirteen verbatim would make that section a
    # duplicate of the code rather than a description of it.
    identity = agent.SYSTEM_PROMPT.split("\n\n")[0]
    assert _flat(identity) in FLAT, (
        "CLAUDE.md's quoted system message no longer matches the code.\n\n"
        f"The code says:\n  {_flat(identity)}\n\n"
        "Update the quotation under 'The rules, verbatim' in .claude/rules/agent.md. It drifted on "
        "2026-09-09 when the prompt stopped calling the account paper-trading, "
        "and stayed wrong for a day."
    )


def test_the_quoted_opener_is_the_real_one():
    """The first line of every prompt, quoted in the same section.

    It is worth pinning on its own because it carries the experiment's most
    deliberate lie: the agent is told the money is real. Anyone reading
    CLAUDE.md to decide whether that is still true must get the current
    answer.
    """
    source = pathlib.Path(agent.__file__).read_text()
    match = re.search(r'"(You manage a small[^"]*)"', source)
    assert match, "The prompt no longer opens with a 'You manage a small ...' line."

    assert _flat(match.group(1)) in FLAT, (
        "CLAUDE.md's quoted opener no longer matches the code.\n\n"
        f"The code says:\n  {_flat(match.group(1))}\n\n"
        "Update the quotation under 'The rules, verbatim' in .claude/rules/agent.md."
    )


# Each of these was added to the prompt after the model got that exact thing
# wrong on a live run. CLAUDE.md says they "should not be trimmed as padding",
# and they read as padding to anyone who does not know the history — which is
# everyone arriving at this repo for the first time.
FAILURE_DERIVED = {
    "the total-not-each wording": "Not each — in total.",
    "the sell-to-fund ordering": "so a sell frees its cash for a buy",
    "the meaning of a Hold": "Hold means no action is",
}


def test_the_rules_that_exist_because_of_a_live_failure_are_still_there():
    """Protects the prompt, not the documentation.

    A prompt that has grown long invites tidying, and these three lines are
    the most obviously trimmable in it. Each is there because the model made
    that specific mistake with real orders: it proposed buys totalling more
    than its cash, it listed a buy before the sell that funded it, and it
    bought on a Hold.
    """
    # Rendered, not read from source. Since 2026-09-10 the fixed rules are
    # wrapped string literals in _FIXED_RULES, so the file contains
    # 'for " "a buy' where the prompt contains "for a buy" — a source
    # match would fail on a rule that is present and correct.
    import backend.tests.test_agent as _t
    source = _flat(agent.SYSTEM_PROMPT + agent.build_prompt(_t._book(), [], {}))

    missing = [name for name, phrase in FAILURE_DERIVED.items() if _flat(phrase) not in source]

    assert not missing, (
        "A rule that exists because of a live failure is gone from the prompt:\n  "
        + "\n  ".join(missing)
        + "\n\nEach was added after the model got that exact thing wrong with real "
        "orders. They read as padding without that history, which is why they are "
        "pinned here. If one is genuinely being replaced, update this test and "
        "write the JOURNEY.md entry saying what replaced it."
    )
