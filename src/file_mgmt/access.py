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
    from src.collections.store import get_collection_meta

    if not get_collection_meta(collection_id):
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
