"""「当前盒子」——**会话级**状态（每个 MCP 会话各自一份）。

模块名 ``state``（不是 ``session``）：它存的是**当前盒子**这个状态，
而 ``session`` 会与 MCP SDK 的会话概念混淆。

**为什么必须是会话级**（设计文档 §5）：有状态 MCP 会话天然适合放这种状态。
若放在进程级全局，两个 Agent 同时连 Hub 时会互相把对方的「当前盒子」改掉。

**为什么可以安全地按会话隔离**：``/mcp`` 只经回环可达（见
``src/pairing/middleware.py``），所以每个会话的调用者都是本机 owner。状态隔离
在这里是**正确性问题**（别串味），而不是权限问题。

实现用 ``WeakKeyDictionary`` 以 ``ServerSession`` 为键：会话结束时 SDK 释放该对象，
条目自动消失，不需要我们自己管生命周期（也不会有内存泄漏）。
"""

from __future__ import annotations

import logging
import weakref
from typing import Any

logger = logging.getLogger("hub.state")

#: 本机（默认值）。空串 = 不切换，工具按本机语义执行。
LOCAL_BOX = ""

_states: "weakref.WeakKeyDictionary[Any, str]" = weakref.WeakKeyDictionary()


def _key(session: Any) -> Any:
    return session


def current_box_id(session: Any) -> str:
    """当前盒子 id；本机返回空串。会话未知时按本机（默认语义）。"""
    if session is None:
        return LOCAL_BOX
    try:
        return _states.get(_key(session), LOCAL_BOX)
    except TypeError:
        # 有些对象不可弱引用（测试替身/未来 SDK 改动）→ 退化为本机，绝不抛。
        return LOCAL_BOX


def set_current_box_id(session: Any, box_id: str) -> None:
    if session is None:
        return
    try:
        _states[_key(session)] = (box_id or "").strip()
    except TypeError:
        logger.warning("cannot store box state on this session type; switch ignored")


def clear(session: Any) -> None:
    if session is None:
        return
    try:
        _states.pop(_key(session), None)
    except TypeError:
        pass
