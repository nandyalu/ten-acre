"""An early wake tells the agent what woke it, and what it replaces.

A sharp move, the earnings check, a stop filling and a position left unguarded
all wake the agent early (`scheduler.wake_agent_now`). A restart with new change
notes did too, until the change-note mechanism was removed on 2026-09-21. The alarm always said "You asked
to be woken now". On 2026-09-13 a restart woke the agent on a Saturday night,
and the prompt told it that it had asked for that.

The note it had left was for a wakeup still two days away. The prompt now says
so, and asks the agent to choose its wakeup and note again, because the early
pass replaces the planned alarm.
"""
import datetime
import types
from zoneinfo import ZoneInfo

import pytest

from backend.services import agent, agent_book
from backend.tasks import scheduler

ET = ZoneInfo("America/New_York")
NOW = datetime.datetime(2026, 9, 12, 23, 45, tzinfo=ET)
PLANNED = datetime.datetime(2026, 9, 14, 9, 25, tzinfo=ET)


@pytest.fixture(autouse=True)
def no_wake(monkeypatch):
    monkeypatch.setattr(scheduler, "_pending_wake", None)
    monkeypatch.setattr(scheduler, "_agent_task_id", None)
    monkeypatch.setattr(scheduler, "_final_pass_at", None)


def _book():
    return agent_book.Book(budget=1000.0, cash=500.0, realized_pnl=0.0, holdings=[])


def _run_task(monkeypatch) -> list[str]:
    seen = []
    monkeypatch.setattr(scheduler, "_run_agent_pass", lambda label, stop_event=None: seen.append(label))
    monkeypatch.setattr(scheduler.agent, "is_enabled", lambda: True)
    monkeypatch.setattr(scheduler.agent, "wakeup_due", lambda now: now)
    scheduler.agent_pass()
    return seen


# --- the label travels with the wake --------------------------------------------


def test_an_early_wake_carries_what_asked_for_it(monkeypatch):
    scheduler.wake_agent_now("Stop fill")

    assert _run_task(monkeypatch) == ["Stop fill"]


def test_a_pass_at_its_own_time_says_the_agent_asked(monkeypatch):
    assert _run_task(monkeypatch) == ["Alarm"]


def test_the_label_does_not_outlive_its_wake(monkeypatch):
    scheduler.wake_agent_now("Earnings")
    _run_task(monkeypatch)

    assert _run_task(monkeypatch) == ["Alarm"]


def test_a_trigger_passes_its_own_label(monkeypatch):
    asked = []
    monkeypatch.setattr(scheduler.agent, "is_enabled", lambda: True)
    monkeypatch.setattr(scheduler, "_last_agent_run", None)
    monkeypatch.setattr(scheduler, "wake_agent_now", asked.append)

    scheduler._maybe_run_agent("Earnings")

    assert asked == ["Earnings"]


# --- what the prompt says -------------------------------------------------------


def test_an_early_pass_names_the_time_the_note_was_for():
    lines = "\n".join(agent.describe_wakeup(
        scheduler._WOKE_BECAUSE["Event-driven"], "Review tracked tickers at the open.",
        planned=PLANNED, now=NOW,
    ))

    assert "A rule watching your tickers spotted something." in lines
    assert (
        '**The note you left for your wakeup on Monday 14 September at 9:25 AM Eastern:** '
        '"Review tracked tickers at the open."'
    ) in lines
    assert "That wakeup has not come yet." in lines
    assert "not an instruction" in lines


def test_an_early_pass_asks_for_the_wakeup_and_note_again():
    """The ask moved out of the wake block on 2026-09-21, into the section
    beside the field it fills. It is the part that does the work: 4 of 4
    samples wrote a new note, and only one mentioned what woke it."""
    lines = "\n".join(agent.describe_next_wakeup(PLANNED, "a note", now=NOW))

    assert "**Choose your next wakeup again.**" in lines
    assert "replaces the wakeup you planned for Monday 14 September at 9:25 AM Eastern" in lines
    assert '"next_wakeup"' in lines and '"next_wakeup_note"' in lines
    assert "If you write no note, the one you left for that wakeup is gone." in lines
    # The fallback now sits directly above the ask, so the ask no longer
    # repeats it.
    assert "You will next be asked at" in lines
    assert "If you give no time, you are asked at the following open" not in lines


def test_a_pass_that_is_not_early_is_only_told_the_fallback():
    lines = "\n".join(agent.describe_next_wakeup(None, None, now=NOW))

    assert "You will next be asked at" in lines
    assert "Choose your next wakeup again" not in lines


def test_an_early_pass_with_no_note_still_names_the_plan():
    """The wake block keeps the fact that this pass is early. Only the ask
    moved."""
    lines = "\n".join(agent.describe_wakeup(None, None, planned=PLANNED, now=NOW))

    assert "**You planned to wake on Monday 14 September at 9:25 AM Eastern.**" in lines

    asked = "\n".join(agent.describe_next_wakeup(PLANNED, None, now=NOW))
    assert "Choose your next wakeup again" in asked
    assert "the one you left for that wakeup is gone" not in asked


def test_a_pass_at_its_planned_time_reads_as_before():
    lines = "\n".join(agent.describe_wakeup(
        "You asked to be woken now.", "a note",
        planned=NOW - datetime.timedelta(seconds=5), now=NOW,
    ))

    assert '**A note you left yourself last pass:** "a note"' in lines
    assert "Choose your next wakeup again" not in lines


def test_the_planned_time_is_read_as_utc_from_the_database(monkeypatch):
    stored = datetime.datetime(2026, 9, 14, 13, 25)  # naive, as the column holds it
    monkeypatch.setattr(agent.db, "get_agent_runs", lambda limit=1: [types.SimpleNamespace(next_wakeup=stored)])

    assert agent._last_planned_wakeup() == PLANNED


def test_the_early_wake_is_named_at_the_top_and_asked_at_the_end():
    """Two halves, deliberately apart since 2026-09-21. Why the pass is
    happening stays under the clock. The ask sits beside the field it fills,
    at the end.

    **The pair moved below the book later the same day**, when the "Now" group
    went under "What is true now", so the account now comes first. The gap
    between the two halves is what this test is for, and it is wider than
    ever.
    """
    far = datetime.datetime.now(ET) + datetime.timedelta(days=3)
    prompt = agent.build_prompt(
        _book(), [], {}, woke_because=scheduler._WOKE_BECAUSE["Event-driven"],
        wakeup_note="a note", planned_wakeup=far,
    )

    assert prompt.index("Your account is") < prompt.index("It is ")
    assert prompt.index("It is ") < prompt.index("This pass is earlier")
    assert prompt.index("This pass is earlier") < prompt.index("Choose your next wakeup again")
    assert prompt.index("## Rules") < prompt.index("## Your next wakeup")


# --- a later turn of the same pass ----------------------------------------------


def test_a_later_turn_names_the_wake_as_history():
    """On 2026-09-13 the second turn restated the wake reason as though the agent
    had just been woken, when its research had just landed."""
    prompt = agent.build_prompt(
        _book(), [], {}, woke_because=scheduler._WOKE_BECAUSE["Event-driven"],
        outcomes=["INTC: the analysis finished."],
    )

    assert "## Why this pass started" in prompt
    assert "A rule watching your tickers spotted something." in prompt
    assert "## Why you are awake" not in prompt
    assert "**Why you are asked again.** This is the same pass, not a new wake" in prompt
    # One pointer since 2026-09-21, because the four sections it used to name
    # individually are one section now.
    assert 'All of it is under "What your last answer did" above.' in prompt
    assert "## What your last answer did" in prompt
    assert "**What you just did, a moment ago, in this pass.**" in prompt


def test_the_first_turn_is_unchanged():
    prompt = agent.build_prompt(_book(), [], {}, woke_because=scheduler._WOKE_BECAUSE["Event-driven"])

    assert "## Why you are awake" in prompt
    assert "A rule watching your tickers spotted something." in prompt
    assert "asked again" not in prompt


def test_each_reason_names_a_section_that_is_really_there():
    """No wake reason may promise a section. Each one is added only when its
    section is in the same prompt."""
    refused = agent_book.Rejection(side="buy", ticker="INTC", quantity=5, why="not enough cash")
    prompt = agent.build_prompt(_book(), [], {}, rejected=[refused], readings=["the case for INTC"])

    assert 'All of it is under "What your last answer did" above.' in prompt
    assert "**What you asked to read.**" in prompt
    assert "**Your previous answer was refused. Fix it:**" in prompt
    assert "What you just did, a moment ago" not in prompt
