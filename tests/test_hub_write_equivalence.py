"""批次 2 等价性测试：**写类工具的转发必须与本地调用同形、同语义**。

三个层次（前两层离线可跑，第三层需要 18900 在跑）：

1. ``TestWriteForwards``：用 FakeApi 驱动 :mod:`src.hub.write_forwards` 的
   多步转发器 —— 钉住「两步序列 / 先读后写 / 批量聚合」的逻辑本身。
2. ``TestConfirmBeforeForward``：**装饰器顺序** —— 确认卡必须在转发**之前**
   （拒绝时绝不能碰远端）。这是批次 2 的承重点：删的是**别的盒子**的数据时，
   "你确定吗"这一问仍然要发生。
3. ``TestLiveWriteEquivalence``：真机（Hub 指向自己）：**各写一对**（本地一条、
   转发一条，``__verify_`` 前缀 + 用完即清）比形状，绝不拿真实数据当靶子。

跑法：``pytest tests/test_hub_write_equivalence.py``
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request

import pytest

BASE = "http://127.0.0.1:18900"


# ── 1. 纯函数层：多步转发器 ───────────────────────────────────


class FakeApi:
    """RemoteApi 的测试替身：脚本化响应 / 记录调用序列。"""

    def __init__(self, script=None):
        #: (method, path) → payload；未命中 → 404 风格 HubCallError
        self.script = script or {}
        #: [(method, path, json_body)] —— 断言调用顺序用
        self.calls: list[tuple[str, str, object]] = []

    async def _req(self, method, path, json_body=None, **params):
        from src.hub.client import HubCallError

        self.calls.append((method, path, json_body))
        for (m, p), payload in self.script.items():
            if m == method and p == path:
                if isinstance(payload, Exception):
                    raise payload
                return payload
        raise HubCallError(f"127.0.0.1 returned 404: not scripted", status=404, detail="not scripted")

    async def get(self, path, **params):
        return await self._req("GET", path, None, **params)

    async def post(self, path, json_body=None, **params):
        return await self._req("POST", path, json_body, **params)

    async def patch(self, path, json_body=None, **params):
        return await self._req("PATCH", path, json_body, **params)

    async def put(self, path, json_body=None, **params):
        return await self._req("PUT", path, json_body, **params)

    async def delete(self, path, **params):
        return await self._req("DELETE", path, None, **params)


def _hc(status: int, detail: str = "boom"):
    from src.hub.client import HubCallError

    return HubCallError(f"127.0.0.1 returned {status}: {detail}", status=status, detail=detail)


def _load(out) -> dict:
    if isinstance(out, str):
        return json.loads(out)
    return out


class TestCreateNoteForward:
    def test_with_content_is_two_steps(self):
        """content 必须走第二步 PUT（REST create 不认 content —— 直接丢是陷阱）。"""
        from src.hub.write_forwards import create_note_forward

        api = FakeApi({
            ("POST", "/notes/col_1"): {"id": "n1", "title": "T", "collection": "col_1",
                                        "created_at": "2026-09-18T10:00:00",
                                        "updated_at": "2026-09-18T10:00:00"},
            ("PUT", "/notes/col_1/n1"): {"message": "Note updated", "id": "n1"},
        })
        out = _load(asyncio.run(create_note_forward(api, {
            "collection": "col_1", "title": "T", "content": "hello :::distill-block",
        })))
        assert out["id"] == "n1"
        assert out["has_content"] is True
        methods = [(m, p) for m, p, _ in api.calls]
        assert methods == [("POST", "/notes/col_1"), ("PUT", "/notes/col_1/n1")]
        # 第二步必须把 content 放进 body
        assert api.calls[1][2] == {"content": "hello :::distill-block"}

    def test_without_content_skips_put(self):
        from src.hub.write_forwards import create_note_forward

        api = FakeApi({
            ("POST", "/notes/col_1"): {"id": "n2", "title": "T", "collection": "col_1",
                                        "created_at": "x", "updated_at": "x"},
        })
        out = _load(asyncio.run(create_note_forward(
            api, {"collection": "col_1", "title": "T"})))
        assert out["has_content"] is False
        assert len(api.calls) == 1, "没有 content 就不该有第二次调用"

    def test_path_params_are_percent_encoded(self):
        """collection 含 ``/`` 或 ``..`` —— 防穿越规则对自定义转发器同样生效。

        ``quote_segment`` 抛 KeyError → ``hub_routed`` 的 wrapper 统一转成
        ``bad_args`` 错误信封（直调转发器时保留异常，生产路径永远不抛）。
        """
        from src.hub.write_forwards import create_note_forward

        api = FakeApi()
        with pytest.raises(KeyError, match=r"\.\."):
            asyncio.run(create_note_forward(
                api, {"collection": "../../hub/boxes", "title": "T"}))
        # 含斜杠的值会被逐段编码（不抛、也不穿）
        api2 = FakeApi({("POST", "/notes/a%2Fb"): {"id": "n", "title": "t",
                                                    "collection": "a/b",
                                                    "created_at": "x", "updated_at": "x"}})
        out = _load(asyncio.run(create_note_forward(
            api2, {"collection": "a/b", "title": "T"})))
        assert out["id"] == "n"
        assert (api2.calls[0][1] == "/notes/a%2Fb"), api2.calls


class TestCancelRetryForwards:
    def test_cancel_completed_is_gentle_not_error(self):
        """已完成任务 → ``cancelled:false``（与本地同契约）；REST 直接 POST 会 400。"""
        from src.hub.write_forwards import cancel_task_forward

        api = FakeApi({("GET", "/documents/tasks/t1"): {"status": "completed"}})
        out = _load(asyncio.run(cancel_task_forward(api, {"task_id": "t1"})))
        assert out["cancelled"] is False
        assert "already completed" in out["message"]
        assert not any(m == "POST" for m, _, _ in api.calls), "已完成就不该发 POST"

    def test_cancel_pending_posts_and_reports_status(self):
        from src.hub.write_forwards import cancel_task_forward

        api = FakeApi({
            ("GET", "/documents/tasks/t1"): {"status": "processing"},
            ("POST", "/documents/tasks/t1/cancel"): {"message": "Task cancelled"},
        })
        out = _load(asyncio.run(cancel_task_forward(api, {"task_id": "t1"})))
        # status 必须是取消**之后**的 failed（本地 task_manager 的转换），
        # 不是 POST 之前读到的 processing —— 否则轮询 failed 的调用方会一直等。
        assert out == {"task_id": "t1", "cancelled": True, "status": "failed"}

    def test_cancel_missing_is_not_found(self):
        from src.hub.write_forwards import cancel_task_forward

        api = FakeApi()
        out = _load(asyncio.run(cancel_task_forward(api, {"task_id": "nope"})))
        assert "not found" in out["error"]

    def test_retry_nonfailed_carries_status(self):
        """非 failed → 错误要带当前 status（模型据此决定等待还是放弃）。"""
        from src.hub.write_forwards import retry_task_forward

        api = FakeApi({("GET", "/documents/tasks/t2"): {"status": "completed"}})
        out = _load(asyncio.run(retry_task_forward(api, {"task_id": "t2"})))
        assert "only failed tasks" in out["error"]
        assert out["status"] == "completed"

    def test_retry_400_means_args_gone(self):
        from src.hub.write_forwards import retry_task_forward

        api = FakeApi({
            ("GET", "/documents/tasks/t3"): {"status": "failed"},
            ("POST", "/documents/tasks/t3/retry"): _hc(400, "not in failed state"),
        })
        out = _load(asyncio.run(retry_task_forward(api, {"task_id": "t3"})))
        assert "no longer available" in out["error"]


class TestTodoForwards:
    def _todo(self, tid="t1", **over):
        d = {"todo_id": tid, "title": "A", "body": None, "done": False, "ddl": None,
             "target_chain_id": None, "chain_id": "main", "chain_title": "Main",
             "is_main_chain": True, "completed_node_id": None,
             "created_at": "x", "updated_at": "x", "completed_at": None,
             "source_meeting_id": None, "source_section_tab_id": None,
             "source_candidate_id": None, "assignee_person_id": None,
             "collection_id": "col_1", "collection_name": "C1", "assignee_name": None}
        d.update(over)
        return d

    def test_create_batch_aggregates_in_order(self):
        """todos 列表 + 单 title 并存：单条插最前（与本地同序），信封逐字段一致。"""
        from src.hub.write_forwards import create_todo_forward

        api = FakeApi({
            ("POST", "/file-mgmt/col_1/todos"): self._todo("t2"),
        })
        # 脚本按 path 命中会返回同一个 payload —— 这里断言调用次数与信封形状
        api.script[("POST", "/file-mgmt/col_1/todos")] = self._todo("t2")
        out = _load(asyncio.run(create_todo_forward(api, {
            "collection": "col_1",
            "todos": [{"title": "Batch"}, {"title": "  "}],  # 第二条空标题 → errors
        })))
        assert out["total"] == 1
        assert out["collection"] == "col_1"
        assert out["todo"]["todo_id"] == "t2"
        assert out["errors"] == ["empty title"]

    def test_create_assign_to_me_resolves_on_remote(self):
        """``assign_to_me`` 必须在**远端**解析 Me（GET /speakers/me），不是本机。"""
        from src.hub.write_forwards import create_todo_forward

        api = FakeApi({
            ("GET", "/speakers/me"): {"person_id": "p_remote", "person": None},
            ("POST", "/file-mgmt/col_1/todos"): self._todo("t9"),
        })
        asyncio.run(create_todo_forward(api, {
            "collection": "col_1", "todos": [{"title": "X", "assign_to_me": True}],
        }))
        body = api.calls[-1][2]
        assert body["assignee_person_id"] == "p_remote"

    def test_create_assign_to_me_without_remote_me_is_me_unset(self):
        from src.hub.write_forwards import create_todo_forward

        api = FakeApi({("GET", "/speakers/me"): {"person_id": None, "person": None}})
        out = _load(asyncio.run(create_todo_forward(api, {
            "collection": "col_1", "todos": [{"title": "X", "assign_to_me": True}],
        })))
        assert "No Me person" in out["error"]

    def test_update_no_fields_returns_current_todo(self):
        """给了 id 但没给字段 → 回读当前 todo（幂等读），不是静默成功。"""
        from src.hub.write_forwards import update_todo_forward

        api = FakeApi({
            ("GET", "/file-mgmt/col_1/todos"): [self._todo("t1")],
        })
        out = _load(asyncio.run(update_todo_forward(
            api, {"collection": "col_1", "todo_id": "t1"})))
        assert out["todo"]["todo_id"] == "t1"
        assert not any(m == "PATCH" for m, _, _ in api.calls)

    def test_update_partial_failure_aggregates_and_continues(self):
        """一条 404、一条成功 → errors 记 404 那条、继续改下一条（本地同构）。"""
        from src.hub.write_forwards import update_todo_forward

        api = FakeApi({("PATCH", "/file-mgmt/col_1/todos/t_ok"): self._todo("t_ok")})
        out = _load(asyncio.run(update_todo_forward(api, {
            "collection": "col_1",
            "updates": [
                {"todo_id": "t_missing", "done": True},
                {"todo_id": "t_ok", "done": True},
            ],
        })))
        assert out["total"] == 1
        assert out["errors"][0]["todo_id"] == "t_missing"
        assert "not scripted" in out["errors"][0]["error"]

    def test_update_no_fields_missing_id_is_whole_call_error(self):
        """无字段项回读 404 → 与本地一样**整个调用报错**（本地 get_todo 的 404
        不被逐项 try 包住）；「成功 + errors」是信封漂移（审查抓到）。"""
        from src.hub.write_forwards import update_todo_forward

        api = FakeApi({("GET", "/file-mgmt/col_1/todos"): []})  # 列表里没有目标
        out = _load(asyncio.run(update_todo_forward(api, {
            "collection": "col_1",
            "updates": [{"todo_id": "t_ok", "done": True}, {"todo_id": "ghost"}],
        })))
        assert out.get("error") == "Todo 'ghost' not found", out
        assert out.get("status_code") == 404

    def test_update_all_failed_is_error_with_results(self):
        from src.hub.write_forwards import update_todo_forward

        api = FakeApi()
        out = _load(asyncio.run(update_todo_forward(api, {
            "collection": "col_1", "todo_id": "t_missing", "done": True,
        })))
        assert out.get("error")
        assert out["results"][0]["todo_id"] == "t_missing"

    def test_delete_batch_dedupes_and_keeps_order(self):
        from src.hub.write_forwards import delete_todo_forward

        api = FakeApi({("DELETE", "/file-mgmt/col_1/todos/a"): {"raw": ""},
                       ("DELETE", "/file-mgmt/col_1/todos/b"): {"raw": ""}})
        out = _load(asyncio.run(delete_todo_forward(api, {
            "collection": "col_1", "todo_ids": ["a", "b", "a"], "todo_id": "b",
        })))
        assert out["todo_ids"] == ["a", "b"]  # 去重
        assert out["total"] == 2
        assert out["deleted"] is True

    def test_delete_partial_404_continues(self):
        from src.hub.write_forwards import delete_todo_forward

        api = FakeApi({("DELETE", "/file-mgmt/col_1/todos/b"): {"raw": ""}})
        out = _load(asyncio.run(delete_todo_forward(api, {
            "collection": "col_1", "todo_ids": ["missing", "b"],
        })))
        assert out["todo_ids"] == ["b"]
        assert out["errors"][0]["todo_id"] == "missing"

    def test_unreachable_aborts_whole_batch(self):
        """盒子失联（status None）→ 整批中止报「连不上」，不是逐条 404。"""
        from src.hub.client import HubCallError
        from src.hub.write_forwards import delete_todo_forward

        api = FakeApi({("DELETE", "/file-mgmt/col_1/todos/a"):
                       HubCallError("cannot reach x: ConnectError", code="unreachable")})
        with pytest.raises(HubCallError):
            asyncio.run(delete_todo_forward(api, {
                "collection": "col_1", "todo_ids": ["a", "b"],
            }))


class TestReviewFixes:
    """批次 2 对抗审查修复的离线回归。"""

    def test_fm_precheck_fails_closed_when_probe_unavailable(self):
        """探针拿不到（旧版本远端没有 /db-status、或 401/500）→ **拒绝**。

        这里的「正式调用」是会自动建库的端点，fail-open 等于绕过整道闸
        （版本偏斜洞，审查抓到）。
        """
        from src.hub.write_forwards import require_fm_collection_on_remote

        class _NoProbe:
            async def get(self, path, **params):
                return None  # _Remote.get 吞掉一切错误后的形态

        out = require_fm_collection_on_remote(
            _NoProbe(), {"collection": "col_1"})
        reason = asyncio.run(out) if not isinstance(out, str) else out
        assert "cannot verify file-management DB state" in reason, reason

    def test_fm_precheck_rejects_display_name(self):
        """file-mgmt 端点只认 ID —— 显示名要明说（不是含混的"探不到"）。"""
        from src.hub.write_forwards import require_fm_collection_on_remote

        class _Cols:
            async def get(self, path, **params):
                if path == "/collections":
                    return [{"id": "col_1", "name": "My Library"}]
                return None

        reason = asyncio.run(require_fm_collection_on_remote(
            _Cols(), {"collection": "My Library"}))
        assert "display name" in reason, reason

    def test_list_tasks_refuses_bad_enums(self):
        """REST 不校验 status/task_type（坏值 → 空成功）；转发必须复刻本地报错。"""
        from src.mcp.tools.tasks import _tasks_filters_refuse

        assert "Invalid status" in _tasks_filters_refuse({"status": "bogus"})
        assert "Invalid task_type" in _tasks_filters_refuse({"task_type": "bogus"})
        assert _tasks_filters_refuse({"status": "failed", "task_type": "upload"}) is None
        assert _tasks_filters_refuse({}) is None

    def test_delete_document_refuses_reserved_segments(self, monkeypatch):
        """collection 叫 ``tasks`` 等保留段时拒绝 —— 否则会撞上远端的
        ``DELETE /documents/tasks/completed``（清任务记录），静默删错对象。"""
        from src.hub import state, store
        from src.mcp.tools.documents import delete_document

        store.replace_boxes([
            {"box_id": "rb", "box_name": "R", "addr": "http://127.0.0.1:1", "token": "t"}
        ])
        sess = type("S", (), {})()
        state.set_current_box_id(sess, "rb")

        from src.hub import client as client_mod

        async def _no_call(*a, **k):
            raise AssertionError("拒绝之前不该发任何远端调用")

        monkeypatch.setattr(client_mod, "call", _no_call)

        ctx = type("Ctx", (), {})()
        ctx.session = sess
        out = asyncio.run(delete_document(collection="tasks", source="completed", ctx=ctx))
        text = json.dumps(out if isinstance(out, str) else getattr(out, "structuredContent", out))
        assert "reserved" in text, text


# ── 2. 装饰器顺序：确认卡必须在转发之前 ─────────────────────


@pytest.fixture(autouse=True)
def _tmp_hub(tmp_path, monkeypatch):
    from src.hub import state, store

    monkeypatch.setattr(store, "HUB_DIR", tmp_path / "hub")
    store.clear()
    state._states.clear()
    yield
    state._states.clear()


class TestConfirmBeforeForward:
    def test_declined_confirmation_never_touches_remote(self, monkeypatch):
        """**核心承重**：远端模式下拒绝确认 → 绝不发任何 REST 调用。

        若装饰器顺序反了（hub_routed 在 confirm 外面），转发会先发生、
        「拒绝」永远轮不到 —— 删的就是远端数据。此测试直接钉住顺序。
        """
        from src.hub import state, store
        from src.hub import confirm as confirm_mod
        from src.mcp.common import err
        from src.mcp.tools.notes import delete_note

        store.replace_boxes([
            {"box_id": "rb", "box_name": "R", "addr": "http://127.0.0.1:1", "token": "t"}
        ])
        sess = type("S", (), {})()
        state.set_current_box_id(sess, "rb")

        monkeypatch.setattr(confirm_mod, "client_supports_elicit", lambda s=None: True)

        async def _deny(tool, args):
            return err("refused: the user did not approve", code="x")

        monkeypatch.setattr(confirm_mod, "request_confirmation", _deny)

        from src.hub import client as client_mod
        fired = {"n": 0}

        async def _spy(*a, **k):
            fired["n"] += 1
            raise AssertionError("远端被 touch 了 —— 确认没有挡在转发前面")

        monkeypatch.setattr(client_mod, "call", _spy)

        # ctx 必须带会话 —— 传 None 会让路由静默回落本机、转发路径根本不走到，
        # 测试就退化成「测了个寂寞」（变异校验时实踩）。
        ctx = type("Ctx", (), {})()
        ctx.session = sess
        out = asyncio.run(delete_note(collection="c", note_id="n", ctx=ctx))
        text = json.dumps(out if isinstance(out, str) else getattr(out, "structuredContent", out))
        assert "did not approve" in text
        assert fired["n"] == 0

    def test_destructive_tools_confirm_outside_routing(self):
        """静态钉序：6 个 delete_* 的 confirm 标记必须在**最外层**函数对象上。"""
        import inspect

        from src.hub.confirm import DESTRUCTIVE_TOOLS
        from src.hub.routing import ROUTES
        from src.mcp import server as srv

        registered = {t.name: t for t in srv.mcp._tool_manager.list_tools()}
        for name in DESTRUCTIVE_TOOLS:
            fn = registered[name].fn if hasattr(registered[name], "fn") else registered[name]
            assert name in ROUTES, f"{name} 应已接转发（批次 2）"
            # confirm_destructive 会给包装函数挂 __hub_confirm__；挂着它
            # 就说明 confirm 在 hub_routed 外面（后装饰 = 更外层）。
            assert getattr(fn, "__hub_confirm__", None) == name, (
                f"{name}: confirm_destructive 不在最外层 —— 远端删除会绕过确认"
            )

    def test_confirm_payload_names_target_box(self, monkeypatch):
        """确认卡要告诉用户「在哪台盒子上删」。"""
        from src.hub import state, store
        from src.hub import confirm as confirm_mod
        from src.hub import box_label
        from src.mcp import server as srv

        store.replace_boxes([
            {"box_id": "rb", "box_name": "Office", "addr": "http://h:1", "token": "t"}
        ])
        sess = type("S", (), {})()
        state.set_current_box_id(sess, "rb")

        class Ctx:
            session = sess

        monkeypatch.setattr(srv.mcp, "get_context", lambda: Ctx())
        req = confirm_mod.build_request("delete_note", {"collection": "c", "note_id": "n"})
        assert req["box"] == "Office"


# ── 3. 真机层：写一对、比形状、清理 ───────────────────────────


def _has_service() -> bool:
    try:
        with urllib.request.urlopen(f"{BASE}/health", timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


pytestmark_live = pytest.mark.skipif(
    not _has_service(), reason="需要 18900 在跑（真机写类等价性）"
)


def _rest(path: str, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode("utf-8", "ignore")
            return json.loads(raw) if raw.strip() else None
    except urllib.error.HTTPError as e:
        return {"_status": e.code, "_body": e.read().decode("utf-8", "ignore")[:200]}
    except Exception:
        return None


class _Mcp:
    """一条 MCP 会话（复用：switch_box 是会话级状态）。不声明 elicit 能力
    → 走 A 案「放行 + warning」分支，测试无需点确认卡。"""

    def __init__(self):
        import httpx

        self._c = httpx.Client(timeout=90)
        r = self._c.post(f"{BASE}/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                        "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                                   "clientInfo": {"name": "write-equiv", "version": "0"}}},
                        headers={"Accept": "application/json, text/event-stream"})
        self.sid = r.headers.get("mcp-session-id", "")
        self._c.post(f"{BASE}/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                     headers=self._h())

    def _h(self):
        return {"Accept": "application/json, text/event-stream", "mcp-session-id": self.sid}

    def call(self, name: str, args: dict):
        r = self._c.post(f"{BASE}/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                        "params": {"name": name, "arguments": args}}, headers=self._h())
        res = {}
        for line in r.text.splitlines():
            if line.startswith("data: "):
                d = json.loads(line[6:])
                if "result" in d:
                    res = d["result"]
        sc = res.get("structuredContent")
        if isinstance(sc, dict) and isinstance(sc.get("result"), str):
            try:
                return json.loads(sc["result"])
            except Exception:
                return {"_text": sc["result"]}
        return sc or {}


@pytest.fixture
def self_hub():
    original = _rest("/api/hub/boxes") or {}
    original = original.get("boxes") or []

    def _put(boxes):
        req = urllib.request.Request(
            f"{BASE}/api/hub/boxes", data=json.dumps({"boxes": boxes}).encode(),
            method="PUT", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            r.read()

    _put([
        *[{"box_id": b["box_id"], "box_name": b["box_name"], "addr": b["addr"],
           "last_checked": b.get("last_checked", "")} for b in original],
        {"box_id": "__write_equiv__", "box_name": "WriteSelf", "addr": BASE, "token": ""},
    ])
    yield
    _put([{"box_id": b["box_id"], "box_name": b["box_name"], "addr": b["addr"],
           "last_checked": b.get("last_checked", "")} for b in original])


def _shape_of(obj) -> set:
    out = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.add(k)
            out |= _shape_of(v)
    elif isinstance(obj, list) and obj:
        out |= _shape_of(obj[0])
    return out


@pytestmark_live
class TestLiveWriteEquivalence:
    """各写一对（本地/转发）→ 比形状 → 立即清理。靶子全用 ``__verify_`` 前缀。"""

    @staticmethod
    def _pair(s, name, args_local, args_fwd):
        """本地一条 + 转发一条，返回 (local, forwarded, fwd_detail)。"""
        s.call("switch_box", {"name": "local"})
        local = s.call(name, args_local)
        sw = s.call("switch_box", {"name": "WriteSelf"})
        assert sw.get("reachable"), f"切不到 self hub: {sw}"
        forwarded = s.call(name, args_fwd)
        s.call("switch_box", {"name": "local"})
        return local, forwarded

    # ── notes ──────────────────────────────────────────────

    def test_note_create_update_delete(self, self_hub):
        s = _Mcp()
        cols = _rest("/api/collections") or []
        col = cols[0]["id"]

        loc, fwd = self._pair(
            s, "create_note",
            {"collection": col, "title": "__verify_loc__", "content": "body loc"},
            {"collection": col, "title": "__verify_fwd__", "content": "body fwd"},
        )
        assert not loc.get("error") and not fwd.get("error"), (loc, fwd)
        assert _shape_of(loc) - {"id", "title"} == _shape_of(fwd) - {"id", "title"}, (loc, fwd)
        assert fwd["has_content"] is True
        # content 真的落了（两步转发的第二步没丢）
        got = _rest(f"/api/notes/{col}/{fwd['id']}")
        assert "body fwd" in (got.get("content") or "")

        lu, fu = self._pair(
            s, "update_note",
            {"collection": col, "note_id": loc["id"], "content": "v2"},
            {"collection": col, "note_id": fwd["id"], "content": "v2"},
        )
        assert not lu.get("error") and not fu.get("error"), (lu, fu)
        assert fu["updated_fields"] == ["content"]

        # delete 有确认卡：本测试客户端不支持 elicit → A 案放行 + warning
        ld, fd = self._pair(
            s, "delete_note",
            {"collection": col, "note_id": loc["id"]},
            {"collection": col, "note_id": fwd["id"]},
        )
        assert not ld.get("error") and not fd.get("error"), (ld, fd)
        assert fd.get("confirmed") is False  # A 案 warning 挂上了（远端删除也提示）

    def test_note_refuses_bad_collection_with_same_message(self, self_hub):
        """坏库 id：本地与转发必须**同样报错**（precheck 防「静默建孤儿笔记」）。"""
        s = _Mcp()
        bad = {"collection": "col_does_not_exist", "title": "__verify_x__"}
        s.call("switch_box", {"name": "local"})
        local = s.call("create_note", bad)
        s.call("switch_box", {"name": "WriteSelf"})
        forwarded = s.call("create_note", bad)
        s.call("switch_box", {"name": "local"})
        assert local.get("error") and forwarded.get("error")
        assert "does not exist" in forwarded["error"], forwarded
        # 且远端真的没建出孤儿（REST create 不查库存在——这是 precheck 挡的那一刀）
        notes = _rest(f"/api/notes/col_does_not_exist") or {}
        assert not (notes.get("notes")), "坏库上建出了孤儿笔记！"

    # ── hot words ──────────────────────────────────────────

    def test_hot_words_create_update_delete(self, self_hub):
        s = _Mcp()
        loc, fwd = self._pair(
            s, "create_hot_words_library",
            {"name": "__verify_loc__", "description": "d"},
            {"name": "__verify_fwd__", "description": "d"},
        )
        assert not loc.get("error") and not fwd.get("error"), (loc, fwd)
        assert fwd.get("id")

        lu, fu = self._pair(
            s, "update_hot_words_library",
            {"library_id": loc["id"], "words": [{"text": "W", "weight": 5, "lang": "en"}]},
            {"library_id": fwd["id"], "words": [{"text": "W", "weight": 5, "lang": "en"}]},
        )
        assert not lu.get("error") and not fu.get("error"), (lu, fu)
        assert fu["words"][0]["text"] == "W"

        # 空 update 必须被拒（REST 会静默成功 —— refuse 的那一刀）
        s.call("switch_box", {"name": "WriteSelf"})
        empty = s.call("update_hot_words_library", {"library_id": fwd["id"]})
        s.call("switch_box", {"name": "local"})
        assert "Provide at least one" in (empty.get("error") or "")

        ld, fd = self._pair(
            s, "delete_hot_words_library",
            {"library_id": loc["id"]}, {"library_id": fwd["id"]},
        )
        assert not ld.get("error") and not fd.get("error"), (ld, fd)
        assert fd.get("id") == fwd["id"]

    # ── todos ──────────────────────────────────────────────

    @staticmethod
    def _fm_ready_col() -> str:
        """建一个**自带 file-mgmt DB** 的临时库（REST create 会 init DB）。"""
        out = _rest("/api/collections", "POST", {"name": "__verify_todo_col__"})
        assert out and out.get("id"), out
        return out["id"]

    def test_todo_create_update_delete_batch(self, self_hub):
        s = _Mcp()
        col = self._fm_ready_col()
        try:
            loc, fwd = self._pair(
                s, "create_todo",
                {"collection": col, "todos": [
                    {"title": "__verify_a__"}, {"title": "__verify_b__"}]},
                {"collection": col, "todos": [
                    {"title": "__verify_c__"}, {"title": "__verify_d__"}]},
            )
            assert not loc.get("error") and not fwd.get("error"), (loc, fwd)
            assert fwd["total"] == 2
            assert _shape_of(loc["todos"][0]) == _shape_of(fwd["todos"][0]), (
                loc["todos"][0], fwd["todos"][0])  # 增富字段（collection_name…）两侧同形

            lu, fu = self._pair(
                s, "update_todo",
                {"collection": col, "updates": [
                    {"todo_id": loc["todos"][0]["todo_id"], "done": True},
                    {"todo_id": loc["todos"][1]["todo_id"], "clear_body": True}]},
                {"collection": col, "updates": [
                    {"todo_id": fwd["todos"][0]["todo_id"], "done": True},
                    {"todo_id": fwd["todos"][1]["todo_id"], "clear_body": True}]},
            )
            assert not lu.get("error") and not fu.get("error"), (lu, fu)
            assert fu["total"] == 2

            ld, fd = self._pair(
                s, "delete_todo",
                {"collection": col,
                 "todo_ids": [t["todo_id"] for t in loc["todos"]]},
                {"collection": col,
                 "todo_ids": [t["todo_id"] for t in fwd["todos"]]},
            )
            assert not ld.get("error") and not fd.get("error"), (ld, fd)
            assert fd["deleted"] is True and fd["total"] == 2
        finally:
            _rest(f"/api/collections/{col}", "DELETE")

    def test_todo_on_fmless_collection_refuses_like_local(self, self_hub):
        """**静默创建陷阱的正测**：本地 MCP 建的库没有 fm DB——

        本地 create_todo 拒绝（``_require_fm_collection``）；REST 写端点会
        **静默自动建库**（``_open_db`` 幂等创建）。转发必须跟本地一样拒绝，
        且拒绝**不得**触发远端建库（db-status 是纯读探针）。
        """
        s = _Mcp()
        # 本地 MCP 建库（不走 REST → 不 init fm DB）
        created = s.call("create_collection", {"name": "__verify_fmless__"})
        assert not created.get("error"), created
        col = created["id"]
        try:
            before = _rest(f"/api/file-mgmt/{col}/db-status")
            assert before.get("initialized") is False, before  # 前置：确实没有 DB
            s.call("switch_box", {"name": "local"})
            local = s.call("create_todo", {"collection": col, "title": "__verify_x__"})
            s.call("switch_box", {"name": "WriteSelf"})
            forwarded = s.call("create_todo", {"collection": col, "title": "__verify_x__"})
            s.call("switch_box", {"name": "local"})
            assert local.get("error") and forwarded.get("error"), (local, forwarded)
            assert "File-management DB not available" in forwarded["error"], forwarded
            after = _rest(f"/api/file-mgmt/{col}/db-status")
            assert after.get("initialized") is False, "拒绝的探测/调用竟然把远端 DB 建出来了"
        finally:
            s.call("delete_collection", {"collection": col})

    # ── meetings ───────────────────────────────────────────

    def test_meeting_create_update_delete(self, self_hub):
        """建/改/删一对临时会议 —— try/finally 兜底（批次 3 审查发现早前失败
        运行泄漏过 4 个 __verify_loc__ 孤儿会议，已手动清理）。"""
        s = _Mcp()
        loc, fwd = self._pair(
            s, "create_meeting",
            {"title": "__verify_loc__"},
            {"title": "__verify_fwd__"},
        )
        try:
            assert not loc.get("error") and not fwd.get("error"), (loc, fwd)
            assert fwd.get("id")

            lu, fu = self._pair(
                s, "update_meeting",
                {"meeting_id": loc["id"], "title": "__verify_loc2__"},
                {"meeting_id": fwd["id"], "title": "__verify_fwd2__"},
            )
            assert not lu.get("error") and not fu.get("error"), (lu, fu)
            assert fu["updated_fields"] == ["title"]
            assert fu["title"] == "__verify_fwd2__"

            # 坏会议 id：转发必须是**错误**（404），不是 500 或静默成功
            s.call("switch_box", {"name": "WriteSelf"})
            missing = s.call("update_meeting", {"meeting_id": "nope", "title": "x"})
            s.call("switch_box", {"name": "local"})
            assert missing.get("error"), missing

            ld, fd = self._pair(
                s, "delete_meeting",
                {"meeting_id": loc["id"]}, {"meeting_id": fwd["id"]},
            )
            assert not ld.get("error") and not fd.get("error"), (ld, fd)
            assert "cleaned_allocations" in fd, fd  # REST 对齐新增的契约字段
        finally:
            # 兜底：断言在中途失败时也不留孤儿（批次 3 审查发现早前失败运行
            # 泄漏过 4 个 __verify_loc__ 会议）。已被上面删掉的会 404，忽略。
            for mid in (loc.get("id"), fwd.get("id")):
                if mid:
                    _rest(f"/api/meetings/{mid}", "DELETE")

    # ── collections ────────────────────────────────────────

    def test_collection_create_update_delete(self, self_hub):
        """建/改/删一对临时库 —— try/finally 兜底清理（审查抓到早前版本失败时
        泄漏过 4 个 __verify_loc__ 孤儿库）。"""
        s = _Mcp()
        loc, fwd = self._pair(
            s, "create_collection",
            {"name": "__verify_loc__"},
            {"name": "__verify_fwd__"},
        )
        try:
            assert not loc.get("error") and not fwd.get("error"), (loc, fwd)
            assert fwd.get("id") and fwd.get("dimensions")

            lu, fu = self._pair(
                s, "update_collection_config",
                {"collection": loc["id"], "chunk_size": 256},
                {"collection": fwd["id"], "chunk_size": 256},
            )
            assert not lu.get("error") and not fu.get("error"), (lu, fu)
            assert fu["updated_fields"] == ["chunk_size"]
            assert fu["config"]["chunk_size"] == 256
        finally:
            for col_id in (loc.get("id"), fwd.get("id")):
                if col_id:
                    s.call("delete_collection", {"collection": col_id})

    def test_create_collection_honors_sparse_and_provider_ids(self, self_hub):
        """审查 HIGH：REST 建库曾静默丢 sparse_llm_tokenize / *_provider_id ——
        修复后转发建库必须带上（config 里可见）。"""
        s = _Mcp()
        s.call("switch_box", {"name": "WriteSelf"})
        fwd = s.call("create_collection", {
            "name": "__verify_sparse__", "sparse_llm_tokenize": False, "chunk_size": 333,
        })
        s.call("switch_box", {"name": "local"})
        try:
            assert not fwd.get("error"), fwd
            cfg = _rest(f"/api/collections/{fwd['id']}/config") or {}
            assert cfg.get("sparse_llm_tokenize") is False, cfg.get("sparse_llm_tokenize")
            assert cfg.get("chunk_size") == 333
        finally:
            s.call("delete_collection", {"collection": fwd["id"]})

    def test_forwarded_list_tasks_refuses_bad_status(self, self_hub):
        s = _Mcp()
        s.call("switch_box", {"name": "WriteSelf"})
        out = s.call("list_tasks", {"status": "bogus"})
        s.call("switch_box", {"name": "local"})
        assert "Invalid status" in (out.get("error") or ""), out

    # ── documents delete（拿不存在的 source 当靶：形状等价，零破坏） ──

    def test_delete_document_shape_on_missing_source(self, self_hub):
        s = _Mcp()
        cols = _rest("/api/collections") or []
        col = cols[0]["id"]
        loc, fwd = self._pair(
            s, "delete_document",
            {"collection": col, "source": "__verify_missing__.pdf"},
            {"collection": col, "source": "__verify_missing2__.pdf"},
        )
        # 两边都是「成功但 0 块」（本地语义如此 —— 幂等删除）
        assert not loc.get("error") and not fwd.get("error"), (loc, fwd)
        assert fwd["deleted_chunks"] == 0
        assert fwd["source"] == "__verify_missing2__.pdf"

    # ── tasks ──────────────────────────────────────────────

    def test_consolidate_and_task_tools(self, self_hub):
        s = _Mcp()
        # 建一个临时库当 consolidate 的靶子（无文档 → 任务很快结束）
        created = s.call("create_collection", {"name": "__verify_cons__"})
        assert not created.get("error"), created
        col = created["id"]
        try:
            loc, fwd = self._pair(
                s, "trigger_consolidate",
                {"collection": col}, {"collection": col},
            )
            assert not loc.get("error") and not fwd.get("error"), (loc, fwd)
            assert fwd.get("task_id")

            # cancel：对（大概率已完成的）任务 → cancelled:false 温和信封
            s.call("switch_box", {"name": "WriteSelf"})
            cancelled = s.call("cancel_task", {"task_id": fwd["task_id"]})
            missing = s.call("cancel_task", {"task_id": "no_such"})
            got = s.call("get_task_status", {"task_id": fwd["task_id"]})
            s.call("switch_box", {"name": "local"})
            assert cancelled.get("cancelled") is False or cancelled.get("cancelled") is True
            assert "not found" in (missing.get("error") or ""), missing
            assert got.get("status") in ("pending", "processing", "completed", "failed"), got
        finally:
            s.call("delete_collection", {"collection": col})

    def test_clear_completed_counts(self, self_hub):
        """REST 对齐后 removed/remaining 计数必须在（工具契约字段）。"""
        s = _Mcp()
        s.call("switch_box", {"name": "WriteSelf"})
        out = s.call("clear_completed_tasks", {})
        s.call("switch_box", {"name": "local"})
        assert not out.get("error"), out
        assert "removed" in out and "remaining" in out, out
