from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Body, File, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, StreamingResponse

from src.meeting import store
from src.meeting.models import MeetingMode, MeetingStatus, ProcessingState, GenerationState, TranscriptSegment, TranscriptionResult
from src.meeting.service import meeting_service
from src.tasks.task_manager import task_manager

logger = logging.getLogger("meeting")
router = APIRouter()

def _serialize_meeting(meeting, *, include_transcript: bool = False) -> dict:
    """JSON for a meeting, including notes_content (GET-parity).

    PUT/upload/summarize responses used to return ``model_dump()`` only. The
    client then ``setMeeting(m)`` and dropped live notes until a full refresh.
    """
    data = meeting.model_dump(mode="json")
    data.pop("speaker_slots", None)
    try:
        from src.speakers.service import rebuild_speaker_names

        data["speaker_names"] = rebuild_speaker_names(
            meeting.speaker_people, keep=meeting.speaker_names
        ) or None
    except Exception:
        logger.debug("speaker name rebuild skipped for %s", meeting.id, exc_info=True)
    notes = store.get_notes(meeting.id)
    if notes is not None:
        data["notes_content"] = notes
    if include_transcript:
        transcript = store.get_transcript(meeting.id)
        if transcript is not None:
            data["transcript"] = transcript.model_dump(mode="json")
    return data


_AUDIO_MIME_TYPES = {
    "webm": "audio/webm",
    "ogg": "audio/ogg",
    "opus": "audio/ogg",
    "wav": "audio/wav",
    "mp3": "audio/mpeg",
    "m4a": "audio/mp4",
    "mp4": "audio/mp4",
    "aac": "audio/aac",
    "flac": "audio/flac",
}


# ── Meeting CRUD ─────────────────────────────────────────────


def _active_transcription_supports_hot_words() -> bool:
    """True if file or realtime active adapter declares supports_hot_words."""
    try:
        from src.config import get_config
        from src.meeting.transcription import resolve_file_adapter, resolve_realtime_adapter
        from src.meeting.transcription.registry import (
            cls_supports_hot_words,
            file_transcription_registry,
            realtime_transcription_registry,
        )

        config = get_config()
        file_cfg = config.transcription.active_file_provider
        if file_cfg is None:
            file_cfg = config.transcription.get_local_file_provider()
        rt_cfg = config.transcription.active_realtime_provider
        if rt_cfg is None:
            rt_cfg = config.transcription.get_local_realtime_provider()

        def _supports(provider_cfg, registry, resolve_adapter) -> bool:
            if not provider_cfg:
                return False
            adapter_name = resolve_adapter(provider_cfg.adapter or "")
            entry = registry.get(adapter_name)
            if not entry:
                return False
            return cls_supports_hot_words(entry.cls)

        return _supports(
            file_cfg, file_transcription_registry, resolve_file_adapter
        ) or _supports(
            rt_cfg, realtime_transcription_registry, resolve_realtime_adapter
        )
    except Exception as exc:
        logger.debug("Hot-words support probe failed: %s", exc)
        return False


@router.post("/meetings")
async def create_meeting(body: dict = Body()):
    title = body.get("title") or datetime.now().strftime("%Y-%m-%d %H:%M")
    mode = body.get("mode")  # "upload" or "record"
    meeting_mode = MeetingMode(mode) if mode else None
    meeting = store.create_meeting(title=title, mode=meeting_mode)

    # Copy pinned libraries onto the new meeting (user can turn them off)
    try:
        if _active_transcription_supports_hot_words():
            from src.hot_words.store import apply_pinned_libraries

            meeting = apply_pinned_libraries(meeting.id)
            logger.info(
                "[CREATE] Applied pinned hot-words libraries %s to meeting %s",
                meeting.hot_words_library_ids,
                meeting.id,
            )
    except Exception as exc:
        logger.warning("[CREATE] Failed to apply pinned hot-words: %s", exc)

    logger.info("[CREATE] Meeting '%s' id=%s mode=%s", title, meeting.id, meeting_mode)
    return meeting.model_dump()


@router.get("/meetings")
async def list_meetings():
    meetings = store.list_meetings()
    logger.debug("[LIST] Returning %d meetings", len(meetings))
    return [m.model_dump() for m in meetings]


@router.get("/meetings/{meeting_id}")
async def get_meeting(meeting_id: str):
    meeting = store.get_meeting(meeting_id)
    if not meeting:
        logger.warning("[GET] Meeting %s NOT FOUND", meeting_id)
        raise HTTPException(404, "Meeting not found")
    if meeting.speaker_slots:
        try:
            from src.speakers.service import apply_matches_from_slots

            if apply_matches_from_slots(meeting_id):
                meeting = store.get_meeting(meeting_id) or meeting
        except Exception:
            logger.debug("speaker rematch skipped for %s", meeting_id, exc_info=True)
    # Trust persisted needs_reingest / ingested_content_hash (set on save & allocate).
    # Do NOT re-hash every section MD here — that made GET/polling very slow.
    data = _serialize_meeting(meeting, include_transcript=True)
    logger.debug(
        "[GET] Meeting %s status=%s has_notes=%s has_transcript=%s audio_path=%s",
        meeting_id, meeting.status.value, "notes_content" in data,
        "transcript" in data, meeting.audio_path,
    )
    return data


@router.delete("/meetings/{meeting_id}")
async def delete_meeting(meeting_id: str):
    logger.info("[DELETE] Meeting %s", meeting_id)
    meeting = store.get_meeting(meeting_id)
    if meeting:
        meeting_service.cleanup_meeting_allocations(meeting)
    deleted = store.delete_meeting(meeting_id)
    if not deleted:
        logger.warning("[DELETE] Meeting %s NOT FOUND", meeting_id)
        raise HTTPException(404, "Meeting not found")
    logger.info("[DELETE] Meeting %s deleted successfully", meeting_id)
    return {"message": "Meeting deleted"}



@router.post("/meetings/{meeting_id}/discard")
async def discard_meeting_recording(meeting_id: str):
    """Discard all recording data: stop recording, delete audio + transcript, reset to 'created'."""
    logger.info("[DISCARD] Meeting %s", meeting_id)
    try:
        meeting = store.discard_recording(meeting_id)
    except FileNotFoundError as exc:
        logger.warning("[DISCARD] Meeting %s NOT FOUND", meeting_id)
        raise HTTPException(404, str(exc))
    logger.info("[DISCARD] Meeting %s discarded successfully", meeting_id)
    return _serialize_meeting(meeting)


@router.put("/meetings/{meeting_id}")
async def update_meeting(meeting_id: str, body: dict = Body()):
    logger.info("[UPDATE] Meeting %s fields=%s", meeting_id, list(body.keys()))
    allowed_fields = {
        "title",
        "status",
        "mode",
        "speaker_names",
        "hot_words_library_id",
        "hot_words_library_ids",
        "blueprint",
        "tabs",
    }
    fields = {k: v for k, v in body.items() if k in allowed_fields}
    # Handle notes separately -- save to file
    if "notes" in body:
        logger.info("[UPDATE] Saving notes for %s (%d chars)", meeting_id, len(body["notes"]))
        store.save_notes(meeting_id, body["notes"])
    if not fields and "notes" not in body:
        logger.warning("[UPDATE] No valid fields in request for %s", meeting_id)
        raise HTTPException(400, "No valid fields to update")
    if fields:
        meeting = store.update_meeting(meeting_id, **fields)
    else:
        meeting = store.get_meeting(meeting_id)
    logger.info("[UPDATE] Meeting %s updated, status=%s", meeting_id, meeting.status.value)
    return _serialize_meeting(meeting)


# ── File Uploads ──────────────────────────────────────────────


@router.post("/meetings/{meeting_id}/upload-audio")
async def upload_audio(meeting_id: str, file: UploadFile = File(...)):
    logger.info(
        "[UPLOAD-AUDIO] Meeting %s filename=%s content_type=%s size=%s",
        meeting_id, file.filename, file.content_type,
        file.size if hasattr(file, "size") else "unknown",
    )
    meeting = store.get_meeting(meeting_id)
    if not meeting:
        logger.warning("[UPLOAD-AUDIO] Meeting %s NOT FOUND", meeting_id)
        raise HTTPException(404, "Meeting not found")
    content = await file.read()
    logger.info("[UPLOAD-AUDIO] Read %d bytes for meeting %s", len(content), meeting_id)
    ext = (
        file.filename.rsplit(".", 1)[-1]
        if file.filename and "." in file.filename
        else "webm"
    )
    path = store.save_audio(meeting_id, content, ext, original_filename=file.filename)
    logger.info("[UPLOAD-AUDIO] Saved to %s", path)
    updated = store.update_meeting(
        meeting_id,
        mode=MeetingMode.upload,
        # Only reset to created if there's no transcript yet (i.e. pure upload flow).
        # When coming from record mode, save_realtime_transcript already set status
        # to completed — keep it so the frontend can immediately call /transcribe.
        **({} if meeting.status == MeetingStatus.completed else {"status": MeetingStatus.created}),
    )
    logger.info("[UPLOAD-AUDIO] Meeting %s updated: status=%s audio_path=%s", meeting_id, updated.status.value, updated.audio_path)
    return _serialize_meeting(updated)


@router.post("/meetings/{meeting_id}/recording-pcm")
async def ingest_recording_pcm(meeting_id: str, request: Request):
    """Append live 16 kHz s16le PCM so a crash still leaves recoverable audio."""
    meeting = store.get_meeting(meeting_id)
    if not meeting:
        raise HTTPException(404, "Meeting not found")
    chunk = await request.body()
    store.append_recording_pcm(meeting_id, chunk)
    return {"ok": True, "bytes": len(chunk)}


@router.post("/meetings/{meeting_id}/finalize-recording")
def finalize_recording(meeting_id: str):
    meeting = store.get_meeting(meeting_id)
    if not meeting:
        raise HTTPException(404, "Meeting not found")
    path = store.finalize_recording_pcm(meeting_id)
    updated = store.get_meeting(meeting_id)
    logger.info("[FINALIZE-RECORDING] Meeting %s audio_path=%s", meeting_id, path)
    return _serialize_meeting(updated)


@router.get("/meetings/{meeting_id}/audio")
async def serve_audio(meeting_id: str, token: str | None = None):
    """Serve the audio file for playback and for external transcription services.

    When a ``token`` query parameter is provided, it is validated before
    serving.  This protects URLs handed to external services (e.g. DashScope)
    from being accessed after the token expires.
    """
    if token is not None:
        from src.meeting.security import verify_audio_token
        if not verify_audio_token(meeting_id, token):
            logger.warning("[AUDIO] Invalid or expired token for meeting %s", meeting_id)
            raise HTTPException(401, "Invalid or expired token")
    meeting = store.get_meeting(meeting_id)
    if not meeting or not meeting.audio_path:
        raise HTTPException(400, "No audio file")
    from src.speakers.service import resolve_meeting_audio_path

    resolved = resolve_meeting_audio_path(meeting.audio_path)
    audio_path = resolved if resolved is not None else Path(meeting.audio_path)
    if not audio_path.exists():
        raise HTTPException(404, "Audio file not found on disk")
    ext = audio_path.suffix.lstrip(".").lower()
    media_type = _AUDIO_MIME_TYPES.get(ext, "application/octet-stream")
    logger.debug("[AUDIO] Serving %s for meeting %s (mime=%s)", audio_path, meeting_id, media_type)
    return FileResponse(
        path=str(audio_path),
        media_type=media_type,
        filename=audio_path.name,
    )


@router.get("/meetings/{meeting_id}/transcript")
async def get_transcript(meeting_id: str):
    """Return transcript segments for a meeting.

    Prefers sentences.json (with section_tags) when available;
    falls back to transcript.json otherwise.
    """
    sentences = store.get_sentences(meeting_id)
    if sentences:
        segments = [
            {
                "start": s.get("start_time", 0),
                "end": s.get("end_time", 0),
                "text": s.get("original_text", ""),
                "speaker_id": s.get("speaker", ""),
                "sentence_id": s.get("sentence_id", ""),
                "section_tags": s.get("section_tags", []),
            }
            for s in sentences
        ]
        return {"segments": segments}

    # Fallback: transcript.json without sentence metadata
    transcript = store.get_transcript(meeting_id)
    if not transcript:
        return {"segments": []}

    segments = [
        {**seg.model_dump(), "sentence_id": "", "section_tags": []}
        for seg in transcript.segments
    ]
    return {"segments": segments}



@router.get("/transcription/active-provider-info")
async def get_active_provider_info():
    """Return hot-words support and supported language hints for active providers.

    Uses the registry directly to avoid creating actual provider instances,
    which would trigger ML model downloads for local providers.
    """
    from src.config import get_config
    from src.meeting.transcription import resolve_file_adapter, resolve_realtime_adapter
    from src.meeting.transcription.registry import (
        cls_supports_hot_words,
        file_transcription_registry,
        realtime_transcription_registry,
    )

    config = get_config()

    def _info(provider_cfg, registry, *, resolve_adapter):
        from src.meeting.transcription.base import language_hint_limit

        empty = {
            "supports_hot_words": False,
            "supported_language_hints": [],
            "max_language_hints": 1,
            "adapter": None,
            "id": None,
            "name": None,
            "display_name": None,
            "model": None,
        }
        if not provider_cfg:
            return empty
        adapter_name = resolve_adapter(provider_cfg.adapter or "")
        model = getattr(provider_cfg, "model", None)
        meta = {
            "adapter": adapter_name or provider_cfg.adapter or None,
            "id": provider_cfg.id or None,
            "name": provider_cfg.name or None,
            "display_name": None,
            "model": model,
        }
        # If provider has custom language_hints_config, use it (openai_compatible etc.)
        custom = getattr(provider_cfg, "language_hints_config", None)
        if custom:
            entry = registry.get(adapter_name)
            if entry:
                supports = cls_supports_hot_words(entry.cls)
                meta["display_name"] = entry.display_name or entry.name
                max_hints = language_hint_limit(entry.cls, model)
            else:
                supports = False
                max_hints = 1
            # Build hint list from custom config, ensuring "auto" is always first
            hints = [{"code": h.get("code", ""), "label": h.get("label", "")} for h in custom]
            if not any(h["code"] == "auto" for h in hints):
                hints.insert(0, {"code": "auto", "label": "Auto"})
            return {
                "supports_hot_words": supports,
                "supported_language_hints": hints,
                "max_language_hints": max_hints,
                **meta,
            }
        entry = registry.get(adapter_name)
        if not entry:
            return {**empty, **meta}
        adapter_cls = entry.cls
        supports = cls_supports_hot_words(adapter_cls)
        meta["display_name"] = entry.display_name or entry.name
        return {
            "supports_hot_words": supports,
            "supported_language_hints": list(
                getattr(adapter_cls, "SUPPORTED_LANGUAGE_HINTS", [])
            ),
            "max_language_hints": language_hint_limit(adapter_cls, model),
            **meta,
        }

    # Prefer configured active providers; fall back to built-in local ONNX factories
    # so the Meeting language UI still has options before user sets Default.
    file_cfg = config.transcription.active_file_provider
    if file_cfg is None:
        file_cfg = config.transcription.get_local_file_provider()
    rt_cfg = config.transcription.active_realtime_provider
    if rt_cfg is None:
        rt_cfg = config.transcription.get_local_realtime_provider()

    return {
        "file": _info(
            file_cfg,
            file_transcription_registry,
            resolve_adapter=resolve_file_adapter,
        ),
        "realtime": _info(
            rt_cfg,
            realtime_transcription_registry,
            resolve_adapter=resolve_realtime_adapter,
        ),
    }


@router.post("/meetings/{meeting_id}/save-transcript")
async def save_realtime_transcript(meeting_id: str, body: dict = Body()):
    """Persist a transcript produced by the realtime (WebSocket) flow.

    The realtime provider streams segments to the browser via WebSocket, but
    the backend never sees them as finished data — the WebSocket handler just
    forwards events. After the user stops recording, the browser POSTs the
    collected segments here so the meeting gets a saved transcript, a
    ``transcript_path``, and a status of ``completed``. Subsequent operations
    (Summarize, Allocate) then work the same as for file-based meetings.
    """
    logger.info("[SAVE-TRANSCRIPT] Meeting %s (realtime path)", meeting_id)
    meeting = store.get_meeting(meeting_id)
    if not meeting:
        logger.warning("[SAVE-TRANSCRIPT] Meeting %s NOT FOUND", meeting_id)
        raise HTTPException(404, "Meeting not found")
    raw_segments = body.get("segments") or []
    if not isinstance(raw_segments, list):
        raise HTTPException(400, "segments must be a list")
    try:
        segments = [TranscriptSegment(**s) for s in raw_segments]
    except Exception as exc:
        logger.warning("[SAVE-TRANSCRIPT] Invalid segment payload for %s: %s", meeting_id, exc)
        raise HTTPException(400, f"Invalid segment payload: {exc}")
    text = body.get("text") or " ".join(s.text for s in segments)
    result = TranscriptionResult(text=text, segments=segments)
    store.save_transcript(meeting_id, result)
    logger.info(
        "[SAVE-TRANSCRIPT] Saved %d segments (%d chars) for meeting %s",
        len(segments), len(text), meeting_id,
    )
    try:
        recovered = store.finalize_recording_pcm(meeting_id)
        if recovered:
            logger.info("[SAVE-TRANSCRIPT] Finalized live PCM → %s", recovered)
    except Exception as exc:
        logger.warning("[SAVE-TRANSCRIPT] PCM finalize failed (non-fatal): %s", exc)

    # Phase 0: clean old pipeline data + normalize. Speakers gate next (or file-tx);
    # do not leave stale tabs/blueprint that would auto-unlock Studio.
    try:
        from src.meeting.pipeline import normalize_sentences

        store.delete_pipeline_data(meeting_id)

        sentences = normalize_sentences(meeting_id, segments)
        store.save_sentences(meeting_id, [s.model_dump() for s in sentences])

        logger.info(
            "[SAVE-TRANSCRIPT] Normalized %d sentences for meeting %s",
            len(sentences), meeting_id,
        )
    except Exception as e:
        logger.warning("[SAVE-TRANSCRIPT] Post-save hook failed (non-fatal): %s", e)

    store.update_meeting(
        meeting_id,
        status=MeetingStatus.completed,
        processing_state=ProcessingState.idle.value,
        summary_gen_state=GenerationState.idle.value,
        blueprint_gen_state=GenerationState.idle.value,
        summary=None,
        detail=None,
        blueprint=None,
        blueprint_taxonomy=None,
        tabs=None,
    )
    logger.info(
        "[SAVE-TRANSCRIPT] Transcript saved for %s — Speakers gate (or file-tx), then Summary",
        meeting_id,
    )

    return {"message": "Transcript saved", "segments": len(segments)}


@router.post("/meetings/{meeting_id}/upload-notes")
async def upload_notes(meeting_id: str, file: UploadFile = File(...)):
    logger.info("[UPLOAD-NOTES] Meeting %s filename=%s", meeting_id, file.filename)
    meeting = store.get_meeting(meeting_id)
    if not meeting:
        logger.warning("[UPLOAD-NOTES] Meeting %s NOT FOUND", meeting_id)
        raise HTTPException(404, "Meeting not found")
    content_bytes = await file.read()
    filename = file.filename or "notes.txt"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "txt"

    # Parse based on file type
    if ext in ("md", "txt"):
        content = content_bytes.decode("utf-8", errors="replace")
    elif ext == "docx":
        try:
            import mammoth
            from src.parsers.docx import clean_mammoth_markdown

            with io.BytesIO(content_bytes) as buf:
                result = mammoth.convert_to_markdown(buf)
            content = clean_mammoth_markdown(result.value)
        except Exception:
            content = content_bytes.decode("utf-8", errors="replace")
    else:
        content = content_bytes.decode("utf-8", errors="replace")

    path = store.save_notes(meeting_id, content)
    logger.info("[UPLOAD-NOTES] Saved %d chars to %s for meeting %s", len(content), path, meeting_id)
    return {"message": "Notes uploaded", "path": path, "notes_content": content}


@router.post("/meetings/{meeting_id}/images")
async def upload_meeting_note_image(meeting_id: str, file: UploadFile = File(...)):
    """Upload an image for meeting notes. Returns the URL to insert in markdown."""
    meeting = store.get_meeting(meeting_id)
    if not meeting:
        raise HTTPException(404, "Meeting not found")
    content_bytes = await file.read()
    if not content_bytes:
        raise HTTPException(400, "Empty file")
    try:
        safe_name = store.save_note_image(meeting_id, file.filename or "image.png", content_bytes)
    except FileNotFoundError:
        raise HTTPException(404, "Meeting not found")
    except ValueError:
        raise HTTPException(400, "Invalid image path")
    url = f"/api/meetings/{meeting_id}/images/{safe_name}"
    logger.info("[IMAGE] Uploaded %s (%d bytes) for meeting %s", safe_name, len(content_bytes), meeting_id)
    return {"url": url, "filename": safe_name}


@router.get("/meetings/{meeting_id}/images/{filename}")
async def serve_meeting_note_image(meeting_id: str, filename: str):
    """Serve an uploaded meeting-notes image."""
    try:
        image_path = store.get_note_image_path(meeting_id, filename)
    except ValueError:
        raise HTTPException(400, "Invalid image path")
    if not image_path.exists():
        raise HTTPException(404, "Image not found")
    return FileResponse(str(image_path))


# ── Transcription ─────────────────────────────────────────────


@router.post("/meetings/{meeting_id}/transcribe")
async def start_transcription(meeting_id: str, body: dict | None = Body(None)):
    logger.info("[TRANSCRIBE] Request for meeting %s", meeting_id)
    meeting = store.get_meeting(meeting_id)
    if not meeting:
        logger.warning("[TRANSCRIBE] Meeting %s NOT FOUND", meeting_id)
        raise HTTPException(404, "Meeting not found")
    if not meeting.audio_path:
        recovered = store.finalize_recording_pcm(meeting_id)
        if recovered:
            meeting = store.get_meeting(meeting_id)
            logger.info("[TRANSCRIBE] Recovered live PCM → %s", recovered)
    if not meeting or not meeting.audio_path:
        logger.warning("[TRANSCRIBE] Meeting %s has NO AUDIO", meeting_id)
        raise HTTPException(
            400,
            "No audio file was saved for this meeting. Record again or upload an audio file, then re-transcribe.",
        )

    # Check active provider exists
    provider = meeting_service.get_active_file_provider()
    if not provider:
        logger.warning("[TRANSCRIBE] No active file transcription provider configured")
        raise HTTPException(400, "No active file transcription provider configured")

    logger.info("[TRANSCRIBE] Provider found: %s, updating status to transcribing", type(provider).__name__)
    # Drop previous Studio/summary work so the client always re-enters the
    # speaker gate after file transcription (re-tx must not skip Speakers).
    try:
        store.delete_pipeline_data(meeting_id)
    except Exception as exc:
        logger.warning("[TRANSCRIBE] delete_pipeline_data failed (non-fatal): %s", exc)
    store.update_meeting(
        meeting_id,
        status=MeetingStatus.transcribing,
        transcription_error=None,
        processing_state=ProcessingState.idle.value,
        summary_gen_state=GenerationState.idle.value,
        blueprint_gen_state=GenerationState.idle.value,
        summary=None,
        detail=None,
        blueprint=None,
        blueprint_taxonomy=None,
        tabs=None,
        speaker_names=None,
    )

    language_hints = body.get("language_hints") if isinstance(body, dict) else None

    task = task_manager.create_task(
        filename=f"meeting_{meeting_id}",
        task_type="transcribe",
        collection="meetings",
        meeting_id=meeting_id,
        language_hints=language_hints,
    )
    logger.info("[TRANSCRIBE] Task created: id=%s for meeting %s", task.id, meeting_id)
    return {"message": "Transcription started", "task_id": task.id}


@router.post("/meetings/{meeting_id}/cancel-transcribe")
async def cancel_transcription(meeting_id: str):
    """Cancel an in-progress transcription task and reset meeting status."""
    meeting = store.get_meeting(meeting_id)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    # Find and cancel any running transcribe task for this meeting
    cancelled = False
    for task in task_manager.get_all_tasks():
        task_args = task_manager._task_args.get(task.id)
        if task_args and task_args[0] == "transcribe" and task_args[1].get("meeting_id") == meeting_id:
            if task_manager.cancel_task(task.id):
                cancelled = True
    store.update_meeting(meeting_id, status=MeetingStatus.created, transcription_error=None)
    return {"message": "Transcription cancelled", "cancelled": cancelled}


@router.get("/meetings/{meeting_id}/tasks")
async def get_meeting_tasks(meeting_id: str):
    tasks = task_manager.get_all_tasks()
    meeting_tasks = [
        t.to_dict()
        for t in tasks
        if t.filename == f"meeting_{meeting_id}"
    ]
    pending = sum(1 for t in meeting_tasks if t["status"] == "pending")
    processing = sum(1 for t in meeting_tasks if t["status"] == "processing")
    logger.debug("[TASKS] Meeting %s: %d total, %d pending, %d processing", meeting_id, len(meeting_tasks), pending, processing)
    return {"tasks": meeting_tasks, "pending": pending, "processing": processing}


@router.websocket("/meetings/{meeting_id}/realtime-transcribe")
async def realtime_transcribe(websocket: WebSocket, meeting_id: str):
    print(f"[REALTIME-WS] >>> handler entered, meeting={meeting_id}", flush=True)
    logger.info("[REALTIME-WS] Connection for meeting %s", meeting_id)
    await websocket.accept()
    print(f"[REALTIME-WS] >>> accepted, getting provider", flush=True)

    # Capture the loop now, while we're on the event loop's thread.
    # on_segment() is invoked from a different thread (the DashScope SDK thread)
    # and cannot use asyncio.get_event_loop() there.
    main_loop = asyncio.get_running_loop()

    meta = meeting_service.get_active_realtime_provider_meta()
    logger.info(
        "[REALTIME-WS] Config active realtime: id=%s adapter=%s model=%s",
        meta.get("id"),
        meta.get("adapter"),
        meta.get("model"),
    )
    # Tell client which engine will be used (before possibly slow local load)
    await websocket.send_json(
        {
            "type": "provider",
            "provider_id": meta.get("id"),
            "adapter": meta.get("adapter"),
            "name": meta.get("name"),
            "model": meta.get("model"),
        }
    )

    provider = meeting_service.get_active_realtime_provider()
    if not provider:
        print("[REALTIME-WS] >>> NO PROVIDER CONFIGURED", flush=True)
        logger.warning("[REALTIME-WS] No active realtime transcription provider")
        await websocket.send_json(
            {"error": "No active realtime transcription provider"}
        )
        await websocket.close()
        return

    print(
        f"[REALTIME-WS] >>> provider: {type(provider).__name__} adapter={meta.get('adapter')}",
        flush=True,
    )
    logger.info(
        "[REALTIME-WS] Provider instance: %s (adapter=%s)",
        type(provider).__name__,
        meta.get("adapter"),
    )

    async def _safe_send(payload):
        try:
            await websocket.send_json(payload)
        except Exception:
            pass  # client disconnected, ignore

    def on_segment(segment, is_final, key):
        try:
            payload = {
                "type": "transcript",
                "key": str(key) if key is not None else None,
                "start": segment.start,
                "end": segment.end,
                "text": segment.text,
                "speaker_id": segment.speaker_id,
                "is_final": is_final,
            }
            main_loop.call_soon_threadsafe(
                asyncio.create_task, _safe_send(payload)
            )
        except Exception as exc:
            print(f"[REALTIME-WS] >>> on_segment error: {exc!r}", flush=True)

    # Track whether the SDK has already been stopped, so the finally
    # block can skip a redundant stop() (and the WS stays open long enough
    # to deliver the last in-flight event before the underlying Task
    # objects run).
    provider_already_stopped = False

    # Read language hints from query params (e.g. ?language_hints=zh&language_hints=en)
    language_hints = websocket.query_params.getlist("language_hints") or None
    # "auto" means auto-detect — strip it so the provider doesn't receive it
    if language_hints:
        language_hints = [h for h in language_hints if h != "auto"] or None
    if language_hints:
        logger.info("[REALTIME-WS] Language hints from client: %s", language_hints)

    try:
        # Load hot words only when the active realtime adapter can apply them
        hot_words = None
        meeting = store.get_meeting(meeting_id)
        if meeting and getattr(provider, "supports_hot_words", False):
            from src.hot_words.store import collect_meeting_hot_words
            hot_words = collect_meeting_hot_words(meeting) or None
            if hot_words:
                logger.info("[REALTIME-WS] Loaded %d hot words for meeting %s", len(hot_words), meeting_id)

        print(f"[REALTIME-WS] >>> calling provider.start()", flush=True)
        await provider.start(on_segment, hot_words=hot_words, language_hints=language_hints)
        print(f"[REALTIME-WS] >>> provider.start() returned, entering receive loop", flush=True)
        await websocket.send_json({"type": "ready", "message": "realtime session ready"})

        client_requested_stop = False
        pcm_frames = 0
        pcm_bytes = 0
        while True:
            # receive() returns a dict with "type" plus either "text" or "bytes".
            # We use this to support a JSON stop signal from the client (so
            # the SDK can flush its last sentence before we tear down).
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                raise WebSocketDisconnect()
            if "text" in message:
                try:
                    payload = json.loads(message["text"])
                except Exception:
                    payload = {}
                if payload.get("action") == "stop":
                    print(f"[REALTIME-WS] >>> client sent stop signal", flush=True)
                    client_requested_stop = True
                    break
                # Unknown text message — ignore.
                continue
            if "bytes" in message:
                data = message["bytes"] or b""
                pcm_frames += 1
                pcm_bytes += len(data)
                if pcm_frames <= 3 or pcm_frames % 50 == 0:
                    logger.info(
                        "[REALTIME-WS] pcm frame#%d +%dB total=%dB adapter=%s",
                        pcm_frames,
                        len(data),
                        pcm_bytes,
                        meta.get("adapter"),
                    )
                # send_frame only enqueues for local FunASR; must stay fast
                # so the WS receive loop does not stall behind generate().
                await provider.send_frame(data)

        # Client asked for a graceful stop. Order matters:
        #   1. Tell the SDK to finalize first (otherwise it doesn't know
        #      to flush and the last sentence is lost).
        #   2. THEN wait ~2s for the last in-flight segment to be
        #      delivered through on_event → on_segment → WebSocket send.
        # The previous version did the sleep first, so the SDK never got
        # the chance to flush — the user's last words were dropped.
        if client_requested_stop:
            print(
                f"[REALTIME-WS] >>> stopping provider first, then waiting for flush",
                flush=True,
            )
            await provider.stop()
            provider_already_stopped = True
            await asyncio.sleep(2.0)
    except WebSocketDisconnect:
        print(f"[REALTIME-WS] >>> client disconnected for meeting {meeting_id}", flush=True)
        logger.info("[REALTIME-WS] Client disconnected for meeting %s", meeting_id)
    except Exception as exc:
        print(f"[REALTIME-WS] >>> ERROR for meeting {meeting_id}: {exc!r}", flush=True)
        logger.error("[REALTIME-WS] Error for meeting %s: %s", meeting_id, exc, exc_info=True)
    finally:
        if not provider_already_stopped:
            await provider.stop()
        print(f"[REALTIME-WS] <<< handler exiting for meeting {meeting_id}", flush=True)
        logger.info("[REALTIME-WS] Provider stopped for meeting %s", meeting_id)


# ── Summary & Allocation ──────────────────────────────────────


@router.post("/meetings/{meeting_id}/generate-summary")
async def generate_summary(meeting_id: str):
    logger.info("[SUMMARY] Generate request for meeting %s", meeting_id)
    meeting = store.get_meeting(meeting_id)
    if not meeting:
        logger.warning("[SUMMARY] Meeting %s NOT FOUND", meeting_id)
        raise HTTPException(404, "Meeting not found")
    if meeting.processing_state != ProcessingState.idle.value:
        logger.warning("[SUMMARY] Meeting %s is busy: %s", meeting_id, meeting.processing_state)
        raise HTTPException(409, f"Meeting is busy: {meeting.processing_state}")
    transcript = store.get_transcript(meeting_id)
    if not transcript:
        logger.warning("[SUMMARY] Meeting %s has NO TRANSCRIPT", meeting_id)
        raise HTTPException(400, "No transcript available")

    logger.info("[SUMMARY] Starting LLM generation for meeting %s (%d transcript segments)", meeting_id, len(transcript.segments))
    store.update_meeting(meeting_id, processing_state=ProcessingState.summarizing.value)
    task = task_manager.create_task(
        filename=f"summary:{meeting_id}",
        task_type="meeting_summary",
        meeting_id=meeting_id,
    )
    logger.info("[SUMMARY] Task created for meeting %s: task_id=%s", meeting_id, task.id)
    updated = store.get_meeting(meeting_id)
    if not updated:
        raise HTTPException(404, "Meeting not found")
    return _serialize_meeting(updated)


@router.post("/meetings/{meeting_id}/transcript-index")
async def start_transcript_index(meeting_id: str):
    """Build or rebuild the verbatim transcript vector index."""
    meeting = store.get_meeting(meeting_id)
    if not meeting:
        raise HTTPException(404, "Meeting not found")
    if (meeting.transcript_index_status or "") == "building":
        return _serialize_meeting(meeting)
    sentences = store.get_sentences(meeting_id)
    transcript = store.get_transcript(meeting_id)
    if not sentences and not transcript:
        raise HTTPException(400, "No transcript available")
    store.update_meeting(meeting_id, transcript_index_status="building", transcript_index_error="")
    task_manager.create_task(
        filename=f"tx-index:{meeting_id}",
        task_type="meeting_transcript_index",
        meeting_id=meeting_id,
    )
    updated = store.get_meeting(meeting_id)
    if not updated:
        raise HTTPException(404, "Meeting not found")
    return _serialize_meeting(updated)


def _sse_event(event: str, data: object) -> str:
    """Format a dict as an SSE event string."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.get("/meetings/{meeting_id}/blueprint/stream")
async def stream_blueprint(meeting_id: str):
    """Stream blueprint generation as SSE events.

    Pass 1 — General Summary (streaming tokens):
      event: state  → {"summary": "prefilling"|"streaming"|"idle"}
      event: thinking → "reasoning text chunk"
      event: token  → "markdown content chunk"
      event: summary_done → {"title": "...", "general_md": "..."}

    Pass 2 — Blueprint Decomposition:
      event: state → {"blueprint": "prefilling"|"idle"}
      event: blueprint_done → {"taxonomy": {...}, "blueprint": [...]}

    Errors:
      event: error → {"message": "..."}
    """
    logger.info("[SSE] Blueprint stream request for meeting %s", meeting_id)
    meeting = store.get_meeting(meeting_id)
    if not meeting:
        def _err():
            yield _sse_event("error", {"message": "Meeting not found"})
        return StreamingResponse(_err(), media_type="text/event-stream")
    if meeting.processing_state != ProcessingState.idle.value:
        def _err():
            yield _sse_event("error", {"message": f"Meeting is busy: {meeting.processing_state}"})
        return StreamingResponse(_err(), media_type="text/event-stream")
    transcript = store.get_transcript(meeting_id)
    if not transcript:
        def _err():
            yield _sse_event("error", {"message": "No transcript available"})
        return StreamingResponse(_err(), media_type="text/event-stream")

    def _stream():
        for evt in meeting_service.generate_blueprint_stream(meeting_id):
            yield _sse_event(evt["event"], evt["data"])

    return StreamingResponse(_stream(), media_type="text/event-stream")


@router.get("/meetings/{meeting_id}/sections/{tab_id}/generate-stream")
async def stream_section_generation(meeting_id: str, tab_id: str):
    """Stream single-section generation as SSE events.

    Events:
      event: state  → {"section_gen": "prefilling"|"streaming"|"idle"}
      event: thinking → "reasoning text chunk"
      event: token  → "markdown content chunk"
      event: section_done → {"tab_id": "...", "md": "..."}
      event: error → {"message": "..."}
    """
    logger.info("[SSE] Section stream request for meeting %s tab=%s", meeting_id, tab_id)
    meeting = store.get_meeting(meeting_id)
    if not meeting:
        def _err():
            yield _sse_event("error", {"message": "Meeting not found"})
        return StreamingResponse(_err(), media_type="text/event-stream")

    def _stream():
        for evt in meeting_service.generate_section_stream(meeting_id, tab_id):
            yield _sse_event(evt["event"], evt["data"])

    return StreamingResponse(_stream(), media_type="text/event-stream")


@router.post("/meetings/{meeting_id}/extract")
async def start_extract(meeting_id: str, body: dict = Body()):
    """Start extract for one or more section receipts (v3).

    Body: {"receipts": [{"source": "blueprint"|"custom", "tab_id"?: "...", "name": "...", "description": "..."}]}
    """
    receipts = body.get("receipts", [])
    if not receipts:
        raise HTTPException(400, "receipts is required and must not be empty")
    logger.info("[EXTRACT] Request for meeting %s: %d receipts", meeting_id, len(receipts))
    try:
        meeting = await meeting_service.start_extract(meeting_id, receipts)
    except FileNotFoundError as exc:
        raise HTTPException(400, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except RuntimeError as exc:
        raise HTTPException(400, str(exc))
    logger.info("[EXTRACT] Started for meeting %s", meeting_id)
    return _serialize_meeting(meeting)


@router.delete("/meetings/{meeting_id}/sections/{tab_id}")
async def delete_section(meeting_id: str, tab_id: str):
    """Delete a section and clean up its tags (Node 2.1)."""
    logger.info("[DELETE-SECTION] Meeting %s tab=%s", meeting_id, tab_id)
    try:
        meeting = await meeting_service.delete_section(meeting_id, tab_id)
    except FileNotFoundError as exc:
        raise HTTPException(400, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except RuntimeError as exc:
        raise HTTPException(400, str(exc))
    return _serialize_meeting(meeting)


@router.post("/meetings/{meeting_id}/sections/{tab_id}/regenerate")
async def regenerate_section(meeting_id: str, tab_id: str):
    """Regenerate one section: clean tags + re-scan + re-summarize."""
    logger.info("[REGENERATE] Meeting %s tab=%s", meeting_id, tab_id)
    try:
        meeting = await meeting_service.start_section_regenerate(
            meeting_id, tab_id
        )
    except FileNotFoundError as exc:
        raise HTTPException(400, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except RuntimeError as exc:
        raise HTTPException(400, str(exc))
    logger.info("[REGENERATE] Started for meeting %s tab=%s", meeting_id, tab_id)
    return _serialize_meeting(meeting)


@router.post("/meetings/{meeting_id}/sections/{tab_id}/allocate")
async def allocate_section(meeting_id: str, tab_id: str, body: dict):
    """Allocate one section's content to a collection (speaker names resolved, refs stripped).

    Body: collection_id (required), chain_id (optional — defaults to main).
    Response is the meeting dict plus bridge fields: file_id, task_id, node_id, source, chain_id.
    """
    collection_id = body.get("collection_id", "")
    if not collection_id:
        raise HTTPException(400, "collection_id is required")
    chain_id = (body.get("chain_id") or "").strip() or None
    logger.info(
        "[ALLOCATE_SECTION] Meeting %s tab=%s → collection=%s chain=%s",
        meeting_id,
        tab_id,
        collection_id,
        (chain_id or "")[:12],
    )
    try:
        meeting, bridge = await meeting_service.allocate_section_to_collection(
            meeting_id, tab_id, collection_id, chain_id=chain_id,
        )
    except FileNotFoundError as exc:
        raise HTTPException(400, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    logger.info(
        "[ALLOCATE_SECTION] Done meeting %s tab=%s file=%s task=%s node=%s",
        meeting_id,
        tab_id,
        (bridge.get("file_id") or "")[:12],
        bridge.get("task_id"),
        (bridge.get("node_id") or "")[:12],
    )
    out = _serialize_meeting(meeting)
    out.update(bridge)
    return out


@router.get("/meetings/{meeting_id}/sections/{tab_id}/todo-candidates")
async def get_section_todo_candidates(
    meeting_id: str,
    tab_id: str,
    refresh: bool = False,
):
    """Return todo candidates for a section.

    If none stored (legacy allocate before this feature) or ``refresh=1``,
    re-parse the current section MD, persist, and return.
    """
    meeting = store.get_meeting(meeting_id)
    if meeting is None:
        raise HTTPException(404, "Meeting not found")
    tab_meta: dict | None = None
    for t in meeting.tabs or []:
        td = t if isinstance(t, dict) else t.model_dump()
        if td.get("tab_id") == tab_id:
            tab_meta = td
            break
    if tab_meta is None:
        raise HTTPException(404, f"Tab '{tab_id}' not found")

    cands = list(tab_meta.get("todo_candidates") or [])
    if refresh or not cands:
        try:
            cands = meeting_service.extract_section_todo_candidates(
                meeting_id,
                tab_id,
                persist=True,
                use_llm=True,
            )
            # reload tab meta after persist
            meeting = store.get_meeting(meeting_id) or meeting
            for t in meeting.tabs or []:
                td = t if isinstance(t, dict) else t.model_dump()
                if td.get("tab_id") == tab_id:
                    tab_meta = td
                    break
        except Exception as exc:
            logger.warning(
                "extract todo candidates failed %s/%s: %s",
                meeting_id,
                tab_id,
                exc,
            )

    return {
        "meeting_id": meeting_id,
        "tab_id": tab_id,
        "section_name": (tab_meta or {}).get("name") or tab_id,
        "collection_id": (tab_meta or {}).get("associated_collection_id") or "",
        "chain_id": (tab_meta or {}).get("allocated_chain_id") or "",
        "node_id": (tab_meta or {}).get("allocated_node_id") or "",
        "candidates": cands,
    }


@router.post("/meetings/{meeting_id}/todo-candidates/mark-created")
async def mark_todo_candidates_created(meeting_id: str, body: dict):
    """After checklist create, bind candidate_id → todo_id to prevent re-offer.

    Body: ``{ "items": [ {"tab_id": "...", "candidate_id": "...", "todo_id": "..."}, ... ] }``
    """
    items = body.get("items") if isinstance(body, dict) else None
    if not isinstance(items, list):
        raise HTTPException(400, "items array is required")
    try:
        meeting = meeting_service.mark_todo_candidates_created(meeting_id, items)
    except FileNotFoundError as exc:
        raise HTTPException(400, str(exc))
    return _serialize_meeting(meeting)


@router.delete("/meetings/{meeting_id}/sections/{tab_id}/allocate")
async def delete_section_allocation(meeting_id: str, tab_id: str):
    """Remove a section's collection allocation (delete file snapshot + clear metadata)."""
    logger.info("[DELETE_ALLOCATION] Meeting %s tab=%s", meeting_id, tab_id)
    try:
        meeting = await meeting_service.delete_section_allocation(meeting_id, tab_id)
    except FileNotFoundError as exc:
        raise HTTPException(400, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    logger.info("[DELETE_ALLOCATION] Done meeting %s tab=%s", meeting_id, tab_id)
    return _serialize_meeting(meeting)


@router.get("/meetings/{meeting_id}/sections/{tab_id}/md")
async def get_section_md_content(meeting_id: str, tab_id: str):
    """Serve the rendered markdown content for a section tab."""
    from fastapi.responses import PlainTextResponse
    content = store.get_section_md(meeting_id, tab_id)
    if content is None:
        return PlainTextResponse(
            content="Section not found",
            status_code=404,
            media_type="text/plain; charset=utf-8",
        )
    return PlainTextResponse(content=content, media_type="text/markdown; charset=utf-8")


@router.put("/meetings/{meeting_id}/sections/{tab_id}/md")
async def save_section_md_content(meeting_id: str, tab_id: str, body: dict = Body()):
    """Save edited markdown content for a section tab.

    If the tab is already allocated to a collection, mark ``needs_reingest``
    so the UI can offer manual "Update collection" (new file version).
    """
    content = body.get("content", "")
    path = store.save_section_md(meeting_id, tab_id, content)
    # Invalidate cached meeting→note distillation for this tab
    try:
        from src.notes.service import invalidate_meeting_distillation
        invalidate_meeting_distillation(meeting_id, tab_id)
    except Exception:
        pass

    # Single meta write: only touch the edited tab (no full-tab MD rehash).
    needs_reingest = False
    meeting = store.get_meeting(meeting_id)
    if meeting and meeting.tabs:
        updated_tabs: list[dict] = []
        for t in meeting.tabs:
            td = dict(t) if isinstance(t, dict) else (
                t.model_dump() if hasattr(t, "model_dump") else dict(t)
            )
            if td.get("tab_id") == tab_id:
                fid = (td.get("allocated_file_id") or "").strip()
                if fid:
                    stored = (td.get("ingested_content_hash") or "").strip()
                    current = store.section_content_hash(content)
                    # No hash (legacy allocate): any save after allocate counts as dirty
                    needs_reingest = (not stored) or (current != stored)
                    td["needs_reingest"] = needs_reingest
                else:
                    td["needs_reingest"] = False
            updated_tabs.append(td)
        meeting = store.update_meeting(meeting_id, tabs=updated_tabs)

    logger.info(
        "[SAVE-SECTION-MD] Saved %s/%s (%d chars) needs_reingest=%s",
        meeting_id,
        tab_id,
        len(content),
        needs_reingest,
    )
    meeting_payload = _serialize_meeting(meeting) if meeting else None
    return {
        "ok": True,
        "path": path,
        "needs_reingest": needs_reingest,
        "meeting": meeting_payload,
    }


@router.get("/meetings/{meeting_id}/sections/{tab_id}/translate/stream")
async def stream_section_translation(meeting_id: str, tab_id: str, lang: str):
    """SSE stream of a summary translation into `lang`.

    Single code path for all cases: serves the cached file instantly when one
    exists, re-attaches to an in-progress task (replaying missed tokens), or
    starts a fresh generation.  Translation files are never ingested.
    """
    from src.meeting.service import meeting_service as _svc

    def _stream():
        for evt in _svc.generate_translation_stream(meeting_id, tab_id, lang):
            yield _sse_event(evt["event"], evt["data"])

    return StreamingResponse(_stream(), media_type="text/event-stream")


@router.get("/meetings/{meeting_id}/sections/{tab_id}/translations")
async def list_section_translations(meeting_id: str, tab_id: str):
    """List the language codes that already have a translation file."""
    from src.meeting.service import meeting_service as _svc
    return {"languages": _svc.list_summary_translations(meeting_id, tab_id)}


@router.get("/meetings/{meeting_id}/translations/active")
async def list_active_translations(meeting_id: str):
    """List the (tab_id, language) pairs currently being translated.

    Lets the frontend re-attach in-progress streams after a browser refresh.
    """
    from src.meeting.service import meeting_service as _svc
    return {"active": _svc.list_active_translations(meeting_id)}


@router.post("/meetings/{meeting_id}/generate-section-description")
async def generate_section_description(meeting_id: str, body: dict = Body()):
    """Generate a section description via LLM based on section name + General Summary."""
    from src.meeting.service import meeting_service as _svc

    section_name = (body.get("section_name") or "").strip()
    if not section_name:
        raise HTTPException(400, "section_name is required")
    try:
        result = await _svc.generate_section_description(meeting_id, section_name)
    except FileNotFoundError as exc:
        raise HTTPException(400, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return result


