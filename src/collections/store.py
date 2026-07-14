"""Collection metadata storage.

Stores collection metadata (id, name, created_at) separately from Qdrant.
File: data/collections/{id}/meta.json
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("collections.store")
COLLECTIONS_DIR = Path("data").resolve() / "collections"


def _meta_path(collection_id: str) -> Path:
    return COLLECTIONS_DIR / collection_id / "meta.json"


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def generate_id() -> str:
    """Generate a unique collection ID."""
    return f"col_{uuid.uuid4().hex[:12]}"


def create_collection_meta(collection_id: str, name: str, qdrant_name: str = None) -> dict:
    """Create collection metadata."""
    meta = {
        "id": collection_id,
        "name": name,
        "qdrant_name": qdrant_name or name,  # Original Qdrant collection name
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_json(_meta_path(collection_id), meta)
    logger.info("Created collection metadata: %s (%s)", name, collection_id)
    return meta


def find_collection_by_qdrant_name(qdrant_name: str) -> dict | None:
    """Find collection by Qdrant name (original name)."""
    for meta in list_collections_meta():
        if meta.get("qdrant_name") == qdrant_name:
            return meta
    return None


def get_collection_meta(collection_id: str) -> dict | None:
    """Get collection metadata by ID."""
    return _read_json(_meta_path(collection_id))


def update_collection_meta(collection_id: str, **kwargs) -> dict | None:
    """Update collection metadata."""
    meta = get_collection_meta(collection_id)
    if not meta:
        return None

    meta.update(kwargs)
    meta["updated_at"] = datetime.now(timezone.utc).isoformat()
    _write_json(_meta_path(collection_id), meta)
    logger.info("Updated collection metadata: %s", collection_id)
    return meta


def list_collections_meta() -> list[dict]:
    """List all collection metadata."""
    result = []
    if not COLLECTIONS_DIR.exists():
        return result

    for path in COLLECTIONS_DIR.iterdir():
        if path.is_dir():
            meta = _read_json(path / "meta.json")
            if meta:
                result.append(meta)

    return sorted(result, key=lambda x: x.get("created_at", ""))


def delete_collection_meta(collection_id: str) -> bool:
    """Delete collection metadata."""
    import shutil
    meta_dir = COLLECTIONS_DIR / collection_id
    if meta_dir.exists():
        shutil.rmtree(meta_dir)
        logger.info("Deleted collection metadata: %s", collection_id)
        return True
    return False


def find_collection_by_name(name: str) -> dict | None:
    """Find collection by display name."""
    for meta in list_collections_meta():
        if meta.get("name") == name:
            return meta
    return None
