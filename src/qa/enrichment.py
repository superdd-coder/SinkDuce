"""Retryable summaries independent of workbook matching."""

import threading
from weakref import WeakValueDictionary

from src.prompts import QA_SUMMARY_SYSTEM

from . import intelligence, metadata, service, store

_locks = WeakValueDictionary()
_guard = threading.Lock()


def summarize(collection, lid, revision):
    from .pipeline import batches, cached_calls

    with _guard:
        lock = _locks.setdefault((collection, lid), threading.Lock())
    if not lock.acquire(blocking=False):
        return
    try:
        snapshot = service.detail(collection, lid)
        if snapshot["revision"] != revision or not snapshot["can_edit"]:
            return
        vid = snapshot["version"]["id"]
        rows = [
            {"key": q["id"], "text": q["text"], "latest_reply": q["latest_reply"]}
            for q in snapshot["questions"]
            if q["included"] and not q["summary"]
        ]
        if not rows:
            return
        with store.connection(collection, "update") as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            listing = store.get_list(conn, lid)
            if listing["current_version_id"] != vid or listing["revision"] != snapshot["revision"]:
                return
            keys = {r["key"]: service.annotation_key(conn, vid, r["key"]) for r in rows}
            meta = metadata.read(conn, vid)
            meta["annotation_state"] = "running"
            metadata.save(conn, vid, meta)
        jobs = [
            (
                QA_SUMMARY_SYSTEM,
                {"stage": "summaries", "rows": batch},
                intelligence.Annotations,
            )
            for batch in batches(rows, limit=8)
        ]
        for output in cached_calls(jobs, {}):
            if not output:
                continue
            with store.connection(collection, "update") as conn, conn:
                conn.execute("BEGIN IMMEDIATE")
                listing = store.get_list(conn, lid)
                if listing["current_version_id"] != vid or listing["archived"]:
                    return
                for row in output.rows:
                    if service.annotation_key(conn, vid, row.key) == keys[row.key]:
                        service.save_annotation(conn, vid, row.key, row.model_dump())
        with store.connection(collection, "update") as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            if store.get_list(conn, lid)["current_version_id"] == vid:
                meta = metadata.read(conn, vid)
                # Detail derives readiness from valid summaries.
                meta["annotation_state"] = "partial"
                metadata.save(conn, vid, meta)
    finally:
        lock.release()
