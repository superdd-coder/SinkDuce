"""MCP task management tools.

Exposes the async task manager at ``src.tasks.task_manager`` as 5 atomic tools:
- :func:`list_tasks` — list with collection / status / type filters
- :func:`get_task_status` — get one task's progress
- :func:`cancel_task` — cancel pending / processing task
- :func:`retry_task` — re-enqueue a failed task
- :func:`clear_completed_tasks` — remove completed / failed records
"""

from __future__ import annotations

from mcp.server.fastmcp import Context

import logging
from typing import Any

from src.hub.routing import hub_routed
from src.hub.write_forwards import cancel_task_forward, retry_task_forward
from src.mcp.common import err, ok, run_sync, to_json, module_gated
from src.tasks.access import can_see_task, filter_visible_tasks

logger = logging.getLogger(__name__)

# Allowable status values for list_tasks filter. Matches TaskStatus enum in
# src.tasks.task_manager.
_ALLOWED_STATUSES = {"pending", "processing", "completed", "failed"}
_ALLOWED_TYPES = {"upload", "consolidate", "doc_summary", "sparse_recalc",
                  "meeting_summary", "meeting_extract", "meeting_transcript_index"}


def _task_denied(task_id: str) -> str:
    """看不见的任务 = 与"不存在"同形（不泄露"存在但无权"）。"""
    return to_json(err(f"Task '{task_id}' not found"))


@module_gated("tasks")
@hub_routed(
    method="GET",
    path="/documents/tasks/{task_id}",
    # REST 返回 task.to_dict()，与本地**同一个函数**——透传即同形。
    # 404（不存在/不可见）由外层变成结构化 error。
    forwards=("task_id",),
)
async def get_task_status(task_id: str, ctx: Context | None = None) -> str:
    """Check the status and progress of an async task.

    Returns status (pending/processing/completed/failed), progress percentage,
    and any error details. Works for any task type — upload, transcription,
    consolidation, summary generation, etc.
    """
    from src.tasks import task_manager as tm

    def _run() -> dict[str, Any]:
        task = tm.get_task(task_id)
        if not task:
            return err(f"Task '{task_id}' not found")
        # RBAC（2026-09-18）：任务可见性继承其所属库。不可见 → 与"不存在"同形
        # （不泄露"存在但无权"）。此前 MCP 侧完全没有这道判定，而 REST 有 → 通道不一致。
        from src.identity import get_actor

        if not can_see_task(get_actor(), task_id, task):
            return err(f"Task '{task_id}' not found")
        return tm.get_task(task_id).to_dict()

    return to_json(await run_sync(_run))


def _tasks_filters_refuse(args: dict[str, Any]) -> str | None:
    """转发侧复刻本地的枚举校验（REST 不校验：坏值 → **空成功**，
    本地 → 明确报错列出合法值；模型据此改参数而不是误判"没有任务"）。"""
    status = args.get("status")
    if status is not None and status not in _ALLOWED_STATUSES:
        return f"Invalid status '{status}'. Must be one of: {sorted(_ALLOWED_STATUSES)}"
    task_type = args.get("task_type")
    if task_type is not None and task_type not in _ALLOWED_TYPES:
        return f"Invalid task_type '{task_type}'. Must be one of: {sorted(_ALLOWED_TYPES)}"
    return None


@module_gated("tasks")
@hub_routed(
    method="GET",
    path="/documents/tasks",
    query=lambda args: {
        "collection": args.get("collection"),
        "status": args.get("status"),
        "task_type": args.get("task_type"),
        # 工具默认 limit=100；REST 缺省不截断 → 必须把默认值也带上，
        # 否则跨盒子会拿到"全部"而本地只给 100（同一个调用两种结果）
        "limit": args.get("limit", 100),
    },
    refuse=_tasks_filters_refuse,
    # REST 返回 {tasks, total, pending, processing} —— 工具的契约是 {tasks, total}。
    # 多出来的 pending/processing 是**良性超集**（计数信息），保留。
    # 已知差异（低危，记录不拒）：REST 的 collection 过滤会解析**显示名**，
    # 本地按 ID 精确匹配；以及远端钥匙看不到该库时 REST 报 404、本地回空列表
    #（仅受限角色的远端钥匙会出现）。
    forwards=("collection", "status", "task_type", "limit"),
)
async def list_tasks(
    collection: str | None = None,
    status: str | None = None,
    task_type: str | None = None,
    limit: int = 100,
    ctx: Context | None = None,
) -> str:
    """List async tasks, optionally filtered by collection, status, and type.

    Args:
        collection: Filter by collection name (None = all).
        status: One of ``pending`` / ``processing`` / ``completed`` / ``failed``.
        task_type: One of ``upload`` / ``consolidate`` / ``doc_summary`` /
            ``sparse_recalc`` / ``meeting_summary`` / ``meeting_extract``.
        limit: Max tasks to return (default 100).

    Returns:
        JSON ``{"tasks": [...], "total": N}``. Each task includes
        ``id`` / ``filename`` / ``collection`` / ``status`` / ``progress`` /
        ``message`` / ``result`` / ``error`` / ``created_at`` /
        ``started_at`` / ``completed_at`` / ``task_type``.
    """
    from src.tasks import task_manager

    if status is not None and status not in _ALLOWED_STATUSES:
        return to_json(err(
            f"Invalid status '{status}'. Must be one of: {sorted(_ALLOWED_STATUSES)}"
        ))
    if task_type is not None and task_type not in _ALLOWED_TYPES:
        return to_json(err(
            f"Invalid task_type '{task_type}'. Must be one of: {sorted(_ALLOWED_TYPES)}"
        ))

    def _run() -> dict[str, Any]:
        from src.identity import get_actor

        actor = get_actor()
        # 显示名 → ID（docstring 承诺按名过滤，任务记录里存的却是 ID；REST 侧
        # 一直做这个解析。此前本地不解析 → 传名 = 静默空结果，与转发/REST 两个
        # 答案 —— 批次 2 审查抓到，按 REST 同法补齐）。
        collection_id = collection
        if collection:
            from src.collections.store import (
                find_collection_by_name,
                get_collection_meta,
            )

            meta = get_collection_meta(collection) or find_collection_by_name(collection)
            collection_id = meta["id"] if meta else collection
        # RBAC（2026-09-18）：**先按全局**取全部任务再收窄 —— 不能只在传了 collection
        # 时收窄。任务条目带 collection / filename，等于把不可见库的存在与文件名漏出去
        # （REST 列表端点早已这么做，MCP 此前没有 → 同一把钥匙两条通道答案不同）。
        all_tasks = task_manager.get_all_tasks(collection=collection_id)
        all_tasks = filter_visible_tasks(actor, all_tasks)
        # Apply status filter (in-memory; task_manager doesn't index by status)
        if status is not None:
            all_tasks = [t for t in all_tasks if t.status.value == status]
        # Apply task_type filter via _task_args
        if task_type is not None:
            all_tasks = [
                t for t in all_tasks
                if task_manager._task_args.get(t.id, (None,))[0] == task_type
            ]
        # Sort newest first
        all_tasks.sort(key=lambda t: t.created_at, reverse=True)
        all_tasks = all_tasks[:limit]
        # Build dicts with task_type info
        return {
            "tasks": [
                t.to_dict_with_type(task_manager._task_args.get(t.id, ("unknown", {}))[0])
                for t in all_tasks
            ],
            "total": len(all_tasks),
        }

    return to_json(await run_sync(_run))


@module_gated("tasks")
@hub_routed(
    # 先读后写：REST 的 POST cancel 把「已完成」也归成 400，而工具契约要
    # 温和的 ``cancelled:false`` —— 见 write_forwards.cancel_task_forward。
    forward=cancel_task_forward,
    forwards=("task_id",),
)
async def cancel_task(task_id: str, ctx: Context | None = None) -> str:
    """Cancel a pending or processing task.

    Cooperative cancellation — long-running operations (enrichment, embedding)
    check a cancellation event and abort at the next safe checkpoint.
    Idempotent: returns ``cancelled: false`` for already-finished tasks.
    """
    from src.tasks import task_manager

    def _run() -> dict[str, Any]:
        from src.identity import get_actor

        task = task_manager.get_task(task_id)
        # 看不见的任务 = 不存在。**在动手之前**判（破坏先于授权是最糟的）。
        if not task or not can_see_task(get_actor(), task_id, task):
            return err(f"Task '{task_id}' not found")
        if task.status.value in ("completed", "failed"):
            return ok(
                task_id=task_id,
                cancelled=False,
                message=f"Task is already {task.status.value}",
            )
        ok_cancel = task_manager.cancel_task(task_id)
        return ok(
            task_id=task_id,
            cancelled=ok_cancel,
            status=task.status.value,
        )

    return to_json(await run_sync(_run))


@module_gated("tasks")
@hub_routed(
    # 先读后写：区分「不存在 / 状态不对」两种 REST 合并成 400 的情况
    # （模型据此决定等待还是放弃）—— 见 write_forwards.retry_task_forward。
    forward=retry_task_forward,
    forwards=("task_id",),
)
async def retry_task(task_id: str, ctx: Context | None = None) -> str:
    """Re-enqueue a failed task for another attempt.

    Returns an error if the task doesn't exist, isn't failed, or the
    original task arguments are no longer available.
    """
    from src.tasks import task_manager

    def _run() -> dict[str, Any]:
        from src.identity import get_actor

        task = task_manager.get_task(task_id)
        if not task or not can_see_task(get_actor(), task_id, task):
            return err(f"Task '{task_id}' not found")
        if task.status.value != "failed":
            return err(
                f"Task '{task_id}' is {task.status.value}, only failed tasks can be retried",
                status=task.status.value,
            )
        retried = task_manager.retry_task(task_id)
        if retried is None:
            return err(
                f"Cannot retry task '{task_id}' (original arguments no longer available)"
            )
        return ok(task_id=task_id, status="pending", message="Re-queued for processing")

    return to_json(await run_sync(_run))


@module_gated("tasks")
@hub_routed(
    method="DELETE",
    path="/documents/tasks/completed",
    # REST（本轮对齐后）回 {message, removed, remaining}；工具契约是
    # ok(removed=…, remaining=…)——摘两个计数字段即可。
    shape=lambda payload, a: {
        "removed": (payload or {}).get("removed", 0),
        "remaining": (payload or {}).get("remaining", 0),
    },
    forwards=(),
)
async def clear_completed_tasks(ctx: Context | None = None) -> str:
    """Remove all completed and failed task records from memory.

    Does not affect currently pending or processing tasks. Useful for cleaning
    up long task lists after a batch of work has finished.
    """
    from src.tasks import task_manager

    def _run() -> dict[str, Any]:
        before = len(task_manager.tasks)
        task_manager.clear_completed_tasks()
        after = len(task_manager.tasks)
        return ok(removed=before - after, remaining=after)

    return to_json(await run_sync(_run))


__all__ = [
    "get_task_status",
    "list_tasks",
    "cancel_task",
    "retry_task",
    "clear_completed_tasks",
]
