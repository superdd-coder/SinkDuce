"""功能模块（modules）的**服务端强制**（2026-09-18 用户要求）。

**背景**：`modules`（chat / database / recall / tasks / settings）此前**只在前端化妆**
——侧边栏隐藏入口而已，服务端一次都没检查。用户明确要求「真实的服务端限制」。

用户原话：「里面的 module 权限我想要的是真实的服务端限制，而不只是前端限制。」

**为什么两条通道都要补**：REST 与 MCP 是同一个盒子的两个门。只在 REST 加，
Agent 走 MCP 照样能用 —— 那就是"限制"没落地。

**内置角色的影响面**（决定了这次改动的风险）：
- owner / admin / member：五个模块全 True → 加闸门后**行为完全不变**；
- **visitor：recall=False / tasks=False** —— 这是源码注释里早已声明的意图
  （「只读知识库：进不了任务与回顾」），但从未被服务端强制；
- 自定义角色（Intern 等）：用户显式关掉的模块**开始真正生效**。

跑法：``pytest tests/test_module_enforcement.py``
"""

from __future__ import annotations

import asyncio

import pytest

from src.identity import Actor
from src.identity.actor import _current, _set_actor
from src.identity.roles import (
    ALL_MODULES,
    MODULE_DATABASE,
    MODULE_TASKS,
    get_role,
)

OWNER = Actor()
ADMIN = Actor(id="a", kind="key", role_id="admin")
MEMBER = Actor(id="m", kind="key", role_id="member")
VISITOR = Actor(id="v", kind="key", role_id="visitor")


@pytest.fixture
def as_actor():
    tokens = []

    def _run(actor):
        tokens.append(_set_actor(actor))

    yield _run
    for t in reversed(tokens):
        _current.reset(t)


# ── 前置事实：角色定义本身 ────────────────────────────────────


class TestRoleFacts:
    def test_owner_admin_member_have_all_modules(self):
        """这三个角色全模块开 → 加闸门后行为不变（改动风险的依据）。"""
        for rid in ("owner", "admin", "member"):
            role = get_role(rid)
            assert role is not None
            assert all(role.allows_module(m) for m in ALL_MODULES), f"{rid} 缺模块"

    def test_visitor_lacks_tasks_and_recall(self):
        """visitor 的 recall/tasks 是**早已声明的意图**（源码注释），只是没强制。"""
        role = get_role("visitor")
        assert role is not None
        assert not role.allows_module(MODULE_TASKS)
        assert not role.allows_module("recall")


# ── REST：require_module 依赖 ─────────────────────────────────


class TestRequireModuleRest:
    def _dep(self, mods, actor):
        from src.api.routes.identity import require_module

        tok = _set_actor(actor)
        try:
            require_module(*mods)()   # 不抛 = 放行
            return None
        except Exception as e:
            return e
        finally:
            _current.reset(tok)

    def test_visitor_denied_tasks(self):
        e = self._dep((MODULE_TASKS,), VISITOR)
        assert e is not None and getattr(e, "status_code", None) == 403

    def test_visitor_denied_recall(self):
        assert self._dep(("recall",), VISITOR) is not None

    def test_visitor_allowed_chat_and_database(self):
        assert self._dep(("chat",), VISITOR) is None
        assert self._dep((MODULE_DATABASE,), VISITOR) is None

    def test_member_allowed_everything(self):
        for m in ALL_MODULES:
            assert self._dep((m,), MEMBER) is None, f"member 被误拒 {m}"

    def test_owner_and_admin_bypass(self):
        assert self._dep((MODULE_TASKS,), OWNER) is None
        assert self._dep((MODULE_TASKS,), ADMIN) is None

    def test_multi_module_is_or(self):
        """跨页共享的端点：满足其一即放行（否则关一格会弄坏另一页）。"""
        # visitor: chat=True, tasks=False → OR 应放行
        assert self._dep(("chat", MODULE_TASKS), VISITOR) is None
        # 两个都关 → 拒绝
        assert self._dep(("recall", MODULE_TASKS), VISITOR) is not None

    def test_no_identity_context_passes(self):
        """无身份上下文（测试直挂 router / 内部调用）→ 放行，否则所有直挂测试全崩。"""
        from src.api.routes.identity import require_module

        from src.identity.actor import _current as cur

        # 不设 actor：get_actor() 默认是 owner，自然通过；这里顺带钉住不抛
        assert require_module(MODULE_TASKS)() is None

    def test_unknown_role_denied(self):
        e = self._dep((MODULE_TASKS,), Actor(id="x", kind="key", role_id="nope"))
        assert e is not None and getattr(e, "status_code", None) == 403


# ── MCP：module_gated 装饰器 ──────────────────────────────────


class TestMcpModuleGate:
    def _denied(self, out: str) -> bool:
        return '"forbidden"' in out or "permission denied" in out

    def _call(self, fn, actor, *a, **kw):
        tok = _set_actor(actor)
        try:
            return asyncio.run(fn(*a, **kw))
        finally:
            _current.reset(tok)

    def test_visitor_denied_list_tasks(self):
        from src.mcp.tools.tasks import list_tasks

        assert self._denied(self._call(list_tasks, VISITOR))

    def test_member_allowed_list_tasks(self):
        from src.mcp.tools.tasks import list_tasks

        assert not self._denied(self._call(list_tasks, MEMBER))

    def test_visitor_allowed_collections(self):
        """visitor 的 chat/database 是开的 → 跨页共享的库类工具不该被拦。

        这里直接调**闸门执行体**（装饰器内部那一步），而不是整个工具函数：
        后者会去连 Qdrant，测试环境没有 → 失败反映的是"依赖缺失"而不是闸门判定，
        那样就测不出真正想钉的行为。
        """
        from src.mcp.common import _require_module_allowed

        tok = _set_actor(VISITOR)
        try:
            assert _require_module_allowed("chat") is None
            assert _require_module_allowed("chat", "database") is None
        finally:
            _current.reset(tok)

    def test_owner_bypasses(self):
        from src.mcp.tools.tasks import list_tasks

        assert not self._denied(self._call(list_tasks, OWNER))

    def test_gate_failure_is_fail_closed(self, monkeypatch):
        """判定炸了 → 拒绝（不回落放行）。"""
        import src.mcp.common as c

        monkeypatch.setattr(c, "get_role", None, raising=False)
        from src.mcp.tools.tasks import list_tasks

        # roles.get_role 抛错 → _require_module_allowed 的 except 分支拒绝
        import src.identity.roles as roles_mod

        def _boom(rid):
            raise RuntimeError("roles down")

        monkeypatch.setattr(roles_mod, "get_role", _boom)
        assert self._denied(self._call(list_tasks, VISITOR))


# ── 覆盖完整性：每个 MCP 工具都该有闸门 ────────────────────────


class TestMcpGateCoverage:
    """**漏一个就是一个静默的模块绕过** —— 用 AST 扫描钉住全部工具。

    注意排除非工具助手（``_await_mcp`` 等下划线函数）：它们不是注册工具，
    装饰它们反而会破坏内部调用。
    """

    def test_all_registered_tools_are_gated(self):
        import ast
        import pathlib
        import re

        server = pathlib.Path("src/mcp/server.py").read_text()
        registered: set[str] = set()
        for m in re.finditer(r"for _t in \(([^)]+)\)", server, re.S):
            for n in m.group(1).split(","):
                n = n.strip()
                if n and n.isidentifier():
                    registered.add(n)
        registered |= {"list_files", "fm_list_files"}
        for m in re.finditer(r"mcp\.add_tool\((\w+)\)", server):
            registered.add(m.group(1))

        def gated(fn_node) -> bool:
            return any(
                "_gated(" in (ast.unparse(d))
                for d in fn_node.decorator_list
            )

        missing: list[str] = []
        for p in sorted(pathlib.Path("src/mcp/tools").glob("*.py")):
            for node in ast.parse(p.read_text()).body:
                if not isinstance(node, ast.AsyncFunctionDef):
                    continue
                if node.name.startswith("_"):
                    continue   # 内部助手不是工具
                if node.name in registered and not gated(node):
                    missing.append(f"{p.name}:{node.name}")
        assert not missing, f"这些 MCP 工具没有模块闸门: {missing}"

    def test_no_helper_is_gated(self):
        """下划线助手不该被装饰（会破坏内部调用）。"""
        import ast
        import pathlib

        bad: list[str] = []
        for p in sorted(pathlib.Path("src/mcp/tools").glob("*.py")):
            for node in ast.parse(p.read_text()).body:
                if isinstance(node, ast.AsyncFunctionDef) and node.name.startswith("_"):
                    if any(
                        "_gated(" in ast.unparse(d)
                        for d in node.decorator_list
                    ):
                        bad.append(f"{p.name}:{node.name}")
        assert not bad, f"内部助手被误装饰: {bad}"
