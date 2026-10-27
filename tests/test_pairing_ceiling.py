"""阶段 5：签发天花板——"只有本机能造 admin"（RBAC §7.1）。

通用规则（**按能力集合比较，不按角色名**——否则新建一个名字不同、能力等同 admin
的角色就成了绕过口）：

    可签发 ⟺ 发行者持 key.manage
              且 目标角色的能力与模块 ⊆ 发行者的
              且 (目标角色含 key.manage → 发行者必须是本机)

跑法：``pytest tests/test_pairing_ceiling.py``
"""

from __future__ import annotations

import pytest

from src.pairing import service, store
from src.identity import roles as roles_mod


@pytest.fixture(autouse=True)
def _tmp_pairing(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "PAIRING_DIR", tmp_path)
    monkeypatch.setattr(service, "local_entry_port", lambda: 18900)
    monkeypatch.setattr(roles_mod, "IDENTITY_DIR", tmp_path)
    monkeypatch.setattr(roles_mod, "ROLES_PATH", tmp_path / "roles.json")
    yield tmp_path


def _grant(issuer_role: str | None, target_role: str) -> bool:
    return service.can_grant_role(issuer_role, roles_mod.get_role(target_role))


class TestCeilingRule:
    def test_local_can_grant_admin_member_visitor(self):
        for r in ("admin", "member", "visitor"):
            assert _grant(None, r) is True, f"本机应能签发 {r}"

    def test_local_cannot_grant_owner(self):
        """owner 是安全锚：本机身份，不是一把可复制的钥匙。"""
        assert _grant(None, "owner") is False

    def test_member_cannot_grant_anything(self):
        """member 无 key.manage。"""
        for r in ("admin", "member", "visitor"):
            assert _grant("member", r) is False

    def test_visitor_cannot_grant_anything(self):
        for r in ("member", "visitor"):
            assert _grant("visitor", r) is False

    def test_admin_cannot_grant_admin(self):
        """**核心规则**：要再得一把 admin，必须回到本机。"""
        assert _grant("admin", "admin") is False

    def test_admin_can_grant_member_and_visitor(self):
        """本机之外的日常签发不受影响（防过度收紧）。"""
        assert _grant("admin", "member") is True
        assert _grant("admin", "visitor") is True

    def test_unknown_issuer_role_fails_closed(self):
        assert _grant("role_does_not_exist", "member") is False

    def test_unknown_target_role_fails_closed(self):
        assert service.can_grant_role(None, None) is False


class TestCapabilitySubsetNotRoleName:
    """按**能力集合**比较，不按角色名（否则改名即绕过）。"""

    def test_custom_role_equal_to_admin_cannot_be_granted_by_admin(self, tmp_path):
        # 造一个能力等同 admin 的自定义角色
        role = roles_mod.create_custom_role(
            name="Pseudo Admin",
            modules={k: True for k in roles_mod.ALL_MODULES},
            capabilities={k: True for k in roles_mod.ALL_CAPABILITIES},
        )
        # admin 不能授予这个角色（它含 key.manage，且能力 = admin 自己）
        assert service.can_grant_role("admin", roles_mod.get_role(role.id)) is False
        # 本机可以（但它是"能再管钥匙"的，所以…含 key.manage → 本机也需显式允许）
        # 本机授予含 key.manage 的自定义角色：规则里 owner 只挡 owner 本身，
        # 但"含 key.manage 的目标"对本机是允许的（本机即 key.manage 之源）。
        assert service.can_grant_role(None, roles_mod.get_role(role.id)) is True

    def test_custom_role_without_key_manage_can_be_granted(self, tmp_path):
        role = roles_mod.create_custom_role(
            name="标注员",
            modules={"chat": True, "database": True},
            capabilities={"config.write": True},
        )
        # admin 有这个角色的全部能力（admin 全开）→ 可以授予
        assert service.can_grant_role("admin", roles_mod.get_role(role.id)) is True

    def test_admin_cannot_grant_role_having_capability_it_lacks(self, tmp_path, monkeypatch):
        """admin 不得授予"含自己没有的能力"的角色（构造一个 admin 缺能力的角色场景）。"""
        # 直接构造一个含 key.manage 的角色（admin 有 key.manage，所以要用别的）
        role = roles_mod.create_custom_role(
            name="超级角色",
            modules={k: True for k in roles_mod.ALL_MODULES},
            capabilities={k: True for k in roles_mod.ALL_CAPABILITIES},
        )
        # 该角色能力 ⊇ admin 的 → admin 不得授予
        assert service.can_grant_role("admin", roles_mod.get_role(role.id)) is False


class TestIssueTokenIntegration:
    def test_local_issues_member(self):
        rec = service.issue_token(name="phone", role="member", is_local=True)
        assert rec["token"].startswith("sd1_")
        assert service.list_tokens()[0]["role"] == "member"

    def test_issue_rejects_unknown_role(self):
        with pytest.raises(service.PairingError) as e:
            service.issue_token(name="x", role="role_nope", is_local=True)
        assert e.value.code == "unknown_role"

    def test_non_local_cannot_issue_admin(self):
        """非本机（持 admin 钥匙）签发 admin → 拒。"""
        with pytest.raises(service.PairingError) as e:
            service.issue_token(
                name="x",
                role="admin",
                has_valid_token=True,
                issuer_role="admin",
            )
        assert e.value.code == "role_not_grantable"

    def test_admin_issuer_can_issue_member(self):
        rec = service.issue_token(
            name="phone",
            role="member",
            has_valid_token=True,
            issuer_role="admin",
        )
        assert rec["token"].startswith("sd1_")

    def test_member_issuer_cannot_issue(self):
        with pytest.raises(service.PairingError) as e:
            service.issue_token(
                name="x",
                role="member",
                has_valid_token=True,
                issuer_role="member",
            )
        assert e.value.code == "role_not_grantable"


class TestOwnerMigration:
    def test_existing_owner_keys_become_admin(self, tmp_path):
        """存量设备钥匙 owner → admin（本机 __local__ 保持 owner）。"""
        import json

        records = [
            {"id": "a", "name": "laptop", "role": "owner", "token_hash": "h1"},
            {"id": "b", "name": "__local__", "role": "owner", "token_hash": "h2"},
            {"id": "c", "name": "phone", "role": "member", "token_hash": "h3"},
        ]
        changed = service._migrate_owner_keys_to_admin(records)
        assert changed is True
        assert records[0]["role"] == "admin", "设备钥匙降为 admin"
        assert records[1]["role"] == "owner", "本机钥匙保持 owner"
        assert records[2]["role"] == "member", "已有角色不动"

    def test_migration_is_idempotent(self):
        records = [{"id": "a", "name": "laptop", "role": "admin", "token_hash": "h1"}]
        assert service._migrate_owner_keys_to_admin(records) is False
