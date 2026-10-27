"""Meeting 权限三档模型（2026-09-17 用户重定）。

**用户的诉求原文**：

> 「Meeting 的权限应该分做读取，创建，管理。然后创建权限应该是可以创建转录以及操作
> 自己的 Meeting，有自己创建的 Meeting 的所有权限，然后管理权限是可以去操作或者删除
> 别人的 Meeting，读取就是纯读取。现在这个权限划分变成我只能创建却不能用这个 Meeting
> 功能，很不合理」

**旧模型为什么不合理**（用户实测踩到）：档位是 `create / read / operate`，创建者例外
挂在 `operate` 上。member 默认只有 `create + read` → 建完会议**连保存转写都被拒**
（`update_meeting` 要 operate 档）→ 前端看到「Failed to save transcript: role 'member'
may not perform 'meeting.update'」「上传失败 (403)」，会议还变成红色草稿点不进去。
「能建但用不了」荒谬。

**新模型**（三档语义）：

| 档位 | 含义 |
|---|---|
| `read` | 纯读取（列表、转写稿、回放、总结、简报） |
| `create` | 能建会议 + **对自己建的会议拥有一切权限**（录制/转写/上传/改名/归档/删除） |
| `manage` | 能操作 / 删除**别人的**会议，以及跨会议的会议组 |

判定式（`policy._check_domain`）：

    放行 ⟺ role ∈ acl[meeting][tier]
          ∨ (tier ≠ read ∧ created_by == actor)

`read` 不参与创建者例外（能建却读不到自己的会很怪；且 create 默认已含 read 子集）。

跑法：``pytest tests/test_meeting_tier_model.py``
"""

from __future__ import annotations

import pytest

from src.identity import Actor
from src.identity import acl as acl_mod
from src.identity import policy
from src.meeting.models import Meeting

MEMBER = Actor(id="k-member", kind="key", role_id="member")
OTHER_MEMBER = Actor(id="k-other", kind="key", role_id="member")
VISITOR = Actor(id="k-visitor", kind="key", role_id="visitor")
OWNER = Actor()

#: 用户重定后的三个档位名（顺序 = UI 展示顺序）。
EXPECTED_TIERS = ("read", "create", "manage")

#: 会写会议内容的动作——它们都必须在"创建者对自己会议"这一条上放行。
WRITE_ACTIONS = (
    "meeting.update",
    "meeting.finalize",
    "meeting.archive",
    "meeting.delete",
    "meeting.discard",
)


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    from src.identity import roles as roles_mod

    monkeypatch.setattr(roles_mod, "IDENTITY_DIR", tmp_path)
    monkeypatch.setattr(roles_mod, "ROLES_PATH", tmp_path / "roles.json")
    monkeypatch.setattr(acl_mod, "IDENTITY_DIR", tmp_path)
    monkeypatch.setattr(acl_mod, "DOMAIN_ACL_PATH", tmp_path / "domain_acl.json")
    yield tmp_path


def _allow(*, read=(), create=(), manage=()):
    """把 meeting 三档设成给定角色（隐式角色由 acl 自动并上）。"""
    acl = acl_mod.default_domain_acl()
    acl[acl_mod.DOMAIN_MEETING] = {
        "read": list(read),
        "create": list(create),
        "manage": list(manage),
    }
    return acl


class TestTierShape:
    def test_three_tiers_are_read_create_manage(self):
        assert acl_mod.DOMAIN_TIERS[acl_mod.DOMAIN_MEETING] == EXPECTED_TIERS

    def test_old_operate_tier_is_gone(self):
        assert "operate" not in acl_mod.DOMAIN_TIERS[acl_mod.DOMAIN_MEETING]

    def test_defaults_give_member_read_and_create(self):
        acl = acl_mod.default_domain_acl()[acl_mod.DOMAIN_MEETING]
        assert "member" in acl["read"]
        assert "member" in acl["create"]
        # 管的**是别人的** → 默认不给 member（创建者例外另算）
        assert "member" not in acl["manage"]

    def test_visitor_has_nothing(self):
        acl = acl_mod.default_domain_acl()[acl_mod.DOMAIN_MEETING]
        for tier in EXPECTED_TIERS:
            assert "visitor" not in acl[tier]


class TestCreateIncludesFullOwnership:
    """**这是用户这条需求的核心**：有 create 档 = 自己建的会议想干什么都行。"""

    @pytest.mark.parametrize("action", WRITE_ACTIONS)
    def test_creator_with_only_create_can_do_every_write(self, monkeypatch, action):
        # member 只有 read + create（正是默认配置）——建完必须能用
        monkeypatch.setattr(
            acl_mod, "load_domain_acl", lambda: _allow(read=("member",), create=("member",))
        )
        policy.authorize(
            MEMBER, action, {"meeting_id": "m1", "created_by": "k-member"}
        )

    def test_creator_is_not_blocked_by_missing_manage(self, monkeypatch):
        """**回归**：旧模型正是"只有 create、没有 operate"→ 保存转写 403。"""
        monkeypatch.setattr(
            acl_mod, "load_domain_acl", lambda: _allow(read=("member",), create=("member",))
        )
        # update_meeting 走的就是这个动作（录制写盘必经）
        policy.authorize(
            MEMBER, "meeting.update", {"meeting_id": "m1", "created_by": "k-member"}
        )

    def test_whole_meeting_object_shape(self, monkeypatch):
        """`require_meeting_operate` 传 `{"meeting": <对象>}` 这条形状也要通。"""
        monkeypatch.setattr(
            acl_mod, "load_domain_acl", lambda: _allow(read=("member",), create=("member",))
        )
        m = Meeting(id="m1", title="t", created_by="k-member")
        policy.authorize(MEMBER, "meeting.update", {"meeting": m.model_dump()})


class TestManageIsForOthersMeetings:
    def test_manage_tier_allows_others_meetings(self, monkeypatch):
        monkeypatch.setattr(
            acl_mod,
            "load_domain_acl",
            lambda: _allow(read=("member",), create=("member",), manage=("member",)),
        )
        # 别人建的也能改
        policy.authorize(
            MEMBER, "meeting.delete", {"meeting_id": "m1", "created_by": "k-other"}
        )

    def test_without_manage_others_meetings_are_denied(self, monkeypatch):
        from fastapi import HTTPException

        monkeypatch.setattr(
            acl_mod, "load_domain_acl", lambda: _allow(read=("member",), create=("member",))
        )
        with pytest.raises(HTTPException) as e:
            policy.authorize(
                MEMBER, "meeting.update", {"meeting_id": "m1", "created_by": "k-other"}
            )
        assert e.value.status_code == 403

    def test_member_cannot_delete_others_meeting_by_default(self, monkeypatch):
        """默认配置下「别人不能删你的会议」——这是用户最初担心的点。"""
        from fastapi import HTTPException

        with pytest.raises(HTTPException):
            policy.authorize(
                OTHER_MEMBER, "meeting.delete", {"meeting_id": "m1", "created_by": "k-member"}
            )


class TestReadIsPureRead:
    def test_read_tier_grants_read(self, monkeypatch):
        monkeypatch.setattr(
            acl_mod, "load_domain_acl", lambda: _allow(read=("member",))
        )
        policy.authorize(MEMBER, "meeting.read", {"meeting_id": "m1"})

    def test_read_alone_grants_no_write(self, monkeypatch):
        """纯读取就是纯读取：没有任何写权限，哪怕是自己建的也不能建。"""
        from fastapi import HTTPException

        monkeypatch.setattr(
            acl_mod, "load_domain_acl", lambda: _allow(read=("member",))
        )
        for action in WRITE_ACTIONS:
            with pytest.raises(HTTPException) as e:
                policy.authorize(
                    MEMBER, action, {"meeting_id": "m1", "created_by": "k-member"}
                )
            assert e.value.status_code == 403, action

    def test_create_requires_create_tier(self, monkeypatch):
        from fastapi import HTTPException

        monkeypatch.setattr(
            acl_mod, "load_domain_acl", lambda: _allow(read=("member",))
        )
        with pytest.raises(HTTPException) as e:
            policy.authorize(MEMBER, "meeting.create", {})
        assert e.value.status_code == 403

    def test_legacy_meeting_without_creator_denies_write(self, monkeypatch):
        """存量会议没有 created_by → 创建者例外无从判定 → 只认 manage 档。"""
        from fastapi import HTTPException

        monkeypatch.setattr(
            acl_mod, "load_domain_acl", lambda: _allow(read=("member",), create=("member",))
        )
        with pytest.raises(HTTPException) as e:
            policy.authorize(MEMBER, "meeting.update", {"meeting_id": "m1"})
        assert e.value.status_code == 403


class TestVisitorExcluded:
    def test_visitor_cannot_read_or_write(self, monkeypatch):
        from fastapi import HTTPException

        for action in ("meeting.read", *WRITE_ACTIONS):
            with pytest.raises(HTTPException):
                policy.authorize(
                    VISITOR, action, {"meeting_id": "m1", "created_by": "k-visitor"}
                )


class TestOwnerBypassesEverything:
    @pytest.mark.parametrize("action", ("meeting.read", "meeting.create", *WRITE_ACTIONS))
    def test_owner_allowed(self, monkeypatch, action):
        monkeypatch.setattr(acl_mod, "load_domain_acl", lambda: _allow())
        policy.authorize(OWNER, action, {"meeting_id": "m1"})
