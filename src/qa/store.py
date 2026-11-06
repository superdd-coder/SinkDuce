"""Q&A tables in the existing per-collection SQLite database."""

import json
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime

from fastapi import HTTPException

from src.file_mgmt.access import _open_db
from src.identity import authorize, get_actor


def now():
    return datetime.now(UTC).isoformat()


def new_id():
    return uuid.uuid4().hex


def fail(code, status=409):
    raise HTTPException(status, detail={"code": code, "message": code})


SCHEMA = [
    "CREATE TABLE IF NOT EXISTS qa_version_metadata(version_id TEXT PRIMARY KEY REFERENCES qa_versions(id),data_json TEXT NOT NULL)",
    """CREATE TABLE IF NOT EXISTS qa_correction_previews (
        id TEXT PRIMARY KEY,list_id TEXT NOT NULL REFERENCES qa_lists(id),payload_json TEXT NOT NULL,created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS qa_revoked_associations (
        text_revision_id TEXT PRIMARY KEY REFERENCES qa_text_revisions(id),correction_id TEXT NOT NULL REFERENCES qa_correction_previews(id))""",
    """CREATE TABLE IF NOT EXISTS qa_templates (
        id TEXT PRIMARY KEY,name TEXT NOT NULL,settings_json TEXT NOT NULL,revision INTEGER NOT NULL,updated_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS qa_list_templates (
        list_id TEXT PRIMARY KEY REFERENCES qa_lists(id),template_id TEXT NOT NULL REFERENCES qa_templates(id))""",
    """CREATE TABLE IF NOT EXISTS qa_draft_proposals (
        id TEXT PRIMARY KEY,version_id TEXT NOT NULL,question_id TEXT NOT NULL,column_id TEXT NOT NULL,
        actor_id TEXT NOT NULL,context_hash TEXT NOT NULL,result_json TEXT NOT NULL,created_at TEXT NOT NULL,
        FOREIGN KEY(version_id,question_id,column_id) REFERENCES qa_version_cells(version_id,question_id,column_id))""",
    """CREATE TABLE IF NOT EXISTS qa_reply_sources (
        text_revision_id TEXT PRIMARY KEY REFERENCES qa_text_revisions(id),sources_json TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS qa_materials (
        id TEXT PRIMARY KEY,list_id TEXT NOT NULL REFERENCES qa_lists(id),current_version_id TEXT NOT NULL,
        revision INTEGER NOT NULL,archived INTEGER NOT NULL,created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS qa_material_versions (
        id TEXT PRIMARY KEY,material_id TEXT NOT NULL REFERENCES qa_materials(id),number INTEGER NOT NULL,
        filename TEXT NOT NULL,content_hash TEXT NOT NULL,parse_state TEXT NOT NULL,index_state TEXT NOT NULL,
        error_code TEXT NOT NULL,content TEXT NOT NULL,metadata_json TEXT NOT NULL,created_at TEXT NOT NULL,
        UNIQUE(material_id,number))""",
    "CREATE TABLE IF NOT EXISTS qa_schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)",
    """CREATE TABLE IF NOT EXISTS qa_lists (
        id TEXT PRIMARY KEY, title TEXT NOT NULL, current_version_id TEXT,
        revision INTEGER NOT NULL DEFAULT 1, archived INTEGER NOT NULL DEFAULT 0,
        created_by TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS qa_versions (
        id TEXT PRIMARY KEY, list_id TEXT NOT NULL REFERENCES qa_lists(id),
        number INTEGER NOT NULL, state TEXT NOT NULL CHECK(state IN ('working','frozen')),
        parent_id TEXT REFERENCES qa_versions(id), created_at TEXT NOT NULL, frozen_at TEXT,
        UNIQUE(list_id,number))""",
    """CREATE UNIQUE INDEX IF NOT EXISTS qa_one_working ON qa_versions(list_id) WHERE state='working'""",
    """CREATE TABLE IF NOT EXISTS qa_questions (
        id TEXT PRIMARY KEY, list_id TEXT NOT NULL REFERENCES qa_lists(id),
        born_version_id TEXT NOT NULL REFERENCES qa_versions(id))""",
    """CREATE TABLE IF NOT EXISTS qa_columns (
        id TEXT PRIMARY KEY, list_id TEXT NOT NULL REFERENCES qa_lists(id),
        born_version_id TEXT NOT NULL REFERENCES qa_versions(id))""",
    """CREATE TABLE IF NOT EXISTS qa_text_revisions (
        id TEXT PRIMARY KEY, question_id TEXT NOT NULL REFERENCES qa_questions(id),
        column_id TEXT REFERENCES qa_columns(id), body TEXT NOT NULL,
        previous_id TEXT REFERENCES qa_text_revisions(id), created_by TEXT NOT NULL,
        created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS qa_version_questions (
        version_id TEXT NOT NULL REFERENCES qa_versions(id), question_id TEXT NOT NULL REFERENCES qa_questions(id),
        text_revision_id TEXT NOT NULL REFERENCES qa_text_revisions(id), included INTEGER NOT NULL,
        position INTEGER NOT NULL, external_number TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL,
        type TEXT NOT NULL,
        type_origin TEXT NOT NULL DEFAULT 'default', absent_since INTEGER,
        updated_at TEXT NOT NULL, PRIMARY KEY(version_id,question_id))""",
    """CREATE TABLE IF NOT EXISTS qa_version_columns (
        version_id TEXT NOT NULL REFERENCES qa_versions(id), column_id TEXT NOT NULL REFERENCES qa_columns(id),
        label TEXT NOT NULL, included INTEGER NOT NULL, position INTEGER NOT NULL,
        absent_since INTEGER, PRIMARY KEY(version_id,column_id))""",
    """CREATE TABLE IF NOT EXISTS qa_version_cells (
        version_id TEXT NOT NULL, question_id TEXT NOT NULL, column_id TEXT NOT NULL,
        text_revision_id TEXT NOT NULL REFERENCES qa_text_revisions(id),
        historical_revision_id TEXT REFERENCES qa_text_revisions(id), cleared_since INTEGER,
        PRIMARY KEY(version_id,question_id,column_id),
        FOREIGN KEY(version_id,question_id) REFERENCES qa_version_questions(version_id,question_id),
        FOREIGN KEY(version_id,column_id) REFERENCES qa_version_columns(version_id,column_id))""",
    """CREATE TABLE IF NOT EXISTS qa_events (
        id TEXT PRIMARY KEY, list_id TEXT NOT NULL REFERENCES qa_lists(id), version_id TEXT NOT NULL,
        event_type TEXT NOT NULL, actor_id TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS qa_questions_by_list ON qa_questions(list_id)",
    "CREATE INDEX IF NOT EXISTS qa_columns_by_list ON qa_columns(list_id)",
    """CREATE TABLE IF NOT EXISTS qa_imports (
        id TEXT PRIMARY KEY,list_id TEXT NOT NULL REFERENCES qa_lists(id),base_revision INTEGER NOT NULL,
        plan_json TEXT NOT NULL,committed_version_id TEXT REFERENCES qa_versions(id),created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS qa_exports (
        id TEXT PRIMARY KEY,list_id TEXT NOT NULL REFERENCES qa_lists(id),version_id TEXT NOT NULL REFERENCES qa_versions(id),
        snapshot_json TEXT NOT NULL,options_json TEXT NOT NULL,created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS qa_reply_drafts (
        version_id TEXT NOT NULL,question_id TEXT NOT NULL,column_id TEXT NOT NULL,actor_id TEXT NOT NULL,
        body TEXT NOT NULL,draft_revision INTEGER NOT NULL,base_cell_revision_id TEXT NOT NULL REFERENCES qa_text_revisions(id),
        sources_json TEXT NOT NULL,context_hash TEXT NOT NULL,updated_at TEXT NOT NULL,
        PRIMARY KEY(version_id,question_id,column_id,actor_id),
        FOREIGN KEY(version_id,question_id,column_id) REFERENCES qa_version_cells(version_id,question_id,column_id))""",
    """CREATE TABLE IF NOT EXISTS qa_annotations (
        version_id TEXT NOT NULL,question_id TEXT NOT NULL,context_key TEXT NOT NULL,
        summary TEXT NOT NULL,reply_summary TEXT NOT NULL,suggested_type TEXT NOT NULL,
        PRIMARY KEY(version_id,question_id),
        FOREIGN KEY(version_id,question_id) REFERENCES qa_version_questions(version_id,question_id))""",
]


@contextmanager
def connection(collection_id, action="read"):
    conn = _open_db(collection_id)
    try:
        authorize(get_actor(), f"qa.{action}", {"collection_id": collection_id})
        with conn:
            for sql in SCHEMA:
                conn.execute(sql)
            conn.execute("INSERT OR IGNORE INTO qa_schema_migrations VALUES(1,?)", (now(),))
        migrate_options(conn)
        yield conn
    finally:
        conn.close()


def migrate_options(conn):
    if conn.execute("SELECT 1 FROM qa_schema_migrations WHERE version=2").fetchone():
        return
    # Rebuild only the constrained table; child foreign keys retain its original name.
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM qa_schema_migrations WHERE version=2").fetchone():
                return
            sql = next(
                s
                for s in SCHEMA
                if s.startswith("CREATE TABLE IF NOT EXISTS qa_version_questions ")
            )
            conn.execute(sql.replace("qa_version_questions (", "qa_version_questions_new ("))
            conn.execute("INSERT INTO qa_version_questions_new SELECT * FROM qa_version_questions")
            conn.execute("DROP TABLE qa_version_questions")
            conn.execute("ALTER TABLE qa_version_questions_new RENAME TO qa_version_questions")
            if conn.execute("PRAGMA foreign_key_check").fetchone():
                fail("migration_integrity_error", 500)
            conn.execute("INSERT INTO qa_schema_migrations VALUES(2,?)", (now(),))
    finally:
        conn.execute("PRAGMA foreign_keys=ON")


def get_list(conn, list_id):
    row = conn.execute("SELECT * FROM qa_lists WHERE id=?", (list_id,)).fetchone()
    if row is None:
        fail("not_found", 404)
    return row


@contextmanager
def writing(collection_id, list_id, data, event_type):
    with connection(collection_id, "update") as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        listing = get_list(conn, list_id)
        if data.version_id and data.version_id != listing["current_version_id"]:
            fail("frozen_version")
        if listing["revision"] != data.expected_revision:
            fail("stale_revision")
        if listing["archived"] and event_type != "list.update":
            fail("archived_list")
        version = conn.execute(
            "SELECT * FROM qa_versions WHERE id=?", (listing["current_version_id"],)
        ).fetchone()
        if version is None:
            fail("import_not_committed")
        yield conn, listing, version
        conn.execute(
            "UPDATE qa_lists SET revision=revision+1,updated_at=? WHERE id=?", (now(), list_id)
        )
        conn.execute(
            "INSERT INTO qa_events VALUES(?,?,?,?,?,?,?)",
            (
                new_id(),
                list_id,
                version["id"],
                event_type,
                get_actor().id,
                json.dumps(data.model_dump(exclude_none=True), ensure_ascii=False),
                now(),
            ),
        )


def text_revision(conn, question_id, body, column_id=None, previous_id=None):
    if previous_id:
        previous = conn.execute(
            "SELECT body FROM qa_text_revisions WHERE id=?", (previous_id,)
        ).fetchone()
        if previous and previous["body"] == body:
            return previous_id
    rid = new_id()
    conn.execute(
        "INSERT INTO qa_text_revisions VALUES(?,?,?,?,?,?,?)",
        (
            rid,
            question_id,
            column_id,
            body,
            previous_id,
            get_actor().id,
            now(),
        ),
    )
    return rid
