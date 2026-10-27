"""Shared utilities for MCP tools.

Every MCP tool has the same boilerplate:
- Defer-import ``src.services`` to avoid circular imports
- Wrap blocking I/O in a thread-pool executor
- JSON-serialize the result (with safe defaults for non-serializable types)

This module provides:
- :func:`run_sync` — async wrapper around blocking functions
- :func:`to_json` — safe JSON serialization
- :func:`ok` / :func:`err` — success / error response builders
- :func:`require_collection` — collection existence check
- :func:`require_task` — task existence check
- :func:`safe_filename` — path traversal guard
- :func:`decode_base64_content` — base64 → bytes for upload tools
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


# ── Async / serialization ────────────────────────────────────


def run_sync(fn: Callable[[], T]) -> asyncio.Future[T]:
    """Schedule a blocking function on the default thread pool.

    **必须保留请求上下文**：MCP 工具里大量 ``run_sync(_run)`` 的 ``_run`` 会读
    ``get_actor()`` 做可见性/授权判断（检索、列库、读文件…）。裸
    ``run_in_executor`` 不复制 ``contextvars``，线程里身份退回默认 owner，于是
    ``may_see_everything`` 为真、**隐藏库的过滤整个失效**（2026-09-17 实测到的
    泄露路径）。见 ``src/async_ctx.py``。

    Usage::

        async def my_tool(...) -> str:
            def _run():
                return some_blocking_call()
            return to_json(await run_sync(_run))
    """
    from src.async_ctx import run_with_context

    return asyncio.ensure_future(run_with_context(fn))


def to_json(obj: Any) -> str:
    """JSON-serialize with safe defaults (handles datetime, Path, etc.)."""
    return json.dumps(obj, ensure_ascii=False, default=str)


# ── Response builders ─────────────────────────────────────────


def ok(**fields: Any) -> dict[str, Any]:
    """Build a success response dict (no ``error`` key)."""
    return dict(fields)


def err(message: str, **extra: Any) -> dict[str, Any]:
    """Build an error response dict (always includes ``error`` key)."""
    payload: dict[str, Any] = {"error": message}
    payload.update(extra)
    return payload


def mcp_result(data: dict[str, Any]) -> Any:
    """Return a short text line + ``structuredContent`` (avoid dual full JSON).

    MCP SDK otherwise puts the entire payload in TextContent *and*
    structuredContent when tools return a bare dict — doubling large trees
    or long document text in the model context.

    ⚠️ **字段名是 ``structuredContent``（驼峰），不是 ``structured_content``**。
    这是个**曾经真实存在的静默 bug**：这里一度传 ``structured_content=``，pydantic
    对未知关键字默认忽略（构造不报错），于是 ``structuredContent`` 恒为 ``None`` ——
    工具只返回了一行 ``OK ...`` 文本，**结构化载荷整体丢失**，
    而声明了 ``outputSchema`` 的工具还会因此变成
    「Output validation error: outputSchema defined but no structured output returned」。
    单测当时只断言「返回的是 JSON 字符串」，所以照绿。已由
    ``tests/test_mcp_result_shape.py`` 钉住。
    """
    try:
        from mcp.types import CallToolResult, TextContent

        is_err = bool(data.get("error"))
        if is_err:
            summary = f"error: {data.get('error')}"
            if data.get("extract_status"):
                summary += f" extract_status={data['extract_status']}"
            if data.get("hint"):
                summary += f" | {str(data['hint'])[:120]}"
        else:
            bits: list[str] = []
            if "collection" in data:
                bits.append(f"collection={data['collection']}")
            sm = data.get("summary") or {}
            if isinstance(sm, dict):
                if "unique_file_count" in sm:
                    bits.append(f"files={sm['unique_file_count']}")
                if "folder_count" in sm:
                    bits.append(f"folders={sm['folder_count']}")
                if "node_count" in sm:
                    bits.append(f"nodes={sm['node_count']}")
                if "detached_branch_count" in sm:
                    bits.append(f"detached={sm['detached_branch_count']}")
            if data.get("total") is not None:
                bits.append(f"total={data['total']}")
            if data.get("file_id"):
                bits.append(f"file_id={data['file_id']}")
            if data.get("extract_status"):
                bits.append(f"extract_status={data['extract_status']}")
            if data.get("total_chars") is not None:
                bits.append(f"chars={data['total_chars']}")
            if data.get("truncated"):
                bits.append("truncated=true")
            summary = "OK " + " ".join(bits) if bits else "OK"
            if data.get("warnings"):
                summary += f" warnings={len(data['warnings'])}"
            if data.get("read_hint"):
                summary += " | " + str(data["read_hint"])[:120]
        return CallToolResult(
            content=[TextContent(type="text", text=summary)],
            structuredContent=data,
            # ⚠️ 字段名是 ``isError``（驼峰）。写成 ``is_error`` 会被 pydantic
            # 静默收进 model_extra —— 不仅是"名字不对"，还会：
            # ① 在线上多发一个非标准键；② **错误结果被报成成功**（isError 恒 False），
            # 于是只认 isError 的客户端会把"库不存在"当成一次正常返回。
            # 与本仓 `-> str` 那条路（FastMCP 包装，isError 由协议层决定）行为对齐：
            # 两边都按 payload 有没有 error 键来定。
            isError=is_err,
        )
    except Exception:
        # Fallback: plain dict (SDK will serialize; may dual-encode)
        return data


# ── Resource existence checks ────────────────────────────────


def require_collection(collection: str) -> dict[str, Any] | None:
    """Return None if collection exists, else return an error dict.

    Usage in a tool::

        def _run():
            if e := require_collection(collection):
                return e
            ...
    """
    # Lazy import to avoid loading services at MCP-module import time
    from src.services import services

    if not services.db.collection_exists(collection):
        return err(f"Collection '{collection}' does not exist")

    # RBAC（2026-09-16）：存在性 ≠ 可见性。**这里是 MCP 全部库类工具的公共准入点**
    # （collections / documents / search / summaries / notes / file_mgmt / tasks
    # 都先调它），所以一处升级即可覆盖整个工具面——比逐个工具补更不易漏。
    #
    # 错误文案与"不存在"**保持一致**：不泄露"存在但你没权限"。
    # 无身份上下文（同机 Agent 走回环）→ get_actor() 是 owner，自然通过。
    try:
        from src.identity import get_actor
        from src.identity import visibility as _vis

        actor = get_actor()
        if actor is not None and not _vis.is_collection_visible(actor, collection):
            return err(f"Collection '{collection}' does not exist")
    except Exception:
        # fail-closed：判定失败按不可见处理
        return err(f"Collection '{collection}' does not exist")
    return None


def require_domain(domain: str) -> dict[str, Any] | None:
    """域 read 档闸门（meeting / hot_words / people）。None = 放行。

    与 REST 侧 ``api/routes/identity.require_domain``（FastAPI router 级依赖）
    **同一个判定**，只是形态不同——MCP 工具是普通函数，没有依赖注入，所以显式调。
    两处必须一致：同一把钥匙走 REST 被拒、走 MCP 却被放行，就是权限模型的裂缝。

    **为什么必须有**（2026-09-18 实测的泄露）：这三域在 REST 上有 router 级依赖兜底，
    而 MCP 工具**直连 store**，此前**零防护**——visitor 钥匙走 MCP 能读到：
    全部会议标题/状态、会议转写稿、热词库、以及（见下方 tasks 说明）任务列表。
    ``meetings.py`` / ``hot_words.py`` 的授权点统计当时是 **0**。

    错误文案用「权限不足」而非「不存在」：这是**能力级**判定（这个角色能不能碰这个域），
    不是资源级可见性——后者必须保持 404 同形（不泄露"存在但无权"）。域级说清楚更好，
    好让 Agent 转告用户而不是反复试。文案里显式写明 do not retry（用户 2026-09-18 要求）。

    fail-closed：判定抛异常 → 拒绝。
    """
    try:
        from src.identity import acl as _acl
        from src.identity import get_actor
        from src.identity.policy import may_see_everything

        actor = get_actor()
        # 无身份上下文（内部调用/测试直调）→ get_actor() 默认就是 owner，自然通过。
        # 本机回环的 Agent 也走这条：门卫把回环请求设成 owner。
        if actor is None or may_see_everything(actor):
            return None
        if _acl.domain_allows(domain, "read", actor.role_id):
            return None
    except Exception:
        logger.exception("domain gate failed for %r — denying", domain)
        return err(
            f"permission check unavailable for the {domain} domain",
            code="forbidden",
        )
    return err(
        f"permission denied: this key's role has no access to the {domain} domain. "
        "Do not retry the same call — tell the user this key lacks that permission.",
        code="forbidden",
    )


def domain_gated(domain: str):
    """装饰器：给 MCP 工具套上域闸门（见 :func:`require_domain`）。

    用法（在工具定义处）::

        @domain_gated(DOMAIN_MEETING)
        async def list_meetings(...) -> str:
            ...

    **为什么用装饰器而不是逐个工具插一行**：这些域一个文件里就有 11 个工具，
    手写调用漏掉一个就是一次**静默的鉴权绕过**——而那正是它要防的 bug。
    装饰器让「新工具自动带闸门」成为默认，不依赖记性。

    ``functools.wraps`` 必须保留：FastMCP 靠 ``inspect.signature`` 生成工具
    schema（``properties`` / ``required``），丢掉签名就会把参数表弄丢
    （2026-09-18 已用 ``add_tool`` 与 ``@mcp.tool()`` 两条注册路径各验一次）。
    """
    import functools

    def _deco(fn):
        @functools.wraps(fn)
        async def _wrapper(*args: Any, **kwargs: Any):
            if e := require_domain(domain):
                return to_json(e)
            return await fn(*args, **kwargs)

        return _wrapper

    return _deco


def module_gated(*modules: str):
    """装饰器：给 MCP 工具套上**功能模块**闸门（``chat``/``database``/``recall``/
    ``tasks``/``settings``）。**多模块 OR 语义**（跨页共享的工具只满足其一即可）。

    与 REST 侧 ``api/routes/identity.require_module`` **同一套判定**（同一个
    ``role.allows_module``）。两侧必须一致：同一把钥匙走 REST 被拒、走 MCP 却放行，
    就是权限模型的裂缝（这正是 2026-09-18 在 tasks 上实测到的问题）。

    ``modules`` 此前只在前端化妆（侧边栏隐藏入口），服务端一次都没查 —— 用户要求
    「真实的服务端限制」，所以 REST 与 MCP 两条通道一起补。
    """
    import functools

    def _deco(fn):
        @functools.wraps(fn)
        async def _wrapper(*args: Any, **kwargs: Any):
            if e := _require_module_allowed(*modules):
                return to_json(e)
            return await fn(*args, **kwargs)

        return _wrapper

    return _deco


def _require_module_allowed(*modules: str) -> dict[str, Any] | None:
    """模块闸门的执行体（MCP 侧）。None = 放行。fail-closed。"""
    try:
        from src.identity import get_actor
        from src.identity.policy import may_see_everything
        from src.identity.roles import get_role

        actor = get_actor()
        if actor is None or may_see_everything(actor):
            return None
        role = get_role(actor.role_id)
        if role is not None and any(role.allows_module(m) for m in modules):
            return None
    except Exception:
        logger.exception("module gate failed for %s — denying", modules)
        return err("permission check unavailable", code="forbidden")
    return err(
        "permission denied: this key's role has no access to the "
        + (" or ".join(modules) if len(modules) > 1 else modules[0])
        + " module. Do not retry the same call — tell the user this key lacks that permission.",
        code="forbidden",
    )


def require_task(task_id: str) -> dict[str, Any] | tuple[Any, None] | None:
    """Return (task, None) if found, else (None, error_dict).

    Returns a 2-tuple so callers can both grab the task and short-circuit::

        task_or_err = require_task(task_id)
        if isinstance(task_or_err, dict):
            return json.dumps(task_or_err)
        task = task_or_err
    """
    from src.tasks import task_manager as _tm

    task = _tm.get_task(task_id)
    if not task:
        return err(f"Task '{task_id}' not found")
    return task


# ── Path / content safety ────────────────────────────────────


# Filename: allow letters, digits, dot, dash, underscore, space, parens, Chinese.
# Reject path separators and NUL bytes.
import re

_FILENAME_RE = re.compile(r"^[A-Za-z0-9._\- ()（）一-鿿]+$")


def safe_filename(name: str, max_len: int = 255) -> str:
    """Validate a user-supplied filename.

    Raises ValueError if ``name`` contains path separators, NUL, or other
    characters that could enable path traversal. Returns the name unchanged
    on success.
    """
    if not name:
        raise ValueError("Filename must not be empty")
    if len(name) > max_len:
        raise ValueError(f"Filename too long (max {max_len} chars)")
    if "/" in name or "\\" in name or "\x00" in name:
        raise ValueError(f"Invalid filename: {name!r}")
    if not _FILENAME_RE.match(name):
        raise ValueError(f"Invalid filename: {name!r}")
    return name


def decode_base64_content(content_b64: str) -> bytes:
    """Decode a base64 string to raw bytes.

    Raises ValueError on invalid input. Tolerant of whitespace / newlines in
    the input (common when JSON-embedding base64 content).
    """
    if not content_b64:
        raise ValueError("Empty content")
    try:
        # stripdata: URLs sometimes prepend the data URL prefix
        if content_b64.startswith("data:"):
            _, _, content_b64 = content_b64.partition(",")
        return base64.b64decode(content_b64, validate=False)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"Invalid base64 content: {exc}") from exc


# ── Destructive-field guard ──────────────────────────────────

# Fields that the collection config exposes to MCP but are forbidden to modify
# because changing them would either destroy indexed data (chunk_mode) or
# require a full re-index (embedding_*).
DESTRUCTIVE_COLLECTION_FIELDS: frozenset[str] = frozenset({
    "chunk_mode",
    "embedding_provider",
    "embedding_model",
    "embedding_dimensions",
    "embedding_base_url",
    "embedding_api_key",
    "embedding_batch_size",
})


def filter_destructive_fields(updates: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Remove destructive fields from an update dict.

    Returns ``(safe_updates, rejected_fields)``. Callers may choose to raise
    on ``rejected_fields`` or simply log a warning.
    """
    rejected = [k for k in updates if k in DESTRUCTIVE_COLLECTION_FIELDS]
    safe = {k: v for k, v in updates.items() if k not in DESTRUCTIVE_COLLECTION_FIELDS}
    return safe, rejected


__all__ = [
    "run_sync",
    "to_json",
    "ok",
    "err",
    "mcp_result",
    "require_collection",
    "require_task",
    "safe_filename",
    "decode_base64_content",
    "DESTRUCTIVE_COLLECTION_FIELDS",
    "filter_destructive_fields",
]
