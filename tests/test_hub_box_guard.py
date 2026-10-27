"""跨盒子守卫 + 修复复盘（2026-09-18 审计）。

**本文件存在的理由是一个真实漏洞**：转发只接了少数工具，而 `switch_box` 是**全局
意图**。若不设守卫，切到「Office」之后调用一个**未接转发**的工具（如 `delete_note`），
它会在**本机**执行 —— 调用方以为在操作 Office，确认卡也会照常弹、用户点了「允许」，
结果删的是本机同 id 的东西。**静默作用在错误对象上**比报错危险得多。

守卫挂在 ``FastMCP.call_tool``（覆盖全部工具，含**将来新增**的），fail-closed。

跑法：``pytest tests/test_hub_box_guard.py``
"""

from __future__ import annotations

import asyncio
import json

import pytest


@pytest.fixture(autouse=True)
def _tmp_hub(tmp_path, monkeypatch):
    from src.hub import state, store

    monkeypatch.setattr(store, "HUB_DIR", tmp_path / "hub")
    store.clear()
    state._states.clear()
    yield
    state._states.clear()


class FakeSession:
    pass


class FakeCtx:
    def __init__(self, session=None):
        self.session = session


# ── ★ 守卫的核心行为 ─────────────────────────────────────────


class TestGuardRefusesUnforwarded:
    def test_remote_box_plus_unforwarded_tool_is_refused(self, monkeypatch):
        """**核心回归**：切到远端 + 未接转发的工具 → 拒绝，**不在本机执行**。

        样本用**合成工具名**（批次 4.5 后全部真实工具已接转发——合成名走的是
        同一条「不在 ROUTES」守卫分支，且永不过期；早前用真实工具当样本，
        每接一批就要换一次，2026-09-18/19 实踩三轮）。
        """
        from src.mcp import server as srv
        from src.hub import state, store

        SAMPLE = "some_tool_added_next_year"
        store.replace_boxes([{"box_id": "rb1", "box_name": "RemoteBox", "addr": "http://h:1", "token": "t"}])
        sess = FakeSession()
        state.set_current_box_id(sess, "rb1")

        called = {"n": 0}
        real = srv._original_call_tool

        async def spy(name, arguments):
            called["n"] += 1
            return "REAL-RAN"

        monkeypatch.setattr(srv, "_original_call_tool", spy)
        monkeypatch.setattr(srv.mcp, "get_context", lambda: FakeCtx(sess))

        out = asyncio.run(srv.mcp.call_tool(SAMPLE, {"collection": "c"}))
        text = _refusal_text(out)
        assert "only works on this machine" in text
        assert called["n"] == 0, "**守卫失效：未转发的工具在本机执行了**"
        assert "switch_box" in text

    def test_forwarded_tool_passes(self, monkeypatch):
        """已接转发的工具照常走（list_notes 在 ROUTES 里）。"""
        from src.mcp import server as srv
        from src.hub import state, store

        store.replace_boxes([{"box_id": "rb1", "box_name": "R", "addr": "http://h:1", "token": "t"}])
        sess = FakeSession()
        state.set_current_box_id(sess, "rb1")

        monkeypatch.setattr(srv, "_original_call_tool", lambda n, a: _ret("OK"))
        monkeypatch.setattr(srv.mcp, "get_context", lambda: FakeCtx(sess))
        assert asyncio.run(srv.mcp.call_tool("list_notes", {"collection": "c"})) == "OK"

    def test_hub_native_tools_pass(self, monkeypatch):
        """Hub 原语不属于任何盒子，永远放行（否则切到远端后连 switch_box 都用不了）。"""
        from src.mcp import server as srv
        from src.hub import state, store

        store.replace_boxes([{"box_id": "rb1", "box_name": "R", "addr": "http://h:1", "token": "t"}])
        sess = FakeSession()
        state.set_current_box_id(sess, "rb1")

        monkeypatch.setattr(srv, "_original_call_tool", lambda n, a: _ret(f"OK:{n}"))
        monkeypatch.setattr(srv.mcp, "get_context", lambda: FakeCtx(sess))
        for name in ("list_boxes", "switch_box", "search_all_boxes"):
            assert asyncio.run(srv.mcp.call_tool(name, {})) == f"OK:{name}"

    def test_local_mode_is_untouched(self, monkeypatch):
        """**本机（默认）路径行为完全不变** —— 这是绝大多数调用。"""
        from src.mcp import server as srv

        called = {"n": 0}

        async def spy(name, arguments):
            called["n"] += 1
            return "RAN"

        monkeypatch.setattr(srv, "_original_call_tool", spy)
        monkeypatch.setattr(srv.mcp, "get_context", lambda: FakeCtx(FakeSession()))
        # 未选任何盒子 → 直通
        assert asyncio.run(srv.mcp.call_tool("delete_note", {"collection": "c"})) == "RAN"
        assert called["n"] == 1

    def test_no_session_at_all_is_local(self, monkeypatch):
        from src.mcp import server as srv

        monkeypatch.setattr(srv, "_original_call_tool", lambda n, a: _ret("RAN"))
        monkeypatch.setattr(srv.mcp, "get_context", lambda: object())
        assert asyncio.run(srv.mcp.call_tool("delete_collection", {"collection": "c"})) == "RAN"

    def test_guard_failure_refuses_not_falls_through(self, monkeypatch):
        """守卫自己坏了**拒绝**而不是落回本机执行（2026-09-19 反转）。

        旧决策是 fail-open（「失败方向=回到改造前」）；审查指出失败方向也可
        能是「作用在错误盒子/对象」——正是守卫要消灭的危害。现在改为拒绝并
        给出恢复动作（switch_box local + 重试），代价是一次重试而不是删错东西。
        """
        from src.mcp import server as srv

        def boom():
            raise RuntimeError("context unavailable")

        monkeypatch.setattr(srv.mcp, "get_context", boom)
        monkeypatch.setattr(srv, "_original_call_tool", lambda n, a: _ret("RAN"))
        out = asyncio.run(srv.mcp.call_tool("delete_note", {"collection": "c"}))
        text = _refusal_text(out)
        assert "internal error" in text
        assert "switch_box" in text
        # 绝不能静默执行
        assert "RAN" not in text

    def test_guard_covers_unknown_future_tools(self, monkeypatch):
        """**核心**：守卫按「在不在 ROUTES」判定，所以**将来新增**的工具自动被覆盖。

        这条测的是「不用改守卫就能拦住新工具」——装饰器方案恰恰会在这种时候漏。
        """
        from src.mcp import server as srv
        from src.hub import state, store

        store.replace_boxes([{"box_id": "rb1", "box_name": "R", "addr": "http://h:1", "token": "t"}])
        sess = FakeSession()
        state.set_current_box_id(sess, "rb1")
        monkeypatch.setattr(srv, "_original_call_tool", lambda n, a: _ret("RAN"))
        monkeypatch.setattr(srv.mcp, "get_context", lambda: FakeCtx(sess))

        out = asyncio.run(srv.mcp.call_tool("some_tool_added_next_year", {}))
        assert "only works on this machine" in _refusal_text(out)


async def _ret(value):
    return value


def _refusal_text(out) -> str:
    """取出守卫拒绝时的文本。

    形状是 ``CallToolResult.structuredContent == {"result": <text>}`` ——
    **必须与工具声明的 outputSchema 匹配**，否则 MCP 客户端会把它当协议错误抛
    （``Invalid structured content``）。见 `_refusal_result` 的说明。
    """
    sc = getattr(out, "structuredContent", None) or getattr(out, "structured_content", None)
    if isinstance(sc, dict) and "result" in sc:
        return str(sc["result"])
    if isinstance(out, str):
        try:
            d = json.loads(out)
            if isinstance(d, dict) and "result" in d:
                return str(d["result"])
        except Exception:
            return out
    return ""


# ── 诚实字段：Hub 原语不能出现在 local_only ──────────────────


class TestRoutingHonesty:
    def test_hub_native_tools_not_in_local_only(self):
        """**核心回归**：`search_all_boxes` 曾被列进 `local_only_tools`。

        那是**说反话**：该字段的语义是「作用在某个盒子上、但还没接转发」，
        而 `search_all_boxes` 恰恰查的是**全部**盒子。实测发现过一次。
        """
        from src.hub import store
        from src.hub.routing import HUB_NATIVE_TOOLS
        from src.hub.tools import list_boxes

        store.clear()
        out = json.loads(asyncio.run(list_boxes(FakeCtx(FakeSession()))))
        for name in HUB_NATIVE_TOOLS:
            assert name not in out["local_only_tools"], f"{name} 被误列为「仍打本机」"
            assert name not in out["routed_tools"], f"{name} 不是「已转发」工具"

    def test_local_only_are_real_registered_tools(self):
        """清单里每个名字都必须真在注册表里（防拼写/幽灵条目）。"""
        from src.mcp.server import MCP_TOOL_NAMES
        from src.hub.tools import list_boxes

        out = json.loads(asyncio.run(list_boxes(FakeCtx(FakeSession()))))
        unknown = [n for n in out["local_only_tools"] if n not in MCP_TOOL_NAMES]
        assert unknown == []


# ── 确认卡必须说清在哪台机器上执行 ───────────────────────────


class TestConfirmShowsTargetBox:
    """确认提示语里「在哪台盒子上执行」的信息。

    注意：确认机制已改成 **A 案**（在 Agent 侧用 ``elicit`` 问，见
    ``tests/test_hub_confirm.py``）。这里只保留与「盒子名字」相关的部分 ——
    它现在住在 :mod:`src.hub.box_label`（从 confirm 抽出来，避免 routing↔confirm 循环）。
    """

    def test_payload_carries_box_field(self):
        from src.hub.confirm import build_request

        req = build_request("delete_note", {"collection": "c"})
        assert "box" in req
        assert req["box"] == "", "本机时为空串"

    def test_current_box_label_empty_when_local(self):
        from src.hub import box_label, store

        store.clear()
        # 没有 MCP 上下文（测试环境）→ 空串 = 本机，且**绝不抛**
        assert box_label.current_box_label() == ""

    def test_current_box_label_resolves_name(self, monkeypatch):
        from src.hub import box_label, state, store

        store.replace_boxes(
            [{"box_id": "b1", "box_name": "Office", "addr": "http://h:1", "token": "t"}]
        )
        sess = FakeSession()
        state.set_current_box_id(sess, "b1")

        class Ctx:
            session = sess

        from src.mcp import server as srv

        monkeypatch.setattr(srv.mcp, "get_context", lambda: Ctx())
        assert box_label.current_box_label() == "Office"

    def test_label_failure_never_raises(self, monkeypatch):
        """展示用的查询失败不能让一次真实操作挂掉 → 必须返回空串。"""
        from src.hub import box_label
        from src.mcp import server as srv

        def boom():
            raise RuntimeError("no context")

        monkeypatch.setattr(srv.mcp, "get_context", boom)
        assert box_label.current_box_label() == ""
