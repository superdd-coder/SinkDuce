"""Per-collection SQLite connection management.

Path: data/collections/{collection_id}/meta.db
WAL mode (multi-reader, single-writer), foreign_keys ON, Row factory.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("file_mgmt.store")

COLLECTIONS_DIR = Path("data").resolve() / "collections"

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
      version          INTEGER NOT NULL DEFAULT 1
    )''',
    # node_groups (1:1 folder)
    '''CREATE TABLE node_groups (
      group_id    TEXT PRIMARY KEY,
      folder_id   TEXT UNIQUE REFERENCES folders(folder_id),
      name        TEXT NOT NULL,
      description TEXT,
      created_by  TEXT NOT NULL DEFAULT 'local'
    )''',
    # chains (circular: parent_node_id -> nodes; self-ref parent_chain_id)
    '''CREATE TABLE chains (
      chain_id        TEXT PRIMARY KEY,
      parent_chain_id TEXT REFERENCES chains(chain_id),
      parent_node_id  TEXT REFERENCES nodes(node_id),
      folder_id       TEXT UNIQUE REFERENCES folders(folder_id),
      title           TEXT,
      created_by      TEXT NOT NULL DEFAULT 'local'
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
    # file_paths (multi-path)
    '''CREATE TABLE file_paths (
      path_id        TEXT PRIMARY KEY,
      file_id        TEXT REFERENCES files(file_id),
      folder_id      TEXT REFERENCES folders(folder_id),
      is_primary     INTEGER DEFAULT 0,
      source_node_id TEXT REFERENCES nodes(node_id),
      created_by     TEXT NOT NULL DEFAULT 'local',
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
    "idx_file_nodes_node", "idx_file_nodes_file",
    "idx_messages_owner", "idx_files_archived",
    "idx_folders_parent", "idx_nodes_created_by", "idx_files_created_by",
}


# ────────────────────────────────────────────────────────────────
# System data seeding
# ────────────────────────────────────────────────────────────────

def _seed_system_data(conn: sqlite3.Connection) -> None:
    """Insert system folders, system groups, and main chain."""
    now = datetime.now(timezone.utc).isoformat()

    # 3 system folders (kind=system_group, is_system=1)
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


# ────────────────────────────────────────────────────────────────
# Public API
# ────────────────────────────────────────────────────────────────

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
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_collection_db(collection_id: str) -> None:
    """Create meta.db with all tables, indexes, and system data.

    Called when a collection is created.  If meta.db already exists this
    is a no-op (idempotent guard).
    """
    path = _db_path(collection_id)
    if path.exists():
        logger.warning(
            "meta.db already exists for collection %s, skipping init",
            collection_id,
        )
        return

    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path))
    try:
        # WAL + FK must be set outside a transaction
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")

        # Single transaction with deferred FK checks for circular refs
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
        # Clean up partially created db to avoid half-initialized state
        for suffix in ("", "-wal", "-shm"):
            p = path.parent / f"meta.db{suffix}"
            if p.exists():
                try:
                    p.unlink()
                except OSError:
                    pass
        logger.exception("Failed to init meta.db for collection %s", collection_id)
        raise
    finally:
        conn.close()


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
