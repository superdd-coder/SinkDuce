"""阶段 1：身份与角色内核（roles / acl / policy / visibility）。

这些测试钉住**语义**，不是实现细节：谁能在什么资源上做什么。改动策略引擎时若这里
变红，说明语义变了——先确认是不是想要的，再改测试。

跑法：``pytest tests/test_identity_rbac.py``
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.identity import Actor, can
from src.identity import acl as acl_mod
from src.identity import roles as roles_mod

# ── fixtures ───────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolate_identity(tmp_path, monkeypatch):
    """把角色/ACL 落盘路径指到 tmp，别碰真实 data/。"""
    monkeypatch.setattr(roles_mod, "IDENTITY_DIR", tmp_path)
    monkeypatch.setattr(roles_mod, "ROLES_PATH", tmp_path / "roles.json")
    monkeypatch.setattr(acl_mod, "IDENTITY_DIR", tmp_path)
    monkeypatch.setattr(acl_mod, "DOMAIN_ACL_PATH", tmp_path / "domain_acl.json")
    yield tmp_path


OWNER = Actor()  # 本机
ADMIN = Actor(id="k-admin", kind="key", role_id="admin")
MEMBER = Actor(id="k-member", kind="key", role_id="member")
VISITOR = Actor(id="k-visitor", kind="key", role_id="visitor")
UNKNOWN = Actor(id="k-unknown", kind="key", role_id="role_does_not_exist")

COL = {"collection_id": "col_x"}


# ── roles ──────────────────────────────────────────────────────


class TestBuiltinRoles:
    def test_four_builtins_exist(self):
        assert set(roles_mod.BUILTIN_IDS) == {"owner", "admin", "member", "visitor"}

    def test_owner_is_local_only_and_not_assignable(self):
        owner = roles_mod.BUILTIN_ROLES["owner"]
        assert owner.assignable is False, "owner 不发钥匙（安全锚）"
        assert owner.editable is False

    def test_member_has_no_management_capabilities(self):
        member = roles_mod.BUILTIN_ROLES["member"]
        assert member.capability_set == frozenset(), "member 不应有任何管理能力"
        # 但模块全开（能进各功能区）
        assert member.allows_module("chat") and member.allows_module("database")

    def test_visitor_cannot_enter_tasks_or_recall(self):
        visitor = roles_mod.BUILTIN_ROLES["visitor"]
        assert visitor.allows_module("chat")
        assert visitor.allows_module("database")
        assert not visitor.allows_module("tasks")
        assert not visitor.allows_module("recall")

    def test_unknown_role_returns_none_not_owner(self):
        assert roles_mod.get_role("nope") is None, "未知角色必须 fail-closed"
        assert roles_mod.get_role(None) is None


class TestCustomRoles:
    def test_create_and_list(self, _isolate_identity):
        role = roles_mod.create_custom_role(
            name="标注员", modules={"chat": True}, capabilities={}
        )
        assert role.id.startswith(roles_mod.CUSTOM_PREFIX)
        listed = [r.id for r in roles_mod.list_roles()]
        assert role.id in listed

    def test_builtin_cannot_be_edited_or_deleted(self, _isolate_identity):
        with pytest.raises(roles_mod.RoleError) as e1:
            roles_mod.update_custom_role("admin", name="Hacked")
        assert e1.value.code == "builtin_immutable"
        with pytest.raises(roles_mod.RoleError) as e2:
            roles_mod.delete_custom_role("owner")
        assert e2.value.code == "builtin_immutable"

    def test_custom_role_is_editable(self, _isolate_identity):
        role = roles_mod.create_custom_role(name="X", modules={"chat": True})
        updated = roles_mod.update_custom_role(
            role.id, name="Y", capabilities={"acl.manage": True}
        )
        assert updated.name == "Y"
        assert updated.has("acl.manage")

    def test_deleted_role_disappears(self, _isolate_identity):
        role = roles_mod.create_custom_role(name="Temp")
        roles_mod.delete_custom_role(role.id)
        assert roles_mod.get_role(role.id) is None

    def test_file_content_ignores_builtin_overrides(self, _isolate_identity, tmp_path):
        """手改 roles.json 不能篡改内置角色（内置以代码为准）。"""
        payload = {
            "version": 1,
            "roles": [
                {
                    "id": "visitor",
                    "name": "Sneaky",
                    "builtin": False,
                    "capabilities": {"acl.manage": True},
                    "modules": {"chat": True},
                }
            ],
        }
        (tmp_path / "roles.json").write_text(json.dumps(payload), encoding="utf-8")
        visitor = roles_mod.get_role("visitor")
        assert visitor.has("acl.manage") is False, "磁盘内容不得覆盖内置角色"
        assert visitor.name == "Visitor"


# ── acl ────────────────────────────────────────────────────────


class TestDomainAcl:
    def test_defaults_exclude_visitor_from_three_domains(self):
        acl = acl_mod.load_domain_acl()
        for domain in (acl_mod.DOMAIN_MEETING, acl_mod.DOMAIN_HOT_WORDS, acl_mod.DOMAIN_PEOPLE):
            for tier, role_ids in acl[domain].items():
                assert "visitor" not in role_ids, f"{domain}.{tier} 不该含 visitor"

    def test_meeting_manage_is_admin_only_by_default(self):
        """默认 manage（管**别人**的）只给隐式角色；member 拿到的是 read+create。"""
        assert acl_mod.domain_tier_roles(acl_mod.DOMAIN_MEETING, "manage") == [
            "owner",
            "admin",
        ]
        assert acl_mod.domain_allows(acl_mod.DOMAIN_MEETING, "create", "member")
        assert acl_mod.domain_allows(acl_mod.DOMAIN_MEETING, "read", "member")

    def test_save_rejects_non_subset_tier(self, _isolate_identity):
        bad = acl_mod.default_domain_acl()
        bad[acl_mod.DOMAIN_MEETING]["create"] = ["owner", "admin", "solo_role"]
        bad[acl_mod.DOMAIN_MEETING]["read"] = ["owner", "admin"]
        with pytest.raises(acl_mod.AclError) as e:
            acl_mod.save_domain_acl(bad)
        assert e.value.code == "tier_not_subset"

    def test_save_then_load_roundtrip(self, _isolate_identity):
        acl = acl_mod.default_domain_acl()
        acl[acl_mod.DOMAIN_MEETING]["manage"] = ["owner", "admin", "member"]
        acl_mod.save_domain_acl(acl)
        assert acl_mod.domain_allows(acl_mod.DOMAIN_MEETING, "manage", "member")

    def test_legacy_operate_tier_is_read_as_manage(self, _isolate_identity, tmp_path):
        """存量配置写的是旧档名 `operate` → 读的时候要当成 `manage`（别静默丢弃）。

        静默丢弃等于**悄悄改权限**：用户明明配过"member 能管别人的会议"，
        升级后变成不能，而且界面上看不出来。
        """
        (tmp_path / "domain_acl.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "domains": {
                        "meeting": {
                            "create": ["member"],
                            "read": ["member"],
                            "operate": ["member"],
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        acl = acl_mod.load_domain_acl()
        assert "member" in acl[acl_mod.DOMAIN_MEETING]["manage"], "旧 operate 应映射到 manage"

    def test_missing_file_falls_back_to_defaults(self, _isolate_identity):
        assert acl_mod.load_domain_acl() == acl_mod.default_domain_acl()


class TestCollectionAcl:
    def test_defaults(self):
        acl = acl_mod.default_collection_acl()
        assert "visitor" in acl["read"]
        assert "member" in acl["write"]
        assert acl["delete"] == ["owner", "admin"]
        assert acl["config"] == ["owner", "admin"]

    def test_unknown_collection_uses_defaults(self, monkeypatch):
        monkeypatch.setattr(
            "src.collections.store.get_collection_meta", lambda cid: None
        )
        assert acl_mod.collection_acl("nope") == acl_mod.default_collection_acl()


class TestImplicitRoles:
    """owner / admin 永远有每一档，且**不出现在可勾选名单里**（2026-09-17 用户定）。

    一个关不掉的开关比没有开关更糟：UI 上「取消勾选 admin」是句谎话。所以两侧都
    强制——读时并上（任何直接查 ACL 的代码都不会因为名单里没写而误拒），落盘/传输
    时剥离（不存同一事实两遍，UI 也没有可关的选项）。
    """

    def test_implicit_roles_are_owner_and_admin(self):
        assert acl_mod.IMPLICIT_ROLES == ("owner", "admin")

    def test_every_default_tier_covers_implicit_roles(self):
        for domain, tiers in acl_mod.default_domain_acl().items():
            for tier, role_ids in tiers.items():
                for role_id in acl_mod.IMPLICIT_ROLES:
                    assert role_id in role_ids, f"{domain}.{tier} 缺 {role_id}"
        for tier, role_ids in acl_mod.default_collection_acl().items():
            for role_id in acl_mod.IMPLICIT_ROLES:
                assert role_id in role_ids, f"collection.{tier} 缺 {role_id}"

    def test_disk_file_stores_explicit_roles_only(self, _isolate_identity, tmp_path):
        acl_mod.save_domain_acl(acl_mod.default_domain_acl())
        raw = json.loads((tmp_path / "domain_acl.json").read_text(encoding="utf-8"))
        for domain, tiers in raw["domains"].items():
            for tier, role_ids in tiers.items():
                for role_id in acl_mod.IMPLICIT_ROLES:
                    assert role_id not in role_ids, f"落盘不该含隐式角色：{domain}.{tier}"

    def test_stripped_acl_regains_implicit_roles_on_read(self, _isolate_identity):
        """手改/历史遗留的名单把 owner/admin 删了 → 读回来仍在（自愈，不迁移）。"""
        stripped = acl_mod.default_domain_acl()
        for tiers in stripped.values():
            for tier in tiers:
                tiers[tier] = [
                    r for r in tiers[tier] if r not in acl_mod.IMPLICIT_ROLES
                ]
        acl_mod.save_domain_acl(stripped)
        for domain, tiers in acl_mod.load_domain_acl().items():
            for tier, role_ids in tiers.items():
                assert role_ids[:2] == list(acl_mod.IMPLICIT_ROLES), f"{domain}.{tier}"

    def test_collection_acl_strips_on_write_and_merges_on_read(self, monkeypatch):
        from src.collections import store as collections_store

        written: dict = {}
        monkeypatch.setattr(
            collections_store, "get_collection_meta", lambda cid: {"id": cid}
        )
        monkeypatch.setattr(
            collections_store,
            "set_collection_acl",
            lambda cid, acl: written.update(acl) or {"id": cid},
        )

        saved = acl_mod.save_collection_acl("col_x", {"read": ["member"], "write": []})
        # 落盘只有显式角色（不存两遍）……
        assert written["read"] == ["member"]
        assert written["write"] == []
        # ……但返回值已并上隐式角色（调用方拿去渲染时不会漏 admin）。
        assert saved["read"] == ["owner", "admin", "member"]
        assert saved["delete"] == ["owner", "admin"]
        assert saved["config"] == ["owner", "admin"]

        # 读回同一份盘上内容：隐式角色被并上。
        monkeypatch.setattr(
            collections_store, "get_collection_meta", lambda cid: {"acl": written}
        )
        loaded = acl_mod.collection_acl("col_x")
        assert loaded["read"] == ["owner", "admin", "member"]


# ── policy：四类判定轴 ──────────────────────────────────────────


class TestOwnerPassesEverything:
    @pytest.mark.parametrize(
        "action,resource",
        [
            ("file.upload", COL),
            ("collection.delete", COL),
            ("config.write", {}),
            ("key.manage", {}),
            ("acl.manage", {}),
            ("collection.create", {}),
            ("meeting.delete", {}),
            ("people.delete", {}),
        ],
    )
    def test_owner_allowed(self, action, resource):
        assert can(OWNER, action, resource) is True

    def test_system_actor_allowed(self):
        system = Actor(id="system", kind="system", role_id="owner")
        assert can(system, "config.write", {}) is True


class TestActionTierTable:
    @pytest.mark.parametrize(
        "action,expected",
        [
            ("file.upload", ("collection", "write")),
            ("file.delete", ("collection", "write")),
            ("note.create", ("collection", "write")),
            ("todo.update", ("collection", "write")),
            ("collection.read", ("collection", "read")),
            ("collection.update", ("collection", "config")),
            ("collection.delete", ("collection", "delete")),
            ("meeting.create", ("meeting", "create")),
            ("meeting.delete", ("meeting", "manage")),
            ("hot_words.create", ("hot_words", "write")),
            ("people.read", ("people", "read")),
            ("config.write", ("box", "config.write")),
        ],
    )
    def test_resolve(self, action, expected):
        from src.identity.policy import resolve_action

        assert resolve_action(action) == expected

    def test_unknown_action_unresolved(self):
        from src.identity.policy import resolve_action

        assert resolve_action("totally.made.up") is None


class TestMemberDefaults:
    def test_can_write_collection_content(self):
        assert can(MEMBER, "file.upload", COL) is True
        assert can(MEMBER, "note.create", COL) is True

    def test_cannot_delete_or_reconfigure_collection(self):
        assert can(MEMBER, "collection.delete", COL) is False
        assert can(MEMBER, "collection.update", COL) is False

    def test_cannot_create_collection(self):
        """建库是结构级操作，仅 owner + admin（用户 2026-09-16 定）。"""
        assert can(MEMBER, "collection.create", {}) is False
        assert can(ADMIN, "collection.create", {}) is True

    def test_cannot_write_config(self):
        assert can(MEMBER, "config.write", {}) is False

    def test_meeting_can_create_and_read(self):
        assert can(MEMBER, "meeting.create", {}) is True
        assert can(MEMBER, "meeting.read", {}) is True

    def test_meeting_operate_only_on_own(self):
        """member 删不掉别人的会议，但删得掉自己建的（创建者例外）。"""
        others = {"meeting": {"created_by": "someone-else"}}
        mine = {"meeting": {"created_by": "k-member"}}
        assert can(MEMBER, "meeting.delete", others) is False, "不能删别人的会议"
        assert can(MEMBER, "meeting.delete", mine) is True, "自己建的可以删"

    def test_legacy_meeting_without_creator_is_not_owned(self):
        """存量会议无 created_by → 例外不生效（保守）。"""
        assert can(MEMBER, "meeting.delete", {"meeting": {}}) is False


class TestVisitor:
    def test_reads_collections_only(self):
        assert can(VISITOR, "collection.read", COL) is True
        assert can(VISITOR, "file.upload", COL) is False
        assert can(VISITOR, "note.create", COL) is False

    def test_has_no_meeting_hotwords_people_access(self):
        """visitor = 只读知识库的人，与会议/热词/人物完全无缘。"""
        for action in (
            "meeting.read",
            "meeting.create",
            "meeting.delete",
            "hot_words.read",
            "hot_words.create",
            "people.read",
            "people.create",
        ):
            assert can(VISITOR, action, {}) is False, f"visitor 不该能做 {action}"


class TestFailClosed:
    def test_collection_action_without_collection_id_is_denied(self):
        """漏传资源键 = 后门 → 必须拒绝（这是本设计的核心纪律之一）。"""
        assert can(MEMBER, "file.upload", {}) is False
        assert can(MEMBER, "file.upload", {"collection_id": None}) is False
        assert can(MEMBER, "note.create", {}) is False

    def test_unknown_action_is_denied(self):
        assert can(MEMBER, "made.up.action", COL) is False

    def test_unknown_role_is_denied_not_owner(self):
        """未知角色（数据损坏）绝不能回落成 owner。"""
        assert can(UNKNOWN, "collection.read", COL) is False
        assert can(UNKNOWN, "config.write", {}) is False

    def test_invisible_collection_returns_404_not_403(self, monkeypatch):
        """不可见 → 404（403 会确认"存在但没权限"）。"""
        from fastapi import HTTPException

        from src.identity import authorize

        # 造一个 visitor 不可见的库：read 档去掉 visitor
        monkeypatch.setattr(
            acl_mod,
            "collection_allows",
            lambda cid, tier, role: role != "visitor",
        )
        with pytest.raises(HTTPException) as e:
            authorize(VISITOR, "collection.read", COL)
        assert e.value.status_code == 404

    def test_visible_but_unauthorized_returns_403(self):
        """有读权限但越权（如 member 删库）→ 403（不是 404：他看得见这个库）。"""
        from fastapi import HTTPException

        from src.identity import authorize

        with pytest.raises(HTTPException) as e:
            authorize(MEMBER, "collection.delete", COL)
        assert e.value.status_code == 403


class TestAdminSeesEverything:
    def test_admin_can_do_all_collection_ops(self):
        assert can(ADMIN, "collection.delete", COL) is True
        assert can(ADMIN, "collection.update", COL) is True

    def test_admin_can_manage_keys_and_acl(self):
        assert can(ADMIN, "key.manage", {}) is True
        assert can(ADMIN, "acl.manage", {}) is True


class TestVisibilityHelpers:
    def test_may_see_everything(self):
        from src.identity import may_see_everything

        assert may_see_everything(OWNER) is True
        assert may_see_everything(ADMIN) is True
        assert may_see_everything(MEMBER) is False
        assert may_see_everything(VISITOR) is False

    def test_visible_collection_ids_none_for_admin(self):
        from src.identity import visibility

        assert visibility.visible_collection_ids(OWNER) is None
        assert visibility.visible_collection_ids(ADMIN) is None

    def test_visible_collection_ids_filters_for_member(self, monkeypatch):
        from src.identity import visibility

        monkeypatch.setattr(visibility, "_all_collection_ids", lambda: ["a", "b"])
        monkeypatch.setattr(acl_mod, "collection_allows", lambda cid, tier, role: True)
        assert visibility.visible_collection_ids(MEMBER) == {"a", "b"}

        monkeypatch.setattr(
            acl_mod, "collection_allows", lambda cid, tier, role: cid == "a"
        )
        assert visibility.visible_collection_ids(MEMBER) == {"a"}

    def test_meeting_invisible_to_visitor(self):
        from src.identity import visibility

        assert visibility.is_meeting_visible(VISITOR, "m1") is False
        assert visibility.is_meeting_visible(MEMBER, "m1") is True


class TestActionRegistryCompleteness:
    """回归守卫：代码里每个 authorize 动作名都必须在策略表里登记。

    漏登记的后果是**静默 fail-closed**——策略引擎会拒绝，功能直接坏掉，而且
    报错信息只说"未登记动作"，离"我忘了加一行表"很远。这条测试把它变成编译期
    性质的错误：加了新动作但没登记，这里立刻红。
    """

    def test_every_authorize_call_site_is_registered(self):
        import pathlib
        import re

        from src.identity.policy import resolve_action

        src_root = pathlib.Path(__file__).resolve().parent.parent / "src"
        actions: set[str] = set()
        for path in src_root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for m in re.finditer(r'authorize\(\s*[^,]+,\s*"([a-z_.]+)"', text):
                actions.add(m.group(1))
            for m in re.finditer(r'_actor_for\(\s*"([a-z_.]+)"', text):
                actions.add(m.group(1))

        assert actions, "没扫到任何 authorize 调用——扫描逻辑坏了？"
        unregistered = sorted(a for a in actions if resolve_action(a) is None)
        assert not unregistered, (
            f"这些动作名未在 policy._EXACT / 前缀族里登记，会被 fail-closed 拒绝："
            f"{unregistered}"
        )

    def test_action_table_has_no_orphan_entries(self):
        """（已删除反向检查）

        曾尝试"表里不留未使用条目"，但**留一条多余的空条目无害**（永远不会被调用），
        而能力名（``config.write``/``key.manage``…）与尚未接线的动作（``meeting.archive``、
        ``meeting_group.delete``…）在阶段 3/4/6 接线前必然"未使用"——误报成本远高于
        收益。真正要防的是**反方向**：调用点用了未登记的动作（上面那条守卫）。
        """
        pytest.skip("留待阶段 6 接线完成后可考虑恢复")
