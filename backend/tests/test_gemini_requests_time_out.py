"""Every request to Google has a time limit.

On 2026-09-24 one decide call never returned. Google's SDK waits forever by
default, so the pass held _pass_lock for ten hours, and the last pass before
the close and every alarm after it were skipped. The decide client and the
graph's Google clients (the text fallback and every analysis) now fail after
llm_gemini.REQUEST_TIMEOUT_SECONDS.

Pure — no LLM, no network.
"""
from types import SimpleNamespace

from google.genai import types

from backend.services import analysis, llm_gemini

_MS = int(llm_gemini.REQUEST_TIMEOUT_SECONDS * 1000)


def _capture_client(monkeypatch):
    made = []
    monkeypatch.setattr(llm_gemini, "_client", None)
    monkeypatch.setattr(llm_gemini.llm_throttle, "attach", lambda *a: None)
    monkeypatch.setattr(
        llm_gemini.genai, "Client", lambda **kwargs: made.append(kwargs) or SimpleNamespace()
    )
    return made


def _capture_config(monkeypatch):
    seen = []
    monkeypatch.setattr(
        analysis, "TradingAgentsGraph",
        lambda config: seen.append(config) or SimpleNamespace(),
    )
    monkeypatch.setitem(analysis.DEFAULT_CONFIG, "llm_timeout", None)
    return seen


# --- the decide client ----------------------------------------------------------


def test_the_decide_client_has_a_timeout(monkeypatch):
    made = _capture_client(monkeypatch)
    monkeypatch.setattr(llm_gemini, "http_options", None)

    llm_gemini.client()

    assert made[0]["http_options"].timeout == _MS


def test_options_a_caller_set_keep_the_timeout(monkeypatch):
    """The probe sets its own retry options. They must not remove the limit."""
    made = _capture_client(monkeypatch)
    monkeypatch.setattr(
        llm_gemini, "http_options",
        types.HttpOptions(retry_options=types.HttpRetryOptions(attempts=1)),
    )

    llm_gemini.client()

    options = made[0]["http_options"]
    assert options.timeout == _MS
    assert options.retry_options.attempts == 1


def test_a_timeout_a_caller_set_is_kept(monkeypatch):
    made = _capture_client(monkeypatch)
    monkeypatch.setattr(llm_gemini, "http_options", types.HttpOptions(timeout=1234))

    llm_gemini.client()

    assert made[0]["http_options"].timeout == 1234


# --- the graph's clients: the text fallback and every analysis ------------------


def test_a_google_graph_has_a_timeout(monkeypatch):
    seen = _capture_config(monkeypatch)

    analysis._build_graph("gemini-3.5-flash-lite", provider="google")

    assert seen[0]["llm_timeout"] == llm_gemini.REQUEST_TIMEOUT_SECONDS


def test_a_configured_timeout_wins(monkeypatch):
    seen = _capture_config(monkeypatch)
    monkeypatch.setitem(analysis.DEFAULT_CONFIG, "llm_timeout", "90")

    analysis._build_graph("gemini-3.5-flash-lite", provider="google")

    assert seen[0]["llm_timeout"] == "90"


def test_another_provider_keeps_its_own_default(monkeypatch):
    """The local pool queues a request for up to ten minutes on purpose."""
    seen = _capture_config(monkeypatch)

    analysis._build_graph("gemma4-e4b-qat-128k", provider="ollama")

    assert seen[0]["llm_timeout"] is None
