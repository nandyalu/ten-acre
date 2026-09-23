"""Staying inside rate limits that a vendor states in its documentation, not in its responses.

Gemini sends no budget headers, and a free key allows 15 requests a minute and
500 a day. One analysis makes about 20 calls in 70 seconds, so waiting for
refusals alone would collect one on every analysis. A deployment states the
limits, and the app waits before a call that would pass a minute limit and
refuses a call past the day's.

Unset, nothing changes: a local pool must never wait for a limit nobody stated.
"""
import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from backend.services import agent, llm_throttle

_VARS = (
    "LLM_REQUESTS_PER_MINUTE", "LLM_TOKENS_PER_MINUTE", "LLM_REQUESTS_PER_DAY", "LLM_DAY_TIMEZONE",
    "AGENT_LLM_REQUESTS_PER_MINUTE", "AGENT_LLM_TOKENS_PER_MINUTE", "AGENT_LLM_REQUESTS_PER_DAY",
    "AGENT_DECISION_MODEL",
)


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    """A clock that moves only when the throttle sleeps, and a day count kept
    in a dict instead of the database."""
    for name in _VARS:
        monkeypatch.delenv(name, raising=False)
    llm_throttle.reset()
    state = {"now": 1000.0, "slept": [], "store": {}}

    def sleep(seconds):
        state["slept"].append(seconds)
        state["now"] += seconds

    monkeypatch.setattr(llm_throttle.time, "monotonic", lambda: state["now"])
    monkeypatch.setattr(llm_throttle.time, "sleep", sleep)
    # The shared bucket's count is state["store"]; any other bucket's is
    # state["buckets"][name], each shaped as the setting is: {"day", "count"}.
    state["buckets"] = {None: state["store"]}

    def load(day, bucket=None):
        stored = state["buckets"].setdefault(bucket, {})
        return stored.get("count", 0) if stored.get("day") == day else 0

    monkeypatch.setattr(llm_throttle, "_load_day_count", load)
    monkeypatch.setattr(
        llm_throttle, "_save_day_count",
        lambda day, count, bucket=None: state["buckets"].setdefault(bucket, {}).update(day=day, count=count),
    )
    yield state
    llm_throttle.reset()


def _call(result="ok"):
    calls = []

    def fn(**kwargs):
        calls.append(kwargs)
        return result

    return llm_throttle.throttled(fn), calls


# --- nothing stated -------------------------------------------------------------


def test_no_stated_limit_never_waits_and_counts_nothing(clock):
    run, calls = _call()

    for _ in range(100):
        run(messages="x" * 10_000)

    assert len(calls) == 100
    assert clock["slept"] == []
    assert clock["store"] == {}
    assert llm_throttle.requests_left_today() is None


def test_an_unreadable_value_means_no_limit(monkeypatch):
    monkeypatch.setenv("LLM_REQUESTS_PER_MINUTE", "fifteen")

    assert llm_throttle.limits().requests_per_minute is None


# --- per minute -----------------------------------------------------------------


def test_the_call_past_the_minute_limit_waits_for_the_oldest_to_leave(clock, monkeypatch):
    monkeypatch.setenv("LLM_REQUESTS_PER_MINUTE", "2")
    run, calls = _call()

    run()
    clock["now"] += 10
    run()
    run()  # the third in the same minute

    assert len(calls) == 3
    assert len(clock["slept"]) == 1
    # The first call started 10s before the second, so it leaves the window
    # 50s after that.
    assert clock["slept"][0] == pytest.approx(50.05)


def test_the_token_limit_uses_the_vendors_own_count(clock, monkeypatch):
    """The estimate decides only until the call returns. After that the
    vendor's usage replaces it."""
    monkeypatch.setenv("LLM_TOKENS_PER_MINUTE", "1000")
    gemini_answer = SimpleNamespace(usage_metadata=SimpleNamespace(total_token_count=900))
    run, _ = _call(gemini_answer)

    run(contents="short")          # estimated at 1 token, counted at 900
    run(contents="x" * 800)        # 200 more would pass 1000

    assert len(clock["slept"]) == 1


def test_one_call_larger_than_the_token_limit_still_goes_in_an_empty_minute(clock, monkeypatch):
    """Otherwise it would wait forever."""
    monkeypatch.setenv("LLM_TOKENS_PER_MINUTE", "10")
    run, calls = _call()

    run(messages="x" * 10_000)

    assert len(calls) == 1
    assert clock["slept"] == []


# --- per day --------------------------------------------------------------------


def test_the_day_limit_refuses_without_waiting_or_calling(clock, monkeypatch):
    monkeypatch.setenv("LLM_REQUESTS_PER_DAY", "2")
    run, calls = _call()

    run()
    run()
    with pytest.raises(llm_throttle.DailyLimitReached):
        run()

    assert len(calls) == 2
    assert clock["slept"] == []


def test_a_spent_day_is_not_mistaken_for_a_rate_limit(monkeypatch):
    """A refusal to wait out would retry. A spent day must not."""
    assert not llm_throttle.rate_limited(llm_throttle.DailyLimitReached(
        "All 500 model requests for today are spent; the count starts again at midnight in America/Los_Angeles"
    ))


def test_the_day_count_survives_a_restart(clock, monkeypatch):
    monkeypatch.setenv("LLM_REQUESTS_PER_DAY", "500")
    run, _ = _call()
    run()
    run()
    llm_throttle.reset()  # the process starts again

    assert llm_throttle.requests_left_today() == 498


def test_a_new_day_starts_the_count_again(clock, monkeypatch):
    monkeypatch.setenv("LLM_REQUESTS_PER_DAY", "500")
    clock["store"].update(day="2020-01-01", count=500)

    assert llm_throttle.requests_left_today() == 500


def test_the_shortfall_says_when_the_count_starts_again_in_eastern_time(monkeypatch):
    monkeypatch.setenv("LLM_REQUESTS_PER_DAY", "500")
    pacific = ZoneInfo("America/Los_Angeles")
    monkeypatch.setattr(
        llm_throttle, "_now", lambda zone: datetime.datetime(2026, 9, 13, 20, 0, tzinfo=pacific)
    )

    text = llm_throttle.describe_daily_shortfall(12)

    assert "allows 500 requests a day, and 12 are left today" in text
    assert f"about {llm_throttle.REQUESTS_PER_ANALYSIS}" in text
    assert "Monday 14 September at 3:00 AM Eastern" in text


# --- refusals from Gemini -------------------------------------------------------


def test_gemini_names_its_wait_in_the_body():
    json_form = Exception(
        "429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'details': "
        "[{'@type': 'type.googleapis.com/google.rpc.RetryInfo', 'retryDelay': '41s'}]}}"
    )
    proto_form = Exception("Resource exhausted [retry_delay { seconds: 17 }]")

    assert llm_throttle.retry_after(json_form) == 41.0
    assert llm_throttle.retry_after(proto_form) == 17.0


# --- attaching to Gemini --------------------------------------------------------


class _Models:
    def __init__(self):
        self.calls = 0

    def generate_content(self, **kwargs):
        self.calls += 1
        return "answer"


class _GeminiLlm:
    def __init__(self):
        self.client = SimpleNamespace(models=_Models())


def test_gemini_calls_go_through_the_throttle(clock, monkeypatch):
    monkeypatch.setenv("LLM_REQUESTS_PER_DAY", "1")
    llm = _GeminiLlm()
    llm_throttle.attach(llm)

    assert llm.client.models.generate_content(model="m", contents="hi") == "answer"
    with pytest.raises(llm_throttle.DailyLimitReached):
        llm.client.models.generate_content(model="m", contents="hi")
    assert llm.client.models.calls == 1


def test_attaching_to_gemini_twice_wraps_once():
    llm = _GeminiLlm()
    llm_throttle.attach(llm)
    first = llm.client.models.generate_content
    llm_throttle.attach(llm)

    assert llm.client.models.generate_content is first


# --- research that cannot finish ------------------------------------------------


def test_research_that_cannot_finish_today_is_refused_before_it_runs(monkeypatch):
    """An analysis that starts with too few requests fails halfway, after it
    spends them. The agent is told why, and nothing runs or is charged."""
    ran = []
    monkeypatch.setattr(agent.llm_throttle, "requests_left_today", lambda: llm_throttle.REQUESTS_PER_ANALYSIS + 3)
    monkeypatch.setattr(agent.llm_throttle, "describe_daily_shortfall", lambda left: f"{left} left.")
    monkeypatch.setattr(agent.research, "get_price", lambda: 0.05)
    monkeypatch.setattr(agent.analysis_reader, "read", lambda ticker, date=None: "the case")
    agent.set_research_runner(lambda tickers: ran.extend(tickers))
    try:
        lines, failed = agent._research_and_report(["INTC", "SMR"])
    finally:
        agent.set_research_runner(None)

    assert ran == ["INTC"]
    assert lines[0] == f"SMR: not analysed, and nothing was charged. {llm_throttle.REQUESTS_PER_ANALYSIS + 3} left."
    assert "INTC: the analysis YOU ordered" in lines[1]
    assert failed == {"SMR": f"{llm_throttle.REQUESTS_PER_ANALYSIS + 3} left."}


def test_with_no_daily_limit_research_is_not_checked(monkeypatch):
    ran = []
    monkeypatch.setattr(agent.llm_throttle, "requests_left_today", lambda: None)
    monkeypatch.setattr(agent.research, "get_price", lambda: 0.0)
    monkeypatch.setattr(agent.analysis_reader, "read", lambda ticker, date=None: "the case")
    agent.set_research_runner(lambda tickers: ran.extend(tickers))
    try:
        agent._research_and_report(["INTC", "SMR"])
    finally:
        agent.set_research_runner(None)

    assert ran == ["INTC", "SMR"]


# --- a separate decision model (2026-09-23) ---------------------------------------


def _two_models(monkeypatch):
    """Analysis on flash-lite, decisions on 3.8-flash: Google limits each model
    on its own, so each gets its own bucket."""
    monkeypatch.setenv("AGENT_DECISION_MODEL", "gemini-3.8-flash")
    from backend.services import analysis

    monkeypatch.setattr(analysis, "get_model", lambda: "gemini-3.5-flash-lite")


def test_the_decision_model_counts_against_its_own_day(clock, monkeypatch):
    _two_models(monkeypatch)
    monkeypatch.setenv("LLM_REQUESTS_PER_DAY", "500")
    monkeypatch.setenv("AGENT_LLM_REQUESTS_PER_DAY", "1")
    run, calls = _call()

    run(model="gemini-3.8-flash")
    with pytest.raises(llm_throttle.DailyLimitReached):
        run(model="gemini-3.8-flash")
    # The analysis model's day is untouched by the decider's, and goes on.
    run(model="gemini-3.5-flash-lite")

    assert len(calls) == 2
    assert clock["buckets"][llm_throttle.AGENT]["count"] == 1
    assert llm_throttle.requests_left_today() == 499


def test_the_decision_model_keeps_its_own_minute(clock, monkeypatch):
    _two_models(monkeypatch)
    monkeypatch.setenv("LLM_REQUESTS_PER_MINUTE", "15")
    monkeypatch.setenv("AGENT_LLM_REQUESTS_PER_MINUTE", "1")
    run, _ = _call()

    run(model="gemini-3.8-flash")
    run(model="gemini-3.5-flash-lite")  # a different bucket: no wait
    assert clock["slept"] == []
    run(model="models/gemini-3.8-flash")  # the SDK's own spelling, same bucket
    assert len(clock["slept"]) == 1


def test_one_model_named_twice_stays_one_bucket(monkeypatch):
    """The same name for both is one Google limit, so splitting it would let
    the two together pass it."""
    monkeypatch.setenv("AGENT_DECISION_MODEL", "gemini-3.5-flash-lite")
    from backend.services import analysis

    monkeypatch.setattr(analysis, "get_model", lambda: "gemini-3.5-flash-lite")
    assert llm_throttle.bucket_for("gemini-3.5-flash-lite") is None
    monkeypatch.delenv("AGENT_DECISION_MODEL")
    assert llm_throttle.bucket_for("gemini-3.8-flash") is None
