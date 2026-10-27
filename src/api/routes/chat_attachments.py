from __future__ import annotations

import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import FileResponse, Response

from src.api.routes.identity import require_module
from src.api.routes.sessions import _get_store, require_session_access
from src.chat_attachments.limits import ResourceLimit
from src.chat_attachments.models import MAX_FILE_BYTES, Attachment
from src.chat_attachments.service import capabilities, parse_attachment
from src.chat_attachments.store import for_sessions
from src.chat_attachments.upload import check_envelope, stream_source

router = APIRouter(dependencies=[Depends(require_module("chat"))])
_executor = ThreadPoolExecutor(max_workers=5, thread_name_prefix="chat-parse")


def _session(sid):
    require_session_access(sid)
    sessions = _get_store()
    if sessions.get_session(sid) is None:
        raise HTTPException(404, "Session not found")
    return for_sessions(sessions)


def _attachment(sid, aid):
    store = _session(sid)
    try:
        a = store.get(sid, aid)
    except ValueError:
        a = None
    if a is None:
        raise HTTPException(404, "Attachment not found")
    return store, a


def _resource_error(exc):
    return HTTPException(exc.status_code, detail={"code": "attachment_resource_limit", "scope": exc.scope,
                                                 "message": str(exc)},
                         headers={"Retry-After": "5"} if exc.status_code == 429 else None)


async def _parse(store, sid, aid, cfg=None, lease=None):
    if lease is None:
        from src.identity import get_actor
        lease = store.resources.queue(sid, aid, get_actor().id)
    submitted = False
    try:
        while (state := store.resources.try_start(lease)) == "wait":
            await asyncio.sleep(0.1)
        if state == "missing":
            return

        def run():
            try:
                parse_attachment(store, sid, aid, cfg)
            finally:
                store.resources.finish_job(lease)

        future = asyncio.get_running_loop().run_in_executor(_executor, run)
        submitted = True
        await asyncio.shield(future)
    finally:
        # Cancellation of the awaiting request cannot free a running worker's
        # capacity; that worker releases its own lease in run().
        if not submitted:
            store.resources.finish_job(lease)


def _schedule(background, store, sid, aid, lease=None):
    from src.identity import get_actor
    try:
        lease = lease or store.resources.queue(sid, aid, get_actor().id)
    except ResourceLimit as exc:
        raise _resource_error(exc) from exc
    try:
        if store.update(sid, aid, status="processing", error="") is None:
            raise HTTPException(404, "Attachment not found")
        background.add_task(_parse, store, sid, aid, lease=lease)
    except BaseException:
        store.resources.finish_job(lease)
        raise


@router.get("/chat/attachment-types")
def attachment_types():
    return capabilities()


@router.post("/sessions/{session_id}/attachments", response_model=Attachment, status_code=201,
             openapi_extra={"requestBody": {"required": True, "content": {"multipart/form-data": {
                 "schema": {"type": "object", "required": ["file"],
                            "properties": {"file": {"type": "string", "format": "binary"}}}}}}})
async def upload_attachment(session_id: str, background: BackgroundTasks, request: Request):
    store = _session(session_id)
    check_envelope(request, MAX_FILE_BYTES)
    from src.identity import get_actor
    sessions = _get_store()
    original_session = sessions.get_session(session_id)
    if original_session is None:
        raise HTTPException(404, "Session not found")
    incarnation = (original_session.created_at, original_session.created_by)

    def check_session():
        current = sessions.get_session(session_id)
        if current is None or (current.created_at, current.created_by) != incarnation:
            raise ResourceLimit("The conversation changed during this upload. Upload the file again.", status_code=409)
        require_session_access(session_id)

    try:
        # Admission precedes multipart processing, not merely the file copy.
        lease = store.resources.reserve(session_id, get_actor().id, MAX_FILE_BYTES, check=check_session)
    except ResourceLimit as exc:
        raise _resource_error(exc) from exc
    committed = scheduled = False
    try:
        filename, size = await stream_source(request, store.upload_path(lease), max_bytes=MAX_FILE_BYTES,
                                             extensions=set(capabilities()["extensions"]))
        a = store.commit_upload(lease, filename, size, check=check_session)
        committed = True
        _schedule(background, store, session_id, a.id, lease=lease)
        scheduled = True
        return a
    except ResourceLimit as exc:
        raise _resource_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(507, "Attachment storage is unavailable") from exc
    finally:
        if committed and not scheduled:
            store.resources.finish_job(lease)
            store.remove(session_id, lease.id)
        store.resources.abort(lease)


@router.get("/sessions/{session_id}/attachments", response_model=list[Attachment])
def list_attachments(session_id: str):
    store = _session(session_id)
    rows = store.list(session_id)
    for a in rows:
        if a.status == "processing" and not store.resources.active(session_id, a.id):
            a.status = "failed"
            a.error = "Parsing was interrupted. Retry this attachment."
            store.update(session_id, a.id, status=a.status, error=a.error)
    return rows


@router.post("/sessions/{session_id}/attachments/{attachment_id}/retry", response_model=Attachment)
def retry_attachment(session_id: str, attachment_id: str, background: BackgroundTasks):
    store, a = _attachment(session_id, attachment_id)
    if a.bound or a.status == "ready":
        raise HTTPException(409, "Only failed or interrupted draft attachments can be retried")
    _schedule(background, store, session_id, attachment_id)
    return store.get(session_id, attachment_id)


@router.delete("/sessions/{session_id}/attachments/{attachment_id}", status_code=204)
def remove_attachment(session_id: str, attachment_id: str):
    store, _ = _attachment(session_id, attachment_id)
    try:
        store.remove(session_id, attachment_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/sessions/{session_id}/attachments/{attachment_id}/source")
def download_attachment(session_id: str, attachment_id: str):
    store, a = _attachment(session_id, attachment_id)
    return FileResponse(store.source_path(session_id, attachment_id), filename=a.filename, media_type="application/octet-stream")


@router.get("/sessions/{session_id}/attachments/{attachment_id}/text")
def attachment_text(session_id: str, attachment_id: str):
    store, a = _attachment(session_id, attachment_id)
    if a.status != "ready":
        raise HTTPException(409, "Attachment text is not ready")
    try:
        text = store.text(session_id, attachment_id)
        native = store.native_text(session_id, attachment_id)
        return {"text": text,
                "position_map": store.parsed(session_id, attachment_id).get("position_map", []) if text.startswith(native) else [],
                "native_length": len(native) if text.startswith(native) else 0}
    except FileNotFoundError:
        raise HTTPException(404, "Attachment text is unavailable") from None


@router.get("/sessions/{session_id}/attachments/{attachment_id}/preview")
def preview_attachment(session_id: str, attachment_id: str):
    store, a = _attachment(session_id, attachment_id)
    if a.status != "ready":
        raise HTTPException(404, "Image preview not available")
    source = store.source_path(session_id, attachment_id)
    headers = {"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"}
    if source.suffix.lower() in {".ppt", ".pptx"}:
        converted = source.parent / "pages.pdf"
        if converted.is_file():
            return FileResponse(converted, filename=Path(a.filename).stem + ".pdf",
                                media_type="application/pdf", content_disposition_type="inline", headers=headers)
        mime = ("application/vnd.ms-powerpoint" if source.suffix.lower() == ".ppt" else
                "application/vnd.openxmlformats-officedocument.presentationml.presentation")
        return FileResponse(source, filename=a.filename, media_type=mime,
                            content_disposition_type="inline", headers=headers)
    if not a.image_count:
        raise HTTPException(404, "Image preview not available")
    try:
        header, data = store.image_url(session_id, attachment_id, 0).split(";base64,", 1)
        mime = header.removeprefix("data:")
        if mime not in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
            raise ValueError("Invalid image format")
        return Response(base64.b64decode(data, validate=True), media_type=mime, headers=headers)
    except (ValueError, OSError) as exc:
        raise HTTPException(404, "Image preview not available") from exc
