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


class LiveSummaryEntry(BaseModel):
    id: str  # globally unique, monotonic ("e1", "e2", ...); survives compaction
    kind: str  # point | decision | question | action
    text: str
    speaker: str | None = None
    t: float = 0.0  # meeting-time seconds when the entry was created
    status: str = "active"  # active | resolved


class LiveSummaryTopic(BaseModel):
    text: str
    since: float = 0.0
    closed: bool = False


class LiveSummaryState(BaseModel):
    """Server-owned state of the in-meeting live summary.

    Persisted to ``live_summary.json`` and pushed to the client as the
    WS snapshot. The LLM never sees or writes this whole object — it only
    returns validated ops against it.
    """

    entries: list[LiveSummaryEntry] = Field(default_factory=list)
    topic: LiveSummaryTopic | None = None
    compacted_upto: str = ""  # watermark: entries up to this id were tidied
    tail_from_t: float = 0.0  # transcript seconds covered by the last round
    round: int = 0
    engine: str = "idle"  # idle | running
    updated_at: str = ""


class MeetingGroupMember(BaseModel):
    meeting_id: str
    n: int


class MeetingGroup(BaseModel):
    id: str = ""
    title: str = ""
    members: list[MeetingGroupMember] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_chat_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MeetingBrief(BaseModel):
    """Pre-meeting brief artifact, persisted on the meeting itself."""

    state: str = "idle"  # idle | generating | ready | error
    markdown: str = ""
    error: str | None = None
    generated_at: str = ""
    group_id: str | None = None
    group_title: str = ""
    person_ids: list[str] = Field(default_factory=list)
    locale: str = "zh-CN"


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
    expected_people: list[str] = Field(default_factory=list)  # pre-selected attendees
    brief: MeetingBrief | None = None
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
