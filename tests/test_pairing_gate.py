"""CONN-6: 全局配对门卫中间件 — /api 全站强制（HTTP + WebSocket）。

规则（backlog CONN-6 / 设计档案）：
- 默认拒绝：非本机且无有效钥匙 → HTTP 401 / WS close 1008（没有全开放引导期）
- 本机 = 回环来源地址（桌面壳）或「请求落在回环独占端口且是领钥匙端点」
- 豁免面：SPA 静态、/health、/assets
- **/mcp 只认本机**（2026-09-18 重写）：有效钥匙也不行，非本机一律 404 ——
  见 ``test_mcp_loopback_only.py`` 与 ``src/pairing/middleware.py`` 的不变式说明。
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


# ── 决策纯函数 ──────────────────────────────────────────────────


class TestGateDecision:
    def test_non_api_paths_always_open(self):
        from src.pairing.middleware import should_allow

        assert should_allow(path="/", is_local=False, token_valid=False) is True
        assert should_allow(path="/health", is_local=False, token_valid=False) is True
        assert should_allow(path="/assets/index-abc.js", is_local=False, token_valid=False) is True

    def test_mcp_needs_local_not_key(self):
        # /mcp 只认本机：钥匙不是这条闸门的凭据（不变式，见 middleware docstring）
        from src.pairing.middleware import should_allow

        assert should_allow(path="/mcp", is_local=False, token_valid=False) is False
        assert should_allow(path="/mcp/", is_local=False, token_valid=False) is False
        assert should_allow(path="/mcp", is_local=True, token_valid=False) is True
        # **核心回归**：带钥匙的非本机请求也必须被拒
        assert should_allow(path="/mcp", is_local=False, token_valid=True) is False
        assert should_allow(path="/mcp/anything", is_local=False, token_valid=True) is False
        assert should_allow(path="/mcp", is_local=True, token_valid=True) is True

    def test_api_denied_by_default(self):
        from src.pairing.middleware import should_allow

        assert should_allow(path="/api/meetings", is_local=False, token_valid=False) is False

    def test_api_local_always_allowed(self):
        from src.pairing.middleware import should_allow

        assert should_allow(path="/api/meetings", is_local=True, token_valid=False) is True

    def test_api_valid_token_allowed(self):
        from src.pairing.middleware import should_allow

        assert should_allow(path="/api/meetings", is_local=False, token_valid=True) is True

    def test_local_key_path_allowed_on_local_port(self):
        from src.pairing.middleware import should_allow

        assert should_allow(
            path=service.LOCAL_KEY_PATH, is_local=False, token_valid=False, local_key=True
        ) is True


# ── 集成：经 TestClient 走真实 app ──────────────────────────────


@pytest.fixture
def client():
    from src.main import app

    return TestClient(app, base_url=LAN_BASE)


class TestGateIntegration:
    def test_nonlocal_api_401_when_local_key_exists(self, client):
        service.ensure_local_key()
        resp = client.get("/api/pairing/tokens")
        assert resp.status_code == 401
        assert resp.json()["detail"]["code"] == "pairing_required"

    def test_nonlocal_denied_when_no_keys_at_all(self, client):
        resp = client.get("/api/pairing/tokens")
        assert resp.status_code == 401

    def test_forged_token_401(self, client):
        service.ensure_local_key()
        resp = client.get("/api/pairing/tokens", headers={"X-Pairing-Token": "sd1_forged"})
        assert resp.status_code == 401

    def test_health_stays_open(self, client):
        resp = client.get("/health")
        assert resp.status_code != 401

    def test_spa_stays_open(self, client):
        resp = client.get("/")
        assert resp.status_code != 401

    def test_mcp_denied_without_key(self, client):
        # 局域网无钥匙 → 404（不是 401：见下条）
        resp = client.post("/mcp")
        assert resp.status_code == 404

    def test_mcp_denied_even_with_key_and_hides_existence(self, client):
        """**核心回归**：局域网 + 有效钥匙也必须进不去，且**看不出路径存在**。

        401 会告诉扫描者「有个要钥匙的 MCP，去搞把钥匙」；404 让它与「这台机器上
        没有这个路径」不可区分。钥匙在这条闸门上不是凭据 —— 见 middleware 的
        「/mcp 收敛回环」不变式。
        """
        key = service.ensure_local_key()
        probe = TestClient(
            client.app, base_url=LAN_BASE, headers={"X-Pairing-Token": key["token"]}
        )
        resp = probe.post("/mcp")
        assert resp.status_code == 404
        assert "pairing" not in resp.text.lower()

    def test_mcp_allowed_on_loopback(self, client):
        # 同机 Agent（回环来源）免钥匙——证明放行方式：能穿透门卫抵达 MCP 内部
        # （其 lifespan 未在测试中运行，抛 RuntimeError 即为证据）
        probe = TestClient(client.app, base_url=LAN_BASE, client=("127.0.0.1", 51000))
        try:
            probe.post("/mcp")
        except RuntimeError as exc:
            assert "Task group" in str(exc)
        else:
            return  # 正常响应也算穿透

    def test_query_token_accepted(self, client):
        key = service.ensure_local_key()
        resp = client.get(f"/api/pairing/tokens?token={key['token']}")
        assert resp.status_code == 200

    def test_local_key_denied_to_keyed_remote(self, client):
        # 防御纵深（2026-09-16）：门卫对「外部端口上的领钥匙请求」放行给持钥匙者
        # （端口准入 NONE → 落普通门禁），路由层必须再拦一次——领钥匙通道只属于
        # 物理本机，局域网设备纵然手里有有效钥匙也拿不到本机钥匙明文。
        key = service.ensure_local_key()
        resp = client.get(
            "/api/pairing/local-key", headers={"X-Pairing-Token": key["token"]}
        )
        assert resp.status_code == 403

    def test_local_key_ok_from_loopback(self, client):
        # 本机（回环客户端）照常静默领钥匙——桌面壳/本机浏览器的路径不受影响。
        probe = TestClient(
            client.app, base_url="http://127.0.0.1:18900", client=("127.0.0.1", 51000)
        )
        resp = probe.get("/api/pairing/local-key")
        assert resp.status_code == 200
        assert resp.json()["token"].startswith("sd1_")

    def test_ws_denied_without_key(self, client):
        from starlette.websockets import WebSocketDisconnect

        with pytest.raises(WebSocketDisconnect) as ei:
            with client.websocket_connect("/api/meetings/m1/realtime-transcribe"):
                pass
        assert ei.value.code == 1008

    def test_ws_allowed_on_loopback_client(self, client):
        # 桌面壳：监听只绑回环，客户端地址就是回环 → 免钥匙
        service.ensure_local_key()
        probe = TestClient(client.app, base_url=LAN_BASE, client=("127.0.0.1", 51000))
        with pytest.raises(Exception) as ei:
            with probe.websocket_connect("/api/meetings/m1/realtime-transcribe"):
                pass
        # 不是 1008（门卫放行），失败发生在真正的转写路由里
        assert "1008" not in str(ei.value)


# ── 长短钥匙：门卫双接受（sda1_ 短票 + sd1_ 长钥匙）──────────────


class TestDualAccept:
    def test_short_token_passes_gate_and_routes(self, client):
        key = service.ensure_local_key()
        at = service.mint_access_token(key["token"])
        resp = client.get("/api/pairing/tokens", headers={"X-Pairing-Token": at["access_token"]})
        assert resp.status_code == 200

    def test_short_token_in_query_passes(self, client):
        # WS/SSE/图片通道走 ?token=，同样必须被门卫认
        key = service.ensure_local_key()
        at = service.mint_access_token(key["token"])
        resp = client.get(f"/api/pairing/tokens?token={at['access_token']}")
        assert resp.status_code == 200

    def test_forged_short_token_401(self, client):
        key = service.ensure_local_key()
        tok = service.mint_access_token(key["token"])["access_token"]
        bad = tok[:-1] + ("0" if tok[-1] != "0" else "1")
        resp = client.get("/api/pairing/tokens", headers={"X-Pairing-Token": bad})
        assert resp.status_code == 401

    def test_revoked_long_key_short_token_401(self, client):
        key = service.issue_token(name="phone", is_local=True)
        at = service.mint_access_token(key["token"])
        assert service.revoke_token(key["id"]) is True
        resp = client.get("/api/pairing/tokens", headers={"X-Pairing-Token": at["access_token"]})
        assert resp.status_code == 401

    def test_mint_route_with_long_key(self, client):
        key = service.ensure_local_key()
        resp = client.post("/api/pairing/access-token", headers={"X-Pairing-Token": key["token"]})
        assert resp.status_code == 200
        body = resp.json()
        assert body["access_token"].startswith(service.ACCESS_TOKEN_PREFIX)
        assert body["expires_in"] == service.ACCESS_TOKEN_TTL_SECONDS

    def test_mint_route_without_key_401(self, client):
        service.ensure_local_key()
        resp = client.post("/api/pairing/access-token")
        assert resp.status_code == 401
