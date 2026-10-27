"""API key redaction helpers."""

from __future__ import annotations

import pytest


def test_mask_secret_empty():
    from src.secrets import mask_secret

    assert mask_secret(None) == ""
    assert mask_secret("") == ""


def test_mask_secret_short():
    from src.secrets import mask_secret

    assert mask_secret("short") == "****"
    assert "short" not in mask_secret("short")
    mask_secret("short").encode("ascii")


def test_mask_secret_long_keeps_last_four():
    from src.secrets import mask_secret

    out = mask_secret("sk-abcdefghijklmnop")
    assert out.endswith("mnop")
    assert "sk-abcdef" not in out
    assert out.startswith("****")
    out.encode("ascii")


def test_is_masked_secret():
    from src.secrets import is_masked_secret, mask_secret

    assert is_masked_secret(mask_secret("sk-abcdefghijklmnop"))
    assert is_masked_secret("****")
    assert is_masked_secret("••••xxxx")  # leftover from earlier mask
    assert not is_masked_secret("sk-abcdefghijklmnop")
    assert not is_masked_secret("")
    assert not is_masked_secret(None)


def test_effective_secret_ignores_mask():
    from src.secrets import effective_secret, mask_secret

    stored = "sk-real-secret-key"
    assert effective_secret(mask_secret(stored), stored) == stored
    assert effective_secret("••••abcd", stored) == stored
    assert effective_secret("", stored) == stored
    assert effective_secret(None, stored) == stored
    assert effective_secret("sk-new-key-value", stored) == "sk-new-key-value"


def test_redact_mapping_nested_copy():
    from src.secrets import redact_mapping

    raw = {
        "api_key": "sk-supersecretkey",
        "embedding_api_key": "emb-1234567890",
        "nested": {"rerank_api_key": "rk-abcdefghij"},
        "name": "keep",
    }
    out = redact_mapping(raw)
    assert raw["api_key"] == "sk-supersecretkey"
    assert "sk-supersecretkey" not in str(out)
    assert out["name"] == "keep"
    assert out["api_key"].startswith("****")
    assert out["nested"]["rerank_api_key"].startswith("****")


def test_get_current_config_redacts_keys(monkeypatch):
    from src.api.routes import config as cfg_routes
    from src.config import AppConfig, LLMConfig, LLMProviderConfig

    cfg = AppConfig(
        llm=LLMConfig(
            providers=[
                LLMProviderConfig(id="p1", name="n", api_key="sk-abcdefghijklmnop")
            ]
        )
    )
    monkeypatch.setattr(cfg_routes, "get_config", lambda: cfg)
    data = cfg_routes.get_current_config()
    dumped = str(data)
    assert "sk-abcdefghijklmnop" not in dumped
    assert data["llm"]["providers"][0]["api_key"].startswith("****")


def test_request_api_key_skips_mask_and_matches_base_url():
    from types import SimpleNamespace

    from src.api.routes.config import _request_api_key

    stored_default = "sk-default-key"
    card = SimpleNamespace(base_url="https://api.example.com/v1", api_key="sk-card-key")
    got = _request_api_key(
        {"api_key": "••••wxyz", "base_url": card.base_url},
        stored_default,
        providers=[card],
        base_url=card.base_url,
    )
    assert got == "sk-card-key"


def test_write_text_atomic_roundtrip(tmp_path):
    from src.atomic_io import write_text_atomic

    target = tmp_path / "meta.json"
    write_text_atomic(target, '{"ok": true}')
    assert target.read_text() == '{"ok": true}'
    assert not target.with_suffix(".json.tmp").exists()
