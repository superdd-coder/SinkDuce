"""Bounded background exports with independent, persisted format status."""
from __future__ import annotations

import asyncio
import contextvars
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from src.reports.rendering import render_docx, render_html, render_pdf

logger = logging.getLogger(__name__)
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="report-export")
_lock = threading.RLock()
_jobs = set()


def _run_export(store, sid, rid, revision, fmt):
    try:
        store.write_export(sid, rid, revision, fmt, None, status="running")
        report = store.read(sid, rid, revision)
        renderer = {"html": render_html, "docx": render_docx, "pdf": render_pdf}[fmt]
        from src.reports.assets import SourceImages
        content = renderer(report, source_images=SourceImages(sid, store.root.parent / "chat_attachments"))
        store.write_export(sid, rid, revision, fmt, content, status="ready")
    except FileNotFoundError:
        pass  # Session deletion wins over a late conversion result.
    except Exception as exc:
        logger.warning("Report export failed (%s)", type(exc).__name__, exc_info=True)
        message = str(exc) if isinstance(exc, ValueError) else "Export could not be completed. Retry this format."
        try:
            store.write_export(sid, rid, revision, fmt, None, status="failed", error=message)
        except FileNotFoundError:
            pass
    finally:
        with _lock:
            _jobs.discard((str(store.root), sid, rid, revision, fmt))


def schedule_export(background, store, sid, rid, revision, fmt):
    # Caller has checked session authorization on the request thread.
    key = (str(store.root), sid, rid, revision, fmt)
    with _lock:
        state = store.export_state(sid, rid, revision, fmt)
        if state["status"] == "ready" or key in _jobs:
            return state
        _jobs.add(key)
        try:
            state = store.write_export(sid, rid, revision, fmt, None, status="queued")
        except Exception:
            _jobs.discard(key)
            raise
    context = contextvars.copy_context()

    async def run():
        try:
            await asyncio.get_running_loop().run_in_executor(
                _executor, lambda: context.run(_run_export, store, sid, rid, revision, fmt))
        finally:
            with _lock:
                _jobs.discard(key)
    background.add_task(run)
    return state
