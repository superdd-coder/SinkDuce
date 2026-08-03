"""On-disk layout for managed file versions.

Layout (v2)::

    data/collections/{collection_id}/files/{file_id}/{version_id}/{basename}
    data/collections/{collection_id}/files/{file_id}/{version_id}/parsed.txt
    data/collections/{collection_id}/files/{file_id}/{version_id}/images/…

Legacy (flat, still readable)::

    data/collections/{collection_id}/files/{file_id}/{basename}
    data/collections/{collection_id}/files/{file_id}/parsed.txt
    data/collections/{collection_id}/files/{file_id}/images/…

Identity layers (product language):

- ``file_id`` — managed file handle (paths, pin, nodes, messages). Stable.
- ``version_id`` — one content snapshot of that managed file.
- ``version_no`` (DB) — UI label v1/v2; never used as folder name.
- ``storage_file_id`` (DB) — basename of the blob (display / extension).

Version control flags (current, archived, commit message) live only in SQLite.
"""

from __future__ import annotations

import logging
import shutil
import uuid
from pathlib import Path

logger = logging.getLogger("file_mgmt.storage_paths")

COLLECTIONS_DIR = Path("data").resolve() / "collections"

# Process-local: collections whose disk layout migration already ran.
_layout_migrated: set[str] = set()
# Process-local: collections whose storage_file_id label repair already ran.
_storage_labels_repaired: set[str] = set()


def files_dir(collection_id: str) -> Path:
    return COLLECTIONS_DIR / collection_id / "files"


def managed_file_dir(collection_id: str, file_id: str) -> Path:
    return files_dir(collection_id) / file_id


def version_dir(collection_id: str, file_id: str, version_id: str) -> Path:
    """Directory for one version's blob + sidecars."""
    return managed_file_dir(collection_id, file_id) / version_id


def storage_basename(storage_file_id: str | None) -> str:
    """Basename used for display and for the blob file name."""
    if not storage_file_id:
        return ""
    # Allow accidental relative paths in DB; always take final component.
    return Path(str(storage_file_id).replace("\\", "/")).name


def _is_content_blob_name(name: str) -> bool:
    """True if *name* looks like a real content file (not a sidecar / noise)."""
    if not name or name.startswith("."):
        return False
    if name in {"parsed.txt"}:
        return False
    if name.endswith(".extracted.txt"):
        return False
    return True


def _iter_content_blobs(directory: Path) -> list[Path]:
    """List content files in *directory* (non-recursive)."""
    if not directory.is_dir():
        return []
    out: list[Path] = []
    try:
        for f in sorted(directory.iterdir()):
            if f.is_file() and _is_content_blob_name(f.name):
                out.append(f)
    except OSError:
        return []
    return out


def discover_content_blob(
    collection_id: str,
    file_id: str,
    version_id: str | None = None,
) -> Path | None:
    """Find a content blob when DB storage_file_id does not match disk.

    Used for note/meeting snapshots (flat ``{title}.md``) and when migration
    stored a display label instead of the real basename.
    """
    root = managed_file_dir(collection_id, file_id)
    if not root.is_dir():
        return None
    if version_id:
        found = _iter_content_blobs(root / version_id)
        if found:
            return found[0]
    # Flat layout under file_id (notes/meetings ingest)
    found = _iter_content_blobs(root)
    if found:
        return found[0]
    # Any version subdir (prefer when version_id unknown)
    if not version_id:
        try:
            for child in sorted(root.iterdir()):
                if not child.is_dir():
                    continue
                found = _iter_content_blobs(child)
                if found:
                    return found[0]
        except OSError:
            pass
    return None


def resolve_version_blob(
    collection_id: str,
    file_id: str,
    version_id: str | None,
    storage_file_id: str | None,
) -> Path | None:
    """Locate the on-disk blob for a version (new layout, then legacy flat).

    Returns a Path only when the file exists.
    """
    name = storage_basename(storage_file_id)
    root = managed_file_dir(collection_id, file_id)
    if not root.is_dir() and not name and not version_id:
        return None

    candidates: list[Path] = []

    if version_id and name:
        candidates.append(root / version_id / name)
    if version_id and storage_file_id and "/" in str(storage_file_id).replace("\\", "/"):
        # Relative path under file_id (e.g. "{version_id}/{name}")
        rel = str(storage_file_id).replace("\\", "/").lstrip("/")
        candidates.append(root / rel)
    if name:
        candidates.append(root / name)  # legacy flat
    if version_id:
        # Any content file under this version dir (DB name may be a display label)
        for f in _iter_content_blobs(root / version_id):
            if not name or f.name == name:
                candidates.append(f)
            else:
                candidates.append(f)

    seen: set[str] = set()
    for p in candidates:
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        try:
            if p.is_file():
                return p
        except OSError:
            continue

    # Note/meeting snapshots + bad storage_file_id (label instead of basename)
    return discover_content_blob(collection_id, file_id, version_id)


def expected_version_blob_path(
    collection_id: str,
    file_id: str,
    version_id: str,
    storage_file_id: str | None,
) -> Path:
    """Canonical path for a *new* write (may not exist yet)."""
    name = storage_basename(storage_file_id) or "upload.bin"
    return version_dir(collection_id, file_id, version_id) / name


def write_version_blob(
    collection_id: str,
    file_id: str,
    version_id: str,
    file_bytes: bytes,
    filename: str,
) -> tuple[Path, str]:
    """Write bytes under ``files/{file_id}/{version_id}/{basename}``.

    Returns ``(absolute_path, storage_basename)`` for DB ``storage_file_id``.
    """
    safe_name = Path(filename).name
    if not safe_name:
        from fastapi import HTTPException

        raise HTTPException(400, "Invalid filename")
    if safe_name in {"parsed.txt"} or safe_name.endswith(".extracted.txt"):
        from fastapi import HTTPException

        raise HTTPException(400, f"Invalid storage name: {safe_name}")

    vdir = version_dir(collection_id, file_id, version_id)
    vdir.mkdir(parents=True, exist_ok=True)
    save_path = vdir / safe_name
    if save_path.exists():
        stem, suf = save_path.stem, save_path.suffix
        save_path = vdir / f"{stem}_{uuid.uuid4().hex[:8]}{suf}"
        safe_name = save_path.name
    save_path.write_bytes(file_bytes)
    return save_path, safe_name


def version_blob_exists(
    collection_id: str,
    file_id: str,
    version_id: str | None,
    storage_file_id: str | None,
) -> bool:
    return resolve_version_blob(
        collection_id, file_id, version_id, storage_file_id
    ) is not None


def delete_version_storage(
    collection_id: str,
    file_id: str,
    version_id: str,
    storage_file_id: str | None,
) -> None:
    """Remove one version's directory (preferred) or legacy flat blob."""
    vdir = version_dir(collection_id, file_id, version_id)
    if vdir.is_dir():
        try:
            shutil.rmtree(vdir)
            return
        except OSError:
            logger.warning("Could not rmtree version dir %s", vdir)

    # Legacy flat: only delete the named blob + matching extract caches
    name = storage_basename(storage_file_id)
    if not name:
        return
    root = managed_file_dir(collection_id, file_id)
    blob = root / name
    if blob.is_file():
        try:
            blob.unlink()
        except OSError:
            logger.warning("Could not delete legacy blob %s", blob)
    for pattern in (f"{name}.extracted.txt",):
        p = root / pattern
        if p.is_file():
            try:
                p.unlink()
            except OSError:
                pass
    # version-scoped extract under flat dir
    if version_id:
        scoped = root / f"{name}.{version_id[:12]}.extracted.txt"
        if scoped.is_file():
            try:
                scoped.unlink()
            except OSError:
                pass


def rename_version_blob_on_disk(
    collection_id: str,
    file_id: str,
    version_id: str,
    old_storage: str,
    new_storage: str,
) -> None:
    """Rename blob file when display name changes (same version)."""
    old_name = storage_basename(old_storage)
    new_name = storage_basename(new_storage)
    if not old_name or not new_name or old_name == new_name:
        return
    blob = resolve_version_blob(collection_id, file_id, version_id, old_storage)
    if blob is None or not blob.is_file():
        return
    dest = blob.parent / new_name
    if dest.exists():
        return
    try:
        blob.rename(dest)
    except OSError:
        logger.warning("Could not rename blob %s -> %s", blob, dest)
        return
    # Sidecar extract caches that used the old basename
    for suffix in (f"{old_name}.extracted.txt",):
        side = blob.parent / suffix
        if side.is_file():
            try:
                side.rename(blob.parent / f"{new_name}.extracted.txt")
            except OSError:
                pass


def find_image_file(
    collection_id: str | None,
    file_id: str,
    image_id: str,
    *,
    extensions: tuple[str, ...] = ("png", "jpg", "jpeg", "gif", "webp", "bmp"),
) -> Path | None:
    """Locate ``images/{image_id}.{ext}`` under version dirs or legacy flat."""
    roots: list[Path] = []
    if collection_id:
        roots.append(managed_file_dir(collection_id, file_id))
    else:
        cols = COLLECTIONS_DIR
        if cols.is_dir():
            for col_dir in cols.iterdir():
                if col_dir.is_dir():
                    roots.append(col_dir / "files" / file_id)

    for root in roots:
        if not root.is_dir():
            continue
        # Legacy: files/{file_id}/images/
        for ext in extensions:
            p = root / "images" / f"{image_id}.{ext}"
            if p.is_file():
                return p
        # Version dirs: files/{file_id}/{version_id}/images/
        try:
            for child in root.iterdir():
                if not child.is_dir():
                    continue
                # Skip non-version noise; version_id is hex uuid without dashes
                for ext in extensions:
                    p = child / "images" / f"{image_id}.{ext}"
                    if p.is_file():
                        return p
        except OSError:
            continue
    return None


def migrate_collection_layout(collection_id: str) -> dict:
    """Move flat version blobs into ``{file_id}/{version_id}/`` (idempotent).

    Shared basenames (legacy overwrite): only the *winning* version (current,
    else highest ``version_no``) receives a **move**; other rows keep no blob.
    """
    if collection_id in _layout_migrated:
        return {"skipped": True, "reason": "already_done_this_process"}

    root = files_dir(collection_id)
    if not root.is_dir():
        _layout_migrated.add(collection_id)
        return {"skipped": True, "reason": "no_files_dir"}

    from src.file_mgmt.store import get_db

    try:
        conn = get_db(collection_id)
    except FileNotFoundError:
        _layout_migrated.add(collection_id)
        return {"skipped": True, "reason": "no_meta_db"}

    moved = 0
    copied_sidecars = 0
    shared_left = 0
    try:
        rows = conn.execute(
            """SELECT fv.version_id, fv.file_id, fv.version_no, fv.storage_file_id,
                      f.current_version_id
               FROM file_versions fv
               JOIN files f ON f.file_id = fv.file_id
               ORDER BY fv.file_id, fv.version_no"""
        ).fetchall()

        # Group by file_id + basename for shared-blob detection
        from collections import defaultdict

        by_key: dict[tuple[str, str], list] = defaultdict(list)
        for r in rows:
            name = storage_basename(r["storage_file_id"])
            if name:
                by_key[(r["file_id"], name)].append(r)

        winners: set[str] = set()  # version_ids that own a shared flat blob
        for (_fid, _name), group in by_key.items():
            if len(group) == 1:
                winners.add(group[0]["version_id"])
                continue
            cur = next(
                (g for g in group if g["version_id"] == g["current_version_id"]),
                None,
            )
            if cur is not None:
                winners.add(cur["version_id"])
            else:
                best = max(group, key=lambda g: int(g["version_no"] or 0))
                winners.add(best["version_id"])
            shared_left += len(group) - 1

        for r in rows:
            fid = r["file_id"]
            vid = r["version_id"]
            name = storage_basename(r["storage_file_id"])
            if not name:
                continue

            target = version_dir(collection_id, fid, vid) / name
            if target.is_file():
                continue  # already in place

            # Already somewhere under version dir with different resolution
            existing = resolve_version_blob(collection_id, fid, vid, name)
            if existing is not None and existing.parent.name == vid:
                continue

            flat = managed_file_dir(collection_id, fid) / name
            if not flat.is_file():
                continue

            if vid not in winners and len(by_key[(fid, name)]) > 1:
                # Loser of shared overwrite — do not steal winner's only blob
                continue

            vdir = version_dir(collection_id, fid, vid)
            vdir.mkdir(parents=True, exist_ok=True)
            try:
                shutil.move(str(flat), str(target))
                moved += 1
            except OSError:
                logger.warning("Layout migrate: failed move %s -> %s", flat, target)
                continue

            # Per-blob extract next to flat blob
            for side_name in (
                f"{name}.extracted.txt",
                f"{name}.{vid[:12]}.extracted.txt",
            ):
                side = managed_file_dir(collection_id, fid) / side_name
                if side.is_file():
                    dest = vdir / f"{name}.extracted.txt"
                    try:
                        shutil.move(str(side), str(dest))
                        copied_sidecars += 1
                    except OSError:
                        pass

        # Current-version shared sidecars: parsed.txt + images/ at file root
        current_rows = conn.execute(
            """SELECT f.file_id, f.current_version_id
               FROM files f
               WHERE f.current_version_id IS NOT NULL"""
        ).fetchall()
        for cr in current_rows:
            fid = cr["file_id"]
            vid = cr["current_version_id"]
            if not vid:
                continue
            froot = managed_file_dir(collection_id, fid)
            vdir = version_dir(collection_id, fid, vid)
            if not froot.is_dir():
                continue
            vdir.mkdir(parents=True, exist_ok=True)

            parsed = froot / "parsed.txt"
            if parsed.is_file() and not (vdir / "parsed.txt").is_file():
                try:
                    shutil.move(str(parsed), str(vdir / "parsed.txt"))
                    copied_sidecars += 1
                except OSError:
                    pass

            images = froot / "images"
            if images.is_dir() and not (vdir / "images").exists():
                try:
                    shutil.move(str(images), str(vdir / "images"))
                    copied_sidecars += 1
                except OSError:
                    pass

        logger.info(
            "Layout migrate %s: moved=%d sidecars=%d shared_losers=%d",
            collection_id,
            moved,
            copied_sidecars,
            shared_left,
        )
        _layout_migrated.add(collection_id)
        return {
            "moved": moved,
            "sidecars": copied_sidecars,
            "shared_losers": shared_left,
        }
    finally:
        conn.close()


def repair_storage_file_labels(collection_id: str) -> int:
    """Fix file_versions.storage_file_id when it is a display label, not disk name.

    Notes/meetings often store ``Meeting: Title / Section`` while disk has
    ``tab_02.md``. Idempotent; safe to call on every open.
    """
    if collection_id in _storage_labels_repaired:
        return 0
    try:
        from src.file_mgmt.store import get_db

        conn = get_db(collection_id)
    except FileNotFoundError:
        _storage_labels_repaired.add(collection_id)
        return 0

    repaired = 0
    try:
        rows = conn.execute(
            "SELECT version_id, file_id, storage_file_id FROM file_versions"
        ).fetchall()
        with conn:
            for r in rows:
                fid = r["file_id"]
                vid = r["version_id"]
                old = storage_basename(r["storage_file_id"])
                blob = resolve_version_blob(
                    collection_id, fid, vid, r["storage_file_id"]
                )
                if blob is None or not blob.name:
                    continue
                if blob.name == old:
                    continue
                conn.execute(
                    "UPDATE file_versions SET storage_file_id=? WHERE version_id=?",
                    (blob.name, vid),
                )
                repaired += 1
        if repaired:
            logger.info(
                "Repaired %d storage_file_id label(s) in %s",
                repaired,
                collection_id,
            )
        _storage_labels_repaired.add(collection_id)
        return repaired
    except Exception:
        logger.exception("storage_file_id repair failed for %s", collection_id)
        return repaired
    finally:
        conn.close()


def ensure_layout_migrated(collection_id: str) -> None:
    """Run layout migration + storage label repair (safe, idempotent)."""
    try:
        if collection_id not in _layout_migrated:
            migrate_collection_layout(collection_id)
    except Exception:
        logger.exception("Layout migration failed for %s", collection_id)
        # Do not mark done — allow retry next open
    try:
        repair_storage_file_labels(collection_id)
    except Exception:
        logger.exception("storage label repair failed for %s", collection_id)
