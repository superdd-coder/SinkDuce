from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import struct
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import Meeting, MeetingMode, MeetingStatus, ProcessingState, GenerationState, TranscriptionResult
from src.config import DATA_DIR

from .webm_fixer import fix_webm_duration

logger = logging.getLogger("meeting.store")
MEETINGS_DIR = DATA_DIR / "meetings"


def section_content_hash(content: str) -> str:
    """Stable SHA-256 of section markdown (raw editor content on disk)."""
    return hashlib.sha256((content or "").encode("utf-8")).hexdigest()


def enrich_tabs_needs_reingest(meeting: Meeting) -> Meeting:
    """Recompute ``needs_reingest`` from disk MD vs ``ingested_content_hash``.

    Survives UI remounts: boolean-only flags can be lost; hash comparison is durable.
    Mutates a shallow copy of tab dicts on the meeting object.
    """
    if not meeting.tabs:
        return meeting
    enriched: list[dict] = []
    for t in meeting.tabs:
        td = dict(t) if isinstance(t, dict) else (
            t.model_dump() if hasattr(t, "model_dump") else dict(t)
        )
        fid = (td.get("allocated_file_id") or "").strip()
        stored = (td.get("ingested_content_hash") or "").strip()
        if fid and stored:
            tid = td.get("tab_id") or ""
            md = get_section_md(meeting.id, tid) if tid else None
            current = section_content_hash(md or "")
            td["needs_reingest"] = current != stored
        elif fid and not stored:
            # Legacy allocate without hash: keep explicit flag if set
            td.setdefault("needs_reingest", False)
        else:
            td["needs_reingest"] = False
        enriched.append(td)
    meeting.tabs = enriched
    return meeting

# Per-meeting file lock to prevent concurrent read-modify-write races
# when multiple section streams write to the same meta.json.
_locks: dict[str, threading.Lock] = {}
_locks_lock = threading.Lock()


def _get_lock(meeting_id: str) -> threading.Lock:
    with _locks_lock:
        if meeting_id not in _locks:
            _locks[meeting_id] = threading.Lock()
        return _locks[meeting_id]


def _meeting_dir(meeting_id: str) -> Path:
    from src.paths import assert_resource_id, confine

    assert_resource_id(meeting_id, name="meeting_id")
    return confine(MEETINGS_DIR / meeting_id, MEETINGS_DIR)


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write to temp file then atomic rename to prevent partial writes
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    _read_cache_evict(path)


def _read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


# ── Read cache (mtime-validated) ─────────────────────────────────────
# meta.json / sentences.json / section md are re-read several times per
# chat turn (catalog, digest, lookup stitching). Cache the parsed value
# keyed by path and revalidate with st_mtime_ns. Every write goes through
# _write_json / write_text below, so a changed mtime invalidates the entry;
# a missing file is cached as (None, None).
_read_cache: dict[str, tuple[int | None, Any]] = {}
_read_cache_lock = threading.Lock()


def _read_cached(path: Path, load):
    """mtime-validated cache around a pure read of *path*.

    ``load`` is called only on miss and must not mutate its result after
    returning (callers get defensive copies from the public getters).
    """
    key = str(path)
    try:
        mtime: int | None = path.stat().st_mtime_ns
    except OSError:
        mtime = None
    with _read_cache_lock:
        entry = _read_cache.get(key)
        if entry is not None and entry[0] == mtime:
            return entry[1]
    value = None if mtime is None else load(path)
    with _read_cache_lock:
        _read_cache[key] = (mtime, value)
    return value


def _read_cache_evict(path: Path) -> None:
    with _read_cache_lock:
        _read_cache.pop(str(path), None)


def _meeting_to_dict(meeting: Meeting) -> dict:
    data = meeting.model_dump()
    data["created_at"] = meeting.created_at.isoformat()
    data["updated_at"] = meeting.updated_at.isoformat()
    return data


def _dict_to_meeting(data: dict) -> Meeting:
    if "created_at" in data and isinstance(data["created_at"], str):
        dt = datetime.fromisoformat(data["created_at"])
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        data["created_at"] = dt
    if "updated_at" in data and isinstance(data["updated_at"], str):
        dt = datetime.fromisoformat(data["updated_at"])
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        data["updated_at"] = dt
    return Meeting(**data)


def create_meeting(title: str, mode: MeetingMode | None = None) -> Meeting:
    from src.identity import authorize, get_actor

    authorize(get_actor(), "meeting.create", {})
    meeting_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    meeting = Meeting(
        id=meeting_id,
        title=title,
        mode=mode,
        created_at=now,
        updated_at=now,
    )
    meeting_dir = _meeting_dir(meeting_id)
    meeting_dir.mkdir(parents=True, exist_ok=True)
    _write_json(meeting_dir / "meta.json", _meeting_to_dict(meeting))
    logger.info("Created meeting id=%s title='%s' dir=%s", meeting_id, title, meeting_dir)
    return meeting


def get_meeting(meeting_id: str) -> Meeting | None:
    cached = _read_cached(_meeting_dir(meeting_id) / "meta.json", _load_meeting)
    if cached is None:
        return None
    # Fresh instance per call: callers mutate their Meeting freely.
    return cached.model_copy(deep=True)


def _load_meeting(path: Path) -> Meeting | None:
    data = _read_json(path)
    if data is None:
        return None
    return _dict_to_meeting(data)


def list_meetings() -> list[Meeting]:
    if not MEETINGS_DIR.exists():
        return []
    meetings: list[Meeting] = []
    for entry in MEETINGS_DIR.iterdir():
        if not entry.is_dir():
            continue
        try:
            data = _read_json(entry / "meta.json")
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Skipping corrupted meeting %s: %s", entry.name, exc)
            continue
        if data is not None:
            meetings.append(_dict_to_meeting(data))
    # Newest first by creation time (not last activity)
    meetings.sort(key=lambda m: m.created_at or m.updated_at, reverse=True)
    return meetings


def update_meeting(meeting_id: str, **fields) -> Meeting:
    from src.identity import authorize, get_actor

    authorize(get_actor(), "meeting.update", {"meeting_id": meeting_id})
    with _get_lock(meeting_id):
        meeting = get_meeting(meeting_id)
        if meeting is None:
            raise FileNotFoundError(f"Meeting {meeting_id} not found")
        if "hot_words_library_ids" in fields:
            ids = [
                str(x).strip()
                for x in (fields.get("hot_words_library_ids") or [])
                if str(x).strip()
            ]
            fields["hot_words_library_ids"] = ids
            fields["hot_words_library_id"] = ids[0] if ids else None
        elif "hot_words_library_id" in fields:
            one = fields.get("hot_words_library_id") or None
            fields["hot_words_library_id"] = one
            fields["hot_words_library_ids"] = [one] if one else []
        for key, value in fields.items():
            setattr(meeting, key, value)
        meeting.updated_at = datetime.now(timezone.utc)
        _write_json(_meeting_dir(meeting_id) / "meta.json", _meeting_to_dict(meeting))
    return meeting


def delete_meeting(meeting_id: str) -> bool:
    from src.identity import authorize, get_actor

    authorize(get_actor(), "meeting.delete", {"meeting_id": meeting_id})
    directory = _meeting_dir(meeting_id)
    if not directory.exists():
        return False
    try:
        from src.meeting.transcript_index import purge_meeting_transcripts

        purge_meeting_transcripts(meeting_id)
    except Exception:
        logger.warning("transcript index purge skipped for %s", meeting_id, exc_info=True)
    try:
        from src.meeting.group_store import drop_meeting_from_all_groups

        drop_meeting_from_all_groups(meeting_id)
    except Exception:
        logger.warning("group membership drop skipped for %s", meeting_id, exc_info=True)
    shutil.rmtree(directory)
    return True


def save_audio(meeting_id: str, file_bytes: bytes, ext: str, original_filename: str | None = None) -> str:
    meeting = get_meeting(meeting_id)
    if meeting is None:
        raise FileNotFoundError(f"Meeting {meeting_id} not found")
    # Delete old audio file if replacing
    if meeting.audio_path:
        old_path = Path(meeting.audio_path)
        if old_path.exists():
            old_path.unlink()
            logger.info("Deleted old audio: %s for meeting %s", old_path, meeting_id)
    # Use original filename if provided, otherwise fall back to audio.{ext}
    if original_filename and "." in original_filename:
        safe_name = original_filename.replace("/", "_").replace("\\", "_")
        audio_path = _meeting_dir(meeting_id) / safe_name
    else:
        audio_path = _meeting_dir(meeting_id) / f"audio.{ext}"
    audio_path.write_bytes(file_bytes)
    fix_webm_duration(audio_path)
    update_meeting(meeting_id, audio_path=str(audio_path))
    logger.info("Saved audio: %s (%d bytes) for meeting %s", audio_path, len(file_bytes), meeting_id)
    return str(audio_path)


RECORDING_PCM_NAME = "recording.s16le"
RECORDING_WAV_NAME = "recording.wav"
_PCM_RATE = 16_000


def recording_pcm_path(meeting_id: str) -> Path:
    return _meeting_dir(meeting_id) / RECORDING_PCM_NAME


def append_recording_pcm(meeting_id: str, chunk: bytes) -> None:
    """Append 16 kHz s16le PCM during live capture and fsync so a crash keeps audio."""
    if not chunk:
        return
    meeting = get_meeting(meeting_id)
    if meeting is None:
        raise FileNotFoundError(f"Meeting {meeting_id} not found")
    path = recording_pcm_path(meeting_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _get_lock(meeting_id):
        with path.open("ab") as fh:
            fh.write(chunk)
            fh.flush()
            os.fsync(fh.fileno())


def _pcm_s16le_to_wav(pcm: bytes, sample_rate: int = _PCM_RATE) -> bytes:
    n = len(pcm)
    return b"".join(
        (
            b"RIFF",
            struct.pack("<I", 36 + n),
            b"WAVE",
            b"fmt ",
            struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16),
            b"data",
            struct.pack("<I", n),
            pcm,
        )
    )


def finalize_recording_pcm(meeting_id: str) -> str | None:
    """Turn crash-safe PCM into a WAV and set meeting.audio_path. None if empty."""
    meeting = get_meeting(meeting_id)
    if meeting is None:
        raise FileNotFoundError(f"Meeting {meeting_id} not found")
    pcm_path = recording_pcm_path(meeting_id)
    if not pcm_path.is_file() or pcm_path.stat().st_size < 2:
        return None
    pcm = pcm_path.read_bytes()
    wav_path = _meeting_dir(meeting_id) / RECORDING_WAV_NAME
    wav_path.write_bytes(_pcm_s16le_to_wav(pcm))
    update_meeting(meeting_id, audio_path=str(wav_path), mode=MeetingMode.record)
    logger.info(
        "Finalized recording WAV: %s (%d pcm bytes) for meeting %s",
        wav_path,
        len(pcm),
        meeting_id,
    )
    return str(wav_path)


_IMAGE_EXTS = frozenset({"png", "jpg", "jpeg", "gif", "webp", "svg", "bmp"})


def save_note_image(meeting_id: str, filename: str, content: bytes) -> str:
    """Save a notes image under ``meetings/{id}/images/`` and return the stored name."""
    meeting = get_meeting(meeting_id)
    if meeting is None:
        raise FileNotFoundError(f"Meeting {meeting_id} not found")
    ext = filename.rsplit(".", 1)[-1].lower() if "." in (filename or "") else "png"
    if ext not in _IMAGE_EXTS:
        ext = "png"
    safe_name = f"{uuid.uuid4().hex[:10]}.{ext}"
    images_dir = _meeting_dir(meeting_id) / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    from src.paths import confine

    path = confine(images_dir / safe_name, images_dir)
    path.write_bytes(content)
    logger.info("Saved note image: %s (%d bytes) for meeting %s", path, len(content), meeting_id)
    return safe_name


def get_note_image_path(meeting_id: str, filename: str) -> Path:
    from src.paths import confine

    images_dir = _meeting_dir(meeting_id) / "images"
    return confine(images_dir / filename, images_dir)


def save_notes(meeting_id: str, content: str) -> str:
    meeting = get_meeting(meeting_id)
    if meeting is None:
        raise FileNotFoundError(f"Meeting {meeting_id} not found")
    notes_path = _meeting_dir(meeting_id) / "notes.md"
    notes_path.write_text(content, encoding="utf-8")
    _read_cache_evict(notes_path)
    update_meeting(meeting_id, notes_path=str(notes_path))
    logger.info("Saved notes: %s (%d chars) for meeting %s", notes_path, len(content), meeting_id)
    return str(notes_path)


def save_transcript(meeting_id: str, result: TranscriptionResult) -> str:
    meeting = get_meeting(meeting_id)
    if meeting is None:
        raise FileNotFoundError(f"Meeting {meeting_id} not found")
    transcript_path = _meeting_dir(meeting_id) / "transcript.json"
    data = result.model_dump()
    _write_json(transcript_path, data)
    update_meeting(meeting_id, transcript_path=str(transcript_path))
    logger.info("Saved transcript: %s (%d segments, %d chars) for meeting %s", transcript_path, len(result.segments), len(result.text), meeting_id)
    return str(transcript_path)


def get_notes(meeting_id: str) -> str | None:
    meeting = get_meeting(meeting_id)
    if meeting is None or meeting.notes_path is None:
        return None
    notes_path = Path(meeting.notes_path)
    if not notes_path.exists():
        return None
    return notes_path.read_text(encoding="utf-8")


def get_transcript(meeting_id: str) -> TranscriptionResult | None:
    meeting = get_meeting(meeting_id)
    if meeting is None or meeting.transcript_path is None:
        return None
    data = _read_cached(Path(meeting.transcript_path), _load_transcript_dict)
    if data is None:
        return None
    return TranscriptionResult(**data)


def _load_transcript_dict(path: Path) -> dict | None:
    return _read_json(path)


# ── Live summary (in-meeting incremental state) ───────────────────────


def live_summary_path(meeting_id: str) -> Path:
    return _meeting_dir(meeting_id) / "live_summary.json"


def save_live_summary(meeting_id: str, state: dict) -> None:
    """Persist the LiveSummaryState snapshot written after each engine round."""
    meeting = get_meeting(meeting_id)
    if meeting is None:
        raise FileNotFoundError(f"Meeting {meeting_id} not found")
    _write_json(live_summary_path(meeting_id), state)


def get_live_summary(meeting_id: str) -> dict | None:
    """Return the persisted LiveSummaryState dict, or None if never written."""
    data = _read_cached(live_summary_path(meeting_id), _read_json)
    if data is None:
        return None
    import copy

    return copy.deepcopy(data)


# ── Pipeline data (sentences, chunks, section markdown) ───────────────


def sentences_path(meeting_id: str) -> Path:
    return _meeting_dir(meeting_id) / "sentences.json"


def save_sentences(meeting_id: str, sentences: list[dict]) -> str:
    """Persist Sentence array (metadata only, no embedding vectors)."""
    path = sentences_path(meeting_id)
    with _get_lock(meeting_id):
        _write_json(path, {"sentences": sentences})
    return str(path)


def get_sentences(meeting_id: str) -> list[dict] | None:
    """Return raw Sentence dicts or None if not yet computed."""
    cached = _read_cached(sentences_path(meeting_id), _load_sentences)
    if cached is None:
        return None
    # Fresh dicts per call so callers can mutate rows without touching the cache.
    return [dict(s) for s in cached]


def _load_sentences(path: Path) -> list[dict] | None:
    data = _read_json(path)
    if data is None:
        return None
    return data.get("sentences", [])


def apply_section_tags(
    meeting_id: str, tab_id: str, sentence_ids: list[str]
) -> None:
    """Atomically set *tab_id* on the given sentences; drop it from others.

    Parallel section streams must use this instead of load/save of the
    whole sentences.json, or the last writer wipes the others' T-tags.
    """
    wanted = set(sentence_ids)
    with _get_lock(meeting_id):
        data = _read_json(sentences_path(meeting_id))
        if data is None:
            return
        sentences = data.get("sentences") or []
        for s in sentences:
            tags = [t for t in (s.get("section_tags") or []) if t != tab_id]
            if s.get("sentence_id", "") in wanted:
                tags.append(tab_id)
            s["section_tags"] = tags
        _write_json(sentences_path(meeting_id), {"sentences": sentences})


def section_md_path(meeting_id: str, tab_id: str) -> Path:
    return _meeting_dir(meeting_id) / f"{tab_id}.md"


def save_section_md(meeting_id: str, tab_id: str, content: str) -> str:
    """Write a section's generated markdown to its tab file."""
    path = section_md_path(meeting_id, tab_id)
    path.write_text(content, encoding="utf-8")
    _read_cache_evict(path)
    return str(path)


def get_section_md(meeting_id: str, tab_id: str) -> str | None:
    """Read a section's generated markdown or None."""
    path = section_md_path(meeting_id, tab_id)
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return None
    with _read_cache_lock:
        entry = _read_cache.get(str(path))
        if entry is not None and entry[0] == mtime:
            return entry[1]
    content = path.read_text(encoding="utf-8")
    with _read_cache_lock:
        _read_cache[str(path)] = (mtime, content)
    return content


def translation_md_path(meeting_id: str, tab_id: str, lang: str) -> Path:
    """Path of a translated summary: `{tab_id}_{LANG}.md` (never ingested)."""
    return _meeting_dir(meeting_id) / f"{tab_id}_{lang.upper()}.md"


def save_translation_md(meeting_id: str, tab_id: str, lang: str, content: str) -> str:
    """Write a translated summary markdown to its language file."""
    path = translation_md_path(meeting_id, tab_id, lang)
    path.write_text(content, encoding="utf-8")
    _read_cache_evict(path)
    return str(path)


def get_translation_md(meeting_id: str, tab_id: str, lang: str) -> str | None:
    """Read a translated summary markdown or None."""
    path = translation_md_path(meeting_id, tab_id, lang)
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def list_translation_langs(meeting_id: str, tab_id: str) -> list[str]:
    """Return the language codes that already have a translation file."""
    prefix = f"{tab_id}_"
    langs = [
        p.stem[len(prefix):]
        for p in _meeting_dir(meeting_id).glob(f"{prefix}*.md")
        if p.stem[len(prefix):]
    ]
    return sorted(langs)


def delete_pipeline_data(meeting_id: str) -> None:
    """Remove all derived pipeline data (sentences, section mds).

    Called before re-running pipeline after re-transcription.
    Keeps transcript.json and meta.json intact.
    """
    import os as _os

    _meeting_dir(meeting_id)
    for name in ("sentences.json",):
        p = _meeting_dir(meeting_id) / name
        if p.exists():
            p.unlink()
    # Remove section .md files
    meeting = get_meeting(meeting_id)
    if meeting and meeting.tabs:
        for tab in meeting.tabs:
            if isinstance(tab, dict):
                tid = tab.get("tab_id", "")
            else:
                tid = getattr(tab, "tab_id", "")
            if tid:
                md = section_md_path(meeting_id, tid)
                if md.exists():
                    md.unlink()
    try:
        from src.meeting.transcript_index import purge_meeting_transcripts

        purge_meeting_transcripts(meeting_id)
    except Exception:
        logger.warning("transcript index purge skipped for %s", meeting_id, exc_info=True)
    logger.info("Deleted pipeline data for meeting %s", meeting_id)


def discard_recording(meeting_id: str) -> Meeting:
    """Delete all recording-related data and reset the meeting to 'created'.

    Removes audio file, transcript, pipeline data (sentences, section mds),
    summary, notes, and allocated-collection metadata.  The meeting record
    itself is kept so the user can start a new recording in the same slot.
    """
    meeting = get_meeting(meeting_id)
    if meeting is None:
        raise FileNotFoundError(f"Meeting {meeting_id} not found")

    # 1. Delete audio file
    if meeting.audio_path:
        p = Path(meeting.audio_path)
        if p.exists():
            p.unlink()
            logger.info("[DISCARD] Deleted audio %s for meeting %s", p, meeting_id)
    for extra in (RECORDING_PCM_NAME, RECORDING_WAV_NAME):
        p = _meeting_dir(meeting_id) / extra
        if p.exists():
            p.unlink()
            logger.info("[DISCARD] Deleted %s for meeting %s", p, meeting_id)

    # 2. Delete transcript file
    if meeting.transcript_path:
        p = Path(meeting.transcript_path)
        if p.exists():
            p.unlink()
            logger.info("[DISCARD] Deleted transcript %s for meeting %s", p, meeting_id)

    # 2b. Delete live summary artifact from the discarded recording
    ls_path = live_summary_path(meeting_id)
    if ls_path.exists():
        ls_path.unlink()
        logger.info("[DISCARD] Deleted live summary for meeting %s", meeting_id)

    # 3. Delete pipeline data (sentences, section mds)
    try:
        delete_pipeline_data(meeting_id)
    except Exception as exc:
        logger.warning("[DISCARD] Failed to delete pipeline data: %s", exc)

    # 4. Delete notes file
    if meeting.notes_path:
        p = Path(meeting.notes_path)
        if p.exists():
            p.unlink()
            logger.info("[DISCARD] Deleted notes %s for meeting %s", p, meeting_id)

    # 5. Reset meeting fields
    updated = update_meeting(
        meeting_id,
        status=MeetingStatus.created,
        audio_path=None,
        transcript_path=None,
        notes_path=None,
        summary=None,
        detail=None,
        transcription_error=None,
        processing_state=ProcessingState.idle.value,
        summary_gen_state=GenerationState.idle.value,
        blueprint_gen_state=GenerationState.idle.value,
        blueprint=None,
        blueprint_taxonomy=None,
        tabs=None,
        allocated_collections=[],
        allocated_file_ids=[],
        speaker_names=None,
        speaker_people=None,
        speaker_matches=None,
        speaker_slots=None,
        speaker_slots_status=None,
        speaker_slots_ms=None,
        transcript_index_status="",
        transcript_index_error="",
    )
    logger.info("[DISCARD] Meeting %s fully discarded, reset to 'created'", meeting_id)
    return updated
