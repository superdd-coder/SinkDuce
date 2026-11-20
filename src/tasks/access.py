"""Resource-level visibility and mutation policy shared by the REST and MCP task endpoints."""

from __future__ import annotations

from typing import Any


def task_collection(task_id: str, task: Any | None = None) -> str | None:
    from src.tasks import task_manager

    _, args = task_manager._task_args.get(task_id, (None, {}))
    return getattr(task, "collection", None) or args.get("collection")


def restore_task_requester(saved):
    """Restore a server-recorded requester. Unattributed jobs cannot run as the owner."""
    from src.identity.actor import Actor

    if isinstance(saved, dict) and all(isinstance(saved.get(k), str) and saved[k] for k in ("id", "kind", "role_id")):
        kind = saved["kind"]
        if kind == "key" or (kind in {"local", "system"} and saved["id"] == kind and saved["role_id"] == "owner"):
            return Actor(id=saved["id"], kind=kind, role_id=saved["role_id"])
    return Actor(id="unattributed", kind="anonymous", role_id="")


def _collection_write_action(task_type):
    return "collection.update" if task_type == "embedding_rebuild" else "file.update"


def _task_access(actor: Any, task_id: str, task: Any, *, write: bool) -> bool:
    from src.identity.policy import can, is_owner
    from src.identity.visibility import is_collection_visible
    from src.tasks import task_manager

    if actor is None or is_owner(actor):
        return True
    kind, args = task_manager._task_args.get(task_id, ("", {}))
    meeting_id = args.get("meeting_id")
    if meeting_id or (kind or "").startswith("meeting_"):
        if not can(actor, "meeting.read"):
            return False
        if not write:
            return True
        from src.meeting.store import get_meeting
        meeting = get_meeting(meeting_id) if meeting_id else None
        return meeting is not None and can(actor, "meeting.update", {"meeting": meeting.model_dump()})
    collection = task_collection(task_id, task)
    if not write:
        return not collection or is_collection_visible(actor, collection)
    return bool(collection) and can(actor, _collection_write_action(kind), {"collection_id": collection})


def require_execution_permission(actor, task_type: str, collection: str, args: dict) -> None:
    from src.identity import authorize
    from src.pairing.service import device_actor_is_active

    if actor.kind in {"system", "local"}:
        return
    if not device_actor_is_active(actor):
        raise PermissionError("The device authorization for this task is no longer active")
    meeting_id = args.get("meeting_id")
    if meeting_id or task_type.startswith("meeting_") or task_type == "transcribe":
        from src.meeting.store import get_meeting
        meeting = get_meeting(meeting_id) if meeting_id else None
        if meeting is None:
            raise PermissionError("Meeting unavailable")
        authorize(actor, "meeting.update", {"meeting": meeting.model_dump()})
        if task_type != "upload":
            return
    authorize(actor, _collection_write_action(task_type), {"collection_id": collection})


def can_see_task(actor: Any, task_id: str, task: Any | None = None) -> bool:
    return _task_access(actor, task_id, task, write=False)


def can_manage_task(actor: Any, task_id: str, task: Any) -> bool:
    return _task_access(actor, task_id, task, write=True)


def clear_manageable_tasks(actor: Any) -> tuple[int, int]:
    from src.tasks import task_manager

    visible = filter_visible_tasks(actor, task_manager.get_all_tasks())
    manageable = {t.id for t in visible if can_manage_task(actor, t.id, t)}
    removed = task_manager.clear_completed_tasks(manageable)
    return removed, len(visible) - removed


def filter_visible_tasks(actor: Any, tasks: list[Any]) -> list[Any]:
    """把一批任务收窄到可见集（列表出口用）。"""
    return [t for t in tasks if can_see_task(actor, getattr(t, "id", ""), t)]
