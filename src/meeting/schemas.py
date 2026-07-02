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


# ── Blueprint & Tab ───────────────────────────────────────────────────


class BlueprintItem(BaseModel):
    """One section definition inferred by the LLM during summarization (v3)."""

    blueprint_id: str  # code-assigned: bp_01, bp_02, ...
    tab_name: str
    tab_description: str  # ~200-char description of this section
    associated_collection_id: str = ""
    associated_collection_name: str = ""


class Tab(BaseModel):
    """Runtime tab representing one section or the General overview (v3)."""

    tab_id: str
    type: str  # "general" | "section"
    blueprint_id: str = ""  # from blueprint item → "bp_01"; custom → ""; cleared on re-summarize
    name: str
    description: str = ""
    processing_state: str = "idle"  # "idle" | "generating"
    associated_collection_id: str = ""
    associated_collection_name: str = ""
    allocated_file_id: str = ""
    is_dirty: bool = False  # set True when user edits name/description; reset on regenerate
    md_file_path: str = ""
    payload_ref: list[str] = Field(default_factory=list)
