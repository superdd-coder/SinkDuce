"""``mcp_result`` 的形状 + **守卫必须在真实 MCP 入口生效**。

本文件是两个「测试绿、生产坏」的教训：

1. ``mcp_result`` 一度把 ``structuredContent`` 写成 ``structured_content`` —— pydantic
   对未知关键字**默认忽略**，构造不报错，于是结构化载荷**整体丢失**（工具只剩一行
   ``OK ...`` 文本），声明了 ``outputSchema`` 的工具还会变成协议级
   「Output validation error」。
2. 跨盒子守卫一度只覆盖了 ``FastMCP.call_tool``，**而真实 MCP 流量不走它**：
   FastMCP 在构造时就把 ``self.call_tool`` 绑进 lowlevel handler 的闭包了。
   单测直接调 ``mcp.call_tool`` 全绿，真客户端仍然能删错东西。

⇒ 本文件**必须**从 lowlevel 入口（``request_handlers``）验证，不能只调 Python 方法。

跑法：``pytest tests/test_mcp_result_shape.py``
"""

from __future__ import annotations

import asyncio
import json

import pytest
from mcp import types


@pytest.fixture(autouse=True)
def _tmp_hub(tmp_path, monkeypatch):
    from src.hub import state, store

    monkeypatch.setattr(store, "HUB_DIR", tmp_path / "hub")
    store.clear()
    state._states.clear()
    yield
    state._states.clear()


# ── (1) mcp_result 的形状 ────────────────────────────────────


class TestMcpResultShape:
    def test_structured_content_is_populated(self):
        """**核心回归**：``structuredContent`` 必须是真实载荷。

        曾经传错关键字名 → 恒为 ``None``，工具只剩一行文本。单测当时只断言
        「返回的是 JSON 字符串」，所以照绿。
        """
        from src.mcp.common import mcp_result, ok

        r = mcp_result(ok(collection="c", total=3))
        assert r.structuredContent == {"collection": "c", "total": 3}

    def test_error_payload_also_structured(self, ):
        from src.mcp.common import err, mcp_result

        r = mcp_result(err("boom", code="x"))
        assert r.structuredContent == {"error": "boom", "code": "x"}

    def test_text_summary_is_short_not_the_whole_payload(self):
        """文本摘要要短（避免同一份载荷在两处出现、双倍吃上下文）。"""
        from src.mcp.common import mcp_result, ok

        big = {"items": [{"i": i, "text": "x" * 200} for i in range(20)]}
        r = mcp_result(ok(**big))
        assert len(r.content[0].text) < 300, "文本摘要不该塞进整份载荷"

    def test_no_underscore_kwarg_remains(self):
        """防回归：源码里不该再出现 ``structured_content=`` 这个错名字。"""
        import pathlib

        src = pathlib.Path("src/mcp/common.py").read_text()
        assert "structured_content=data" not in src
        assert "structuredContent=data" in src

    def test_passing_wrong_kwarg_is_silently_ignored_by_pydantic(self):
        """说明**为什么**这个 bug 能悄无声息地存在：pydantic 不报错。

        这条不是「要求报错」，而是把该风险**钉进文档化的测试**里，
        让下一个人知道这类字段名错误不会有任何提示。
        """
        from mcp.types import CallToolResult, TextContent

        hidden = CallToolResult(
            content=[TextContent(type="text", text="hi")],
            structured_content={"should": "be ignored"},  # 错名字
        )
        assert hidden.structuredContent is None


# ── (2) 守卫必须在**真实** MCP 入口生效 ──────────────────────


class TestGuardOnRealEntryPoint:
    @pytest.fixture
    def remote_box(self, monkeypatch):
        from src.hub import state, store

        store.replace_boxes(
            [{"box_id": "rb1", "box_name": "RemoteBox", "addr": "http://h:1", "token": "t"}]
        )
        sess = type("S", (), {})()
        state.set_current_box_id(sess, "rb1")

        from src.mcp import server as srv

        class Ctx:
            session = sess

        monkeypatch.setattr(srv.mcp, "get_context", lambda: Ctx())
        return sess

    def test_lowlevel_handler_is_our_wrapper(self):
        """守卫必须重注册到 ``request_handlers``，否则线上不生效。"""
        from src.mcp.server import mcp

        h = mcp._mcp_server.request_handlers[types.CallToolRequest]
        # 这是我们 wrapper 的闭包链（不是 FastMCP 原始注册的那个）
        assert "handler" in h.__qualname__

    def test_call_tool_attribute_patched(self):
        from src.mcp.server import mcp

        assert mcp.call_tool.__name__ == "_box_scoped_call_tool"

    def test_guard_works_through_lowlevel_entry(self, remote_box):
        """**核心回归**：走 lowlevel handler（真实 MCP 入口）也必须被拦住。

        样本用**合成工具名**（批次 4.5 后真实工具已全部接转发；合成名走同一条
        守卫分支且永不过期）。
        """
        from src.mcp.server import mcp
        from src.hub.routing import ROUTES

        SAMPLE = "some_tool_added_next_year"
        handler = mcp._mcp_server.request_handlers[types.CallToolRequest]

        async def run():
            req = types.CallToolRequest(
                method="tools/call",
                params=types.CallToolRequestParams(
                    name="some_tool_added_next_year",
                    arguments={"collection": "c"},
                ),
            )
            return await handler(req)

        res = asyncio.run(run())
        payload = res.root if hasattr(res, "root") else res
        sc = payload.structuredContent
        assert sc, "拒绝结果必须带结构化载荷（否则过不了 outputSchema）"
        assert "result" in sc, f"形状必须匹配 outputSchema，实际={sc}"
        assert "only works on this machine" in sc["result"]

    def test_local_mode_passes_through_lowlevel(self, monkeypatch):
        """本机模式在真实入口上也不受影响（绝大多数调用走这里）。"""
        from src.mcp import server as srv

        monkeypatch.setattr(srv.mcp, "get_context", lambda: object())
        handler = srv.mcp._mcp_server.request_handlers[types.CallToolRequest]

        async def run():
            # 用一个真实存在、参数简单的只读工具
            req = types.CallToolRequest(
                method="tools/call",
                params=types.CallToolRequestParams(name="list_boxes", arguments={}),
            )
            return await handler(req)

        res = asyncio.run(run())
        payload = res.root if hasattr(res, "root") else res
        text = payload.content[0].text if getattr(payload, "content", None) else ""
        assert "not_forwarded" not in text, "本机模式竟被守卫拦了"


# ── (3) 守卫的拒绝结果必须过客户端校验 ───────────────────────


class TestRefusalMatchesOutputSchema:
    """**这是只有真实客户端才抓得到的 bug**。

    工具声明了 ``outputSchema``，而 **MCP 客户端会校验** structuredContent
    （``ClientSession._validate_tool_result``）。守卫最初返回 ``{"error": ...}``，
    46 个 ``{"required": ["result"]}`` 的工具全部变成
    ``RuntimeError: Invalid structured content returned by tool ...`` ——
    一次正确的拒绝被伪装成协议错误。单测（直接调 handler）看不见这一层。
    """

    def test_refusal_shape_is_result_wrapped(self):
        """拒绝的形状：``{"result": text}`` 满足 outputSchema，**且 isError=True**。

        为什么必须标 isError：只给结构化文本而 isError=False 时，按 isError 判定的
        客户端会把"被拒绝"读成"成功但结果为空"（实测过）。形状（result 键）与
        标志（isError）解决的是两件不同的事，都要。
        """
        from src.mcp.server import _refusal_result

        r = _refusal_result("nope")
        assert r.structuredContent["result"] == "nope"
        assert r.structuredContent["error"] == "nope"  # 也给按 error 判定的客户端
        assert r.content[0].text == "nope"
        assert r.isError is True

    def test_every_refusal_validates_against_its_tool_schema(self, monkeypatch):
        """**核心回归**：每个**会被守卫拒绝**的工具，其拒绝结果都要过它自己的 outputSchema。

        模仿客户端的校验路径（jsonschema.validate），所以能抓住"形状对不上"。

        ⚠️ 只针对**未接转发**的工具：已接转发的工具在远端模式下**不会**被守卫拒绝
        （它们真的转发出去了），拿它们测拒绝形状会得到参数校验错误而不是守卫拒绝 ——
        那是测试自己的问题，不是产品的。这一区分是实测踩出来的
        （早先这条把已接工具也算进来，于是红了，看起来像产品坏了）。
        """
        import jsonschema
        from mcp import types

        from src.hub import state, store
        from src.hub.routing import HUB_NATIVE_TOOLS, ROUTES
        from src.mcp import server as srv

        store.replace_boxes(
            [{"box_id": "rb1", "box_name": "RB", "addr": "http://h:1", "token": "t"}]
        )
        sess = type("S", (), {})()
        state.set_current_box_id(sess, "rb1")

        class Ctx:
            session = sess

        monkeypatch.setattr(srv.mcp, "get_context", lambda: Ctx())
        handler = srv.mcp._mcp_server.request_handlers[types.CallToolRequest]

        async def run():
            bad = []
            for tool in srv.mcp._tool_manager.list_tools():
                if tool.name in ROUTES or tool.name in HUB_NATIVE_TOOLS:
                    continue  # 这些不会被守卫拒绝
                req = types.CallToolRequest(
                    method="tools/call",
                    params=types.CallToolRequestParams(name=tool.name, arguments={}),
                )
                res = await handler(req)
                payload = res.root if hasattr(res, "root") else res
                if tool.output_schema and payload.structuredContent is not None:
                    try:
                        jsonschema.validate(payload.structuredContent, tool.output_schema)
                    except Exception as exc:  # noqa: BLE001
                        bad.append((tool.name, str(exc)[:80]))
            return bad



        bad = asyncio.run(run())
        assert bad == [], f"拒绝结果过不了工具自己的 outputSchema：{bad}"
