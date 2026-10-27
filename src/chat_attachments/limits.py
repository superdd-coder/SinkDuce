"""Persistent, atomic admission for attachment storage and background work."""
from __future__ import annotations

import os
import shutil
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from src.chat_attachments import ownership
from src.chat_attachments.models import Attachment


@dataclass(frozen=True)
class AttachmentLimits:
    session_files: int = 100
    actor_files: int = 500
    installation_files: int = 2000
    session_bytes: int = 500 * 1024**2
    actor_bytes: int = 2 * 1024**3
    installation_bytes: int = 10 * 1024**3
    session_uploads: int = 5
    actor_uploads: int = 5
    installation_uploads: int = 10
    pending_parses: int = 25
    actor_parses: int = 10
    running_parses: int = 5


LIMITS = AttachmentLimits()
MIN_FREE_BYTES = 16 * 1024**2


class ResourceLimit(ValueError):
    def __init__(self, message: str, *, status_code: int = 413, scope: str = "installation"):
        super().__init__(message)
        self.status_code = status_code
        self.scope = scope


@dataclass(frozen=True)
class Lease:
    id: str
    session_id: str
    token: str


class ResourceLedger:
    """All workers sharing a data directory share the same capacity budget.

    Source attribution survives restarts. Active work retains a lease until its
    worker exits, even if the user deletes its source in the meantime.
    """

    def __init__(self, root: Path, session_owner=None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / ".uploads").mkdir(exist_ok=True)
        self.path = self.root / ".resources.sqlite3"
        with self._transaction() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS resources (
                id TEXT PRIMARY KEY, session_id TEXT NOT NULL, actor TEXT NOT NULL,
                bytes INTEGER NOT NULL, retained INTEGER NOT NULL DEFAULT 1,
                phase TEXT NOT NULL, job_actor TEXT NOT NULL DEFAULT '',
                pid INTEGER NOT NULL DEFAULT 0, token TEXT NOT NULL,
                wants_parse INTEGER NOT NULL DEFAULT 0,
                incarnation TEXT NOT NULL DEFAULT ''
            )""")
            if "incarnation" not in {row["name"] for row in db.execute("PRAGMA table_info(resources)")}:
                db.execute("ALTER TABLE resources ADD COLUMN incarnation TEXT NOT NULL DEFAULT ''")
            db.execute("CREATE INDEX IF NOT EXISTS resources_session ON resources(session_id)")
            db.execute("CREATE INDEX IF NOT EXISTS resources_actor ON resources(actor)")
            db.execute("CREATE TABLE IF NOT EXISTS migrations (name TEXT PRIMARY KEY)")
            if not db.execute("SELECT 1 FROM migrations WHERE name='legacy-sources'").fetchone():
                for path in self.root.glob("*/*/metadata.json"):
                    try:
                        a = Attachment.model_validate_json(path.read_text(encoding="utf-8"))
                        if a.session_id != path.parent.parent.name or a.id != path.parent.name:
                            continue
                        source = path.parent / ("source" + Path(a.filename).suffix.lower())
                        owner = session_owner(a.session_id) if session_owner else None
                        db.execute("""INSERT OR IGNORE INTO resources
                            (id, session_id, actor, bytes, phase, token) VALUES (?, ?, ?, ?, 'idle', '')""",
                            (a.id, a.session_id, owner or "local", source.stat().st_size))
                    except (OSError, ValueError):
                        continue
                db.execute("INSERT INTO migrations VALUES ('legacy-sources')")

    @contextmanager
    def _transaction(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _reap(self, db):
        # No timeout can safely release a worker that is still running. Reclaim
        # only leases whose owning incarnation has exited; keep committed sources.
        dead = {(row["pid"], row["incarnation"]) for row in db.execute(
            "SELECT DISTINCT pid,incarnation FROM resources WHERE phase != 'idle'")
            if not ownership.alive(self.root, row["incarnation"])}
        for owner in dead:
            for row in db.execute("SELECT * FROM resources WHERE pid=? AND incarnation=? AND phase='upload'", owner):
                self.upload_path(Lease(row["id"], row["session_id"], row["token"])).unlink(missing_ok=True)
                shutil.rmtree(self.root / row["session_id"] / row["id"], ignore_errors=True)
            db.execute("DELETE FROM resources WHERE pid=? AND incarnation=? AND (phase='upload' OR retained=0)", owner)
            db.execute("""UPDATE resources SET phase='idle', pid=0, incarnation='', token='', wants_parse=0
                WHERE pid=? AND incarnation=?""", owner)

    @staticmethod
    def _work_counts(db, actor):
        return db.execute("""SELECT COUNT(*) AS total,
            COALESCE(SUM(job_actor=?), 0) AS actor FROM resources
            WHERE phase IN ('queued','running') OR (phase='upload' AND wants_parse=1)""", (actor,)).fetchone()

    def _check_parse(self, db, actor):
        counts = self._work_counts(db, actor)
        if counts["total"] >= LIMITS.pending_parses or counts["actor"] >= LIMITS.actor_parses:
            raise ResourceLimit("Attachment parsing is busy. Retry after current files finish.", status_code=429)

    def reserve(self, sid: str, actor: str, max_bytes: int, *, parse: bool = True, check=None) -> Lease:
        # Session IDs become disk paths only after this explicit validation.
        from src.chat_attachments.store import _ID
        if not _ID.fullmatch(sid):
            raise ValueError("Invalid attachment or session ID")
        lease = Lease(uuid.uuid4().hex, sid, uuid.uuid4().hex)
        with self._transaction() as db:
            self._reap(db)
            if check:
                check()
            reserved = db.execute("SELECT COALESCE(SUM(bytes),0) FROM resources WHERE phase='upload'").fetchone()[0]
            if shutil.disk_usage(self.root).free < reserved + max_bytes + MIN_FREE_BYTES:
                raise ResourceLimit("Attachment storage is nearly full. Free disk space before uploading.", status_code=507)
            for scope, where, values in (
                ("session", "session_id=?", (sid,)),
                ("actor", "actor=?", (actor,)),
                ("installation", "1=1", ()),
            ):
                usage = db.execute(f"""SELECT COUNT(*) AS files, COALESCE(SUM(bytes),0) AS bytes
                    FROM resources WHERE (retained=1 OR phase='upload') AND {where}""", values).fetchone()
                if usage["files"] >= getattr(LIMITS, f"{scope}_files") or usage["bytes"] + max_bytes > getattr(LIMITS, f"{scope}_bytes"):
                    raise ResourceLimit(f"The {scope} attachment storage limit has been reached. Remove unused files or conversations.", scope=scope)
                uploads = db.execute(f"SELECT COUNT(*) FROM resources WHERE phase='upload' AND {where}", values).fetchone()[0]
                if uploads >= getattr(LIMITS, f"{scope}_uploads"):
                    raise ResourceLimit("Too many attachment uploads. Retry after current uploads finish.", status_code=429, scope=scope)
            if parse:
                self._check_parse(db, actor)
            db.execute("""INSERT INTO resources
                (id,session_id,actor,bytes,phase,job_actor,pid,incarnation,token,wants_parse)
                VALUES (?,?,?,?,'upload',?,?,?,?,?)""",
                (lease.id, sid, actor, max_bytes, actor, os.getpid(), ownership.identity(self.root), lease.token, int(parse)))
        return lease

    def upload_path(self, lease: Lease) -> Path:
        return self.root / ".uploads" / f"{lease.id}.part"

    def commit(self, lease: Lease, size: int, publish) -> None:
        with self._transaction() as db:
            row = db.execute("SELECT * FROM resources WHERE id=? AND token=? AND phase='upload' AND retained=1",
                             (lease.id, lease.token)).fetchone()
            if row is None:
                raise ResourceLimit("The upload was cancelled or its conversation was deleted.", status_code=409)
            if not 0 < size <= row["bytes"]:
                raise ValueError("Invalid attachment size")
            publish()
            db.execute("UPDATE resources SET bytes=?, phase=? WHERE id=?",
                       (size, "queued" if row["wants_parse"] else "idle", lease.id))

    def abort(self, lease: Lease) -> None:
        with self._transaction() as db:
            db.execute("DELETE FROM resources WHERE id=? AND token=? AND phase='upload'", (lease.id, lease.token))
        self.upload_path(lease).unlink(missing_ok=True)

    def queue(self, sid: str, aid: str, actor: str) -> Lease:
        lease = Lease(aid, sid, uuid.uuid4().hex)
        with self._transaction() as db:
            self._reap(db)
            row = db.execute("SELECT * FROM resources WHERE id=? AND session_id=? AND retained=1", (aid, sid)).fetchone()
            if row is None:
                raise ResourceLimit("Attachment not found", status_code=404)
            if row["phase"] != "idle":
                raise ResourceLimit("This attachment is already being parsed", status_code=409)
            self._check_parse(db, actor)
            db.execute("UPDATE resources SET phase='queued', job_actor=?, pid=?, incarnation=?, token=?, wants_parse=1 WHERE id=?",
                       (actor, os.getpid(), ownership.identity(self.root), lease.token, aid))
        return lease

    def try_start(self, lease: Lease) -> str:
        with self._transaction() as db:
            self._reap(db)
            row = db.execute("SELECT phase FROM resources WHERE id=? AND token=? AND retained=1", (lease.id, lease.token)).fetchone()
            if row is None or row["phase"] != "queued":
                return "missing"
            if db.execute("SELECT COUNT(*) FROM resources WHERE phase='running'").fetchone()[0] >= LIMITS.running_parses:
                return "wait"
            db.execute("UPDATE resources SET phase='running' WHERE id=?", (lease.id,))
            return "started"

    def finish_job(self, lease: Lease) -> None:
        with self._transaction() as db:
            db.execute("DELETE FROM resources WHERE id=? AND token=? AND retained=0", (lease.id, lease.token))
            db.execute("""UPDATE resources SET phase='idle', pid=0, incarnation='', token='', wants_parse=0
                WHERE id=? AND token=? AND phase IN ('queued','running')""", (lease.id, lease.token))

    def active(self, sid: str, aid: str) -> bool:
        with self._transaction() as db:
            self._reap(db)
            return db.execute("""SELECT 1 FROM resources WHERE id=? AND session_id=?
                AND phase IN ('queued','running') AND retained=1""", (aid, sid)).fetchone() is not None

    def delete(self, sid: str, aid: str | None, remove) -> None:
        with self._transaction() as db:
            where, values = ("session_id=? AND id=?", (sid, aid)) if aid else ("session_id=?", (sid,))
            remove()
            # A deleted upload can still own an open file and a live connection.
            # Keep its bytes/upload/parse reservations until its request closes;
            # otherwise repeated session resets bypass every admission budget.
            db.execute(f"""UPDATE resources SET retained=0,
                bytes=CASE WHEN phase='upload' THEN bytes ELSE 0 END
                WHERE {where} AND phase IN ('upload','running')""", values)
            db.execute(f"DELETE FROM resources WHERE {where} AND phase NOT IN ('upload','running')", values)
