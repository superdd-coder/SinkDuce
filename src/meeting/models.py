from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class MeetingStatus(str, Enum):
    created = "created"
    recording = "recording"
    transcribing = "transcribing"
    completed = "completed"


class MeetingMode(str, Enum):
    upload = "upload"
    record = "record"


class ProcessingState(str, Enum):
    idle = "idle"
    summarizing = "summarizing"
    breaking_down = "breaking_down"
    extracting = "extracting"


class TranscriptSegment(BaseModel):
    start: float  # seconds
    end: float
    text: str
    speaker_id: str | None = None


class TranscriptionResult(BaseModel):
    text: str
    segments: list[TranscriptSegment] = []
    language: str | None = None


class Meeting(BaseModel):
    id: str = ""
    title: str = ""
    status: MeetingStatus = MeetingStatus.created
    mode: MeetingMode | None = None
    audio_path: str | None = None
    notes_path: str | None = None
    transcript_path: str | None = None
    detail: str | None = None
    summary: str | None = None
    transcription_error: str | None = None
    processing_state: str = ProcessingState.idle.value
    blueprint: list[dict] | None = None
    tabs: list[dict] | None = None
    allocated_collections: list[str] = Field(default_factory=list)
    allocated_file_ids: list[str] = Field(default_factory=list)
    speaker_names: dict[str, str] | None = None
    hot_words_library_id: str | None = None
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
