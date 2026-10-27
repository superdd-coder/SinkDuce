"""隐藏库在 Chat 里的第三轮泄露（2026-09-17 用户第三次报）。

用户原话："无法读取的collection仍然还是可以在Chat里面找出来，还是有问题；"

前两轮修的是**检索**（Retriever / DirectQuery / catalog / 嵌套线程池身份）。
这轮实测（容器内以 member 身份真跑）发现泄露**不在检索**，而在**枚举出口**——
三个各自独立、都能被 Chat 触达：

1. **`build_collection_directory()` 完全没过滤**：解析失败时整份目录作为错误载荷
   回传给模型（"pick from the directory below"），模型因此拿到**全部库的 id + 显示名**，
   甚至能 resolve 到隐藏库。这是最直接的出口。
2. **`get_query_history` / `/api/history` 在"取不到 ACL"时 fail-open**：
   历史条目含 question/answer/sources 全文。条目里的 collection 常是**显示名**
   （如 "Project A"，带空格）或**已删除库的 id**；`get_collection_meta` 对这两种都会
   抛 `ValueError`，而 `collection_acl` 在 except 里回落**默认名单**（member 可读）
   → 隐藏库/已删库的历史全文对人可见。
3. **截断顺序**：过滤必须发生在 `[-limit:]` **之前**。反过来不会直接泄露条目
   （过滤仍会执行），但隐藏条目会**占掉额度**，返回条数 < limit 就是"最近 N 条里
   有你看不到的东西"的可观测信号，同时让可见条目无辜少给。

跑法：``pytest tests/test_hidden_visibility_leaks.py``
"""

from __future__ import annotations

import json

import pytest

from src.identity import Actor
from src.identity.actor import _current, _set_actor
from src.identity import acl as acl_mod

MEMBER = Actor(id="k-mem", kind="key", role_id="member")
OWNER = Actor()
ADMIN = Actor(id="k-adm", kind="key", role_id="admin")

HIDDEN = "col_hidden"
VISIBLE = "col_visible"


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

    def _run(actor: Actor):
        tokens.append(_set_actor(actor))

    yield _run
    for t in reversed(tokens):
        _current.reset(t)


def _hide(monkeypatch, hidden=HIDDEN):
    """hidden 对 member/visitor 不可见；其余可见。owner/admin 恒可见。"""

    def _allows(cid, tier, role):
        if role in ("owner", "admin"):
            return True
        return cid != hidden

    monkeypatch.setattr(acl_mod, "collection_allows", _allows)


# ── ① Chat 的库目录（最直接的枚举出口）────────────────────────


class TestCollectionDirectory:
    def _patch(self, monkeypatch, *, visible):
        from src.identity import visibility as _vis
        from src.services import services

        monkeypatch.setattr(
            _vis, "visible_collection_ids",
            lambda actor: None if actor.role_id in ("owner", "admin") else set(visible),
        )
        # 测试环境没有真 db（services.db is None）→ 塞一个最小假 db
        monkeypatch.setattr(
            services, "db",
            type("_Db", (), {"list_collections": staticmethod(lambda: [HIDDEN, VISIBLE])})(),
        )

    def test_directory_drops_hidden_collection(self, as_actor, monkeypatch):
        """**核心回归**：目录交给模型，隐藏库绝不能出现。"""
        from src.chatbox import collection_resolve as cr
        self._patch(monkeypatch, visible={VISIBLE})

        as_actor(MEMBER)
        ids = {c["id"] for c in cr.build_collection_directory()}
        assert HIDDEN not in ids, "隐藏库出现在交给模型的目录里 —— 这就是用户报的泄露"
        assert VISIBLE in ids, "可见库必须保留（别过度收紧）"

    def test_resolve_cannot_reach_hidden_collection(self, as_actor, monkeypatch):
        """点名隐藏库（id 或显示名）都不能解析成功。"""
        from src.chatbox import collection_resolve as cr
        self._patch(monkeypatch, visible={VISIBLE})

        as_actor(MEMBER)
        res = cr.resolve_collection(HIDDEN)
        assert "error" in res, "点名隐藏库竟然解析成功 —— 绕过枚举的通道没堵上"
        # 错误载荷里的**目录**不能带上隐藏库。（error 文案会原样回显用户问的字符串，
        # 那不是泄露面——泄露面是 collections 列表。）
        assert not res.get("collection"), "解析结果指向了某个库"
        dir_ids = {c.get("id") for c in (res.get("collections") or [])}
        assert HIDDEN not in dir_ids, "错误载荷把隐藏库目录带给了模型"

    def test_directory_fail_closed_on_gate_error(self, as_actor, monkeypatch):
        """可见性判定炸了 → 空目录（绝不回落"不过滤"）。

        也把 db 打桩好：这样一旦闸门失效，本测试是因为拿到**非空目录**而红，
        而不是因为 db 是 None 抛异常——失败理由必须指向泄露本身。
        """
        from src.chatbox import collection_resolve as cr
        from src.identity import visibility as _vis
        from src.services import services

        def _boom(actor):
            raise RuntimeError("gate down")

        monkeypatch.setattr(_vis, "visible_collection_ids", _boom)
        monkeypatch.setattr(
            services, "db",
            type("_Db", (), {"list_collections": staticmethod(lambda: [HIDDEN, VISIBLE])})(),
        )
        as_actor(MEMBER)
        assert cr.build_collection_directory() == [], (
            "闸门失效时回落成了整份目录（fail-open）"
        )

    def test_owner_gets_full_directory(self, as_actor, monkeypatch):
        from src.chatbox import collection_resolve as cr
        self._patch(monkeypatch, visible={VISIBLE})

        as_actor(OWNER)
        ids = {c["id"] for c in cr.build_collection_directory()}
        assert {HIDDEN, VISIBLE} <= ids, "owner 应当看到全部"


# ── ② "取不到 ACL" 必须 fail-closed，不能回落默认名单 ───────────


class TestAclFailClosed:
    def test_unreadable_acl_denies_instead_of_defaulting(self, monkeypatch):
        """**核心回归**：meta 读不出来（非法 id / 已删库）→ 空名单。

        旧代码在 except 里回落 `default_collection_acl()`（member 可读），于是
        "库名拼错/库已删除"反而被判成**可见**——历史记录恰恰按显示名存 collection。
        """
        from src.collections import store as cstore
        from src.identity import acl as _acl

        def _raise(cid):
            raise ValueError("invalid collection_id")

        monkeypatch.setattr(cstore, "get_collection_meta", _raise)
        acl = _acl.collection_acl("Project A")  # 带空格的显示名
        assert acl["read"] == [], "读不到 ACL 却回落了默认名单 —— fail-open 泄露"
        assert not _acl.collection_allows("Project A", "read", "member")

    def test_legacy_meta_without_acl_key_keeps_default(self, monkeypatch):
        """存量库（meta 存在但没有 ``acl`` 键）仍走产品默认 —— 别误伤。"""
        from src.collections import store as cstore
        from src.identity import acl as _acl

        monkeypatch.setattr(cstore, "get_collection_meta", lambda cid: {"id": cid, "name": "legacy"})
        assert _acl.collection_allows("legacy_col", "read", "member"), "存量库不该被收紧"
        assert _acl.collection_allows("legacy_col", "read", "visitor")


class TestCanonicalCollectionId:
    """ACL 以 **meta.id** 为键，而可见集枚举走 **Qdrant 库名**——两者可能不同

    （本机实测 40 个 meta 里 34 个 ``id != qdrant_name``）。若拿 Qdrant 名直接查 ACL，
    会查不到 meta 而回落默认名单 = 把**配过权限的**库悄悄放行。所以先收敛到规范 id。
    """

    def test_qdrant_name_resolves_to_meta_id(self, monkeypatch):
        from src.collections import store as cstore
        from src.identity import acl as _acl

        monkeypatch.setattr(cstore, "get_collection_meta", lambda cid: None)
        monkeypatch.setattr(
            cstore, "find_collection_by_qdrant_name",
            lambda name: {"id": "col_real"} if name == "Friendly Qdrant Name" else None,
        )
        assert _acl.canonical_collection_id("Friendly Qdrant Name") == "col_real"

    def test_meta_id_passes_through_unchanged(self, monkeypatch):
        from src.collections import store as cstore
        from src.identity import acl as _acl

        monkeypatch.setattr(cstore, "get_collection_meta", lambda cid: {"id": cid})
        assert _acl.canonical_collection_id("col_direct") == "col_direct"

    def test_unknown_id_returned_as_is(self, monkeypatch):
        from src.collections import store as cstore
        from src.identity import acl as _acl

        monkeypatch.setattr(cstore, "get_collection_meta", lambda cid: None)
        monkeypatch.setattr(cstore, "find_collection_by_qdrant_name", lambda name: None)
        assert _acl.canonical_collection_id("col_ghost") == "col_ghost"

    def test_acl_lookup_uses_canonical_id(self, monkeypatch):
        """**核心回归**：以 Qdrant 名调用时，读到的是该库真正的 ACL（不是默认名单）。"""
        from src.collections import store as cstore
        from src.identity import acl as _acl

        metas = {
            "col_hidden": {"id": "col_hidden", "acl": {"read": [], "write": [], "delete": [], "config": []}},
        }
        monkeypatch.setattr(cstore, "get_collection_meta", lambda cid: metas.get(cid))
        monkeypatch.setattr(
            cstore, "find_collection_by_qdrant_name",
            lambda name: {"id": "col_hidden"} if name == "Hidden Qdrant Name" else None,
        )
        assert not _acl.collection_allows("Hidden Qdrant Name", "read", "member"), (
            "按 Qdrant 名查 ACL 时回落了默认名单 —— 配过权限的库被悄悄放行"
        )


# ── ③ 历史记录：全文泄露面 ───────────────────────────────────


class TestHistoryVisibility:
    def _patch(self, monkeypatch, visible_ids):
        from src.identity import visibility as _vis

        monkeypatch.setattr(_vis, "visible_collection_ids", lambda actor: visible_ids)

    def test_entry_by_id_hidden_is_dropped(self, monkeypatch):
        from src.identity import visibility as _vis

        self._patch(monkeypatch, {VISIBLE})
        assert not _vis.history_entry_visible(MEMBER, {"collection": HIDDEN})

    def test_entry_by_id_visible_is_kept(self, monkeypatch):
        from src.identity import visibility as _vis

        self._patch(monkeypatch, {VISIBLE})
        assert _vis.history_entry_visible(MEMBER, {"collection": VISIBLE})

    def test_entry_by_display_name_resolves(self, monkeypatch):
        """历史里的 collection 常是**显示名** —— 解析回 id 后按可见集判定。"""
        from src.collections import store as cstore
        from src.identity import visibility as _vis

        self._patch(monkeypatch, {VISIBLE})
        monkeypatch.setattr(
            cstore, "find_collection_by_name",
            lambda name: {"id": VISIBLE} if name == "Visible Name" else None,
        )
        assert _vis.history_entry_visible(MEMBER, {"collection": "Visible Name"})
        assert _vis.history_entry_visible(MEMBER, {"collection": "Visible Name"})

    def test_entry_by_display_name_of_hidden_is_dropped(self, monkeypatch):
        """**核心回归**：显示名解析到隐藏库 → 拒绝（旧代码这条会漏）。"""
        from src.collections import store as cstore
        from src.identity import visibility as _vis

        self._patch(monkeypatch, {VISIBLE})
        monkeypatch.setattr(
            cstore, "find_collection_by_name",
            lambda name: {"id": HIDDEN},
        )
        assert not _vis.history_entry_visible(MEMBER, {"collection": "Hidden Name"})

    def test_entry_without_collection_is_hidden_from_non_admin(self, monkeypatch):
        from src.identity import visibility as _vis

        self._patch(monkeypatch, {VISIBLE})
        assert not _vis.history_entry_visible(MEMBER, {})
        assert not _vis.history_entry_visible(MEMBER, {"collection": "   "})

    def test_unresolvable_collection_is_dropped(self, monkeypatch):
        """解析不出来的归属（已删除库 + 拼错名）→ 拒绝，不是放行。"""
        from src.collections import store as cstore
        from src.identity import visibility as _vis

        self._patch(monkeypatch, {VISIBLE})

        def _raise(name):
            raise ValueError("invalid")

        monkeypatch.setattr(cstore, "find_collection_by_name", _raise)
        assert not _vis.history_entry_visible(MEMBER, {"collection": "Project A"})

    def test_admin_sees_everything(self, monkeypatch):
        from src.identity import visibility as _vis

        self._patch(monkeypatch, {VISIBLE})
        assert _vis.history_entry_visible(ADMIN, {"collection": HIDDEN})
        assert _vis.history_entry_visible(ADMIN, {})

    def test_gate_failure_denies_entry(self, monkeypatch):
        """可见集判定炸了 → 拒绝该条（fail-closed）。"""
        from src.identity import visibility as _vis

        def _boom(actor):
            raise RuntimeError("down")

        monkeypatch.setattr(_vis, "visible_collection_ids", _boom)
        assert not _vis.history_entry_visible(MEMBER, {"collection": VISIBLE})


class TestHistoryEndpointTruncationOrder:
    """**过滤必须在 ``[-limit:]`` 之前**：否则隐藏条目占掉额度，调用方拿到
    "条数 < limit"（= 可观测的"有你看不到的东西"），可见条目也无辜少给。"""

    def _run_endpoint(self, monkeypatch, tmp_path, entries, limit):
        import src.api.routes.query as q
        from src.identity import visibility as _vis

        hist = tmp_path / "history"
        hist.mkdir()
        (hist / "history.jsonl").write_text(
            "\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8"
        )
        monkeypatch.setattr(q, "HISTORY_DIR", hist)
        monkeypatch.setattr(
            _vis, "visible_collection_ids",
            lambda actor: None if actor.role_id in ("owner", "admin") else {VISIBLE},
        )
        token = _set_actor(MEMBER)
        try:
            return q.get_history(limit=limit)
        finally:
            _current.reset(token)

    def test_hidden_entry_not_smuggled_in_by_limit(self, monkeypatch, tmp_path):
        # 隐藏条目排在**最后**（= 最近），limit 又很小。
        entries = [{"collection": VISIBLE, "question": f"q{i}"} for i in range(5)]
        entries.append({"collection": HIDDEN, "question": "SECRET"})
        out = self._run_endpoint(monkeypatch, tmp_path, entries, limit=2)
        assert all(e.get("collection") != HIDDEN for e in out), "隐藏库条目漏了出来"

    def test_visible_entries_are_not_shortchanged(self, monkeypatch, tmp_path):
        """过滤前置：limit=3 就该给满 3 条可见条目，不被隐藏条目的额度挤掉。"""
        entries = [{"collection": VISIBLE, "question": f"q{i}"} for i in range(5)]
        entries.append({"collection": HIDDEN, "question": "SECRET"})
        out = self._run_endpoint(monkeypatch, tmp_path, entries, limit=3)
        assert len(out) == 3, "隐藏条目占掉了额度（过滤应在截断之前）"
        assert all(e.get("collection") == VISIBLE for e in out)


class TestQueryHistoryTool:
    def _write_history(self, tmp_path, entries):
        hist = tmp_path / "history"
        hist.mkdir(exist_ok=True)
        (hist / "history.jsonl").write_text(
            "\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8"
        )
        return hist

    def test_tool_filters_hidden_entries(self, monkeypatch, tmp_path):
        import asyncio

        import src.mcp.tools.search as s
        from src.identity import visibility as _vis

        self._write_history(
            tmp_path,
            [
                {"collection": HIDDEN, "question": "SECRET Q", "answer": "SECRET A"},
                {"collection": VISIBLE, "question": "ok"},
            ],
        )
        monkeypatch.setattr("src.config.DATA_DIR", tmp_path)
        monkeypatch.setattr(
            _vis, "visible_collection_ids",
            lambda actor: None if actor.role_id in ("owner", "admin") else {VISIBLE},
        )
        token = _set_actor(MEMBER)
        try:
            out = asyncio.run(s.get_query_history(limit=10, include_details=True))
        finally:
            _current.reset(token)
        assert "SECRET" not in out, "工具返回了隐藏库的历史全文"
        assert "ok" in out

    def test_tool_gate_failure_returns_empty(self, monkeypatch, tmp_path):
        import asyncio

        import src.mcp.tools.search as s
        from src.identity import visibility as _vis

        self._write_history(tmp_path, [{"collection": VISIBLE, "question": "ok"}])
        monkeypatch.setattr("src.config.DATA_DIR", tmp_path)

        def _boom(actor):
            raise RuntimeError("down")

        monkeypatch.setattr(_vis, "visible_collection_ids", _boom)
        token = _set_actor(MEMBER)
        try:
            out = asyncio.run(s.get_query_history(limit=10))
        finally:
            _current.reset(token)
        assert json.loads(out) == [], "判定失败必须空表（fail-closed）"
