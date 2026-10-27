"""阶段 0 spike：门卫设置的操作者能否到达各类型 handler？

整个 RBAC 建立在「每个请求都能读到自己的身份」上。``contextvars`` 有几处**已知**不传播
的地方，必须先钉死，否则后面每个埋点都可能白写：

| 路径 | 机制 | 预期 |
|---|---|---|
| ``async def`` 路由 | 同一 asyncio 任务 | ✅ 可见 |
| ``def``（同步）路由 | FastAPI → anyio 线程池（``copy_context``） | ✅ 可见（**本测试的核心验证点**） |
| WebSocket 路由 | 同一任务 | ✅ 可见 |
| 裸 ASGI handler（``add_route`` 注册，同 ``/mcp`` 的方式） | 同一 ASGI 调用链 | ✅ 可见 |
| ``threading.Thread`` | **不复制 context** | ❌ 读到默认值（= 本机 owner）→ 权限提升路径 |

最后一条是**反例**：它必须失败（读到默认值），用来把已知危险行为钉在测试里——将来谁
依赖线程内 ``get_actor()`` 做授权，这里的文档与断言会提示他。正确做法是显式传 actor。

跑法：``pytest tests/test_actor_propagation.py``
"""

from __future__ import annotations

import threading

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.routing import Route

from src.identity import Actor, get_actor
from src.pairing.middleware import PairingGateMiddleware


class _RawAsgiProbe:
    """裸 ASGI handler，模拟 ``main.py`` 用 ``add_route`` 注册的子应用。

    注册方式与 ``/mcp`` 完全相同（``Route(path, asgi_instance, methods=...)``），
    但**路径不是 /mcp** —— ``/mcp`` 从 2026-09-18 起只认回环（见
    ``tests/test_mcp_loopback_only.py``），拿它来验「钥匙身份是否传播」会被闸门先拦掉，
    测的就不是传播了。这里测的是**注册机制**，路径取一条普通受门禁路径即可。
    """

    def __init__(self, sink: dict):
        self.sink = sink

    async def __call__(self, scope, receive, send):
        self.sink["raw"] = get_actor()
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": b'{"ok":true}'})


def _make_app(sink: dict) -> FastAPI:
    """带真实门卫的最小 app：四条探测路径，全部落在受门禁前缀下。"""
    app = FastAPI()
    app.add_middleware(PairingGateMiddleware)

    @app.get("/api/probe/async")
    async def probe_async():
        sink["async"] = get_actor()
        return {"ok": True}

    # 关键：同步路由 → FastAPI 丢进 anyio 线程池
    @app.get("/api/probe/sync")
    def probe_sync():
        sink["sync"] = get_actor()
        return {"ok": True}

    @app.websocket("/api/probe/ws")
    async def probe_ws(ws: WebSocket):
        sink["ws"] = get_actor()
        await ws.accept()
        await ws.close()

    app.router.routes.append(Route("/api/probe/raw", _RawAsgiProbe(sink), methods=["GET", "POST"]))
    return app


@pytest.fixture
def local_gate(monkeypatch):
    """模拟「本机来源」：门卫放行且身份=本机 owner。"""
    monkeypatch.setattr("src.pairing.service.is_local_scope", lambda scope: True)


@pytest.fixture
def key_gate(monkeypatch):
    """模拟「持钥匙的远端设备」：门卫放行且身份=该钥匙的角色。"""
    monkeypatch.setattr("src.pairing.service.is_local_scope", lambda scope: False)
    monkeypatch.setattr(
        "src.pairing.service.authenticate",
        lambda token: {"id": "key-abc123", "role": "member"},
    )


# ── 本机（owner）────────────────────────────────────────────────


def test_async_route_sees_local_actor(local_gate):
    sink: dict = {}
    client = TestClient(_make_app(sink))
    assert client.get("/api/probe/async").status_code == 200
    actor = sink["async"]
    assert actor.id == "local"
    assert actor.kind == "local"
    assert actor.role_id == "owner"


def test_sync_route_sees_local_actor(local_gate):
    """**核心验证点**：同步路由跑在 anyio 线程池里，anyio 会 copy_context。"""
    sink: dict = {}
    client = TestClient(_make_app(sink))
    assert client.get("/api/probe/sync").status_code == 200
    actor = sink["sync"]
    assert actor.id == "local", "同步路由读不到身份 → 后续必须改用显式传参！"
    assert actor.role_id == "owner"


def test_websocket_route_sees_local_actor(local_gate):
    sink: dict = {}
    client = TestClient(_make_app(sink))
    with client.websocket_connect("/api/probe/ws"):
        pass
    assert sink["ws"].id == "local"


def test_bare_asgi_subapp_sees_local_actor(local_gate):
    """裸 ASGI 子应用（与 /mcp 同一种注册方式）也要能读到身份。"""
    sink: dict = {}
    client = TestClient(_make_app(sink))
    assert client.post("/api/probe/raw", json={}).status_code == 200
    assert sink["raw"].id == "local"


# ── 持钥匙（角色随钥匙走）────────────────────────────────────────


def test_async_route_sees_key_actor(key_gate):
    sink: dict = {}
    client = TestClient(_make_app(sink))
    assert client.get("/api/probe/async").status_code == 200
    actor = sink["async"]
    assert actor.id == "key-abc123"
    assert actor.kind == "key"
    assert actor.role_id == "member"


def test_sync_route_sees_key_actor(key_gate):
    sink: dict = {}
    client = TestClient(_make_app(sink))
    assert client.get("/api/probe/sync").status_code == 200
    actor = sink["sync"]
    assert actor.id == "key-abc123"
    assert actor.role_id == "member"


def test_bare_asgi_subapp_sees_key_actor(key_gate):
    sink: dict = {}
    client = TestClient(_make_app(sink))
    assert client.post("/api/probe/raw", json={}).status_code == 200
    assert sink["raw"].role_id == "member"


# ── 隔离性：身份不跨请求串味 ─────────────────────────────────────


def test_actor_does_not_leak_between_requests(local_gate, monkeypatch):
    """一个请求结束后，身份必须复位——否则下一个请求会继承上一个的身份。"""
    sink: dict = {}
    client = TestClient(_make_app(sink))
    assert client.get("/api/probe/async").status_code == 200

    # 请求已结束，调用方上下文里应回到默认（本机 owner）
    assert get_actor().kind == "local"

    # 换成钥匙身份再发一次：不复用上一次的结果
    monkeypatch.setattr("src.pairing.service.is_local_scope", lambda scope: False)
    monkeypatch.setattr(
        "src.pairing.service.authenticate",
        lambda token: {"id": "key-xyz", "role": "visitor"},
    )
    assert client.get("/api/probe/sync").status_code == 200
    assert sink["sync"].id == "key-xyz"
    assert sink["sync"].role_id == "visitor"


# ── 反例：线程不传播（把危险行为钉住）────────────────────────────


def test_thread_does_not_inherit_actor(local_gate):
    """**反例（预期失败）**：``threading.Thread`` 不复制 context。

    这意味着线程内 ``get_actor()`` 拿到的是默认值 ``Actor("local","local","owner")``
    ——**owner**。所以：

    * **绝不在线程内做授权判定**（会以 owner 静默放行）；
    * 线程需要身份时**显式传 Actor**（闭包/参数）。

    这条断言是文档：若将来有人改了 ``get_actor`` 的默认值或让它跨线程，这里会红，
    提醒他重新审视所有后台线程的授权假设。
    """
    seen: dict = {}
    sink: dict = {}
    client = TestClient(_make_app(sink))
    assert client.get("/api/probe/async").status_code == 200  # 让中间件设置过身份

    def _in_thread():
        seen["actor"] = get_actor()

    t = threading.Thread(target=_in_thread)
    t.start()
    t.join()

    # 子线程读到默认值（不是本机请求的 actor 语义，而是 ContextVar 的 default）
    assert seen["actor"] == Actor()
    assert seen["actor"].role_id == "owner", (
        "线程内默认角色是 owner —— 这就是为什么授权必须在请求期完成"
    )


def test_explicit_actor_can_be_passed_to_thread(local_gate):
    """正解示范：把 Actor 显式传给线程，而不是依赖 get_actor()。"""
    captured = Actor(id="key-abc123", kind="key", role_id="member")
    seen: dict = {}

    def _work(actor: Actor):
        seen["actor"] = actor

    t = threading.Thread(target=_work, args=(captured,))
    t.start()
    t.join()

    assert seen["actor"].role_id == "member"
