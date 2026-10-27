"""Current operator (请求级身份).

单用户安装里一切请求都解析成 ``Actor("local", "local", "owner")``——本机即主人。
多设备/多角色场景下由配对门卫在请求入口把钥匙身份写进来（见
``src/pairing/middleware.py``）。

**传播边界（2026-09-16 实测，改动前必读）**：

- ✅ ``async def`` 路由：同一 asyncio 任务，直接可见。
- ✅ ``def``（同步）路由：FastAPI 丢进 anyio 线程池，anyio 会 ``copy_context()``，
  因此可见（由 ``tests/test_actor_propagation.py`` 钉住）。
- ✅ WebSocket 路由：同一任务，可见。
- ❌ **``threading.Thread`` 手起的线程：不可见**——``contextvars`` 不跨线程，
  子线程读到的是默认值（= 本机 owner）。**这是权限提升路径**：若线程内做授权判定，
  会以 owner 身份静默放行。
  规则：**授权必须在请求期判定完**；线程内需要身份时显式把 ``Actor`` 传进去
  （闭包/参数），不依赖 ``get_actor()``。

``get_actor()`` 的默认值刻意保持 owner：它同时是"无请求上下文的内部操作"（启动期、
后台任务）的语义。**不要依赖默认值来授权用户请求**——用户请求必经门卫设置身份。
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass

#: 内置角色 id。owner = 本机，全权且不可被吊销（见 RBAC 设计 §3.2）。
ROLE_OWNER = "owner"
#: 无请求上下文（启动期 / 后台任务）的 actor id。
ACTOR_SYSTEM = "system"
#: 本机 actor id。
ACTOR_LOCAL = "local"


@dataclass(frozen=True)
class Actor:
    """一次请求的操作者。

    - ``id``：``"local"``（本机）/ ``"system"``（内部）或配对钥匙 id。
    - ``kind``：``"local"`` / ``"system"`` / ``"key"`` / ``"anonymous"``。
    - ``role_id``：角色定义表里的 id；决定模块与能力（见 ``src/identity/roles.py``）。
    """

    id: str = ACTOR_LOCAL
    kind: str = "local"
    role_id: str = ROLE_OWNER


_current: ContextVar[Actor] = ContextVar("sinkduce_actor", default=Actor())


def get_actor() -> Actor:
    return _current.get()


def set_actor(actor: Actor) -> Token[Actor]:
    """把当前请求的操作者写进上下文。返回 token，调用方负责 ``reset_actor``。"""
    return _current.set(actor)


def reset_actor(token: Token[Actor]) -> None:
    _current.reset(token)


def system_actor() -> Actor:
    """无请求上下文的内部操作（启动期、后台任务）。

    语义同 owner，但它**不是**"免检通道"：凡由用户请求触发的后台动作，授权必须
    已在请求期完成（见模块 docstring 的传播边界）。
    """
    return Actor(id=ACTOR_SYSTEM, kind="system", role_id=ROLE_OWNER)


# 向后兼容：早期接缝测试直接引用了下划线名。
_set_actor = set_actor

def _reset_actor_token(token) -> None:
    """按 LIFO 复位（contextvars 要求 Token 逆序 reset）。

    **为什么单独暴露**：测试里有个隐蔽的坑——如果按**正序** reset 多个 Token，
    后设的身份会残留在 context 里，泄漏到后续测试（实测让 96 个无关测试变红）。
    统一走这个助手，避免每个 fixture 各写一遍。
    """
    _current.reset(token)
