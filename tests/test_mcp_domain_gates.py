"""MCP 工具层的域闸门（2026-09-18）。

**背景（实测的泄露）**：`meetings` / `hot_words` 两个域在 REST 上有 router 级依赖
（`APIRouter(dependencies=[Depends(require_domain(...))])`）兜底，而 MCP 工具**直连
store**，此前**零防护**——授权点统计是 0。visitor 钥匙走 MCP 能读到全部会议标题、
会议转写稿、热词库，而同一把钥匙走 REST 是被 403 挡住的。

**同一把钥匙，两条通道答案必须一致**：REST 拒绝、MCP 放行 = 权限模型的裂缝。

本测试钉三件事：
1. 无该域 read 档 → MCP 工具被拒且**不返回任何数据**；
2. 有该域 read 档 → 照常放行（别过度收紧，把功能弄坏）；
3. owner / 无身份上下文 → 放行（本机 Agent 走回环，不该被自己的闸门拦住）。

跑法：``pytest tests/test_mcp_domain_gates.py``
"""

from __future__ import annotations

import asyncio
import json

import pytest

from src.identity import Actor
from src.identity.actor import _current, _set_actor
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


@pytest.fixture
def as_actor():
    tokens = []

    def _run(actor):
        tokens.append(_set_actor(actor))

    yield _run
    for t in reversed(tokens):  # LIFO：正序 reset 会残留身份
        _current.reset(t)


def _call(coro):
    return asyncio.run(coro)


def _is_denied(payload: str) -> bool:
    try:
        d = json.loads(payload)
    except Exception:
        return False
    return "error" in d and d.get("code") == "forbidden"


# ── 会议域 ────────────────────────────────────────────────────


class TestMeetingDomainGate:
    def test_visitor_denied_list_meetings(self, as_actor):
        """**核心回归**：visitor 无 meeting read 档 → 被拒，且不返回会议数据。"""
        from src.mcp.tools.meetings import list_meetings

        as_actor(VISITOR)
        out = _call(list_meetings())
        assert _is_denied(out), f"visitor 竟能调 list_meetings: {out[:200]}"
        assert "meetings" not in out, "拒绝文案里不该夹带数据"

    def test_visitor_denied_transcript_read(self, as_actor):
        """转写稿是会议域最敏感的内容——必须一起挡住。"""
        from src.mcp.tools.meetings import get_meeting_transcript

        as_actor(VISITOR)
        assert _is_denied(_call(get_meeting_transcript("m1")))

    def test_visitor_denied_writes_too(self, as_actor):
        """写操作同样被域闸门挡住（读取泄露是本次主因，但写不该更宽松）。"""
        from src.mcp.tools.meetings import create_meeting, delete_meeting, update_meeting

        as_actor(VISITOR)
        assert _is_denied(_call(create_meeting("t")))
        assert _is_denied(_call(update_meeting("m1", title="x")))
        assert _is_denied(_call(delete_meeting("m1")))

    def test_member_allowed(self, as_actor, monkeypatch):
        """member 有 read 档 → 放行（别过度收紧）。"""
        from src.meeting import store as mstore

        monkeypatch.setattr(mstore, "list_meetings", lambda: [])
        from src.mcp.tools.meetings import list_meetings

        as_actor(MEMBER)
        out = _call(list_meetings())
        assert not _is_denied(out), f"member 被误拒: {out[:200]}"
        assert '"meetings"' in out

    def test_owner_allowed(self, as_actor):
        from src.mcp.tools.meetings import list_meetings

        as_actor(OWNER)
        assert not _is_denied(_call(list_meetings()))

    def test_admin_allowed(self, as_actor):
        from src.mcp.tools.meetings import list_meetings

        as_actor(ADMIN)
        assert not _is_denied(_call(list_meetings()))

    def test_error_tells_agent_not_to_retry(self, as_actor):
        """文案必须显式叫停重试（用户要求：撞墙别反复撞）。"""
        from src.mcp.tools.meetings import list_meetings

        as_actor(VISITOR)
        out = _call(list_meetings())
        assert "do not retry" in out.lower()

    def test_gate_failure_is_fail_closed(self, as_actor, monkeypatch):
        """判定炸了 → 拒绝（fail-closed），不回落放行。"""
        import src.identity.acl as _acl

        def _boom(domain, tier, role):
            raise RuntimeError("acl down")

        monkeypatch.setattr(_acl, "domain_allows", _boom)
        from src.mcp.tools.meetings import list_meetings

        as_actor(VISITOR)
        assert _is_denied(_call(list_meetings()))


# ── 热词域 ────────────────────────────────────────────────────


class TestHotWordsDomainGate:
    def test_visitor_denied(self, as_actor):
        from src.mcp.tools.hot_words import (
            get_hot_words_library,
            list_hot_words_libraries,
        )

        as_actor(VISITOR)
        assert _is_denied(_call(list_hot_words_libraries()))
        assert _is_denied(_call(get_hot_words_library("lib1")))

    def test_visitor_denied_writes(self, as_actor):
        from src.mcp.tools.hot_words import (
            create_hot_words_library,
            delete_hot_words_library,
            update_hot_words_library,
        )

        as_actor(VISITOR)
        assert _is_denied(_call(create_hot_words_library("n")))
        assert _is_denied(_call(update_hot_words_library("lib1", name="x")))
        assert _is_denied(_call(delete_hot_words_library("lib1")))

    def test_member_allowed(self, as_actor, monkeypatch):
        from src.hot_words import store as hstore
        from src.mcp.tools.hot_words import list_hot_words_libraries

        monkeypatch.setattr(hstore, "list_libraries", lambda: [])
        monkeypatch.setattr(hstore, "get_pinned_library_ids", lambda: [])
        as_actor(MEMBER)
        assert not _is_denied(_call(list_hot_words_libraries()))


# ── 覆盖完整性：装饰器不能漏 ──────────────────────────────────


class TestGateCoverage:
    """**装饰器漏一个就是一次静默的鉴权绕过** —— 用源码扫描钉住全部工具。"""

    def _assert_all_gated(self, path: str, const: str):
        import ast
        import pathlib

        src = pathlib.Path(path).read_text()
        tree = ast.parse(src)
        funcs = [n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)]
        assert funcs, f"{path} 里没找到 async 工具"
        missing = []
        for n in funcs:
            decos = [
                ast.unparse(d) if hasattr(ast, "unparse") else ""
                for d in n.decorator_list
            ]
            if not any(const in d for d in decos):
                missing.append(n.name)
        assert not missing, f"{path} 里这些工具没有域闸门: {missing}"

    def test_meetings_all_gated(self):
        self._assert_all_gated("src/mcp/tools/meetings.py", "DOMAIN_MEETING")

    def test_hot_words_all_gated(self):
        self._assert_all_gated("src/mcp/tools/hot_words.py", "DOMAIN_HOT_WORDS")
