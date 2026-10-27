"""Definitive file suggestions — post-upload LLM judgment + accumulate + decide.

After a file ingest stores its auto-generated short summary, a background
judge decides whether the file is a good candidate for ``is_definitive``
(sources that feed Collection Summary). Suggestions accumulate in the
per-collection ``definitive_suggestions`` ledger; the Library Overview shows
a badge while pending ones exist.

Dedup contract (one row per file; decisions bind to the content hash):
- ``pending``    → the single open suggestion.
- ``dismissed``  → hidden (user dismissal or machine rejection share the same
  rule); re-judged only when the summary hash changes on a later version
  upload — a rewritten file may re-appear, unchanged content never does.
- ``applied``    → file became definitive; skipped while it stays definitive
  (hash-bound like the others if the user later clears the flag).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
import uuid
from datetime import datetime, timezone

logger = logging.getLogger("file_mgmt.definitive_suggestions")

# Small settle delay after ingest so the Qdrant write is visible.
JUDGE_DELAY_SEC = 2.0

# Process-wide cap on concurrent judge LLM calls. Judges ride behind bulk
# uploads (bounded by the 5-slot upload queue, but a folder drop can still
# complete several ingests in the same settle window) — keep well under
# provider rate limits.
JUDGE_LLM_CONCURRENCY = 2
_judge_semaphore = threading.Semaphore(JUDGE_LLM_CONCURRENCY)

# Backoff between judge attempts after a failed / unparseable LLM call.
# Exhausted retries leave no ledger row, so the next version upload re-judges.
JUDGE_RETRY_DELAYS = (10.0, 30.0)

_timers_lock = threading.Lock()
_timers: dict[tuple[str, str], threading.Timer] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _open(collection_id: str):
    from src.file_mgmt.store import get_db, init_collection_db

    init_collection_db(collection_id)
    return get_db(collection_id)


def _summary_hash(short_summary: str) -> str:
    return hashlib.sha256(short_summary.strip().encode("utf-8")).hexdigest()


def _file_short_summary(collection_id: str, file_id: str) -> str:
    from src.file_mgmt.todo_suggestions import _file_short_summary as _fetch

    return _fetch(collection_id, file_id)


def _get_judge_llm(collection_id: str):
    """Resolve judge LLM (collection override → enrichment → default)."""
    from src.tasks.handlers import _get_enriching_llm
    from src.services import services

    config: dict = {}
    try:
        if services.db is not None:
            config = services.db.get_collection_config(collection_id) or {}
    except Exception:
        config = {}
    return _get_enriching_llm(config)


def _parse_verdict(text: str) -> dict | None:
    """Parse {"suitable": bool, "reason": str} out of an LLM reply."""
    raw = (text or "").strip()
    if not raw:
        return None
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    data = None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", raw)
        if m:
            try:
                data = json.loads(m.group(0))
            except json.JSONDecodeError:
                return None
    if not isinstance(data, dict):
        return None
    suitable = data.get("suitable")
    if not isinstance(suitable, bool):
        return None
    reason = str(data.get("reason") or "").strip()
    return {"suitable": suitable, "reason": reason[:300]}


# ════════════════════════════════════════════════════════════════
# Ledger operations
# ════════════════════════════════════════════════════════════════


def _row_status(collection_id: str, file_id: str) -> str | None:
    conn = _open(collection_id)
    try:
        row = conn.execute(
            "SELECT status FROM definitive_suggestions WHERE file_id=?",
            (file_id,),
        ).fetchone()
        return row["status"] if row else None
    finally:
        conn.close()


def _ledger_row(collection_id: str, file_id: str):
    """Full ledger row for a file (status + content hash), or None."""
    conn = _open(collection_id)
    try:
        return conn.execute(
            "SELECT status, summary_hash FROM definitive_suggestions WHERE file_id=?",
            (file_id,),
        ).fetchone()
    finally:
        conn.close()


def _close(collection_id: str, file_id: str, status: str) -> None:
    conn = _open(collection_id)
    try:
        with conn:
            conn.execute(
                """UPDATE definitive_suggestions
                   SET status=?, decided_at=COALESCE(decided_at, ?), updated_at=?
                   WHERE file_id=?""",
                (status, _now_iso(), _now_iso(), file_id),
            )
    finally:
        conn.close()


def _upsert_verdict(
    collection_id: str,
    file_id: str,
    *,
    suitable: bool,
    reason: str,
    summary_hash: str,
) -> None:
    status = "pending" if suitable else "dismissed"
    conn = _open(collection_id)
    try:
        with conn:
            conn.execute(
                """INSERT INTO definitive_suggestions
                   (suggestion_id, file_id, reason, summary_hash, status,
                    created_at, decided_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, NULL, ?)
                   ON CONFLICT(file_id) DO UPDATE SET
                     reason=excluded.reason,
                     summary_hash=excluded.summary_hash,
                     status=excluded.status,
                     decided_at=NULL,
                     updated_at=excluded.updated_at""",
                (
                    uuid.uuid4().hex,
                    file_id,
                    reason,
                    summary_hash,
                    status,
                    _now_iso(),
                    _now_iso(),
                ),
            )
    finally:
        conn.close()


def get_definitive_suggestions(collection_id: str) -> dict:
    """List pending suggestions with file display info.

    Auto-closes stale pending rows: a file already definitive becomes
    ``applied``; deleted / file-archived files simply drop out of the list.
    """
    conn = _open(collection_id)
    try:
        with conn:
            conn.execute(
                """UPDATE definitive_suggestions
                   SET status='applied',
                       decided_at=COALESCE(decided_at, ?),
                       updated_at=?
                   WHERE status='pending'
                     AND file_id IN (SELECT file_id FROM files WHERE is_definitive=1)""",
                (_now_iso(), _now_iso()),
            )
        rows = conn.execute(
            """SELECT s.suggestion_id, s.file_id, s.reason, s.created_at,
                      fv.storage_file_id AS filename,
                      f.source, f.source_label
               FROM definitive_suggestions s
               JOIN files f ON f.file_id = s.file_id
               LEFT JOIN file_versions fv ON fv.version_id = f.current_version_id
               WHERE s.status='pending' AND f.archived=0
               ORDER BY s.created_at DESC, s.rowid DESC"""
        ).fetchall()
    finally:
        conn.close()

    from src.file_mgmt.service import _attachment_display_fields, _load_file_index

    try:
        file_index = _load_file_index(collection_id)
    except Exception:
        file_index = {}

    items = []
    for r in rows:
        names = _attachment_display_fields(
            collection_id,
            r["file_id"],
            r["filename"] or "",
            index=file_index,
        )
        items.append(
            {
                "suggestion_id": r["suggestion_id"],
                "file_id": r["file_id"],
                "filename": names.get("filename") or r["filename"] or "",
                "display_name": names.get("display_name") or "",
                "original_ext": _ext_of(r["filename"] or ""),
                "doc_kind": _kind_of(r["source"]),
                "reason": r["reason"] or "",
                "created_at": r["created_at"],
            }
        )
    return {"suggestions": items}


def _ext_of(filename: str) -> str:
    name = (filename or "").strip()
    if "." not in name:
        return ""
    return name.rsplit(".", 1)[-1].lower()


def _kind_of(source: str | None) -> str:
    src = (source or "").strip()
    if src.startswith("__meeting__:"):
        return "meeting"
    if src.startswith("__note__:"):
        return "note"
    return "file"


# ════════════════════════════════════════════════════════════════
# User decisions
# ════════════════════════════════════════════════════════════════


def _mark_dismissed(collection_id: str, file_id: str, summary_hash: str) -> None:
    """Write/flip a dismissed row anchored to the given content hash."""
    now = _now_iso()
    conn = _open(collection_id)
    try:
        with conn:
            conn.execute(
                """INSERT INTO definitive_suggestions
                   (suggestion_id, file_id, reason, summary_hash, status,
                    created_at, decided_at, updated_at)
                   VALUES (?, ?, '', ?, 'dismissed', ?, ?, ?)
                   ON CONFLICT(file_id) DO UPDATE SET
                     status='dismissed', updated_at=excluded.updated_at""",
                (uuid.uuid4().hex, file_id, summary_hash, now, now, now),
            )
    finally:
        conn.close()


def dismiss_suggestion(collection_id: str, file_id: str) -> dict:
    """Dismiss a suggestion. The decision binds to the content hash: the file
    stays hidden until a new version with changed content reopens evaluation.
    Dismissing without a prior suggestion anchors to the current content."""
    from fastapi import HTTPException

    conn = _open(collection_id)
    try:
        f = conn.execute(
            "SELECT 1 FROM files WHERE file_id=?", (file_id,)
        ).fetchone()
        if not f:
            raise HTTPException(404, f"File '{file_id}' not found")
        row = conn.execute(
            "SELECT status, summary_hash FROM definitive_suggestions WHERE file_id=?",
            (file_id,),
        ).fetchone()
    finally:
        conn.close()

    if row and row["status"] == "dismissed":
        return {"ok": True, "file_id": file_id, "status": "dismissed"}

    if row:
        _mark_dismissed(collection_id, file_id, row["summary_hash"])
    else:
        # Anchor the dismissal to the current content (empty hash when no
        # summary is available yet — a later summary will re-judge).
        try:
            short = _file_short_summary(collection_id, file_id)
            s_hash = _summary_hash(short) if short.strip() else ""
        except Exception:
            s_hash = ""
        _mark_dismissed(collection_id, file_id, s_hash)
    return {"ok": True, "file_id": file_id, "status": "dismissed"}


def dismiss_all_suggestions(collection_id: str) -> dict:
    """Dismiss every pending suggestion the dialog shows (file present, not
    archived, not already definitive). Each decision keeps its own
    content-hash anchor, exactly like a single dismissal — rows the dialog
    never shows (archived files, stale pending on definitive files) are left
    untouched."""
    conn = _open(collection_id)
    try:
        rows = conn.execute(
            """SELECT s.file_id, s.summary_hash
               FROM definitive_suggestions s
               JOIN files f ON f.file_id = s.file_id
               WHERE s.status='pending' AND f.archived=0 AND f.is_definitive=0"""
        ).fetchall()
    finally:
        conn.close()
    for r in rows:
        _mark_dismissed(collection_id, r["file_id"], r["summary_hash"])
    return {"ok": True, "dismissed": len(rows)}


def apply_suggestion(collection_id: str, file_id: str) -> dict:
    """Accept the suggestion: set is_definitive via the normal update path.

    Reuses ``service.update_file`` so every product side-effect fires
    (summary include flag, debounced consolidate, file.updated event).
    """
    from fastapi import HTTPException

    from src.file_mgmt import service

    conn = _open(collection_id)
    try:
        row = conn.execute(
            "SELECT version FROM files WHERE file_id=?", (file_id,)
        ).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(404, f"File '{file_id}' not found")

    # Optimistic lock; one retry rides over a concurrent version bump.
    last_err: Exception | None = None
    for _ in range(2):
        try:
            file_out = service.update_file(
                collection_id, file_id, {"is_definitive": True, "version": row["version"]}
            )
            break
        except HTTPException as e:
            if e.status_code != 409:
                raise
            last_err = e
            conn = _open(collection_id)
            try:
                fresh = conn.execute(
                    "SELECT version FROM files WHERE file_id=?", (file_id,)
                ).fetchone()
            finally:
                conn.close()
            if not fresh:
                raise HTTPException(404, f"File '{file_id}' not found")
            row = fresh
    else:
        raise last_err or HTTPException(409, "Could not apply suggestion")

    _close(collection_id, file_id, "applied")
    return {"ok": True, "file_id": file_id, "status": "applied", "file": file_out}


# ════════════════════════════════════════════════════════════════
# Judgment
# ════════════════════════════════════════════════════════════════


def judge_file_for_definitive(collection_id: str, file_id: str) -> None:
    """Best-effort sync judgment — never raises, safe to run in a thread."""
    try:
        _judge_inner(collection_id, file_id)
    except Exception:
        logger.warning(
            "definitive judge failed col=%s file=%s",
            collection_id,
            file_id,
            exc_info=True,
        )


def _judge_verdict_with_retries(
    collection_id: str, filename_label: str, short: str
) -> dict | None:
    """Call the judge LLM under the global semaphore, with backoff retries.

    Returns the parsed verdict, or None when every attempt failed — in which
    case no ledger row is written and the next version upload re-judges.
    """
    from src.prompts import (
        DEFINITIVE_SUGGEST_SYSTEM_PROMPT,
        DEFINITIVE_SUGGEST_USER_PROMPT,
    )

    llm = _get_judge_llm(collection_id)
    if llm is None:
        logger.warning(
            "definitive judge skip col=%s reason=no-llm "
            "(no enrichment/default LLM configured)",
            collection_id,
        )
        return None
    user_prompt = DEFINITIVE_SUGGEST_USER_PROMPT.format(
        filename=filename_label, summary=short[:1500]
    )

    last_err: Exception | None = None
    for attempt in range(len(JUDGE_RETRY_DELAYS) + 1):
        if attempt:
            time.sleep(JUDGE_RETRY_DELAYS[attempt - 1])
        try:
            with _judge_semaphore:
                text = llm.generate(
                    user_prompt,
                    system=DEFINITIVE_SUGGEST_SYSTEM_PROMPT,
                    temperature=0.2,
                    max_tokens=300,
                    thinking=False,
                )
        except Exception as e:
            last_err = e
            logger.warning(
                "definitive judge attempt %d/%d failed col=%s: %s",
                attempt + 1,
                len(JUDGE_RETRY_DELAYS) + 1,
                collection_id,
                e,
            )
            continue
        verdict = _parse_verdict(text)
        if verdict is not None:
            return verdict
        last_err = ValueError(f"unparseable judge reply: {text[:120]!r}")
        logger.warning(
            "definitive judge attempt %d/%d unparseable col=%s",
            attempt + 1,
            len(JUDGE_RETRY_DELAYS) + 1,
            collection_id,
        )
    if last_err is not None:
        logger.warning(
            "definitive judge exhausted retries col=%s: %s", collection_id, last_err
        )
    return None


def _hash_taken_elsewhere(collection_id: str, summary_hash: str, file_id: str) -> bool:
    """True when another file already holds a suggestion row for this content.

    Only non-dismissed states block (pending / applied): a dismissed row is
    that file's own machine rejection and must not swallow different files
    that happen to share the same content.
    """
    conn = _open(collection_id)
    try:
        row = conn.execute(
            """SELECT 1 FROM definitive_suggestions
               WHERE summary_hash=? AND file_id<>? AND status IN ('pending','applied')
               LIMIT 1""",
            (summary_hash, file_id),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def _judge_inner(collection_id: str, file_id: str) -> None:
    conn = _open(collection_id)
    try:
        frow = conn.execute(
            "SELECT is_definitive, archived FROM files WHERE file_id=?",
            (file_id,),
        ).fetchone()
    finally:
        conn.close()
    if not frow:
        logger.info(
            "definitive judge skip col=%s file=%s reason=file-missing",
            collection_id, file_id,
        )
        return
    if frow["is_definitive"] or frow["archived"]:
        # Already decided by the user (or excluded from the library) — close
        # any stale pending row so the badge never lingers.
        reason = "already-definitive" if frow["is_definitive"] else "archived"
        logger.info(
            "definitive judge skip col=%s file=%s reason=%s",
            collection_id, file_id, reason,
        )
        if _row_status(collection_id, file_id) == "pending":
            _close(collection_id, file_id, "applied" if frow["is_definitive"] else "dismissed")
        return

    row = _ledger_row(collection_id, file_id)
    if row and row["status"] == "pending":
        logger.info(
            "definitive judge skip col=%s file=%s reason=pending-exists",
            collection_id, file_id,
        )
        return

    short = _file_short_summary(collection_id, file_id)
    if not short.strip():
        # Observable on purpose — a silent exit here once hid a dead summary
        # path (user could not tell "rejected" from "never ran").
        logger.warning(
            "definitive judge skip col=%s file=%s reason=no-summary "
            "(no short summary in Qdrant payload or doc summary)",
            collection_id, file_id,
        )
        return
    s_hash = _summary_hash(short)
    if row and row["summary_hash"] == s_hash:
        # Already decided for this exact content (user dismissal or machine
        # rejection) — only a changed content version reopens evaluation.
        logger.info(
            "definitive judge skip col=%s file=%s reason=decided-same-content",
            collection_id, file_id,
        )
        return
    if _hash_taken_elsewhere(collection_id, s_hash, file_id):
        # Another file with the same content already has a suggestion
        # (pending or applied). Duplicate copies / re-uploads must not
        # multiply suggestions — one verdict per content.
        logger.info(
            "definitive judge skip col=%s file=%s reason=same-content-suggested-elsewhere",
            collection_id, file_id,
        )
        return

    names = _display_label(collection_id, file_id)
    verdict = _judge_verdict_with_retries(collection_id, names, short)
    if verdict is None:
        # No LLM / exhausted retries — leave no row so the next version
        # upload re-judges this file.
        return

    _upsert_verdict(
        collection_id,
        file_id,
        suitable=verdict["suitable"],
        reason=verdict["reason"],
        summary_hash=s_hash,
    )
    logger.info(
        "definitive judge col=%s file=%s suitable=%s",
        collection_id,
        file_id,
        verdict["suitable"],
    )


def _display_label(collection_id: str, file_id: str) -> str:
    conn = _open(collection_id)
    try:
        row = conn.execute(
            """SELECT fv.storage_file_id AS filename, f.source_label
               FROM files f
               LEFT JOIN file_versions fv ON fv.version_id = f.current_version_id
               WHERE f.file_id=?""",
            (file_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return file_id
    return (row["source_label"] or "").strip() or (row["filename"] or "").strip() or file_id


# ════════════════════════════════════════════════════════════════
# Scheduling (post-upload hook)
# ════════════════════════════════════════════════════════════════


def _timer_fire(collection_id: str, file_id: str) -> None:
    with _timers_lock:
        _timers.pop((collection_id, file_id), None)
    judge_file_for_definitive(collection_id, file_id)


def schedule_judge(
    collection_id: str, file_id: str, delay: float = JUDGE_DELAY_SEC
) -> None:
    """Background-judge a freshly ingested file.

    All gating (already definitive, unchanged-content dismissal, pending
    duplicate) happens inside the judge itself — scheduling is always cheap
    and safe.
    """
    if not collection_id or not file_id:
        return
    with _timers_lock:
        key = (collection_id, file_id)
        old = _timers.get(key)
        if old is not None:
            try:
                old.cancel()
            except Exception:
                pass
        t = threading.Timer(delay, _timer_fire, args=(collection_id, file_id))
        t.daemon = True
        _timers[key] = t
        t.start()


def schedule_definitive_suggestion_judge(
    collection_id: str, file_id: str, delay: float = JUDGE_DELAY_SEC
) -> None:
    """Public alias for the upload-handler hook."""
    try:
        schedule_judge(collection_id, file_id, delay=delay)
    except Exception:
        logger.warning(
            "definitive suggestion schedule failed col=%s file=%s",
            collection_id,
            file_id,
            exc_info=True,
        )
