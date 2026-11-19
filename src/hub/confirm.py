"""Require interactive approval before dispatching irreversible MCP operations."""

from __future__ import annotations

import functools
import logging
import re
from typing import Any, Callable

logger = logging.getLogger("hub.confirm")

#: ★ **需要人工确认的工具**（权威登记表）→ 给人看的一句话（``{target}`` 用参数填）。
#:
#: 判定标准是**不可逆**（用户内容删掉就没了），不是名字长什么样。
#: 新增这类工具时**必须**在这里登记——测试会用 :data:`_DESTRUCTION_HINT` 扫全量工具名，
#: **匹配到却没登记就失败**。这样即使新工具叫 ``remove_x`` / ``purge_y`` 也拦得住。
DESTRUCTIVE_TOOLS: dict[str, str] = {
    "delete_collection": "Delete the collection “{target}” and everything in it",
    "delete_document": "Delete a document from “{target}”",
    "delete_hot_words_library": "Delete the hot-words library “{target}”",
    "delete_meeting": "Delete the meeting “{target}”",
    "delete_note": "Delete the note “{target}”",
    "delete_todo": "Delete a to-do in “{target}”",
}

#: 名字像破坏性、但**已经看过并有意不闸**的工具 → 理由。
#: 留着它让覆盖面检查**可判定**：命中词根的工具要么进 ``DESTRUCTIVE_TOOLS``（闸上）、
#: 要么进这里（说明为什么不闸）；两个都不在 = 测试失败，逼下一个人做决定。
REVIEWED_UNGLATED: dict[str, str] = {
    "clear_completed_tasks": "只清已完成/失败的**任务记录**，不删用户内容",
}

#: 名字暗示「这东西会毁数据」的词根。**故意宽**：宁可多要求一次解释
#: （进 REVIEWED_UNGLATED），也不要漏掉一个真的不可逆操作。
_DESTRUCTION_HINT = re.compile(
    r"(delete|destroy|remove|purge|wipe|drop|erase|clear|reset|overwrite)",
    re.IGNORECASE,
)

#: 向后兼容的别名（外部只读引用）。
CONFIRM_SUMMARIES = DESTRUCTIVE_TOOLS

#: 绝不外发的参数名（**凭据**与内部对象）。
_REDACTED_KEYS = frozenset(
    {
        "ctx", "token", "access_token", "pairing_token", "api_key", "apikey",
        "authorization", "secret", "password",
        "content", "content_b64", "body", "text", "base64",
    }
)


def _is_sensitive(key: str) -> bool:
    k = (key or "").casefold()
    if k in _REDACTED_KEYS:
        return True
    return any(s in k for s in ("token", "secret", "password", "api_key", "apikey", "key"))


def _target(args: dict[str, Any]) -> str:
    """从工具参数里挑一个「这是在对谁动手」的可读标识。

    顺序有讲究：库名/会议名/笔记名这类**人能认出来**的字段优先，内部 id 垫底 ——
    提示语里要出现「RAG 笔记」而不是一串 hex。
    """
    for key in (
        "collection_name", "name", "title", "meeting_title",
        "collection", "meeting_id", "note_id", "todo_id", "library_id",
        "file_id", "source",
    ):
        value = args.get(key)
        if value:
            return str(value)
    return "(unspecified)"


def build_request(tool: str, args: dict[str, Any]) -> dict[str, Any]:
    """构造确认请求（纯函数，便于测试）。**不含任何凭据**。"""
    from src.hub.box_label import current_box_label

    template = DESTRUCTIVE_TOOLS.get(tool) or f"Run {tool}"
    safe: dict[str, Any] = {}
    for k, v in args.items():
        if v in (None, "") or _is_sensitive(k):
            continue
        if isinstance(v, str) and len(v) > 200:
            v = v[:200] + "…"
        safe[k] = v
    return {
        "tool": tool,
        "summary": template.format(target=_target(args)),
        # 写类已接跨盒子转发（2026-09-18 批次 2）——远端模式下这里是**目标盒子的
        # 名字**，提示语据此显示 "On box: X"。本机（默认）为空串。
        "box": current_box_label(),
        "args": safe,
    }


def _current_session() -> Any:
    """当前 MCP 会话对象（拿不到就 None）。"""
    try:
        from src.mcp.server import mcp

        ctx = mcp.get_context()
        return getattr(ctx, "session", None)
    except Exception:
        return None


def client_supports_elicit(session: Any = None) -> bool:
    """客户端是否声明了 elicitation 能力。

    必须在**会话上**问（``check_client_capability``），不能靠猜客户端名字 ——
    能力是握手的标准字段，按名字判断会在客户端升级/换实现时静默出错。
    """
    if session is None:
        session = _current_session()
    if session is None:
        return False
    try:
        from mcp import types

        return bool(
            session.check_client_capability(
                types.ClientCapabilities(elicitation=types.ElicitationCapability())
            )
        )
    except Exception:
        logger.debug("MCP confirmation capability unavailable", exc_info=True)
        return False


async def request_confirmation(tool: str, args: dict[str, Any]) -> dict[str, Any] | None:
    """Return a refusal unless this client obtained an affirmative response."""
    from src.mcp.common import err

    if not client_supports_elicit():
        return err(
            "This operation requires interactive confirmation. Open SinkDuce and complete the deletion there.",
            code="confirmation_required",
        )
    payload = build_request(tool, args)
    from pydantic import BaseModel

    class _Confirm(BaseModel):
        # 字段名就是客户端表单里显示的那个问题；bool → 客户端渲染成勾选框。
        confirm: bool = False

    message = (
        f"{payload['summary']}."
        + (f" On box: {payload['box']}." if payload.get("box") else " On this machine.")
        + " This cannot be undone."
    )
    # ⚠️ **必须用 FastMCP 的 `Context.elicit`，不能用 `ServerSession.elicit`。**
    # 两者签名不同（实测踩过）：FastMCP 版收 ``schema=<pydantic model>`` 并做校验、
    # 返回 ``AcceptedElicitation``（``.data`` 是**校验后的模型实例**）/ ``Declined`` /
    # ``Cancelled``；而 ``ServerSession.elicit`` 收 ``requestedSchema=<dict>``，
    # 用 FastMCP 的写法调它会 ``TypeError: unexpected keyword argument 'schema'``。
    # 第一版就是踩了这个 —— 而 except 又把它吞成"放行"，于是**拒绝被静默当成了同意**。
    try:
        from src.mcp.server import mcp

        ctx = mcp.get_context()
        result = await ctx.elicit(message=message, schema=_Confirm)
    except Exception as exc:
        logger.warning("MCP confirmation failed for %s (%s)", tool, type(exc).__name__)
        return err(
            f"refused: could not get a clear answer for {tool} from the client "
            f"({type(exc).__name__}). Do not retry; ask the user directly.",
            code="confirmation_failed",
        )

    action = getattr(result, "action", "decline")
    if action == "accept":
        data = getattr(result, "data", None)
        approved = bool(getattr(data, "confirm", False)) if data is not None else False
        if approved:
            logger.info("MCP %s approved by the agent's user", tool)
            return None
        # 填了表单但选了否：等价于拒绝（不放过）
        action = "decline"

    logger.info("MCP %s not approved (%s)", tool, action)
    return err(
        f"refused: the user did not approve {tool} ({action}). Do not retry the same "
        "call; ask the user what they want instead.",
        code="confirmation_declined",
    )


def confirm_destructive(tool_name: str | None = None):
    """Ask before dispatch so one decision covers local and forwarded tools."""
    def decorate(fn: Callable[..., Any]):
        name = tool_name or fn.__name__

        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any):
            from src.hub.routing import _bind_kwargs, _ctx_param_name, _to_tool_result

            bound = _bind_kwargs(fn, args, kwargs)
            bound.pop(_ctx_param_name(fn), None)
            refusal = await request_confirmation(name, bound)
            if refusal is not None:
                return _to_tool_result(refusal, fn)
            return await fn(*args, **kwargs)

        wrapper.__hub_confirm__ = name
        return wrapper
    return decorate


__all__ = [
    "CONFIRM_SUMMARIES",
    "DESTRUCTIVE_TOOLS",
    "REVIEWED_UNGLATED",
    "build_request",
    "client_supports_elicit",
    "confirm_destructive",
    "request_confirmation",
]
