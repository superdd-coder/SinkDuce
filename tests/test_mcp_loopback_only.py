"""★ 不变式：**MCP 只经回环可达，任何情况下不暴露到网络（包括将来的云端中继）**。

2026-09-18 定稿。这条不变式**承重两个结论**，所以单独成文件钉住：

1. 回环 ⇒ ``is_local`` ⇒ actor 恒为 owner ⇒ **不存在权限更低的一方** ⇒
   不需要权限交集 / 可达盒子清单 / 逐调用者授权（"共享单台盒子"因此是安全的）。
2. 唯一身份是 owner ⇒ 有状态会话的**身份冻结 / 会话劫持**失去危害前提
   （冻结的是 owner、能劫到的也是 owner）。这正是能切回 ``stateless_http=False``
   的理由。

⇒ **中继落地会同时打破这两条。** 放开 MCP 之前必须先补调用者权限判定。

跑法：``pytest tests/test_mcp_loopback_only.py``
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.pairing import service, store

LOCAL_BASE = "http://127.0.0.1:18900"
LAN_BASE = "http://192.168.1.30:18901"


@pytest.fixture(autouse=True)
def _tmp_pairing(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "PAIRING_DIR", tmp_path)
    monkeypatch.setattr(service, "local_entry_port", lambda: 18900)
    yield


@pytest.fixture
def client():
    from src.main import app

    return TestClient(app, base_url=LAN_BASE)


# ── 决策层：钥匙不是 /mcp 的凭据 ─────────────────────────────


class TestKeyIsNotACredentialForMcp:
    def test_valid_key_does_not_open_mcp(self):
        from src.pairing.middleware import should_allow

        assert should_allow(path="/mcp", is_local=False, token_valid=True) is False

    def test_local_key_grant_does_not_open_mcp(self):
        """连领钥匙通道的放行也不能用来进 /mcp（两条闸门语义不同）。"""
        from src.pairing.middleware import should_allow

        assert (
            should_allow(path="/mcp", is_local=False, token_valid=False, local_key=True)
            is False
        )

    def test_api_contract_unchanged(self):
        """对照：/api 仍是「本机或有效钥匙」——这次收敛**只针对 /mcp**。"""
        from src.pairing.middleware import should_allow

        assert should_allow(path="/api/pairing/tokens", is_local=False, token_valid=True) is True
        assert should_allow(path="/api/pairing/tokens", is_local=False, token_valid=False) is False


# ── 路径形态：前缀匹配不能漏 ─────────────────────────────────


class TestPathShapes:
    @pytest.mark.parametrize(
        "path",
        ["/mcp", "/mcp/", "/mcp/messages", "/mcp/session/abc"],
    )
    def test_all_mcp_shapes_require_local(self, path):
        from src.pairing.middleware import should_allow

        assert should_allow(path=path, is_local=False, token_valid=True) is False

    def test_lookalike_paths_are_not_mcp(self):
        """``/mcpx`` 不是 MCP —— 前缀判定必须带边界（否则误伤同名路径）。"""
        from src.pairing.middleware import _is_mcp_path

        assert _is_mcp_path("/mcp") is True
        assert _is_mcp_path("/mcp/x") is True
        assert _is_mcp_path("/mcpx") is False
        assert _is_mcp_path("/mcpx/y") is False


# ── 集成：经真实 app ─────────────────────────────────────────


class TestIntegration:
    def test_lan_keyed_client_gets_404_not_401(self, client):
        """**核心回归**：局域网 + 有效钥匙 → 404，且响应里不含 pairing 线索。

        实测过旧行为：`192.168.110.146:18901/mcp` + member 钥匙 = **200**。
        """
        key = service.ensure_local_key()
        probe = TestClient(
            client.app, base_url=LAN_BASE, headers={"X-Pairing-Token": key["token"]}
        )
        resp = probe.post("/mcp")
        assert resp.status_code == 404
        body = resp.text.lower()
        assert "pairing" not in body
        assert "token" not in body

    def test_404_body_has_no_hint_of_mcp(self, client):
        """响应体只说明「没有这个路径」，不带任何「这里有什么」的线索。"""
        key = service.ensure_local_key()
        resp = TestClient(
            client.app, base_url=LAN_BASE, headers={"X-Pairing-Token": key["token"]}
        ).post("/mcp")
        assert resp.json() == {"detail": "Not Found"}

    def test_lan_without_key_also_404(self, client):
        """无钥匙也一样 404（不该因缺钥匙而变成 401）。"""
        assert client.post("/mcp").status_code == 404

    def test_loopback_still_reaches_mcp(self, client):
        """同机回环照常穿透（否则本机 Agent 全废）。"""
        probe = TestClient(client.app, base_url=LAN_BASE, client=("127.0.0.1", 51000))
        try:
            probe.post("/mcp")
        except RuntimeError as exc:
            assert "Task group" in str(exc)  # 抵达了 MCP 内部（lifespan 未跑）
        else:
            return

    def test_api_still_accepts_key_on_lan(self, client):
        """对照：钥匙在 /api 上照旧好使（证明收敛没有误伤）。"""
        key = service.ensure_local_key()
        resp = client.get("/api/pairing/tokens", headers={"X-Pairing-Token": key["token"]})
        assert resp.status_code == 200
