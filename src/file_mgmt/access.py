"""Shared DB / identity helpers for file_mgmt service modules.

Moved out of service.py so domain modules (todos, later files/timeline)
can open a collection DB without importing the facade (no cycles).
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException

from src.file_mgmt.store import get_db
from src.identity import authorize, get_actor


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_collection(collection_id: str) -> None:
    """库存在性 + **可见性**校验（RBAC，2026-09-16）。

    两者合并为同一个 404：不可见与不存在**必须不可区分**——否则 403/404 的差异
    会变成"这个库存在但你没权限"的可枚举信息泄露。

    放在这里而不是逐个路由：``_open_db`` 是 file_mgmt 全部读写的公共入口（68 处
    调用），一处覆盖远胜于几十处各判一套。
    """
    from src.collections.store import get_collection_meta

    if not get_collection_meta(collection_id):
        raise HTTPException(
            status_code=404,
            detail=f"Collection '{collection_id}' not found",
        )
    from src.identity import get_actor
    from src.identity import visibility as _visibility

    try:
        visible = _visibility.is_collection_visible(get_actor(), collection_id)
    except Exception:
        # fail-closed：可见性判定失败 → 当作不可见（宁拒绝不放行）
        visible = False
    if not visible:
        raise HTTPException(
            status_code=404,
            detail=f"Collection '{collection_id}' not found",
        )


def _actor_for(action: str, collection_id: str, **resource):
    actor = get_actor()
    authorize(actor, action, {"collection_id": collection_id, **resource})
    return actor


def _actor_id() -> str:
    return get_actor().id


def _open_db(collection_id: str):
    _validate_collection(collection_id)
    from src.file_mgmt.store import init_collection_db
    init_collection_db(collection_id)  # idempotent: creates, backfills, migrates
    return get_db(collection_id)


def _main_chain_id(conn) -> str:
    row = conn.execute(
        "SELECT chain_id FROM chains WHERE parent_chain_id IS NULL"
    ).fetchone()
    if not row:
        raise HTTPException(500, "Main chain not found")
    return row["chain_id"]
