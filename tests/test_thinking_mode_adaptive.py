"""Adaptive thinking-mode handling for OpenAI-compatible LLM providers.

Models differ in thinking posture:
  - "toggle": enable_thinking / thinking-disabled works (qwen3.7 …)
  - "always": rejects thinking-off (百炼第三方智谱、qwen3.8 …) — the official
    guidance is to use reasoning_effort (low/high/max) instead
  - "none": rejects the thinking parameters entirely (OpenAI …)

Posture is probed once at provider save time and persisted on the provider
config; a runtime retry on the always-think 400 is the fallback for models
the probe could not classify.

Run: pytest tests/test_thinking_mode_adaptive.py -v --tb=short
"""

from __future__ import annotations

from types import SimpleNamespace

from src.config import LLMProviderConfig

import pytest


@pytest.fixture(autouse=True)
def _fresh_learned_modes(monkeypatch):
    """Isolate the process-level learned-posture cache between tests."""
    from src.providers.llm import thinking as thinking_mod

    monkeypatch.setattr(thinking_mod, "_LEARNED_MODES", {})


ALWAYS_THINK_ERR = (
    "Error code: 400 - {'error': {'message': '该模型始终思考，不支持关闭思考；"
    "请使用 low、high 或 max。', 'type': None, 'param': None, 'code': '1210'}, "
    "'request_id': '62bdf9ff-98e1-9b75-96ba-828fdd482417'}"
)
UNSUPPORTED_PARAM_ERR = (
    "Error code: 400 - {'error': {'message': 'Unrecognized request argument "
    "supplied: enable_thinking'}}"
)
AUTH_ERR = "Error code: 401 - Invalid API key"

DASHSCOPE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


# ══════════════ config field ══════════════


def test_llm_provider_config_has_thinking_mode():
    assert LLMProviderConfig().thinking_mode == ""
    assert LLMProviderConfig(thinking_mode="always").thinking_mode == "always"


# ══════════════ error classification ══════════════


def test_is_always_think_error_matches_user_reported_error():
    from src.providers.llm.thinking import is_always_think_error

    assert is_always_think_error(RuntimeError(ALWAYS_THINK_ERR)) is True
    assert is_always_think_error(RuntimeError("This model always thinks and cannot disable thinking")) is True
    assert is_always_think_error(RuntimeError(AUTH_ERR)) is False
    assert is_always_think_error(RuntimeError("Error code: 400 - bad request")) is False


# ══════════════ probe ══════════════


class _StubCompletions:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _StubClient:
    def __init__(self, outcomes):
        self.chat = SimpleNamespace(completions=_StubCompletions(outcomes))


def _patch_probe_client(monkeypatch, outcomes):
    from src.providers.llm import thinking as thinking_mod

    stub = _StubClient(outcomes)
    monkeypatch.setattr(thinking_mod, "OpenAI", lambda **kw: stub)
    return stub


def test_probe_classifies_toggle(monkeypatch):
    from src.providers.llm.thinking import probe_thinking_mode

    stub = _patch_probe_client(monkeypatch, [SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))])])
    assert probe_thinking_mode(DASHSCOPE_URL, "sk-x", "m") == "toggle"
    # The probe must ask with thinking OFF — that is the capability in question.
    assert stub.chat.completions.calls[0]["extra_body"] == {"enable_thinking": False}
    assert stub.chat.completions.calls[0]["max_tokens"] == 1


def test_probe_classifies_always(monkeypatch):
    from src.providers.llm.thinking import probe_thinking_mode

    _patch_probe_client(monkeypatch, [RuntimeError(ALWAYS_THINK_ERR)])
    assert probe_thinking_mode(DASHSCOPE_URL, "sk-x", "m") == "always"


def test_probe_classifies_none_when_param_rejected(monkeypatch):
    from src.providers.llm.thinking import probe_thinking_mode

    _patch_probe_client(monkeypatch, [RuntimeError(UNSUPPORTED_PARAM_ERR)])
    assert probe_thinking_mode(DASHSCOPE_URL, "sk-x", "m") == "none"


def test_probe_unknown_on_auth_error(monkeypatch):
    from src.providers.llm.thinking import probe_thinking_mode

    _patch_probe_client(monkeypatch, [RuntimeError(AUTH_ERR)])
    assert probe_thinking_mode(DASHSCOPE_URL, "sk-x", "m") == ""


# ══════════════ runtime kwargs per mode ══════════════


def _provider(mode: str = ""):
    from src.providers.llm.openai_compat import OpenAICompatLLM

    return OpenAICompatLLM(
        LLMProviderConfig(
            base_url=DASHSCOPE_URL, api_key="sk-x", model="zhipu-glm",
            thinking_mode=mode,
        )
    )


def test_apply_thinking_always_uses_reasoning_effort_low_for_off():
    p = _provider("always")
    kwargs: dict = {"model": "zhipu-glm", "messages": []}
    p._apply_thinking_kwargs(kwargs, thinking=False)
    assert "extra_body" not in kwargs
    assert kwargs["reasoning_effort"] == "low"


def test_apply_thinking_always_maps_effort_for_on():
    p = _provider("always")
    kwargs: dict = {"model": "zhipu-glm", "messages": []}
    p._apply_thinking_kwargs(kwargs, thinking=True, effort="high")
    assert "extra_body" not in kwargs
    assert kwargs["reasoning_effort"] == "max"


def test_apply_thinking_none_sends_nothing():
    p = _provider("none")
    kwargs: dict = {"model": "zhipu-glm", "messages": []}
    p._apply_thinking_kwargs(kwargs, thinking=False)
    assert "extra_body" not in kwargs
    assert "reasoning_effort" not in kwargs


def test_apply_thinking_toggle_keeps_existing_shape():
    p = _provider("toggle")
    kwargs: dict = {"model": "zhipu-glm", "messages": []}
    p._apply_thinking_kwargs(kwargs, thinking=False)
    assert kwargs["extra_body"] == {"enable_thinking": False}


# ══════════════ runtime fallback retry (probe could not classify) ══════════════


def test_generate_retries_and_learns_always_mode():
    p = _provider("")
    ok = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="answer"))])
    stub = _StubClient([RuntimeError(ALWAYS_THINK_ERR), ok])
    p._client = stub

    out = p.generate("hi", thinking=False)

    assert out == "answer"
    assert stub.chat.completions.calls[0]["extra_body"] == {"enable_thinking": False}
    assert "extra_body" not in stub.chat.completions.calls[1]
    assert stub.chat.completions.calls[1]["reasoning_effort"] == "low"
    assert p._thinking_mode == "always"


def test_generate_does_not_retry_on_unrelated_error():
    p = _provider("")
    stub = _StubClient([RuntimeError(AUTH_ERR)])
    p._client = stub

    try:
        p.generate("hi", thinking=False)
        raised = False
    except RuntimeError:
        raised = True
    assert raised is True
    assert len(stub.chat.completions.calls) == 1
    assert p._thinking_mode == ""


# ══════════════ per-model learned cache (slot instances are ephemeral) ══════════════


def test_learned_mode_shared_across_instances(monkeypatch):
    from src.providers.llm import thinking as thinking_mod

    monkeypatch.setattr(thinking_mod, "_LEARNED_MODES", {})
    ok = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="a"))])
    p1 = _provider("")
    p1._client = _StubClient([RuntimeError(ALWAYS_THINK_ERR), ok])
    p1.generate("hi", thinking=False)
    assert p1._thinking_mode == "always"

    # A fresh instance for the same endpoint+model (e.g. a slot override)
    # starts adapted instead of paying the failed request again.
    p2 = _provider("")
    assert p2._thinking_mode == "always"
    kwargs: dict = {"model": "zhipu-glm", "messages": []}
    p2._apply_thinking_kwargs(kwargs, thinking=False)
    assert kwargs.get("reasoning_effort") == "low"


def test_config_mode_used_when_nothing_learned(monkeypatch):
    from src.providers.llm import thinking as thinking_mod

    monkeypatch.setattr(thinking_mod, "_LEARNED_MODES", {})
    p = _provider("toggle")
    assert p._thinking_mode == "toggle"


# ══════════════ agent streaming retry ══════════════


def test_agent_retry_handles_always_think_error():
    from src.chatbox.agent import _retry_llm_kwargs_after_error

    llm = _provider("")
    kwargs = {
        "model": "zhipu-glm",
        "extra_body": {"enable_thinking": False},
        "tool_choice": {"type": "function", "function": {"name": "f"}},
    }
    out = _retry_llm_kwargs_after_error(kwargs, RuntimeError(ALWAYS_THINK_ERR), llm=llm)

    assert out is not None
    assert "extra_body" not in out
    assert out["reasoning_effort"] == "low"
    assert out["tool_choice"] == "auto"
    assert llm._thinking_mode == "always"


# ══════════════ save-time probe wiring ══════════════


def _patch_llm_route_env(monkeypatch, providers):
    import src.api.routes.config as config_routes

    cfg = SimpleNamespace(llm=SimpleNamespace(providers=providers))
    monkeypatch.setattr(config_routes, "get_config", lambda: cfg)
    monkeypatch.setattr(config_routes, "save_config", lambda c: None)
    monkeypatch.setattr(config_routes, "reload_config", lambda: None)

    async def _no_refresh():
        return None

    monkeypatch.setattr(config_routes, "async_refresh_llm_runtime", _no_refresh)
    return cfg


def test_create_llm_provider_probes_and_persists_mode(monkeypatch):
    import src.api.routes.config as config_routes

    _patch_llm_route_env(monkeypatch, [])
    monkeypatch.setattr(
        config_routes, "_probe_provider_thinking",
        lambda p: "always", raising=False,
    )

    from fastapi.testclient import TestClient

    from src.main import app

    resp = TestClient(app).post(
        "/api/llm/providers",
        json={
            "name": "Zhipu", "provider": "openai_compatible",
            "model": "glm-x", "base_url": DASHSCOPE_URL, "api_key": "sk-x",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["thinking_mode"] == "always"


def test_update_llm_provider_reprobes_on_model_change(monkeypatch):
    import src.api.routes.config as config_routes

    existing = LLMProviderConfig(
        id="p1", name="Zhipu", model="glm-old",
        base_url=DASHSCOPE_URL, api_key="sk-x", is_default=True,
        thinking_mode="toggle",
    )
    _patch_llm_route_env(monkeypatch, [existing])
    monkeypatch.setattr(
        config_routes, "_probe_provider_thinking",
        lambda p: "always", raising=False,
    )

    from fastapi.testclient import TestClient

    from src.main import app

    resp = TestClient(app).put(
        "/api/llm/providers/p1",
        json={"model": "glm-new"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["thinking_mode"] == "always"
