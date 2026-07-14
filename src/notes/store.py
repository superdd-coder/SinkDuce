"""File-based storage for Notes.

Directory layout (flat — notes are globally unique by UUID):
    data/notes/{note_id}/
        meta.json           – Note metadata (includes collection field)
        content.md          – User-authored markdown
        distillation.md     – Cached LLM distillation
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
from datetime import datetime, timezone
from pathlib import Path

from .models import Note

logger = logging.getLogger("notes.store")
NOTES_DIR = Path("data").resolve() / "notes"


def _note_dir(note_id: str) -> Path:
    """Return the directory for a note. Note IDs are globally unique UUIDs."""
    return NOTES_DIR / note_id


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
        dt = datetime.fromisoformat(data["created_at"])
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        data["created_at"] = dt
    if "updated_at" in data and isinstance(data["updated_at"], str):
        dt = datetime.fromisoformat(data["updated_at"])
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        data["updated_at"] = dt
    return Note(**data)


def _find_note_dir(note_id: str) -> Path | None:
    """Find a note directory, trying both flat and legacy paths."""
    # Flat path (current)
    ndir = NOTES_DIR / note_id
    if ndir.is_dir():
        return ndir
    # Legacy: search collection subdirectories
    if NOTES_DIR.is_dir():
        for col_dir in NOTES_DIR.iterdir():
            if col_dir.is_dir():
                candidate = col_dir / note_id
                if candidate.is_dir():
                    return candidate
    return None


# ── CRUD ───────────────────────────────────────────────────────


def create_note(collection: str, title: str) -> Note:
    note_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    note = Note(
        id=note_id,
        title=title,
        collection=collection,
        created_at=now,
        updated_at=now,
    )
    ndir = _note_dir(note_id)
    ndir.mkdir(parents=True, exist_ok=True)
    _write_json(ndir / "meta.json", _note_to_dict(note))
    (ndir / "content.md").write_text("", encoding="utf-8")
    logger.info("Created note id=%s title='%s' collection='%s'", note_id, title, collection)
    return note


def get_note(note_id: str) -> Note | None:
    ndir = _find_note_dir(note_id)
    if ndir is None:
        return None
    data = _read_json(ndir / "meta.json")
    if data is None:
        return None
    return _dict_to_note(data)


def list_notes(collection: str | None = None) -> list[Note]:
    """List notes, optionally filtered by collection."""
    if not NOTES_DIR.exists():
        return []
    notes: list[Note] = []
    for entry in NOTES_DIR.iterdir():
        if not entry.is_dir():
            continue
        data = _read_json(entry / "meta.json")
        if data is not None:
            note = _dict_to_note(data)
            if collection is None or note.collection == collection:
                notes.append(note)
    notes.sort(key=lambda n: n.updated_at, reverse=True)
    return notes


def update_note(note_id: str, **fields) -> Note:
    ndir = _find_note_dir(note_id)
    if ndir is None:
        raise FileNotFoundError(f"Note {note_id} not found")
    data = _read_json(ndir / "meta.json")
    if data is None:
        raise FileNotFoundError(f"Note {note_id} has no meta.json")
    note = _dict_to_note(data)
    for key, value in fields.items():
        setattr(note, key, value)
    note.updated_at = datetime.now(timezone.utc)
    _write_json(ndir / "meta.json", _note_to_dict(note))
    return note


def delete_note(note_id: str) -> bool:
    ndir = _find_note_dir(note_id)
    if ndir is None:
        return False
    # Clean up references from other notes that reference this one
    refs = get_referenced_by(note_id)
    for ref_note_id in refs:
        _remove_reference(ref_note_id, note_id)
    shutil.rmtree(ndir)
    return True


# ── Content ────────────────────────────────────────────────────


def get_content(note_id: str) -> str | None:
    ndir = _find_note_dir(note_id)
    if ndir is None:
        return None
    content_path = ndir / "content.md"
    if not content_path.exists():
        return None
    return content_path.read_text(encoding="utf-8")


def save_content(note_id: str, content: str) -> str:
    ndir = _find_note_dir(note_id)
    if ndir is None:
        raise FileNotFoundError(f"Note {note_id} not found")
    content_path = ndir / "content.md"
    content_path.write_text(content, encoding="utf-8")
    update_note(note_id)
    logger.info("Saved content for note %s (%d chars)", note_id, len(content))
    return str(content_path)


# ── Distillation caching ───────────────────────────────────────


def get_distillation(source_note_id: str) -> str | None:
    ndir = _find_note_dir(source_note_id)
    if ndir is None:
        return None
    dist_path = ndir / "distillation.md"
    if not dist_path.exists():
        return None
    return dist_path.read_text(encoding="utf-8")


def save_distillation(source_note_id: str, content: str) -> None:
    ndir = _find_note_dir(source_note_id)
    if ndir is None:
        raise FileNotFoundError(f"Note {source_note_id} not found")
    ndir.mkdir(parents=True, exist_ok=True)
    dist_path = ndir / "distillation.md"
    hash_path = ndir / "distillation.hash"
    dist_path.write_text(content, encoding="utf-8")
    source_content = get_content(source_note_id) or ""
    content_hash = hashlib.sha256(source_content.encode("utf-8")).hexdigest()
    hash_path.write_text(content_hash, encoding="utf-8")
    logger.info("Saved distillation for %s (%d chars)", source_note_id, len(content))


def delete_distillation(source_note_id: str) -> bool:
    ndir = _find_note_dir(source_note_id)
    if ndir is None:
        return False
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


def source_content_changed(source_note_id: str) -> bool:
    ndir = _find_note_dir(source_note_id)
    if ndir is None:
        return True
    hash_path = ndir / "distillation.hash"
    if not hash_path.exists():
        return True
    current_content = get_content(source_note_id) or ""
    current_hash = hashlib.sha256(current_content.encode("utf-8")).hexdigest()
    stored_hash = hash_path.read_text(encoding="utf-8").strip()
    return stored_hash != current_hash


def cleanup_distillations_if_unused(source_note_id: str) -> None:
    refs = get_referenced_by(source_note_id)
    if refs:
        return
    ndir = _find_note_dir(source_note_id)
    if ndir is None:
        return
    dist_path = ndir / "distillation.md"
    if not dist_path.exists():
        return
    if source_content_changed(source_note_id):
        delete_distillation(source_note_id)
        logger.info("Cleared stale distillation for %s", source_note_id)
    else:
        logger.info("Preserved distillation for %s", source_note_id)


# ── References ─────────────────────────────────────────────────


def get_references(note_id: str) -> list[dict]:
    ndir = _find_note_dir(note_id)
    if ndir is None:
        return []
    refs = _read_json(ndir / "references.json")
    return refs if isinstance(refs, list) else []


def save_references(note_id: str, refs: list[dict]) -> None:
    ndir = _find_note_dir(note_id)
    if ndir is None:
        raise FileNotFoundError(f"Note {note_id} not found")
    _write_json(ndir / "references.json", refs)


# ── Referenced By ──────────────────────────────────────────────


def get_referenced_by(note_id: str) -> list[str]:
    ndir = _find_note_dir(note_id)
    if ndir is None:
        return []
    refs = _read_json(ndir / "referenced_by.json")
    return refs if isinstance(refs, list) else []


def _add_referenced_by(source_note_id: str, target_note_id: str) -> None:
    refs = get_referenced_by(source_note_id)
    if target_note_id not in refs:
        refs.append(target_note_id)
        ndir = _find_note_dir(source_note_id)
        if ndir:
            _write_json(ndir / "referenced_by.json", refs)


def _remove_referenced_by(source_note_id: str, target_note_id: str) -> None:
    refs = get_referenced_by(source_note_id)
    if target_note_id in refs:
        refs.remove(target_note_id)
        ndir = _find_note_dir(source_note_id)
        if ndir:
            _write_json(ndir / "referenced_by.json", refs)
            if not refs:
                cleanup_distillations_if_unused(source_note_id)


def _remove_reference(note_id: str, source_note_id: str) -> None:
    refs = get_references(note_id)
    refs = [r for r in refs if r.get("source_note_id") != source_note_id]
    save_references(note_id, refs)


# ── Propagation ────────────────────────────────────────────────


def build_propagation_chain(note_id: str, visited: set[str] | None = None) -> list[dict]:
    if visited is None:
        visited = set()
    if note_id in visited:
        return []
    visited.add(note_id)

    links = []
    referenced_by = get_referenced_by(note_id)
    note = get_note(note_id)
    source_title = note.title if note else note_id

    for target_id in referenced_by:
        if target_id in visited:
            continue
        target = get_note(target_id)
        target_title = target.title if target else target_id
        links.append({
            "source_id": note_id,
            "source_title": source_title,
            "target_id": target_id,
            "target_title": target_title,
        })
        sub_links = build_propagation_chain(target_id, visited)
        links.extend(sub_links)

    return links
