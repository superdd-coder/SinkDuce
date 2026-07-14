"""FastAPI route for the MCP content staging endpoint.

This is a **regular HTTP endpoint** (NOT an MCP tool) — it lives at
``POST /api/mcp/stage-content`` so file content is transmitted out-of-band
and never appears in the LLM conversation transcript.

See :mod:`src.mcp.staging` for the design rationale.

Content modes (tried in order)
-------------------------------

1. **multipart/form-data** — field name ``file``.  Zero encoding overhead.
   The original filename from the upload is used automatically.  Preferred
   for all uploads where the client can construct a multipart body.

   .. code-block:: bash

       curl -F "file=@report.pdf" http://host:18900/api/mcp/stage-content

2. **application/octet-stream** — raw bytes in body, filename in
   ``X-Filename`` header.  Same zero overhead, just a different shape.

   .. code-block:: bash

       curl --data-binary @report.pdf \\
            -H "Content-Type: application/octet-stream" \\
            -H "X-Filename: report.pdf" \\
            http://host:18900/api/mcp/stage-content

3. **application/json with ``file_path``** — for files already on the
   SinkDuce server filesystem.  The server reads the file directly.

   .. code-block:: bash

       curl -H "Content-Type: application/json" \\
            -d '{"file_path": "/data/uploads/report.pdf"}' \\
            http://host:18900/api/mcp/stage-content

4. **application/json with ``content_b64``** — fallback for clients that
   can only send JSON bodies.  Requires base64 encoding on the client side.

   .. code-block:: bash

       curl -H "Content-Type: application/json" \\
            -d '{"filename":"report.pdf","content_b64":"JVBERi..."}' \\
            http://host:18900/api/mcp/stage-content

Response (all modes)::

    {"staging_token": "a1b2c3...", "filename": "report.pdf", "size_bytes": 12345}

The returned ``staging_token`` expires after 10 minutes.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Request, UploadFile
from fastapi.responses import JSONResponse

from src.mcp.staging import staging_store
from src.mcp.common import decode_base64_content, safe_filename

logger = logging.getLogger(__name__)

router = APIRouter()


# ══════════════════════════════════════════════════════════════════
# Direct upload (one-shot, no staging token needed)
# ══════════════════════════════════════════════════════════════════


@router.post("/mcp/upload")
async def direct_upload(request: Request):
    """Upload a file directly — one HTTP call, no staging token.

    Accepts ``multipart/form-data`` with fields:

    - ``file`` (required): the file to upload
    - ``collection`` (optional, default ``"default"``): collection ID

    This is the **simplest upload path** for LLM agents.  Example::

        curl -F "file=@report.pdf" -F "collection=col_xxx" \\
             http://localhost:18900/api/mcp/upload

    The server validates the collection, checks allowed file types, saves the
    file, creates an async processing task, and returns the task info — all in
    one call.  File bytes travel over HTTP only; they never enter the LLM
    conversation transcript.
    """
    import uuid
    from pathlib import Path as _Path

    from src.services import services
    from src.tasks import task_manager

    # Parse multipart form
    content_type: str = (request.headers.get("content-type") or "").lower()
    if not content_type.startswith("multipart/form-data"):
        return JSONResponse(
            status_code=400,
            content={"error": "Use multipart/form-data: curl -F 'file=@...' -F 'collection=...'"},
        )

    try:
        form = await request.form()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"error": "Invalid multipart form data"},
        )

    upload: UploadFile | None = form.get("file")  # type: ignore[assignment]
    if upload is None or not hasattr(upload, "filename"):
        return JSONResponse(
            status_code=400,
            content={"error": "Missing 'file' field. Use: curl -F 'file=@path' -F 'collection=...'"},
        )

    filename = (upload.filename or "").strip()
    if not filename:
        return JSONResponse(status_code=400, content={"error": "Filename is empty"})

    raw = await upload.read()
    if not raw:
        return JSONResponse(status_code=400, content={"error": "Empty file"})

    collection = (form.get("collection") or "default")  # type: ignore[assignment]
    if isinstance(collection, UploadFile):
        collection = "default"
    elif hasattr(collection, "strip"):
        collection = str(collection).strip()
    else:
        collection = str(collection)

    # Validate filename
    try:
        safe_filename(filename)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})

    # Validate collection
    from src.mcp.common import require_collection as _require_collection
    if e := _require_collection(collection):
        return JSONResponse(status_code=404, content=e)

    # Check allowed file types
    col_config = services.db.get_collection_config(collection)
    allowed = col_config.get("allowed_file_types")
    if allowed:
        ext = Path(filename).suffix.lower().lstrip(".")
        if ext not in allowed:
            return JSONResponse(
                status_code=400,
                content={"error": f"File type '.{ext}' not allowed. Allowed: {', '.join(allowed)}"},
            )

    # Save and queue
    from src.mcp.tools.documents import _files_dir as _doc_files_dir

    file_id = uuid.uuid4().hex
    file_source = f"__file__:{file_id}"
    file_dir = _doc_files_dir(collection) / file_id
    file_dir.mkdir(parents=True, exist_ok=True)

    # safe_filename already validated above
    import re as _re
    safe_name = _re.sub(r'[<>:"/\\|?*\x00]', '_', filename).strip()
    if not safe_name or safe_name in ('.', '..'):
        safe_name = "uploaded_file"
    save_path = file_dir / safe_name
    save_path.write_bytes(raw)

    task = task_manager.create_task(
        filename=safe_name,
        task_type="upload",
        file_path=str(save_path),
        collection=collection,
        filename_param=file_source,
        source_label=safe_name,
        file_id=file_id,
    )

    logger.info(
        "Direct upload: file=%r size=%d collection=%s task=%s",
        filename, len(raw), collection, task.id,
    )

    return {
        "ok": True,
        "message": "File uploaded and queued for processing",
        "task_id": task.id,
        "file_id": file_id,
        "filename": safe_name,
        "size_bytes": len(raw),
        "collection": collection,
    }


# ══════════════════════════════════════════════════════════════════
# Direct meeting audio upload (one-shot)
# ══════════════════════════════════════════════════════════════════


@router.post("/mcp/meeting-upload")
async def direct_meeting_upload(request: Request):
    """Upload audio to a meeting — one HTTP call.

    Accepts ``multipart/form-data`` with fields:

    - ``file`` (required): the audio file
    - ``meeting_id`` (required): target meeting ID

    Example::

        curl -F "file=@recording.webm" -F "meeting_id=meet_xxx" \\
             http://localhost:18900/api/mcp/meeting-upload
    """
    from src.meeting import store as mstore
    from src.meeting.models import MeetingMode, MeetingStatus

    content_type: str = (request.headers.get("content-type") or "").lower()
    if not content_type.startswith("multipart/form-data"):
        return JSONResponse(
            status_code=400,
            content={"error": "Use multipart/form-data: curl -F 'file=@...' -F 'meeting_id=...'"},
        )

    try:
        form = await request.form()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"error": "Invalid multipart form data"},
        )

    upload: UploadFile | None = form.get("file")  # type: ignore[assignment]
    if upload is None or not hasattr(upload, "filename"):
        return JSONResponse(status_code=400, content={"error": "Missing 'file' field"})

    filename = (upload.filename or "").strip()
    if not filename:
        return JSONResponse(status_code=400, content={"error": "Filename is empty"})

    raw = await upload.read()
    if not raw:
        return JSONResponse(status_code=400, content={"error": "Empty file"})

    meeting_id = form.get("meeting_id")  # type: ignore[assignment]
    if isinstance(meeting_id, UploadFile):
        meeting_id = ""
    elif hasattr(meeting_id, "strip"):
        meeting_id = str(meeting_id).strip()
    else:
        meeting_id = str(meeting_id or "")

    if not meeting_id:
        return JSONResponse(status_code=400, content={"error": "Missing 'meeting_id' field"})

    meeting = mstore.get_meeting(meeting_id)
    if not meeting:
        return JSONResponse(status_code=404, content={"error": f"Meeting '{meeting_id}' not found"})

    ext = filename.rsplit(".", 1)[-1] if "." in filename else "webm"
    path = mstore.save_audio(meeting_id, raw, ext, original_filename=filename)
    updated = mstore.update_meeting(meeting_id, mode=MeetingMode.upload, status=MeetingStatus.created)

    logger.info(
        "Direct meeting upload: meeting=%s file=%r size=%d",
        meeting_id, filename, len(raw),
    )

    return {
        "ok": True,
        "message": "Audio uploaded to meeting",
        "meeting_id": meeting_id,
        "filename": filename,
        "size_bytes": len(raw),
        "status": updated.status.value,
        "audio_path": path,
    }


async def _stage(filename: str, raw: bytes) -> dict:
    """Shared staging helper — validate, store, return response."""
    try:
        safe_filename(filename)
    except ValueError as exc:
        return {"status": 400, "error": str(exc)}

    try:
        token = await staging_store.put(filename, raw)
    except ValueError as exc:
        return {"status": 413, "error": str(exc)}

    logger.info(
        "Staged content: token=%s filename=%r size=%d",
        token, filename, len(raw),
    )
    return {
        "staging_token": token,
        "filename": filename,
        "size_bytes": len(raw),
    }


@router.post("/mcp/stage-content")
async def stage_content(request: Request):
    """Accept file content via a side channel and return a staging token.

    Supports four content modes — see the module docstring above for examples.
    """
    content_type: str = (request.headers.get("content-type") or "").lower()

    # ── Mode 1: multipart/form-data ─────────────────────────
    if content_type.startswith("multipart/form-data"):
        try:
            form = await request.form()
        except Exception:
            return JSONResponse(
                status_code=400,
                content={"error": "Invalid multipart form data"},
            )
        upload: UploadFile | None = form.get("file")  # type: ignore[assignment]
        if upload is None or not hasattr(upload, "filename"):
            return JSONResponse(
                status_code=400,
                content={"error": "Missing 'file' field in form data"},
            )
        filename = (upload.filename or "").strip()
        if not filename:
            return JSONResponse(
                status_code=400,
                content={"error": "Filename is empty"},
            )
        raw = await upload.read()
        result = await _stage(filename, raw)
        if "error" in result:
            return JSONResponse(status_code=result.pop("status"), content=result)
        return result

    # ── Mode 2: application/octet-stream ────────────────────
    if content_type.startswith("application/octet-stream"):
        filename = (request.headers.get("x-filename") or "").strip()
        if not filename:
            return JSONResponse(
                status_code=400,
                content={"error": "Missing X-Filename header for octet-stream upload"},
            )
        raw = await request.body()
        if not raw:
            return JSONResponse(
                status_code=400,
                content={"error": "Empty request body"},
            )
        result = await _stage(filename, raw)
        if "error" in result:
            return JSONResponse(status_code=result.pop("status"), content=result)
        return result

    # ── Modes 3 & 4: application/json ───────────────────────
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={
                "error": (
                    "Unsupported content type. Use one of:\n"
                    "  - multipart/form-data (field: file)\n"
                    "  - application/octet-stream (header: X-Filename)\n"
                    "  - application/json with file_path (server-local file)\n"
                    "  - application/json with filename + content_b64 (base64 fallback)"
                ),
            },
        )

    # ── Mode 3: server-local file_path ──────────────────────
    file_path = body.get("file_path", "")
    if file_path:
        path = Path(file_path)
        if not path.is_file():
            return JSONResponse(
                status_code=400,
                content={"error": f"File not found: {file_path}"},
            )
        # Only allow files under data/ or /tmp/ for safety
        resolved = path.resolve()
        allowed_roots = [
            Path("data").resolve(),
            Path("/tmp").resolve(),
        ]
        if not any(str(resolved).startswith(str(root)) for root in allowed_roots):
            return JSONResponse(
                status_code=400,
                content={
                    "error": (
                        f"File path must be under data/ or /tmp/. "
                        f"Got: {file_path}"
                    ),
                },
            )
        filename = body.get("filename") or path.name
        try:
            raw = path.read_bytes()
        except Exception as exc:
            return JSONResponse(
                status_code=400,
                content={"error": f"Failed to read file: {exc}"},
            )
        result = await _stage(filename, raw)
        if "error" in result:
            return JSONResponse(status_code=result.pop("status"), content=result)
        return result

    # ── Mode 4: JSON with base64 content ────────────────────
    filename = body.get("filename", "")
    if not filename:
        return JSONResponse(
            status_code=400,
            content={"error": "Missing required field: filename (or use file_path for server-local files)"},
        )

    content_b64 = body.get("content_b64", "")
    if not content_b64:
        return JSONResponse(
            status_code=400,
            content={"error": "Missing required field: content_b64"},
        )

    try:
        raw = decode_base64_content(content_b64)
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content={"error": str(exc)},
        )

    result = await _stage(filename, raw)
    if "error" in result:
        return JSONResponse(status_code=result.pop("status"), content=result)
    return result
