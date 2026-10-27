from types import SimpleNamespace
from unittest.mock import MagicMock

from src.services import _resolve_chat_llm


def _p(**kwargs):
    data = {
        "id": "p1",
        "is_default": False,
        "default_model": "chat-a",
        "model": "chat-a",
        "function_call_model_ids": ["chat-a"],
        "visual_model_ids": [],
    }
    data.update(kwargs)
    return SimpleNamespace(**data)


def test_chat_llm_uses_provider_pipe_model(monkeypatch):
    a = _p(id="dash", function_call_model_ids=["qwen-plus"], default_model="qwen-plus")
    b = _p(id="or", function_call_model_ids=["qwen-plus"], default_model="qwen-plus")
    created = {}

    def _create(p, model=None):
        created["id"] = p.id
        created["model"] = model
        return MagicMock()

    monkeypatch.setattr("src.providers.llm.create_llm_for_provider", _create)
    cfg = SimpleNamespace(
        llm=SimpleNamespace(providers=[a, b]),
        default_chat_model="or|qwen-plus",
    )
    assert _resolve_chat_llm(cfg) is not None
    assert created["id"] == "or"
    assert created["model"] == "qwen-plus"


def test_chat_llm_legacy_bare_name_takes_first(monkeypatch):
    a = _p(id="dash", function_call_model_ids=["qwen-plus"], default_model="qwen-plus")
    b = _p(id="or", function_call_model_ids=["qwen-plus"], default_model="qwen-plus")
    created = {}

    def _create(p, model=None):
        created["id"] = p.id
        created["model"] = model
        return MagicMock()

    monkeypatch.setattr("src.providers.llm.create_llm_for_provider", _create)
    cfg = SimpleNamespace(
        llm=SimpleNamespace(providers=[a, b]),
        default_chat_model="qwen-plus",
    )
    assert _resolve_chat_llm(cfg) is not None
    assert created["id"] == "dash"
