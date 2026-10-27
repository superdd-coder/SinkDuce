"""阶段 6：权限管理端点（identity API）的权限边界与结构。

钉住三件事：
1. ``/me`` 任何已认证都能读（UI 靠它化妆，拿不到就整页失灵）；
2. 角色/ACL 管理需要 ``acl.manage``（owner + admin），其余角色 403；
3. 内置角色不可改/删；在用角色删不掉（409 + 计数）。

跑法：``pytest tests/test_identity_api.py``
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.identity import acl as acl_mod
from src.identity import roles as roles_mod
from src.identity.actor import _current, _set_actor
from src.identity import Actor


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(roles_mod, "IDENTITY_DIR", tmp_path)
    monkeypatch.setattr(roles_mod, "ROLES_PATH", tmp_path / "roles.json")
    monkeypatch.setattr(acl_mod, "IDENTITY_DIR", tmp_path)
    monkeypatch.setattr(acl_mod, "DOMAIN_ACL_PATH", tmp_path / "domain_acl.json")
    # 钥匙计数：别读真实 data/
    from src.pairing import store as pairing_store

    monkeypatch.setattr(pairing_store, "PAIRING_DIR", tmp_path / "pairing")
    yield tmp_path


@pytest.fixture
def local_client():
    """本机（回环）客户端 —— owner 全权。"""
    from src.main import app

    return TestClient(app, base_url="http://127.0.0.1:18900", client=("127.0.0.1", 51000))


@pytest.fixture
def as_key():
    """以指定角色的钥匙身份运行（模拟门卫注入的身份）。"""
    tokens = []

    def _run(role_id: str, actor_id: str = "k1"):
        tokens.append(_set_actor(Actor(id=actor_id, kind="key", role_id=role_id)))

    yield _run
    for t in reversed(tokens):  # LIFO：正序 reset 会残留身份、污染后续测试
        _current.reset(t)


# ── /me：必须宽容 ─────────────────────────────────────────────


class TestMe:
    def test_owner_me(self, local_client):
        r = local_client.get("/api/identity/me")
        assert r.status_code == 200
        body = r.json()
        assert body["role_id"] == "owner"
        assert body["is_local"] is True
        assert body["capabilities"]["acl.manage"] is True

    def test_visitor_me_is_allowed_and_narrow(self, as_key):
        """visitor 也要能拿到自己的画像（UI 靠它决定隐藏哪些入口）。"""
        from src.api.routes.identity import get_me

        as_key("visitor")
        body = get_me(None)  # type: ignore[arg-type]
        assert body["role_id"] == "visitor"
        # 形状=完整键集（前端按"键是否存在"渲染），但**值全为 False**——无任何能力
        assert all(v is False for v in body["capabilities"].values())
        assert set(body["capabilities"]) == set(roles_mod.ALL_CAPABILITIES)
        assert body["is_local"] is False

    def test_unknown_role_returns_minimal_profile_not_500(self, as_key):
        from src.api.routes.identity import get_me

        as_key("role_gone")
        body = get_me(None)  # type: ignore[arg-type]
        assert body["role_id"] == "role_gone"
        assert body["capabilities"] == {}


class TestCapabilityCatalogue:
    def test_lists_modules_and_capabilities(self, local_client):
        r = local_client.get("/api/identity/capabilities")
        assert r.status_code == 200
        body = r.json()
        assert set(body["modules"]) == set(roles_mod.ALL_MODULES)
        assert set(body["capabilities"]) == set(roles_mod.ALL_CAPABILITIES)
        assert body["owner_only_capabilities"] == ["key.manage"]


# ── 角色管理：需要 acl.manage ──────────────────────────────────


class TestRoleManagement:
    def test_owner_can_list_roles(self, local_client):
        r = local_client.get("/api/identity/roles")
        assert r.status_code == 200
        ids = [x["id"] for x in r.json()["roles"]]
        assert ids[:4] == ["owner", "admin", "member", "visitor"]

    def test_member_cannot_manage_roles(self, as_key):
        from fastapi import HTTPException

        from ee.api.identity import list_roles

        as_key("member")
        with pytest.raises(HTTPException) as e:
            list_roles()
        assert e.value.status_code == 403

    def test_visitor_cannot_manage_roles(self, as_key):
        from fastapi import HTTPException

        from ee.api.identity import list_roles

        as_key("visitor")
        with pytest.raises(HTTPException) as e:
            list_roles()
        assert e.value.status_code == 403

    def test_create_custom_role(self, local_client):
        r = local_client.post(
            "/api/identity/roles",
            json={"name": "标注员", "modules": {"chat": True}, "capabilities": {}},
        )
        assert r.status_code == 200
        assert r.json()["id"].startswith("role_")
        assert r.json()["builtin"] is False

    def test_builtin_role_cannot_be_edited(self, local_client):
        r = local_client.put("/api/identity/roles/admin", json={"name": "Hacked"})
        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "builtin_immutable"

    def test_builtin_role_cannot_be_deleted(self, local_client):
        r = local_client.delete("/api/identity/roles/visitor")
        assert r.status_code == 403

    def test_empty_name_rejected(self, local_client):
        r = local_client.post("/api/identity/roles", json={"name": "   "})
        assert r.status_code == 400

    def test_role_in_use_cannot_be_deleted(self, local_client, monkeypatch):
        """在用角色删不掉：直接删会让那些钥匙悄悄失去全部权限。"""
        from ee.api import identity as id_mod

        role = local_client.post("/api/identity/roles", json={"name": "临时"}).json()
        monkeypatch.setattr(id_mod, "_key_counts", lambda: {role["id"]: 2})
        r = local_client.delete(f"/api/identity/roles/{role['id']}")
        assert r.status_code == 409
        assert r.json()["detail"]["code"] == "role_in_use"
        assert r.json()["detail"]["params"]["count"] == 2

    def test_unused_role_delete_ok(self, local_client, monkeypatch):
        from ee.api import identity as id_mod

        role = local_client.post("/api/identity/roles", json={"name": "临时"}).json()
        monkeypatch.setattr(id_mod, "_key_counts", lambda: {})
        assert local_client.delete(f"/api/identity/roles/{role['id']}").status_code == 200


class TestGrantCeilingOnCapabilities:
    """授予天花板：不得授予自己没有的能力；key.manage 只能本机授予。"""

    def test_local_can_grant_key_manage(self, local_client):
        """本机（owner）可以造出含 key.manage 的角色——它是该能力的源头。"""
        r = local_client.post(
            "/api/identity/roles",
            json={"name": "co-admin", "capabilities": {"key.manage": True}},
        )
        assert r.status_code == 200
        assert r.json()["capabilities"]["key.manage"] is True

    def test_admin_cannot_grant_key_manage(self, as_key):
        """admin 想造一个含 key.manage 的角色 → 拒（否则天花板失守）。"""
        from fastapi import HTTPException

        from ee.api.identity import create_role

        as_key("admin")
        with pytest.raises(HTTPException) as e:
            create_role({"name": "x", "capabilities": {"key.manage": True}})
        assert e.value.status_code == 403
        assert e.value.detail["code"] == "capability_owner_only"

    def test_visitor_cannot_grant_anything(self, as_key):
        from fastapi import HTTPException

        from ee.api.identity import create_role

        as_key("visitor")
        with pytest.raises(HTTPException) as e:
            create_role({"name": "x", "capabilities": {"config.write": True}})
        assert e.value.status_code == 403


# ── 领域 ACL ──────────────────────────────────────────────────


class TestDomainAclApi:
    def test_owner_can_read_domains(self, local_client):
        r = local_client.get("/api/identity/domain-acl")
        assert r.status_code == 200
        body = r.json()
        assert set(body["domains"]) == {"meeting", "hot_words", "people"}
        assert body["tiers"]["people"] == list(acl_mod.DOMAIN_TIERS["people"])

    def test_owner_can_write_domains(self, local_client):
        acl = local_client.get("/api/identity/domain-acl").json()["domains"]
        acl["meeting"]["manage"] = ["owner", "admin", "member"]
        r = local_client.put("/api/identity/domain-acl", json={"domains": acl})
        assert r.status_code == 200
        assert "member" in r.json()["domains"]["meeting"]["manage"]

    def test_non_subset_rejected(self, local_client):
        """create 不是 read 的子集 → 拒（能创建却看不到自己创建的会议是荒谬配置）。"""
        acl = local_client.get("/api/identity/domain-acl").json()["domains"]
        acl["meeting"]["read"] = ["owner", "admin"]
        acl["meeting"]["create"] = ["owner", "admin", "member"]
        r = local_client.put("/api/identity/domain-acl", json={"domains": acl})
        assert r.status_code == 400
        assert r.json()["detail"]["code"] == "tier_not_subset"

    def test_member_cannot_read_domain_acl(self, as_key):
        from fastapi import HTTPException

        from ee.api.identity import get_domain_acl

        as_key("member")
        with pytest.raises(HTTPException) as e:
            get_domain_acl()
        assert e.value.status_code == 403


# ── 每库 ACL ──────────────────────────────────────────────────


class TestCollectionAclApi:
    def test_owner_can_read_and_write(self, local_client, monkeypatch):
        monkeypatch.setattr(
            "src.collections.store.get_collection_meta",
            lambda cid: {"id": cid, "name": "x"},
        )
        monkeypatch.setattr(
            "src.collections.store.set_collection_acl", lambda cid, acl: {"id": cid, "acl": acl}
        )
        r = local_client.get("/api/identity/collection-acl/col_1")
        assert r.status_code == 200
        r2 = local_client.put(
            "/api/identity/collection-acl/col_1",
            json={"acl": {"read": ["owner"], "write": [], "delete": [], "config": []}},
        )
        assert r2.status_code == 200

    def test_member_cannot_change_collection_acl(self, as_key):
        """能写数据 ≠ 能改权限规则（堵死"能写文件就给自己提权"）。"""
        from fastapi import HTTPException

        from ee.api.identity import put_collection_acl

        as_key("member")
        with pytest.raises(HTTPException) as e:
            put_collection_acl("col_1", {"acl": {}})
        assert e.value.status_code == 403


# ── 隐式角色（owner/admin）不该出现成开关 ───────────────────────


class TestImplicitRoleNotInLists:
    """UI 拿 ``implicit_roles`` 过滤掉 owner/admin，后端在两侧都强制并上。

    这里钉住"UI 过滤有据可依"：接口必须**声明**哪些角色是隐式的，否则前端只能硬编码
    ``owner``/``admin`` 字符串——那样自定义角色或改名就会漂。
    """

    def test_domain_acl_response_declares_implicit_roles(self, local_client):
        body = local_client.get("/api/identity/domain-acl").json()
        assert body["implicit_roles"] == ["owner", "admin"]

    def test_collection_acl_response_declares_implicit_roles(
        self, local_client, monkeypatch
    ):
        monkeypatch.setattr(
            "src.collections.store.get_collection_meta",
            lambda cid: {"id": cid, "name": "x"},
        )
        body = local_client.get("/api/identity/collection-acl/col_1").json()
        assert body["implicit_roles"] == ["owner", "admin"]

    def test_put_domain_acl_stripped_still_returns_implicit(
        self, local_client
    ):
        """客户端（或旧版前端）把 owner/admin 全去掉提交 → 读回仍有（自愈）。"""
        r = local_client.put(
            "/api/identity/domain-acl",
            json={"domains": {"meeting": {"create": ["member"], "read": ["member"], "operate": []}}},
        )
        assert r.status_code == 200
        for tier, role_ids in r.json()["domains"]["meeting"].items():
            assert role_ids[:2] == ["owner", "admin"], f"meeting.{tier} 缺隐式角色"


# ── /collections 的 permissions 探针（2026-09-17）─────────────────
#
# 用户报的 bug 的修法依赖这条链：`/collections` 带上每库四档 → 前端据此不画
# 设置入口。这里钉住"响应真的带了这个字段，且值随身份变"。


class TestCollectionsPermissionsPayload:
    def _stub(self, monkeypatch):
        """打桩 services.db（测试环境里它没初始化）+ meta 读取。"""
        from src.api.routes import collections as col_mod

        class _Db:
            @staticmethod
            def list_collections():
                return ["col_a"]

            @staticmethod
            def get_collection_info(cid):
                return {"points_count": 0}

        monkeypatch.setattr(col_mod.services, "db", _Db(), raising=False)
        monkeypatch.setattr(
            "src.collections.store.get_collection_meta",
            lambda cid: {"id": cid, "name": "A"},
        )

    def test_member_sees_config_false(self, as_key, monkeypatch):
        """member 对 read/write=True 的库，config/delete 必须是 False。"""
        from src.api.routes.collections import list_collections

        self._stub(monkeypatch)
        monkeypatch.setattr(
            acl_mod,
            "collection_allows",
            lambda cid, tier, role: tier in ("read", "write"),
        )
        as_key("member")
        rows = list_collections()
        assert len(rows) == 1
        assert rows[0]["permissions"] == {
            "read": True,
            "write": True,
            "delete": False,
            "config": False,
        }

    def test_owner_sees_all_true(self, as_key, monkeypatch):
        from src.api.routes.collections import list_collections

        self._stub(monkeypatch)
        monkeypatch.setattr(acl_mod, "collection_allows", lambda cid, tier, role: False)
        as_key("owner")
        assert all(list_collections()[0]["permissions"].values())

    def test_probe_failure_is_fail_closed(self, as_key, monkeypatch):
        """探针本身炸了 → 四档 False（少画入口），而不是让请求 500 或默认放行。"""
        from src.api.routes.collections import list_collections
        from src.identity import visibility

        self._stub(monkeypatch)
        as_key("member")

        def _boom(actor, cid):
            raise RuntimeError("probe exploded")

        monkeypatch.setattr(visibility, "collection_permissions", _boom)
        rows = list_collections()
        assert rows[0]["permissions"] == {
            "read": False,
            "write": False,
            "delete": False,
            "config": False,
        }


# ── 会议录制所需的读端点必须对 member 开放（2026-09-17）────────────
#
# **用户报的症状**：远端 member 录制会议时看到「未配置文件转写服务。请到设置 → 转写」，
# 实时转写与实时纪要也用不了。根因是 config.py 的写闸门把**转写 provider 列表**也
# 一起收了口，前端 `.catch(() => setHasXxxProvider(false))` 把 403 吃成"没配服务"。
# **一个 403 制造了三个表面症状。**
#
# 这两个列表是数据面（录制可用性判定），信息类别与已开放的 /llm/providers 相同。


class TestTranscriptionReadsAreOpen:
    def test_transcription_provider_lists_are_read_open(self):
        """钉住豁免表本身，防止将来有人"顺手收紧"又造出同一个 bug。"""
        from src.api.routes.config import _READ_OPEN_SUFFIXES

        assert "/transcription/file-providers" in _READ_OPEN_SUFFIXES
        assert "/transcription/realtime-providers" in _READ_OPEN_SUFFIXES

    def test_member_can_read_but_not_write_transcription(self, as_key):
        """读放开、写仍然要 config.write —— 别把豁免做成后门。"""
        from fastapi import HTTPException

        from src.api.routes.config import _config_write_gate

        class _Req:
            def __init__(self, method, path):
                self.method = method
                self.url = type("U", (), {"path": path})()

        as_key("member")
        # 读：放行（不抛）
        _config_write_gate(_Req("GET", "/api/transcription/file-providers"))
        _config_write_gate(_Req("GET", "/api/transcription/realtime-providers"))
        # 写：仍要 403
        with pytest.raises(HTTPException) as e:
            _config_write_gate(_Req("POST", "/api/transcription/file-providers"))
        assert e.value.status_code == 403

    def test_other_config_reads_still_gated(self, as_key):
        """只有被点名的豁免放开；其余配置读仍要 config.read。"""
        from fastapi import HTTPException

        from src.api.routes.config import _config_write_gate

        class _Req:
            def __init__(self, method, path):
                self.method = method
                self.url = type("U", (), {"path": path})()

        as_key("member")
        for path in ("/api/config", "/api/transcription/live-translate-providers"):
            with pytest.raises(HTTPException) as e:
                _config_write_gate(_Req("GET", path))
            assert e.value.status_code == 403, path


# ── /me can_create：创建入口的化妆信号（与墙同一引擎）────────────


class TestCanCreate:
    def test_owner_can_create_both(self, local_client):
        r = local_client.get("/api/identity/me")
        assert r.status_code == 200
        body = r.json()
        assert body["can_create"]["collection"] is True
        assert body["can_create"]["meeting"] is True

    def test_visitor_cannot_create_anything(self, as_key):
        from src.api.routes.identity import get_me

        as_key("visitor")
        body = get_me(None)  # type: ignore[arg-type]
        assert body["can_create"]["collection"] is False
        assert body["can_create"]["meeting"] is False

    def test_member_collection_no_meeting_yes(self, as_key):
        """内置默认：member 不能建库（结构级操作），但有会议 create 档。"""
        from src.api.routes.identity import get_me

        as_key("member")
        body = get_me(None)  # type: ignore[arg-type]
        assert body["can_create"]["collection"] is False
        assert body["can_create"]["meeting"] is True

    def test_meeting_create_follows_domain_acl(self, as_key):
        """收掉 member 的会议 create 档后信号反转——与 store 建会那道墙同源。"""
        from src.api.routes.identity import get_me

        as_key("member")
        body = get_me(None)  # type: ignore[arg-type]
        assert body["can_create"]["meeting"] is True

        acl_mod.save_domain_acl_payload(
            {"domains": {"meeting": {"read": ["member"], "create": [], "manage": []}}}
        )
        body = get_me(None)  # type: ignore[arg-type]
        assert body["can_create"]["meeting"] is False

    def test_unknown_role_failclosed(self, as_key):
        from src.api.routes.identity import get_me

        as_key("role_gone")
        body = get_me(None)  # type: ignore[arg-type]
        assert body["can_create"] == {"collection": False, "meeting": False}
