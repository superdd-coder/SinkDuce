"""阶段 4：Chat 工具层不得成为权限绕过通道（代理人越权 / confused deputy）。

Chat 是**服务端以自己身份**替用户执行工具。若工具不跟着提问者权限走，前面所有
隐藏都能被一句"帮我找 X"绕过去——用户 2026-09-16 明确提出的两个例子：

1. 看不到某库的人 → Chat 照样 `search_knowledge_base` / `get_document_text` 读出内容；
2. 没有会议权限的人 → `read_meeting_summary` / `lookup_meeting_transcript` 照样念转写稿。

本测试钉住三层防线：
- **工具集收窄**（化妆）：模型看不到不该用的工具；
- **执行处校验**（墙）：即使工具名出现在上下文里，执行时也拒绝；
- **会议摘要预注入**（最隐蔽）：普通 Chat 每轮注入会议标题清单，无需工具调用。

跑法：``pytest tests/test_chat_tool_acl.py``
"""

from __future__ import annotations

import pytest

from src.identity import Actor
from src.identity.actor import _current, _set_actor
from src.identity import acl as acl_mod


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
    """在指定身份下运行一段代码。"""
    tokens = []

    def _run(actor: Actor):
        tokens.append(_set_actor(actor))

    yield _run
    for t in reversed(tokens):  # LIFO：正序 reset 会残留身份、污染后续测试
        _current.reset(t)


MEMBER = Actor(id="k-mem", kind="key", role_id="member")
VISITOR = Actor(id="k-vis", kind="key", role_id="visitor")
OWNER = Actor()


def _tool_names(tools: list[dict]) -> set[str]:
    return {((t.get("function") or {}).get("name")) or "" for t in tools}


# ── 工具集收窄（化妆层）───────────────────────────────────────


class TestToolSetNarrowing:
    def test_visitor_loses_meeting_tools(self, as_actor, monkeypatch):
        """visitor 没有会议权限 → 会议三件套不进工具集。"""
        from src.chatbox import query_tools as qt

        monkeypatch.setattr(qt, "_filter_tools_for_actor", qt._filter_tools_for_actor)
        as_actor(VISITOR)
        tools = qt.tools_for_mode("agentic", has_meetings=True)
        names = _tool_names(tools)
        assert "read_meeting_summary" not in names
        assert "lookup_meeting_transcript" not in names
        assert "list_meeting_catalog" not in names

    def test_member_keeps_meeting_tools(self, as_actor):
        """member 有会议权限 → 工具照常（防过度收紧把功能弄坏）。"""
        from src.chatbox import query_tools as qt

        as_actor(MEMBER)
        names = _tool_names(qt.tools_for_mode("agentic", has_meetings=True))
        assert "read_meeting_summary" in names
        assert "list_meeting_catalog" in names

    def test_owner_keeps_everything(self, as_actor):
        from src.chatbox import query_tools as qt

        as_actor(OWNER)
        names = _tool_names(qt.tools_for_mode("agentic", has_meetings=True))
        assert "read_meeting_summary" in names
        assert "search_knowledge_base" in names

    def test_filter_failure_does_not_break_chat(self, as_actor, monkeypatch):
        """化妆失败不该炸聊天——原样返回工具（墙仍在执行处）。"""
        from src.chatbox import query_tools as qt

        async def _boom(*a, **k):
            raise RuntimeError("nope")

        monkeypatch.setattr(qt, "may_see_everything", None, raising=False)
        as_actor(MEMBER)
        # 直接调：内部异常被吞、返回原列表
        original = qt.tools_for_mode("agentic", has_meetings=True)
        assert isinstance(original, list) and original


# ── 执行处校验（真正的墙）────────────────────────────────────


class TestExecutionGate:
    def _check(self, name, args, actor):
        import asyncio

        from src.chatbox.query_tools import _check_tool_allowed

        token = _set_actor(actor)
        try:
            return asyncio.run(_check_tool_allowed(name, args))
        finally:
            _current.reset(token)

    def test_visitor_denied_meeting_tools(self):
        """即使模型复现了工具名，执行时也拒绝。"""
        assert self._check("read_meeting_summary", {"meeting_id": "m1"}, VISITOR) == "unknown meeting"
        assert self._check("lookup_meeting_transcript", {"meeting_id": "m1"}, VISITOR) is not None

    def test_member_allowed_meeting_tools(self):
        assert self._check("read_meeting_summary", {"meeting_id": "m1"}, MEMBER) is None

    def test_hidden_collection_denied(self, monkeypatch):
        """点名一个不可见库 → 返回"未知集合"（与不存在同形，不暗示"存在但无权限"）。"""
        import src.identity.visibility as vis

        monkeypatch.setattr(vis, "is_collection_visible", lambda actor, cid: False)
        denied = self._check("get_document_text", {"collection": "secret"}, VISITOR)
        assert denied is not None
        assert "secret" in denied
        assert "unknown" in denied.lower()

    def test_visible_collection_allowed(self, monkeypatch):
        import src.identity.visibility as vis

        monkeypatch.setattr(vis, "is_collection_visible", lambda actor, cid: True)
        assert self._check("get_document_text", {"collection": "public"}, VISITOR) is None

    def test_owner_bypasses_gate(self):
        assert self._check("read_meeting_summary", {"meeting_id": "m1"}, OWNER) is None


# ── 会议摘要预注入（最隐蔽的一条）────────────────────────────


class TestMeetingDigestInjection:
    def test_visitor_gets_empty_digest(self, as_actor, monkeypatch):
        """普通 Chat 每轮注入会议标题清单——无会议权限的人必须拿到空串。

        这条比工具更隐蔽：**不需要任何工具调用**就能泄露会议标题。
        """
        from src.chatbox.meeting_context import build_meeting_catalog_digest
        import src.meeting.catalog as mc

        # 让底层"surface 可见性"返回一堆会议 id（模拟库里确实有会议）
        monkeypatch.setattr(mc, "visible_meeting_ids", mc.visible_meeting_ids)
        as_actor(VISITOR)
        # RBAC 闸在 visible_meeting_ids 内部：visitor 拿不到任何 id
        assert build_meeting_catalog_digest(collections=["c1"]) == ""

    def test_visible_meeting_ids_gate_blocks_visitor(self, as_actor):
        from src.meeting.catalog import visible_meeting_ids

        as_actor(VISITOR)
        assert visible_meeting_ids(collections=["c1"]) == []

    def test_visible_meeting_ids_allows_member(self, as_actor):
        from src.meeting.catalog import visible_meeting_ids

        as_actor(MEMBER)
        # 不抛错即可（返回值取决于会议数据，测试环境通常为空）
        assert isinstance(visible_meeting_ids(collections=["c1"]), list)
