from __future__ import annotations

import json
import logging
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class Session:
    id: str
    title: str
    collections: list[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""


@dataclass
class Message:
    id: str
    session_id: str
    role: str  # "user" | "assistant"
    content: str
    sources: list[dict] | None = None
    metadata: dict | None = None
    created_at: str = ""


class SessionStore:
    """SQLite-backed session and message storage. Zero new dependencies."""

    def __init__(self, db_path: str = "data/sessions.db"):
        self._db_path = Path(db_path)
        self._lock = threading.Lock()
        self._local = threading.local()
        self._init_db()

    # ── connection management ──────────────────────────────────────

    def _get_conn(self) -> sqlite3.Connection:
        """Return a thread-local connection."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return conn

    # ── schema ─────────────────────────────────────────────────────

    def _init_db(self):
        conn = self._get_conn()
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id          TEXT PRIMARY KEY,
                title       TEXT NOT NULL DEFAULT '',
                collections TEXT NOT NULL DEFAULT '[]',
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS messages (
                id          TEXT PRIMARY KEY,
                session_id  TEXT NOT NULL,
                role        TEXT NOT NULL,
                content     TEXT NOT NULL DEFAULT '',
                sources     TEXT,
                metadata    TEXT,
                created_at  TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_messages_session
                ON messages(session_id, created_at);
            """
        )
        conn.commit()

    # ── helpers ────────────────────────────────────────────────────

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _uid() -> str:
        return uuid.uuid4().hex

    @staticmethod
    def _row_to_session(row: sqlite3.Row) -> Session:
        return Session(
            id=row["id"],
            title=row["title"],
            collections=json.loads(row["collections"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_message(row: sqlite3.Row) -> Message:
        return Message(
            id=row["id"],
            session_id=row["session_id"],
            role=row["role"],
            content=row["content"],
            sources=json.loads(row["sources"]) if row["sources"] else None,
            metadata=json.loads(row["metadata"]) if row["metadata"] else None,
            created_at=row["created_at"],
        )

    # ── session CRUD ───────────────────────────────────────────────

    def create_session(self, title: str = "", collections: list[str] | None = None) -> Session:
        sid = self._uid()
        now = self._now()
        title = title or "New Chat"
        cols_json = json.dumps(collections if collections is not None else [], ensure_ascii=False)
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                "INSERT INTO sessions (id, title, collections, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (sid, title, cols_json, now, now),
            )
            conn.commit()
        logger.info("Created session %s", sid)
        return Session(id=sid, title=title, collections=collections or [], created_at=now, updated_at=now)

    def list_sessions(self, limit: int = 50) -> list[Session]:
        with self._lock:
            conn = self._get_conn()
            rows = conn.execute(
                "SELECT * FROM sessions ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_session(r) for r in rows]

    def get_session(self, session_id: str) -> Session | None:
        with self._lock:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT * FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
        return self._row_to_session(row) if row else None

    def update_session(self, session_id: str, title: str | None = None) -> Session:
        now = self._now()
        with self._lock:
            conn = self._get_conn()
            if title is not None:
                conn.execute(
                    "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
                    (title, now, session_id),
                )
            else:
                conn.execute(
                    "UPDATE sessions SET updated_at = ? WHERE id = ?",
                    (now, session_id),
                )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            raise ValueError(f"Session {session_id} not found")
        return self._row_to_session(row)

    def delete_session(self, session_id: str) -> bool:
        """Delete session and cascade-delete its messages."""
        with self._lock:
            conn = self._get_conn()
            cur = conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            conn.commit()
            deleted = cur.rowcount > 0
        if deleted:
            logger.info("Deleted session %s", session_id)
        return deleted

    # ── message CRUD ───────────────────────────────────────────────

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        sources: list[dict] | None = None,
        metadata: dict | None = None,
    ) -> Message:
        mid = self._uid()
        now = self._now()
        sources_json = json.dumps(sources, ensure_ascii=False) if sources else None
        meta_json = json.dumps(metadata, ensure_ascii=False) if metadata else None
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                "INSERT INTO messages (id, session_id, role, content, sources, metadata, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (mid, session_id, role, content, sources_json, meta_json, now),
            )
            # Bump session updated_at
            conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (now, session_id),
            )
            conn.commit()
        return Message(
            id=mid, session_id=session_id, role=role, content=content,
            sources=sources, metadata=metadata, created_at=now,
        )

    def get_messages(self, session_id: str, limit: int = 100) -> list[Message]:
        with self._lock:
            conn = self._get_conn()
            rows = conn.execute(
                "SELECT * FROM messages WHERE session_id = ? ORDER BY created_at ASC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        return [self._row_to_message(r) for r in rows]
