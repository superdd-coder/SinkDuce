"""collections 域的 **Agent 视图函数**（批次 4.5：REST agent 端点与 MCP 工具共用）。

`collections_list_view` / `collection_info_view` 与 `list_collections` /
`get_collection` 工具的本地输出**同一份**——工具的富信封（notes_count /
last_active / catalog_definition）比网页版 `/api/collections` 重，所以走
agent 专用端点而不是改造网页端点。
"""

from __future__ import annotations

import logging
from typing import Any

from src.mcp.common import ok, require_collection

logger = logging.getLogger("collections_views")


def collections_list_view() -> list[dict[str, Any]]:
    """`list_collections` 工具的信封：富元数据 + RBAC 可见性收窄。"""
    from src.collections import store as cstore
    from src.services import services
    from src.notes import store as nstore
    from src.tasks import task_manager
    from src.rag.summary_manager import SummaryManager
    from src.meeting.transcript_index import INTERNAL_QDRANT_COLLECTIONS

    names = [c for c in services.db.list_collections() if c not in INTERNAL_QDRANT_COLLECTIONS]
    # RBAC：枚举出口必须收窄（该工具不经过 require_collection）
    from src.identity import get_actor
    from src.identity import visibility as _vis

    names = _vis.filter_collection_ids(get_actor(), names)
    sm = SummaryManager(db=services.db)

    # Build notes-count map (cheap: O(notes) once, not per-collection)
    notes_by_collection: dict[str, int] = {}
    for n in nstore.list_notes():
        notes_by_collection[n.collection] = notes_by_collection.get(n.collection, 0) + 1

    # Build last-active map from task manager
    last_active_by_collection: dict[str, Any] = {}
    for tid, (ttype, _args) in task_manager._task_args.items():
        t = task_manager.tasks.get(tid)
        if not t:
            continue
        ts = t.completed_at or t.started_at or t.created_at
        cur = last_active_by_collection.get(t.collection)
        if cur is None or ts > cur:
            last_active_by_collection[t.collection] = ts

    result: list[dict[str, Any]] = []
    for cid in names:
        meta = cstore.get_collection_meta(cid) or {}
        if not meta:
            meta = {"id": cid, "name": cid, "qdrant_name": cid}
        try:
            info = services.db.get_collection_info(cid)
            points_count = info.get("points_count", 0)
        except Exception:
            points_count = 0
        try:
            desc = sm.get_project_description(cid) or ""
        except Exception:
            desc = ""
        result.append({
            **meta,
            "archived": bool(meta.get("archived")),
            "points_count": points_count,
            "notes_count": notes_by_collection.get(cid, 0),
            "last_active": (
                last_active_by_collection[cid].isoformat()
                if cid in last_active_by_collection else None
            ),
            "catalog_definition": desc,
        })
    return result


def collection_info_view(collection: str) -> dict[str, Any]:
    """`get_collection` 工具的信封：元数据 + 脱敏 config + 统计 + 目录定义。"""
    from src.collections import store as cstore
    from src.services import services
    from src.notes import store as nstore
    from src.rag.summary_manager import SummaryManager

    if e := require_collection(collection):
        return e
    meta = cstore.get_collection_meta(collection) or {
        "id": collection, "name": collection, "qdrant_name": collection,
    }
    from src.secrets import redact_mapping

    config = services.db.get_collection_config(collection)
    config.pop("sparse_vocab", None)  # internal BM25 state, not useful to consumers
    if isinstance(config, dict):
        config = redact_mapping(config)
    try:
        info = services.db.get_collection_info(collection)
        points_count = info.get("points_count", 0)
    except Exception:
        points_count = 0
    notes_count = sum(
        1 for n in nstore.list_notes() if n.collection == collection
    )
    sm = SummaryManager(db=services.db)
    try:
        desc = sm.get_project_description(collection) or ""
    except Exception:
        desc = ""
    return ok(
        **meta,
        config=config,
        points_count=points_count,
        notes_count=notes_count,
        catalog_definition=desc,
    )


__all__ = ["collection_info_view", "collections_list_view"]
