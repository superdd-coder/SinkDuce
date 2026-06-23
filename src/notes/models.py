"""Pydantic models for the Notes feature."""

from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field


class Note(BaseModel):
    """A standalone note belonging to a collection."""
    id: str = ""
    title: str = ""
    collection: str = ""
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class NoteListItem(BaseModel):
    """List item returned by the notes list endpoint, including metadata
    about whether this note has been distilled into other notes."""
    id: str
    title: str
    collection: str
    created_at: str
    updated_at: str
    is_extracted: bool = False
    extracted_into: list[str] = Field(default_factory=list)
    is_ingested: bool = False


class InjectionBlock(BaseModel):
    """A single injection block inside a note's content."""
    block_id: str
    source_note_id: str
    source_title: str = ""


class PropagationLink(BaseModel):
    """One link in a propagation chain: source → target."""
    source_id: str
    source_title: str
    target_id: str
    target_title: str


class PropagationChain(BaseModel):
    """Full propagation chain preview shown to the user."""
    origin_id: str
    origin_title: str
    links: list[PropagationLink] = Field(default_factory=list)
    total_affected: int = 0
