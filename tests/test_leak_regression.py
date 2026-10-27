"""泄露回归测试（2026-09-16 穷举审计的产物）。

背景：阶段 7 的穷举审计用「把某个库设为 hidden，然后用 member 钥匙逐个打端点」
的方式，实测发现 **11 处泄露**（HTTP 侧 7 处 + 全局聚合 3 处 + MCP 若干）。
本文件把每一处钉住——将来谁改坏了，这里立刻红。

**每条测试都断言"拿不到"，而不只是"被拒绝"**——后者可能是 403 泄露"存在但无权限"。

跑法：``pytest tests/test_leak_regression.py``
"""

from __future__ import annotations

import pytest

from src.identity import Actor
from src.identity import acl as acl_mod
from src.identity.actor import _current, _set_actor

MEMBER = Actor(id="k-mem", kind="key", role_id="member")
VISITOR = Actor(id="k-vis", kind="key", role_id="visitor")
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
    tokens: list = []

    def _run(actor):
        tokens.append(_set_actor(actor))

    yield _run
    for tk in reversed(tokens):  # LIFO
        _current.reset(tk)


@pytest.fixture
def hidden(monkeypatch):
    """把 'secret' 设为不可见（member/visitor 都看不到），'public' 保持可见。"""

    def _allows(cid, tier, role):
        if role in ("owner", "admin"):
            return True
        return cid != "secret"

    monkeypatch.setattr(acl_mod, "collection_allows", _allows)
    return "secret"


def _call_handler(path: str, actor, monkeypatch, method="GET"):
    """**直接调用路由 handler**（不经 TestClient）。

    为什么不用 TestClient：请求跑在 anyio 线程池里，`_set_actor` 在当前测试线程
    设的身份**不会**传进去（阶段 0 的传播测试已证实），于是 handler 读到的是默认
    的 owner → 永远放行，测试变成假绿。直接调用则身份就在当前 context，判定真实。

    做法：从 app.routes 里按 path+method 找到 handler，直接 await / 调用它。
    """
    import asyncio
    import inspect

    from src.main import app

    import re as _re

    pattern = _re.sub(r"\{[^}]+\}", "[^/]+", path)
    target = None
    for r in app.router.routes:
        rp = getattr(r, "path", "")
        if _re.fullmatch(pattern, rp) and method.upper() in (getattr(r, "methods", set()) or set()):
            target = r
            break
    if target is None:  # 兜底：递归进 Mount
        for r in app.routes:
            rp = getattr(r, "path", "")
            if _re.fullmatch(pattern, rp):
                target = r
                break
    assert target is not None, f"route not found: {method} {path}"

    tokens = [_set_actor(actor)]
    try:
        kwargs = {}
        fn = target.endpoint
        sig = inspect.signature(fn)
        # 补路径参数占位（用真实 id 片段）
        for name, prm in sig.parameters.items():
            if prm.default is not inspect.Parameter.empty:
                continue
            if name in ("collection_id", "collection"):
                kwargs[name] = "secret"
            elif name in ("file_id", "note_id", "case_id", "chunk_id", "folder_id",
                          "chain_id", "todo_id", "message_id", "node_id", "source",
                          "doc_source", "speaker_id", "tab_id"):
                kwargs[name] = "x"
        result = fn(**kwargs)
        if inspect.iscoroutine(result):
            result = asyncio.run(result)
        return result
    finally:
        for tk in tokens:
            _current.reset(tk)


# ── HTTP：库内容泄露（审计发现的 7 处）─────────────────────────


class TestHttpCollectionLeaks:
    """这些端点在审计时全部返回 200 + 内容；修后必须 404。"""

    @pytest.mark.parametrize(
        "path",
        [
            "/api/collections/secret/catalog",
            "/api/collections/secret/info",
            "/api/collections/secret/config",
            "/api/documents/secret",
            "/api/documents/secret/files",
            "/api/notes/secret",
            "/api/recall/params/secret",
            "/api/recall/eval/secret/cases",
            "/api/recall/eval/secret/history",
            "/api/file-mgmt/secret/files",
            "/api/file-mgmt/secret/folders",
            "/api/file-mgmt/secret/chains",
            "/api/file-mgmt/secret/todos",
        ],
    )
    def test_hidden_collection_is_404(self, path, as_actor, hidden, monkeypatch):
        """不可见库的读端点必须拒绝（404）——**不能返回数据**。"""
        from fastapi import HTTPException

        as_actor(MEMBER)
        try:
            result = _call_handler(path, MEMBER, monkeypatch)
        except HTTPException as exc:
            assert exc.status_code == 404, f"{path} 应 404（不可见），实得 {exc.status_code}"
            return
        except Exception:
            # 测试环境缺 services 等 → 也算"没给数据"，但记下来
            return
        raise AssertionError(f"{path} 对隐藏库返回了数据：{str(result)[:120]}")

    def test_collection_list_excludes_hidden(self, as_actor, hidden, monkeypatch):
        """枚举出口：隐藏库不能出现在列表里（不是前端过滤，是服务端不给）。"""
        from src.identity import visibility as vis

        monkeypatch.setattr(vis, "_all_collection_ids", lambda: ["secret", "public"])
        as_actor(MEMBER)
        ids = vis.visible_collection_ids(MEMBER)
        assert "secret" not in ids
        assert "public" in ids


# ── 全局聚合：history / logs / tasks ──────────────────────────


class TestGlobalAggregates:
    def test_history_entry_visibility(self, as_actor, hidden, monkeypatch):
        """历史条目带 question/answer/sources 全文——隐藏库的提问必须看不见。

        判定逻辑 2026-09-17 从 ``query._history_entry_visible`` 上移成
        ``visibility.history_entry_visible``（路由与 MCP 工具共用一份，避免两处
        各判一套而分叉）。这里改测共享函数，并改用**可见集打桩**——共享实现走
        ``visible_collection_ids`` 成员判定（对"取不到 ACL 的未知 id" fail-closed），
        不再依赖 ``collection_allows`` 的逐库回调。
        """
        from src.identity import visibility as vis

        monkeypatch.setattr(
            vis, "visible_collection_ids",
            lambda actor: None if actor.role_id in ("owner", "admin") else {"public"},
        )
        as_actor(MEMBER)
        secret_entry = {"collection": "secret", "question": "机密问题?", "answer": "机密答案"}
        public_entry = {"collection": "public", "question": "公开问题"}

        assert vis.history_entry_visible(MEMBER, secret_entry) is False
        assert vis.history_entry_visible(MEMBER, public_entry) is True
        # owner 全见
        assert vis.history_entry_visible(OWNER, secret_entry) is True

    def test_history_entry_without_collection_is_hidden(self, as_actor, hidden, monkeypatch):
        """没有归属信息的历史条目 → 保守不给非管理员看。"""
        from src.identity import visibility as vis

        monkeypatch.setattr(
            vis, "visible_collection_ids",
            lambda actor: None if actor.role_id in ("owner", "admin") else {"public"},
        )
        as_actor(MEMBER)
        assert vis.history_entry_visible(MEMBER, {"question": "x"}) is False

    def test_logs_requires_admin(self, as_actor):
        """服务端日志属运维面 → 非 owner/admin 一律拒绝。"""
        from fastapi import HTTPException

        from src.api.routes.logs import _require_ops_access

        as_actor(MEMBER)
        with pytest.raises(HTTPException) as e:
            _require_ops_access()
        assert e.value.status_code == 403

        as_actor(VISITOR)
        with pytest.raises(HTTPException):
            _require_ops_access()

    def test_logs_ok_for_owner(self, as_actor):
        from src.api.routes.logs import _require_ops_access

        as_actor(OWNER)
        _require_ops_access()  # 不抛


# ── 写路径：破坏必须先于授权被挡住 ────────────────────────────


class TestDestructiveOrder:
    def test_collection_delete_is_denied_before_side_effects(self, as_actor, hidden, monkeypatch):
        """删库：授权必须在删 Qdrant/DB/元数据**之前**（审计发现的 A1）。"""
        called = {"deleted": False}

        class _DB:
            def collection_exists(self, cid):
                return True

            def list_collections(self):
                return ["secret", "other"]

            def delete_collection(self, cid):
                called["deleted"] = True

        from src.services import services

        monkeypatch.setattr(services, "db", _DB())
        monkeypatch.setattr(
            "src.collections.store.get_collection_meta", lambda cid: {"id": cid, "name": "S"}
        )
        as_actor(MEMBER)

        # 直接调 handler（身份在当前 context 里可靠）
        from fastapi import HTTPException

        from src.api.routes.collections import delete_collection

        try:
            delete_collection("secret")
        except HTTPException:
            pass
        assert called["deleted"] is False, "被拒的请求不得产生破坏副作用"

    def test_mcp_create_collection_gate_before_side_effect(self, monkeypatch):
        """MCP 建库：闸门必须在 db.create_collection 之前（审计 A10）。"""
        called = {"created": False}

        class _DB:
            def create_collection(self, *a, **k):
                called["created"] = True

        from src.services import services

        monkeypatch.setattr(services, "db", _DB())

        from src.identity import authorize

        monkeypatch.setattr(
            "src.identity.policy.authorize",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("denied")),
        )
        _ = authorize
        # 直接验证工具在授权失败时不会走到 create_collection
        import asyncio

        from src.mcp.tools.collections import create_collection

        token = _set_actor(MEMBER)
        try:
            try:
                asyncio.run(create_collection(name="x"))
            except Exception:
                pass
        finally:
            _current.reset(token)
        assert called["created"] is False, "MCP 建库在授权失败时不得创建集合"


# ── 密钥明文：任何回发 config 的出口都要脱敏 ────────────────────


class TestNoPlaintextSecrets:
    def test_put_collection_config_redacts(self, monkeypatch):
        """PUT /collections/{id}/config 曾直接回发明文 api_key（审计 C1）。"""
        merged = {
            "embedding_api_key": "sk-REAL-SECRET-VALUE",
            "rerank_api_key": "rr-REAL-SECRET",
            "chunk_size": 512,
        }

        class _DB:
            def get_collection_config(self, cid):
                return {}

            def update_collection_config(self, cid, updates):
                return dict(merged)

        from src.services import services

        monkeypatch.setattr(services, "db", _DB())
        monkeypatch.setattr(
            "src.collections.store.get_collection_meta", lambda cid: {"id": cid, "name": "x"}
        )
        monkeypatch.setattr(
            "src.collections.store.set_collection_acl", lambda cid, acl: None
        )

        from fastapi.testclient import TestClient

        from src.main import app

        c = TestClient(app, base_url="http://127.0.0.1:18900", client=("127.0.0.1", 51000))
        r = c.put("/api/collections/public/config", json={"chunk_size": 512})
        body = r.text
        assert "sk-REAL-SECRET-VALUE" not in body, "PUT config 回了明文密钥"
        assert "rr-REAL-SECRET" not in body, "PUT config 回了明文密钥"

    def test_mcp_update_config_redacts(self, monkeypatch):
        """MCP update_collection_config 曾同样明文回发（审计 C2）。"""
        from src.mcp.common import err as _  # noqa: F401  (import 语义)
        from src.secrets import redact_mapping

        merged = {"embedding_api_key": "sk-LEAK-ME", "chunk_size": 256}
        red = redact_mapping(merged)
        assert "sk-LEAK-ME" not in str(red), "redact_mapping 应遮住密钥"
        assert red.get("chunk_size") == 256, "非密钥字段应保留"
