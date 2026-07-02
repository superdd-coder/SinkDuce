"""Meeting v2 pipeline — initialization, chunking, tagging, summarization.

Pure functions operating on Sentence/Chunk data.  No external I/O
(that stays in store.py and service.py).
"""

from __future__ import annotations

import logging

from src.meeting.models import TranscriptSegment
from src.meeting.schemas import Sentence

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


# ── Node 1.2: Payload construction ───────────────────────────────────


def build_payload(
    tagged_ids: set[str],
    sentences: list[Sentence],
    radius: int = 3,
    gap_threshold: float | None = None,
) -> list[str]:
    """Build a section payload from tagged sentence IDs plus context.

    For each tagged sentence, includes ±radius neighbour sentences
    (by time order, not ID order).  Deduplicated, returns sorted
    by original sentence order.

    If *gap_threshold* is set (in seconds), neighbour expansion stops
    when the time gap between two consecutive sentences exceeds it.
    Tagged sentences themselves are always included regardless of gaps.
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
        # Tagged sentence always included
        included.add(idx)

        if gap_threshold is not None:
            # Left expansion with gap check
            for i in range(idx - 1, max(0, idx - radius) - 1, -1):
                gap = sentences[i + 1].start_time - sentences[i].end_time
                if gap > gap_threshold:
                    break
                included.add(i)
            # Right expansion with gap check
            for i in range(idx + 1, min(len(sentences), idx + radius + 1)):
                gap = sentences[i].start_time - sentences[i - 1].end_time
                if gap > gap_threshold:
                    break
                included.add(i)
        else:
            lo = max(0, idx - radius)
            hi = min(len(sentences), idx + radius + 1)
            included.update(range(lo, hi))

    return [sentences[i].sentence_id for i in sorted(included)]
