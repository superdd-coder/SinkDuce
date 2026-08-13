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
    """Return the raw files.json dict (mutators / old-library cleanup only)."""
    path = _index_path(collection_id)
    try:
        if path.is_file():
            return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        logger.warning("[FileIndex] corrupt files.json for %s, resetting", collection_id)
    return {}


def _sqlite_index_entries(collection_id: str) -> dict[str, dict]:
    """Current files from meta.db, keyed by file_id. Empty if DB missing."""
    if not collection_id:
        return {}
    db_path = COLLECTIONS_DIR / collection_id / "meta.db"
    if not db_path.is_file():
        return {}
    try:
        from src.file_mgmt.store import get_db

        conn = get_db(collection_id)
        try:
            rows = conn.execute(
                """SELECT f.file_id AS file_id,
                          fv.storage_file_id AS storage_name
                   FROM files f
                   LEFT JOIN file_versions fv
                     ON fv.version_id = f.current_version_id"""
            ).fetchall()
        finally:
            conn.close()
    except Exception:
        logger.debug(
            "sqlite index overlay failed for %s", collection_id, exc_info=True
        )
        return {}

    out: dict[str, dict] = {}
    for row in rows:
        fid = str(row["file_id"] if hasattr(row, "keys") else row[0])
        name = str(
            (row["storage_name"] if hasattr(row, "keys") else row[1]) or ""
        ).strip()
        ext = Path(name).suffix.lower().lstrip(".") if name else ""
        entry: dict = {
            "source": f"__file__:{fid}",
            "source_label": name,
            "file_type": "file",
        }
        if ext:
            entry["original_ext"] = ext
        out[fid] = entry
    return out


def load_for_read(collection_id: str) -> dict[str, dict]:
    """Display/list index: JSON fallback overlaid by SQLite current files.

    Regular ``__file__:`` rows take the current storage name from SQLite.
    ``__note__:`` / ``__meeting__:`` JSON source + label are kept.
    Files that exist only in SQLite (post stop-write) appear here.
    """
    idx = dict(load(collection_id) or {})
    for fid, sql in _sqlite_index_entries(collection_id).items():
        prev = dict(idx.get(fid) or {})
        src = (prev.get("source") or "").strip()
        if src.startswith("__note__:") or src.startswith("__meeting__:"):
            if not (prev.get("source_label") or "").strip() and sql.get("source_label"):
                prev["source_label"] = sql["source_label"]
            prev["source"] = src
            if not prev.get("file_type"):
                prev["file_type"] = "note" if src.startswith("__note__:") else "meeting"
            if sql.get("original_ext") and not prev.get("original_ext"):
                prev["original_ext"] = sql["original_ext"]
        else:
            prev["source"] = f"__file__:{fid}"
            if sql.get("source_label"):
                prev["source_label"] = sql["source_label"]
            prev["file_type"] = prev.get("file_type") or "file"
            if sql.get("original_ext"):
                prev["original_ext"] = sql["original_ext"]
        if "ingested_at" not in prev:
            prev["ingested_at"] = 0
        if "chunks" not in prev:
            prev["chunks"] = prev.get("chunks", 0)
        idx[fid] = prev
    return idx


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


def update_source_label(
    collection_id: str,
    file_id: str,
    source_label: str,
    *,
    original_ext: str | None = None,
    source: str | None = None,
) -> None:
    """Patch display label without resetting chunks / ingested_at.

    Used by Library rename so Recall / All Files stay aligned with SQLite
    ``file_versions.storage_file_id``.
    """
    label = (source_label or "").strip()
    if not label or not file_id:
        return
    data = load(collection_id)
    entry = dict(data.get(file_id) or {})
    entry["source_label"] = label
    if source:
        entry["source"] = source
    elif not entry.get("source"):
        entry["source"] = f"__file__:{file_id}"
    if original_ext is not None:
        if original_ext:
            entry["original_ext"] = original_ext
        else:
            entry.pop("original_ext", None)
    data[file_id] = entry
    save(collection_id, data)


def current_storage_filename(collection_id: str, file_id: str) -> str | None:
    """Return current version blob basename from SQLite, or None if unavailable."""
    if not collection_id or not file_id:
        return None
    try:
        from src.file_mgmt.store import get_db

        conn = get_db(collection_id)
        try:
            row = conn.execute(
                """SELECT fv.storage_file_id AS name
                   FROM files f
                   JOIN file_versions fv ON fv.version_id = f.current_version_id
                   WHERE f.file_id=?""",
                (file_id,),
            ).fetchone()
            if not row:
                return None
            name = (row["name"] if hasattr(row, "keys") else row[0]) or ""
            name = str(name).strip()
            return name or None
        finally:
            conn.close()
    except Exception:
        logger.debug(
            "current_storage_filename failed col=%s file=%s",
            collection_id,
            file_id,
            exc_info=True,
        )
        return None


def _basename(path_or_name: str) -> str:
    s = (path_or_name or "").replace("\\", "/").strip()
    if not s:
        return ""
    return s.rsplit("/", 1)[-1]


def is_opaque_source_key(value: str | None) -> bool:
    """True when *value* is a technical source id, not a human file name."""
    s = (value or "").strip()
    if not s:
        return True
    if s.startswith(("__file__:", "__meeting__:", "__note__:", "file:")):
        return True
    # bare 32-char hex (file_id) — not a display name
    if len(s) == 32 and all(c in "0123456789abcdef" for c in s.lower()):
        return True
    return False


# Generic placeholders — not useful as display_name (UI should keep looking)
_GENERIC_PLACEHOLDERS = frozenset(
    {
        "document",
        "meeting",
        "note",
        "unknown",
        "untitled",
        "file",
    }
)


def is_generic_display_name(value: str | None) -> bool:
    """True for empty, opaque, or last-resort placeholders like 'Document'."""
    s = (value or "").strip()
    if not s or is_opaque_source_key(s):
        return True
    low = s.lower()
    if low in _GENERIC_PLACEHOLDERS:
        return True
    # "Document (a1824f77)" style
    if low.startswith("document (") and low.endswith(")"):
        return True
    return False


def _human_label(value: str | None) -> str | None:
    """Return *value* only if it looks like a real display name."""
    s = (value or "").strip()
    if not s or is_opaque_source_key(s):
        return None
    # Strip common "Meeting: " / "Note: " prefixes for cleaner UI
    if s.startswith("Meeting: "):
        s = s[len("Meeting: ") :].strip()
    elif s.startswith("Note: "):
        s = s[len("Note: ") :].strip()
    if not s or is_generic_display_name(s):
        return None
    return s


def _label_from_index_entry(entry: dict | None) -> str | None:
    if not entry:
        return None
    return _human_label(entry.get("source_label"))


def _lookup_index_by_source(
    idx: dict[str, dict], source: str
) -> dict | None:
    """Find files.json entry by stable source key or file_id key."""
    src = (source or "").strip()
    if not src or not idx:
        return None
    # Direct file_id key for __file__:{id}
    if src.startswith("__file__:"):
        fid = src[len("__file__:") :].strip()
        if fid and fid in idx:
            return idx[fid]
    if src in idx:
        return idx[src]
    for _fid, entry in idx.items():
        if (entry.get("source") or "").strip() == src:
            return entry
    return None


def _meeting_display_from_store(source: str) -> str | None:
    """Resolve ``__meeting__:{id}:{tab}`` via meeting meta (cross-collection)."""
    if not source.startswith("__meeting__:"):
        return None
    rest = source[len("__meeting__:") :]
    if ":" in rest:
        meeting_id, tab_id = rest.split(":", 1)
    else:
        meeting_id, tab_id = rest, "tab_general"
    meeting_id = (meeting_id or "").strip()
    tab_id = (tab_id or "tab_general").strip() or "tab_general"
    if not meeting_id:
        return None
    try:
        from src.notes.service import build_meeting_tab_distill_source

        title, _md, _speakers = build_meeting_tab_distill_source(
            meeting_id, tab_id
        )
        return _human_label(title)
    except Exception:
        try:
            from src.meeting.store import get_meeting

            m = get_meeting(meeting_id)
            if m and (m.title or "").strip():
                return (m.title or "").strip()
        except Exception:
            logger.debug(
                "meeting display resolve failed for %s", source, exc_info=True
            )
    return None


def _note_display_from_store(source: str) -> str | None:
    if not source.startswith("__note__:"):
        return None
    note_id = source[len("__note__:") :].strip()
    if not note_id:
        return None
    try:
        from src.notes import store as notes_store

        note = notes_store.get_note(note_id)
        if note and (note.title or "").strip():
            return (note.title or "").strip()
    except Exception:
        logger.debug("note display resolve failed for %s", source, exc_info=True)
    return None


def resolve_display_name(
    collection_id: str,
    source: str,
    *,
    payload_label: str | None = None,
    index: dict[str, dict] | None = None,
) -> str:
    """Human-readable name for a document ``source`` key.

    Preference for managed ``__file__:{id}`` files:
      1. SQLite current version ``storage_file_id`` (canonical after rename/version)
      2. ``files.json`` ``source_label``
      3. chunk payload ``source_label`` (ingest-time snapshot) — never opaque keys
      4. short fallback (never raw ``__file__:…`` / ``__meeting__:…``)

    Notes / meetings: index label → meeting/note store title → payload.
    """
    src = (source or "").strip()
    payload = _human_label(payload_label)

    if not src:
        return payload or "Unknown"

    idx = index if index is not None else (
        load_for_read(collection_id) if collection_id else {}
    )

    # ── Managed files ──────────────────────────────────────────────
    if src.startswith("__file__:") or src.startswith("file:"):
        prefix = "__file__:" if src.startswith("__file__:") else "file:"
        file_id = src[len(prefix) :].strip()
        if file_id and collection_id:
            cur = _human_label(current_storage_filename(collection_id, file_id))
            if cur:
                return cur
        entry = _lookup_index_by_source(
            idx, src if src.startswith("__file__:") else f"__file__:{file_id}"
        )
        lab = _label_from_index_entry(entry)
        if lab:
            return lab
        # Also try bare file_id key in index (some older indexes)
        if file_id and file_id in idx:
            lab = _label_from_index_entry(idx[file_id])
            if lab:
                return lab
        if payload:
            return payload
        # Empty — callers / UI fall back to other maps; never invent "Document"
        return ""

    # ── Index hit for notes / meetings / legacy paths ──────────────
    entry = _lookup_index_by_source(idx, src)
    lab = _label_from_index_entry(entry)
    if lab:
        return lab

    # ── Domain stores (works even when files.json is missing) ──────
    if src.startswith("__meeting__:"):
        mtitle = _meeting_display_from_store(src)
        if mtitle:
            return mtitle
        if payload:
            return payload
        return ""

    if src.startswith("__note__:"):
        ntitle = _note_display_from_store(src)
        if ntitle:
            return ntitle
        if payload:
            return payload
        return ""

    # ── Legacy path / plain filename sources ───────────────────────
    if payload:
        return payload
    base = _basename(src)
    if base and not is_opaque_source_key(base) and not is_generic_display_name(base):
        return base
    return ""


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
