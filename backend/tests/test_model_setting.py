"""The selectable analysis model.

Which LLM an analysis runs on is a setting rather than only an env var, so a
new model can be tried without a redeploy. Two things have to hold for that to
be safe: an endpoint that can't be reached must never block a save or lose the
current model, and the model a run used must reach the recorded signal even if
the setting changes mid-run.

No LLM and no database: ``db.get_setting``/``set_setting`` are replaced with a
dict, and the model list with a fake HTTP response.
"""
import io
import json

import pytest

from backend.services import analysis


@pytest.fixture
def settings_store(monkeypatch):
    store: dict[str, str] = {}
    monkeypatch.setattr(analysis.db, "get_setting", lambda key: store.get(key))
    monkeypatch.setattr(analysis.db, "set_setting", lambda key, value: store.update({key: value}))
    return store


@pytest.fixture(autouse=True)
def clear_model_cache(monkeypatch):
    monkeypatch.setattr(analysis, "_model_list_cache", (0.0, []))
    monkeypatch.setitem(analysis.DEFAULT_CONFIG, "llm_provider", "ollama")


def _fake_urlopen(models=(), error=None):
    """Stands in for urllib.request.urlopen, returning an OpenAI-style
    /models body (which is what ollama serves too)."""

    class _Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.close()

    def urlopen(url, timeout=None):
        if error is not None:
            raise error
        body = {"data": [{"id": name} for name in models]}
        return _Response(json.dumps(body).encode())

    return urlopen


def test_unset_setting_uses_the_stacks_configured_model(settings_store):
    assert analysis.get_model() == analysis.DEFAULT_MODEL


def test_set_model_accepts_a_model_the_endpoint_serves(settings_store, monkeypatch):
    monkeypatch.setattr(analysis.urllib.request, "urlopen", _fake_urlopen(["a:latest", "b:latest"]))

    analysis.set_model("b:latest")

    assert analysis.get_model() == "b:latest"


def test_set_model_rejects_a_model_the_endpoint_does_not_serve(settings_store, monkeypatch):
    monkeypatch.setattr(analysis.urllib.request, "urlopen", _fake_urlopen(["a:latest"]))

    with pytest.raises(ValueError, match="pull it there first"):
        analysis.set_model("typo:latest")

    assert analysis.get_model() == analysis.DEFAULT_MODEL


def test_an_unreachable_endpoint_does_not_block_a_save(settings_store, monkeypatch):
    """An empty list means "couldn't ask", not "no models exist" — treating it
    as the latter would make the setting unchangeable exactly when the pool is
    having a bad day."""
    monkeypatch.setattr(analysis.urllib.request, "urlopen", _fake_urlopen(error=OSError("down")))

    analysis.set_model("something:latest")

    assert analysis.get_model() == "something:latest"


def test_a_bare_name_matches_its_latest_tag(settings_store, monkeypatch):
    """The deployed default is written without a tag; ollama reports it with
    one. Rejecting it would make the endpoint refuse the model it is running."""
    monkeypatch.setattr(analysis.urllib.request, "urlopen", _fake_urlopen(["gemma4-e2b-96k:latest"]))

    analysis.set_model("gemma4-e2b-96k")

    assert analysis.get_model() == "gemma4-e2b-96k"


def test_the_current_model_is_always_offered(settings_store, monkeypatch):
    """A dropdown with no option matching the current value shows some other
    model as if it were the one running."""
    monkeypatch.setattr(analysis.urllib.request, "urlopen", _fake_urlopen(["a:latest"]))
    settings_store[analysis._MODEL_SETTING_KEY] = "elsewhere:latest"

    assert "elsewhere:latest" in analysis.model_choices()


def test_the_current_spelling_replaces_the_endpoints_tagged_one(settings_store, monkeypatch):
    monkeypatch.setattr(
        analysis.urllib.request, "urlopen", _fake_urlopen(["a:latest", "gemma4-e2b-96k:latest"])
    )
    settings_store[analysis._MODEL_SETTING_KEY] = "gemma4-e2b-96k"

    choices = analysis.model_choices()

    assert choices == ["a:latest", "gemma4-e2b-96k"]


def test_model_choices_is_empty_when_the_endpoint_is_unreachable(settings_store, monkeypatch):
    """Empty means "couldn't ask" all the way to the UI, which then shows a text
    field instead of a dropdown of one."""
    monkeypatch.setattr(analysis.urllib.request, "urlopen", _fake_urlopen(error=OSError("down")))

    assert analysis.model_choices() == []


def test_list_models_returns_empty_when_the_endpoint_is_unreachable(monkeypatch):
    monkeypatch.setattr(analysis.urllib.request, "urlopen", _fake_urlopen(error=OSError("down")))

    assert analysis.list_models() == []


def test_list_models_survives_a_malformed_body(monkeypatch):
    monkeypatch.setattr(
        analysis.urllib.request, "urlopen", _fake_urlopen(error=ValueError("not json"))
    )

    assert analysis.list_models() == []


def test_list_models_is_cached_between_calls(monkeypatch):
    calls = []
    inner = _fake_urlopen(["a:latest"])

    def counting_urlopen(url, timeout=None):
        calls.append(url)
        return inner(url, timeout)

    monkeypatch.setattr(analysis.urllib.request, "urlopen", counting_urlopen)

    assert analysis.list_models() == ["a:latest"]
    assert analysis.list_models() == ["a:latest"]
    assert len(calls) == 1


def test_a_failed_refresh_keeps_the_last_known_list(monkeypatch):
    monkeypatch.setattr(analysis.urllib.request, "urlopen", _fake_urlopen(["a:latest"]))
    analysis.list_models()

    monkeypatch.setattr(analysis.urllib.request, "urlopen", _fake_urlopen(error=OSError("down")))
    assert analysis.list_models(force=True) == ["a:latest"]


def test_the_graph_runs_on_the_selected_model_at_both_think_stages(settings_store, monkeypatch):
    captured = {}
    monkeypatch.setattr(analysis, "TradingAgentsGraph", lambda config: captured.update(config))
    settings_store[analysis._MODEL_SETTING_KEY] = "chosen:latest"

    analysis._build_graph()

    assert captured["deep_think_llm"] == "chosen:latest"
    assert captured["quick_think_llm"] == "chosen:latest"


def test_building_a_graph_does_not_mutate_the_shared_default_config(settings_store, monkeypatch):
    monkeypatch.setattr(analysis, "TradingAgentsGraph", lambda config: config)
    settings_store[analysis._MODEL_SETTING_KEY] = "chosen:latest"

    analysis._build_graph()

    assert analysis.DEFAULT_CONFIG["deep_think_llm"] == analysis.DEFAULT_MODEL


# --- listing models from a metered endpoint -------------------------------------


def test_the_model_list_is_authenticated_and_not_sent_as_urllib(monkeypatch):
    """Two separate things break a metered provider's /models call, and both
    were found on 2026-09-09 pointing at Cerebras.

    The key is the obvious one: ollama needs none, so none was ever sent. The
    User-Agent is the one that costs an hour — urllib's default is
    "Python-urllib/3.x" and Cloudflare, which fronts Cerebras, refuses it
    outright. The identical request returned 403 as urllib and 200 as curl,
    with nothing wrong with the key at all.
    """
    from tradingagents.default_config import DEFAULT_CONFIG
    from backend.services import analysis

    monkeypatch.setitem(DEFAULT_CONFIG, "llm_provider", "openai_compatible")
    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "csk-secret")

    headers = analysis._models_auth_header()

    assert headers["Authorization"] == "Bearer csk-secret"
    assert "urllib" not in headers["User-Agent"].lower()


def test_a_keyless_endpoint_still_identifies_itself(monkeypatch):
    """ollama needs no key, and must not be sent an empty Bearer — but it
    still gets a User-Agent, since nothing is gained by looking like urllib."""
    from tradingagents.default_config import DEFAULT_CONFIG
    from backend.services import analysis

    monkeypatch.setitem(DEFAULT_CONFIG, "llm_provider", "ollama")
    monkeypatch.delenv("OPENAI_COMPATIBLE_API_KEY", raising=False)

    headers = analysis._models_auth_header()

    assert "Authorization" not in headers
    assert headers["User-Agent"]
