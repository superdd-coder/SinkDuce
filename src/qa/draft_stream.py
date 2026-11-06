"""Bounded streaming bridge for synchronous Q&A assistance stages."""

import asyncio
import json
import threading
from contextlib import suppress
from contextvars import copy_context

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse

from . import drafting

_workers = threading.BoundedSemaphore(4)
_stages = frozenset(
    {"preparing", "intent", "planning", "retrieving", "reranking", "grading", "drafting", "retrying", "finalizing"}
)
_operations = frozenset({"draft", "polish", "revise"})
_error_codes = frozenset(
    {
        "ai_unavailable", "ai_repeated_reply", "ai_input_limit", "ai_busy",
        "stale_revision", "stale_draft", "read_only", "not_found", "forbidden",
        "permission_denied", "not_included", "material_not_ready", "material_archived",
    }
)


class DraftStreamCancelled(BaseException):
    """End disconnected work even through provider-error recovery blocks."""


def _event(name, payload):
    return f"event: {name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def response(request: Request, collection, lid, qid, cid, data):
    # Capture while the request's actor and source scope are still active.
    context = copy_context()

    async def stream():
        stopped = threading.Event()
        queue = asyncio.Queue(maxsize=8)
        loop = asyncio.get_running_loop()

        def publish(name, payload):
            if stopped.is_set():
                return

            def deliver():
                if stopped.is_set():
                    return
                # Slow readers need the latest status, not an unbounded backlog.
                if queue.full():
                    queue.get_nowait()
                queue.put_nowait((name, payload))

            try:
                loop.call_soon_threadsafe(deliver)
            except RuntimeError:  # The client and its event loop have closed.
                stopped.set()

        def check_cancel():
            if stopped.is_set():
                raise DraftStreamCancelled()

        def progress(payload):
            check_cancel()
            stage = payload.get("stage")
            if stage not in _stages:
                return
            safe = {"stage": stage}
            if payload.get("operation") in _operations:
                safe["operation"] = payload["operation"]
            count = payload.get("count")
            if isinstance(count, int) and not isinstance(count, bool) and 0 <= count <= 1_000_000:
                safe["count"] = count
            publish("progress", safe)

        def run():
            if stopped.is_set():
                return
            if not _workers.acquire(blocking=False):
                publish("error", {"code": "ai_busy", "status": 503})
                return
            try:
                if stopped.is_set():
                    return
                result = drafting.generate(
                    collection, lid, qid, cid, data, on_progress=progress, check_cancel=check_cancel
                )
                publish("result", result)
            except DraftStreamCancelled:
                pass
            except HTTPException as error:
                detail = error.detail if isinstance(error.detail, dict) else {}
                code = detail.get("code")
                if code not in _error_codes:
                    code = "ai_unavailable"
                publish("error", {"code": code, "status": error.status_code})
            except Exception:  # noqa: BLE001 — provider details and source content stay private
                publish("error", {"code": "ai_unavailable", "status": 503})
            finally:
                _workers.release()

        worker = asyncio.create_task(asyncio.to_thread(context.run, run))
        last_heartbeat = loop.time()
        try:
            while not await request.is_disconnected():
                try:
                    name, payload = await asyncio.wait_for(queue.get(), timeout=0.5)
                except TimeoutError:
                    if loop.time() - last_heartbeat >= 15:
                        yield ": keep-alive\n\n"
                        last_heartbeat = loop.time()
                    continue
                yield _event(name, payload)
                if name in {"result", "error"}:
                    break
        finally:
            # The next real stage callback, including the one before persistence,
            # stops a provider call that finished after its client disconnected.
            stopped.set()
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
