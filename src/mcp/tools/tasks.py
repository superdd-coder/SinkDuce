"""MCP task management tools.

Exposes the async task manager at ``src.tasks.task_manager`` as 5 atomic tools:
- :func:`list_tasks` — list with collection / status / type filters
- :func:`get_task_status` — get one task's progress
- :func:`cancel_task` — cancel pending / processing task
- :func:`retry_task` — re-enqueue a failed task
- :func:`clear_completed_tasks` — remove completed / failed records
"""

from __future__ import annotations

import logging
from typing import Any

from src.mcp.common import err, ok, run_sync, to_json

logger = logging.getLogger(__name__)

# Allowable status values for list_tasks filter. Matches TaskStatus enum in
# src.tasks.task_manager.
_ALLOWED_STATUSES = {"pending", "processing", "completed", "failed"}
_ALLOWED_TYPES = {"upload", "consolidate", "doc_summary", "sparse_recalc",
                  "meeting_summary", "meeting_extract"}


async def get_task_status(task_id: str) -> str:
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
        return tm.get_task(task_id).to_dict()

    return to_json(await run_sync(_run))


async def list_tasks(
    collection: str | None = None,
    status: str | None = None,
    task_type: str | None = None,
    limit: int = 100,
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
        all_tasks = task_manager.get_all_tasks(collection=collection)
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


async def cancel_task(task_id: str) -> str:
    """Cancel a pending or processing task.

    Cooperative cancellation — long-running operations (enrichment, embedding)
    check a cancellation event and abort at the next safe checkpoint.
    Idempotent: returns ``cancelled: false`` for already-finished tasks.
    """
    from src.tasks import task_manager

    def _run() -> dict[str, Any]:
        task = task_manager.get_task(task_id)
        if not task:
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


async def retry_task(task_id: str) -> str:
    """Re-enqueue a failed task for another attempt.

    Returns an error if the task doesn't exist, isn't failed, or the
    original task arguments are no longer available.
    """
    from src.tasks import task_manager

    def _run() -> dict[str, Any]:
        task = task_manager.get_task(task_id)
        if not task:
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


async def clear_completed_tasks() -> str:
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
