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
            cur_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            if cur_mode.lower() != "wal":
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

    def create_session(self, title: str = "", collections: list[str] | None = None, session_id: str | None = None) -> Session:
        sid = session_id or self._uid()
        now = self._now()
        title = title or "New Chat"
        cols_json = json.dumps(collections if collections is not None else [], ensure_ascii=False)
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                "INSERT OR IGNORE INTO sessions (id, title, collections, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (sid, title, cols_json, now, now),
            )
            conn.commit()
            # If the session already existed (IGNORE'd), fetch the existing one
            row = conn.execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone()
        if row:
            logger.info("Created session %s" if not session_id else "Using session %s", sid)
            return self._row_to_session(row)
        # Should not happen, but safety fallback
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

    def _fetch_all_messages(self, session_id: str) -> list[Message]:
        """Load every row for a session in chronological order (no window)."""
        with self._lock:
            conn = self._get_conn()
            rows = conn.execute(
                "SELECT * FROM messages WHERE session_id = ? ORDER BY created_at ASC",
                (session_id,),
            ).fetchall()
        return [self._row_to_message(r) for r in rows]

    @staticmethod
    def _is_dialogue_message(m: Message) -> bool:
        """True for user turns and final assistant answers (not tool placeholders)."""
        if m.role == "user":
            return True
        if m.role == "assistant":
            content = (m.content or "").strip()
            meta = m.metadata if isinstance(m.metadata, dict) else {}
            if not content and meta.get("tool_calls"):
                return False
            return bool(content)
        return False

    def _apply_dialogue_window(
        self,
        all_msgs: list[Message],
        *,
        max_dialogue: int,
    ) -> list[Message]:
        """Keep all system rows + the last *max_dialogue* dialogue units.

        Dialogue unit = ``user`` or final ``assistant`` answer. Tool-call
        placeholders and ``tool`` rows after the cut stay with those turns.
        System rows (e.g. meeting transcript) never count toward the budget
        and are always returned first.
        """
        if not all_msgs:
            return []
        if max_dialogue <= 0:
            return list(all_msgs)

        system_msgs = [m for m in all_msgs if m.role == "system"]
        dialogue_indices = [
            i for i, m in enumerate(all_msgs) if self._is_dialogue_message(m)
        ]
        if len(dialogue_indices) <= max_dialogue:
            return list(all_msgs)

        keep_from_idx = dialogue_indices[-max_dialogue]
        tail = [
            m for i, m in enumerate(all_msgs)
            if i >= keep_from_idx and m.role != "system"
        ]
        return system_msgs + tail

    def get_messages(
        self,
        session_id: str,
        limit: int | None = 100,
    ) -> list[Message]:
        """Return messages in chronological order.

        *limit* is the max number of **dialogue units** (each ``user`` message
        and each final ``assistant`` answer), not raw DB rows. Tool rows that
        belong to kept turns are included. ``role=system`` (e.g. meeting
        transcript) is always kept and does not count toward *limit*.

        Pass ``limit=None`` for the full unwindowed history (UI / trim / counts).
        """
        all_msgs = self._fetch_all_messages(session_id)
        if limit is None:
            return all_msgs
        return self._apply_dialogue_window(all_msgs, max_dialogue=limit)

    def get_context_messages(
        self,
        session_id: str,
        *,
        max_dialogue: int = 32,
        scan_limit: int | None = None,  # retained for call-compat; unused
    ) -> list[Message]:
        """LLM context window — same semantics as :meth:`get_messages`(*max_dialogue*).

        Catalog and speaker mapping are **never** stored as session messages;
        they are injected ephemerally in ``ChatboxAgent._build_messages`` only.
        """
        del scan_limit  # noqa: F841 — accepted for backward-compatible kwargs
        return self.get_messages(session_id, limit=max_dialogue)

    def count_messages(self, session_id: str, exclude_system: bool = False) -> int:
        """Count total messages in a session.

        When *exclude_system* is True, system messages are excluded from the
        count.  This is useful for truncation logic where system messages
        (e.g. transcript context) should not count toward the message limit.
        """
        with self._lock:
            conn = self._get_conn()
            if exclude_system:
                row = conn.execute(
                    "SELECT COUNT(*) FROM messages WHERE session_id = ? AND role != 'system'",
                    (session_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*) FROM messages WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
        return row[0] if row else 0

    def count_dialogue_messages(self, session_id: str) -> int:
        """Count user + final-assistant rows (not tool-call rounds).

        Prefer :meth:`count_dialogue_turns` for UI "round" counters
        (1 user + 1 reply = 1 turn).
        """
        msgs = self.get_messages(session_id, limit=None)
        return sum(1 for m in msgs if self._is_dialogue_message(m))

    def count_dialogue_turns(self, session_id: str) -> int:
        """Count Q&A rounds: one ``user`` message = one turn (request + reply).

        Tool rows and assistant messages do not add to the turn count.
        """
        msgs = self.get_messages(session_id, limit=None)
        return sum(1 for m in msgs if m.role == "user")

    def trim_messages(self, session_id: str, keep_last: int) -> int:
        """Delete oldest messages, keeping only the most recent *keep_last*.

        System messages (role='system') are **never** deleted — they carry
        persistent context such as transcript text that must survive
        truncation.  Returns number of messages deleted.
        """
        with self._lock:
            conn = self._get_conn()
            # Always keep system messages + the most recent *keep_last* non-system messages
            system_rows = conn.execute(
                "SELECT id FROM messages WHERE session_id = ? AND role = 'system'",
                (session_id,),
            ).fetchall()
            system_ids = {r["id"] for r in system_rows}

            non_system_rows = conn.execute(
                "SELECT id FROM messages WHERE session_id = ? AND role != 'system' ORDER BY created_at DESC LIMIT ?",
                (session_id, keep_last),
            ).fetchall()
            keep_ids = system_ids | {r["id"] for r in non_system_rows}
            if not keep_ids:
                return 0
            placeholders = ",".join("?" * len(keep_ids))
            cur = conn.execute(
                f"DELETE FROM messages WHERE session_id = ? AND id NOT IN ({placeholders})",
                (session_id, *keep_ids),
            )
            conn.commit()
            deleted = cur.rowcount
        if deleted:
            logger.info("Trimmed %d messages from session %s, kept %d", deleted, session_id, keep_last)
        return deleted

    def trim_to_dialogue_messages(self, session_id: str, keep_dialogue: int) -> int:
        """Backward-compatible alias: *keep_dialogue* is treated as **turns** (user rows)."""
        return self.trim_to_dialogue_turns(session_id, keep_dialogue)

    def trim_to_dialogue_turns(self, session_id: str, keep_turns: int) -> int:
        """Trim so only the last *keep_turns* Q&A rounds remain.

        A turn starts at a ``user`` message and includes following tool/assistant
        rows until the next user. System messages (e.g. meeting transcript) are
        never deleted. Returns number of rows deleted.
        """
        if keep_turns <= 0:
            return 0
        msgs = self.get_messages(session_id, limit=None)
        user_ids = [m.id for m in msgs if m.role == "user"]
        if len(user_ids) <= keep_turns:
            return 0
        keep_from_id = user_ids[-keep_turns]
        delete_ids: list[str] = []
        cut = False
        for m in msgs:
            if m.id == keep_from_id:
                cut = True
            if cut:
                continue
            if m.role == "system":
                continue
            delete_ids.append(m.id)
        if not delete_ids:
            return 0
        with self._lock:
            conn = self._get_conn()
            placeholders = ",".join("?" * len(delete_ids))
            cur = conn.execute(
                f"DELETE FROM messages WHERE session_id = ? AND id IN ({placeholders})",
                (session_id, *delete_ids),
            )
            conn.commit()
            deleted = cur.rowcount
        if deleted:
            logger.info(
                "Trimmed %d msgs from session %s to keep %d dialogue turns",
                deleted, session_id, keep_turns,
            )
        return deleted
