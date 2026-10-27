"""MCP 写操作确认 —— **A 案：在 Agent 侧问**（2026-09-18 用户拍板）。

## 这个文件要钉住的三件事

1. **确认属于 Agent 侧，不属于盒子**。第一版把卡做在盒子网页上 —— 那是错的
   （主体错位：驱使 Agent 的人坐在 Agent 客户端；而且做成"没有浏览器就拒绝"会把
   headless Agent 的删除功能整个堵死）。所以这里**不再有任何进程内 SSE/浏览器确认**。
2. **`elicit` 可用时**：拒绝/取消 → 拦下；同意 → 放行。
3. **`elicit` 不可用**（老客户端）→ **放行**，但结果里必须带 ``warning`` ——
   放行不是静默。

⚠️ **验证方式很关键**：确认逻辑读的是**会话的能力声明**，所以必须用真客户端
（``mcp.client.streamable_http`` + ``ClientSession``）跑，不能只调 Python 方法。
第一版就是因为只调 Python 方法而"全绿"，真客户端上完全不生效（见交接档 §3.6）。
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading

import pytest


@pytest.fixture(scope="module")
def live_server():
    """起一个**真的** SinkDuce MCP 服务（本线程内 uvicorn），供真客户端连。

    直接用 ``src.mcp.server:get_http_app()``，所以测的是**线上那条路**。

    ⚠️ **必须 module 作用域**：``StreamableHTTPSessionManager.run()``
    *每个实例只能跑一次*（SDK 显式 raise），而 :func:`session_lifespan` 用的是
    进程内的单例 manager ⇒ 一个进程里只能进一次 lifespan。做成 function 作用域的话
    第二个测试就会拿到
    「RuntimeError: StreamableHTTPSessionManager .run() can only be called once per
    instance」。
    """
    import uvicorn
    from starlette.applications import Starlette
    from starlette.routing import Route

    from src.mcp.server import get_http_app, session_lifespan

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()

    # 用**最小 Starlette 外壳**把 MCP handler 挂成一条路由（与 src/main.py 同样的挂法）。
    #
    # 为什么不把 MCP sub-app 直接交给 uvicorn：uvicorn 会去跑**应用自己的 lifespan**，
    # 而 MCP sub-app 的 lifespan 也想启动那个单例 session manager → 与下面显式进入的
    # ``session_lifespan()`` 撞车，报
    # 「StreamableHTTPSessionManager .run() can only be called once per instance」
    # （uvicorn 侧表现为 startup 失败 + SystemExit(3)）。Starlette 默认 lifespan 是空操作，
    # 于是 manager 只由我们显式启动这一次 —— 线上 src/main.py 正是这个结构。
    app = Starlette(routes=[Route("/mcp", get_http_app(), methods=["GET", "POST", "DELETE"])])
    ready, stop = threading.Event(), threading.Event()

    def run():
        async def _serve():
            # **必须进 session_lifespan**：它跑 FastMCP 的 StreamableHTTPSessionManager
            # 的 anyio task group；不进的话每个 /mcp 请求都会
            # 「RuntimeError: Task group is not initialized」。src/main.py 也是这么做的。
            async with session_lifespan():
                cfg = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
                server = uvicorn.Server(cfg)
                task = asyncio.create_task(server.serve())
                while not server.started:
                    await asyncio.sleep(0.02)
                ready.set()
                while not stop.is_set():
                    await asyncio.sleep(0.05)
                server.should_exit = True
                await task

        asyncio.run(_serve())

    t = threading.Thread(target=run, daemon=True)
    t.start()
    assert ready.wait(20), "测试用 MCP 服务没起来"
    yield port
    stop.set()
    t.join(timeout=10)


@pytest.fixture(autouse=True)
def _stub_services(monkeypatch):
    """测试环境没有 Qdrant，``services.db`` 是 None。

    确认层测的是「有没有问、问了之后放不放行」，所以把**库存在性**与
    **真实删除动作**桩掉，让请求能走到工具正文并成功返回 —— 这样才能断言
    「放行后确实执行了 + 带了 warning」。
    """
    # ⚠️ 要打在**工具模块里的那个名字**上，不是 `src.mcp.common`：
    # 工具是 `from src.mcp.common import ... require_collection ...` 静态导入的，
    # 名字在导入时就绑到工具模块的命名空间了 —— 改 common 模块的属性对已绑定的名字无效
    # （这类「patch 对地方」的坑本轮踩过不止一次）。
    from src.mcp.tools import notes as notes_tool

    monkeypatch.setattr(notes_tool, "require_collection", lambda c: None)

    from src.notes import store as nstore

    # 约定：``note_id`` 以 ``__missing`` 开头 → 桩成"查不到"，
    # 用来测「操作失败时不该贴那条未确认提示」。
    monkeypatch.setattr(
        nstore, "get_note",
        lambda nid: None if str(nid).startswith("__missing") else object(),
    )
    monkeypatch.setattr(nstore, "delete_note", lambda nid: True)


def _call(port: int, name: str, args: dict, *, elicit: bool, answer=None):
    """用真客户端调一次工具。

    ``elicit=False`` → 客户端**不声明** elicitation 能力（模拟老客户端）。
    ``answer`` → 收到 elicit 请求时返回的动作：``"accept"`` / ``"decline"`` /
    ``"cancel"``。
    """
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    from mcp.shared.exceptions import McpError
    import mcp.types as types

    seen: list[str] = []

    async def main():
        async with streamablehttp_client(f"http://127.0.0.1:{port}/mcp") as (r, w, _):
            kwargs = {}
            if elicit:
                async def cb(context, params):
                    seen.append(params.message)
                    act = answer or "decline"
                    if act == "accept":
                        content = {"confirm": True}
                    elif act == "accept_unchecked":
                        # 接受了表单但**没勾确认框** —— 等价于拒绝，
                        # 也是唯一能分辨「读到 .data」还是「读到 .content」的用例：
                        # 读错字段时 .content 为 None → 会被误判成同意。
                        act, content = "accept", {"confirm": False}
                    else:
                        content = None
                    return types.ElicitResult(action=act, content=content)

                kwargs["elicitation_callback"] = cb
            async with ClientSession(r, w, **kwargs) as s:
                await s.initialize()
                try:
                    res = await s.call_tool(name, args)
                except Exception as exc:  # 客户端侧校验失败也要能看到
                    return {"_exc": f"{type(exc).__name__}: {exc}"}
                sc = res.structuredContent
                if isinstance(sc, dict) and isinstance(sc.get("result"), str):
                    try:
                        return json.loads(sc["result"])
                    except Exception:
                        return {"_text": sc["result"]}
                return sc or {"_isError": res.isError, "_text": (res.content or [{}])[0].text if res.content else ""}

    return asyncio.run(main()), seen


# ── ★ 客户端**支持** elicit：真的问、真的拦 ────────────────────


class TestClientSupportsElicit:
    def test_decline_blocks_and_asks_the_client(self, live_server):
        """**核心**：拒绝 → 不执行；并且**确实向 Agent 客户端**发了 elicit 请求。

        ``seen`` 里应当有那条问句 —— 那是"确认发生在 Agent 侧"的直接证据
        （盒子侧没有任何确认通道了）。
        """
        out, seen = _call(
            live_server, "delete_note",
            {"collection": "col_x", "note_id": "n"},
            elicit=True, answer="decline",
        )
        assert out.get("code") == "confirmation_declined", out
        assert seen, "没有向 Agent 客户端发 elicit —— 确认没发生在 Agent 侧！"
        assert "irreversible" in seen[0] or "cannot be undone" in seen[0]
        # 拒绝时提示语里要有「别再试」
        assert "do not retry" in out["error"].lower()

    def test_cancel_blocks_too(self, live_server):
        out, seen = _call(
            live_server, "delete_note",
            {"collection": "col_x", "note_id": "n"},
            elicit=True, answer="cancel",
        )
        assert out.get("code") == "confirmation_declined"
        assert seen

    def test_accept_proceeds_to_the_tool_body(self, live_server):
        """同意 → 放行到工具本体（并真的执行了）。

        服务层被桩掉（测试环境无 Qdrant），所以这里看到的是 delete_note 的
        **成功返回**；关键断言是「确认错误不在」+「确实问过客户端」。
        """
        out, seen = _call(
            live_server, "delete_note",
            {"collection": "col_x", "note_id": "n"},
            elicit=True, answer="accept",
        )
        blob = json.dumps(out, ensure_ascii=False)
        assert "confirmation_declined" not in blob
        assert out.get("id") == "n", blob
        assert seen
        # 已确认过的执行**不该**再被贴「未确认」警告
        assert "confirmed" not in out, blob

    def test_accept_but_unchecked_is_denied(self, live_server):
        """**核心**：用户接受了表单、但没勾「确认」→ 必须**拦下**。

        这条专门分辨服务端读的是哪个字段：FastMCP 的 ``AcceptedElicitation``
        把校验后的模型放在 ``.data``，而**协议层**（``ElicitResult``）用的是
        ``.content``。读错字段时 ``.data``/``.content`` 一方为 None，会被误判成同意 ——
        实测踩过（最初的实现读 ``.content``，于是"拒绝"被静默当成了"同意"）。
        """
        out, seen = _call(
            live_server, "delete_note",
            {"collection": "col_x", "note_id": "n"},
            elicit=True, answer="accept_unchecked",
        )
        assert seen, "应该问过客户端"
        assert out.get("code") == "confirmation_declined", (
            "没勾确认框却被当成同意执行了：" + json.dumps(out, ensure_ascii=False)
        )

    def test_message_names_the_target(self, live_server):
        """问句里要出现目标标识，用户才知道自己在批准什么。"""
        _out, seen = _call(
            live_server, "delete_note",
            {"collection": "col_abc", "note_id": "n"},
            elicit=True, answer="decline",
        )
        assert "col_abc" in seen[0], seen


# ── ★ 客户端**不支持** elicit：放行 + 明确警告 ──────────────────


class TestClientWithoutElicit:
    def test_proceeds_but_warns(self, live_server):
        """**A 案的核心行为**：老客户端上**照常执行**，但结果必须带 warning。

        - 执行（不是拒绝）→ 不因为客户端没实现 elicit 就把删除功能堵死；
        - 带 ``warning`` → 放行不是静默，模型看得到、也该转告用户。
        """
        out, seen = _call(
            live_server, "delete_note",
            {"collection": "col_x", "note_id": "n"},
            elicit=False,
        )
        assert not seen, "客户端没声明能力，不该收到 elicit"
        blob = json.dumps(out, ensure_ascii=False)
        assert "confirmation_declined" not in blob, "A 案下不该拦：" + blob
        assert out.get("id") == "n", "应该真的执行了：" + blob
        assert out.get("confirmed") is False, blob
        assert "irreversible" in str(out.get("warning", "")), blob

    def test_no_warning_when_the_operation_failed(self, live_server):
        """**核心**：操作失败时**不能**贴「未确认就执行」的提示。

        那条提示是「它没经确认就跑了」—— 如果操作本身失败了（目标不存在），
        说它"跑了"是假信息。实测发现过（"Note not found" 结果上挂着那条 warning）。
        """
        out, _ = _call(
            live_server, "delete_note",
            {"collection": "col_x", "note_id": "__missing_note__"},
            elicit=False,
        )
        assert "not found" in json.dumps(out, ensure_ascii=False), out
        assert "confirmed" not in out, f"失败结果不该贴未确认提示：{out}"
        assert "warning" not in out, f"失败结果不该贴 warning：{out}"

    def test_ungated_tool_gets_no_confirmation_mark(self, live_server):
        """未闸的工具（这里用 Hub 原语 list_boxes）不该被贴确认标记。"""
        out, _ = _call(live_server, "list_boxes", {}, elicit=False)
        assert "confirmed" not in out, "未闸工具不该被加确认标记"


# ── 覆盖面（名称变体也要拦住）────────────────────────────────


class TestCoverage:
    def test_every_destructive_looking_tool_is_decided(self):
        from src.hub.confirm import REVIEWED_UNGLATED, DESTRUCTIVE_TOOLS, _DESTRUCTION_HINT
        from src.mcp.server import MCP_TOOL_NAMES

        undecided = [
            n for n in MCP_TOOL_NAMES
            if _DESTRUCTION_HINT.search(n)
            and n not in DESTRUCTIVE_TOOLS
            and n not in REVIEWED_UNGLATED
        ]
        assert undecided == [], f"名字像破坏性，但既没闸也没说明：{undecided}"

    def test_every_gated_tool_has_the_decorator(self):
        from src.hub.confirm import DESTRUCTIVE_TOOLS
        from src.mcp.server import mcp

        reg = {t.name: t for t in mcp._tool_manager.list_tools()}
        for name in DESTRUCTIVE_TOOLS:
            fn = reg[name].fn if hasattr(reg[name], "fn") else reg[name]
            assert getattr(fn, "__hub_confirm__", None) == name

    def test_reversible_writes_are_not_gated(self):
        """**刻意的边界**：新增/修改/上传不闸（可逆；闸上会让 Agent 处处受限）。"""
        from src.mcp.server import mcp

        reg = {t.name: t for t in mcp._tool_manager.list_tools()}
        for name in ("create_note", "update_note", "create_todo", "upload_file_from_staging"):
            fn = reg[name].fn if hasattr(reg[name], "fn") else reg[name]
            assert getattr(fn, "__hub_confirm__", None) is None

    def test_reviewed_ungated_entries_have_reasons(self):
        from src.hub.confirm import REVIEWED_UNGLATED

        for name, why in REVIEWED_UNGLATED.items():
            assert why and len(why) >= 10, f"{name} 的「不闸理由」太短"


# ── 载荷构造 ─────────────────────────────────────────────────


class TestBuildRequest:
    def test_no_credentials_in_payload(self):
        from src.hub.confirm import build_request

        req = build_request(
            "delete_note",
            {"collection": "c", "note_id": "n", "token": "sd1_secret",
             "ctx": object(), "content": "x" * 5000},
        )
        blob = json.dumps(req, default=str)
        assert "sd1_secret" not in blob
        assert "content" not in req["args"] and "ctx" not in req["args"]

    def test_target_prefers_readable_name(self):
        from src.hub.confirm import build_request

        req = build_request("delete_collection", {"collection": "col_x", "collection_name": "RAG 笔记"})
        assert "RAG 笔记" in req["summary"]

    def test_mentions_box_only_when_not_local(self):
        from src.hub.confirm import build_request

        assert build_request("delete_note", {"collection": "c"})["box"] == ""


# ── ★ 不该再有盒子侧确认通道 ─────────────────────────────────


class TestNoBoxSideConfirm:
    def test_no_confirm_event_registered(self):
        """`hub.confirm_request` 事件与 `hub` 域都该删掉 —— 确认不再走盒子。"""
        from src.events.models import DOMAIN_BY_TYPE

        assert "hub.confirm_request" not in DOMAIN_BY_TYPE

    def test_no_bus_subscriber_probe(self):
        """`bus.subscriber_count` 是给浏览器确认卡加的（"没人听就拒"）。
        A 案下它既不需要、也不该有 —— 留着会引诱人再写一个"没浏览器就拒"。"""
        from src.events.bus import EventBus

        assert not hasattr(EventBus, "subscriber_count")

    def test_no_frontend_confirm_artifacts(self):
        """前端确认卡组件/状态/接口都该不存在。"""
        import pathlib

        for f in (
            "frontend/src/lib/hub-confirm.ts",
            "frontend/src/components/layout/hub-confirm-dialog.tsx",
        ):
            assert not pathlib.Path(f).exists(), f"{f} 应该已删除（确认不在盒子里）"

    def test_no_confirm_route(self):
        import pathlib

        src = pathlib.Path("src/api/routes/sessions.py").read_text()
        assert "hub-confirm" not in src
