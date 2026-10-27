"""会议分组写操作的授权（2026-09-17）。

**为什么单列**：会议组是**跨会议**的容器，写它的破坏半径比单个会议大——删整组会
级联删掉成员会议（`delete_meetings=True` 是默认值），其中可能混着**别人的**会议。

审查时发现这些端点**完全没有授权**：

    DELETE /meeting-groups/{id}                 删除（默认级联删成员会议）
    PATCH  /meeting-groups/{id}                 改名
    POST   /meeting-groups/{id}/archive|unarchive
    POST   /meeting-groups/{id}/members         加成员
    DELETE /meeting-groups/{id}/members/{mid}   移除成员

它们只经过路由级的 `require_domain("meeting")`（**read 档**）。前端把按钮藏在
`can_operate`（manage 档）后面，但**UI 从来不是墙**——任何 member 直接打这些接口
就能删掉整组连带别人的会议。

**门用 manage 档、不给创建者例外**：组里可能混着别人的会议，逐会议所有权代表不了
"删整组"这一下（与 `visibility.can_manage_meetings` 同一个保守口径）。

跑法：``pytest tests/test_meeting_group_auth.py``
"""

from __future__ import annotations

import asyncio

import pytest

from src.identity import Actor
from src.identity import acl as acl_mod
from src.identity.actor import _current, _set_actor
from src.meeting import routes as mr

MEMBER = Actor(id="k-member", kind="key", role_id="member")
OWNER = Actor()


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    from src.identity import roles as roles_mod

    monkeypatch.setattr(roles_mod, "IDENTITY_DIR", tmp_path)
    monkeypatch.setattr(roles_mod, "ROLES_PATH", tmp_path / "roles.json")
    monkeypatch.setattr(acl_mod, "IDENTITY_DIR", tmp_path)
    monkeypatch.setattr(acl_mod, "DOMAIN_ACL_PATH", tmp_path / "domain_acl.json")
    yield tmp_path


@pytest.fixture
def as_actor():
    tokens = []

    def _run(actor):
        tokens.append(_set_actor(actor))

    yield _run
    for t in reversed(tokens):
        _current.reset(t)


def _fake_group(meeting_ids):
    """造一个最小的 group 替身。

    **注意**：成员列表要在 class 外先算好——class body 看不到外层函数的局部变量，
    直接写 `members = [... for m in members]` 会 NameError（踩过）。
    """
    member_objs = [type("M", (), {"meeting_id": m})() for m in meeting_ids]

    class _G:
        id = "g1"
        title = "G"

    _G.members = member_objs
    return _G()


def _stub_group(monkeypatch, *, members=("m1", "m2")):
    """打桩 group_store + meeting store，避免碰真实数据。"""
    from src.meeting import group_store as gs

    calls: dict = {"deleted_meetings": [], "group_deleted": False, "renamed": False}

    def _group():
        return _fake_group(members)

    monkeypatch.setattr(gs, "get_group", lambda gid: _group(), raising=False)
    monkeypatch.setattr(
        gs, "delete_group", lambda gid: calls.__setitem__("group_deleted", True), raising=False
    )
    monkeypatch.setattr(
        gs, "rename_group", lambda gid, t: calls.__setitem__("renamed", True) or _group(),
        raising=False,
    )
    monkeypatch.setattr(mr.store, "get_meeting", lambda mid: object())
    monkeypatch.setattr(
        mr.store, "delete_meeting",
        lambda mid: calls["deleted_meetings"].append(mid),
    )
    monkeypatch.setattr(mr, "_delete_group_session", lambda gid: None)
    return calls


class TestMemberCannotTouchGroups:
    def test_member_cannot_delete_group(self, as_actor, monkeypatch):
        """**最重要的那条**：默认配置下 member 没有 manage 档，删整组必须 403。

        而且必须在**级联删成员会议之前**就被挡住——否则"删一半发现没权限"。
        """
        from fastapi import HTTPException

        calls = _stub_group(monkeypatch)
        as_actor(MEMBER)
        with pytest.raises(HTTPException) as e:
            asyncio.run(mr.delete_meeting_group("g1"))
        assert e.value.status_code == 403
        assert calls["deleted_meetings"] == [], "被拒后不该删任何成员会议"
        assert calls["group_deleted"] is False

    def test_member_cannot_rename_group(self, as_actor, monkeypatch):
        from fastapi import HTTPException

        calls = _stub_group(monkeypatch)
        as_actor(MEMBER)
        with pytest.raises(HTTPException) as e:
            asyncio.run(mr.rename_meeting_group("g1", {"title": "x"}))
        assert e.value.status_code == 403
        assert calls["renamed"] is False

    def test_member_cannot_archive_group(self, as_actor, monkeypatch):
        from fastapi import HTTPException

        _stub_group(monkeypatch)
        as_actor(MEMBER)
        with pytest.raises(HTTPException) as e:
            asyncio.run(mr.archive_meeting_group("g1"))
        assert e.value.status_code == 403

    def test_member_cannot_add_member(self, as_actor, monkeypatch):
        from fastapi import HTTPException

        _stub_group(monkeypatch)
        as_actor(MEMBER)
        with pytest.raises(HTTPException) as e:
            asyncio.run(mr.add_meeting_group_member("g1", {"meeting_id": "m9"}))
        assert e.value.status_code == 403

    def test_member_cannot_remove_member(self, as_actor, monkeypatch):
        from fastapi import HTTPException

        _stub_group(monkeypatch)
        as_actor(MEMBER)
        with pytest.raises(HTTPException) as e:
            asyncio.run(mr.remove_meeting_group_member("g1", "m1"))
        assert e.value.status_code == 403


class TestManageTierGrantsIt:
    def test_manage_tier_can_delete_group(self, as_actor, monkeypatch):
        acl = acl_mod.default_domain_acl()
        acl[acl_mod.DOMAIN_MEETING]["manage"] = ["owner", "admin", "member"]
        monkeypatch.setattr(acl_mod, "load_domain_acl", lambda: acl)

        calls = _stub_group(monkeypatch)
        as_actor(MEMBER)
        asyncio.run(mr.delete_meeting_group("g1"))
        assert calls["group_deleted"] is True

    def test_owner_always_allowed(self, as_actor, monkeypatch):
        calls = _stub_group(monkeypatch)
        as_actor(OWNER)
        asyncio.run(mr.delete_meeting_group("g1"))
        assert calls["group_deleted"] is True


class TestGateOrder:
    def test_gate_runs_before_cascade(self, as_actor, monkeypatch):
        """授权必须在级联删除**之前**（用户审计过的 A1 类问题）。"""
        from fastapi import HTTPException

        order: list[str] = []
        from src.meeting import group_store as gs

        monkeypatch.setattr(gs, "get_group", lambda gid: _fake_group(("m1",)), raising=False)
        monkeypatch.setattr(gs, "delete_group", lambda gid: order.append("group"), raising=False)
        monkeypatch.setattr(mr.store, "get_meeting", lambda mid: object())
        monkeypatch.setattr(
            mr.store, "delete_meeting", lambda mid: order.append("meeting")
        )
        monkeypatch.setattr(mr, "_delete_group_session", lambda gid: None)

        as_actor(MEMBER)
        with pytest.raises(HTTPException):
            asyncio.run(mr.delete_meeting_group("g1"))
        assert order == [], f"被拒时不该有任何写入，实得 {order}"
