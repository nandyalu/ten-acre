"""Each deployment states its own start date.

The second container came up on 2026-09-09 and its home page said "day 8".
The date was a constant compiled into the frontend bundle, so every image
built from this repo claimed the first deployment's start however long that
particular container had been running.

Deliberately not derived from the data: the earliest recorded pass in the live
database is 2026-09-03 while the published start is the 2nd, and that gap is
intentional — the container was switched on that day and the first pass fell
after hours. Deriving it would replace a claim somebody made on purpose with
an artifact of scheduling.
"""
import datetime

import pytest

from backend.services import experiment


@pytest.fixture(autouse=True)
def no_ambient_config(monkeypatch):
    monkeypatch.delenv("EXPERIMENT_START_DATE", raising=False)


def test_an_unset_variable_keeps_the_published_date():
    """The original deployment's site must not move. No page it has ever
    served may change because this became configurable."""
    assert experiment.start_date() == datetime.date(2026, 9, 2)


def test_a_deployment_can_state_its_own_start(monkeypatch):
    monkeypatch.setenv("EXPERIMENT_START_DATE", "2026-09-09")
    assert experiment.start_date() == datetime.date(2026, 9, 9)


def test_an_unparseable_date_falls_back_rather_than_raising(monkeypatch, caplog):
    """A mistyped date should cost a wrong label on one page, never the
    container's ability to start."""
    monkeypatch.setenv("EXPERIMENT_START_DATE", "9th September")

    assert experiment.start_date() == experiment.DEFAULT_START
    assert "not an ISO date" in caplog.text


def test_an_empty_value_is_the_same_as_unset(monkeypatch):
    monkeypatch.setenv("EXPERIMENT_START_DATE", "   ")
    assert experiment.start_date() == experiment.DEFAULT_START


# --- the day counter ------------------------------------------------------------


def test_the_first_day_is_day_one():
    assert experiment.day_number(datetime.date(2026, 9, 2)) == 1


def test_the_day_counts_from_the_start(monkeypatch):
    monkeypatch.setenv("EXPERIMENT_START_DATE", "2026-09-09")
    assert experiment.day_number(datetime.date(2026, 9, 9)) == 1
    assert experiment.day_number(datetime.date(2026, 9, 10)) == 2


def test_a_second_container_does_not_inherit_the_first_ones_day(monkeypatch):
    """The bug this was written for: a container started on the 9th showing
    'day 8' because it counted from the first deployment's start."""
    today = datetime.date(2026, 9, 9)

    assert experiment.day_number(today) == 8  # the default, i.e. the old behaviour

    monkeypatch.setenv("EXPERIMENT_START_DATE", "2026-09-09")
    assert experiment.day_number(today) == 1


def test_a_date_before_the_start_never_goes_below_day_one():
    """A clock skew or a start date in the future must not render 'day -3'."""
    assert experiment.day_number(datetime.date(2026, 8, 30)) == 1


def test_the_settings_payload_carries_it(monkeypatch):
    """It reaches the frontend through the settings call the app already
    makes, rather than a new endpoint."""
    monkeypatch.setenv("EXPERIMENT_START_DATE", "2026-09-09")
    from backend.api.routes.settings import _current_settings

    assert _current_settings().experiment_start == datetime.date(2026, 9, 9)


# --- stamped when the agent is switched on --------------------------------------


@pytest.fixture
def stored(monkeypatch):
    """The BotSetting row, in memory — a test must never write the real one."""
    values: dict[str, str] = {}
    monkeypatch.setattr(experiment.db, "get_setting", lambda key: values.get(key))
    monkeypatch.setattr(experiment.db, "set_setting", lambda key, value: values.update({key: value}))
    return values


def test_switching_the_agent_on_stamps_today(stored):
    """The agent is off until a person turns it on, so this is a deliberate
    act on a date they chose — which is what the start date has always meant."""
    recorded = experiment.record_start(datetime.date(2027, 1, 15))

    assert recorded == datetime.date(2027, 1, 15)
    assert experiment.start_date() == datetime.date(2027, 1, 15)


def test_the_stamp_is_written_once_and_never_moves(stored):
    """A deployment paused and resumed months later has not started a second
    experiment, and a moving date would rewrite every 'day N' already shown."""
    experiment.record_start(datetime.date(2027, 1, 15))
    again = experiment.record_start(datetime.date(2027, 4, 2))

    assert again == datetime.date(2027, 1, 15)
    assert experiment.start_date() == datetime.date(2027, 1, 15)


def test_the_env_var_corrects_a_stamp_written_on_the_wrong_day(stored, monkeypatch):
    """2026-09-23: a deployment running since the 3rd was stamped the day its
    settings were next saved, and the variable could not correct it."""
    experiment.record_start(datetime.date(2026, 9, 23))
    monkeypatch.setenv("EXPERIMENT_START_DATE", "2026-09-03")

    assert experiment.start_date() == datetime.date(2026, 9, 3)


def test_switching_on_stamps_the_stated_date_not_today(stored, monkeypatch):
    monkeypatch.setenv("EXPERIMENT_START_DATE", "2026-09-03")
    experiment.record_start()
    monkeypatch.delenv("EXPERIMENT_START_DATE")

    assert experiment.start_date() == datetime.date(2026, 9, 3)


def test_the_live_deployment_is_untouched(stored, monkeypatch):
    """It has no stored date and no variable, so it still answers what its own
    site has shown since the first day."""
    monkeypatch.delenv("EXPERIMENT_START_DATE", raising=False)
    assert experiment.start_date() == datetime.date(2026, 9, 2)


def test_a_corrupt_stored_value_falls_back_rather_than_raising(stored, monkeypatch):
    stored["experiment_started_on"] = "not-a-date"
    monkeypatch.delenv("EXPERIMENT_START_DATE", raising=False)

    assert experiment.start_date() == experiment.DEFAULT_START


# --- the minute-bar backfill follows it -----------------------------------------


def test_the_backfill_floor_follows_this_deployments_start(stored):
    """The reason this was wrong to leave as a constant: a self-hoster
    starting four months from now would page back four months of 1-minute
    bars to reach a date belonging to somebody else's experiment."""
    from backend.services import intraday

    experiment.record_start(datetime.date(2027, 1, 15))

    assert intraday.experiment_start() == datetime.datetime(2027, 1, 15, 0, 0)


def test_the_backfill_floor_is_read_per_call_not_frozen_at_import(stored):
    """A default argument is evaluated once at import, which would freeze
    whatever the date was when the module first loaded."""
    from backend.services import intraday

    experiment.record_start(datetime.date(2027, 1, 15))
    first = intraday.experiment_start()
    stored["experiment_started_on"] = "2027-06-01"

    assert first != intraday.experiment_start()
