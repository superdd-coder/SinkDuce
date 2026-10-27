"""Definitive file suggestions — auto-judge on upload, accumulate, apply/dismiss.

Run: pytest tests/test_file_mgmt_definitive_suggestions.py -v --tb=short
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import time
import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.file_mgmt.routes import router as file_mgmt_router
from src.file_mgmt.store import COLLECTIONS_DIR, EXPECTED_TABLES, init_collection_db


@pytest.fixture(autouse=True)
def cleanup_test_collections():
    existing: set[str] = set()
    if COLLECTIONS_DIR.exists():
        existing = {d.name for d in COLLECTIONS_DIR.iterdir() if d.is_dir()}
    yield
    if COLLECTIONS_DIR.exists():
        for d in COLLECTIONS_DIR.iterdir():
            if d.is_dir() and d.name not in existing:
                shutil.rmtree(d, ignore_errors=True)


def _setup_collection(coll_id: str) -> None:
    from src.collections.store import create_collection_meta

    create_collection_meta(coll_id, f"Test {coll_id}")
    init_collection_db(coll_id)


def _db_path(collection_id: str) -> str:
    return str(COLLECTIONS_DIR / collection_id / "meta.db")


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(file_mgmt_router, prefix="/api/file-mgmt")
    return TestClient(app)


def _insert_file(
    coll_id: str,
    file_id: str | None = None,
    is_definitive: int = 0,
    archived: int = 0,
    filename: str | None = None,
) -> str:
    from src.file_mgmt.store import get_db

    fid = file_id or uuid.uuid4().hex
    now = "2026-09-07T00:00:00Z"
    vid = uuid.uuid4().hex
    name = filename or f"report-{fid[:6]}.pdf"
    conn = get_db(coll_id)
    try:
        conn.execute("PRAGMA defer_foreign_keys=ON")
        conn.execute("BEGIN")
        conn.execute(
            """INSERT INTO file_versions
               (version_id, file_id, version_no, storage_file_id,
                archived, commit_message, created_by, created_at)
               VALUES (?, ?, 1, ?, 0, 'initial', 'local', ?)""",
            (vid, fid, name, now),
        )
        conn.execute(
            """INSERT INTO files
               (file_id, current_version_id, is_definitive, archived,
                unsupported, created_by, version)
               VALUES (?, ?, ?, ?, 0, 'local', 1)""",
            (fid, vid, is_definitive, archived),
        )
        conn.commit()
    finally:
        conn.close()
    return fid


def _insert_suggestion(
    coll_id: str,
    file_id: str,
    *,
    status: str = "pending",
    reason: str = "Looks authoritative",
    summary_hash: str = "h0",
) -> str:
    from src.file_mgmt.store import get_db

    sid = uuid.uuid4().hex
    now = "2026-09-07T00:00:00Z"
    conn = get_db(coll_id)
    try:
        conn.execute(
            """INSERT INTO definitive_suggestions
               (suggestion_id, file_id, reason, summary_hash, status,
                created_at, decided_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, NULL, ?)""",
            (sid, file_id, reason, summary_hash, status, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return sid


def _llm_return(suitable: bool, reason: str = "A quarterly report worth keeping") -> MagicMock:
    llm = MagicMock()
    llm.generate.return_value = json.dumps({"suitable": suitable, "reason": reason})
    return llm


# ════════════════════════════════════════════════════════════════
# Schema
# ════════════════════════════════════════════════════════════════


def test_definitive_suggestions_table_in_schema():
    assert "definitive_suggestions" in EXPECTED_TABLES
    coll = "defsug-schema-1"
    _setup_collection(coll)
    conn = sqlite3.connect(_db_path(coll))
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    conn.close()
    assert "definitive_suggestions" in tables


def test_backfill_creates_table_on_existing_db():
    coll = "defsug-backfill-1"
    _setup_collection(coll)
    conn = sqlite3.connect(_db_path(coll))
    conn.execute("DROP TABLE definitive_suggestions")
    conn.commit()

    from src.file_mgmt.store import _ensure_definitive_suggestions_table

    _ensure_definitive_suggestions_table(conn)
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    conn.close()
    assert "definitive_suggestions" in tables


# ════════════════════════════════════════════════════════════════
# Judge
# ════════════════════════════════════════════════════════════════


def test_judge_suitable_creates_pending():
    from src.file_mgmt import definitive_suggestions as ds

    coll = "defsug-judge-ok"
    _setup_collection(coll)
    fid = _insert_file(coll)

    with (
        patch.object(ds, "_file_short_summary", return_value="Quarterly revenue grew 20%."),
        patch.object(ds, "_get_judge_llm", return_value=_llm_return(True, "Authored quarterly report")),
    ):
        ds.judge_file_for_definitive(coll, fid)

    out = ds.get_definitive_suggestions(coll)
    assert len(out["suggestions"]) == 1
    item = out["suggestions"][0]
    assert item["file_id"] == fid
    assert item["reason"] == "Authored quarterly report"
    assert item["filename"].endswith(".pdf")


def test_judge_unsuitable_not_listed():
    from src.file_mgmt import definitive_suggestions as ds

    coll = "defsug-judge-no"
    _setup_collection(coll)
    fid = _insert_file(coll)

    with (
        patch.object(ds, "_file_short_summary", return_value="Random scratch notes."),
        patch.object(ds, "_get_judge_llm", return_value=_llm_return(False, "Fragments only")),
    ):
        ds.judge_file_for_definitive(coll, fid)

    out = ds.get_definitive_suggestions(coll)
    assert out["suggestions"] == []
    # Unsuitable writes a dismissed row bound to this content's hash
    conn = sqlite3.connect(_db_path(coll))
    row = conn.execute(
        "SELECT status FROM definitive_suggestions WHERE file_id=?", (fid,)
    ).fetchone()
    conn.close()
    assert row[0] == "dismissed"


def test_file_short_summary_reads_via_services_singleton(monkeypatch):
    """Regression: 'from src import services' grabs the MODULE object, which
    never has .db — both summary paths silently returned '' and the judge
    exited with no trace. The Services SINGLETON (from src.services import
    services) carries the Qdrant handle."""
    from src.services import services as svc
    from src.file_mgmt import todo_suggestions as ts

    class _StubDB:
        def scroll_points(self, *, collection, scroll_filter, limit,
                          with_payload, with_vectors):
            assert collection and scroll_filter is not None
            return [{"payload": {"summary": "Quarterly report content."}}], None

    monkeypatch.setattr(svc, "db", _StubDB())
    assert ts._file_short_summary("col-x", "file-x") == "Quarterly report content."


def test_judge_no_summary_is_noop():
    from src.file_mgmt import definitive_suggestions as ds

    coll = "defsug-judge-empty"
    _setup_collection(coll)
    fid = _insert_file(coll)

    with (
        patch.object(ds, "_file_short_summary", return_value=""),
        patch.object(ds, "_get_judge_llm") as mock_llm,
    ):
        ds.judge_file_for_definitive(coll, fid)

    mock_llm.assert_not_called()
    assert ds.get_definitive_suggestions(coll)["suggestions"] == []


def test_judge_no_summary_leaves_trace_in_log(caplog):
    """The 'no summary' exit must be observable — its silence is exactly why
    the dead-summary bug went unnoticed (user asked: judged or never ran?)."""
    import logging as _logging

    from src.file_mgmt import definitive_suggestions as ds

    coll = "defsug-judge-empty-log"
    _setup_collection(coll)
    fid = _insert_file(coll)

    with caplog.at_level(_logging.INFO, logger="file_mgmt.definitive_suggestions"):
        with (
            patch.object(ds, "_file_short_summary", return_value=""),
            patch.object(ds, "_get_judge_llm") as mock_llm,
        ):
            ds.judge_file_for_definitive(coll, fid)

    assert any("no-summary" in r.message for r in caplog.records)
    mock_llm.assert_not_called()


def test_judge_skips_already_definitive():
    from src.file_mgmt import definitive_suggestions as ds

    coll = "defsug-judge-def"
    _setup_collection(coll)
    fid = _insert_file(coll, is_definitive=1)

    with (
        patch.object(ds, "_file_short_summary", return_value="Important doc."),
        patch.object(ds, "_get_judge_llm") as mock_llm,
    ):
        ds.judge_file_for_definitive(coll, fid)

    mock_llm.assert_not_called()
    assert ds.get_definitive_suggestions(coll)["suggestions"] == []


def test_dismissed_same_content_blocks_rejudge():
    """A dismissed suggestion stays hidden while the content is unchanged."""
    from src.file_mgmt import definitive_suggestions as ds

    coll = "defsug-judge-decided"
    _setup_collection(coll)
    fid = _insert_file(coll)
    summary = "Unchanged version content."
    _insert_suggestion(
        coll, fid, status="dismissed", summary_hash=ds._summary_hash(summary)
    )

    with (
        patch.object(ds, "_file_short_summary", return_value=summary),
        patch.object(ds, "_get_judge_llm") as mock_llm,
    ):
        ds.judge_file_for_definitive(coll, fid)

    mock_llm.assert_not_called()
    assert ds.get_definitive_suggestions(coll)["suggestions"] == []
    conn = sqlite3.connect(_db_path(coll))
    row = conn.execute(
        "SELECT status FROM definitive_suggestions WHERE file_id=?", (fid,)
    ).fetchone()
    conn.close()
    assert row[0] == "dismissed"


def test_dismissed_rerejudges_on_new_version_content():
    """Dismiss binds to the content, not the file forever: a new version with
    changed content reopens the evaluation and may re-appear (requirement 6)."""
    from src.file_mgmt import definitive_suggestions as ds

    coll = "defsug-judge-reopen"
    _setup_collection(coll)
    fid = _insert_file(coll)
    _insert_suggestion(coll, fid, status="dismissed", summary_hash="old-content-hash")

    with (
        patch.object(ds, "_file_short_summary", return_value="Rewritten report v2."),
        patch.object(
            ds, "_get_judge_llm", return_value=_llm_return(True, "New version qualifies")
        ),
    ):
        ds.judge_file_for_definitive(coll, fid)

    out = ds.get_definitive_suggestions(coll)["suggestions"]
    assert len(out) == 1
    assert out[0]["reason"] == "New version qualifies"
    conn = sqlite3.connect(_db_path(coll))
    row = conn.execute(
        "SELECT status FROM definitive_suggestions WHERE file_id=?", (fid,)
    ).fetchone()
    conn.close()
    assert row[0] == "pending"


def test_pending_row_not_duplicated():
    from src.file_mgmt import definitive_suggestions as ds

    coll = "defsug-judge-pending"
    _setup_collection(coll)
    fid = _insert_file(coll)
    _insert_suggestion(coll, fid, status="pending")

    with (
        patch.object(ds, "_file_short_summary", return_value="Summary text."),
        patch.object(ds, "_get_judge_llm") as mock_llm,
    ):
        ds.judge_file_for_definitive(coll, fid)

    mock_llm.assert_not_called()
    assert len(ds.get_definitive_suggestions(coll)["suggestions"]) == 1


def test_judge_llm_failure_is_silent():
    from src.file_mgmt import definitive_suggestions as ds

    coll = "defsug-judge-err"
    _setup_collection(coll)
    fid = _insert_file(coll)

    llm = MagicMock()
    llm.generate.side_effect = RuntimeError("boom")
    with (
        patch.object(ds, "_file_short_summary", return_value="Some summary."),
        patch.object(ds, "_get_judge_llm", return_value=llm),
        patch.object(ds, "JUDGE_RETRY_DELAYS", ()),
    ):
        # Must not raise even after exhausting retries
        ds.judge_file_for_definitive(coll, fid)

    assert ds.get_definitive_suggestions(coll)["suggestions"] == []


def test_judge_retries_after_transient_failure():
    from src.file_mgmt import definitive_suggestions as ds

    coll = "defsug-judge-retry"
    _setup_collection(coll)
    fid = _insert_file(coll)

    llm = MagicMock()
    llm.generate.side_effect = [
        RuntimeError("429 rate limited"),
        json.dumps({"suitable": True, "reason": "Recovered on retry"}),
    ]
    with (
        patch.object(ds, "_file_short_summary", return_value="Summary."),
        patch.object(ds, "_get_judge_llm", return_value=llm),
        patch.object(ds, "JUDGE_RETRY_DELAYS", (0.0, 0.0)),
    ):
        ds.judge_file_for_definitive(coll, fid)

    assert llm.generate.call_count == 2
    out = ds.get_definitive_suggestions(coll)["suggestions"]
    assert len(out) == 1
    assert out[0]["reason"] == "Recovered on retry"


def test_judge_llm_concurrency_capped():
    import threading
    import time as _time

    from src.file_mgmt import definitive_suggestions as ds

    coll = "defsug-judge-cap"
    _setup_collection(coll)
    fids = [_insert_file(coll) for _ in range(6)]

    lock = threading.Lock()
    state = {"cur": 0, "max": 0}

    def _gen(*_args, **_kwargs):
        with lock:
            state["cur"] += 1
            state["max"] = max(state["max"], state["cur"])
        _time.sleep(0.05)
        with lock:
            state["cur"] -= 1
        return json.dumps({"suitable": True, "reason": "ok"})

    llm = MagicMock()
    llm.generate.side_effect = _gen
    with (
        patch.object(ds, "_file_short_summary", return_value="Summary."),
        patch.object(ds, "_get_judge_llm", return_value=llm),
        patch.object(ds, "JUDGE_RETRY_DELAYS", ()),
    ):
        threads = [
            threading.Thread(target=ds.judge_file_for_definitive, args=(coll, fid))
            for fid in fids
        ]
        for th in threads:
            th.start()
        for th in threads:
            th.join(timeout=10)

    assert state["max"] <= ds.JUDGE_LLM_CONCURRENCY
    assert len(ds.get_definitive_suggestions(coll)["suggestions"]) == 6


def test_judge_parses_fenced_json():
    from src.file_mgmt import definitive_suggestions as ds

    coll = "defsug-judge-fence"
    _setup_collection(coll)
    fid = _insert_file(coll)

    llm = MagicMock()
    llm.generate.return_value = (
        '```json\n{"suitable": true, "reason": "Contract with signatures"}\n```'
    )
    with (
        patch.object(ds, "_file_short_summary", return_value="Vendor contract 2026."),
        patch.object(ds, "_get_judge_llm", return_value=llm),
    ):
        ds.judge_file_for_definitive(coll, fid)

    out = ds.get_definitive_suggestions(coll)
    assert len(out["suggestions"]) == 1
    assert out["suggestions"][0]["reason"] == "Contract with signatures"


def test_judge_archived_file_skipped():
    from src.file_mgmt import definitive_suggestions as ds

    coll = "defsug-judge-arch"
    _setup_collection(coll)
    fid = _insert_file(coll, archived=1)

    with (
        patch.object(ds, "_file_short_summary", return_value="Summary."),
        patch.object(ds, "_get_judge_llm") as mock_llm,
    ):
        ds.judge_file_for_definitive(coll, fid)

    mock_llm.assert_not_called()


# ════════════════════════════════════════════════════════════════
# API
# ════════════════════════════════════════════════════════════════


def test_get_lists_only_pending_with_file_info():
    coll = "defsug-api-get"
    _setup_collection(coll)
    f1 = _insert_file(coll, filename="annual-report.pdf")
    f2 = _insert_file(coll, filename="scratch.md")
    _insert_suggestion(coll, f1, reason="Annual report")
    _insert_suggestion(coll, f2, status="dismissed")

    resp = _client().get(f"/api/file-mgmt/{coll}/definitive-suggestions")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["suggestions"]) == 1
    item = body["suggestions"][0]
    assert item["file_id"] == f1
    assert item["filename"] == "annual-report.pdf"
    assert item["reason"] == "Annual report"


def test_get_hides_archived_and_missing_files():
    coll = "defsug-api-hide"
    _setup_collection(coll)
    f_arch = _insert_file(coll, archived=1)
    _insert_suggestion(coll, f_arch, reason="x")
    # suggestion for a file that no longer exists
    conn = sqlite3.connect(_db_path(coll))
    conn.execute(
        """INSERT INTO definitive_suggestions
           (suggestion_id, file_id, reason, summary_hash, status,
            created_at, decided_at, updated_at)
           VALUES ('ghost', 'no-such-file', 'r', 'h', 'pending',
                   '2026-09-07T00:00:00Z', NULL, '2026-09-07T00:00:00Z')"""
    )
    conn.commit()
    conn.close()

    resp = _client().get(f"/api/file-mgmt/{coll}/definitive-suggestions")
    assert resp.status_code == 200
    assert resp.json()["suggestions"] == []


def test_stale_pending_on_definitive_file_auto_closes():
    coll = "defsug-api-stale"
    _setup_collection(coll)
    fid = _insert_file(coll, is_definitive=1)
    _insert_suggestion(coll, fid, status="pending")

    resp = _client().get(f"/api/file-mgmt/{coll}/definitive-suggestions")
    assert resp.status_code == 200
    assert resp.json()["suggestions"] == []

    conn = sqlite3.connect(_db_path(coll))
    row = conn.execute(
        "SELECT status FROM definitive_suggestions WHERE file_id=?", (fid,)
    ).fetchone()
    conn.close()
    assert row[0] == "applied"


def test_dismiss_removes_from_list():
    coll = "defsug-api-dismiss"
    _setup_collection(coll)
    fid = _insert_file(coll)
    _insert_suggestion(coll, fid)

    client = _client()
    resp = client.post(f"/api/file-mgmt/{coll}/definitive-suggestions/{fid}/dismiss")
    assert resp.status_code == 200, resp.text

    assert client.get(f"/api/file-mgmt/{coll}/definitive-suggestions").json()[
        "suggestions"
    ] == []
    # Dismiss again is idempotent-ok
    resp2 = client.post(f"/api/file-mgmt/{coll}/definitive-suggestions/{fid}/dismiss")
    assert resp2.status_code == 200


def test_dismiss_unknown_file_returns_404():
    coll = "defsug-api-dismiss-404"
    _setup_collection(coll)
    resp = _client().post(
        f"/api/file-mgmt/{coll}/definitive-suggestions/{uuid.uuid4().hex}/dismiss"
    )
    assert resp.status_code == 404


_PATCH_INFO = (
    patch("src.api.routes.info._snapshot_includes", return_value={}),
)


def _patch_definitive_side_effects(sm=None):
    sm = sm or MagicMock()
    sm.get_doc_summary.return_value = None
    from src.tasks.task_manager import task_manager

    return (
        patch("src.api.routes.info._snapshot_includes", return_value={}),
        patch("src.api.routes.info._get_summary_manager", return_value=sm),
        patch("src.api.routes.info.schedule_debounced_consolidate"),
        patch.object(task_manager, "create_task"),
    )


def test_apply_sets_definitive_and_closes():
    from src.main import app

    coll = "defsug-api-apply"
    _setup_collection(coll)
    fid = _insert_file(coll)
    _insert_suggestion(coll, fid)

    client = TestClient(app)
    patches = _patch_definitive_side_effects()
    with patches[0], patches[1], patches[2], patches[3]:
        resp = client.post(
            f"/api/file-mgmt/{coll}/definitive-suggestions/{fid}/apply"
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["file"]["is_definitive"] is True

    # Removed from list
    assert client.get(f"/api/file-mgmt/{coll}/definitive-suggestions").json()[
        "suggestions"
    ] == []
    # Row closed as applied
    conn = sqlite3.connect(_db_path(coll))
    row = conn.execute(
        "SELECT status FROM definitive_suggestions WHERE file_id=?", (fid,)
    ).fetchone()
    conn.close()
    assert row[0] == "applied"


def test_apply_missing_file_returns_404():
    coll = "defsug-api-apply-404"
    _setup_collection(coll)
    resp = _client().post(
        f"/api/file-mgmt/{coll}/definitive-suggestions/{uuid.uuid4().hex}/apply"
    )
    assert resp.status_code == 404


# ════════════════════════════════════════════════════════════════
# Scheduling
# ════════════════════════════════════════════════════════════════


def test_schedule_judge_runs_in_background():
    from src.file_mgmt import definitive_suggestions as ds

    coll = "defsug-sched"
    _setup_collection(coll)
    fid = _insert_file(coll)

    done = MagicMock()

    def _fake_judge(collection_id: str, file_id: str):
        done(collection_id, file_id)

    with patch.object(ds, "judge_file_for_definitive", _fake_judge):
        ds.schedule_judge(coll, fid, delay=0.01)
        for _ in range(100):
            if done.called:
                break
            time.sleep(0.02)
    assert done.called
    assert done.call_args[0][1] == fid


def test_upload_handler_hook_schedules_judge():
    """The upload handler must schedule a suggestion judgment for managed uploads."""
    import inspect

    from src.tasks import handlers

    src = inspect.getsource(handlers.upload_handler)
    assert "schedule_definitive_suggestion_judge" in src


# ════════════════════════════════════════════════════════════════
# Same-content dedup across files
# ════════════════════════════════════════════════════════════════


def test_judge_skips_when_same_content_pending_elsewhere():
    """The same content uploaded twice (duplicate copies) must not spawn a
    second suggestion while the first is still pending — and must not spend
    an LLM call."""
    from src.file_mgmt import definitive_suggestions as ds

    coll = "defsug-hash-dup"
    _setup_collection(coll)
    fid_a = _insert_file(coll)
    fid_b = _insert_file(coll)

    same_summary = "The QPA records the sale of 90% of the SPV for EUR 602,690."

    llm = _llm_return(True, "Settled record")
    with (
        patch.object(ds, "_file_short_summary", return_value=same_summary),
        patch.object(ds, "_get_judge_llm", return_value=llm),
    ):
        ds.judge_file_for_definitive(coll, fid_a)
        ds.judge_file_for_definitive(coll, fid_b)

    out = ds.get_definitive_suggestions(coll)
    assert len(out["suggestions"]) == 1
    assert out["suggestions"][0]["file_id"] == fid_a
    # Only ONE judge LLM call total — the duplicate short-circuited.
    assert llm.generate.call_count == 1


def test_judge_reruns_when_pending_became_applied_elsewhere_same_content():
    """If the first copy was already applied (definitive), a duplicate copy
    still must not re-suggest — same hash in any non-dismissed state blocks."""
    from src.file_mgmt import definitive_suggestions as ds

    coll = "defsug-hash-applied"
    _setup_collection(coll)
    fid_a = _insert_file(coll)
    fid_b = _insert_file(coll)

    same_summary = "Signed side letter fixing the capital contribution terms."
    llm = _llm_return(True, "Settled")
    with (
        patch.object(ds, "_file_short_summary", return_value=same_summary),
        patch.object(ds, "_get_judge_llm", return_value=llm),
    ):
        # fid_a's row is pending from upload; then user applies it → applied
        ds.judge_file_for_definitive(coll, fid_a)
        ds._close(coll, fid_a, "applied")
        llm.generate.reset_mock()
        ds.judge_file_for_definitive(coll, fid_b)

    out = ds.get_definitive_suggestions(coll)
    assert out["suggestions"] == []
    llm.generate.assert_not_called()


# ════════════════════════════════════════════════════════════════
# Dismiss all (bulk — e.g. a batch of quarterly-report false positives)
# ════════════════════════════════════════════════════════════════


def test_dismiss_all_clears_pending_keeps_decided():
    coll = "defsug-dismiss-all"
    _setup_collection(coll)
    f1 = _insert_file(coll)
    f2 = _insert_file(coll)
    f3 = _insert_file(coll)
    _insert_suggestion(coll, f1, status="pending", summary_hash="h1")
    _insert_suggestion(coll, f2, status="pending", summary_hash="h2")
    _insert_suggestion(coll, f3, status="dismissed", summary_hash="h3")

    resp = _client().post(
        f"/api/file-mgmt/{coll}/definitive-suggestions/dismiss-all"
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["dismissed"] == 2

    assert _client().get(
        f"/api/file-mgmt/{coll}/definitive-suggestions"
    ).json()["suggestions"] == []
    conn = sqlite3.connect(_db_path(coll))
    rows = {
        r[0]: r[1]
        for r in conn.execute(
            "SELECT file_id, status FROM definitive_suggestions"
        ).fetchall()
    }
    conn.close()
    assert rows[f1] == "dismissed"
    assert rows[f2] == "dismissed"
    assert rows[f3] == "dismissed"


def test_dismiss_all_binds_each_decision_to_its_content_hash():
    """Bulk dismissal keeps the per-file hash anchor — a changed-content
    version still reopens evaluation, same as a single dismiss."""
    coll = "defsug-dismiss-all-hash"
    _setup_collection(coll)
    fid = _insert_file(coll)
    _insert_suggestion(coll, fid, status="pending", summary_hash="content-hash-7")

    resp = _client().post(
        f"/api/file-mgmt/{coll}/definitive-suggestions/dismiss-all"
    )
    assert resp.status_code == 200

    conn = sqlite3.connect(_db_path(coll))
    row = conn.execute(
        "SELECT status, summary_hash FROM definitive_suggestions WHERE file_id=?",
        (fid,),
    ).fetchone()
    conn.close()
    assert row[0] == "dismissed"
    assert row[1] == "content-hash-7"


def test_dismiss_all_skips_rows_the_dialog_never_shows():
    """Archived files' pending rows stay pending (unarchiving may still show
    them), and stale pending on an already-definitive file is left for the
    list endpoint's auto-close-to-applied — bulk dismiss must not flip it."""
    coll = "defsug-dismiss-all-hidden"
    _setup_collection(coll)
    f_arch = _insert_file(coll, archived=1)
    f_def = _insert_file(coll, is_definitive=1)
    _insert_suggestion(coll, f_arch, status="pending")
    _insert_suggestion(coll, f_def, status="pending")

    resp = _client().post(
        f"/api/file-mgmt/{coll}/definitive-suggestions/dismiss-all"
    )
    assert resp.status_code == 200
    assert resp.json()["dismissed"] == 0

    conn = sqlite3.connect(_db_path(coll))
    rows = {
        r[0]: r[1]
        for r in conn.execute(
            "SELECT file_id, status FROM definitive_suggestions"
        ).fetchall()
    }
    conn.close()
    assert rows[f_arch] == "pending"
    assert rows[f_def] == "pending"


def test_dismiss_all_empty_is_ok():
    coll = "defsug-dismiss-all-empty"
    _setup_collection(coll)
    resp = _client().post(
        f"/api/file-mgmt/{coll}/definitive-suggestions/dismiss-all"
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "dismissed": 0}
