"""本机/对外双监听端口（CONN-6 身份模型）。

端口分工（2026-09-15 定稿）：
- 主端口 `api_port`（默认 18900）= **本机入口**。容器里宿主机只把它发布到回环，
  所以「落在它上面」= 请求来自盒子本机；本机前端经它静默领取主人钥匙。
- 对外端口 `external_port`（默认 18901）= **局域网入口**，需钥匙。

桌面 App / 裸机形态只有主端口一个监听：客户端来源即回环，天然是本机，不需要第二个口。
"""

from __future__ import annotations

import socket

import pytest


# ── 监听计划（纯函数）────────────────────────────────────────────


class TestPlanListeners:
    def test_container_gets_external_listener(self):
        from src.main import plan_listeners

        assert plan_listeners("0.0.0.0", 18900, 18901, add_external=True) == [
            ("0.0.0.0", 18900),
            ("0.0.0.0", 18901),
        ]

    def test_non_container_keeps_single_listener(self):
        # 桌面/裸机：回环客户端地址即可认本机，不开第二个口
        from src.main import plan_listeners

        assert plan_listeners("127.0.0.1", 18910, 18901, add_external=False) == [("127.0.0.1", 18910)]
        assert plan_listeners("0.0.0.0", 18910, 18901, add_external=False) == [("0.0.0.0", 18910)]

    def test_no_external_listener_when_ports_collide(self):
        from src.main import plan_listeners

        assert plan_listeners("0.0.0.0", 18900, 18900, add_external=True) == [("0.0.0.0", 18900)]

    def test_no_external_listener_when_disabled(self):
        from src.main import plan_listeners

        assert plan_listeners("0.0.0.0", 18900, 0, add_external=True) == [("0.0.0.0", 18900)]

    def test_loopback_main_host_needs_no_external_listener(self):
        from src.main import plan_listeners

        assert plan_listeners("127.0.0.1", 18900, 18901, add_external=True) == [("127.0.0.1", 18900)]


# ── 容器判定 ────────────────────────────────────────────────────


class TestInContainer:
    def test_detects_docker_marker(self, monkeypatch, tmp_path):
        import src.config as cfg

        marker = tmp_path / ".dockerenv"
        marker.write_text("", encoding="utf-8")
        monkeypatch.setattr(cfg, "_CONTAINER_MARKERS", (marker,))
        monkeypatch.delenv("SINKDUCE_DESKTOP", raising=False)
        monkeypatch.delenv("SINKDUCE_CONTAINER", raising=False)
        assert cfg.in_container() is True

    def test_env_flag_forces_container(self, monkeypatch):
        import src.config as cfg

        monkeypatch.delenv("SINKDUCE_DESKTOP", raising=False)
        monkeypatch.setenv("SINKDUCE_CONTAINER", "1")
        assert cfg.in_container() is True

    def test_desktop_runtime_is_never_container(self, monkeypatch):
        import src.config as cfg

        monkeypatch.setenv("SINKDUCE_DESKTOP", "1")
        monkeypatch.setenv("SINKDUCE_CONTAINER", "1")
        assert cfg.in_container() is False

    def test_no_markers_no_env_is_not_container(self, monkeypatch):
        import src.config as cfg

        monkeypatch.delenv("SINKDUCE_DESKTOP", raising=False)
        monkeypatch.delenv("SINKDUCE_CONTAINER", raising=False)
        monkeypatch.setattr(cfg, "_CONTAINER_MARKERS", ())
        assert cfg.in_container() is False


# ── 对外端口解析 ────────────────────────────────────────────────


class TestResolveExternalPort:
    def test_default_from_config(self, monkeypatch):
        from src.config import resolve_external_port

        monkeypatch.delenv("SINKDUCE_EXTERNAL_PORT", raising=False)
        assert resolve_external_port(18901) == 18901

    def test_env_overrides(self, monkeypatch):
        from src.config import resolve_external_port

        monkeypatch.setenv("SINKDUCE_EXTERNAL_PORT", "19999")
        assert resolve_external_port(18901) == 19999


# ── 真实绑定 ────────────────────────────────────────────────────


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TestBuildListeners:
    def test_binds_every_planned_listener(self):
        from src.main import build_listeners

        socks = build_listeners([("127.0.0.1", _free_port()), ("127.0.0.1", _free_port())])
        try:
            assert len(socks) == 2
            for s in socks:
                assert s.getsockname()[1] > 0
        finally:
            for s in socks:
                s.close()

    def test_secondary_conflict_degrades_without_crashing(self):
        from src.main import build_listeners

        port = _free_port()
        # 同一端口绑两次：第二个失败，但主监听可用 → 不应致命
        socks = build_listeners([("127.0.0.1", port), ("127.0.0.1", port)])
        try:
            assert len(socks) == 1
        finally:
            for s in socks:
                s.close()

    def test_primary_bind_failure_is_fatal(self):
        from src.main import build_listeners

        port = _free_port()
        blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        blocker.bind(("127.0.0.1", port))
        try:
            with pytest.raises(OSError):
                build_listeners([("127.0.0.1", port)])
        finally:
            blocker.close()
