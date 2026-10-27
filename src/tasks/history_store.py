"""Persistent ingest history for upload tasks.

One JSON file per collection (``data/task_history/<collection_id>.json``)
recording every file ingest as a row: queued → processing → success/failed,
with the failing stage and error.  Survives restarts and queue clears so
the Library "Ingest" dialog can list the full upload history and offer
per-file retry.

Writes go through a module-level cache + lock; every mutation flushes to
disk (cheap at this scale: rows are small, mutations are a handful per
file).  Upload tasks only — background maintenance (sparse_recalc,
doc_summary, consolidate) is deliberately not recorded here.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from src.config import DATA_DIR

logger = logging.getLogger("tasks.history_store")

HISTORY_DIR = DATA_DIR / "task_history"
MAX_RECORDS = 1000

_LOCK = threading.RLock()
_cache: dict[str, dict] = {}

TERMINAL_STATUSES = {"success", "failed"}


def _now() -> str:
    # Millisecond precision — same-second enqueues must sort in true order.
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")[:-4] + "Z"


def _path(collection_id: str) -> Path:
    return HISTORY_DIR / f"{collection_id}.json"


def _load(collection_id: str) -> dict:
    data = _cache.get(collection_id)
    if data is not None:
        return data
    path = _path(collection_id)
    try:
        if path.is_file():
            data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        logger.warning("[IngestHistory] corrupt history for %s, resetting", collection_id)
    if not isinstance(data, dict) or not isinstance(data.get("records"), list):
        data = {"records": []}
    _cache[collection_id] = data
    return data


def _flush(collection_id: str) -> None:
    path = _path(collection_id)
    try:
        from src.atomic_io import write_text_atomic

        data = _cache.get(collection_id) or {"records": []}
        write_text_atomic(path, json.dumps(data, ensure_ascii=False))
    except OSError:
        logger.warning("[IngestHistory] flush failed for %s", collection_id, exc_info=True)


def _find_by_task(data: dict, task_id: str) -> dict | None:
    for row in reversed(data["records"]):
        if row.get("task_id") == task_id:
            return row
    return None


def record_queued(
    collection_id: str,
    task_id: str,
    filename: str,
    kwargs: dict | None = None,
    file_id: str = "",
) -> dict:
    """Create (or reset) the history row for an enqueued upload task.

    Re-enqueueing a known task_id (crash recovery) reuses and resets the
    existing row instead of duplicating it.
    """
    with _LOCK:
        data = _load(collection_id)
        row = _find_by_task(data, task_id)
        if row is None:
            row = {
                "record_id": uuid.uuid4().hex,
                "created_at": _now(),
            }
            data["records"].append(row)
        row.update({
            "task_id": task_id,
            "filename": filename,
            "collection_id": collection_id,
            "file_id": file_id or (kwargs or {}).get("file_id", ""),
            "task_type": "upload",
            "status": "queued",
            "stage": "",
            "error": "",
            "attempt": row.get("attempt", 0),
            "finished_at": "",
            "kwargs": kwargs or {},
        })
        _prune(data)
        _flush(collection_id)
        return dict(row)


def mark_processing(collection_id: str, task_id: str) -> None:
    with _LOCK:
        data = _load(collection_id)
        row = _find_by_task(data, task_id)
        if row is None:
            return
        row["status"] = "processing"
        _flush(collection_id)


def mark_success(collection_id: str, task_id: str) -> None:
    with _LOCK:
        data = _load(collection_id)
        row = _find_by_task(data, task_id)
        if row is None:
            return
        row["status"] = "success"
        row["finished_at"] = _now()
        row["error"] = ""
        _flush(collection_id)


def mark_failed(collection_id: str, task_id: str, error: str, stage: str = "") -> None:
    with _LOCK:
        data = _load(collection_id)
        row = _find_by_task(data, task_id)
        if row is None:
            return
        row["status"] = "failed"
        row["error"] = (error or "")[:500]
        if stage:
            row["stage"] = stage
        row["finished_at"] = _now()
        _flush(collection_id)


def mark_interrupted(exclude_task_ids: set[str] | None = None) -> int:
    """Fail rows left queued/processing by a dead process.

    Called once at startup BEFORE ledger recovery re-enqueues its tasks:
    pass the task ids about to be recovered so their rows stay queued.
    Returns the number of rows marked.  Scans every collection file.
    """
    exclude = exclude_task_ids or set()
    marked = 0
    with _LOCK:
        if not HISTORY_DIR.is_dir():
            return 0
        for path in HISTORY_DIR.glob("*.json"):
            collection_id = path.stem
            data = _load(collection_id)
            changed = 0
            for row in data["records"]:
                if row.get("status") in TERMINAL_STATUSES:
                    continue
                if row.get("task_id") in exclude:
                    continue
                row["status"] = "failed"
                row["stage"] = "interrupted"
                row["error"] = "Interrupted by app restart"
                row["finished_at"] = _now()
                marked += 1
                changed += 1
            if changed:
                _flush(collection_id)
    return marked


def retry_record(collection_id: str, record_id: str, new_task_id: str) -> dict | None:
    """Reset a failed row to queued under a fresh task id (attempt+1).

    Only failed rows are retryable; returns the updated row or None.
    """
    with _LOCK:
        data = _load(collection_id)
        row = next(
            (r for r in reversed(data["records"]) if r.get("record_id") == record_id),
            None,
        )
        if row is None or row.get("status") != "failed":
            return None
        row["status"] = "queued"
        row["task_id"] = new_task_id
        row["attempt"] = row.get("attempt", 0) + 1
        row["stage"] = ""
        row["error"] = ""
        row["finished_at"] = ""
        _flush(collection_id)
        return dict(row)


def get_record_by_task(collection_id: str, task_id: str) -> dict | None:
    with _LOCK:
        data = _load(collection_id)
        row = _find_by_task(data, task_id)
        return dict(row) if row else None


def get_record(collection_id: str, record_id: str) -> dict | None:
    """Full row (kwargs included) by record_id — used by the retry flow."""
    with _LOCK:
        data = _load(collection_id)
        row = next(
            (r for r in reversed(data["records"]) if r.get("record_id") == record_id),
            None,
        )
        return dict(row) if row else None


def list_records(collection_id: str) -> list[dict]:
    """All rows for a collection, newest first, kwargs stripped."""
    with _LOCK:
        data = _load(collection_id)
        rows = [dict(r) for r in reversed(data["records"])]
    for r in rows:
        r.pop("kwargs", None)
    return rows


def status_counts(collection_id: str) -> dict[str, int]:
    counts = {"queued": 0, "processing": 0, "success": 0, "failed": 0}
    for row in list_records(collection_id):
        status = row.get("status")
        if status in counts:
            counts[status] += 1
    return counts


def _prune(data: dict) -> None:
    """FIFO cap so a 300-file batch cannot grow the file unboundedly."""
    records = data["records"]
    if len(records) > MAX_RECORDS:
        data["records"] = records[-MAX_RECORDS:]
