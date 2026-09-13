"""A failed analysis is retried, and one that still fails is reported as failed.

Since research began to run inside the pass on 2026-09-12, an analysis that
failed was reported to the agent as "It has finished", beside the older
analysis of the same ticker. Two analyses have failed on these deployments:
Ollama refused the connection on 2026-09-10, and Cerebras returned 402 when its
credits ran out on 2026-09-11. Those two cases decide how a failure is answered.

No LLM and no network: the graph run, the recording and the waits are stubbed.
"""
import asyncio
import datetime
from types import SimpleNamespace

import pytest

from backend.services import agent, analysis, llm_throttle
from backend.tasks import scheduler


# --- telling failures apart ----------------------------------------------------


class ConnectError(Exception):
    """Named like httpcore's, which is what Ollama's refusal raised."""


class _Status(Exception):
    def __init__(self, status):
        super().__init__(f"Error code: {status}")
        self.status_code = status


def test_a_refused_connection_is_the_service():
    assert analysis.failure_kind(ConnectError("[Errno 111] Connection refused")) == "service"
    assert analysis.failure_kind(ConnectionRefusedError()) == "service"


def test_the_cause_is_read_through_a_wrapper():
    try:
        try:
            raise ConnectError("refused")
        except ConnectError as inner:
            raise RuntimeError("graph failed") from inner
    except RuntimeError as outer:
        assert analysis.failure_kind(outer) == "service"


def test_a_server_error_or_a_rate_limit_is_the_service():
    assert analysis.failure_kind(_Status(503)) == "service"
    assert analysis.failure_kind(_Status(429)) == "service"


def test_payment_or_permission_is_refused():
    """Cerebras' 402 would not clear in an hour."""
    assert analysis.failure_kind(_Status(402)) == "refused"
    assert analysis.failure_kind(_Status(401)) == "refused"


def test_a_spent_day_is_its_own_kind():
    assert analysis.failure_kind(llm_throttle.DailyLimitReached("spent")) == "limit"


def test_anything_else_is_an_app_error():
    assert analysis.failure_kind(ValueError("bad state")) == "app"
    assert analysis.failure_kind(_Status(404)) == "app"


# --- retrying ------------------------------------------------------------------


@pytest.fixture
def run(monkeypatch):
    """Scripted graph runs and recordings. Each item in ``attempts`` is either
    an exception to raise or a final_state to return."""
    state = {"attempts": [], "records": [], "calls": 0, "record_calls": 0, "slept": [], "probes": []}

    async def one_attempt(ticker):
        state["calls"] += 1
        outcome = state["attempts"].pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome, "BUY"

    def record(ticker, final_state, decision, trigger=None):
        state["record_calls"] += 1
        outcome = state["records"].pop(0) if state["records"] else "signal"
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    async def sleep(seconds):
        state["slept"].append(seconds)

    monkeypatch.setattr(analysis, "_one_attempt", one_attempt)
    monkeypatch.setattr(analysis, "record_signal", record)
    monkeypatch.setattr(analysis, "_sleep", sleep)
    monkeypatch.setattr(analysis, "_service_answers", lambda: state["probes"].pop(0) if state["probes"] else None)
    monkeypatch.setattr(analysis, "_recorded_since", lambda ticker, started: None)

    def go():
        return asyncio.run(analysis.run_analysis_and_record("intc"))

    state["go"] = go
    return state


def test_an_app_error_is_retried_once(run):
    run["attempts"] = [ValueError("flaky"), {"started_at": None}]

    assert run["go"]() == "signal"
    assert run["calls"] == 2
    assert run["slept"] == []


def test_an_app_error_twice_is_reported(run):
    run["attempts"] = [ValueError("broken"), ValueError("broken")]

    with pytest.raises(analysis.AnalysisFailed) as failed:
        run["go"]()

    assert "An error in this app stopped it 2 times" in str(failed.value)
    assert "Nothing was charged." in str(failed.value)


def test_the_service_is_waited_for_until_it_answers(run):
    """Ollama is asked whether it answers before the analysis runs again, so
    a long outage costs probes, not analyses."""
    run["attempts"] = [ConnectError("refused"), {"started_at": None}]
    run["probes"] = [False, False, True]

    assert run["go"]() == "signal"
    assert run["calls"] == 2
    assert run["slept"] == [30.0, 60.0, 120.0]


def test_the_wait_doubles_and_gives_up_after_an_hour(run):
    """With no endpoint to ask (Gemini), the analysis itself is the probe."""
    run["attempts"] = [ConnectError("refused")] * 50

    with pytest.raises(analysis.AnalysisFailed) as failed:
        run["go"]()

    assert run["slept"][:6] == [30.0, 60.0, 120.0, 240.0, 480.0, 600.0]
    assert max(run["slept"]) == 600.0
    assert sum(run["slept"]) == pytest.approx(3600.0)
    assert "did not answer for 60 minutes" in str(failed.value)


def test_a_refusal_of_access_is_reported_at_once(run):
    run["attempts"] = [_Status(402)]

    with pytest.raises(analysis.AnalysisFailed) as failed:
        run["go"]()

    assert run["calls"] == 1
    assert run["slept"] == []
    assert "refused the request" in str(failed.value)


def test_a_spent_day_is_reported_at_once(run):
    run["attempts"] = [RuntimeError("wrapped")]
    run["attempts"][0].__cause__ = llm_throttle.DailyLimitReached("spent")

    with pytest.raises(analysis.AnalysisFailed) as failed:
        run["go"]()

    assert run["calls"] == 1
    assert "requests for today ran out" in str(failed.value)


# --- recording, which comes after the charge -----------------------------------


def test_a_missing_price_retries_the_record_not_the_analysis(run):
    """Running the analysis again would charge it twice."""
    run["attempts"] = [{"started_at": None}]
    run["records"] = [None, "signal"]

    assert run["go"]() == "signal"
    assert run["calls"] == 1
    assert run["record_calls"] == 2
    assert run["slept"] == [analysis._RECORD_RETRY_SECONDS]


def test_a_record_that_never_works_says_it_was_charged(run):
    run["attempts"] = [{"started_at": None}]
    run["records"] = [None, None, None]

    with pytest.raises(analysis.AnalysisFailed) as failed:
        run["go"]()

    assert run["calls"] == 1
    assert str(failed.value).startswith("It ran and was charged, but no current price for INTC")


def test_a_record_that_failed_after_writing_is_not_written_twice(run, monkeypatch):
    started = datetime.datetime(2026, 9, 13, 14, 0, tzinfo=datetime.timezone.utc)
    run["attempts"] = [{"started_at": started}]
    run["records"] = [RuntimeError("reports table locked")]
    monkeypatch.setattr(analysis, "_recorded_since", lambda ticker, s: "the stored signal" if s == started else None)

    assert run["go"]() == "the stored signal"
    assert run["record_calls"] == 1


def test_the_stored_run_is_found_by_its_start(monkeypatch):
    started = datetime.datetime(2026, 9, 13, 14, 0, tzinfo=datetime.timezone.utc)
    rows = [SimpleNamespace(created_at=datetime.datetime(2026, 9, 13, 14, 0))]  # naive UTC, as stored
    monkeypatch.setattr(analysis.db, "get_recent_signals", lambda ticker=None, limit=10, by_time=False: rows)

    assert analysis._recorded_since("INTC", started) is rows[0]
    assert analysis._recorded_since("INTC", started + datetime.timedelta(minutes=1)) is None


# --- what reaches the agent ----------------------------------------------------


def test_run_analyses_hands_back_what_stopped_each_ticker(monkeypatch):
    async def fake(ticker, trigger=None):
        if ticker == "SMR":
            raise analysis.AnalysisFailed("The model service refused the request. Nothing was charged.")
        return f"signal:{ticker}"

    monkeypatch.setattr(analysis, "run_analysis_and_record", fake)
    failures = {}

    signals = asyncio.run(analysis.run_analyses(["INTC", "SMR"], failures=failures))

    assert signals == ["signal:INTC"]
    assert failures == {"SMR": "The model service refused the request. Nothing was charged."}


def test_the_bridge_returns_the_failures(monkeypatch):
    async def fake_run_analyses(tickers, on_failure=None, trigger=None, failures=None):
        failures["SMR"] = "reason"
        return []

    async def drive():
        monkeypatch.setattr(scheduler, "_main_loop", asyncio.get_running_loop())
        monkeypatch.setattr(scheduler.analysis, "run_analyses", fake_run_analyses)
        return await asyncio.to_thread(scheduler._research_for_agent, ["INTC", "SMR"])

    assert asyncio.run(drive()) == {"SMR": "reason"}


def test_the_agent_is_told_a_failed_analysis_did_not_finish(monkeypatch):
    monkeypatch.setattr(agent.llm_throttle, "requests_left_today", lambda: None)
    monkeypatch.setattr(agent.research, "get_price", lambda: 0.05)
    monkeypatch.setattr(agent.analysis_reader, "read", lambda ticker, date=None: f"the case for {ticker}")
    agent.set_research_runner(lambda tickers: {"SMR": "The model service refused the request. Nothing was charged."})
    try:
        intc, smr = agent._research_and_report(["INTC", "SMR"])
    finally:
        agent.set_research_runner(None)

    assert "It has finished" in intc
    assert smr.startswith("**SMR: the analysis you ordered did not finish.** The model service refused")
    assert "It has finished" not in smr
    assert "the case for SMR" not in smr
    assert "from an earlier analysis" in smr
