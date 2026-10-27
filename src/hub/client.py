"""Hub 出站 HTTP 客户端：本机盒子 → 远端盒子的 **REST** 调用。

**为什么不走远端 MCP**（设计文档 §2.2）：REST 端点已存在、已过测试，鉴权模型与
MCP 完全相同（同一个门卫、同一把钥匙），还省掉「MCP 套 MCP」的嵌套会话与两层超时
叠加。分界是：**MCP 只负责「Agent → Hub」这一跳**。

**与浏览器请求的根本区别**：浏览器那套（``frontend/src/api/http.ts``）带
「来源感知」——当前来源是远端就自动拼那个 origin、还带热票续期逻辑。Hub 侧的调用
**目标地址是显式的**（每条盒子记录里的 ``addr``），所以这里不碰也不该碰前端那套。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("hub.client")

# 单次出站调用的超时。比浏览器默认宽容一点（Hub 可能在跨设备/慢链路上），
# 但不设成 None——一个卡死的远端点绝不能把 MCP 工具调用永远挂住。
DEFAULT_TIMEOUT = 20.0
# 探针（预鉴权）要快：用户/模型都在等这个答案，宁可快速失败也别等 20 秒。
PROBE_TIMEOUT = 6.0


class HubCallError(Exception):
    """出站调用失败（网络层或非 2xx）。

    ``status`` 为 None 表示根本没连上（离线/DNS/超时）；有值表示对方答复了但不是 2xx。
    这个区分对模型很重要：前者「盒子连不上」，后者「这台盒子拒绝了我」。
    """

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        code: str = "",
        detail: str = "",
        extra: dict | None = None,
    ):
        self.status = status
        self.code = code
        # 远端答复里的干净错误文本（不带 "http://x.x.x.x returned 404: " 前缀）。
        # 批量转发器逐条聚合 errors 时用它——每条都带一遍地址前缀是噪音。
        self.detail = detail or message
        # detail dict 的附加字段（suggested_name 等）——错误信封保真用
        self.extra = extra or {}
        super().__init__(message)


def _url(addr: str, path: str) -> str:
    base = (addr or "").strip().rstrip("/")
    if not path.startswith("/"):
        path = "/" + path
    return f"{base}{path}"


def _headers(token: str | None) -> dict[str, str]:
    h = {"Accept": "application/json"}
    tok = (token or "").strip()
    if tok:
        # 与前端同一套头名（门卫读 X-Pairing-Token 或 Authorization: Bearer）。
        h["X-Pairing-Token"] = tok
    return h


def _extract_error(resp) -> tuple[str, str, dict]:
    """从错误响应里取 (message, code, extra)。

    ``extra`` 是 detail dict 里**除 message/code 外的全部字段**（如 409
    name_conflict 的 ``suggested_name``）——远端错误信封的这些字段是调用方
    （Agent）重试的依据，丢了就是「本地有、转发没有」的信封漂移（批次 5
    审查 #5）。宽容解析——对方不一定是我们自己的后端。
    """
    try:
        body = resp.json()
    except Exception:
        return (resp.text or "")[:200], "", {}
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, dict):
        extra = {k: v for k, v in detail.items() if k not in ("message", "code")}
        return str(detail.get("message") or detail.get("code") or ""), str(detail.get("code") or ""), extra
    if isinstance(detail, str):
        return detail, "", {}
    return (str(body)[:200] if body else ""), "", {}


async def call(
    addr: str,
    path: str,
    token: str | None = None,
    *,
    method: str = "GET",
    json_body: Any = None,
    params: dict[str, Any] | None = None,
    files: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> Any:
    """向某台盒子发一个 REST 调用，返回 JSON。

    ``files`` / ``data``（批次 5）：multipart 上传——Hub 把**本机 staging 里的
    字节**直传远端（httpx 的 files= 语义：``{"file": (filename, bytes)}``）。
    上传的传输时间与体积成正比，调用方必须显式给大 timeout。

    失败一律抛 :class:`HubCallError`——**调用方必须处理**，不要静默吞掉：
    「盒子 X 连不上」是可操作信息（设计文档 §10：跨盒子检索**绝不静默省略离线盒子**，
    那会给出看起来完整、实际缺一块的答案，是最危险的失败模式）。
    """
    import httpx

    url = _url(addr, path)
    try:
        async with httpx.AsyncClient(timeout=timeout) as http:
            resp = await http.request(
                method,
                url,
                headers=_headers(token),
                json=None if (files or data) else json_body,
                files=files,
                data=data,
                # 去掉 None（httpx 会把 None 序列化成 "None"）
                params={k: v for k, v in (params or {}).items() if v is not None} or None,
            )
    except httpx.TimeoutException as exc:
        raise HubCallError(f"timed out after {timeout:g}s talking to {addr}", code="timeout") from exc
    except httpx.HTTPError as exc:
        # ConnectError / DNS / TLS… 统一成「连不上」
        raise HubCallError(f"cannot reach {addr}: {type(exc).__name__}", code="unreachable") from exc

    if resp.status_code == 401:
        msg, code, extra = _extract_error(resp)
        raise HubCallError(
            f"{addr} rejected the key (401){': ' + msg if msg else ''}",
            status=401,
            code=code or "unauthorized",
            detail=msg,
            extra=extra,
        )
    if resp.status_code == 403:
        msg, code, extra = _extract_error(resp)
        raise HubCallError(
            f"{addr} denied this request (403){': ' + msg if msg else ''}",
            status=403,
            code=code or "forbidden",
            detail=msg,
            extra=extra,
        )
    if resp.status_code >= 400:
        msg, code, extra = _extract_error(resp)
        raise HubCallError(
            f"{addr} returned {resp.status_code}{': ' + msg if msg else ''}",
            status=resp.status_code,
            code=code,
            detail=msg or str(resp.status_code),
            extra=extra,
        )
    try:
        return resp.json()
    except ValueError:
        return {"raw": resp.text}


async def probe(addr: str, token: str | None) -> Any:
    """预鉴权探针：调目标盒子的 ``/api/identity/me``（设计文档 §9）。

    **为什么选这个端点**：它被设计成**宽容端点**（任何已认证请求都返回），
    所以一次调用就能同时拿到四件事：连不连得上、钥匙是否仍有效、实际角色、
    哪些能力不可用。用别端点会掺进业务语义（例如「库不存在」看起来像鉴权失败）。

    **明确不做三件事**（设计文档 §9）：不当放行依据、不做工具发现、不做清单过滤。
    """
    return await call(addr, "/api/identity/me", token, timeout=PROBE_TIMEOUT)
