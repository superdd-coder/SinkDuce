"""Remote shell（远程模式瘦壳）运行时信号与配置字段。

对应 development_doc/2026-09-21-remote-mode-design.md §5：
- ``AppConfig.remote_mode`` 默认 False，旧 config.yaml 无该字段时迁移不丢；
- ``SINKDUCE_REMOTE_SHELL`` 环境变量 → ``is_remote_shell_runtime()``；
- ``health_payload()`` 仅在瘦壳运行时追加 ``remote_shell: True``（正常模式
  不出现该键 —— 前端靠「键存在」识别模式，默认关闭必须零差异）。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from src.config import (
    AppConfig,
    health_payload,
    is_remote_shell_runtime,
    load_config,
    save_config,
)


# ── 配置字段 ───────────────────────────────────────────────


def test_app_config_remote_mode_defaults_false():
    cfg = AppConfig()
    assert cfg.remote_mode is False


def test_load_config_remote_mode_missing_defaults_false():
    config_content = """
llm:
  providers: []
embedding:
  providers: []
rerank:
  providers: []
qdrant:
  host: localhost
  port: 6333
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(config_content)
        f.flush()
        assert load_config(f.name).remote_mode is False


def test_load_config_remote_mode_true():
    config_content = """
remote_mode: true
locale: zh-CN
llm:
  providers: []
embedding:
  providers: []
rerank:
  providers: []
qdrant:
  host: localhost
  port: 6333
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(config_content)
        f.flush()
        cfg = load_config(f.name)
        assert cfg.remote_mode is True
        assert cfg.locale == "zh-CN"


def test_save_config_roundtrip_preserves_remote_mode(tmp_path: Path):
    path = tmp_path / "config.yaml"
    cfg = AppConfig()
    cfg.remote_mode = True
    save_config(cfg, path)
    assert load_config(path).remote_mode is True

    cfg.remote_mode = False
    save_config(cfg, path)
    assert load_config(path).remote_mode is False


# ── 运行时信号 ─────────────────────────────────────────────


def test_remote_shell_runtime_env_gated(monkeypatch):
    monkeypatch.delenv("SINKDUCE_REMOTE_SHELL", raising=False)
    assert is_remote_shell_runtime() is False

    for value in ("1", "true", "yes"):
        monkeypatch.setenv("SINKDUCE_REMOTE_SHELL", value)
        assert is_remote_shell_runtime() is True

    monkeypatch.setenv("SINKDUCE_REMOTE_SHELL", "0")
    assert is_remote_shell_runtime() is False


# ── /api/config 的 remote_mode section（正常模式设置页开关的写入口）──────


def test_config_api_accepts_remote_mode_section(tmp_path, monkeypatch):
    """PUT /api/config section=remote_mode 必须落盘（顶层 bool 字段）。

    顶层 bool 不属于任何嵌套 section：若不专门处理，update_config 会拿
    ``getattr(config, "remote_mode")``（= False）去取 ``model_fields`` 而崩。
    """
    monkeypatch.setattr("src.config.CONFIG_PATH", tmp_path / "config.yaml")

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from src.api.routes.config import router as config_router

    app = FastAPI()
    app.include_router(config_router, prefix="/api")
    client = TestClient(app)

    res = client.put(
        "/api/config", json={"section": "remote_mode", "data": {"remote_mode": True}}
    )
    assert res.status_code == 200, res.text
    on_disk = (tmp_path / "config.yaml").read_text(encoding="utf-8")
    assert "remote_mode: true" in on_disk

    res = client.put(
        "/api/config", json={"section": "remote_mode", "data": {"remote_mode": False}}
    )
    assert res.status_code == 200
    on_disk = (tmp_path / "config.yaml").read_text(encoding="utf-8")
    assert "remote_mode: false" in on_disk


# ── health 标志 ────────────────────────────────────────────


def test_health_payload_omits_remote_shell_outside_shell_mode(monkeypatch):
    monkeypatch.delenv("SINKDUCE_REMOTE_SHELL", raising=False)
    monkeypatch.delenv("SINKDUCE_DESKTOP", raising=False)
    monkeypatch.delenv("SINKDUCE_PORT", raising=False)
    monkeypatch.delenv("SINKDUCE_HOST", raising=False)
    assert "remote_shell" not in health_payload()


def test_health_payload_reports_remote_shell_in_shell_mode(monkeypatch):
    monkeypatch.setenv("SINKDUCE_REMOTE_SHELL", "1")
    monkeypatch.setenv("SINKDUCE_DESKTOP", "1")
    monkeypatch.setenv("SINKDUCE_HOST", "127.0.0.1")
    monkeypatch.setenv("SINKDUCE_PORT", "18910")
    payload = health_payload()
    assert payload["remote_shell"] is True
    assert payload["desktop"] is True
