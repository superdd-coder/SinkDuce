"""Meeting v2 pipeline — initialization, chunking, tagging, summarization.

Pure functions operating on Sentence/Chunk data.  No external I/O
(that stays in store.py and service.py).
"""

from __future__ import annotations

import logging

from src.meeting.models import TranscriptSegment
from src.meeting.schemas import Chunk, Sentence

logger = logging.getLogger(__name__)

# ── Node 0.0: TranscriptSegment → Sentence ──────────────────────────


def normalize_sentences(
    meeting_id: str,
    segments: list[TranscriptSegment],
) -> list[Sentence]:
    """Convert STT TranscriptSegments into internal Sentence array.

    Node 0.0 — pure mapping, no external data access.
    """
    sentences: list[Sentence] = []
    for idx, seg in enumerate(segments):
        sentences.append(
            Sentence(
                sentence_id=_make_sentence_id(meeting_id, idx),
                speaker=seg.speaker_id or "0",
                start_time=seg.start,
                end_time=seg.end,
                original_text=seg.text,
                section_tags=[],
                embedding=None,
            )
        )
    logger.info(
        "[Node 0.0] Normalized %d segments → sentences for meeting %s",
        len(sentences),
        meeting_id,
    )
    return sentences


def _make_sentence_id(meeting_id: str, idx: int) -> str:
    return f"{meeting_id[:8]}_stt_{idx:04d}"


# ── Node 0.1: Logical Chunking ───────────────────────────────────────

# ── Token parameters ──
_SAME_SPEAKER_GAP_SEC = 3.0
_CHUNK_CHAR_LIMIT = 600


def _make_segments(sentences: list[Sentence]) -> list[list[int]]:
    """Group consecutive indices where speaker is same and gap < 3s.

    Returns list of index groups (segments).  A segment is an atomic
    unit that is never split across chunks.
    """
    if not sentences:
        return []

    segments: list[list[int]] = []
    current: list[int] = [0]

    for i in range(1, len(sentences)):
        prev, cur = sentences[i - 1], sentences[i]
        gap = cur.start_time - prev.end_time
        if prev.speaker == cur.speaker and gap < _SAME_SPEAKER_GAP_SEC:
            current.append(i)
        else:
            segments.append(current)
            current = [i]
    segments.append(current)
    return segments


def chunk_sentences(meeting_id: str, sentences: list[Sentence]) -> list[Chunk]:
    """Build Chunks from Sentences using segment-level aggregation.

    Node 0.1 — pure code, no LLM calls.

    Algorithm:
    1. Group consecutive same-speaker sentences (gap < 3s) into segments.
    2. Iterate segments: add all sentence_ids of current segment to
       current chunk.  Once char count >= 600, seal chunk and start new.
    3. If a single segment exceeds 600 chars it occupies its own chunk.
    """
    segments = _make_segments(sentences)
    chunks: list[Chunk] = []
    chunk_idx = 0
    current_ids: list[str] = []
    current_chars = 0

    for seg in segments:
        seg_ids = [sentences[i].sentence_id for i in seg]
        seg_chars = sum(len(sentences[i].original_text) for i in seg)

        current_ids.extend(seg_ids)
        current_chars += seg_chars

        if current_chars >= _CHUNK_CHAR_LIMIT:
            chunks.append(
                Chunk(
                    chunk_id=f"{meeting_id[:8]}_chunk_{chunk_idx:03d}",
                    sentence_refs=current_ids,
                    char_count=current_chars,
                )
            )
            chunk_idx += 1
            current_ids = []
            current_chars = 0

    # Flush remaining
    if current_ids:
        chunks.append(
            Chunk(
                chunk_id=f"{meeting_id[:8]}_chunk_{chunk_idx:03d}",
                sentence_refs=current_ids,
                char_count=current_chars,
            )
        )

    logger.info(
        "[Node 0.1] %d sentences → %d chunks for meeting %s",
        len(sentences),
        len(chunks),
        meeting_id,
    )
    return chunks


# ── Node 1.2: Payload construction ───────────────────────────────────


def build_payload(
    tagged_ids: set[str],
    sentences: list[Sentence],
    radius: int = 3,
) -> list[str]:
    """Build a section payload from tagged sentence IDs plus context.

    For each tagged sentence, includes ±radius neighbour sentences
    (by time order, not ID order).  Deduplicated, returns sorted
    by original sentence order.
    """
    if not tagged_ids:
        return []

    # Build index map: sentence_id → positional index
    id_to_idx: dict[str, int] = {s.sentence_id: i for i, s in enumerate(sentences)}

    included: set[int] = set()
    for sid in tagged_ids:
        idx = id_to_idx.get(sid)
        if idx is None:
            continue
        lo = max(0, idx - radius)
        hi = min(len(sentences), idx + radius + 1)
        included.update(range(lo, hi))

    return [sentences[i].sentence_id for i in sorted(included)]
