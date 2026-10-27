"""Pairing token system (src/pairing/) — CONN-1 / CONN-6.

身份模型（2026-09-15 重做）：
- 本机身份 = 「请求落在回环独占端口」+ Host 是回环 + Origin 可接受；
  本机前端启动时静默从该端口领走主人钥匙，用户零操作。
- 来源 IP 不再作为可信信号（VPN/代理/Docker NAT 都会改写它，实测证伪）。
- 非本机设备一律凭钥匙。
"""

from __future__ import annotations

import json
import secrets

import pytest
from fastapi.testclient import TestClient

from src.pairing import service, store

LOCAL_BASE = "http://127.0.0.1:18900"
LAN_BASE = "http://192.168.1.30:18901"


@pytest.fixture(autouse=True)
def _tmp_pairing(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "PAIRING_DIR", tmp_path)
    # 测试里钉死本机入口端口 = 主 api_port，避免跟随开发者本机 config.yaml
    monkeypatch.setattr(service, "local_entry_port", lambda: 18900)
    yield


# ── 本机判定（端口 + 回环）───────────────────────────────────────


class TestLocalScope:
    """判定纯函数。注意：真实请求必带 Host，所以这里都补上 host 头。"""

    @staticmethod
    def _scope(client_ip, port=18900, host=b"127.0.0.1:18900"):
        return {
            "client": (client_ip, 1234),
            "server": ("0.0.0.0", port),
            "headers": [(b"host", host)],
        }

    def test_loopback_client_host_is_local(self):
        assert service.is_local_scope(self._scope("127.0.0.1")) is True
        assert service.is_local_scope(self._scope("::1")) is True

    def test_lan_client_host_is_not_local(self):
        # TEST-NET（RFC 5737）：硬编码具体私网 IP 会与开发机真实网卡撞车，
        # 那时门卫判「本机自己的地址」反而放行，测试在本机永远失败（实测）。
        assert service.is_local_scope(self._scope("198.51.100.7")) is False

    def test_external_port_is_not_local_trust(self):
        # 对外端口上的非回环来源不是本机
        assert service.is_local_scope(self._scope("198.51.100.7", port=18901)) is False
        assert (
            service.local_key_access(
                {
                    "client": ("198.51.100.7", 1234),
                    "server": ("198.51.100.8", 18901),
                    "headers": [],
                }
            )
            == service.LOCAL_KEY_NONE
        )


class TestLegacyTrustPathsRemoved:
    """旧方案（来源 IP 猜测）已清除——这些路径不得再放行。"""

    def test_trusted_cidrs_env_no_longer_grants_access(self, monkeypatch):
        monkeypatch.setenv("SINKDUCE_TRUSTED_CIDRS", "198.51.100.0/24")
        scope = {
            "client": ("198.51.100.7", 1234),
            "server": ("0.0.0.0", 18900),
            "headers": [(b"host", b"127.0.0.1:18900")],
        }
        assert service.is_local_scope(scope) is False

    def test_gateway_ip_no_longer_grants_access(self):
        # Docker 网关识别（/proc/net/route）已删除：容器网关地址不再视为本机
        scope = {
            "client": ("172.23.0.1", 1234),
            "server": ("0.0.0.0", 18900),
            "headers": [(b"host", b"127.0.0.1:18900")],
        }
        assert service.is_local_scope(scope) is False
        assert not hasattr(service, "_gateway_ips")

    def test_own_lan_address_no_longer_grants_access(self, monkeypatch):
        # 盒子自己的网卡地址也不再放行：同机 Docker 容器出站经 NAT 后源地址就是它，
        # 与「本机浏览器用 LAN IP 打开」不可区分（2026-09-19 实弹审计的提权路径）。
        # 注意必须带本机 Host 头，否则在 _host_is_local 就被拒，测不到客户端分支。
        monkeypatch.setattr(service, "local_addresses", lambda: ["192.168.1.30"])
        scope = {
            "client": ("192.168.1.30", 1234),
            "server": ("0.0.0.0", 18900),
            "headers": [(b"host", b"192.168.1.30:18900")],
        }
        assert service.is_local_scope(scope) is False
        # 对照：同样的 Host，客户端换成回环 → 本机
        assert service.is_local_scope({**scope, "client": ("127.0.0.1", 1234)}) is True

    def test_own_lan_ip_client_gets_401_not_owner(self, monkeypatch):
        # 门卫级钉死（2026-09-19 审计场景）：源地址 = 自己的网卡地址 + 本机 Host，
        # 不带钥匙打受保护端点 → 401（修复前这里被 is_local 判本机 → owner 直接放行）。
        monkeypatch.setattr(service, "local_addresses", lambda: ["192.168.1.30"])
        from src.main import app

        lan_client = TestClient(app, base_url="http://192.168.1.30:18900", client=("192.168.1.30", 50000))
        assert lan_client.get("/api/config", headers={"Host": "192.168.1.30:18900"}).status_code == 401


# ── service：发放 / 校验 / 吊销 / 过期 / 配额 ────────────────────


class TestUnlimitedKeys:
    def test_no_device_key_quota(self):
        for i in range(7):
            service.issue_token(name=f"k{i}", is_local=True)
        assert service.active_token_count() == 7


class TestPairingService:
    def test_issue_then_verify_roundtrip(self):
        result = service.issue_token(name="我的手机", is_local=True)
        rec = service.verify_token(result["token"])
        assert rec is not None
        assert rec["name"] == "我的手机"

    def test_store_never_contains_plaintext(self):
        result = service.issue_token(name="phone", is_local=True)
        raw = (store._tokens_path()).read_text(encoding="utf-8")
        assert result["token"] not in raw
        assert "token_hash" in raw

    def test_wrong_token_verifies_to_none(self):
        service.issue_token(name="phone", is_local=True)
        assert service.verify_token("sd1_forged_token") is None

    def test_revoke_then_verify_none(self):
        result = service.issue_token(name="phone", is_local=True)
        assert service.revoke_token(result["id"]) is True
        assert service.verify_token(result["token"]) is None

    def test_revoke_unknown_id_returns_false(self):
        assert service.revoke_token("no-such-id") is False

    def test_expired_token_rejected(self):
        result = service.issue_token(name="tmp", is_local=True, ttl_days=7)
        real_now = service._now

        def _future():
            from datetime import timedelta

            return real_now() + timedelta(days=8)

        monkey = pytest.MonkeyPatch()
        monkey.setattr(service, "_now", _future)
        try:
            assert service.verify_token(result["token"]) is None
        finally:
            monkey.undo()

    def test_issue_response_includes_box_id(self):
        from src.pairing import store as pstore
        result = service.issue_token(name="x", is_local=True)
        box = pstore.load_box()
        assert result["box_id"] == box["box_id"]

    def test_revoke_reduces_active_count(self):
        first = service.issue_token(name="k0", is_local=True)
        assert service.active_token_count() == 1
        service.revoke_token(first["id"])
        assert service.active_token_count() == 0

    def test_issue_policy_requires_local_or_valid_token(self):
        assert service.can_issue(is_local=True, has_valid_token=False) is True
        assert service.can_issue(is_local=False, has_valid_token=True) is True
        assert service.can_issue(is_local=False, has_valid_token=False) is False

    def test_list_is_masked(self):
        service.issue_token(name="phone", is_local=True)
        items = service.list_tokens()
        assert len(items) == 1
        assert "token_hash" not in items[0]
        assert items[0]["token_hint"].startswith("sd1_")


# ── routes（TestClient 默认来源 host="testclient"，即非本机）──────


@pytest.fixture
def client():
    from src.main import app

    return TestClient(app, base_url=LAN_BASE)


@pytest.fixture
def local_client():
    """模拟「本机浏览器」：回环客户端地址 + 回环 Host（桌面形态的硬信号）。"""
    from src.main import app

    return TestClient(app, base_url=LOCAL_BASE, client=("127.0.0.1", 50000))


@pytest.fixture
def container_client(monkeypatch):
    """容器形态：客户端是网关 IP（无法靠地址判断），靠端口映射隔离。"""
    from src.main import app

    monkeypatch.setattr("src.config.in_container", lambda: True)
    return TestClient(app, base_url=LOCAL_BASE, client=("172.17.0.1", 50000))


def _desktop_app(monkeypatch, entry_port=18910):
    """桌面形态（非容器）：只有一个监听，主端口既是本机入口也是唯一入口。"""
    from src.main import app

    monkeypatch.setattr(service, "local_entry_port", lambda: entry_port)
    monkeypatch.setattr("src.config.in_container", lambda: False)
    return app


class TestLocalKeyDesktopNoLeak:
    """桌面形态若把主端口开到局域网，领钥匙端点与局域网流量共用同一端口——
    绝不能仅凭端口 + 可伪造的 Host 头就发钥匙。本机客户端地址（回环）才是硬信号。"""

    def test_lan_client_cannot_forge_host_to_get_key(self, monkeypatch):
        app = _desktop_app(monkeypatch)
        # 客户端来自局域网（真实地址），但伪造 Host 为回环
        c = TestClient(app, base_url="http://192.168.1.30:18910", client=("192.168.1.30", 55555))
        resp = c.get(service.LOCAL_KEY_PATH, headers={"Host": "127.0.0.1:18910"})
        assert resp.status_code != 200
        assert resp.status_code == 403

    def test_lan_client_without_origin_still_refused(self, monkeypatch):
        # 脚本通常不带 Origin；不能因此放行
        app = _desktop_app(monkeypatch)
        c = TestClient(app, base_url="http://192.168.1.30:18910", client=("192.168.1.30", 55555))
        resp = c.get(service.LOCAL_KEY_PATH, headers={"Host": "127.0.0.1:18910", "Origin": ""})
        assert resp.status_code == 403

    def test_loopback_client_still_gets_key(self, monkeypatch):
        # 真正的本机（回环客户端）不受影响——桌面自用照常无感
        app = _desktop_app(monkeypatch)
        c = TestClient(app, base_url="http://127.0.0.1:18910", client=("127.0.0.1", 55555))
        resp = c.get(service.LOCAL_KEY_PATH)
        assert resp.status_code == 200
        assert resp.json()["token"].startswith("sd1_")

    def test_container_gateway_client_still_gets_key(self, monkeypatch):
        # 容器形态回归：客户端是网关 IP（非回环），靠端口映射隔离，应照常放行
        from src.main import app

        monkeypatch.setattr(service, "local_entry_port", lambda: 18900)
        monkeypatch.setattr("src.config.in_container", lambda: True)
        c = TestClient(app, base_url="http://172.17.0.1:18900", client=("172.17.0.1", 55555))
        resp = c.get(service.LOCAL_KEY_PATH, headers={"Host": "127.0.0.1:18900"})
        assert resp.status_code == 200


class TestPairingRoutes:
    def test_issue_requires_local_or_valid_token(self, client):
        # 默认拒绝：非本机且无有效钥匙 → 401（本机前端自动领钥匙，无需手抄）
        resp = client.post("/api/pairing/issue", json={"name": "我的手机"})
        assert resp.status_code == 401

    def test_issue_ok_with_admin_key(self, client):
        # RBAC：签发需要 key.manage。存量钥匙迁移后就是 admin，这是真实用户路径。
        first = service.issue_token(name="laptop", role="admin", is_local=True)
        resp = client.post(
            "/api/pairing/issue",
            json={"name": "我的手机", "role": "member"},
            headers={"Authorization": f"Bearer {first['token']}"},
        )
        assert resp.status_code == 200
        assert resp.json()["token"].startswith("sd1_")

    def test_member_key_cannot_issue(self, client):
        """member 无 key.manage → 不得签发新钥匙（RBAC §7.1）。"""
        member = service.issue_token(name="phone", role="member", is_local=True)
        resp = client.post(
            "/api/pairing/issue",
            json={"name": "tablet"},
            headers={"Authorization": f"Bearer {member['token']}"},
        )
        assert resp.status_code == 403

    def test_second_issue_nonlocal_without_token_forbidden(self, client):
        client.post("/api/pairing/issue", json={"name": "first"})
        resp = client.post("/api/pairing/issue", json={"name": "second"})
        assert resp.status_code == 401
        assert resp.json()["detail"]["code"] == "pairing_required"

    def test_issue_with_valid_bearer_token_ok(self, client):
        first = service.issue_token(name="first", role="admin", is_local=True)
        resp = client.post(
            "/api/pairing/issue",
            json={"name": "second"},
            headers={"Authorization": f"Bearer {first['token']}"},
        )
        assert resp.status_code == 200

    def test_issue_with_forged_bearer_forbidden(self, client):
        client.post("/api/pairing/issue", json={"name": "first"})
        resp = client.post(
            "/api/pairing/issue",
            json={"name": "second"},
            headers={"Authorization": "Bearer sd1_forged"},
        )
        assert resp.status_code == 401

    def test_tokens_list_requires_auth_for_nonlocal(self, client):
        client.post("/api/pairing/issue", json={"name": "first"})
        resp = client.get("/api/pairing/tokens")
        assert resp.status_code == 401

    def test_tokens_list_with_valid_token_masked(self, client):
        first = service.issue_token(name="first", role="admin", is_local=True)  # 管理面需 key.manage
        resp = client.get(
            "/api/pairing/tokens",
            headers={"X-Pairing-Token": first["token"]},
        )
        assert resp.status_code == 200
        items = resp.json()["tokens"]
        assert len(items) == 1
        assert "token_hash" not in json.dumps(items)

    def test_tokens_list_reports_no_quota(self, client):
        first = service.issue_token(name="first", role="admin", is_local=True)  # 管理面需 key.manage
        resp = client.get("/api/pairing/tokens", headers={"X-Pairing-Token": first["token"]})
        assert resp.status_code == 200
        assert resp.json()["limit"] is None

    def test_revoke_flow(self, client):
        first = service.issue_token(name="first", role="admin", is_local=True)  # 管理面需 key.manage
        resp = client.post(
            "/api/pairing/revoke",
            json={"id": first["id"]},
            headers={"Authorization": f"Bearer {first['token']}"},
        )
        assert resp.status_code == 200
        assert service.verify_token(first["token"]) is None

    def test_revoke_unknown_id_404(self, client):
        first = service.issue_token(name="first", role="admin", is_local=True)  # 管理面需 key.manage
        resp = client.post(
            "/api/pairing/revoke",
            json={"id": "no-such-id"},
            headers={"Authorization": f"Bearer {first['token']}"},
        )
        assert resp.status_code == 404

    def test_revoke_without_auth_401(self, client):
        client.post("/api/pairing/issue", json={"name": "first"})
        resp = client.post("/api/pairing/revoke", json={"id": "whatever"})
        assert resp.status_code == 401


# ── CONN-2：盒子信息（名称/ID/地址）与 last_seen ─────────────────


class TestBoxEndpoints:
    def test_box_info_with_token_nonlocal(self, client):
        first = service.issue_token(name="x", is_local=True)
        resp = client.get("/api/pairing/box", headers={"X-Pairing-Token": first["token"]})
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["box_id"]) >= 16
        assert body["box_name"]
        assert isinstance(body["addresses"], list)

    def test_box_id_stable_across_calls(self, client):
        first = service.issue_token(name="x", is_local=True)
        h = {"X-Pairing-Token": first["token"]}
        a = client.get("/api/pairing/box", headers=h).json()["box_id"]
        b = client.get("/api/pairing/box", headers=h).json()["box_id"]
        assert a == b

    def test_box_get_nonlocal_denied_without_key(self, client):
        resp = client.get("/api/pairing/box")
        assert resp.status_code == 401

    def test_box_rename_with_valid_token_ok(self, client):
        first = service.issue_token(name="first", is_local=True)
        resp = client.put(
            "/api/pairing/box",
            json={"box_name": "改个名"},
            headers={"Authorization": f"Bearer {first['token']}"},
        )
        assert resp.status_code == 200
        assert resp.json()["box_name"] == "改个名"

    def test_box_rename_response_shape_matches_get(self, client):
        # 回归：PUT 返回必须与 GET 同构（含 addresses），否则前端渲染崩溃
        first = service.issue_token(name="first", is_local=True)
        resp = client.put(
            "/api/pairing/box",
            json={"box_name": "书房盒子"},
            headers={"X-Pairing-Token": first["token"]},
        )
        body = resp.json()
        assert set(["box_id", "box_name", "addresses"]).issubset(body.keys())
        assert isinstance(body["addresses"], list)

    def test_box_rename_nonlocal_401(self, client):
        client.post("/api/pairing/issue", json={"name": "first"})
        resp = client.put("/api/pairing/box", json={"box_name": "劫持"})
        assert resp.status_code == 401


class TestLastSeen:
    def test_verify_records_last_seen(self):
        result = service.issue_token(name="x", is_local=True)
        service.verify_token(result["token"])
        tokens = service.list_tokens()
        assert tokens[0]["last_seen"] is not None

    def test_throttled_within_60s(self):
        result = service.issue_token(name="x", is_local=True)
        service.verify_token(result["token"])
        first = service.list_tokens()[0]["last_seen"]
        service.verify_token(result["token"])  # 窗口内不刷新
        assert service.list_tokens()[0]["last_seen"] == first

    def test_refresh_after_throttle_window(self, monkeypatch):
        result = service.issue_token(name="x", is_local=True)
        service.verify_token(result["token"])
        first = service.list_tokens()[0]["last_seen"]
        real_now = service._now

        def _later():
            from datetime import timedelta

            return real_now() + timedelta(seconds=61)

        monkeypatch.setattr(service, "_now", _later)
        service.verify_token(result["token"])
        assert service.list_tokens()[0]["last_seen"] != first


# ── 本机自动领钥匙：只在物理本地通道发放 ────────────────────────


class TestLocalKeyEndpoint:
    """Host/Origin 校验（两种形态共用）。"""

    def test_hands_out_key_with_loopback_origin(self, local_client):
        resp = local_client.get(
            service.LOCAL_KEY_PATH, headers={"Origin": "http://localhost:18900"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["token"].startswith("sd1_")
        assert body["box_id"]

    def test_no_origin_header_is_accepted(self, local_client):
        # 非浏览器调用（curl 在盒子上执行）没有 Origin
        resp = local_client.get(service.LOCAL_KEY_PATH)
        assert resp.status_code == 200

    def test_own_lan_address_origin_is_accepted(self, local_client, monkeypatch):
        # 盒子自己的网卡地址作为 Origin（本机用自己 LAN IP 打开页面的场景）
        monkeypatch.setattr(service, "local_addresses", lambda: ["192.168.1.30"])
        resp = local_client.get(
            service.LOCAL_KEY_PATH, headers={"Origin": "http://192.168.1.30:18900"}
        )
        assert resp.status_code == 200

    def test_foreign_private_origin_is_refused(self, local_client):
        # 局域网里别人服务器上的恶意页面（私网 Origin）→ 拒绝。
        # 放行任意私网来源等于给恶意页面开正门。
        # 用 TEST-NET 段（RFC 5737）模拟邻居：具体私网 IP 会与开发机真实网卡撞车。
        resp = local_client.get(
            service.LOCAL_KEY_PATH, headers={"Origin": "http://198.51.100.7:18900"}
        )
        assert resp.status_code == 403

    def test_token_is_stable_across_calls(self, local_client):
        a = local_client.get(service.LOCAL_KEY_PATH).json()["token"]
        b = local_client.get(service.LOCAL_KEY_PATH).json()["token"]
        assert a == b

    def test_handed_out_key_opens_the_gated_api(self, local_client, client):
        token = local_client.get(service.LOCAL_KEY_PATH).json()["token"]
        # 同一把钥匙，从局域网端口（非本机）访问受保护端点 → 放行
        resp = client.get("/api/pairing/tokens", headers={"X-Pairing-Token": token})
        assert resp.status_code == 200

    def test_external_port_is_refused(self, client):
        # 对外端口上，门卫在到达路由前就 401（端点不可从外部枚举）
        resp = client.get(service.LOCAL_KEY_PATH)
        assert resp.status_code == 401

    def test_dns_rebinding_host_is_refused(self, local_client):
        # Host 不是回环（攻击者把 evil.com 解析到 127.0.0.1）→ 拒绝
        resp = local_client.get(
            service.LOCAL_KEY_PATH,
            headers={"Host": "evil.com:18900", "Origin": "http://evil.com"},
        )
        assert resp.status_code == 403

    def test_foreign_origin_is_refused(self, local_client):
        # 盒子浏览器里的恶意网页跨源来领钥匙 → 拒绝
        resp = local_client.get(
            service.LOCAL_KEY_PATH, headers={"Origin": "https://evil.com"}
        )
        assert resp.status_code == 403

    def test_public_ip_origin_is_refused(self, local_client):
        resp = local_client.get(
            service.LOCAL_KEY_PATH, headers={"Origin": "http://93.184.216.34:18900"}
        )
        assert resp.status_code == 403

    def test_lan_host_header_is_refused(self, local_client):
        # 若本机入口被误发布到外网，局域网邻居的 Host 是非回环地址 → 403。
        # 必须用 TEST-NET 段（RFC 5737）模拟邻居：硬编码具体私网 IP 会与
        # 某些开发机的真实网卡地址撞车（192.168.1.30 恰为本机 IP 时，门卫
        # 判「本机自己的地址」反而放行——2026-09-16 实测）。
        resp = local_client.get(
            service.LOCAL_KEY_PATH,
            headers={"Host": "198.51.100.7:18900", "Origin": "http://198.51.100.7:18900"},
        )
        assert resp.status_code == 403


class TestContainerForm:
    """容器形态：客户端是网关 IP（地址不可信），本机身份靠端口映射。
    因此本机入口端口上的其他 /api 路径也应放行（整个端口即本机）。"""

    def test_gateway_client_gets_key(self, container_client):
        resp = container_client.get(
            service.LOCAL_KEY_PATH, headers={"Host": "127.0.0.1:18900"}
        )
        assert resp.status_code == 200

    def test_gateway_client_on_local_port_is_trusted(self, container_client):
        # 落在本机入口端口（宿主机只映射到回环）= 本机 → 过门卫（500 表示到达路由、无 DB）
        resp = container_client.get("/api/collections")
        assert resp.status_code != 401

    def test_pairing_routes_trust_local_port(self, container_client):
        """回归：配对路由自带守卫也不能用「客户端 IP」判本机。

        Docker 里客户端 IP 是网关地址，用它判定会把本机当外人 → 设置页的配对卡片
        401 → 前端弹出钥匙门（用户实测到的现象）。
        """
        assert container_client.get("/api/pairing/box").status_code == 200
        assert container_client.get("/api/pairing/tokens").status_code == 200

    def test_pairing_issue_trusts_local_port(self, container_client):
        # 本机入口端口上直接签发也应放行（配额从 0 起）
        resp = container_client.post("/api/pairing/issue", json={"name": "phone"})
        assert resp.status_code == 200

    def test_gateway_client_on_external_port_needs_key(self, monkeypatch):
        from src.main import app

        monkeypatch.setattr("src.config.in_container", lambda: True)
        monkeypatch.setattr(service, "local_entry_port", lambda: 18900)
        # 对外端口（18901）上的网关客户端不是本机
        c = TestClient(app, base_url="http://172.17.0.1:18901", client=("172.17.0.1", 55555))
        resp = c.get("/api/collections")
        assert resp.status_code == 401


class TestLocalScopeHardening:
    """本机判定的两道闸：Host 必须是回环 + Origin 必须是本机自己。

    背景：本机入口端口在宿主机只绑回环，但那挡不住「跑在盒子自己浏览器里的网页」。
    两种攻击都必须挡住：
    - DNS rebinding：页面在 evil.com:18900，DNS 重绑到本机，浏览器打 127.0.0.1 时
      仍是同源请求（不带 Origin），但 Host 是 evil.com；
    - 恶意页面直连：页面跨源 fetch http://127.0.0.1:18900（Host 回环，Origin 是 evil.com）。
    """

    @pytest.fixture(autouse=True)
    def _fixed(self, monkeypatch):
        monkeypatch.setattr(service, "local_addresses", lambda: ["172.23.0.3"])

    def test_dns_rebinding_without_origin_refused(self, container_client):
        # 同源请求不带 Origin —— 只能靠 Host 认出来
        resp = container_client.get("/api/config", headers={"Host": "evil.com:18900"})
        assert resp.status_code == 401

    def test_dns_rebinding_with_matching_origin_refused(self, container_client):
        resp = container_client.get(
            "/api/config",
            headers={"Host": "evil.com:18900", "Origin": "http://evil.com:18900"},
        )
        assert resp.status_code == 401

    def test_cross_origin_page_hitting_loopback_refused(self, container_client):
        # Host 回环但 Origin 是外站 → 恶意页面
        resp = container_client.get(
            "/api/config",
            headers={"Host": "127.0.0.1:18900", "Origin": "https://evil.com"},
        )
        assert resp.status_code == 401

    def test_other_lan_machine_page_refused(self, container_client):
        resp = container_client.get(
            "/api/config",
            headers={"Host": "127.0.0.1:18900", "Origin": "http://192.168.110.99:18900"},
        )
        assert resp.status_code == 401

    def test_real_box_browser_allowed(self, container_client):
        # 本机浏览器：Host 回环（同源 GET 通常不带 Origin）
        assert container_client.get(
            "/api/config", headers={"Host": "127.0.0.1:18900"}
        ).status_code == 200
        # 带 Origin 也必须是回环
        assert container_client.get(
            "/api/config",
            headers={"Host": "localhost:18900", "Origin": "http://localhost:18900"},
        ).status_code == 200

    def test_host_missing_refused(self, container_client):
        # HTTP/1.1 必带 Host；缺失即不可信
        resp = container_client.get(
            "/api/config",
            headers={"Host": "127.0.0.1:18900", "Origin": "http://127.0.0.1:18900"},
        )
        assert resp.status_code == 200  # 对照：正常可达
        # 用空 Host 模拟缺失（TestClient 会补，所以直接测判定函数）
        from src.pairing import service as svc

        assert svc.is_local_scope({"client": ("172.23.0.1", 1), "server": ("0.0.0.0", 18900), "headers": []}) is False


class TestOriginAcceptance:
    """Origin 判定函数本身：只认回环 / 盒子网卡地址 / 无 Origin。"""

    @pytest.fixture(autouse=True)
    def _fixed_addrs(self, monkeypatch):
        monkeypatch.setattr(service, "local_addresses", lambda: ["172.23.0.3"])

    def test_loopback_origin_accepted(self, container_client):
        resp = container_client.get(
            "/api/pairing/box",
            headers={"Host": "127.0.0.1:18900", "Origin": "http://127.0.0.1:18900"},
        )
        assert resp.status_code == 200

    def test_box_own_address_origin_accepted(self, container_client):
        resp = container_client.get(
            "/api/pairing/box",
            headers={"Host": "172.23.0.3:18900", "Origin": "http://172.23.0.3:18900"},
        )
        assert resp.status_code == 200

    def test_public_origin_rejected(self, container_client):
        resp = container_client.get(
            "/api/pairing/box",
            headers={"Host": "127.0.0.1:18900", "Origin": "https://evil.com"},
        )
        assert resp.status_code == 401

    def test_no_origin_accepted(self, container_client):
        # 本机 curl / MCP agent 不带 Origin
        resp = container_client.get("/api/pairing/box", headers={"Host": "127.0.0.1:18900"})
        assert resp.status_code == 200


class TestEnsureLocalKey:
    def test_creates_token_and_recovery_file(self):
        result = service.ensure_local_key()
        assert result["token"].startswith("sd1_")
        assert service.verify_token(result["token"]) is not None
        key_file = store.PAIRING_DIR / "local-key.txt"
        assert key_file.exists()
        assert result["token"] in key_file.read_text(encoding="utf-8")
        assert (key_file.stat().st_mode & 0o777) == 0o600

    def test_reuses_existing_active_local_key(self):
        first = service.ensure_local_key()
        assert service.ensure_local_key()["token"] == first["token"]

    def test_is_excluded_from_device_listing(self):
        service.ensure_local_key()
        assert service.active_token_count() == 0
        assert service.list_tokens() == []
        for i in range(7):
            service.issue_token(name=f"k{i}", is_local=True)
        assert service.active_token_count() == 7

    def test_rotates_after_revoke(self):
        first = service.ensure_local_key()
        rec = service.verify_token(first["token"])
        service.revoke_token(rec["id"])
        second = service.ensure_local_key()
        assert second["token"] != first["token"]
        assert service.verify_token(second["token"]) is not None

    def test_plaintext_never_lands_in_tokens_json(self):
        result = service.ensure_local_key()
        assert result["token"] not in store._tokens_path().read_text(encoding="utf-8")


class TestLegacyOwnerKeyMigration:
    """旧方案（手抄 owner-key.txt 的主人钥匙）升级清理。

    旧版 ensure_owner_key 在零活跃钥匙时自动发一把名为 owner 的钥匙并把明文写进
    owner-key.txt。新方案不再需要它：留着会占配额、在列表里显示成一把神秘设备钥匙。
    识别方式精确——用 owner-key.txt 的内容哈希比对，零误伤。
    """

    def _plant_legacy(self):
        legacy = service.issue_token(name="owner", is_local=True)
        (store.PAIRING_DIR / "owner-key.txt").write_text(legacy["token"] + "\n", encoding="utf-8")
        return legacy

    def test_legacy_key_revoked_and_file_removed(self):
        legacy = self._plant_legacy()
        service.ensure_local_key()
        assert service.verify_token(legacy["token"]) is None
        assert not (store.PAIRING_DIR / "owner-key.txt").exists()

    def test_legacy_key_no_longer_shown_or_counted(self):
        self._plant_legacy()
        service.ensure_local_key()
        # list_tokens 含已吊销记录（与旧行为一致，UI 自行过滤 revoked）；
        # 用户实际看到的活跃列表为空，且不再占配额。
        assert [t for t in service.list_tokens() if not t["revoked_at"]] == []
        assert service.active_token_count() == 0

    def test_does_not_touch_unrelated_key_named_owner(self):
        # 用户自己起的同名设备钥匙、没有对应 owner-key.txt → 不能误杀
        mine = service.issue_token(name="owner", is_local=True)
        result = service.ensure_local_key()
        assert service.verify_token(mine["token"]) is not None
        assert service.verify_token(result["token"]) is not None

    def test_migration_is_idempotent(self):
        self._plant_legacy()
        service.ensure_local_key()
        second = service.ensure_local_key()
        assert service.verify_token(second["token"]) is not None


class TestRoleAssignment:
    def test_issued_key_defaults_to_member(self):
        """RBAC：新钥匙默认 member（最小权限；UI 下拉可显式改）。

        与"存量钥匙迁移为 admin"不矛盾——存量是主人自己的设备（迁移保管理权），
        新钥匙默认取更保守的一档，需要更大权限时在创建弹窗里显式选。
        """
        from src.pairing import store as pstore

        service.issue_token(name="x", is_local=True)
        rec = pstore.load()[0]
        assert rec["role"] == "member"

    def test_explicit_role_is_stored(self):
        service.issue_token(name="x", role="visitor", is_local=True)
        assert service.list_tokens()[0]["role"] == "visitor"

    def test_list_exposes_role(self):
        service.issue_token(name="x", is_local=True)
        items = service.list_tokens()
        assert items[0]["role"] == "member"


# ── 对外地址 advertised_addr（API Key 卡片重构，2026-09-16）──────────


class TestAdvertisedAddr:
    def test_normalize_strips_scheme_slashes_whitespace(self):
        from src.pairing.service import normalize_advertised_addr

        assert normalize_advertised_addr(" http://192.168.1.30:18901/ ") == "192.168.1.30:18901"
        assert normalize_advertised_addr("HTTPS://Box.LAN/") == "Box.LAN"
        assert normalize_advertised_addr("192.168.1.30") == "192.168.1.30"
        assert normalize_advertised_addr("   ") == ""

    def test_put_then_get_roundtrip(self, local_client):
        resp = local_client.put(
            "/api/pairing/box",
            json={"box_name": "书房盒子", "advertised_addr": "http://192.168.1.30:18901/"},
        )
        assert resp.status_code == 200
        assert resp.json()["advertised_addr"] == "192.168.1.30:18901"
        got = local_client.get("/api/pairing/box").json()
        assert got["advertised_addr"] == "192.168.1.30:18901"

    def test_put_name_only_keeps_addr(self, local_client):
        # None = 不修改：只改名的 PUT 不得清掉已存的地址
        local_client.put(
            "/api/pairing/box",
            json={"box_name": "A", "advertised_addr": "192.168.1.30"},
        )
        got = local_client.put("/api/pairing/box", json={"box_name": "B"}).json()
        assert got["box_name"] == "B"
        assert got["advertised_addr"] == "192.168.1.30"

    def test_put_empty_clears_addr(self, local_client):
        local_client.put(
            "/api/pairing/box",
            json={"box_name": "A", "advertised_addr": "192.168.1.30"},
        )
        got = local_client.put(
            "/api/pairing/box", json={"box_name": "A", "advertised_addr": "  "}
        ).json()
        assert "advertised_addr" not in got

    def test_box_without_addr_stays_clean(self, local_client):
        got = local_client.get("/api/pairing/box").json()
        assert "advertised_addr" not in got


class TestHealthInContainer:
    def test_container_flag_reported(self, monkeypatch):
        from src.config import health_payload

        monkeypatch.setenv("SINKDUCE_CONTAINER", "1")
        monkeypatch.setattr("src.config.is_desktop_runtime", lambda: False)
        body = health_payload()
        assert body["in_container"] is True

    def test_no_flag_outside_container(self, monkeypatch):
        from src.config import health_payload

        monkeypatch.delenv("SINKDUCE_CONTAINER", raising=False)
        monkeypatch.setattr("src.config.is_desktop_runtime", lambda: False)
        body = health_payload()
        assert "in_container" not in body


# ── TTL：有效期校验 + 滑动延展（2026-09-16）─────────────────────


def _travel(**kwargs):
    """把 service._now 拨到未来（时间旅行），返回可 undo 的 monkeypatch。"""
    from datetime import timedelta

    real_now = service._now

    def _future():
        return real_now() + timedelta(**kwargs)

    mp = pytest.MonkeyPatch()
    mp.setattr(service, "_now", _future)
    return mp


class TestTtlValidation:
    """ttl_days 校验：0 / 小数曾静默变「永久」（falsy 分支），必须显式拒绝。"""

    def test_ttl_days_zero_rejected(self):
        with pytest.raises(service.PairingError) as ei:
            service.issue_token(name="x", is_local=True, ttl_days=0)
        assert ei.value.code == "invalid_ttl"

    def test_ttl_days_fraction_rejected(self):
        # 直接打 service 层：API 层 int 类型已挡掉小数，这里守最后一道
        with pytest.raises(service.PairingError) as ei:
            service.issue_token(name="x", is_local=True, ttl_days=0.5)
        assert ei.value.code == "invalid_ttl"

    def test_ttl_days_negative_rejected(self):
        with pytest.raises(service.PairingError) as ei:
            service.issue_token(name="x", is_local=True, ttl_days=-3)
        assert ei.value.code == "invalid_ttl"

    def test_ttl_days_none_means_permanent(self):
        result = service.issue_token(name="x", is_local=True)
        assert result["expires_at"] is None

    def test_ttl_days_seven_sets_expiry(self):
        result = service.issue_token(name="x", is_local=True, ttl_days=7)
        assert result["expires_at"] is not None


class TestSlidingTtl:
    """滑动钥匙：每次活跃使用把 expires_at 顺延为「现在 + ttl」；
    长期不用则在首个 TTL 到期时自然死亡。"""

    def test_sliding_key_extends_expiry_on_use(self):
        result = service.issue_token(name="dev", is_local=True, ttl_days=30, sliding=True)
        exp_before = result["expires_at"]
        mp = _travel(days=29)
        try:
            rec = service.verify_token(result["token"])
        finally:
            mp.undo()
        assert rec is not None
        assert rec["expires_at"] > exp_before

    def test_sliding_key_unused_dies_at_ttl(self):
        result = service.issue_token(name="dev", is_local=True, ttl_days=30, sliding=True)
        mp = _travel(days=31)
        try:
            assert service.verify_token(result["token"]) is None
        finally:
            mp.undo()

    def test_static_key_expiry_does_not_extend(self):
        result = service.issue_token(name="guest", is_local=True, ttl_days=7)
        mp = _travel(days=6)
        try:
            rec = service.verify_token(result["token"])
        finally:
            mp.undo()
        assert rec is not None
        # 静态钥匙：用了也顺延不了，到期时间纹丝不动（6 天后只剩 1 天寿命）
        assert rec["expires_at"] == result["expires_at"]

    def test_sliding_extension_throttled(self):
        # 节流窗口（60s）内的连续使用不重复写盘，expires_at 也不变
        result = service.issue_token(name="dev", is_local=True, ttl_days=30, sliding=True)
        first = service.verify_token(result["token"])
        mp = _travel(seconds=10)
        try:
            second = service.verify_token(result["token"])
        finally:
            mp.undo()
        assert second is not None
        assert second["expires_at"] == first["expires_at"]

    def test_sliding_expired_long_key_cannot_extend(self):
        # 死掉的钥匙不能靠「使用」复活：过期后验票仍是 None
        result = service.issue_token(name="dev", is_local=True, ttl_days=30, sliding=True)
        mp = _travel(days=45)
        try:
            assert service.verify_token(result["token"]) is None
        finally:
            mp.undo()


class TestIssueRouteTtl:
    def test_issue_invalid_ttl_maps_400(self, client):
        key = service.ensure_local_key()
        resp = client.post(
            "/api/pairing/issue",
            headers={"X-Pairing-Token": key["token"]},
            json={"name": "x", "ttl_days": 0},
        )
        assert resp.status_code == 400
        assert resp.json()["detail"]["code"] == "invalid_ttl"

    def test_issue_with_ttl_roundtrip(self, client):
        key = service.ensure_local_key()
        resp = client.post(
            "/api/pairing/issue",
            headers={"X-Pairing-Token": key["token"]},
            json={"name": "guest", "ttl_days": 7},
        )
        assert resp.status_code == 200
        assert resp.json()["expires_at"] is not None


# ── 长短钥匙：短命 access token（sda1_）─────────────────────────


class TestAccessToken:
    """短票 = 长钥匙的 10 分钟衍生物。核心契约：
    双接受（长短都认）、不落盘、吊销即时传导、短票不能再生短票。"""

    def test_mint_and_authenticate_roundtrip(self):
        key = service.issue_token(name="phone", is_local=True)
        at = service.mint_access_token(key["token"])
        assert at["access_token"].startswith(service.ACCESS_TOKEN_PREFIX)
        rec = service.authenticate(at["access_token"])
        assert rec is not None
        assert rec["id"] == key["id"]

    def test_long_key_authenticates_unchanged(self):
        key = service.issue_token(name="phone", is_local=True)
        rec = service.authenticate(key["token"])
        assert rec is not None and rec["id"] == key["id"]

    def test_mint_requires_valid_long_key(self):
        with pytest.raises(service.PairingError) as ei:
            service.mint_access_token("sd1_forged")
        assert ei.value.code == "pairing_required"

    def test_short_token_cannot_mint_short_token(self):
        # 无限续期链必须断掉：短票去换票 = 查无此哈希 = 拒绝
        key = service.issue_token(name="phone", is_local=True)
        at = service.mint_access_token(key["token"])["access_token"]
        with pytest.raises(service.PairingError):
            service.mint_access_token(at)

    def test_short_token_expires(self):
        key = service.issue_token(name="phone", is_local=True)
        at = service.mint_access_token(key["token"])
        mp = _travel(seconds=601)
        try:
            assert service.authenticate(at["access_token"]) is None
        finally:
            mp.undo()

    def test_forged_signature_rejected(self):
        key = service.issue_token(name="phone", is_local=True)
        tok = service.mint_access_token(key["token"])["access_token"]
        forged = tok[:-1] + ("0" if tok[-1] != "0" else "1")
        assert service.authenticate(forged) is None

    def test_tampered_payload_rejected(self):
        import base64

        key = service.issue_token(name="phone", is_local=True)
        tok = service.mint_access_token(key["token"])["access_token"]
        body, sig = tok[len(service.ACCESS_TOKEN_PREFIX):].split(".")
        other = base64.urlsafe_b64encode(b'{"kid":"attacker","exp":9999999999}').decode().rstrip("=")
        assert service.authenticate(f"{service.ACCESS_TOKEN_PREFIX}{other}.{sig}") is None

    def test_revoked_long_key_kills_short_token_immediately(self):
        key = service.issue_token(name="phone", is_local=True)
        at = service.mint_access_token(key["token"])
        assert service.revoke_token(key["id"]) is True
        assert service.authenticate(at["access_token"]) is None

    def test_expired_long_key_cannot_mint(self):
        key = service.issue_token(name="tmp", is_local=True, ttl_days=1)
        mp = _travel(days=2)
        try:
            with pytest.raises(service.PairingError) as ei:
                service.mint_access_token(key["token"])
        finally:
            mp.undo()
        assert ei.value.code == "pairing_required"

    def test_local_key_can_mint(self):
        key = service.ensure_local_key()
        at = service.mint_access_token(key["token"])
        assert service.authenticate(at["access_token"]) is not None

    def test_short_token_never_in_store(self):
        key = service.issue_token(name="phone", is_local=True)
        at = service.mint_access_token(key["token"])["access_token"]
        raw = (store._tokens_path()).read_text(encoding="utf-8")
        assert "sda1_" not in raw
        assert at not in raw

    def test_access_secret_file_is_0600(self):
        import os
        import stat

        key = service.issue_token(name="phone", is_local=True)
        service.mint_access_token(key["token"])
        p = store.PAIRING_DIR / "access-secret"
        assert p.exists()
        assert stat.S_IMODE(os.stat(p).st_mode) == 0o600

    def test_secret_rotation_invalidates_outstanding_tokens(self):
        # 轮换密钥 = 在飞短票全部作废（无害，长钥匙照旧）
        key = service.issue_token(name="phone", is_local=True)
        at = service.mint_access_token(key["token"])
        (store.PAIRING_DIR / "access-secret").write_text(secrets.token_hex(32) + "\n")
        assert service.authenticate(at["access_token"]) is None
        # 长钥匙不受影响，重新换票恢复
        at2 = service.mint_access_token(key["token"])
        assert service.authenticate(at2["access_token"]) is not None
