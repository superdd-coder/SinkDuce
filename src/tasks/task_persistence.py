"""Durable ledger for upload (ingest) tasks.

``TaskManager`` state lives in memory, so a crash between "file stored"
and "chunks embedded" used to orphan the file forever: the task list was
gone on restart and nothing re-linked the file to an ingest. Upload tasks
are the only task type that cannot be re-derived from existing state, so
they get this small SQLite ledger:

- a row is written when an upload task is queued
- the row is deleted on any terminal state (completed / failed / cancelled)
- rows found at startup are re-enqueued by ``TaskManager.recover_upload_tasks``

Every function is best-effort by contract: callers wrap them in
try/except so a ledger failure can never break the ingest pipeline.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone

from src.config import DATA_DIR

logger = logging.getLogger(__name__)

DB_PATH = DATA_DIR / "tasks" / "tasks.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS upload_tasks (
    task_id       TEXT PRIMARY KEY,
    collection_id TEXT NOT NULL,
    file_id       TEXT,
    version_id    TEXT,
    file_path     TEXT,
    payload       TEXT NOT NULL,
    created_at    TEXT NOT NULL
)
"""


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(_SCHEMA)
    return conn


def record_upload_task(task_id: str, collection_id: str, payload: dict) -> None:
    """Persist a queued upload task so a crash can be recovered from."""
    now = datetime.now(timezone.utc).isoformat()
    conn = _connect()
    try:
        with conn:
            conn.execute(
                """INSERT OR REPLACE INTO upload_tasks
                   (task_id, collection_id, file_id, version_id, file_path,
                    payload, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    task_id,
                    collection_id,
                    payload.get("file_id"),
                    payload.get("version_id"),
                    payload.get("file_path"),
                    json.dumps(payload, ensure_ascii=False),
                    now,
                ),
            )
    finally:
        conn.close()


def finish_upload_task(task_id: str) -> None:
    """Remove the ledger row once the task reached a terminal state."""
    conn = _connect()
    try:
        with conn:
            conn.execute("DELETE FROM upload_tasks WHERE task_id=?", (task_id,))
    finally:
        conn.close()


def pending_upload_tasks() -> list[dict]:
    """All ledger rows — tasks that were queued/running when the process died.

    Ordered oldest first so recovery can keep the newest row per file.
    """
    if not DB_PATH.exists():
        return []
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM upload_tasks ORDER BY created_at, rowid"
        ).fetchall()
        out: list[dict] = []
        for r in rows:
            d = dict(r)
            try:
                d["payload"] = json.loads(d["payload"])
            except (TypeError, ValueError):
                continue  # unreadable payload — nothing to re-enqueue
            out.append(d)
        return out
    except sqlite3.Error as e:
        logger.warning("upload task ledger unreadable (%s); skipping recovery", e)
        return []
    finally:
        conn.close()
