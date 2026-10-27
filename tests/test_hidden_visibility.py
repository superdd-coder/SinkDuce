"""阶段 3：读路径隐藏（"完全隐藏"的矩阵测试）。

承重性质：**不可见 = 该资源根本不进入结果集**，不是"查出来再过滤"。所以这些测试
主要断言"拿不到"，而不是"被标记了不可见"——后者意味着数据已经到手。

跑法：``pytest tests/test_hidden_visibility.py``
"""

from __future__ import annotations

import pytest

from src.identity import Actor
from src.identity import acl as acl_mod

MEMBER = Actor(id="k-mem", kind="key", role_id="member")
VISITOR = Actor(id="k-vis", kind="key", role_id="visitor")
OWNER = Actor()
ADMIN = Actor(id="k-adm", kind="key", role_id="admin")


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    from src.identity import roles as roles_mod

    monkeypatch.setattr(roles_mod, "IDENTITY_DIR", tmp_path)
    monkeypatch.setattr(roles_mod, "ROLES_PATH", tmp_path / "roles.json")
    monkeypatch.setattr(acl_mod, "IDENTITY_DIR", tmp_path)
    monkeypatch.setattr(acl_mod, "DOMAIN_ACL_PATH", tmp_path / "domain_acl.json")
    yield tmp_path


def _hide_collection(monkeypatch, hidden: str, visible: list[str]):
    """把 `hidden` 设为对 member/visitor 不可见，visible 里的保持可见。"""

    def _allows(cid, tier, role):
        if role in ("owner", "admin"):
            return True
        if cid == hidden:
            return False
        return cid in visible

    monkeypatch.setattr(acl_mod, "collection_allows", _allows)


# ── ① catalog 枚举 ────────────────────────────────────────────


class TestCatalogHiding:
    def test_explicit_list_is_filtered_too(self, monkeypatch):
        """**点名通道也必须过滤**：显式传一个不可见库，不能成为绕过枚举的路径。"""
        from src.identity import visibility

        monkeypatch.setattr(visibility, "_all_collection_ids", lambda: ["secret", "public"])
        _hide_collection(monkeypatch, "secret", ["public"])

        vis = visibility.visible_collection_ids(MEMBER)
        assert vis == {"public"}

    def test_owner_sees_all(self, monkeypatch):
        from src.identity import visibility

        assert visibility.visible_collection_ids(OWNER) is None
        assert visibility.visible_collection_ids(ADMIN) is None

    def test_filter_collection_ids_drops_hidden(self, monkeypatch):
        from src.identity import visibility

        monkeypatch.setattr(visibility, "_all_collection_ids", lambda: ["secret", "public"])
        _hide_collection(monkeypatch, "secret", ["public"])
        assert visibility.filter_collection_ids(MEMBER, ["secret", "public"]) == ["public"]
        assert visibility.filter_collection_ids(MEMBER, ["secret"]) == []


# ── ② 检索层：隐藏库根本不查 ─────────────────────────────────


class TestRetrieverHiding:
    def test_retrieve_returns_empty_for_hidden_collection(self, monkeypatch):
        """隐藏库必须**在检索阶段**就短路——返回空，而不是查了再筛。"""
        from src.rag.retriever import Retriever

        def _boom(*a, **k):
            raise AssertionError("不应触碰 embedding/db —— 隐藏库必须提前短路")

        r = Retriever.__new__(Retriever)  # 不跑 __init__（需要真 provider/db）
        r.db = None
        r.embedding = type("E", (), {"embed_query": staticmethod(_boom)})()

        import src.identity.visibility as vis

        monkeypatch.setattr(vis, "is_collection_visible", lambda actor, cid: False)
        assert r.retrieve("q", collection="secret") == []

    def test_retrieve_proceeds_for_visible_collection(self, monkeypatch):
        """可见库照常走（防过度收紧把检索弄坏）。"""
        from src.rag.retriever import Retriever

        import src.identity.visibility as vis

        monkeypatch.setattr(vis, "is_collection_visible", lambda actor, cid: True)

        r = Retriever.__new__(Retriever)
        r.db = None
        calls = {"n": 0}

        class _Emb:
            def embed_query(self, q):
                calls["n"] += 1
                raise RuntimeError("stop-here")  # 证明已经越过可见性闸门

        r.embedding = _Emb()
        with pytest.raises(RuntimeError, match="stop-here"):
            r.retrieve("q", collection="public")
        assert calls["n"] == 1


# ── ⑧ 事件流：按作用域过滤 ────────────────────────────────────


class TestEventVisibility:
    def _ev(self, scope):
        class _E:
            pass

        e = _E()
        e.scope = scope
        return e

    def test_hidden_collection_event_is_dropped(self, monkeypatch):
        from src.events.routes import _event_visible_to

        _hide_collection(monkeypatch, "secret", ["public"])
        assert _event_visible_to(MEMBER, self._ev({"collection_id": "secret"})) is False
        assert _event_visible_to(MEMBER, self._ev({"collection_id": "public"})) is True

    def test_admin_receives_everything(self, monkeypatch):
        from src.events.routes import _event_visible_to

        _hide_collection(monkeypatch, "secret", [])
        assert _event_visible_to(ADMIN, self._ev({"collection_id": "secret"})) is True

    def test_meeting_event_needs_meeting_read(self, monkeypatch):
        from src.events.routes import _event_visible_to

        assert _event_visible_to(VISITOR, self._ev({"meeting_id": "m1"})) is False
        assert _event_visible_to(MEMBER, self._ev({"meeting_id": "m1"})) is True

    def test_session_event_requires_ownership(self, monkeypatch):
        """会话事件带 owner；非本人不投递（拿不到 owner 也保守不投）。"""
        from src.events.routes import _event_visible_to

        mine = self._ev({"session_id": "s1", "owner": "k-mem"})
        theirs = self._ev({"session_id": "s1", "owner": "someone-else"})
        unknown = self._ev({"session_id": "s1"})  # 无 owner（老事件形状）
        assert _event_visible_to(MEMBER, mine) is True
        assert _event_visible_to(MEMBER, theirs) is False
        assert _event_visible_to(MEMBER, unknown) is False, "拿不到归属必须保守不投"

    def test_unknown_scope_key_is_not_delivered(self, monkeypatch):
        """没有作用域键的事件不投给非管理员（宁可少投，不泄露）。"""
        from src.events.routes import _event_visible_to

        assert _event_visible_to(MEMBER, self._ev({})) is False
        assert _event_visible_to(ADMIN, self._ev({})) is True

    def test_hotwords_and_people_domain_gate(self, monkeypatch):
        from src.events.routes import _event_visible_to

        assert _event_visible_to(VISITOR, self._ev({"library_id": "l1"})) is False
        assert _event_visible_to(MEMBER, self._ev({"library_id": "l1"})) is True
        assert _event_visible_to(VISITOR, self._ev({"person_id": "p1"})) is False


# ── ⑪ Chat 会话归属 ───────────────────────────────────────────


class TestSessionOwnership:
    def test_list_filters_by_creator(self, tmp_path):
        from src.db.sessions import SessionStore

        store = SessionStore(str(tmp_path / "s.db"))
        a = store.create_session(title="A", created_by="alice")
        b = store.create_session(title="B", created_by="bob")

        alice = {s.id for s in store.list_sessions(created_by="alice")}
        assert a.id in alice
        assert b.id not in alice, "不能看到别人的会话"

        # 不过滤 = admin/owner 视角
        everyone = {s.id for s in store.list_sessions()}
        assert {a.id, b.id} <= everyone

    def test_legacy_blank_creator_is_normalized_to_local(self, tmp_path):
        """存量会话的 ``created_by=''`` 在**读取**时归一化为 local。

        注意：``create_session`` 会规范化空串（新会话永远有归属），所以存量场景必须
        直接写库模拟——这是老库迁移后的真实形状。
        """
        import sqlite3

        from src.db.sessions import SessionStore

        db_path = str(tmp_path / "s.db")
        store = SessionStore(db_path)
        legacy = store.create_session(title="old", created_by="alice")
        # 直接把 created_by 抹成空串，模拟老库迁移后的行
        conn = sqlite3.connect(db_path)
        conn.execute("UPDATE sessions SET created_by = '' WHERE id = ?", (legacy.id,))
        conn.commit()
        conn.close()

        local = {s.id for s in store.list_sessions(created_by="local")}
        assert legacy.id in local, "空串归本机"
        others = {s.id for s in store.list_sessions(created_by="bob")}
        assert legacy.id not in others, "空串不能对任何成员可见"

    def test_created_by_defaults_to_actor(self, tmp_path):
        """不显式传 created_by 时取请求期 actor（本机 = local）。"""
        from src.db.sessions import SessionStore

        store = SessionStore(str(tmp_path / "s.db"))
        s = store.create_session(title="x")
        assert s.created_by == "local"

    def test_require_session_access_404_for_others(self, monkeypatch):
        from fastapi import HTTPException

        from src.api.routes import sessions as sess_mod
        from src.identity.actor import _current, _set_actor

        monkeypatch.setattr(sess_mod, "_session_owner", lambda sid: "alice")
        token = _set_actor(MEMBER)  # id=k-mem ≠ alice
        try:
            with pytest.raises(HTTPException) as e:
                sess_mod.require_session_access("s1")
            assert e.value.status_code == 404, "别人的会话必须是 404（不是 403）"
        finally:
            _current.reset(token)

    def test_require_session_access_allows_owner_of_session(self, monkeypatch):
        from src.api.routes import sessions as sess_mod
        from src.identity.actor import _current, _set_actor

        monkeypatch.setattr(sess_mod, "_session_owner", lambda sid: "k-mem")
        token = _set_actor(MEMBER)
        try:
            sess_mod.require_session_access("s1")  # 不抛
        finally:
            _current.reset(token)

    def test_admin_can_access_any_session(self, monkeypatch):
        from src.api.routes import sessions as sess_mod
        from src.identity.actor import _current, _set_actor

        monkeypatch.setattr(sess_mod, "_session_owner", lambda sid: "alice")
        token = _set_actor(ADMIN)
        try:
            sess_mod.require_session_access("s1")  # 不抛：admin 看全部
        finally:
            _current.reset(token)


# ── ⑤ 每库权限探针（UI 化妆用，2026-09-17）────────────────────────
#
# 用户报的 bug：collection 设置面板是 autosave（无保存按钮），没有 config 档的人
# 一打开就 POST `/config` → 403 → 弹"保存失败"。修法是**入口就不画**，而前端要
# draw 不 draw 需要后端告诉它"这个库我能干什么"——就是这里的 `permissions`。
#
# 纪律与可见性一致：不可见的库四档全 False（不泄露"存在但无权"），admin/owner 全 True。


class TestCollectionPermissionsProbe:
    def test_owner_gets_every_tier(self, monkeypatch):
        from src.identity import visibility

        monkeypatch.setattr(acl_mod, "collection_allows", lambda cid, tier, role: False)
        assert visibility.collection_permissions(OWNER, "col_x") == {
            "read": True,
            "write": True,
            "delete": True,
            "config": True,
        }

    def test_admin_gets_every_tier(self, monkeypatch):
        from src.identity import visibility

        monkeypatch.setattr(acl_mod, "collection_allows", lambda cid, tier, role: False)
        assert all(visibility.collection_permissions(ADMIN, "col_x").values())

    def test_hidden_collection_is_all_false(self, monkeypatch):
        """不可见 → 四档全 False。**不能**泄露"存在但你没 config 档"。"""
        from src.identity import visibility

        _hide_collection(monkeypatch, hidden="col_secret", visible=[])
        perms = visibility.collection_permissions(MEMBER, "col_secret")
        assert perms == {"read": False, "write": False, "delete": False, "config": False}

    def test_visible_but_no_config_tier(self, monkeypatch):
        """能读但改不了配置 —— 正是用户报的那个场景（member 打开设置页被拒）。"""
        from src.identity import visibility

        monkeypatch.setattr(
            acl_mod,
            "collection_allows",
            lambda cid, tier, role: tier in ("read", "write"),
        )
        perms = visibility.collection_permissions(MEMBER, "col_x")
        assert perms == {"read": True, "write": True, "delete": False, "config": False}

    def test_tiers_are_independent_axes(self, monkeypatch):
        """允许"能删但不能写"这种组合（四档是独立轴，不是包含链）。"""
        from src.identity import visibility

        monkeypatch.setattr(
            acl_mod,
            "collection_allows",
            lambda cid, tier, role: tier in ("read", "delete"),
        )
        perms = visibility.collection_permissions(MEMBER, "col_x")
        assert perms["delete"] is True and perms["write"] is False


# ── ⑥ 会议权限探针（UI 化妆用，2026-09-17）──────────────────────
#
# 用户要求：「没有会议删除权限的话，会议的删除按钮也要看不到才对」。
# 前端要 draw 不 draw 需要后端告诉它"这个会议我能操作吗"——就是这里的
# `meeting_permissions` / `can_operate_meetings`。
#
# 纪律：operate 走**与执行路径同一次调用**（`can(actor, "meeting.delete", …)`），
# 不自己重算 ACL 名单——重算就有两个真相，一旦档位语义变了按钮和墙就会分叉。


class TestMeetingPermissionsProbe:
    def test_owner_can_read_and_write(self):
        from src.identity import visibility

        assert visibility.meeting_permissions(OWNER, "m1") == {"read": True, "write": True}

    def test_admin_can_read_and_write(self):
        from src.identity import visibility

        assert visibility.meeting_permissions(ADMIN, "m1") == {"read": True, "write": True}

    def test_legacy_meeting_without_creator_is_not_writable(self):
        """存量会议（无 created_by）→ member 不能写（例外不生效，保守）。

        注意：默认配置下 member 有 create 档，但**这个会议没有 creator 记录**，
        所以创建者所有权无从判定 → 只认 manage 档 → member 拿不到 write。
        """
        from src.identity import visibility

        assert visibility.meeting_permissions(MEMBER, "m1") == {
            "read": True,
            "write": False,
        }

    def test_visitor_neither(self):
        from src.identity import visibility

        assert visibility.meeting_permissions(VISITOR, "m1") == {
            "read": False,
            "write": False,
        }

    def test_write_tracks_the_live_acl(self, monkeypatch):
        """把 member 加进 manage 档 → 探针必须跟着变 True（同一次 can 调用）。"""
        from src.identity import visibility

        acl = acl_mod.default_domain_acl()
        acl[acl_mod.DOMAIN_MEETING]["manage"] = ["owner", "admin", "member"]
        monkeypatch.setattr(acl_mod, "load_domain_acl", lambda: acl)
        assert visibility.meeting_permissions(MEMBER, "m1")["write"] is True

    def test_missing_id_is_denied(self):
        from src.identity import visibility

        assert visibility.meeting_permissions(MEMBER, None) == {
            "read": False,
            "write": False,
        }
        assert visibility.meeting_permissions(MEMBER, "") == {
            "read": False,
            "write": False,
        }

    def test_group_manage_is_the_role_wide_tier(self):
        """分组是跨会议容器 → 用"角色整体有没有 manage 档"这个保守口径。

        **创建者例外在这里刻意不生效**：组里可能混着别人的会议，删整组会牵连它们。
        """
        from src.identity import visibility

        assert visibility.can_manage_meetings(OWNER) is True
        assert visibility.can_manage_meetings(ADMIN) is True
        assert visibility.can_manage_meetings(MEMBER) is False
        assert visibility.can_manage_meetings(VISITOR) is False

    def test_group_manage_follows_acl_change(self, monkeypatch):
        from src.identity import visibility

        acl = acl_mod.default_domain_acl()
        acl[acl_mod.DOMAIN_MEETING]["manage"] = ["owner", "admin", "member"]
        monkeypatch.setattr(acl_mod, "load_domain_acl", lambda: acl)
        assert visibility.can_manage_meetings(MEMBER) is True


class TestMeetingListAttachesPermissions:
    """`GET /meetings` 每项都要带 permissions —— 前端拿不到就只能硬编码，必漏。"""

    def _meeting(self, mid: str):
        from src.meeting.models import Meeting

        return Meeting(id=mid, title="t")

    def test_list_attaches_write_flag(self, monkeypatch):
        import asyncio

        from src.meeting import routes as mr

        monkeypatch.setattr(mr.store, "list_meetings", lambda: [self._meeting("m1")])
        from src.identity.actor import _current, _set_actor

        token = _set_actor(MEMBER)
        try:
            rows = asyncio.run(mr.list_meetings(lean=False, search="", status=None, include_archived=False, limit=100))
        finally:
            _current.reset(token)
        # 无 creator 的会议（存量数据）→ member 不能写
        assert rows[0]["permissions"] == {"read": True, "write": False}

    def test_owner_list_is_writable(self, monkeypatch):
        import asyncio

        from src.meeting import routes as mr
        from src.identity.actor import _current, _set_actor

        monkeypatch.setattr(mr.store, "list_meetings", lambda: [self._meeting("m1")])
        token = _set_actor(OWNER)
        try:
            rows = asyncio.run(mr.list_meetings(lean=False, search="", status=None, include_archived=False, limit=100))
        finally:
            _current.reset(token)
        assert rows[0]["permissions"] == {"read": True, "write": True}

    def test_probe_failure_is_fail_closed(self, monkeypatch):
        """探针炸了 → 按"不能操作"，而不是让整个列表 500。"""
        from src.meeting import routes as mr
        from src.identity import visibility

        rows = [{"id": "m1"}]
        monkeypatch.setattr(
            visibility,
            "meeting_permissions",
            lambda a, mid: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        from src.identity.actor import _current, _set_actor

        token = _set_actor(MEMBER)
        try:
            mr._attach_meeting_permissions(rows)
        finally:
            _current.reset(token)
        assert rows[0]["permissions"] == {"read": False, "write": False}


# ── ⑦ 检索层最底线的可见性漏斗（2026-09-17 泄露修复）─────────────────
#
# **用户报的 bug**：member 的 Chat 搜出了它没权限的 collection。
#
# 根因有两层：
# ① `run_in_executor` 不复制 contextvars → 线程里身份退回 owner →
#    `visible_collection_ids` 返回 None（= 全部）→ 上层过滤静默失效（见
#    tests/test_async_ctx_propagation.py）；
# ② `DirectQuery.retrieve` **自己不设防**——它信任调用方给的 collections。
#
# ①按调用点修了；②在这里补**最底线的漏斗**：这是真正读向量的那一层，只要它
# 自己再收一次口，任何未来新增的调用方（或又一次身份丢失）都不会直接泄露。


class TestDirectQueryVisibilityFunnel:
    """**必须真的能发现泄露**：用"检索层有没有去碰隐藏库"当判据。

    判据选 `_retrieve_one` 实际处理的库（= 真正读了向量配置的那一步），而不是
    "有没有抛异常"——后者在漏斗被摘掉时**照样通过**（我第一版就犯了这个错：
    测试全绿但摘掉漏斗也不红，等于没测）。
    """

    def _make(self, monkeypatch, touched: list, *, allows):
        from src.identity import acl as _acl
        from src.identity import visibility as _vis
        from src.rag.direct_query import DirectQueryModule

        monkeypatch.setattr(_vis, "_all_collection_ids", lambda: ["col_ok", "col_hidden"])
        monkeypatch.setattr(_acl, "collection_allows", allows)

        class _Db:
            @staticmethod
            def get_collection_config(cid):
                # 这一步 = 检索真正开始处理该库
                touched.append(cid)
                return {"chunk_mode": "normal"}

        return DirectQueryModule(retriever=None, db=_Db())

    def test_hidden_collection_never_reaches_retrieval(self, monkeypatch):
        """**核心回归**：调用方误传了隐藏库 → 它绝不能进到检索步。"""
        from src.identity.actor import _current, _set_actor

        touched: list[str] = []
        m = self._make(
            monkeypatch, touched,
            allows=lambda cid, tier, role: cid == "col_ok",
        )

        token = _set_actor(MEMBER)
        try:
            from src.rag.direct_query import DirectQueryModule
            DirectQueryModule.retrieve(m, "q", ["col_ok", "col_hidden"], top_k=3)
        finally:
            _current.reset(token)

        assert "col_hidden" not in touched, (
            "隐藏库被检索了 —— 可见性漏斗失效（这正是用户报的泄露）"
        )
        assert "col_ok" in touched, "可见库应当照常被检索"

    def test_all_hidden_never_enters_retrieval(self, monkeypatch):
        from src.identity.actor import _current, _set_actor

        touched: list[str] = []
        m = self._make(monkeypatch, touched, allows=lambda cid, tier, role: False)

        token = _set_actor(MEMBER)
        try:
            from src.rag.direct_query import DirectQueryModule
            res = DirectQueryModule.retrieve(m, "q", ["col_hidden"], top_k=3)
        finally:
            _current.reset(token)

        assert touched == [], "全不可见时不该碰任何库"
        assert res.chunks == []

    def test_owner_touches_everything(self, monkeypatch):
        """owner 不受影响（vis=None 时不收窄，别误伤）。"""
        from src.identity import acl as _acl
        from src.identity.actor import _current, _set_actor

        monkeypatch.setattr(_acl, "collection_allows", lambda cid, tier, role: False)

        touched: list[str] = []
        m = self._make(monkeypatch, touched, allows=lambda cid, tier, role: False)

        token = _set_actor(OWNER)
        try:
            from src.rag.direct_query import DirectQueryModule
            DirectQueryModule.retrieve(m, "q", ["a", "b"], top_k=1)
        finally:
            _current.reset(token)

        assert set(touched) == {"a", "b"}, "owner 应当两个库都走到检索"
