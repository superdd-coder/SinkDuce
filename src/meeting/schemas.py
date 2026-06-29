"""Meeting v2 pipeline data models.

Sentence, Chunk, Blueprint, Tab, and MeetingState schemas for the
"long-text meeting dynamic decomposition & interactive extraction" pipeline.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


# ── Atomic units ──────────────────────────────────────────────────────


class Sentence(BaseModel):
    """Minimum atomic physical utterance unit (single source of truth).

    Derived from TranscriptSegment after STT completes.  ``embedding`` is
    reserved for future use (currently not populated).
    """

    sentence_id: str
    speaker: str  # speaker_id from STT, mapped to display name at UI layer
    start_time: float
    end_time: float
    original_text: str
    section_tags: list[str] = Field(default_factory=list)
    embedding: list[float] | None = None  # reserved, not populated yet


class Chunk(BaseModel):
    """LLM-ingestion-sized physical block referencing sentences by id.

    No text copies — only sentence ID references.
    """

    chunk_id: str
    sentence_refs: list[str] = Field(default_factory=list)
    char_count: int = 0


# ── Blueprint & Tab ───────────────────────────────────────────────────


class BlueprintItem(BaseModel):
    """One section definition inferred by the LLM during summarization."""

    tab_id: str  # code-assigned: tab_sec_01, tab_sec_02, ...
    tab_name: str
    associated_collection_id: str = ""
    associated_collection_name: str = ""
    section_description: str  # ~100-char summary of this section


class Tab(BaseModel):
    """Runtime tab representing one section or the General overview."""

    tab_id: str
    type: str  # "general" | "section"
    name: str
    associated_collection_id: str = ""
    associated_collection_name: str = ""
    md_file_path: str = ""
    payload_ref: list[str] = Field(default_factory=list)
