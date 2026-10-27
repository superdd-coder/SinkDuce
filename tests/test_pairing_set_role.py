"""改已发出钥匙的角色（2026-09-17）。

设计文档曾以「降权瞬间在飞的短票」为由推迟这件事。**核实后该理由不成立**：

- 短票 payload 只装 ``kid`` + ``exp``，**不含角色**；
- ``verify_access_token`` 最后**读盘取长钥匙记录**再返回。

所以权限是每请求实时读的，改角色即时生效，没有残留窗口。

但有一件事必须守住：**不能靠"改角色"绕过签发天花板**——先发一把 member 再改成
admin，效用等同于签发 admin。本文件钉住这条。

跑法：``pytest tests/test_pairing_set_role.py``
"""

from __future__ import annotations

import pytest

from src.identity import roles as roles_mod
from src.pairing import service, store


@pytest.fixture(autouse=True)
def _tmp_pairing(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "PAIRING_DIR", tmp_path)
    monkeypatch.setattr(service, "local_entry_port", lambda: 18900)
    monkeypatch.setattr(roles_mod, "IDENTITY_DIR", tmp_path)
    monkeypatch.setattr(roles_mod, "ROLES_PATH", tmp_path / "roles.json")
    yield tmp_path


def _role_of(token_id: str) -> str | None:
    for rec in store.load():
        if rec.get("id") == token_id:
            return rec.get("role")
    return None


class TestLocalCanChangeRole:
    def test_member_to_visitor(self):
        issued = service.issue_token(name="phone", role="member", is_local=True)
        updated = service.set_token_role(issued["id"], "visitor")
        assert updated is not None
        assert updated["role"] == "visitor"
        assert _role_of(issued["id"]) == "visitor"

    def test_visitor_to_member(self):
        issued = service.issue_token(name="phone", role="visitor", is_local=True)
        service.set_token_role(issued["id"], "member")
        assert _role_of(issued["id"]) == "member"

    def test_member_to_admin(self):
        """本机可以把一把已有钥匙提升为 admin（本机是天花板之源）。"""
        issued = service.issue_token(name="laptop", role="member", is_local=True)
        service.set_token_role(issued["id"], "admin")
        assert _role_of(issued["id"]) == "admin"

    def test_noop_when_same_role(self):
        issued = service.issue_token(name="phone", role="member", is_local=True)
        updated = service.set_token_role(issued["id"], "member")
        assert updated["role"] == "member"


class TestUnknownAndErrors:
    def test_unknown_token_returns_none(self):
        assert service.set_token_role("nope", "member") is None

    def test_unknown_role_rejected(self):
        issued = service.issue_token(name="phone", role="member", is_local=True)
        with pytest.raises(service.PairingError) as e:
            service.set_token_role(issued["id"], "role_nonexistent")
        assert e.value.code == "unknown_role"
        assert _role_of(issued["id"]) == "member", "失败不得改动原角色"

    def test_local_key_cannot_be_reroled(self):
        """本机自动钥匙代表本机，角色恒 owner，不可改。"""
        local = service.ensure_local_key()
        with pytest.raises(service.PairingError) as e:
            service.set_token_role(local["id"], "member")
        assert e.value.code == "not_modifiable"

    def test_owner_key_cannot_be_reroled(self):
        """存量 owner 钥匙（迁移前遗留）也不许改——它是锚。"""
        records = [{"id": "k1", "name": "old", "role": "owner", "token_hash": "h"}]
        store.save(records)
        with pytest.raises(service.PairingError) as e:
            service.set_token_role("k1", "member")
        assert e.value.code == "not_modifiable"


class TestCeilingCannotBeBypassed:
    """**核心**：不能靠改角色绕过签发天花板。"""

    def test_admin_cannot_promote_key_to_admin(self):
        """持 admin 钥匙的设备：不能把一把 member 钥匙改成 admin。"""
        issued = service.issue_token(name="phone", role="member", is_local=True)
        with pytest.raises(service.PairingError) as e:
            service.set_token_role(issued["id"], "admin", issuer_role="admin")
        assert e.value.code == "role_not_grantable"
        assert _role_of(issued["id"]) == "member", "被拒时角色不变"

    def test_admin_can_demote_to_visitor(self):
        """降权是允许的（admin 能做）。"""
        issued = service.issue_token(name="phone", role="member", is_local=True)
        service.set_token_role(issued["id"], "visitor", issuer_role="admin")
        assert _role_of(issued["id"]) == "visitor"

    def test_member_cannot_change_anything(self):
        issued = service.issue_token(name="phone", role="member", is_local=True)
        with pytest.raises(service.PairingError) as e:
            service.set_token_role(issued["id"], "visitor", issuer_role="member")
        assert e.value.code == "role_not_grantable"

    def test_visitor_cannot_change_anything(self):
        issued = service.issue_token(name="phone", role="member", is_local=True)
        with pytest.raises(service.PairingError):
            service.set_token_role(issued["id"], "visitor", issuer_role="visitor")


class TestImmediateEffectOnChecks:
    """改角色对**下一次判定**即时生效（这正是"短票无残留"的实证）。"""

    def test_role_change_reflected_in_verify(self):
        from src.identity import Actor, can

        issued = service.issue_token(name="phone", role="member", is_local=True)
        actor = Actor(id=issued["id"], kind="key", role_id="member")
        assert can(actor, "collection.read", {"collection_id": "c1"}) is True

        service.set_token_role(issued["id"], "visitor")
        rec = service.verify_token(issued["token"])
        assert rec is not None
        new_actor = Actor(id=rec["id"], kind="key", role_id=rec["role"])
        # 降权后：visitor 不能写
        assert can(new_actor, "file.upload", {"collection_id": "c1"}) is False

    def test_short_token_carries_no_role(self):
        """短票 payload 不含角色（这是"改角色即时生效"能成立的根据）。"""
        import base64
        import json

        issued = service.issue_token(name="phone", role="member", is_local=True)
        minted = service.mint_access_token(issued["token"])
        # 形如 "sda1_<b64url>.<sig>"：先剥前缀，再取 body
        raw = minted["access_token"]
        b64 = raw.split("_", 1)[1].split(".", 1)[0]
        padded = b64 + "=" * (-len(b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        assert "role" not in payload, "短票不该固化角色——否则改角色对在飞短票无效"
        assert set(payload) == {"kid", "exp"}
