"""The system reports what it sees; the agent decides what that is worth.

Until 2026-09-12 a sharp move or a volume spike commissioned an analysis on the
spot, and the pre-market earnings check did the same for anything reporting
soon. Both chose what the agent should study, and both were charged to the
agent's own research budget — ten such charges on the live book, none of them
asked for. A sharp move is also the moment selling may beat studying, and the
agent was not even asked until sixteen minutes of unordered research had run.

See the 2026-09-12 entry in JOURNEY.md.
"""
import asyncio
import dataclasses
import datetime
import types

import pytest

from backend.services import agent, agent_book, watchdog
from backend.tasks import scheduler


def _book():
    return agent_book.Book(budget=1000.0, cash=500.0, realized_pnl=0.0, holdings=[])


# --- nothing commissions research any more -------------------------------------


def test_the_watchdog_returns_alerts_and_nothing_else():
    """The second return value was the list of tickers to analyse."""
    import inspect

    assert "tuple" not in str(inspect.signature(watchdog.scan_for_alerts).return_annotation)


def test_an_alert_carries_no_analysis_flag():
    assert "trigger_analysis" not in {f.name for f in dataclasses.fields(watchdog.AlertCandidate)}


def test_the_alert_job_wakes_the_agent_instead_of_analysing(monkeypatch):
    woken = []

    async def fake_maybe_run_agent():
        woken.append(True)

    async def nothing():
        return None

    monkeypatch.setattr(scheduler.watchdog, "is_us_market_hours", lambda: True)
    monkeypatch.setattr(scheduler, "_settle_agent_fills", nothing)
    monkeypatch.setattr(scheduler, "_place_queued_exits", nothing)
    monkeypatch.setattr(
        scheduler.watchdog, "scan_for_alerts",
        lambda: [types.SimpleNamespace(message="ORCL moved -5.4% today")],
    )
    monkeypatch.setattr(scheduler, "notify", lambda *a, **kw: nothing())
    monkeypatch.setattr(scheduler, "_maybe_run_agent", fake_maybe_run_agent)

    asyncio.run(scheduler._alert_watchdog_job())

    assert woken == [True]


def test_nothing_in_the_scheduler_can_commission_an_analysis():
    """The function that did it is gone, not merely unused — a dormant one is
    an invitation to call it again."""
    assert not hasattr(scheduler, "_run_triggered_analyses")
    assert not hasattr(scheduler, "_dispatch_immediate_research")


def test_the_earnings_check_records_and_wakes(monkeypatch):
    stored = {}
    woken = []

    async def fake_maybe_run_agent():
        woken.append(True)

    monkeypatch.setattr(scheduler.watchdog, "earnings_due", lambda: [("NVDA", datetime.date(2026, 9, 16))])
    monkeypatch.setattr(scheduler.agent, "store_earnings_dates", lambda up: stored.update(up=list(up)))
    monkeypatch.setattr(scheduler, "_maybe_run_agent", fake_maybe_run_agent)

    class Thursday(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.datetime(2026, 9, 10, 13, 0, tzinfo=datetime.timezone.utc)

    monkeypatch.setattr(scheduler.datetime, "datetime", Thursday)

    asyncio.run(scheduler._earnings_check_job())

    assert stored["up"] == [("NVDA", datetime.date(2026, 9, 16))]
    assert woken == [True]


# --- and the agent is told what was seen ---------------------------------------


def test_what_the_watchdog_saw_reaches_the_prompt():
    prompt = agent.build_prompt(
        _book(), [], {},
        alerts=[{"at": "11 Sep 10:31 AM", "text": "ORCL moved -5.4% today."}],
    )

    assert "## What was noticed" in prompt
    assert "ORCL moved -5.4% today." in prompt
    assert "nothing was analysed" in prompt


def test_earnings_reach_the_prompt_as_a_choice_not_an_action():
    prompt = agent.build_prompt(
        _book(), [], {}, earnings=[("NVDA", "2026-09-16")],
    )

    assert "NVDA on 2026-09-16" in prompt
    assert "sell before it, study it, or hold through it" in prompt


def test_the_prompt_no_longer_claims_a_move_is_analysed_unasked():
    """The old rule said a sharp move is 'analysed on the spot whether you
    asked for it or not'. It was also stated without its qualifier, so the
    agent applied it to ORCL — a ticker nothing was watching."""
    prompt = agent.build_prompt(
        _book(), [], {}, price=0.05, watchlist=["NVDA"], max_watchlist=30,
    )

    assert "analysed on the spot whether you asked for it or not" not in prompt
    assert "Nothing is ever analysed unless you ask for it and pay for it" in prompt


def test_a_past_earnings_date_is_dropped(monkeypatch):
    """The store is written once a day; a date that has passed must not keep
    appearing as something about to happen."""
    monkeypatch.setattr(
        agent.db, "get_setting",
        lambda key: '[["OLD", "2020-01-01"], ["NEW", "2099-01-01"]]',
    )

    assert agent._earnings_due() == [("NEW", "2099-01-01")]


def test_unreadable_stored_earnings_do_not_break_a_pass(monkeypatch):
    monkeypatch.setattr(agent.db, "get_setting", lambda key: "not json")

    assert agent._earnings_due() == []


# --- what the probe runs taught (2026-09-12) ------------------------------------


def test_alerts_older_than_the_last_pass_are_dropped(monkeypatch):
    """**Found by reading the model's own reasoning.** The first version took
    the newest eight alerts whatever their age, so a Saturday pass was shown
    Thursday's and Friday's moves under a heading saying "noticed" — and four
    probe runs against the live book ignored the section completely."""
    now = datetime.datetime.now(datetime.timezone.utc)

    def alert(hours_ago, text):
        return types.SimpleNamespace(
            created_at=now - datetime.timedelta(hours=hours_ago), message=text
        )

    monkeypatch.setattr(
        agent.db, "get_agent_runs",
        lambda limit=1: [types.SimpleNamespace(ran_at=now - datetime.timedelta(hours=3))],
    )
    monkeypatch.setattr(
        agent.db, "get_recent_alerts",
        lambda limit=8: [alert(1, "fresh"), alert(48, "two days old")],
    )

    assert [a["text"] for a in agent._recent_alerts()] == ["fresh"]


def test_with_no_previous_pass_it_falls_back_to_a_day(monkeypatch):
    now = datetime.datetime.now(datetime.timezone.utc)
    monkeypatch.setattr(agent.db, "get_agent_runs", lambda limit=1: [])
    monkeypatch.setattr(
        agent.db, "get_recent_alerts",
        lambda limit=8: [
            types.SimpleNamespace(created_at=now - datetime.timedelta(hours=2), message="today"),
            types.SimpleNamespace(created_at=now - datetime.timedelta(hours=30), message="yesterday"),
        ],
    )

    assert [a["text"] for a in agent._recent_alerts()] == ["today"]


def test_what_the_agent_just_did_sits_above_the_signal_table():
    """Placement was measured, not guessed. Both sections were between the two
    tables, at 42% and 47% of the prompt, and no probe run referenced either."""
    prompt = agent.build_prompt(
        _book(), [_signal_row()], {"AAA": 10.0},
        outcomes=["AAA: bought 1 at about $10.00."],
        alerts=[{"at": "12 Sep 9:31 AM", "text": "AAA moved -6% today."}],
    )

    did = prompt.index("## What you just did")
    noticed = prompt.index("## What was noticed")
    table = prompt.index("Recent analyst signals")

    assert did < noticed < table


def _signal_row():
    return types.SimpleNamespace(
        ticker="AAA", decision="Hold", signal_date=datetime.date(2026, 9, 11),
        created_at=None, price_at_signal=10.0, entry_price=None, stop_loss=None,
        price_target=None, win_probability=None, risk_reward=None,
        expected_value_r=None, id=1, model=None, trigger=None,
    )
