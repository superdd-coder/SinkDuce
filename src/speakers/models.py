from __future__ import annotations

from pydantic import BaseModel, Field


class Enrollment(BaseModel):
    meeting_id: str
    speaker_id: str
    embedding: list[float] = Field(default_factory=list)
    speech_sec: float = 0.0
    enrolled_at: str = ""


class SpeakerMatchTop(BaseModel):
    person_id: str
    score: float


class SpeakerMatch(BaseModel):
    auto: bool = False
    score: float | None = None
    enrolled: bool = False
    cleared: bool = False
    top: list[SpeakerMatchTop] = Field(default_factory=list)


class SpeakerSlotVector(BaseModel):
    """This-meeting embedding for one speaker_id (computed at transcription)."""

    embedding: list[float] = Field(default_factory=list)
    speech_sec: float = 0.0


class Person(BaseModel):
    id: str = ""
    display_name: str = ""
    disambiguator: str = ""
    centroid: list[float] = Field(default_factory=list)
    recent: list[Enrollment] = Field(default_factory=list)
    speech_sec: float = 0.0
    last_meeting_id: str | None = None
    last_speaker_id: str | None = None
    created_at: str = ""
    updated_at: str = ""
