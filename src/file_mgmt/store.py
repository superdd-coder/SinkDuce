"""Per-collection SQLite connection management.

Path: data/collections/{collection_id}/meta.db
WAL mode (multi-reader, single-writer), foreign_keys ON, Row factory.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("file_mgmt.store")

COLLECTIONS_DIR = Path("data").resolve() / "collections"

# Process-local: collections whose schema backfill already ran successfully.
# Avoids re-running ALTER on every API call (and races under concurrent open).
_backfill_done: set[str] = set()
_backfill_lock = threading.Lock()


def _is_duplicate_column_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return "duplicate column" in msg

# ────────────────────────────────────────────────────────────────
# Schema DDL (contract section 2)
# ────────────────────────────────────────────────────────────────

_CREATE_TABLES = [
    # folders (self-referential parent)
    '''CREATE TABLE folders (
      folder_id        TEXT PRIMARY KEY,
      parent_folder_id TEXT REFERENCES folders(folder_id),
      name             TEXT NOT NULL,
      kind             TEXT NOT NULL,
      is_system        INTEGER DEFAULT 0,
      created_by       TEXT NOT NULL DEFAULT 'local',
      created_at       TEXT NOT NULL,
      updated_at       TEXT NOT NULL,
      version          INTEGER NOT NULL DEFAULT 1,
      icon_type        TEXT,
      icon_value       TEXT,
      icon_color       TEXT
    )''',
    # node_groups (1:1 folder)
    '''CREATE TABLE node_groups (
      group_id    TEXT PRIMARY KEY,
      folder_id   TEXT UNIQUE REFERENCES folders(folder_id),
      name        TEXT NOT NULL,
      description TEXT,
      created_by  TEXT NOT NULL DEFAULT 'local',
      icon_type   TEXT,
      icon_value  TEXT,
      icon_color  TEXT
    )''',
    # chains (circular: parent_node_id -> nodes; self-ref parent_chain_id)
    '''CREATE TABLE chains (
      chain_id        TEXT PRIMARY KEY,
      parent_chain_id TEXT REFERENCES chains(chain_id),
      parent_node_id  TEXT REFERENCES nodes(node_id),
      folder_id       TEXT UNIQUE REFERENCES folders(folder_id),
      title           TEXT,
      created_by      TEXT NOT NULL DEFAULT 'local',
      merge_node_id   TEXT REFERENCES nodes(node_id),
      merge_archive_json TEXT
    )''',
    # nodes (chain_id -> chains; group_id -> node_groups)
    '''CREATE TABLE nodes (
      node_id    TEXT PRIMARY KEY,
      chain_id   TEXT REFERENCES chains(chain_id),
      group_id   TEXT REFERENCES node_groups(group_id),
      node_type  TEXT NOT NULL,
      title      TEXT,
      "order"    INTEGER NOT NULL,
      event_time TEXT,
      created_by TEXT NOT NULL DEFAULT 'local',
      created_at TEXT NOT NULL,
      version    INTEGER NOT NULL DEFAULT 1
    )''',
    # files (circular: current_version_id -> file_versions)
    '''CREATE TABLE files (
      file_id            TEXT PRIMARY KEY,
      current_version_id TEXT REFERENCES file_versions(version_id),
      is_definitive      INTEGER DEFAULT 0,
      archived           INTEGER DEFAULT 0,
      unsupported        INTEGER DEFAULT 0,
      created_by         TEXT NOT NULL DEFAULT 'local',
      version            INTEGER NOT NULL DEFAULT 1
    )''',
    # file_versions (file_id -> files)
    '''CREATE TABLE file_versions (
      version_id      TEXT PRIMARY KEY,
      file_id         TEXT REFERENCES files(file_id),
      version_no      INTEGER NOT NULL,
      storage_file_id TEXT NOT NULL,
      archived        INTEGER DEFAULT 0,
      commit_message  TEXT,
      created_by      TEXT NOT NULL DEFAULT 'local',
      created_at      TEXT NOT NULL
    )''',
    # file_paths (multi-path; path-level archive)
    '''CREATE TABLE file_paths (
      path_id        TEXT PRIMARY KEY,
      file_id        TEXT REFERENCES files(file_id),
      folder_id      TEXT REFERENCES folders(folder_id),
      is_primary     INTEGER DEFAULT 0,
      source_node_id TEXT REFERENCES nodes(node_id),
      created_by     TEXT NOT NULL DEFAULT 'local',
      archived       INTEGER NOT NULL DEFAULT 0,
      UNIQUE(file_id, folder_id, source_node_id)
    )''',
    # file_nodes (file x node N:M)
    '''CREATE TABLE file_nodes (
      file_id    TEXT REFERENCES files(file_id),
      node_id    TEXT REFERENCES nodes(node_id),
      version_id TEXT REFERENCES file_versions(version_id),
      greyed     INTEGER DEFAULT 0,
      added_by   TEXT NOT NULL DEFAULT 'local',
      PRIMARY KEY (file_id, node_id)
    )''',
    # messages (single-point storage)
    '''CREATE TABLE messages (
      message_id      TEXT PRIMARY KEY,
      owner_type      TEXT NOT NULL,
      owner_id        TEXT NOT NULL,
      source_node_id  TEXT REFERENCES nodes(node_id),
      body            TEXT,
      author_type     TEXT NOT NULL,
      author_id       TEXT NOT NULL DEFAULT 'local',
      created_at      TEXT NOT NULL,
      edited_at       TEXT,
      edited_by       TEXT,
      version         INTEGER NOT NULL DEFAULT 1
    )''',
]

_CREATE_INDEXES = [
    'CREATE INDEX idx_nodes_chain_order ON nodes(chain_id, "order")',
    'CREATE INDEX idx_nodes_group       ON nodes(group_id)',
    'CREATE INDEX idx_file_paths_folder ON file_paths(folder_id)',
    'CREATE INDEX idx_file_paths_file   ON file_paths(file_id)',
    'CREATE INDEX idx_file_paths_archived ON file_paths(folder_id, archived)',
    'CREATE INDEX idx_file_nodes_node   ON file_nodes(node_id)',
    'CREATE INDEX idx_file_nodes_file   ON file_nodes(file_id)',
    'CREATE INDEX idx_messages_owner    ON messages(owner_type, owner_id, created_at)',
    'CREATE INDEX idx_files_archived    ON files(archived) WHERE archived = 1',
    'CREATE INDEX idx_folders_parent    ON folders(parent_folder_id)',
    'CREATE INDEX idx_nodes_created_by  ON nodes(created_by)',
    'CREATE INDEX idx_files_created_by  ON files(created_by)',
]

# Table names for verification
EXPECTED_TABLES = {
    "folders", "node_groups", "chains", "nodes",
    "files", "file_versions", "file_paths", "file_nodes", "messages",
}

EXPECTED_INDEXES = {
    "idx_nodes_chain_order", "idx_nodes_group",
    "idx_file_paths_folder", "idx_file_paths_file",
    "idx_file_paths_archived",
    "idx_file_nodes_node", "idx_file_nodes_file",
    "idx_messages_owner", "idx_files_archived",
    "idx_folders_parent", "idx_nodes_created_by", "idx_files_created_by",
}


# ────────────────────────────────────────────────────────────────

SYSTEM_FOLDER_NAMES = ["Meeting", "Notes", "Archived"]


# ────────────────────────────────────────────────────────────────
# System data seeding
# ────────────────────────────────────────────────────────────────

def _seed_system_data(conn: sqlite3.Connection) -> None:
    """Insert system folders, system groups, and main chain."""
    now = datetime.now(timezone.utc).isoformat()

    # 4 system folders (kind=system_group, is_system=1)
    meeting_folder_id = uuid.uuid4().hex
    notes_folder_id = uuid.uuid4().hex
    archived_folder_id = uuid.uuid4().hex

    for fid, name in [
        (meeting_folder_id, "Meeting"),
        (notes_folder_id, "Notes"),
        (archived_folder_id, "Archived"),
    ]:
        conn.execute(
            """INSERT INTO folders
               (folder_id, parent_folder_id, name, kind, is_system,
                created_by, created_at, updated_at, version)
               VALUES (?, NULL, ?, 'system_group', 1, 'local', ?, ?, 1)""",
            (fid, name, now, now),
        )

    # 2 system groups (Meeting, Notes — bound to their folders)
    # Archived is a virtual view, no group binding
    for gid, folder_id, name in [
        (uuid.uuid4().hex, meeting_folder_id, "Meeting"),
        (uuid.uuid4().hex, notes_folder_id, "Notes"),
    ]:
        conn.execute(
            """INSERT INTO node_groups
               (group_id, folder_id, name, description, created_by)
               VALUES (?, ?, ?, NULL, 'local')""",
            (gid, folder_id, name),
        )

    # 1 main chain (parent_chain_id=NULL, parent_node_id=NULL, folder_id=NULL)
    conn.execute(
        """INSERT INTO chains
           (chain_id, parent_chain_id, parent_node_id, folder_id, title, created_by)
           VALUES (?, NULL, NULL, NULL, NULL, 'local')""",
        (uuid.uuid4().hex,),
    )

def _backfill_system_folders(conn: sqlite3.Connection) -> int:
    """Ensure all system folders exist — idempotent, safe for existing DBs.

    Checks each name in *SYSTEM_FOLDER_NAMES* against the ``folders`` table
    and inserts any that are missing.  Returns the count of newly created
    folders.
    """
    now = datetime.now(timezone.utc).isoformat()
    created = 0

    for name in SYSTEM_FOLDER_NAMES:
        exists = conn.execute(
            "SELECT 1 FROM folders WHERE name=? AND is_system=1 LIMIT 1",
            (name,),
        ).fetchone()
        if exists:
            continue
        conn.execute(
            """INSERT INTO folders
               (folder_id, parent_folder_id, name, kind, is_system,
                created_by, created_at, updated_at, version)
               VALUES (?, NULL, ?, 'system_group', 1, 'local', ?, ?, 1)""",
            (uuid.uuid4().hex, name, now, now),
        )
        created += 1
        logger.info("Backfilled system folder '%s' for existing DB", name)

    return created


def _ensure_chains_merge_node_id(conn: sqlite3.Connection) -> None:
    """Add chains.merge_node_id if missing (idempotent, existing DBs)."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(chains)").fetchall()}
    if "merge_node_id" in cols:
        return
    try:
        conn.execute(
            "ALTER TABLE chains ADD COLUMN merge_node_id TEXT REFERENCES nodes(node_id)"
        )
        logger.info("Added chains.merge_node_id column")
    except sqlite3.OperationalError as e:
        # Concurrent backfill on another connection may have won the race
        if not _is_duplicate_column_error(e):
            raise


def _ensure_chains_merge_archive_json(conn: sqlite3.Connection) -> None:
    """Add chains.merge_archive_json if missing (idempotent).

    Stores path/file ids archived by end_chain so reopen can reverse only
    merge-time archives (not user manual archives).
    """
    cols = {row[1] for row in conn.execute("PRAGMA table_info(chains)").fetchall()}
    if "merge_archive_json" in cols:
        return
    try:
        conn.execute("ALTER TABLE chains ADD COLUMN merge_archive_json TEXT")
        logger.info("Added chains.merge_archive_json column")
    except sqlite3.OperationalError as e:
        if not _is_duplicate_column_error(e):
            raise


def _ensure_node_groups_icon_columns(conn: sqlite3.Connection) -> None:
    """Add node_groups icon_* columns if missing (idempotent, race-safe)."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(node_groups)").fetchall()}
    for col in ("icon_type", "icon_value", "icon_color"):
        if col in cols:
            continue
        try:
            conn.execute(f"ALTER TABLE node_groups ADD COLUMN {col} TEXT")
            logger.info("Added node_groups.%s column", col)
            cols.add(col)
        except sqlite3.OperationalError as e:
            # Two requests both saw missing column and both ran ALTER
            if not _is_duplicate_column_error(e):
                raise
            cols.add(col)


def _ensure_folders_icon_columns(conn: sqlite3.Connection) -> None:
    """Add folders icon_* columns if missing (idempotent, race-safe)."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(folders)").fetchall()}
    for col in ("icon_type", "icon_value", "icon_color"):
        if col in cols:
            continue
        try:
            conn.execute(f"ALTER TABLE folders ADD COLUMN {col} TEXT")
            logger.info("Added folders.%s column", col)
            cols.add(col)
        except sqlite3.OperationalError as e:
            if not _is_duplicate_column_error(e):
                raise
            cols.add(col)


def _ensure_file_paths_archived(conn: sqlite3.Connection) -> None:
    """Add file_paths.archived if missing (idempotent path-level archive)."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(file_paths)").fetchall()}
    if "archived" not in cols:
        try:
            conn.execute(
                "ALTER TABLE file_paths ADD COLUMN archived INTEGER NOT NULL DEFAULT 0"
            )
            logger.info("Added file_paths.archived column")
        except sqlite3.OperationalError as e:
            if not _is_duplicate_column_error(e):
                raise
    # Index may be missing on older DBs even after column exists
    indexes = {
        row[1] for row in conn.execute("PRAGMA index_list(file_paths)").fetchall()
    }
    if "idx_file_paths_archived" not in indexes:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_file_paths_archived "
            "ON file_paths(folder_id, archived)"
        )


def _cleanup_uncategorized_folder(conn: sqlite3.Connection) -> int:
    """Remove the Uncategorized system folder from existing DBs.

    The Uncategorized folder was removed as a concept — orphan files
    (no file_paths entries) now appear at root level.  This backfill
    deletes the Uncategorized folder row; CASCADE removes related
    file_paths entries, turning those files into true orphans visible
    at root.
    """
    row = conn.execute(
        "SELECT folder_id FROM folders WHERE name='Uncategorized' AND is_system=1 LIMIT 1"
    ).fetchone()
    if not row:
        return 0
    fid = row["folder_id"]
    # Delete file_paths referencing this folder first (FK safety)
    conn.execute("DELETE FROM file_paths WHERE folder_id=?", (fid,))
    conn.execute("DELETE FROM folders WHERE folder_id=?", (fid,))
    logger.info("Removed Uncategorized folder %s — files now root orphans", fid)
    return 1



# ────────────────────────────────────────────────────────────────
# Public API
# ────────────────────────────────────────────────────────────────

def _get_supported_file_types(collection_id: str) -> list[str]:
    """Get supported file types for a collection from config."""
    from src.config import get_config
    cfg = get_config()
    return cfg.parsing.supported_file_types


def _is_file_supported(filename: str, collection_id: str) -> bool:
    """Check if a file type is supported for embedding."""
    ext = Path(filename).suffix.lower().lstrip(".")
    return ext in _get_supported_file_types(collection_id)


def _migrate_files_json_import(collection_id: str) -> None:
    """Import legacy files from files.json into SQLite as root orphans.

    Automatically routes ingested files:
      - __note__:*   -> Notes folder
      - __meeting__:*-> Meeting folder
      - __file__:*   -> root (orphan, no file_paths)

    Idempotent: skips already-imported files, updates unsupported flag
    for existing ones, and backfills missing file_paths for note/meeting
    files that were imported before routing was added.
    """
    from pathlib import Path as _Path
    import json as _json

    fj_path = _Path("data") / "collections" / collection_id / "files.json"
    if not fj_path.exists():
        return

    conn = get_db(collection_id)
    try:
        try:
            with open(fj_path) as fh:
                fj = _json.load(fh)
        except Exception:
            logger.warning("Failed to read files.json for %s, skipping migration", collection_id)
            return

        now = datetime.now(timezone.utc).isoformat()

        # Pre-fetch system folder IDs
        meeting_fid = None
        notes_fid = None
        for name, target in [("Meeting", "meeting_fid"), ("Notes", "notes_fid")]:
            row = conn.execute(
                "SELECT folder_id FROM folders WHERE name=? AND is_system=1 LIMIT 1",
                (name,),
            ).fetchone()
            if row:
                if target == "meeting_fid":
                    meeting_fid = row["folder_id"]
                else:
                    notes_fid = row["folder_id"]

        imported = 0
        fixed_unsupported = 0
        backfilled_paths = 0

        for file_id, entry in fj.items():
            filename = entry.get("source_label", "") or file_id
            source = entry.get("source", "")

            # Determine supported status (per collection config)
            # Note/Meeting files always supported (already ingested)
            # Use original_ext as fallback when source_label has no extension
            if source.startswith("__note__:") or source.startswith("__meeting__:"):
                supported = True
            else:
                original_ext = entry.get("original_ext", "")
                if original_ext and not _Path(filename).suffix:
                    check_name = filename + ("" if original_ext.startswith(".") else ".") + str(original_ext)
                else:
                    check_name = filename
                supported = _is_file_supported(check_name, collection_id)
            unsupported = 0 if supported else 1

            # Determine target folder from source
            target_fid = None
            if source.startswith("__note__:") and notes_fid:
                target_fid = notes_fid
            elif source.startswith("__meeting__:") and meeting_fid:
                target_fid = meeting_fid

            # Check if file already exists
            existing = conn.execute(
                "SELECT file_id, unsupported FROM files WHERE file_id=?", (file_id,)
            ).fetchone()

            if existing:
                # 1) Fix unsupported flag if wrong
                if bool(existing["unsupported"]) != bool(unsupported):
                    conn.execute(
                        "UPDATE files SET unsupported=? WHERE file_id=?",
                        (unsupported, file_id),
                    )
                    fixed_unsupported += 1

                # 2) Backfill missing file_paths for note/meeting files
                if target_fid:
                    has_path = conn.execute(
                        "SELECT 1 FROM file_paths WHERE file_id=? AND folder_id=? LIMIT 1",
                        (file_id, target_fid),
                    ).fetchone()
                    if not has_path:
                        conn.execute(
                            """INSERT INTO file_paths
                               (path_id, file_id, folder_id, is_primary, source_node_id, created_by)
                               VALUES (?, ?, ?, 1, NULL, 'local')""",
                            (uuid.uuid4().hex, file_id, target_fid),
                        )
                        backfilled_paths += 1

                continue

            # --- New file: insert ---
            version_id = uuid.uuid4().hex
            conn.execute("BEGIN")
            conn.execute("PRAGMA defer_foreign_keys=ON")
            try:
                conn.execute(
                    """INSERT INTO files
                       (file_id, current_version_id, is_definitive, archived,
                        unsupported, created_by, version)
                       VALUES (?, NULL, 0, 0, ?, 'local', 1)""",
                    (file_id, unsupported),
                )
                conn.execute(
                    """INSERT INTO file_versions
                       (version_id, file_id, version_no, storage_file_id,
                        archived, commit_message, created_by, created_at)
                       VALUES (?, ?, 1, ?, 0, NULL, 'local', ?)""",
                    (version_id, file_id, filename, now),
                )
                conn.execute(
                    "UPDATE files SET current_version_id=? WHERE file_id=?",
                    (version_id, file_id),
                )
                # Only create file_paths for note/meeting files
                if target_fid:
                    conn.execute(
                        """INSERT INTO file_paths
                           (path_id, file_id, folder_id, is_primary, source_node_id, created_by)
                           VALUES (?, ?, ?, 1, NULL, 'local')""",
                        (uuid.uuid4().hex, file_id, target_fid),
                    )
                conn.commit()
                imported += 1
            except Exception:
                conn.rollback()
                logger.warning("Failed to migrate file %s", file_id)

        if imported:
            logger.info("Migrated %d legacy files from files.json to SQLite", imported)
        if fixed_unsupported:
            logger.info("Fixed unsupported flag for %d files in %s", fixed_unsupported, collection_id)
        if backfilled_paths:
            logger.info("Backfilled %d file_paths for note/meeting files in %s", backfilled_paths, collection_id)
    except Exception:
        logger.exception("Migration of files.json for %s failed", collection_id)
    finally:
        conn.close()

def _db_path(collection_id: str) -> Path:
    return COLLECTIONS_DIR / collection_id / "meta.db"


def get_db(collection_id: str) -> sqlite3.Connection:
    """Return a per-collection SQLite connection (WAL, FK on, Row factory).

    The caller is responsible for closing the connection (``with`` or
    try/finally).  Raises FileNotFoundError if meta.db does not exist —
    creation must go through ``init_collection_db``.
    """
    path = _db_path(collection_id)
    if not path.exists():
        raise FileNotFoundError(
            f"meta.db not found for collection '{collection_id}'. "
            "Has init_collection_db been called?"
        )
    conn = sqlite3.connect(str(path))
    current_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    if current_mode.lower() != "wal":
        conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_collection_db(collection_id: str) -> None:
    """Create meta.db with all tables, indexes, and system data.

    Safe to call on every request via ``_open_db``:
    - Creates DB if missing
    - Runs schema backfill once per process per collection (race-safe)
    """
    path = _db_path(collection_id)

    # Fast path: already created + backfilled in this process
    if path.exists() and collection_id in _backfill_done:
        return

    with _backfill_lock:
        # Re-check under lock
        path = _db_path(collection_id)
        already_existed = path.exists()

        if not already_existed:
            path.parent.mkdir(parents=True, exist_ok=True)

            conn = sqlite3.connect(str(path))
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA foreign_keys=ON")
                conn.execute("BEGIN")
                conn.execute("PRAGMA defer_foreign_keys=ON")

                for ddl in _CREATE_TABLES:
                    conn.execute(ddl)
                for idx_ddl in _CREATE_INDEXES:
                    conn.execute(idx_ddl)
                _seed_system_data(conn)

                conn.commit()
                logger.info("Initialized meta.db for collection %s", collection_id)
            except Exception:
                conn.rollback()
                for suffix in ("", "-wal", "-shm"):
                    p = path.parent / f"meta.db{suffix}"
                    if p.exists():
                        try:
                            p.unlink()
                        except OSError:
                            pass
                logger.exception(
                    "Failed to init meta.db for collection %s", collection_id
                )
                raise
            finally:
                conn.close()
            # Fresh schema already has all columns — no ALTER backfill needed
            _backfill_done.add(collection_id)
            return

        if collection_id in _backfill_done:
            return

        # Backfill: schema / system data for older DBs (once per process)
        conn_backfill = get_db(collection_id)
        try:
            with conn_backfill:
                _ensure_chains_merge_node_id(conn_backfill)
                _ensure_chains_merge_archive_json(conn_backfill)
                _ensure_node_groups_icon_columns(conn_backfill)
                _ensure_folders_icon_columns(conn_backfill)
                _ensure_file_paths_archived(conn_backfill)
                _backfill_system_folders(conn_backfill)
                _cleanup_uncategorized_folder(conn_backfill)
            _backfill_done.add(collection_id)
        finally:
            conn_backfill.close()

    # Note: files.json → SQLite migration now happens lazily when
    # listing the Uncategorized folder (see service.list_files_in_folder).


def delete_collection_db(collection_id: str) -> None:
    """Delete meta.db and WAL/SHM sidecar files.

    Called when a collection is deleted.  No-op if files don't exist.
    """
    path = _db_path(collection_id)
    for suffix in ("", "-wal", "-shm"):
        p = path.parent / f"meta.db{suffix}"
        if p.exists():
            try:
                p.unlink()
            except OSError as e:
                logger.warning("Could not delete %s: %s", p, e)
    logger.info("Deleted meta.db for collection %s", collection_id)
