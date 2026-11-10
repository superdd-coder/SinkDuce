"""Current Q&A passages backed by a durable, project-local vector index."""

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import math
import os
import struct
import uuid
from collections import defaultdict
from contextlib import closing

from src.collections.store import get_collection_meta
from src.file_mgmt import store as files
from src.identity import can, get_actor
from src.identity.policy import may_see_everything
from src.identity.roles import get_role
from src.rag import collection_utils
from src.rag.chunker import ParagraphChunker, estimate_chunking_tokens
from src.rag.retriever import RetrievedChunk

from . import store

logger = logging.getLogger(__name__)

_BATCH_SIZE = 32
_MAX_EMBED_TOKENS = 2048

_PARTS = """
    WITH questions AS (
        SELECT q.*,l.id AS list_id
        FROM qa_lists l JOIN qa_version_questions q ON q.version_id=l.current_version_id
        WHERE l.id=? AND l.archived=0 AND q.included=1
    )
    SELECT q.question_id,'' AS column_id,t.id AS revision_id,t.body
    FROM questions q JOIN qa_text_revisions t ON t.id=q.text_revision_id
    UNION ALL
    SELECT q.question_id,c.column_id,t.id,t.body
    FROM questions q JOIN qa_version_cells c
        ON c.version_id=q.version_id AND c.question_id=q.question_id
    JOIN qa_version_columns col ON col.version_id=c.version_id AND col.column_id=c.column_id
    JOIN qa_text_revisions t ON t.id=c.text_revision_id
    WHERE col.included=1 AND t.body!=''
"""


_CURRENT = """
       SELECT p.*, l.current_version_id, l.title AS list_title, t.body,
              qt.body AS question, c.label AS round_label
       FROM qa_index_points p JOIN qa_lists l ON l.id=p.list_id
       JOIN qa_version_questions q ON q.version_id=l.current_version_id AND q.question_id=p.question_id
       JOIN qa_text_revisions qt ON qt.id=q.text_revision_id
       LEFT JOIN qa_version_columns c ON c.version_id=q.version_id AND c.column_id=p.column_id
       LEFT JOIN qa_version_cells r ON r.version_id=q.version_id AND r.question_id=q.question_id
           AND r.column_id=c.column_id
       JOIN qa_text_revisions t ON t.id=p.revision_id
           AND t.id=CASE WHEN p.column_id='' THEN q.text_revision_id ELSE r.text_revision_id END
       WHERE p.state='ready' AND l.archived=0 AND q.included=1
           AND (p.column_id='' OR c.included=1)
"""

def can_read(collection, *, historical=False):
    meta = get_collection_meta(collection)
    if not meta or (meta.get("archived") and not historical):
        return False
    actor = get_actor()
    role = get_role(actor.role_id)
    return bool(
        (may_see_everything(actor) or (role and role.allows_module("database")))
        and can(actor, "qa.read", {"collection_id": collection})
    )


def _exists(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (name,)).fetchone() is not None


def has_questions(collection):
    """Cheap probe for Chat routing, including projects awaiting backfill."""
    if not can_read(collection) or not files.db_initialized(collection):
        return False
    with closing(files.get_db(collection)) as conn:
        if not _exists(conn, "qa_lists"):
            return False
        return conn.execute(
            """SELECT 1 FROM qa_lists l
               JOIN qa_version_questions q ON q.version_id=l.current_version_id
               WHERE l.archived=0 AND q.included=1 LIMIT 1"""
        ).fetchone() is not None


def _embedding_config(db, collection):
    from src.services import services

    if services.config is None:
        return None, ""
    config = collection_utils.get_collection_embedding_config(
        db.get_collection_config(collection), collection
    )
    if not config or config.provider in ("", "none") or config.dimensions <= 0:
        return None, ""
    identity = [
        config.provider, config.model.strip(), config.base_url.strip().rstrip("/"),
        config.dimensions, "qa-body-v1",
    ]
    return config, _digest(json.dumps(identity))


def _digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _fragments(body):
    text = body.strip()
    if not text:
        return []
    if estimate_chunking_tokens(text) <= _MAX_EMBED_TOKENS:
        return [text]
    return ParagraphChunker(max_tokens=_MAX_EMBED_TOKENS, buffer_ratio=0).chunk(text)


def _batches(items, size=_BATCH_SIZE):
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _project_active(collection):
    meta = get_collection_meta(collection)
    return bool(meta and not meta.get("archived"))


def _generation(conn, list_id):
    row = conn.execute("SELECT generation FROM qa_index_jobs WHERE list_id=?", (list_id,)).fetchone()
    return row[0] if row else None


def _plan(conn, list_id, generation, model_key):
    """Reserve point IDs before Qdrant I/O. Retired IDs are never reused."""
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        if _generation(conn, list_id) != generation:
            return [], []
        existing = defaultdict(list)
        obsolete = set()
        for row in conn.execute("SELECT * FROM qa_index_points WHERE list_id=?", (list_id,)):
            obsolete.add(row["point_id"])
            if row["model_key"] != model_key:
                continue
            if row["state"] == "ready" or (
                row["state"] == "pending" and row["generation"] == generation
            ):
                key = (row["question_id"], row["column_id"], row["revision_id"], row["fragment"])
                existing[key].append(row)

        pending = []
        for part in conn.execute(_PARTS, (list_id,)):
            for fragment, text in enumerate(_fragments(part["body"])):
                key = (part["question_id"], part["column_id"], part["revision_id"], fragment)
                rows = existing.get(key, ())
                point = next((row for row in rows if row["state"] == "ready"), None)
                if point is not None:
                    obsolete.remove(point["point_id"])
                    continue
                point_id = (
                    rows[0]["point_id"] if rows
                    else str(uuid.uuid5(uuid.NAMESPACE_URL, json.dumps([generation, *key])))
                )
                obsolete.discard(point_id)
                conn.execute(
                    """INSERT OR IGNORE INTO qa_index_points VALUES(?,?,?,?,?,?,?,?,'pending')""",
                    (point_id, list_id, *key[:3], model_key, fragment, generation),
                )
                pending.append((point_id, part, fragment, text))

        conn.executemany(
            "UPDATE qa_index_points SET state='retired' WHERE point_id=?",
            [(pid,) for pid in obsolete],
        )
        return pending, sorted(obsolete)


def _vectors(conn, config, model_key, list_id, generation, passages, provider):
    texts = {_digest(item[3]): item[3] for item in passages}
    fmt = f"<{config.dimensions}f"
    vectors = {}
    for digest in texts:
        row = conn.execute(
            "SELECT vector FROM qa_index_vectors WHERE model_key=? AND digest=?", (model_key, digest)
        ).fetchone()
        if row:
            vectors[digest] = list(struct.unpack(fmt, row["vector"]))
    missing = [digest for digest in texts if digest not in vectors]
    if missing:
        computed = provider.embed_texts([texts[digest] for digest in missing])
        if len(computed) != len(missing) or any(
            len(vector) != config.dimensions or not all(math.isfinite(x) for x in vector)
            for vector in computed
        ):
            raise ValueError("Invalid Q&A embedding output")
        vectors.update(zip(missing, computed))

    with conn:
        conn.execute("BEGIN IMMEDIATE")
        if _generation(conn, list_id) != generation:
            return None
        conn.executemany(
            "INSERT OR IGNORE INTO qa_index_vectors VALUES(?,?,?)",
            [(model_key, digest, struct.pack(fmt, *vectors[digest])) for digest in missing],
        )
        conn.executemany(
            "INSERT OR IGNORE INTO qa_index_vector_owners VALUES(?,?,?)",
            [(list_id, model_key, digest) for digest in texts],
        )
    return [vectors[_digest(item[3])] for item in passages]


def _sync_list(conn, db, collection, config, model_key, job):
    from src.tasks.task_manager import check_cancelled

    list_id, generation = job["list_id"], job["generation"]
    pending, retired = _plan(conn, list_id, generation, model_key)
    for ids in _batches(retired, 500):
        check_cancelled()
        db.delete_points(collection, ids)
        with conn:
            conn.executemany("DELETE FROM qa_index_points WHERE point_id=?", [(pid,) for pid in ids])
    if pending:
        provider = collection_utils.create_embedding_provider(config)
        for batch in _batches(pending):
            check_cancelled()
            if not _project_active(collection) or _generation(conn, list_id) != generation:
                return 0
            vectors = _vectors(conn, config, model_key, list_id, generation, batch, provider)
            check_cancelled()
            if vectors is None or not _project_active(collection):
                return 0
            payloads = [
                {
                    "source_kind": "qa_reply" if part["column_id"] else "qa_question",
                    "qa_list_id": list_id,
                    "qa_question_id": part["question_id"],
                    "qa_column_id": part["column_id"],
                    "qa_text_revision_id": part["revision_id"],
                    "qa_model_key": model_key,
                    "qa_fragment": fragment,
                }
                for _, part, fragment, _ in batch
            ]
            ids = [item[0] for item in batch]
            db.upsert_points(collection, ids, vectors, payloads)
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                if _generation(conn, list_id) != generation or not _project_active(collection):
                    return 0  # The next job retires these unpublished IDs.
                conn.executemany(
                    "UPDATE qa_index_points SET state='ready' WHERE point_id=?",
                    [(pid,) for pid in ids],
                )
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        if _generation(conn, list_id) != generation:
            return 0
        if conn.execute("SELECT 1 FROM qa_lists WHERE id=?", (list_id,)).fetchone():
            conn.execute("UPDATE qa_index_jobs SET finished=1 WHERE list_id=?", (list_id,))
        else:
            conn.execute("DELETE FROM qa_index_vector_owners WHERE list_id=?", (list_id,))
            conn.execute(
                """DELETE FROM qa_index_vectors WHERE NOT EXISTS (
                   SELECT 1 FROM qa_index_vector_owners o
                   WHERE o.model_key=qa_index_vectors.model_key AND o.digest=qa_index_vectors.digest)"""
            )
            conn.execute("DELETE FROM qa_index_jobs WHERE list_id=?", (list_id,))
    return len(pending)


def _project_jobs(conn, model_key):
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        for sql in store.SEARCH_SCHEMA:
            conn.execute(sql)
        conn.execute(
            """INSERT OR IGNORE INTO qa_index_jobs(list_id,generation)
               SELECT id,lower(hex(randomblob(16))) FROM qa_lists WHERE current_version_id IS NOT NULL"""
        )
        conn.execute(
            """UPDATE qa_index_jobs SET generation=lower(hex(randomblob(16))),model_key=?,finished=0
               WHERE model_key!=?""",
            (model_key, model_key),
        )
    return conn.execute("SELECT * FROM qa_index_jobs WHERE finished=0 ORDER BY list_id").fetchall()


def sync_project(collection, task=None):
    from src.services import services
    from src.tasks.task_manager import check_cancelled

    db = services.db
    if db is None or not _project_active(collection) or not files.db_initialized(collection):
        return {"indexed": 0}
    if not db.collection_exists(collection):
        return {"indexed": 0}
    config, model_key = _embedding_config(db, collection)
    if not model_key:
        raise RuntimeError("Project embedding is unavailable")
    lock_path = files._db_path(collection).with_suffix(".qa-index.lock")
    with os.fdopen(os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600), "rb") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"indexed": 0}
        with closing(files.get_db(collection)) as conn:
            if not _exists(conn, "qa_lists"):
                return {"indexed": 0}
            jobs = _project_jobs(conn, model_key)
            indexed = 0
            failure = None
            for count, job in enumerate(jobs, 1):
                check_cancelled()
                if not _project_active(collection):
                    break
                try:
                    indexed += _sync_list(conn, db, collection, config, model_key, job)
                except Exception as exc:
                    check_cancelled()
                    failure = failure or exc
                if task:
                    task.progress = count * 100 / len(jobs)
            if failure is not None:
                raise failure
            return {"indexed": indexed}


def _current_rows(conn, ids):
    rows = {}
    for batch in _batches(ids, 500):
        placeholders = ",".join("?" for _ in batch)
        for row in conn.execute(
            f"SELECT * FROM ({_CURRENT}) WHERE point_id IN ({placeholders})", batch
        ):
            rows[row["point_id"]] = row
    return rows


def retrieve(collection, query, top_k):
    from src.services import services

    db = services.db
    if db is None or not can_read(collection) or not files.db_initialized(collection):
        return []
    with closing(files.get_db(collection)) as conn:
        if not _exists(conn, "qa_index_points"):
            return []
        config, model_key = _embedding_config(db, collection)
        if not model_key:
            return []
        ids = [
            row[0] for row in conn.execute(
                f"SELECT point_id FROM ({_CURRENT}) WHERE model_key=?", (model_key,)
            )
        ]
        if not ids:
            return []
        embedding = collection_utils.create_embedding_provider(config)
        points = db.search_qa(collection, embedding.embed_query(query), ids, top_k)
        if not can_read(collection):
            return []
        rows = _current_rows(conn, [point["id"] for point in points])
        chunks = []
        for point in points:
            row = rows.get(point["id"])
            if not row or row["model_key"] != model_key:
                continue
            fragments = _fragments(row["body"])
            if row["fragment"] >= len(fragments):
                continue
            text = fragments[row["fragment"]]
            hint = f"Q&A list: {row['list_title']}"
            if row["column_id"]:
                hint += f"\nQuestion: {row['question'][:600]}\nReply: {row['round_label']}"
            chunks.append(RetrievedChunk(text, point["score"], {
                "id": point["id"], "collection": collection,
                "source": row["list_title"], "context": hint,
                "source_kind": "qa_reply" if row["column_id"] else "qa_question",
                "qa_list_id": row["list_id"], "qa_question_id": row["question_id"],
                "qa_column_id": row["column_id"], "qa_text_revision_id": row["revision_id"],
                "qa_fragment": row["fragment"], "qa_model_key": row["model_key"],
            }))
        return chunks


def pending_projects():
    """Discover upgrades, committed changes, and model changes without opening new databases."""
    from src.collections.store import list_collections_meta
    from src.services import services

    db = services.db
    if db is None:
        return []
    projects = []
    for meta in list_collections_meta():
        collection = meta["id"]
        if meta.get("archived") or not files.db_initialized(collection):
            continue
        try:
            with closing(files.get_db(collection)) as conn:
                if not _exists(conn, "qa_lists"):
                    continue
                has_lists = conn.execute(
                    "SELECT 1 FROM qa_lists WHERE current_version_id IS NOT NULL LIMIT 1"
                ).fetchone()
                has_jobs = _exists(conn, "qa_index_jobs") and conn.execute(
                    "SELECT 1 FROM qa_index_jobs LIMIT 1"
                ).fetchone()
                if not (has_lists or has_jobs):
                    continue
                if not db.collection_exists(collection):
                    continue
                _, model_key = _embedding_config(db, collection)
                if not model_key:
                    continue
                if not has_jobs or conn.execute(
                    """SELECT 1 FROM qa_index_jobs WHERE finished=0 OR model_key!=?
                       UNION ALL
                       SELECT 1 FROM qa_lists WHERE current_version_id IS NOT NULL AND
                       id NOT IN (SELECT list_id FROM qa_index_jobs) LIMIT 1""",
                    (model_key,),
                ).fetchone():
                    projects.append(collection)
        except Exception as exc:
            logger.warning("Q&A index discovery failed (%s)", type(exc).__name__)
    return projects


async def index_task(task, collection):
    import asyncio
    from src.tasks.task_manager import clear_current_task, set_current_task

    def run():
        set_current_task(task.id)
        try:
            return sync_project(collection, task)
        finally:
            clear_current_task()

    task.stage = "indexing"
    try:
        return await asyncio.to_thread(run)
    except Exception:
        # Provider errors can echo request text.
        raise RuntimeError("Q&A indexing failed") from None


async def watch_pending():
    import asyncio
    import time

    from src.tasks.task_manager import TaskStatus, task_manager

    latest = {}
    while True:
        try:
            projects = await asyncio.to_thread(pending_projects)
        except Exception as exc:
            logger.warning("Q&A index discovery failed (%s)", type(exc).__name__)
            projects = []
        for collection in projects:
            if task_manager.has_active_task(collection, "qa_index"):
                continue
            task_id, retry_at = latest.get(collection, ("", 0))
            previous = task_manager.get_task(task_id)
            if previous and previous.status == TaskStatus.FAILED:
                if time.monotonic() < retry_at:
                    continue
                task = task_manager.retry_task(task_id)
            else:
                task = await task_manager.create_task_async(
                    "Q&A index", task_type="qa_index", collection=collection
                )
            if task:
                latest[collection] = (task.id, time.monotonic() + 60)
        await asyncio.sleep(5)
