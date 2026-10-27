"""CONN-6: 全局配对门卫 — /api 与 /mcp 强制（HTTP + WebSocket 升级统一覆盖）。

规则（backlog CONN-6 / 威胁模型文档）：
- 默认拒绝：本机来源 或 持有效钥匙放行；其余 HTTP 401 / WebSocket 1008；
- 本机 = 回环客户端地址（桌面壳）或请求落在本机入口端口（容器，宿主机只映射回环）；
- 例外：本机入口端口上的领钥匙端点（GET /api/pairing/local-key）——只有物理本地
  通道能调用，因此可安全静默发放主人钥匙；
- 豁免面：SPA 静态、/health。
- **/mcp 只认本机**（2026-09-18 重写，见下方 _is_mcp_path）：有效钥匙也不行。
  非本机对 /mcp 的请求一律 **404**（当作路径不存在），见 §「为什么 404 而不是 401」。

来源判定的演进：早期用「来源 IP ∈ 回环/网关/本机网卡」识别本机，实测在
VPN/代理/Docker NAT 下会被改写而失效（见威胁模型 §4）。现已改为「回环地址 +
本机入口端口」两个客户端无法伪造的信号。

── /mcp 收敛回环（不变式，动之前先读）─────────────────────────
**MCP 只经回环可达，任何情况下不暴露到网络 —— 包括将来的云端中继。**

这条不变式承重两个结论：
1. 回环 ⇒ `is_local` ⇒ actor 恒为 owner ⇒ **不存在权限更低的一方** ⇒
   不需要权限交集/可达盒子清单/逐调用者授权（"共享单台盒子"因此是安全的）；
2. 唯一身份是 owner ⇒ 有状态会话的**身份冻结 / 会话劫持**失去危害前提
   （冻结的是 owner、能劫到的也是 owner，而 owner 本来就是本机能拿到的东西）。

**中继落地会同时打破这两条。** 所以将来放开 MCP 之前，必须先在此文件之外补上
「调用者权限判定」，再考虑放行 —— 顺序反了就是静默提权。

── 为什么 404 而不是 401 ───────────────────────────────────
401 的语义是「带上钥匙就能进」，而这里**任何钥匙都进不去**（钥匙不是这条闸门的
凭据）。返回 404 让网络侧看见的和「这台机器上没有 /mcp」完全一致 —— 局域网扫描
扫不出这个面，也就不存在「有个要钥匙的 MCP，去搞把钥匙」的引导。
"""

from __future__ import annotations

import json
from urllib.parse import parse_qs

from . import service

# /api 需要门禁（否则局域网内无钥匙即可读写整个知识库）。
_GATED_PREFIXES = ("/api", "/mcp")

# /mcp 额外一条更强的规则：**只认本机**（见模块 docstring「/mcp 收敛回环」）。
_MCP_PREFIX = "/mcp"


def _is_gated_path(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in _GATED_PREFIXES)


def _is_mcp_path(path: str) -> bool:
    return path == _MCP_PREFIX or path.startswith(_MCP_PREFIX + "/")


def should_allow(*, path: str, is_local: bool, token_valid: bool, local_key: bool = False) -> bool:
    """门卫决策纯函数。默认拒绝：本机、持有效钥匙、或本机端口领钥匙端点才放行。

    **/mcp 是特例：只有 is_local 能过，token 不参与**（不变式，见模块 docstring）。
    """
    if not _is_gated_path(path):
        return True  # SPA / 静态 / /health 不设防
    if _is_mcp_path(path):
        return is_local
    return is_local or token_valid or local_key


def _bearer_from_headers(headers) -> str | None:
    auth = None
    pairing = None
    for k, v in headers or []:
        name = k.decode("latin-1").lower()
        if name == "authorization":
            val = v.decode("latin-1")
            if val.lower().startswith("bearer "):
                auth = val[7:].strip() or None
        elif name == "x-pairing-token":
            pairing = v.decode("latin-1").strip() or None
    return pairing or auth


def _token_from_query(query_string: bytes) -> str | None:
    qs = parse_qs(query_string.decode("latin-1"))
    vals = qs.get("token")
    return vals[0] if vals and vals[0] else None


def _extract_token(scope) -> str | None:
    q = _token_from_query(scope.get("query_string") or b"")
    if q:
        return q
    return _bearer_from_headers(scope.get("headers") or [])


def _client_id_from_headers(headers) -> str | None:
    from src.events.context import CLIENT_ID_HEADER

    for k, v in headers or []:
        if k.decode("latin-1").lower() == CLIENT_ID_HEADER:
            val = v.decode("latin-1").strip()
            if val:
                return val
    return None


def _client_id_from_query(query_string: bytes) -> str | None:
    from src.events.context import CLIENT_ID_QUERY

    qs = parse_qs(query_string.decode("latin-1"))
    vals = qs.get(CLIENT_ID_QUERY)
    return vals[0] if vals and vals[0] else None


def _extract_client_id(scope) -> str | None:
    """Stable per-install frontend id, used only to skip a client's own echoes.

    Header first (normal requests), query second (EventSource cannot set
    headers). This is client-asserted and is not a security boundary — a wrong
    value can only cause a client to miss its own echo, degrading to one
    redundant refetch. The gate below is what actually authorizes the request.
    """
    return _client_id_from_headers(scope.get("headers") or []) or _client_id_from_query(
        scope.get("query_string") or b""
    )


def _json_response(status: int, payload: dict):
    body = json.dumps(payload).encode("utf-8")

    async def send_it(scope, receive, send):
        await send({
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        })
        await send({"type": "http.response.body", "body": body})

    return send_it


def _not_found_response():
    """假 404：形态与 SPA 兜底那条「Not Found」一致。

    故意做成「这台机器上没有这个路径」的样子，而不是「有，但要钥匙」——
    见模块 docstring「为什么 404 而不是 401」。
    """
    return _json_response(404, {"detail": "Not Found"})


class PairingGateMiddleware:
    """纯 ASGI 中间件：同时覆盖 HTTP 与 WebSocket 升级请求。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        if not _is_gated_path(path):
            return await self.app(scope, receive, send)

        token = _extract_token(scope)
        rec = service.authenticate(token)

        # 领钥匙端点：只有落在本机独占端口、且 Host/Origin 指向本机的请求才能调用。
        # 端口对了但 Host/Origin 不像本机浏览器 → 403（区别于未摸到本机端口的 401）。
        local_key = service.LOCAL_KEY_NONE
        if path == service.LOCAL_KEY_PATH:
            local_key = service.local_key_access(scope)
            if local_key == service.LOCAL_KEY_DENY:
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                    return
                resp = _json_response(
                    403, {"detail": {"code": "forbidden", "message": "not a local request"}}
                )
                await resp(scope, receive, send)
                return

        is_local = service.is_local_scope(scope)
        from src.edition import has_permission_management
        if not is_local and rec is not None and not has_permission_management():
            # A restricted core profile preserves the meaning of historical keys.
            if rec.get("role", "member") != "member" or rec.get("name") == service.LOCAL_KEY_NAME:
                rec = None
        allowed = should_allow(
            path=path,
            is_local=is_local,
            token_valid=rec is not None,
            local_key=local_key == service.LOCAL_KEY_GRANT,
        )
        if allowed:
            # 角色预留（CONN-6）：调用者身份挂进 scope state，路由层经 request.state.pairing 可读。
            # 将来 RBAC = 在此按 rec["role"] 与路径规则判定 401/403，业务端点零改动。
            if rec is not None:
                state = scope.setdefault("state", {})
                state["pairing"] = {"key_id": rec.get("id"), "role": rec.get("role", "owner")}
            # 事件溯源（多端同步）：把调用者身份绑进请求上下文，emit_event 据此填 origin。
            # 后台线程没有请求上下文，origin 留空 = 该事件发给所有客户端（正确语义）。
            from src.events import context as _event_context

            _token_ctx = _event_context.set_client(
                _extract_client_id(scope),
                rec.get("id") if rec is not None else None,
            )
            # RBAC：把操作者写进请求上下文，业务层 authorize()/get_actor() 据此判定。
            # 这是**唯一**设置身份的入口——默认值仍是本机 owner（无请求上下文的内部操作）。
            # 传播边界见 src/identity/actor.py 的模块 docstring（线程不传播！）。
            from src.identity import Actor, reset_actor, set_actor

            if is_local:
                actor = Actor(id="local", kind="local", role_id="owner")
            elif rec is not None:
                actor = Actor(
                    id=str(rec.get("id") or "key"),
                    kind="key",
                    role_id=rec.get("role") or "owner",
                )
            else:
                # 理论上到不了（allowed 由 local / token / local_key 三者之一成立）。
                # 保守起见给最小权限，绝不给 owner。
                actor = Actor(id="anonymous", kind="anonymous", role_id="visitor")
            _actor_ctx = set_actor(actor)
            try:
                return await self.app(scope, receive, send)
            finally:
                reset_actor(_actor_ctx)
                _event_context.reset_client(_token_ctx)

        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        # /mcp 被拒 = 非本机（钥匙在这条闸门上不是凭据）→ 装作路径不存在。
        # 必须在 401 之前判：这里给 401 等于告诉局域网「有个要钥匙的 MCP」。
        if _is_mcp_path(path):
            resp = _not_found_response()
            await resp(scope, receive, send)
            return
        resp = _json_response(
            401, {"detail": {"code": "pairing_required", "message": "pairing token required"}}
        )
        await resp(scope, receive, send)


# ── 启动期不变式：受保护路由必须挂在门卫覆盖的前缀下 ────────────
# PairingGateMiddleware 只拦 /api 与 /mcp（其余=SPA/静态/health 放行）。
# 因此「某个 router 挂到了 /api 之外的前缀」会让它上面的身份依赖**永远
# 拿不到 actor 而静默放行**——一次挂载重构就能把闸门整体拆掉。与其依赖
# 逐个 deps 的 actor-None 兜底（那正是给测试留的口子），不如在启动时把
# 整张路由表验一遍：违例直接拒绝启动，把静默洞变成一行报错。
# 主 app 与远程模式瘦壳共用（从 src/main.py 原样搬迁，行为不变）。
_ALLOWED_UNGATED_PREFIXES = ("/api", "/mcp")
_ALLOWED_UNGATED_EXACT = {"/health", "/{full_path:path}"}


def assert_gate_route_coverage(app) -> None:
    from starlette.routing import Mount

    offenders: list[str] = []
    for route in app.router.routes:
        if isinstance(route, Mount):
            if route.path not in ("/assets",):
                offenders.append(f"mount {route.path}")
            continue
        path = getattr(route, "path", None)
        if not isinstance(path, str):
            continue
        if path in _ALLOWED_UNGATED_EXACT:
            continue
        if path.startswith(_ALLOWED_UNGATED_PREFIXES):
            continue
        offenders.append(path)
    if offenders:
        raise RuntimeError(
            "Routes outside the pairing gate detected: "
            + ", ".join(sorted(offenders))
            + " — every protected route must be mounted under /api or /mcp "
            "(see PairingGateMiddleware). Refusing to start."
        )
