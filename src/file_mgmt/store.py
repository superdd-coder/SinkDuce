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

from src.config import DATA_DIR

logger = logging.getLogger("file_mgmt.store")

COLLECTIONS_DIR = DATA_DIR / "collections"

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
      icon_color       TEXT,
      archived         INTEGER NOT NULL DEFAULT 0,
      archive_snapshot TEXT
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
      version    INTEGER NOT NULL DEFAULT 1,
      external_ref TEXT
    )''',
    # files (circular: current_version_id -> file_versions)
    '''CREATE TABLE files (
      file_id            TEXT PRIMARY KEY,
      current_version_id TEXT REFERENCES file_versions(version_id),
      is_definitive      INTEGER DEFAULT 0,
      archived           INTEGER DEFAULT 0,
      unsupported        INTEGER DEFAULT 0,
      created_by         TEXT NOT NULL DEFAULT 'local',
      version            INTEGER NOT NULL DEFAULT 1,
      source             TEXT,
      source_label       TEXT
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
    # todos (collection checklist; independent of timeline nodes)
    '''CREATE TABLE todos (
      todo_id           TEXT PRIMARY KEY,
      title             TEXT NOT NULL,
      body              TEXT,
      done              INTEGER NOT NULL DEFAULT 0,
      ddl               TEXT,
      target_chain_id   TEXT REFERENCES chains(chain_id) ON DELETE SET NULL,
      completed_node_id TEXT REFERENCES nodes(node_id) ON DELETE SET NULL,
      sort_order        INTEGER,
      created_at        TEXT NOT NULL,
      updated_at        TEXT NOT NULL,
      completed_at      TEXT,
      source_meeting_id TEXT,
      source_section_tab_id TEXT,
      source_candidate_id TEXT,
      assignee_person_id TEXT
    )''',
    # Smart to-do suggestions cache (one row per chain)
    '''CREATE TABLE todo_suggestion_state (
      chain_id            TEXT PRIMARY KEY REFERENCES chains(chain_id) ON DELETE CASCADE,
      status              TEXT NOT NULL,
      suggestions_json    TEXT NOT NULL DEFAULT '[]',
      error               TEXT,
      context_fingerprint TEXT,
      updated_at          TEXT NOT NULL,
      generated_at        TEXT
    )''',
    # Definitive suggestions ledger (one row per file; decisions bind to the
    # content hash — a new version with changed content reopens evaluation)
    '''CREATE TABLE definitive_suggestions (
      suggestion_id TEXT PRIMARY KEY,
      file_id       TEXT NOT NULL UNIQUE,
      reason        TEXT NOT NULL DEFAULT '',
      summary_hash  TEXT NOT NULL DEFAULT '',
      status        TEXT NOT NULL DEFAULT 'pending',
      created_at    TEXT NOT NULL,
      decided_at    TEXT,
      updated_at    TEXT NOT NULL
    )''',
]

_CREATE_INDEXES = [
    'CREATE INDEX idx_nodes_chain_order ON nodes(chain_id, "order")',
    'CREATE INDEX idx_nodes_group       ON nodes(group_id)',
    # One meeting (or external) anchor per chain — design 2026-08-12
    'CREATE UNIQUE INDEX idx_nodes_external_ref_chain ON nodes(external_ref, chain_id) WHERE external_ref IS NOT NULL',
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
    'CREATE INDEX idx_todos_done_ddl    ON todos(done, ddl)',
    'CREATE INDEX idx_todos_chain       ON todos(target_chain_id)',
    'CREATE INDEX idx_todos_source_section ON todos(source_meeting_id, source_section_tab_id)',
]

# Table names for verification
EXPECTED_TABLES = {
    "folders", "node_groups", "chains", "nodes",
    "files", "file_versions", "file_paths", "file_nodes", "messages",
    "todos", "todo_suggestion_state", "definitive_suggestions",
}

EXPECTED_INDEXES = {
    "idx_nodes_chain_order", "idx_nodes_group", "idx_nodes_external_ref_chain",
    "idx_file_paths_folder", "idx_file_paths_file",
    "idx_file_paths_archived",
    "idx_file_nodes_node", "idx_file_nodes_file",
    "idx_messages_owner", "idx_files_archived",
    "idx_folders_parent", "idx_nodes_created_by", "idx_files_created_by",
    "idx_todos_done_ddl", "idx_todos_chain", "idx_todos_source_section",
}


# ────────────────────────────────────────────────────────────────

SYSTEM_FOLDER_NAMES = ["Meeting", "Notes", "Archived", "Unassigned", "Root"]


# ────────────────────────────────────────────────────────────────
# System data seeding
# ────────────────────────────────────────────────────────────────

def _seed_system_data(conn: sqlite3.Connection) -> None:
    """Insert system folders, system groups, and main chain."""
    now = datetime.now(timezone.utc).isoformat()

    # System folders (kind=system_group, is_system=1) from the canonical list
    folder_ids: dict[str, str] = {}
    for name in SYSTEM_FOLDER_NAMES:
        fid = uuid.uuid4().hex
        folder_ids[name] = fid
        conn.execute(
            """INSERT INTO folders
               (folder_id, parent_folder_id, name, kind, is_system,
                created_by, created_at, updated_at, version)
               VALUES (?, NULL, ?, 'system_group', 1, 'local', ?, ?, 1)""",
            (fid, name, now, now),
        )

    # 2 system groups (Meeting, Notes — bound to their folders)
    # Archived / Unassigned are virtual views, no group binding
    for name in ("Meeting", "Notes"):
        conn.execute(
            """INSERT INTO node_groups
               (group_id, folder_id, name, description, created_by)
               VALUES (?, ?, ?, NULL, 'local')""",
            (uuid.uuid4().hex, folder_ids[name], name),
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


def _ensure_root_folder(conn: sqlite3.Connection) -> str:
    """Return the Root system folder id, creating the row if missing.

    Root is a real mount target (v2 R1) but stays invisible to the UI tree —
    ``get_folder_tree`` filters it out.
    """
    row = conn.execute(
        "SELECT folder_id FROM folders WHERE name='Root' AND is_system=1 LIMIT 1"
    ).fetchone()
    if row:
        return row["folder_id"]
    fid = uuid.uuid4().hex
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO folders
           (folder_id, parent_folder_id, name, kind, is_system,
            created_by, created_at, updated_at, version)
           VALUES (?, NULL, 'Root', 'system_group', 1, 'local', ?, ?, 1)""",
        (fid, now, now),
    )
    logger.info("Created Root system folder %s", fid)
    return fid


def _ensure_root_mounts(conn: sqlite3.Connection) -> str:
    """Mount every zero-path file into the Root folder; return Root's id.

    Zero-path is a transient state in the root-path model (v2 R1) — legacy
    orphans and files.json migrations must land on Root, not stay invisible.
    """
    root_fid = _ensure_root_folder(conn)
    conn.execute(
        """INSERT INTO file_paths
           (path_id, file_id, folder_id, is_primary, source_node_id, created_by)
           SELECT lower(hex(randomblob(16))), f.file_id, ?, 1, NULL, 'local'
           FROM files f
           WHERE f.file_id NOT IN (SELECT file_id FROM file_paths)""",
        (root_fid,),
    )
    # Standalone callers (list endpoints) rely on this committing; callers
    # inside their own transaction commit with it — inserts are idempotent.
    conn.commit()
    return root_fid


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
    _ensure_folders_archive_columns(conn, cols)


def _ensure_folders_archive_columns(
    conn: sqlite3.Connection, cols: set[str] | None = None
) -> None:
    """Add folders.archived + archive_snapshot if missing."""
    if cols is None:
        cols = {
            row[1] for row in conn.execute("PRAGMA table_info(folders)").fetchall()
        }
    if "archived" not in cols:
        try:
            conn.execute(
                "ALTER TABLE folders ADD COLUMN archived INTEGER NOT NULL DEFAULT 0"
            )
            logger.info("Added folders.archived column")
            cols.add("archived")
        except sqlite3.OperationalError as e:
            if not _is_duplicate_column_error(e):
                raise
            cols.add("archived")
    if "archive_snapshot" not in cols:
        try:
            conn.execute("ALTER TABLE folders ADD COLUMN archive_snapshot TEXT")
            logger.info("Added folders.archive_snapshot column")
            cols.add("archive_snapshot")
        except sqlite3.OperationalError as e:
            if not _is_duplicate_column_error(e):
                raise
            cols.add("archive_snapshot")


def _ensure_nodes_external_ref(conn: sqlite3.Connection) -> None:
    """Add nodes.external_ref + unique (external_ref, chain_id) index."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(nodes)").fetchall()}
    if "external_ref" not in cols:
        try:
            conn.execute("ALTER TABLE nodes ADD COLUMN external_ref TEXT")
            logger.info("Added nodes.external_ref column")
        except sqlite3.OperationalError as e:
            if not _is_duplicate_column_error(e):
                raise
    indexes = {
        row[1] for row in conn.execute("PRAGMA index_list(nodes)").fetchall()
    }
    # Migrate: global unique on external_ref → unique per (external_ref, chain_id)
    if "idx_nodes_external_ref" in indexes:
        try:
            conn.execute("DROP INDEX IF EXISTS idx_nodes_external_ref")
            logger.info("Dropped legacy idx_nodes_external_ref")
        except sqlite3.OperationalError:
            logger.debug("Could not drop idx_nodes_external_ref", exc_info=True)
        indexes = {
            row[1] for row in conn.execute("PRAGMA index_list(nodes)").fetchall()
        }
    if "idx_nodes_external_ref_chain" not in indexes:
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_nodes_external_ref_chain "
            "ON nodes(external_ref, chain_id) WHERE external_ref IS NOT NULL"
        )


def _ensure_todos_table(conn: sqlite3.Connection) -> None:
    """Create todos table + indexes if missing (existing collection DBs)."""
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "todos" not in tables:
        conn.execute(
            """CREATE TABLE todos (
              todo_id           TEXT PRIMARY KEY,
              title             TEXT NOT NULL,
              body              TEXT,
              done              INTEGER NOT NULL DEFAULT 0,
              ddl               TEXT,
              target_chain_id   TEXT REFERENCES chains(chain_id) ON DELETE SET NULL,
              completed_node_id TEXT REFERENCES nodes(node_id) ON DELETE SET NULL,
              sort_order        INTEGER,
              created_at        TEXT NOT NULL,
              updated_at        TEXT NOT NULL,
              completed_at      TEXT
            )"""
        )
        logger.info("Created todos table")
    # Existing DBs created before body column
    cols = {row[1] for row in conn.execute("PRAGMA table_info(todos)").fetchall()}
    if "body" not in cols:
        try:
            conn.execute("ALTER TABLE todos ADD COLUMN body TEXT")
            logger.info("Added todos.body column")
        except sqlite3.OperationalError as e:
            if not _is_duplicate_column_error(e):
                raise
    indexes = {
        row[1] for row in conn.execute("PRAGMA index_list(todos)").fetchall()
    }
    if "idx_todos_done_ddl" not in indexes:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_todos_done_ddl ON todos(done, ddl)"
        )
    if "idx_todos_chain" not in indexes:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_todos_chain ON todos(target_chain_id)"
        )
    # Provenance for meeting-sourced todos (design 2026-08-12)
    for col in (
        "source_meeting_id",
        "source_section_tab_id",
        "source_candidate_id",
        "assignee_person_id",
    ):
        if col not in cols:
            try:
                conn.execute(f"ALTER TABLE todos ADD COLUMN {col} TEXT")
                logger.info("Added todos.%s column", col)
            except sqlite3.OperationalError as e:
                if not _is_duplicate_column_error(e):
                    raise
    cols = {row[1] for row in conn.execute("PRAGMA table_info(todos)").fetchall()}
    indexes = {
        row[1] for row in conn.execute("PRAGMA index_list(todos)").fetchall()
    }
    if "idx_todos_source_section" not in indexes:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_todos_source_section "
            "ON todos(source_meeting_id, source_section_tab_id)"
        )


def _ensure_todo_suggestion_state_table(conn: sqlite3.Connection) -> None:
    """Create todo_suggestion_state table if missing (existing collection DBs)."""
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "todo_suggestion_state" not in tables:
        conn.execute(
            """CREATE TABLE todo_suggestion_state (
              chain_id            TEXT PRIMARY KEY REFERENCES chains(chain_id) ON DELETE CASCADE,
              status              TEXT NOT NULL,
              suggestions_json    TEXT NOT NULL DEFAULT '[]',
              error               TEXT,
              context_fingerprint TEXT,
              updated_at          TEXT NOT NULL,
              generated_at        TEXT
            )"""
        )
        logger.info("Created todo_suggestion_state table")


def _ensure_definitive_suggestions_table(conn: sqlite3.Connection) -> None:
    """Create definitive_suggestions table if missing (existing collection DBs)."""
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "definitive_suggestions" not in tables:
        conn.execute(
            """CREATE TABLE definitive_suggestions (
              suggestion_id TEXT PRIMARY KEY,
              file_id       TEXT NOT NULL UNIQUE,
              reason        TEXT NOT NULL DEFAULT '',
              summary_hash  TEXT NOT NULL DEFAULT '',
              status        TEXT NOT NULL DEFAULT 'pending',
              created_at    TEXT NOT NULL,
              decided_at    TEXT,
              updated_at    TEXT NOT NULL
            )"""
        )
        logger.info("Created definitive_suggestions table")


def _ensure_files_source_columns(conn: sqlite3.Connection) -> None:
    """Persist document source + display label after files.json stop-write."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(files)").fetchall()}
    for col in ("source", "source_label"):
        if col in cols:
            continue
        try:
            conn.execute(f"ALTER TABLE files ADD COLUMN {col} TEXT")
            logger.info("Added files.%s column", col)
        except sqlite3.OperationalError as e:
            if not _is_duplicate_column_error(e):
                raise


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

    Idempotent and cheap on repeat calls: entries already present in SQLite
    are skipped entirely — no per-entry blob probing (the legacy full rescan
    ran on every listing request and cost seconds per folder open). One-time
    repairs for imported rows live in ensure_layout_migrated /
    repair_storage_file_labels; only unseen entries take the import path.
    """
    import json as _json

    fj_path = COLLECTIONS_DIR / collection_id / "files.json"
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

        imported_ids = {
            r["file_id"]
            for r in conn.execute("SELECT file_id FROM files").fetchall()
        }
        pending = {fid: e for fid, e in fj.items() if fid not in imported_ids}
        if not pending:
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

        for file_id, entry in pending.items():
            source = entry.get("source", "")
            # Prefer real on-disk basename over source_label (notes store
            # "Note: Title" as label but write "{safe_title}.md" on disk).
            filename = entry.get("source_label", "") or file_id
            try:
                from src.file_mgmt.storage_paths import discover_content_blob

                disk_blob = discover_content_blob(collection_id, file_id)
                if disk_blob is not None:
                    filename = disk_blob.name
            except Exception:
                pass

            # Determine supported status (per collection config)
            # Note/Meeting files always supported (already ingested)
            # Use original_ext as fallback when source_label has no extension
            if source.startswith("__note__:") or source.startswith("__meeting__:"):
                supported = True
            else:
                original_ext = entry.get("original_ext", "")
                if original_ext and not Path(filename).suffix:
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

        # Legacy classic files land with no path — root-path model requires
        # every file to be mounted somewhere (v2 R1).
        _ensure_root_mounts(conn)
    except Exception:
        logger.exception("Migration of files.json for %s failed", collection_id)
    finally:
        conn.close()

def _db_path(collection_id: str) -> Path:
    from src.paths import assert_resource_id, confine

    assert_resource_id(collection_id, name="collection_id")
    return confine(COLLECTIONS_DIR / collection_id / "meta.db", COLLECTIONS_DIR)


def db_initialized(collection_id: str) -> bool:
    """meta.db 是否已初始化 —— **纯读、不创建**。

    与 :func:`get_db` / :func:`init_collection_db` 的区别就在这：那些是
    「没有就建/就报错」的写路径入口，而这个只回答「现在有没有」。跨盒子转发
    的前置校验需要它：REST 的写端点会**静默自动建库**（``_open_db`` 幂等创建），
    而 MCP 工具的本地实现对未初始化的库是**拒绝**——不加这道纯读探针，
    同一个调用本地报错、转发却悄悄建出库并写入（2026-09-18 批次 2 实测）。
    """
    try:
        return _db_path(collection_id).exists()
    except Exception:
        return False


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
                _ensure_files_source_columns(conn_backfill)
                _ensure_nodes_external_ref(conn_backfill)
                _ensure_todos_table(conn_backfill)
                _ensure_todo_suggestion_state_table(conn_backfill)
                _ensure_definitive_suggestions_table(conn_backfill)
                _backfill_system_folders(conn_backfill)
                _ensure_root_mounts(conn_backfill)
                _cleanup_uncategorized_folder(conn_backfill)
            _backfill_done.add(collection_id)
        finally:
            conn_backfill.close()

        # Disk layout: files/{file_id}/{version_id}/… (idempotent; safe for flat legacy)
        try:
            from src.file_mgmt.storage_paths import ensure_layout_migrated

            ensure_layout_migrated(collection_id)
        except Exception:
            logger.exception(
                "Version-dir layout migration failed for %s", collection_id
            )

    # Note: files.json → SQLite migration runs lazily on root list
    # (service.list_files) and on every folder list (list_files_in_folder).


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
