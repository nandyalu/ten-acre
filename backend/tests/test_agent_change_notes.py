"""The other half of a `note` order: telling the agent when one was answered.

A note reaches the maintainers and "nothing acts on it automatically" — until
this existed, that was also true of the maintainers' side. If someone built
what a note asked for, the agent had no way to learn its note had been read,
and would keep asking or keep working around a restriction that no longer
existed.

Built 2026-09-08 as a database-backed setting, then rebuilt the same day as a
git-tracked file (backend/agent_changes.json) instead — this project has
reset its own database more than once, and an entry here should survive that
the way JOURNEY.md already does. Edited by hand, in the same commit as the
change it describes.
"""
import datetime
import json

import pytest

from backend.services import agent, agent_book


def _book():
    return agent_book.Book(budget=10_000.0, cash=1_000.0, realized_pnl=0.0, holdings=[])


def _days_ago(n):
    return (datetime.date.today() - datetime.timedelta(days=n)).isoformat()


# --- reading backend/agent_changes.json -----------------------------------------


@pytest.fixture
def changes_file(tmp_path, monkeypatch):
    """A throwaway file standing in for backend/agent_changes.json, so no test
    touches the real one."""
    path = tmp_path / "agent_changes.json"
    monkeypatch.setattr(agent, "_CHANGES_FILE", path)
    return path


def test_a_missing_file_is_not_an_error(changes_file):
    """A fresh checkout with no file yet must not crash the agent's first
    decision pass."""
    assert agent.load_change_notes() == []


def test_a_written_entry_comes_back(changes_file):
    changes_file.write_text(json.dumps([
        {"date": "2026-09-08", "message": "You can now see settled vs. unsettled cash."},
    ]))

    got = agent.load_change_notes()

    assert got == [{"date": "2026-09-08", "message": "You can now see settled vs. unsettled cash."}]


def test_unreadable_json_does_not_crash_a_decision_pass(changes_file):
    """A typo while hand-editing the file must degrade to nothing shown, not
    take the agent down with it. The startup log is what catches this, not an
    exception mid-pass."""
    changes_file.write_text("{not valid json")

    assert agent.load_change_notes() == []


def test_the_file_must_be_a_list(changes_file):
    changes_file.write_text(json.dumps({"date": "2026-09-08", "message": "wrong shape"}))

    assert agent.load_change_notes() == []


def test_an_entry_missing_a_field_is_dropped_not_fatal(changes_file):
    changes_file.write_text(json.dumps([
        {"date": "2026-09-08"},
        {"message": "no date"},
        {"date": "2026-09-07", "message": "this one is fine"},
    ]))

    got = agent.load_change_notes()

    assert got == [{"date": "2026-09-07", "message": "this one is fine"}]


# --- the window: _recent_changes -------------------------------------------------


def test_a_note_from_today_is_shown(changes_file):
    changes_file.write_text(json.dumps([{"date": _days_ago(0), "message": "fresh"}]))

    assert [c["message"] for c in agent._recent_changes()] == ["fresh"]


def test_a_note_stops_after_the_agent_has_read_it_enough_times(changes_file, monkeypatch):
    """**Passes, not days (2026-09-12).** The agent picks its own cadence and
    ran between 3 and 11 passes a day over a measured week, so a day window
    showed one note about 25 times if it landed on a busy Tuesday and twice if
    it landed before a quiet weekend."""
    note = {"date": _days_ago(0), "message": "fresh"}
    changes_file.write_text(json.dumps([note]))
    store = {}
    monkeypatch.setattr(agent.db, "get_setting", lambda key: json.dumps(store))
    monkeypatch.setattr(agent.db, "set_setting", lambda key, value: store.update(json.loads(value)))

    for _ in range(agent._CHANGE_NOTES_PASSES):
        assert agent._recent_changes(), "still unread"
        agent.mark_changes_seen(agent._recent_changes())

    assert agent._recent_changes() == []


def test_age_alone_no_longer_expires_a_note(changes_file, monkeypatch):
    """A change made on a Friday must still reach an agent that sleeps all
    weekend. Under the day window it could expire entirely unread."""
    changes_file.write_text(json.dumps([{"date": _days_ago(30), "message": "old but unread"}]))
    monkeypatch.setattr(agent.db, "get_setting", lambda key: "{}")

    assert [c["message"] for c in agent._recent_changes()] == ["old but unread"]


def test_notes_come_back_oldest_first(changes_file):
    changes_file.write_text(json.dumps([
        {"date": _days_ago(1), "message": "second"},
        {"date": _days_ago(2), "message": "first"},
    ]))

    assert [c["message"] for c in agent._recent_changes()] == ["first", "second"]


def test_an_unparseable_date_is_dropped_not_fatal(changes_file):
    changes_file.write_text(json.dumps([
        {"date": "not-a-date", "message": "bad"},
        {"date": _days_ago(0), "message": "good"},
    ]))

    assert [c["message"] for c in agent._recent_changes()] == ["good"]


# --- the prompt: describe_recent_changes / build_prompt -------------------------


def test_no_changes_means_no_section():
    assert agent.describe_recent_changes([]) == []


def test_a_change_is_dated_and_stated():
    said = "\n".join(agent.describe_recent_changes(
        [{"date": "2026-09-08", "message": "You can now sell into a resting exit."}]
    ))

    assert "2026-09-08" in said
    assert "You can now sell into a resting exit." in said


def test_the_section_reaches_the_prompt():
    prompt = agent.build_prompt(
        _book(), [], {},
        changes=[{"date": "2026-09-08", "message": "New tool: X."}],
    )
    assert "New tool: X." in prompt


def test_no_changes_means_the_prompt_says_nothing_about_it():
    """The ordinary case. A header with nothing under it is prompt the agent
    has to read past for no reason."""
    assert "Changes made to this app" not in agent.build_prompt(_book(), [], {}, changes=[])


# --- the checked-in file itself ---------------------------------------------------


def test_the_real_file_is_valid():
    """A guard against committing a typo in backend/agent_changes.json — the
    one file in this feature that is not a fixture."""
    entries = json.loads(agent._CHANGES_FILE.read_text())
    assert isinstance(entries, list)
    for entry in entries:
        assert set(entry) >= {"date", "message"}
        datetime.date.fromisoformat(entry["date"])  # raises if unparseable


# --- the cost of the section (2026-09-12) ---------------------------------------


def test_only_the_newest_few_are_shown(changes_file, monkeypatch):
    """Seven notes landed on 2026-09-10, and with the older ones still inside
    the window the agent was handed ten of them — about 1,600 tokens of
    changelog before it saw a single price."""
    changes_file.write_text(json.dumps(
        [{"date": _days_ago(9 - i), "message": f"note {i}"} for i in range(9)]
    ))
    monkeypatch.setattr(agent.db, "get_setting", lambda key: "{}")

    shown = agent._recent_changes()

    assert len(shown) == agent._CHANGE_NOTES_SHOWN
    assert shown[-1]["message"] == "note 8", "and they are the newest ones"


def test_same_day_notes_collapse_under_one_date():
    lines = agent.describe_recent_changes([
        {"date": "2026-09-12", "message": "First."},
        {"date": "2026-09-12", "message": "Second."},
    ])

    assert len([ln for ln in lines if ln.startswith("- ")]) == 1
    assert "First. Second." in lines[-1]


def test_a_long_note_is_truncated_rather_than_sent_whole():
    """A backstop. The test below keeps the file itself inside the budget, so
    this should never fire in practice."""
    lines = agent.describe_recent_changes([
        {"date": "2026-09-12", "message": "word " * 200},
    ])

    assert len(lines[-1]) < agent._CHANGE_NOTE_MAX_CHARS + 40
    assert lines[-1].endswith("…")


def test_every_note_that_can_be_shown_fits_the_budget():
    """The real guard, against the real file.

    The notes drifted into commit messages: 530 characters on average by
    2026-09-12, the two longest 982 and 971. A note does not need to explain
    the new rule — the rules are in the same prompt and already current — it
    needs to say what is no longer true. JOURNEY.md holds the long version.
    """
    notes = agent.load_change_notes()
    newest = sorted(notes, key=lambda e: e["date"])[-agent._CHANGE_NOTES_SHOWN:]
    too_long = {
        f"{e['date']} ({len(e['message'])} chars)": e["message"][:60]
        for e in newest
        if len(e["message"]) > agent._CHANGE_NOTE_MAX_CHARS
    }

    assert not too_long, f"over {agent._CHANGE_NOTE_MAX_CHARS} characters: {too_long}"


def test_the_whole_section_stays_small(changes_file, monkeypatch):
    """The point of the exercise. It was 6,374 characters on 2026-09-12."""
    changes_file.write_text(json.dumps(
        [{"date": _days_ago(9 - i), "message": "x" * 400} for i in range(9)]
    ))
    monkeypatch.setattr(agent.db, "get_setting", lambda key: "{}")

    rendered = "\n".join(agent.describe_recent_changes(agent._recent_changes()))

    assert len(rendered) < 1800, f"{len(rendered)} characters"


def test_a_note_is_counted_once_per_pass_not_once_per_prompt(changes_file, monkeypatch):
    """A pass builds several prompts — a read, a refusal retry, another
    act-turn. Counting those would expire a note inside the very pass that
    first showed it."""
    changes_file.write_text(json.dumps([{"date": _days_ago(0), "message": "fresh"}]))
    store = {}
    monkeypatch.setattr(agent.db, "get_setting", lambda key: json.dumps(store))
    monkeypatch.setattr(agent.db, "set_setting", lambda key, value: store.update(json.loads(value)))

    shown = agent._recent_changes()
    agent.mark_changes_seen(shown)

    assert list(store.values()) == [1]


def test_a_note_that_left_the_file_is_pruned_from_the_store(changes_file, monkeypatch):
    """Otherwise the row grows for the life of the deployment."""
    changes_file.write_text(json.dumps([{"date": _days_ago(0), "message": "current"}]))
    store = {"deadbeefdead": 2}
    monkeypatch.setattr(agent.db, "get_setting", lambda key: json.dumps(store))

    def save(key, value):
        store.clear()
        store.update(json.loads(value))

    monkeypatch.setattr(agent.db, "set_setting", save)

    agent.mark_changes_seen(agent._recent_changes())

    assert "deadbeefdead" not in store


def test_unreadable_seen_state_re_shows_rather_than_hides(monkeypatch):
    """The harmless direction. Hiding a note nobody read is the bad one."""
    monkeypatch.setattr(agent.db, "get_setting", lambda key: "not json")

    assert agent._changes_seen() == {}
