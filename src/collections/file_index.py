"""Per-collection file metadata index.

Stores lightweight metadata for every ingested file/note/meeting
in ``data/collections/{collection_id}/files.json`` so the file
list does not require a full Qdrant scroll.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

COLLECTIONS_DIR = Path("data").resolve() / "collections"
_LOCK = threading.Lock()


def _index_path(collection_id: str) -> Path:
    """Return the path to files.json for *collection_id*."""
    return COLLECTIONS_DIR / collection_id / "files.json"


def _files_dir(collection_id: str) -> Path:
    """Return the files snapshot directory for *collection_id*."""
    return COLLECTIONS_DIR / collection_id / "files"


# ── public API ──────────────────────────────────────────────────────


def load(collection_id: str) -> dict[str, dict]:
    """Return the files index dict, or ``{}`` if missing."""
    path = _index_path(collection_id)
    try:
        if path.is_file():
            return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        logger.warning("[FileIndex] corrupt files.json for %s, resetting", collection_id)
    return {}


def save(collection_id: str, data: dict[str, dict]) -> None:
    """Atomically write the files index."""
    path = _index_path(collection_id)
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        tmp.replace(path)


def add(collection_id: str, file_id: str, source: str, source_label: str, file_type: str, chunks: int, original_ext: str | None = None) -> None:
    """Add or update a single entry."""
    data = load(collection_id)
    import time
    entry: dict = {
        "source": source,
        "source_label": source_label,
        "file_type": file_type,
        "ingested_at": time.time(),
        "chunks": chunks,
    }
    if original_ext:
        entry["original_ext"] = original_ext
    data[file_id] = entry
    save(collection_id, data)


def remove(collection_id: str, file_id: str) -> bool:
    """Remove a single entry. Returns True if it existed."""
    data = load(collection_id)
    if file_id in data:
        del data[file_id]
        save(collection_id, data)
        return True
    return False


def remove_by_source(collection_id: str, source: str) -> str | None:
    """Remove all entries matching *source*. Returns the first file_id removed."""
    data = load(collection_id)
    to_remove = [fid for fid, entry in data.items() if entry.get("source") == source]
    for fid in to_remove:
        del data[fid]
    if to_remove:
        save(collection_id, data)
        return to_remove[0]
    return None


def ensure_files_dir(collection_id: str, file_id: str) -> Path:
    """Create ``collections/{id}/files/{file_id}/`` and return the Path."""
    d = _files_dir(collection_id) / file_id
    d.mkdir(parents=True, exist_ok=True)
    return d
