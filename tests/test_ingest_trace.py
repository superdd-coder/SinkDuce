import time
from pathlib import Path

from src.rag.ingest_trace import IngestTrace, find_trace, load_trace
from src.tasks.handlers import _record_enrich_trace


def test_ingest_trace_roundtrip(tmp_path: Path):
    trace = IngestTrace(
        tmp_path,
        {"file_id": "abc", "version_id": "v1", "filename": "doc.pdf", "collection": "col"},
    )
    trace.set_config(contextual_enabled=True, is_visual=False, enrich_model="m1")
    trace.add("parse", "Parse", detail="local, 12 chars", data={"chars": 12})
    trace.finish("ok")

    loaded = load_trace(tmp_path)
    assert loaded is not None
    assert loaded["status"] == "ok"
    assert loaded["file_id"] == "abc"
    assert loaded["config"]["enrich_model"] == "m1"
    assert loaded["steps"][0]["id"] == "parse"
    assert isinstance(loaded.get("duration_ms"), int)
    assert loaded["duration_ms"] >= 0
    assert (tmp_path / "ingest_trace.json").is_file()


def test_record_enrich_trace_summary_and_context(tmp_path: Path):
    trace = IngestTrace(tmp_path, {"filename": "a.pdf"})
    _record_enrich_trace(
        trace,
        {
            "summary_attempts": 2,
            "summary_ok": True,
            "short_summary": "A proposal for Project X.",
            "structured_summary": "===DATA===\n- 1",
            "cache_wait_s": 3,
            "summary_ms": 1200,
            "context_ms": 800,
            "context_ran": True,
            "context_batches": 1,
            "context_written": 2,
            "context_skipped_image_only": 0,
            "contexts": [
                {"index": 0, "context": "Project X overview.", "chunk_preview": "Intro"},
                {"index": 1, "context": "", "chunk_preview": "Table"},
            ],
        },
    )
    loaded = load_trace(tmp_path)
    ids = [s["id"] for s in loaded["steps"]]
    assert ids == ["summary", "cache_wait", "context"]
    ctx = next(s for s in loaded["steps"] if s["id"] == "context")
    assert ctx["data"]["written"] == 2
    assert ctx["data"]["contexts"][0]["context"] == "Project X overview."
    assert next(s for s in loaded["steps"] if s["id"] == "summary")["ms"] == 1200
    assert next(s for s in loaded["steps"] if s["id"] == "context")["ms"] == 800
    assert next(s for s in loaded["steps"] if s["id"] == "cache_wait")["ms"] == 3000
    summary = next(s for s in loaded["steps"] if s["id"] == "summary")
    wait = next(s for s in loaded["steps"] if s["id"] == "cache_wait")
    ctx = next(s for s in loaded["steps"] if s["id"] == "context")
    assert "started_ms" in summary and "ended_ms" in summary
    assert wait["started_ms"] == summary["ended_ms"]
    assert ctx["started_ms"] == wait["ended_ms"]
    assert ctx["ended_ms"] == ctx["started_ms"] + 800


def test_record_enrich_trace_cache_wait_not_failed_when_summary_ok(tmp_path: Path):
    from src.rag.ingest_trace import IngestTrace, load_trace
    from src.tasks.handlers import _record_enrich_trace

    trace = IngestTrace(tmp_path, {"filename": "a.pdf"})
    _record_enrich_trace(
        trace,
        {
            "summary_attempts": 1,
            "summary_ok": True,
            "short_summary": "A short overview.",
            "cache_wait_s": 0,
            "context_ran": True,
            "context_batches": 1,
            "context_written": 1,
            "contexts": [],
        },
    )
    loaded = load_trace(tmp_path)
    wait = next(s for s in loaded["steps"] if s["id"] == "cache_wait")
    assert wait["status"] == "skip"
    assert "failed" not in wait["detail"].lower()
    assert "3s" in wait["detail"] or "prefix-cache" in wait["detail"].lower()


def test_ingest_trace_update_running_step(tmp_path: Path):
    trace = IngestTrace(tmp_path, {"filename": "doc.pdf"})
    trace.add("ocr", "OCR classification", status="running", detail="Started")
    trace.update("ocr", status="ok", detail="3 images", ms=1200)
    loaded = load_trace(tmp_path)
    ocr_steps = [s for s in loaded["steps"] if s["id"] == "ocr"]
    assert len(ocr_steps) == 1
    assert ocr_steps[0]["status"] == "ok"
    assert ocr_steps[0]["ms"] == 1200
    assert ocr_steps[0]["detail"] == "3 images"
    assert ocr_steps[0]["ended_ms"] == ocr_steps[0]["started_ms"] + 1200
    assert ocr_steps[0].get("started_at")
    assert ocr_steps[0].get("ended_at")


def test_overlapping_steps_keep_independent_windows(tmp_path: Path):
    trace = IngestTrace(tmp_path, {"filename": "doc.pdf"})
    t0 = time.time()
    trace.add("ocr", "OCR", status="running", started_at=t0)
    trace.add("summary", "Summary", status="running", started_at=t0)
    trace.update("ocr", status="ok", ms=80)
    trace.update("summary", status="ok", ms=240)
    loaded = load_trace(tmp_path)
    ocr = next(s for s in loaded["steps"] if s["id"] == "ocr")
    summary = next(s for s in loaded["steps"] if s["id"] == "summary")
    assert ocr["started_ms"] == summary["started_ms"]
    assert ocr["ended_ms"] < summary["ended_ms"]
    assert ocr["started_ms"] < summary["ended_ms"]
    assert summary["started_ms"] < ocr["ended_ms"]


def test_normalize_legacy_trace_ocr_then_vision_then_summary_wait_context():
    """Schema-1 flush times must reconstruct the real pipeline order."""
    from src.rag.ingest_trace import normalize_ingest_trace

    raw = {
        "schema": 1,
        "started_at": "2026-08-16T05:21:07+00:00",
        "finished_at": "2026-08-16T05:21:45+00:00",
        "duration_ms": 38616,
        "steps": [
            {"id": "parse", "title": "Parse", "status": "ok", "at": "2026-08-16T05:21:12+00:00", "ms": 5136},
            {"id": "images_filter", "title": "Filter", "status": "ok", "at": "2026-08-16T05:21:12+00:00", "ms": 36},
            {"id": "chunk", "title": "Chunk", "status": "ok", "at": "2026-08-16T05:21:12+00:00", "ms": 5},
            {"id": "ocr", "title": "OCR", "status": "ok", "at": "2026-08-16T05:21:29+00:00", "ms": 7088},
            {"id": "vision", "title": "Vision", "status": "ok", "at": "2026-08-16T05:21:29+00:00", "ms": 9995},
            {
                "id": "summary_start",
                "title": "Summary started",
                "status": "ok",
                "at": "2026-08-16T05:21:29+00:00",
            },
            {"id": "summary", "title": "Summary", "status": "ok", "at": "2026-08-16T05:21:43+00:00", "ms": 6184},
            {"id": "cache_wait", "title": "Prefix-cache wait", "status": "ok", "at": "2026-08-16T05:21:43+00:00", "ms": 3000},
            {"id": "context", "title": "Situating", "status": "ok", "at": "2026-08-16T05:21:43+00:00", "ms": 7684},
            {"id": "store", "title": "Store", "status": "ok", "at": "2026-08-16T05:21:45+00:00", "ms": 2258},
        ],
    }
    out = normalize_ingest_trace(raw)
    by_id = {s["id"]: s for s in out["steps"]}

    ocr, vis = by_id["ocr"], by_id["vision"]
    assert ocr["ended_ms"] == vis["started_ms"]
    assert vis["ended_ms"] - vis["started_ms"] == 9995
    assert ocr["ended_ms"] - ocr["started_ms"] == 7088

    summary, wait, ctx = by_id["summary"], by_id["cache_wait"], by_id["context"]
    assert summary["started_ms"] == by_id["summary_start"]["started_ms"] or summary["started_ms"] == 22000
    # Vision flush and summary_start share 05:21:29 → 22000ms from 05:21:07
    assert summary["started_ms"] == 22000
    assert summary["ended_ms"] == 22000 + 6184
    assert wait["started_ms"] == summary["ended_ms"]
    assert wait["ended_ms"] == wait["started_ms"] + 3000
    assert ctx["started_ms"] == wait["ended_ms"]
    # schema-1 context_ms included the 3s sleep
    assert ctx["ended_ms"] - ctx["started_ms"] == 7684 - 3000
    assert wait["started_ms"] >= summary["ended_ms"]
    assert ctx["started_ms"] >= wait["ended_ms"]


def test_find_trace_in_version_dir(tmp_path: Path, monkeypatch):
    col = "col_test"
    fid = "file_test"
    vid = "ver_test"
    vdir = tmp_path / col / "files" / fid / vid
    vdir.mkdir(parents=True)
    IngestTrace(vdir, {"file_id": fid, "version_id": vid}).add("parse", "Parse")

    monkeypatch.setattr(
        "src.file_mgmt.storage_paths.COLLECTIONS_DIR",
        tmp_path,
    )
    data, used = find_trace(col, fid, vid)
    assert data is not None
    assert data["file_id"] == fid
    assert used == str(vdir)
