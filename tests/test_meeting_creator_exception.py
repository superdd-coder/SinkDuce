"""会议「创建者例外」现在真的有数据了（2026-09-17）。

**背景**：设计（RBAC §3.3②）写明「member 能操作**自己建的**会议」。`policy._check_domain`
里那条例外也写好了，但**永远取不到 creator**，所以是死代码：

- `Meeting` 模型**没有 `created_by` 字段** → 存不下来；
- `create_meeting()` 没写它；
- 调用 `authorize` 时只传 `{"meeting_id": …}` → policy 那两行 `resource.get("created_by")`
  永远拿到 None。

**后果（用户实测发现）**：把 member 移出 meeting 的 `operate` 档后，member **连自己
刚建的会议都录不了**——`recording-pcm` 一片 403（`update_meeting` 要 operate 档），
录音一个字节都写不进去。日志里能看见客户端在循环重试、每次都 403。

本文件钉住三件事：模型有字段、创建时写入、authorize 调用点把 creator 传下去。
"""

from __future__ import annotations

import json

import pytest

from src.identity import Actor
from src.identity import acl as acl_mod
from src.identity import policy
from src.meeting import store
from src.meeting.models import Meeting, MeetingStatus

MEMBER = Actor(id="k-member", kind="key", role_id="member")
OTHER_MEMBER = Actor(id="k-other", kind="key", role_id="member")


@pytest.fixture
def meetings(tmp_path, monkeypatch):
    root = tmp_path / "meetings"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(store, "MEETINGS_DIR", root)
    store._read_cache.clear()
    yield root


class TestModelHasField:
    def test_meeting_has_created_by(self):
        assert "created_by" in Meeting.model_fields

    def test_default_is_none_for_legacy(self):
        """存量会议没有这个键 → 解析成 None，不炸（向后兼容）。"""
        m = Meeting(id="m1", title="t")
        assert m.created_by is None

    def test_roundtrips_through_serialization(self):
        m = Meeting(id="m1", title="t", created_by="k-member")
        d = store._meeting_to_dict(m)
        assert d.get("created_by") == "k-member"
        assert store._dict_to_meeting(d).created_by == "k-member"


class TestCreateRecordsCreator:
    def test_create_meeting_writes_actor_id(self, meetings, monkeypatch):
        from src.identity.actor import _current, _set_actor

        token = _set_actor(MEMBER)
        try:
            m = store.create_meeting("mine")
        finally:
            _current.reset(token)
        assert m.created_by == "k-member"
        # 落盘也要有（否则重启后丢失）
        raw = json.loads((meetings / m.id / "meta.json").read_text(encoding="utf-8"))
        assert raw.get("created_by") == "k-member"

    def test_owner_meeting_records_local(self, meetings, monkeypatch):
        from src.identity.actor import _current, _set_actor

        token = _set_actor(Actor())  # 本机
        try:
            m = store.create_meeting("host")
        finally:
            _current.reset(token)
        assert m.created_by == "local"


class TestCreatorExceptionNowReachable:
    """核心：manage 档不含 member 时，**创建者本人**仍能操作自己的会议。

    这是用户 2026-09-17 重定档位后的关键性质——「创建权限 = 可以创建转录以及操作
    自己的 Meeting，有自己创建的 Meeting 的所有权限」。
    """

    def _deny_if_manage_excludes_member(self, monkeypatch):
        """把 member 从 manage 档拿掉（= 默认配置：只能管自己的）。"""
        acl = acl_mod.default_domain_acl()
        acl[acl_mod.DOMAIN_MEETING]["manage"] = ["owner", "admin"]
        monkeypatch.setattr(acl_mod, "load_domain_acl", lambda: acl)

    def test_creator_can_operate_own_meeting(self, monkeypatch):
        self._deny_if_manage_excludes_member(monkeypatch)
        # 不抛 = 放行
        policy.authorize(
            MEMBER, "meeting.update", {"meeting_id": "m1", "created_by": "k-member"}
        )

    def test_creator_can_delete_own_meeting(self, monkeypatch):
        self._deny_if_manage_excludes_member(monkeypatch)
        policy.authorize(
            MEMBER, "meeting.delete", {"meeting_id": "m1", "created_by": "k-member"}
        )

    def test_creator_can_discard_and_archive_own_meeting(self, monkeypatch):
        """录制相关的写动作也必须放行——否则"能建但用不了"（用户实测的 bug）。"""
        self._deny_if_manage_excludes_member(monkeypatch)
        for action in ("meeting.finalize", "meeting.archive", "meeting.discard"):
            policy.authorize(
                MEMBER, action, {"meeting_id": "m1", "created_by": "k-member"}
            )

    def test_other_member_still_denied(self, monkeypatch):
        """例外只给创建者，别人照旧 403——**不能变成"member 都能操作"。**"""
        from fastapi import HTTPException

        self._deny_if_manage_excludes_member(monkeypatch)
        with pytest.raises(HTTPException) as e:
            policy.authorize(
                OTHER_MEMBER,
                "meeting.update",
                {"meeting_id": "m1", "created_by": "k-member"},
            )
        assert e.value.status_code == 403

    def test_read_tier_is_not_covered_by_creator_exception(self, monkeypatch):
        """read 档不参与创建者例外：能建但读不到自己的会议是很怪的配置。

        这里用一个**完全没有 read 档**的角色验证：即使 he 是创建者，read 仍被拒。
        """
        from fastapi import HTTPException

        acl = acl_mod.default_domain_acl()
        acl[acl_mod.DOMAIN_MEETING]["read"] = ["owner", "admin"]
        acl[acl_mod.DOMAIN_MEETING]["manage"] = ["owner", "admin"]
        monkeypatch.setattr(acl_mod, "load_domain_acl", lambda: acl)

        with pytest.raises(HTTPException) as e:
            policy.authorize(
                MEMBER, "meeting.read", {"meeting_id": "m1", "created_by": "k-member"}
            )
        assert e.value.status_code == 403

    def test_legacy_meeting_without_creator_is_denied(self, monkeypatch):
        """存量会议（无 creator）→ 例外不生效。保守：不把老数据当"人人所有"。"""
        from fastapi import HTTPException

        self._deny_if_manage_excludes_member(monkeypatch)
        with pytest.raises(HTTPException) as e:
            policy.authorize(MEMBER, "meeting.update", {"meeting_id": "m1"})
        assert e.value.status_code == 403

    def test_whole_meeting_object_shape_works(self, monkeypatch):
        """`require_meeting_operate` 传的是 `{"meeting": <整个对象>}`，
        那条形状也必须能取到 creator（否则录制路由仍然 403）。"""
        self._deny_if_manage_excludes_member(monkeypatch)
        m = Meeting(id="m1", title="t", created_by="k-member")
        policy.authorize(MEMBER, "meeting.update", {"meeting": m.model_dump()})


class TestUpdateMeetingPassesCreator:
    """`update_meeting` 是**录制写盘**的必经之路（recording-pcm → update_meeting）。
    它必须把 created_by 一起给 policy，否则 member 的录制依旧 403。"""

    def test_update_meeting_allows_creator(self, meetings, monkeypatch):
        from src.identity.actor import _current, _set_actor

        acl = acl_mod.default_domain_acl()
        acl[acl_mod.DOMAIN_MEETING]["manage"] = ["owner", "admin"]
        monkeypatch.setattr(acl_mod, "load_domain_acl", lambda: acl)

        # owner 建会（先拿到 id），然后把创建者改成 member 来模拟"member 建的"
        token = _set_actor(Actor())
        try:
            m = store.create_meeting("t")
        finally:
            _current.reset(token)
        store.update_meeting(m.id, created_by="k-member")

        # member 现在应当能改自己这个会议（= 能录）
        from src.meeting.models import MeetingStatus

        token = _set_actor(MEMBER)
        try:
            store.update_meeting(m.id, status=MeetingStatus.recording)
        finally:
            _current.reset(token)
        assert store.get_meeting(m.id).status == MeetingStatus.recording

    def test_update_meeting_denies_non_creator(self, meetings, monkeypatch):
        from fastapi import HTTPException

        from src.identity.actor import _current, _set_actor

        acl = acl_mod.default_domain_acl()
        acl[acl_mod.DOMAIN_MEETING]["manage"] = ["owner", "admin"]
        monkeypatch.setattr(acl_mod, "load_domain_acl", lambda: acl)

        token = _set_actor(Actor())
        try:
            m = store.create_meeting("t")
        finally:
            _current.reset(token)

        token = _set_actor(OTHER_MEMBER)
        try:
            with pytest.raises(HTTPException) as e:
                store.update_meeting(m.id, status=MeetingStatus.recording)
            assert e.value.status_code == 403
        finally:
            _current.reset(token)
