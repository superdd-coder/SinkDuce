"""MCP 写操作确认 —— **在 Agent 侧问**（设计文档 §7，2026-09-18 定为 A 案）。

## 为什么确认属于 Agent 侧，而不是盒子

MCP 侧此前对写操作**零确认**（删除类有 6 个不可逆工具：删库/删文件/删会议/删笔记/
删待办/删热词库）。第一版把确认卡做在了**盒子的网页**上，那是错的，两个理由：

1. **主体错了**：真正驱使 Agent 的人是坐在 **Agent 客户端**（Claude Code / Cursor /
   ZCode）那边的，那些客户端本来就有自己的工具确认 UI。让盒子网页再问一遍，是
   同一个人被问两次，而**盒子根本不知道现在有没有人在场**。
2. **有个真实后果**：浏览器版做成「没有 SSE 订阅者就拒绝」→ 跑 headless Agent 时
   **每次删除都被拒**，提示「请打开 App」。等于把用户已经给出的删除权限，变成了
   「除非有人盯着盒子网页，否则永远删不了」。那是缺陷，不是安全。

**权限来自钥匙，不是来自这张卡。** 钥匙的角色/ACL 决定「能不能删」，那是另一条独立的
墙（``authorize()`` / ``require_collection()``）。这张卡只回答「你确定吗」——而那个问题
属于**操作 Agent 的人**，所以走 MCP 原生的反向通道问客户端。

## A 案的行为（用户 2026-09-18 拍板）

``elicit`` 可用 → 弹给 Agent 客户端：
- 用户同意 → 执行；
- 用户拒绝 / 取消 / 勾了表单又选否 → **拦下**，并告诉模型别再试。

``elicit`` **不可用**（客户端没实现 / 没有可用会话）→ **放行**，但把「这是不可逆操作、
且这次没经过交互确认」写进**工具结果**。理由：盒子按钥匙执法，「你确定吗」交回给 Agent
客户端自己的确认 UI；不能因为老客户端没实现 elicit 就把删除功能整个堵死。
**放行不是静默** —— 结果里带 ``warning``，模型看得到、也该转告用户。

## 为什么不用 stateless 时代的「浏览器确认卡」

设计文档 §7 当时选浏览器卡，理由是「不依赖客户端支持 elicit」—— 但那建立在一个
**后来被我们自己推翻的前提**上：当时是 stateless，而 ``elicit`` 在 stateless 下
**挂死不返回**（结果回传落在另一条连接上，服务端认不出）。第 1 步切回 stateful 后
``elicit`` 就能用了，**而没人回头重看这个决定**。约束一消失，次优解就变成了错解。
"""

from __future__ import annotations

import functools
import json
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
        # 能力探测**失败** ≠ 客户端不支持（2026-09-19 反转）。按「不支持」处理
        # 会把破坏性操作直接放进 A 案的放行分支——探测故障 = 零确认执行。
        # 改为返回 True：交给 ``request_confirmation`` 走真实的 elicit 尝试，
        # 那条路上任何失败都会 fail-closed 判为拒绝（见下方 except 注释）。
        logger.debug("capability probe failed; deferring to the elicit attempt", exc_info=True)
        return True


async def request_confirmation(tool: str, args: dict[str, Any]) -> dict[str, Any] | None:
    """请求人工确认。返回 None = 放行；返回 dict = 拒绝（附给模型的话术）。

    **假定调用方已经确认客户端支持 elicit**（``confirm_destructive`` 会先判）；
    这里再判一次是为了让这个函数单独调用时也安全。
    """
    payload = build_request(tool, args)
    if not client_supports_elicit():
        return None

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
        # ⚠️ 这里**不能一概放行**。实测过一类危险情形：客户端回
        # ``action="accept"`` 但 ``content=None`` 时，SDK 的
        # ``elicit_with_validation`` 会 **raise ValueError("Unexpected elicitation
        # action")** —— 那是"回答畸形"，**不是**"客户端不支持 elicit"。
        # 早先的 `except Exception: return None` 会把它当成放行 ⇒
        # **畸形回答 = 同意**（正是本模块历史上栽过的那个坑：拒绝静默变同意）。
        #
        # 现在的规则：只有"根本没有可用会话/能力"才放行（那种情况
        # ``client_supports_elicit`` 已经是 False，走不到这里）；能走到这里的
        # 异常一律**判为拒绝**（fail-closed），并把原因写清楚。
        logger.warning("elicit failed for %s (%s) — treating as DECLINED", tool, exc)
        from src.mcp.common import err

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
    from src.mcp.common import err

    return err(
        f"refused: the user did not approve {tool} ({action}). Do not retry the same "
        "call; ask the user what they want instead.",
        code="confirmation_declined",
    )


def unconfirmed_warning(tool: str) -> dict[str, Any]:
    """A 案放行时附加的风险提示（放进工具结果，让模型转告用户）。

    **不是错误**（工具确实执行了），所以叫 warning 而不是 error。
    """
    return {
        "warning": (
            f"{tool} is irreversible and ran without an interactive confirmation "
            "(this client cannot show one). Tell the user what was deleted."
        ),
        "confirmed": False,
    }


def confirm_destructive(tool_name: str | None = None):
    """装饰器：给不可逆的 MCP 写工具套上确认（见模块 docstring）。

    用法（**放在模块闸门内侧**，即写在它下面，顺序 = 先判权限、再问确认）::

        @module_gated("database")
        @confirm_destructive()
        async def delete_note(collection: str, note_id: str) -> str:
            ...

    ``functools.wraps`` 必须保留：FastMCP 靠 ``inspect.signature`` 生成工具 schema，
    丢了签名参数表就没了（与 ``domain_gated`` / ``module_gated`` 同一条注意）。

    **放行分支会把 warning 合并进工具结果**（仅当操作真的成功）—— 见
    :func:`_attach_warning` 与 :func:`_looks_failed`。
    """

    def _deco(fn: Callable[..., Any]):
        name = tool_name or fn.__name__

        @functools.wraps(fn)
        async def _wrapper(*args: Any, **kwargs: Any):
            session = _current_session()
            if client_supports_elicit(session):
                from src.hub.routing import _bind_kwargs, _ctx_param_name

                call_kwargs = _bind_kwargs(fn, args, kwargs)
                ctx_name = _ctx_param_name(fn)
                clean = {k: v for k, v in call_kwargs.items() if k != ctx_name}
                if denied := await request_confirmation(name, clean):
                    from src.mcp.common import to_json

                    return to_json(denied)
                return await fn(*args, **kwargs)

            # A 案：不支持 elicit → 照常执行，但把「没经确认」写进结果。
            result = await fn(*args, **kwargs)
            # **只在真的执行成功时**贴那条提示：操作失败了（目标不存在、没权限…）
            # 却告诉用户「它没经确认就跑了」是假信息 —— 实测发现会出现在
            # 「Note not found」这种错误结果上。失败本身已经说明没发生任何事。
            if _looks_failed(result):
                return result
            return _attach_warning(result, unconfirmed_warning(name))

        # 供覆盖面检查与诊断读取（不改行为）
        _wrapper.__hub_confirm__ = name  # type: ignore[attr-defined]
        return _wrapper

    return _deco


def _looks_failed(result: Any) -> bool:
    """结果是不是一个失败（用来决定要不要贴"未确认执行"的提示）。"""
    from mcp.types import CallToolResult

    if isinstance(result, CallToolResult):
        if result.isError:
            return True
        sc = result.structuredContent or {}
        return bool(sc.get("error"))
    if isinstance(result, str):
        try:
            data = json.loads(result)
        except Exception:
            return True  # 不是 JSON 就当失败（无法确认成功），别贴误导提示
        return not isinstance(data, dict) or bool(data.get("error"))
    if isinstance(result, dict):
        return bool(result.get("error"))
    return True


def _attach_warning(result: Any, extra: dict[str, Any]) -> Any:
    """把 extra 合并进工具结果（兼容 str / dict / CallToolResult 三种形态）。"""
    from mcp.types import CallToolResult, TextContent

    if isinstance(result, CallToolResult):
        sc = dict(result.structuredContent or {})
        sc.update(extra)
        return CallToolResult(
            content=result.content or [TextContent(type="text", text="OK")],
            structuredContent=sc,
            isError=bool(result.isError),
        )
    if isinstance(result, str):
        try:
            data = json.loads(result)
        except Exception:
            return result
        if isinstance(data, dict):
            data.update(extra)
            return json.dumps(data, ensure_ascii=False, default=str)
        return result
    if isinstance(result, dict):
        return {**result, **extra}
    return result


__all__ = [
    "CONFIRM_SUMMARIES",
    "DESTRUCTIVE_TOOLS",
    "REVIEWED_UNGLATED",
    "build_request",
    "client_supports_elicit",
    "confirm_destructive",
    "request_confirmation",
    "unconfirmed_warning",
]
