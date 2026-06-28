"""Agentic RAG global state dataclass — flows through pipeline nodes."""

from __future__ import annotations

from dataclasses import dataclass, field

from src.rag.retriever import RetrievedChunk


@dataclass
class AgentState:
    """Global state flowing through pipeline nodes.

    Mutated in-place by node functions during the variant-fetch pipeline.
    """

    # ── Immutable inputs ────────────────────────────────────────────
    original_query: str = ""
    collections: list[str] = field(default_factory=list)

    # ── Chunk tracking ──────────────────────────────────────────────
    all_chunks: list[RetrievedChunk] = field(default_factory=list)
    retained_chunks: list[RetrievedChunk] = field(default_factory=list)  # "golden context"
    seen_chunk_ids: set[str] = field(default_factory=set)  # Qdrant point IDs

    # ── LLM grading output ──────────────────────────────────────────
    current_gap_analysis: str = ""   # what's still missing (empty = complete)
    retained_info: str = ""          # LLM-maintained running summary of confirmed information

    # ── Parameters (carried for node access) ────────────────────────
    top_k: int = 5
    rerank_top_k: int = 5
