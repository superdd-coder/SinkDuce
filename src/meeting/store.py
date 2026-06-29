from __future__ import annotations

import json
import logging
import shutil
import uuid
from datetime import datetime
from pathlib import Path

from .models import Meeting, MeetingMode, TranscriptionResult
from .webm_fixer import fix_webm_duration

logger = logging.getLogger("meeting.store")
MEETINGS_DIR = Path("data").resolve() / "meetings"


def _meeting_dir(meeting_id: str) -> Path:
    return MEETINGS_DIR / meeting_id


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _meeting_to_dict(meeting: Meeting) -> dict:
    data = meeting.model_dump()
    data["created_at"] = meeting.created_at.isoformat()
    data["updated_at"] = meeting.updated_at.isoformat()
    return data


def _dict_to_meeting(data: dict) -> Meeting:
    if "created_at" in data and isinstance(data["created_at"], str):
        data["created_at"] = datetime.fromisoformat(data["created_at"])
    if "updated_at" in data and isinstance(data["updated_at"], str):
        data["updated_at"] = datetime.fromisoformat(data["updated_at"])
    return Meeting(**data)


def create_meeting(title: str, mode: MeetingMode | None = None) -> Meeting:
    meeting_id = uuid.uuid4().hex
    now = datetime.now()
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
    data = _read_json(_meeting_dir(meeting_id) / "meta.json")
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
        data = _read_json(entry / "meta.json")
        if data is not None:
            meetings.append(_dict_to_meeting(data))
    meetings.sort(key=lambda m: m.updated_at, reverse=True)
    return meetings


def update_meeting(meeting_id: str, **fields) -> Meeting:
    meeting = get_meeting(meeting_id)
    if meeting is None:
        raise FileNotFoundError(f"Meeting {meeting_id} not found")
    for key, value in fields.items():
        setattr(meeting, key, value)
    meeting.updated_at = datetime.now()
    _write_json(_meeting_dir(meeting_id) / "meta.json", _meeting_to_dict(meeting))
    return meeting


def delete_meeting(meeting_id: str) -> bool:
    directory = _meeting_dir(meeting_id)
    if not directory.exists():
        return False
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


def save_notes(meeting_id: str, content: str) -> str:
    meeting = get_meeting(meeting_id)
    if meeting is None:
        raise FileNotFoundError(f"Meeting {meeting_id} not found")
    notes_path = _meeting_dir(meeting_id) / "notes.md"
    notes_path.write_text(content, encoding="utf-8")
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
    data = _read_json(Path(meeting.transcript_path))
    if data is None:
        return None
    return TranscriptionResult(**data)


# ── Pipeline data (sentences, chunks, section markdown) ───────────────


def sentences_path(meeting_id: str) -> Path:
    return _meeting_dir(meeting_id) / "sentences.json"


def chunks_path(meeting_id: str) -> Path:
    return _meeting_dir(meeting_id) / "chunks.json"


def save_sentences(meeting_id: str, sentences: list[dict]) -> str:
    """Persist Sentence array (metadata only, no embedding vectors)."""
    path = sentences_path(meeting_id)
    _write_json(path, {"sentences": sentences})
    return str(path)


def get_sentences(meeting_id: str) -> list[dict] | None:
    """Return raw Sentence dicts or None if not yet computed."""
    data = _read_json(sentences_path(meeting_id))
    if data is None:
        return None
    return data.get("sentences", [])


def save_chunks(meeting_id: str, chunks: list[dict]) -> str:
    """Persist Chunk array."""
    path = chunks_path(meeting_id)
    _write_json(path, {"chunks": chunks})
    return str(path)


def get_chunks(meeting_id: str) -> list[dict] | None:
    """Return raw Chunk dicts or None if not yet computed."""
    data = _read_json(chunks_path(meeting_id))
    if data is None:
        return None
    return data.get("chunks", [])


def section_md_path(meeting_id: str, tab_id: str) -> Path:
    return _meeting_dir(meeting_id) / f"{tab_id}.md"


def save_section_md(meeting_id: str, tab_id: str, content: str) -> str:
    """Write a section's generated markdown to its tab file."""
    path = section_md_path(meeting_id, tab_id)
    path.write_text(content, encoding="utf-8")
    return str(path)


def get_section_md(meeting_id: str, tab_id: str) -> str | None:
    """Read a section's generated markdown or None."""
    path = section_md_path(meeting_id, tab_id)
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def delete_pipeline_data(meeting_id: str) -> None:
    """Remove all derived pipeline data (sentences, chunks, section mds).

    Called before re-running pipeline after re-transcription.
    Keeps transcript.json and meta.json intact.
    """
    import os as _os

    _meeting_dir(meeting_id)
    for name in ("sentences.json", "chunks.json"):
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
    logger.info("Deleted pipeline data for meeting %s", meeting_id)
