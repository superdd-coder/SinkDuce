from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field, model_validator

from src.speakers.models import SpeakerMatch, SpeakerSlotVector


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
    extracting = "extracting"


class GenerationState(str, Enum):
    """Per-tab LLM generation lifecycle.

    idle       — no generation running
    prefilling — LLM is processing input (prefill phase), no token output yet
    streaming  — LLM is emitting tokens (post-prefill decode phase)
    """
    idle = "idle"
    prefilling = "prefilling"
    streaming = "streaming"


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
    summary_gen_state: str = GenerationState.idle.value
    blueprint_gen_state: str = GenerationState.idle.value
    blueprint: list[dict] | None = None
    blueprint_taxonomy: dict | None = None
    tabs: list[dict] | None = None
    allocated_collections: list[str] = Field(default_factory=list)
    allocated_file_ids: list[str] = Field(default_factory=list)
    speaker_names: dict[str, str] | None = None
    speaker_people: dict[str, str] | None = None
    speaker_matches: dict[str, SpeakerMatch] | None = None
    speaker_slots: dict[str, SpeakerSlotVector] | None = None
    speaker_slots_status: str | None = None  # computing | ready | unavailable
    speaker_slots_ms: int | None = None
    hot_words_library_id: str | None = None
    hot_words_library_ids: list[str] = Field(default_factory=list)
    transcript_index_status: str = ""  # "" | building | ready | failed
    transcript_index_error: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="after")
    def _compat_hot_words(self):
        if self.hot_words_library_ids:
            if not self.hot_words_library_id:
                self.hot_words_library_id = self.hot_words_library_ids[0]
        elif self.hot_words_library_id:
            self.hot_words_library_ids = [self.hot_words_library_id]
        return self
