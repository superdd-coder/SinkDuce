"""Per-file ingest inspect log.

Written next to ``parsed.txt`` as ``ingest_trace.json`` so the file-detail
Ingest tab can show steps and LLM returns without grepping mixed app logs.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

TRACE_FILENAME = "ingest_trace.json"
TRACE_SCHEMA = 2


def _iso(ts: float | None = None) -> str:
    moment = datetime.now(timezone.utc) if ts is None else datetime.fromtimestamp(ts, tz=timezone.utc)
    return moment.isoformat(timespec="milliseconds")


def _now_iso() -> str:
    return _iso()


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return str(value)


def trace_path_for(file_dir: Path | None) -> Path | None:
    if file_dir is None:
        return None
    return Path(file_dir) / TRACE_FILENAME


def load_trace(file_dir: Path | None) -> dict[str, Any] | None:
    path = trace_path_for(file_dir)
    if path is None or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("[IngestTrace] failed to read %s", path, exc_info=True)
        return None
    return data if isinstance(data, dict) else None


def find_trace(
    collection_id: str,
    file_id: str,
    version_id: str | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Locate ingest_trace.json for a managed file. Returns (data, dir_used)."""
    from src.file_mgmt.storage_paths import managed_file_dir, version_dir

    if not collection_id or not file_id:
        return None, None
    candidates: list[Path] = []
    if version_id:
        candidates.append(version_dir(collection_id, file_id, version_id))
    root = managed_file_dir(collection_id, file_id)
    candidates.append(root)
    if not version_id and root.is_dir():
        try:
            for child in sorted(root.iterdir(), reverse=True):
                if child.is_dir():
                    candidates.append(child)
        except OSError:
            pass
    seen: set[str] = set()
    for directory in candidates:
        key = str(directory)
        if key in seen:
            continue
        seen.add(key)
        data = load_trace(directory)
        if data is not None:
            return data, key
    return None, None


def _parse_iso(value: Any) -> float | None:
    if not value:
        return None
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def normalize_ingest_trace(data: dict[str, Any]) -> dict[str, Any]:
    """Fill started_ms / ended_ms so the timeline matches the real pipeline.

    Schema-1 traces stamp several steps with the same ``at`` (flush time):
    OCR then Vision share the image-worker flush; Summary → prefix-cache
    wait → Context share the enrich flush. Reconstruct those chains.
    """
    import copy

    out = copy.deepcopy(data)
    steps = list(out.get("steps") or [])
    if not steps:
        return out
    t0 = _parse_iso(out.get("started_at"))
    if t0 is None:
        return out

    def rel_ms(iso: Any) -> int | None:
        ts = _parse_iso(iso)
        if ts is None:
            return None
        return max(0, int((ts - t0) * 1000))

    by_id = {s.get("id"): s for s in steps if s.get("id")}
    ocr = by_id.get("ocr")
    vis = by_id.get("vision")
    summary = by_id.get("summary")
    wait = by_id.get("cache_wait")
    ctx = by_id.get("context")
    marker = by_id.get("summary_start")

    needs_ocr_vis = bool(
        ocr
        and vis
        and ocr.get("started_at") is None
        and vis.get("started_at") is None
        and ocr.get("started_ms") is None
        and vis.get("started_ms") is None
    )
    needs_enrich = bool(
        summary
        and summary.get("started_at") is None
        and summary.get("started_ms") is None
    )

    for step in steps:
        if step.get("started_ms") is not None and step.get("ended_ms") is not None:
            continue
        end = rel_ms(step.get("ended_at") or step.get("at"))
        ms = step.get("ms")
        if end is None:
            continue
        step["ended_ms"] = end
        start_abs = rel_ms(step.get("started_at"))
        if start_abs is not None:
            step["started_ms"] = start_abs
            if ms is None:
                step["ms"] = max(0, end - start_abs)
            continue
        if isinstance(ms, (int, float)):
            step["started_ms"] = max(0, end - int(ms))
        else:
            step["started_ms"] = end
            step["ms"] = 0

    if needs_ocr_vis:
        ocr_ms = int(ocr.get("ms") or 0)
        vis_ms = int(vis.get("ms") or 0)
        ocr_at = _parse_iso(ocr.get("at"))
        vis_at = _parse_iso(vis.get("at"))
        if ocr_ms > 0 and vis_ms > 0 and ocr_at and vis_at and abs(ocr_at - vis_at) <= 1.5:
            end = rel_ms(vis.get("at"))
            if end is not None:
                vis["ended_ms"] = end
                vis["started_ms"] = max(0, end - vis_ms)
                ocr["ended_ms"] = vis["started_ms"]
                ocr["started_ms"] = max(0, ocr["ended_ms"] - ocr_ms)

    if needs_enrich:
        sum_ms = int(summary.get("ms") or 0)
        wait_ms = (
            int(wait.get("ms") or 0)
            if wait and wait.get("status") not in ("skip", None)
            else 0
        )
        if wait and wait.get("status") == "skip":
            wait_ms = 0
        ctx_ms = (
            int(ctx.get("ms") or 0)
            if ctx and ctx.get("status") not in ("skip", None)
            else 0
        )
        ats: list[float] = []
        for rec in (summary, wait, ctx):
            ts = _parse_iso(rec.get("at")) if rec else None
            if ts is not None:
                ats.append(ts)
        same_flush = len(ats) >= 2 and (max(ats) - min(ats) <= 2.0)
        schema = int(out.get("schema") or 1)
        if schema < 2 and same_flush and ctx_ms >= wait_ms > 0:
            ctx_ms -= wait_ms

        chain_start = None
        if marker:
            chain_start = rel_ms(marker.get("started_at") or marker.get("at"))
        if chain_start is None:
            chain_start = rel_ms(summary.get("started_at"))

        if chain_start is not None:
            t = chain_start
            summary["started_ms"] = t
            summary["ended_ms"] = t + sum_ms
            t = summary["ended_ms"]
            if wait is not None:
                wait["started_ms"] = t
                wait["ended_ms"] = t + wait_ms
                wait["ms"] = wait_ms
                t = wait["ended_ms"]
            if ctx is not None and ctx.get("status") != "skip":
                ctx["started_ms"] = t
                ctx["ended_ms"] = t + ctx_ms
                ctx["ms"] = ctx_ms
        elif same_flush and ctx is not None:
            t = rel_ms(ctx.get("at"))
            if t is None:
                t = rel_ms(summary.get("at"))
            if t is not None:
                if ctx.get("status") != "skip":
                    ctx["ended_ms"] = t
                    ctx["started_ms"] = max(0, t - ctx_ms)
                    ctx["ms"] = ctx_ms
                    t = ctx["started_ms"]
                if wait is not None:
                    wait["ended_ms"] = t
                    wait["started_ms"] = max(0, t - wait_ms)
                    wait["ms"] = wait_ms
                    t = wait["started_ms"]
                summary["ended_ms"] = t
                summary["started_ms"] = max(0, t - sum_ms)

    out["steps"] = steps
    return out


class IngestTrace:
    """Append-only step log flushed after every change."""

    def __init__(self, file_dir: Path | None, meta: dict[str, Any] | None = None):
        meta = dict(meta or {})
        self.file_dir = Path(file_dir) if file_dir else None
        self._t0 = time.time()
        self._lock = threading.Lock()
        self.data: dict[str, Any] = {
            "schema": TRACE_SCHEMA,
            "status": "running",
            "started_at": _now_iso(),
            "finished_at": None,
            "duration_ms": None,
            "file_id": meta.get("file_id") or "",
            "version_id": meta.get("version_id") or "",
            "filename": meta.get("filename") or "",
            "collection": meta.get("collection") or "",
            "config": {},
            "steps": [],
        }
        self._flush()

    def set_config(self, **kwargs: Any) -> None:
        with self._lock:
            cfg = self.data.setdefault("config", {})
            for key, value in kwargs.items():
                if value is not None:
                    cfg[key] = _json_safe(value)
        self._flush()

    def _rel_ms(self, ts: float) -> int:
        return max(0, int((ts - self._t0) * 1000))

    def _stamp_start(self, rec: dict[str, Any], ts: float) -> None:
        rec["started_at"] = _iso(ts)
        rec["started_ms"] = self._rel_ms(ts)

    def _stamp_end(self, rec: dict[str, Any], ts: float) -> None:
        rec["ended_at"] = _iso(ts)
        rec["ended_ms"] = self._rel_ms(ts)
        rec["at"] = rec["ended_at"]
        if rec.get("started_ms") is not None:
            rec["ms"] = max(0, int(rec["ended_ms"]) - int(rec["started_ms"]))

    def ended_epoch(self, step_id: str) -> float | None:
        """Wall-clock end of the latest *step_id*, if known."""
        with self._lock:
            rec = next((s for s in reversed(self.data.get("steps") or []) if s.get("id") == step_id), None)
            if rec is None:
                return None
            if rec.get("ended_ms") is not None:
                return self._t0 + int(rec["ended_ms"]) / 1000.0
            raw = rec.get("ended_at") or rec.get("at")
        if not raw:
            return None
        try:
            return datetime.fromisoformat(str(raw)).timestamp()
        except ValueError:
            return None

    def add(
        self,
        step_id: str,
        title: str,
        *,
        status: str = "ok",
        detail: str = "",
        data: dict[str, Any] | None = None,
        ms: int | None = None,
        started_at: float | None = None,
        ended_at: float | None = None,
    ) -> None:
        now = time.time()
        rec: dict[str, Any] = {
            "id": step_id,
            "title": title,
            "status": status,
            "detail": detail or "",
        }
        if status == "running":
            self._stamp_start(rec, started_at if started_at is not None else now)
            rec["at"] = rec["started_at"]
        else:
            t1 = ended_at if ended_at is not None else now
            if started_at is not None:
                t0 = started_at
            elif ms is not None:
                t0 = t1 - (int(ms) / 1000.0)
            else:
                t0 = t1
            self._stamp_start(rec, t0)
            self._stamp_end(rec, t1)
            if ms is not None:
                rec["ms"] = int(ms)
                rec["ended_ms"] = int(rec["started_ms"]) + int(ms)
                rec["ended_at"] = _iso(self._t0 + rec["ended_ms"] / 1000.0)
                rec["at"] = rec["ended_at"]
        if data:
            rec["data"] = _json_safe(data)
        with self._lock:
            self.data.setdefault("steps", []).append(rec)
        self._flush()
        logger.info(
            "[INGEST %s] %s %s — %s",
            self.data.get("filename") or self.data.get("file_id") or "?",
            step_id,
            status,
            detail or title,
        )

    def update(self, step_id: str, **kwargs: Any) -> bool:
        """Patch the latest step with *step_id* (used to finish a running step).

        Returns True if a matching step was found. ``started_at`` / ``ended_at``
        are wall-clock epochs. If only ``ms`` is given, the original start is
        kept and the end is start + ms (not "now"), so late flushes stay honest.
        """
        started_at = kwargs.pop("started_at", None)
        ended_at = kwargs.pop("ended_at", None)
        ms = kwargs.pop("ms", None)
        with self._lock:
            steps = self.data.setdefault("steps", [])
            rec = next((s for s in reversed(steps) if s.get("id") == step_id), None)
            if rec is None:
                return False
            if started_at is not None:
                self._stamp_start(rec, float(started_at))
            for key, value in kwargs.items():
                if key == "data" and isinstance(value, dict):
                    rec["data"] = _json_safe(value)
                elif value is not None:
                    rec[key] = _json_safe(value)
            if ended_at is not None:
                self._stamp_end(rec, float(ended_at))
                if ms is not None:
                    rec["ms"] = int(ms)
            elif ms is not None:
                rec["ms"] = int(ms)
                if rec.get("started_ms") is not None:
                    rec["ended_ms"] = int(rec["started_ms"]) + int(ms)
                    rec["ended_at"] = _iso(self._t0 + rec["ended_ms"] / 1000.0)
                    rec["at"] = rec["ended_at"]
                else:
                    t1 = time.time()
                    self._stamp_end(rec, t1)
                    self._stamp_start(rec, t1 - int(ms) / 1000.0)
                    rec["ms"] = int(ms)
            elif rec.get("status") in ("ok", "skip", "error", "failed") and rec.get("ended_at") is None:
                self._stamp_end(rec, time.time())
        self._flush()
        return True

    @contextmanager
    def span(self, step_id: str, title: str):
        started = time.time()
        rec: dict[str, Any] = {
            "id": step_id,
            "title": title,
            "status": "ok",
            "detail": "",
            "data": {},
        }
        try:
            yield rec
        except Exception as exc:
            rec["status"] = "error"
            rec["detail"] = rec.get("detail") or f"{type(exc).__name__}: {exc}"
            raise
        finally:
            ended = time.time()
            rec["status"] = rec.get("status") or "ok"
            self._stamp_start(rec, started)
            self._stamp_end(rec, ended)
            rec["ms"] = int((ended - started) * 1000)
            if rec.get("data") == {}:
                rec.pop("data", None)
            with self._lock:
                self.data.setdefault("steps", []).append(_json_safe(rec))
            self._flush()
            logger.info(
                "[INGEST %s] %s %s %dms — %s",
                self.data.get("filename") or "?",
                step_id,
                rec.get("status"),
                rec.get("ms"),
                rec.get("detail") or title,
            )

    def finish(self, status: str = "ok", error: str | None = None) -> None:
        with self._lock:
            self.data["status"] = status
            self.data["finished_at"] = _now_iso()
            self.data["duration_ms"] = max(0, int((time.time() - self._t0) * 1000))
            if error:
                self.data["error"] = error
        self._flush()

    def _flush(self) -> None:
        path = trace_path_for(self.file_dir)
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            with self._lock:
                payload = json.dumps(self.data, ensure_ascii=False, indent=2)
            tmp.write_text(payload, encoding="utf-8")
            tmp.replace(path)
        except Exception:
            logger.debug("[IngestTrace] flush failed", exc_info=True)
