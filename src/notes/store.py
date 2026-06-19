"""File-based storage for Notes.

Directory layout (mirrors meeting/store.py convention):
    data/notes/{collection}/{note_id}/
        meta.json           – Note metadata
        content.md          – User-authored markdown
        distillation.md     – Cached LLM distillation (single copy, source-content-keyed)
        distillation.hash   – Source content hash at cache time
        references.json     – List of injection-block references in this note
        referenced_by.json  – List of notes that reference this note (backlinks)
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import uuid
from datetime import datetime
from pathlib import Path

from .models import Note, InjectionBlock

logger = logging.getLogger("notes.store")
NOTES_DIR = Path("data").resolve() / "notes"


def _note_dir(collection: str, note_id: str) -> Path:
    return NOTES_DIR / collection / note_id


def _collection_dir(collection: str) -> Path:
    return NOTES_DIR / collection


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_json(path: Path):
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _note_to_dict(note: Note) -> dict:
    data = note.model_dump()
    data["created_at"] = note.created_at.isoformat()
    data["updated_at"] = note.updated_at.isoformat()
    return data


def _dict_to_note(data: dict) -> Note:
    if "created_at" in data and isinstance(data["created_at"], str):
        data["created_at"] = datetime.fromisoformat(data["created_at"])
    if "updated_at" in data and isinstance(data["updated_at"], str):
        data["updated_at"] = datetime.fromisoformat(data["updated_at"])
    return Note(**data)


# ── CRUD ───────────────────────────────────────────────────────


def create_note(collection: str, title: str) -> Note:
    note_id = uuid.uuid4().hex
    now = datetime.now()
    note = Note(
        id=note_id,
        title=title,
        collection=collection,
        created_at=now,
        updated_at=now,
    )
    ndir = _note_dir(collection, note_id)
    ndir.mkdir(parents=True, exist_ok=True)
    _write_json(ndir / "meta.json", _note_to_dict(note))
    (ndir / "content.md").write_text("", encoding="utf-8")
    logger.info("Created note id=%s title='%s' collection='%s'", note_id, title, collection)
    return note


def get_note(collection: str, note_id: str) -> Note | None:
    data = _read_json(_note_dir(collection, note_id) / "meta.json")
    if data is None:
        return None
    return _dict_to_note(data)


def list_notes(collection: str) -> list[Note]:
    cdir = _collection_dir(collection)
    if not cdir.exists():
        return []
    notes: list[Note] = []
    for entry in cdir.iterdir():
        if not entry.is_dir():
            continue
        data = _read_json(entry / "meta.json")
        if data is not None:
            notes.append(_dict_to_note(data))
    notes.sort(key=lambda n: n.updated_at, reverse=True)
    return notes


def update_note(collection: str, note_id: str, **fields) -> Note:
    note = get_note(collection, note_id)
    if note is None:
        raise FileNotFoundError(f"Note {note_id} not found in collection '{collection}'")
    for key, value in fields.items():
        setattr(note, key, value)
    note.updated_at = datetime.now()
    _write_json(_note_dir(collection, note_id) / "meta.json", _note_to_dict(note))
    return note


def delete_note(collection: str, note_id: str) -> bool:
    ndir = _note_dir(collection, note_id)
    if not ndir.exists():
        return False
    # Clean up references from other notes that reference this one
    refs = get_referenced_by(collection, note_id)
    for ref_note_id in refs:
        _remove_reference(collection, ref_note_id, note_id)
    shutil.rmtree(ndir)
    return True


# ── Content ────────────────────────────────────────────────────


def get_content(collection: str, note_id: str) -> str | None:
    ndir = _note_dir(collection, note_id)
    content_path = ndir / "content.md"
    if not content_path.exists():
        return None
    return content_path.read_text(encoding="utf-8")


def save_content(collection: str, note_id: str, content: str) -> str:
    ndir = _note_dir(collection, note_id)
    note = get_note(collection, note_id)
    if note is None:
        raise FileNotFoundError(f"Note {note_id} not found")
    content_path = ndir / "content.md"
    content_path.write_text(content, encoding="utf-8")
    update_note(collection, note_id)
    logger.info("Saved content for note %s (%d chars)", note_id, len(content))
    return str(content_path)


# ── Distillation caching ───────────────────────────────────────
# Single-key cache: one .md file per source note (not per target).
# The distillation result depends only on the source note content,
# so it can be shared across all targets that reference this source.
#
# Layout:
#   data/notes/{collection}/{note_id}/distillation.md    – Cached distillation
#   data/notes/{collection}/{note_id}/distillation.hash  – Source content hash at cache time


def get_distillation(collection: str, source_note_id: str) -> str | None:
    """Get cached distillation for a source note (content-hash-verified)."""
    ndir = _note_dir(collection, source_note_id)
    dist_path = ndir / "distillation.md"
    hash_path = ndir / "distillation.hash"
    if not dist_path.exists():
        return None
    # Verify source content hasn't changed since cache was written
    current_content = get_content(collection, source_note_id) or ""
    current_hash = hashlib.sha256(current_content.encode("utf-8")).hexdigest()
    if hash_path.exists():
        stored_hash = hash_path.read_text(encoding="utf-8").strip()
        if stored_hash != current_hash:
            logger.info("Distillation cache for %s is stale (content changed), discarding", source_note_id)
            dist_path.unlink()
            hash_path.unlink()
            return None
    return dist_path.read_text(encoding="utf-8")


def save_distillation(collection: str, source_note_id: str, content: str) -> None:
    """Cache a distillation result with a content hash of the source note."""
    ndir = _note_dir(collection, source_note_id)
    ndir.mkdir(parents=True, exist_ok=True)
    dist_path = ndir / "distillation.md"
    hash_path = ndir / "distillation.hash"
    dist_path.write_text(content, encoding="utf-8")
    source_content = get_content(collection, source_note_id) or ""
    content_hash = hashlib.sha256(source_content.encode("utf-8")).hexdigest()
    hash_path.write_text(content_hash, encoding="utf-8")
    logger.info("Saved distillation for %s (%d chars)", source_note_id, len(content))


def delete_distillation(collection: str, source_note_id: str) -> bool:
    """Delete the cached distillation for a source note."""
    ndir = _note_dir(collection, source_note_id)
    dist_path = ndir / "distillation.md"
    hash_path = ndir / "distillation.hash"
    deleted = False
    if dist_path.exists():
        dist_path.unlink()
        deleted = True
    if hash_path.exists():
        hash_path.unlink()
    if deleted:
        logger.info("Deleted distillation for %s", source_note_id)
    return deleted


def get_distillation_content_hash(collection: str, source_note_id: str) -> str | None:
    """Get the content hash at the time the distillation was cached."""
    hash_path = _note_dir(collection, source_note_id) / "distillation.hash"
    if not hash_path.exists():
        return None
    return hash_path.read_text(encoding="utf-8").strip()


def source_content_changed(collection: str, source_note_id: str) -> bool:
    """Check if the source note's content has changed since distillation was cached."""
    ndir = _note_dir(collection, source_note_id)
    hash_path = ndir / "distillation.hash"
    if not hash_path.exists():
        return True  # No cache at all → treat as "changed"
    current_content = get_content(collection, source_note_id) or ""
    current_hash = hashlib.sha256(current_content.encode("utf-8")).hexdigest()
    stored_hash = hash_path.read_text(encoding="utf-8").strip()
    return stored_hash != current_hash


def cleanup_distillations_if_unused(collection: str, source_note_id: str) -> None:
    """If a source note no longer has any referencers, clean up its distillation cache.
    - If content hasn't changed since caching: keep the cache (can be reused later)
    - If content has changed: delete the cache (it's stale)
    """
    refs = get_referenced_by(collection, source_note_id)
    if refs:  # Still has referencers — don't clean up
        return

    # No more referencers — decide whether to keep or delete cache
    ndir = _note_dir(collection, source_note_id)
    dist_path = ndir / "distillation.md"
    if not dist_path.exists():
        return  # No cache to clean up

    if source_content_changed(collection, source_note_id):
        delete_distillation(collection, source_note_id)
        logger.info("Cleared stale distillation for %s (content changed after last reference removed)", source_note_id)
    else:
        logger.info("Preserved distillation for %s (content unchanged, cache may be reused)", source_note_id)


# ── References (injection blocks within a note) ────────────────


def get_references(collection: str, note_id: str) -> list[dict]:
    """Get the list of injection block references in this note.
    Each ref: {block_id, source_note_id, source_title}"""
    refs = _read_json(_note_dir(collection, note_id) / "references.json")
    return refs if isinstance(refs, list) else []


def save_references(collection: str, note_id: str, refs: list[dict]) -> None:
    _write_json(_note_dir(collection, note_id) / "references.json", refs)


def add_reference(collection: str, note_id: str, block_id: str, source_note_id: str, source_title: str) -> None:
    refs = get_references(collection, note_id)
    refs.append({
        "block_id": block_id,
        "source_note_id": source_note_id,
        "source_title": source_title,
    })
    save_references(collection, note_id, refs)
    # Also update the source's referenced_by
    _add_referenced_by(collection, source_note_id, note_id)


def remove_reference_by_block(collection: str, note_id: str, block_id: str) -> None:
    """Remove a specific injection block reference."""
    refs = get_references(collection, note_id)
    removed = [r for r in refs if r.get("block_id") == block_id]
    refs = [r for r in refs if r.get("block_id") != block_id]
    save_references(collection, note_id, refs)
    # Check if any remaining refs point to the same source
    for r in removed:
        source_id = r.get("source_note_id")
        if source_id and not any(rr.get("source_note_id") == source_id for rr in refs):
            _remove_referenced_by(collection, source_id, note_id)


# ── Referenced By (which notes reference this note) ────────────


def get_referenced_by(collection: str, note_id: str) -> list[str]:
    """Get list of note IDs that contain injection blocks from this note."""
    refs = _read_json(_note_dir(collection, note_id) / "referenced_by.json")
    return refs if isinstance(refs, list) else []


def _add_referenced_by(collection: str, source_note_id: str, target_note_id: str) -> None:
    refs = get_referenced_by(collection, source_note_id)
    if target_note_id not in refs:
        refs.append(target_note_id)
        _write_json(_note_dir(collection, source_note_id) / "referenced_by.json", refs)


def _remove_referenced_by(collection: str, source_note_id: str, target_note_id: str) -> None:
    refs = get_referenced_by(collection, source_note_id)
    if target_note_id in refs:
        refs.remove(target_note_id)
        _write_json(_note_dir(collection, source_note_id) / "referenced_by.json", refs)
        # If no more referencers, clean up stale distillation cache
        if not refs:
            cleanup_distillations_if_unused(collection, source_note_id)


def _remove_reference(collection: str, note_id: str, source_note_id: str) -> None:
    """Remove all injection blocks in note_id that reference source_note_id."""
    refs = get_references(collection, note_id)
    refs = [r for r in refs if r.get("source_note_id") != source_note_id]
    save_references(collection, note_id, refs)


# ── Propagation chain detection ────────────────────────────────


def build_propagation_chain(collection: str, note_id: str, visited: set[str] | None = None) -> list[dict]:
    """Recursively build the propagation chain starting from note_id.
    Returns a list of {source_id, source_title, target_id, target_title} links."""
    if visited is None:
        visited = set()
    if note_id in visited:
        return []
    visited.add(note_id)

    links = []
    referenced_by = get_referenced_by(collection, note_id)
    note = get_note(collection, note_id)
    source_title = note.title if note else note_id

    for target_id in referenced_by:
        if target_id in visited:
            continue
        target = get_note(collection, target_id)
        target_title = target.title if target else target_id
        links.append({
            "source_id": note_id,
            "source_title": source_title,
            "target_id": target_id,
            "target_title": target_title,
        })
        # Recurse — chain propagation
        sub_links = build_propagation_chain(collection, target_id, visited)
        links.extend(sub_links)

    return links
