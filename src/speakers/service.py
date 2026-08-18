from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

import numpy as np

from src.speakers.models import (
    Enrollment,
    Person,
    SpeakerMatch,
    SpeakerMatchTop,
    SpeakerSlotVector,
)
from src.speakers.store import get_person, update_person

logger = logging.getLogger("speakers.service")

MIN_SEG_SEC = 1.5
OUTLIER_MIN_COS = 0.40
MIN_ENROLL_SEC = 8.0
AUTO_MIN_COS = 0.70
AUTO_MIN_MARGIN = 0.08
PLAY_MIN_SEC = 3.0
RECENT_MAX = 8
CENTROID_WEIGHT_CAP_SEC = 60.0
MAX_ENROLL_SEGMENTS = 24
# Cloud / post-hoc CAM++: enough for a stable slot; cost ~ audio seconds.
SAMPLE_BUDGET_SEC = 60.0
SAMPLE_CLIP_CAP_SEC = 8.0
SAMPLE_MAX_CLIPS = 12

_embedder = None
_embedder_lock = threading.Lock()
_backfill_locks: dict[str, threading.Lock] = {}
_backfill_meta_lock = threading.Lock()


@dataclass
class SlotMatchResult:
    auto: bool
    selected_id: str | None
    score: float | None
    top: list[SpeakerMatchTop]


def _as_vec(values: Sequence[float] | np.ndarray) -> np.ndarray:
    return np.asarray(values, dtype=np.float32).reshape(-1)


def l2_normalize(values: Sequence[float] | np.ndarray) -> np.ndarray:
    vec = _as_vec(values)
    n = float(np.linalg.norm(vec))
    if n < 1e-8:
        return np.zeros_like(vec)
    return vec / n


def cosine(a: Sequence[float] | np.ndarray, b: Sequence[float] | np.ndarray) -> float:
    va = l2_normalize(a)
    vb = l2_normalize(b)
    na = float(np.linalg.norm(va))
    nb = float(np.linalg.norm(vb))
    if na < 1e-8 or nb < 1e-8:
        return 0.0
    return float(np.dot(va, vb))


def build_slot_vector(
    segments: Sequence[tuple[float, float]],
    embeddings: Sequence[Sequence[float] | np.ndarray],
) -> tuple[list[float] | None, float]:
    """Duration-weighted meeting slot embedding from aligned segment embeds."""
    kept: list[tuple[float, np.ndarray]] = []
    for (start, end), raw in zip(segments, embeddings):
        dur = float(end) - float(start)
        if dur < MIN_SEG_SEC:
            continue
        vec = _as_vec(raw)
        if not np.isfinite(vec).all() or float(np.linalg.norm(vec)) < 1e-5:
            continue
        kept.append((dur, l2_normalize(vec)))

    if not kept:
        return None, 0.0

    def _weighted_mean(rows: list[tuple[float, np.ndarray]]) -> np.ndarray:
        total = sum(d for d, _ in rows)
        acc = np.zeros_like(rows[0][1])
        for dur, vec in rows:
            acc = acc + vec * (dur / total)
        return l2_normalize(acc)

    mean = _weighted_mean(kept)
    filtered = [(d, v) for d, v in kept if cosine(v, mean) >= OUTLIER_MIN_COS]
    if not filtered:
        return None, 0.0
    speech_sec = float(sum(d for d, _ in filtered))
    return _weighted_mean(filtered).astype(np.float32).tolist(), speech_sec


def select_embed_windows(
    segments,
    *,
    budget_sec: float = SAMPLE_BUDGET_SEC,
    clip_cap_sec: float = SAMPLE_CLIP_CAP_SEC,
    max_clips: int = SAMPLE_MAX_CLIPS,
    min_seg_sec: float = MIN_SEG_SEC,
) -> dict[str, list[tuple[int, float, float]]]:
    """Pick CAM++ windows per speaker: longest first, cap each clip, stop at budget.

    Returns ``speaker_id -> [(segment_index, crop_start, crop_end), ...]``.
    Speakers with less speech than ``budget_sec`` contribute every qualifying
    segment (still clipped to ``clip_cap_sec``).
    """
    by_spk: dict[str, list[tuple[int, float, float, float]]] = {}
    for i, seg in enumerate(segments):
        spk = getattr(seg, "speaker_id", None) or ""
        if not spk:
            continue
        start = float(seg.start)
        end = float(seg.end)
        dur = end - start
        if dur < min_seg_sec:
            continue
        by_spk.setdefault(spk, []).append((i, start, end, dur))

    out: dict[str, list[tuple[int, float, float]]] = {}
    for spk, rows in by_spk.items():
        rows.sort(key=lambda row: row[3], reverse=True)
        picked: list[tuple[int, float, float]] = []
        acc = 0.0
        for i, start, end, dur in rows:
            if len(picked) >= max_clips or acc >= budget_sec:
                break
            use = min(dur, clip_cap_sec, budget_sec - acc)
            if use < min_seg_sec:
                break
            mid = (start + end) / 2.0
            c0 = mid - use / 2.0
            c1 = mid + use / 2.0
            if c0 < start:
                c1 += start - c0
                c0 = start
            if c1 > end:
                c0 -= c1 - end
                c1 = end
            c0 = max(start, min(c0, end))
            c1 = max(start, min(c1, end))
            if c1 - c0 < min_seg_sec:
                break
            picked.append((i, c0, c1))
            acc += c1 - c0
        if picked:
            out[spk] = picked
    return out


def gate_auto_match(result: SlotMatchResult, speech_sec: float) -> SlotMatchResult:
    """Thin speech can rank in the picker; it cannot auto-assign."""
    if result.auto and speech_sec < MIN_ENROLL_SEC:
        return SlotMatchResult(auto=False, selected_id=None, score=None, top=result.top)
    return result


def _person_match_score(
    meeting_emb: Sequence[float],
    person: Person,
) -> float:
    """max(cos to centroid, cos to any recent meeting slot)."""
    scores = [cosine(meeting_emb, person.centroid)]
    for row in person.recent or []:
        if row.embedding:
            scores.append(cosine(meeting_emb, row.embedding))
    return max(scores)


def match_slot(
    meeting_emb: Sequence[float],
    people: Sequence[Person],
) -> SlotMatchResult:
    scored: list[SpeakerMatchTop] = []
    for person in people:
        if not person.centroid:
            continue
        scored.append(
            SpeakerMatchTop(
                person_id=person.id,
                score=_person_match_score(meeting_emb, person),
            )
        )
    scored.sort(key=lambda row: row.score, reverse=True)
    top = scored[:3]
    if not top:
        return SlotMatchResult(auto=False, selected_id=None, score=None, top=[])
    best = top[0]
    margin_ok = True
    if len(scored) >= 2:
        margin_ok = (best.score - scored[1].score) >= AUTO_MIN_MARGIN
    auto = best.score >= AUTO_MIN_COS and margin_ok
    return SlotMatchResult(
        auto=auto,
        selected_id=best.person_id if auto else None,
        score=best.score if auto else None,
        top=top,
    )


def _recompute_from_recent(person: Person) -> Person:
    if not person.recent:
        person.centroid = []
        person.speech_sec = 0.0
        person.last_meeting_id = None
        person.last_speaker_id = None
        return person
    rows = [
        (max(min(float(r.speech_sec), CENTROID_WEIGHT_CAP_SEC), 1e-6), _as_vec(r.embedding))
        for r in person.recent
        if r.embedding
    ]
    if not rows:
        person.centroid = []
        person.speech_sec = 0.0
        return person
    total = sum(d for d, _ in rows)
    acc = np.zeros_like(rows[0][1])
    for dur, vec in rows:
        acc = acc + l2_normalize(vec) * (dur / total)
    person.centroid = l2_normalize(acc).astype(np.float32).tolist()
    person.speech_sec = float(sum(r.speech_sec for r in person.recent))
    last = person.recent[-1]
    person.last_meeting_id = last.meeting_id
    person.last_speaker_id = last.speaker_id
    return person


def enroll(
    person_id: str,
    meeting_id: str,
    speaker_id: str,
    meeting_emb: Sequence[float],
    speech_sec: float,
) -> Person:
    person = get_person(person_id)
    if person is None:
        raise FileNotFoundError(f"Person {person_id} not found")
    if speech_sec < MIN_ENROLL_SEC and not person.centroid:
        return person

    if speech_sec < MIN_ENROLL_SEC:
        # Already has a centroid: bind happened at caller; skip thin audio.
        return person

    now = datetime.now(timezone.utc).isoformat()
    row = Enrollment(
        meeting_id=meeting_id,
        speaker_id=speaker_id,
        embedding=list(meeting_emb),
        speech_sec=float(speech_sec),
        enrolled_at=now,
    )
    recent = [r for r in person.recent if r.meeting_id != meeting_id]
    recent.append(row)
    if len(recent) > RECENT_MAX:
        recent = recent[-RECENT_MAX:]
    person.recent = recent
    person = _recompute_from_recent(person)
    return update_person(
        person.id,
        centroid=person.centroid,
        recent=person.recent,
        speech_sec=person.speech_sec,
        last_meeting_id=person.last_meeting_id,
        last_speaker_id=person.last_speaker_id,
    )


def unenroll(person_id: str, meeting_id: str) -> Person | None:
    person = get_person(person_id)
    if person is None:
        return None
    new_recent = [r for r in person.recent if r.meeting_id != meeting_id]
    if len(new_recent) == len(person.recent):
        return person
    person.recent = new_recent
    person = _recompute_from_recent(person)
    return update_person(
        person.id,
        centroid=person.centroid,
        recent=person.recent,
        speech_sec=person.speech_sec,
        last_meeting_id=person.last_meeting_id,
        last_speaker_id=person.last_speaker_id,
    )


def rebind(
    from_person_id: str | None,
    to_person_id: str,
    meeting_id: str,
    speaker_id: str,
    meeting_emb: Sequence[float],
    speech_sec: float,
) -> Person:
    if from_person_id and from_person_id != to_person_id:
        unenroll(from_person_id, meeting_id)
    return enroll(to_person_id, meeting_id, speaker_id, meeting_emb, speech_sec)


def _slot_vector_from_segments(
    segments,
    speaker_id: str,
    segment_embeddings: Sequence[Sequence[float] | np.ndarray] | None,
    *,
    sample: bool = False,
) -> tuple[list[float] | None, float]:
    if segment_embeddings is None:
        return None, 0.0
    segs: list[tuple[float, float]] = []
    embs: list[Sequence[float] | np.ndarray] = []
    if sample:
        for idx, c0, c1 in select_embed_windows(segments).get(speaker_id, []):
            if idx >= len(segment_embeddings):
                continue
            segs.append((c0, c1))
            embs.append(segment_embeddings[idx])
    else:
        for i, seg in enumerate(segments):
            if (getattr(seg, "speaker_id", None) or "") != speaker_id:
                continue
            if i >= len(segment_embeddings):
                continue
            segs.append((float(seg.start), float(seg.end)))
            embs.append(segment_embeddings[i])
    if not segs:
        return None, 0.0
    return build_slot_vector(segs, embs)


def _label_map(people: Sequence[Person]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for person in people:
        key = (person.display_name or "").strip().lower()
        counts[key] = counts.get(key, 0) + 1
    return counts


def rebuild_speaker_names(
    speaker_people: dict[str, str] | None,
    people: Sequence[Person] | None = None,
) -> dict[str, str]:
    from src.speakers.store import get_person as _get, list_people, person_label

    people_list = list(people) if people is not None else list_people()
    by_id = {p.id: p for p in people_list}
    counts = _label_map(people_list)
    names: dict[str, str] = {}
    for spk, pid in (speaker_people or {}).items():
        person = by_id.get(pid) or _get(pid)
        if person is None:
            continue
        names[spk] = person_label(person, name_counts=counts)
    return names


_UNSET = object()


def _write_speaker_state(
    meeting_id: str,
    *,
    speaker_people: dict[str, str],
    speaker_matches: dict[str, SpeakerMatch],
    speaker_names: dict[str, str] | None,
    speaker_slots: dict[str, SpeakerSlotVector] | None | object = _UNSET,
    speaker_slots_status: str | None | object = _UNSET,
    speaker_slots_ms: int | None | object = _UNSET,
) -> object:
    from src.meeting.store import update_meeting

    kwargs: dict = {
        "speaker_people": speaker_people or None,
        "speaker_matches": speaker_matches or None,
        "speaker_names": speaker_names or None,
    }
    if speaker_slots is not _UNSET:
        kwargs["speaker_slots"] = speaker_slots or None
    if speaker_slots_status is not _UNSET:
        kwargs["speaker_slots_status"] = speaker_slots_status
    if speaker_slots_ms is not _UNSET:
        kwargs["speaker_slots_ms"] = speaker_slots_ms
    return update_meeting(meeting_id, **kwargs)


def meeting_slot_vector(meeting, speaker_id: str) -> tuple[list[float] | None, float]:
    raw = (getattr(meeting, "speaker_slots", None) or {}).get(speaker_id)
    if raw is None:
        return None, 0.0
    slot = raw if isinstance(raw, SpeakerSlotVector) else SpeakerSlotVector.model_validate(raw)
    if not slot.embedding:
        return None, 0.0
    return list(slot.embedding), float(slot.speech_sec)


def resolve_meeting_audio_path(raw: str | None) -> Path | None:
    """Return an existing audio file, remapping Docker ``/app/data/...`` paths."""
    if not raw:
        return None
    path = Path(raw)
    if path.is_file():
        return path
    from src.config import DATA_DIR

    parts = path.parts
    if "data" in parts:
        idx = parts.index("data")
        candidate = (DATA_DIR / Path(*parts[idx + 1:])).resolve()
        if candidate.is_file():
            return candidate
    return None


def _load_campplus_embedder():
    """Process-wide CAM++ session — do not reload ONNX per speaker."""
    global _embedder
    if _embedder is not None:
        return _embedder
    with _embedder_lock:
        if _embedder is not None:
            return _embedder
        from src.meeting.transcription.onnx.campplus import (
            resolve_campplus_dir,
            try_load_campplus,
        )

        embedder = try_load_campplus(resolve_campplus_dir())
        _embedder = embedder
        return _embedder


def _meeting_backfill_lock(meeting_id: str) -> threading.Lock:
    with _backfill_meta_lock:
        lock = _backfill_locks.get(meeting_id)
        if lock is None:
            lock = threading.Lock()
            _backfill_locks[meeting_id] = lock
        return lock


def ensure_meeting_slots(meeting_id: str) -> dict[str, SpeakerSlotVector]:
    """Backfill every speaker slot for a meeting in one CAM++ pass.

    Old meetings have no ``speaker_slots``. The first assign computes all
    slots and writes them; later assigns only read ``meta.json``.
    """
    from src.meeting.store import get_meeting, get_transcript, update_meeting

    meeting = get_meeting(meeting_id)
    if meeting is None:
        return {}

    def _as_slots(raw) -> dict[str, SpeakerSlotVector]:
        out: dict[str, SpeakerSlotVector] = {}
        for key, val in (raw or {}).items():
            slot = val if isinstance(val, SpeakerSlotVector) else SpeakerSlotVector.model_validate(val)
            if slot.embedding:
                out[str(key)] = slot
        return out

    existing = _as_slots(meeting.speaker_slots)
    transcript = get_transcript(meeting_id)
    needed = {s.speaker_id for s in (transcript.segments if transcript else []) if s.speaker_id}
    if needed and needed.issubset(existing):
        apply_matches_from_slots(meeting_id)
        return existing

    with _meeting_backfill_lock(meeting_id):
        meeting = get_meeting(meeting_id)
        existing = _as_slots(getattr(meeting, "speaker_slots", None))
        transcript = get_transcript(meeting_id)
        needed = {s.speaker_id for s in (transcript.segments if transcript else []) if s.speaker_id}
        if needed and needed.issubset(existing):
            apply_matches_from_slots(meeting_id)
            return existing
        if transcript is None:
            update_meeting(meeting_id, speaker_slots_status="unavailable")
            return existing
        update_meeting(meeting_id, speaker_slots_status="computing")
        t0 = time.perf_counter()
        logger.info(
            "CAM++ slot backfill start meeting=%s speakers=%d segments=%d",
            meeting_id,
            len(needed),
            len(transcript.segments),
        )
        embs = try_embed_meeting_segments(meeting_id)
        if not embs:
            update_meeting(meeting_id, speaker_slots_status="unavailable")
            logger.warning("CAM++ slot backfill unavailable meeting=%s", meeting_id)
            return existing
        slots = dict(existing)
        for spk in needed:
            if spk in slots:
                continue
            vec, speech_sec = _slot_vector_from_segments(
                transcript.segments, spk, embs, sample=True
            )
            if vec is None:
                continue
            slots[spk] = SpeakerSlotVector(embedding=vec, speech_sec=speech_sec)
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        update_meeting(
            meeting_id,
            speaker_slots=slots,
            speaker_slots_status="ready",
            speaker_slots_ms=elapsed_ms,
        )
        logger.info(
            "CAM++ slot backfill done meeting=%s speakers=%d elapsed_ms=%d",
            meeting_id,
            len(slots),
            elapsed_ms,
        )
        apply_matches_from_slots(meeting_id)
        return slots


def apply_matches_from_slots(meeting_id: str) -> bool:
    """Score saved slots against the current People gallery.

    Fast (in-memory cosine). Does not overwrite a user pick, does not run CAM++.
    Returns True if meeting fields changed.
    """
    from src.meeting.store import get_meeting, update_meeting
    from src.speakers.store import list_people

    meeting = get_meeting(meeting_id)
    if meeting is None:
        return False
    people = list_people()
    raw_slots = getattr(meeting, "speaker_slots", None) or {}
    if not raw_slots:
        return False
    people_map = dict(meeting.speaker_people or {})
    matches = dict(meeting.speaker_matches or {})
    for spk, raw in raw_slots.items():
        slot = raw if isinstance(raw, SpeakerSlotVector) else SpeakerSlotVector.model_validate(raw)
        if not slot.embedding:
            continue
        result = gate_auto_match(match_slot(slot.embedding, people), slot.speech_sec)
        prev = matches.get(spk) or SpeakerMatch()
        if not isinstance(prev, SpeakerMatch):
            prev = SpeakerMatch.model_validate(prev)
        assigned = people_map.get(spk)
        if assigned:
            matches[spk] = SpeakerMatch(
                auto=prev.auto,
                score=prev.score,
                enrolled=prev.enrolled,
                top=result.top,
            )
            continue
        if prev.cleared:
            matches[spk] = SpeakerMatch(
                auto=False,
                score=result.score,
                enrolled=False,
                cleared=True,
                top=result.top,
            )
            continue
        matches[spk] = SpeakerMatch(
            auto=result.auto,
            score=result.score,
            enrolled=False,
            top=result.top,
        )
        if result.auto and result.selected_id:
            people_map[spk] = result.selected_id

    def _dump(value) -> dict:
        return value.model_dump() if hasattr(value, "model_dump") else dict(value or {})

    old_people = dict(meeting.speaker_people or {})
    old_matches = {k: _dump(v) for k, v in (meeting.speaker_matches or {}).items()}
    new_matches = {k: _dump(v) for k, v in matches.items()}
    if people_map == old_people and new_matches == old_matches:
        return False
    update_meeting(
        meeting_id,
        speaker_people=people_map or None,
        speaker_matches=matches or None,
        speaker_names=rebuild_speaker_names(people_map, people) or None,
    )
    return True


def _run_embed(embed_fn, chunk: np.ndarray, sample_rate: int) -> np.ndarray:
    try:
        return np.asarray(embed_fn(chunk, sample_rate), dtype=np.float32).reshape(-1)
    except TypeError:
        return np.asarray(embed_fn(chunk), dtype=np.float32).reshape(-1)
    except Exception:
        return np.zeros(192, dtype=np.float32)


def embed_segments(
    segments,
    waveform: np.ndarray,
    sample_rate: int,
    embed_fn,
) -> list[np.ndarray]:
    embs: list[np.ndarray] = []
    wav = np.asarray(waveform, dtype=np.float32).reshape(-1)
    for seg in segments:
        start = max(0, int(float(seg.start) * sample_rate))
        end = min(len(wav), int(float(seg.end) * sample_rate))
        embs.append(_run_embed(embed_fn, wav[start:end], sample_rate))
    return embs


def embed_selected_windows(
    segments,
    waveform: np.ndarray,
    sample_rate: int,
    embed_fn,
) -> list[np.ndarray]:
    """CAM++ only the sampled windows; other indices stay zero (dropped later)."""
    wav = np.asarray(waveform, dtype=np.float32).reshape(-1)
    embs = [np.zeros(192, dtype=np.float32) for _ in segments]
    for picks in select_embed_windows(segments).values():
        for idx, c0, c1 in picks:
            start = max(0, int(c0 * sample_rate))
            end = min(len(wav), int(c1 * sample_rate))
            embs[idx] = _run_embed(embed_fn, wav[start:end], sample_rate)
    return embs


def try_embed_meeting_segments(meeting_id: str) -> list[np.ndarray] | None:
    """Best-effort CAM++ embed of transcript segments from meeting audio."""
    from src.meeting.store import get_meeting, get_transcript

    meeting = get_meeting(meeting_id)
    transcript = get_transcript(meeting_id)
    audio = resolve_meeting_audio_path(getattr(meeting, "audio_path", None) if meeting else None)
    if meeting is None or audio is None or transcript is None:
        return None
    try:
        from src.meeting.transcription.onnx.pipeline import _load_wav_mono16k

        wav, sr = _load_wav_mono16k(str(audio))
        t0 = time.perf_counter()
        embedder = _load_campplus_embedder()
        if embedder is None:
            logger.warning("CAM++ not available — skip embed for %s", meeting_id)
            return None
        n = len(transcript.segments)
        logger.info("CAM++ embed start meeting=%s segments=%d", meeting_id, n)
        embs = embed_selected_windows(transcript.segments, wav, sr, embedder.embed)
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        logger.info(
            "CAM++ embed done meeting=%s segments=%d elapsed_ms=%d",
            meeting_id,
            n,
            elapsed_ms,
        )
        return embs
    except Exception:
        logger.warning("CAM++ post-pass embed failed for %s", meeting_id, exc_info=True)
        return None


def attach_after_transcription(
    meeting_id: str,
    *,
    segment_embeddings: Sequence[Sequence[float] | np.ndarray] | None = None,
    waveform: np.ndarray | None = None,
    sample_rate: int = 16000,
    embed_fn=None,
):
    """Score this meeting's speaker slots against the People gallery."""
    from src.meeting.store import get_meeting, get_transcript
    from src.speakers.store import list_people, person_label

    meeting = get_meeting(meeting_id)
    if meeting is None:
        raise FileNotFoundError(f"Meeting {meeting_id} not found")
    transcript = get_transcript(meeting_id)
    if transcript is None or not transcript.segments:
        return _write_speaker_state(
            meeting_id,
            speaker_people={},
            speaker_matches={},
            speaker_names=None,
        )

    people = list_people()
    counts = _label_map(people)
    speaker_people: dict[str, str] = {}
    speaker_names: dict[str, str] = {}
    speaker_matches: dict[str, SpeakerMatch] = {}
    speaker_slots: dict[str, SpeakerSlotVector] = {}

    slots = {s.speaker_id for s in transcript.segments if s.speaker_id}
    sampled = False
    if segment_embeddings is None and embed_fn is not None and waveform is not None:
        segment_embeddings = embed_selected_windows(
            transcript.segments, waveform, sample_rate, embed_fn
        )
        sampled = True
    if segment_embeddings is None:
        segment_embeddings = try_embed_meeting_segments(meeting_id)
        sampled = True
    if not segment_embeddings:
        return _write_speaker_state(
            meeting_id,
            speaker_people={},
            speaker_matches={},
            speaker_names=None,
            speaker_slots={},
            speaker_slots_status="unavailable",
        )

    for spk in sorted(slots):
        vec, speech_sec = _slot_vector_from_segments(
            transcript.segments, spk, segment_embeddings, sample=sampled
        )
        if vec is None:
            speaker_matches[spk] = SpeakerMatch()
            continue
        speaker_slots[spk] = SpeakerSlotVector(embedding=vec, speech_sec=speech_sec)
        result = gate_auto_match(match_slot(vec, people), speech_sec)
        speaker_matches[spk] = SpeakerMatch(
            auto=result.auto,
            score=result.score,
            enrolled=False,
            top=result.top,
        )
        if result.auto and result.selected_id:
            speaker_people[spk] = result.selected_id
            person = next((p for p in people if p.id == result.selected_id), None)
            if person is not None:
                speaker_names[spk] = person_label(person, name_counts=counts)

    return _write_speaker_state(
        meeting_id,
        speaker_people=speaker_people,
        speaker_matches=speaker_matches,
        speaker_names=speaker_names,
        speaker_slots=speaker_slots,
        speaker_slots_status="ready" if speaker_slots else "unavailable",
        speaker_slots_ms=0 if speaker_slots else None,
    )


def assign_speaker(
    meeting_id: str,
    speaker_id: str,
    person_id: str | None,
    *,
    segment_embeddings: Sequence[Sequence[float] | np.ndarray] | None = None,
    new_person: dict | None = None,
):
    from src.meeting.store import get_meeting, get_transcript
    from src.speakers.store import create_person, get_person, list_people

    meeting = get_meeting(meeting_id)
    if meeting is None:
        raise FileNotFoundError(f"Meeting {meeting_id} not found")
    if new_person:
        created = create_person(
            new_person.get("display_name") or "",
            new_person.get("disambiguator") or "",
        )
        person_id = created.id
    if person_id:
        person = get_person(person_id)
        if person is None:
            raise FileNotFoundError(f"Person {person_id} not found")

    transcript = get_transcript(meeting_id)
    segments = transcript.segments if transcript else []
    if segment_embeddings is not None:
        vec, speech_sec = _slot_vector_from_segments(
            segments, speaker_id, segment_embeddings
        )
    else:
        vec, speech_sec = meeting_slot_vector(meeting, speaker_id)

    people_map = dict(meeting.speaker_people or {})
    names = dict(meeting.speaker_names or {})
    matches = dict(meeting.speaker_matches or {})
    prev = people_map.get(speaker_id)
    need_backfill = False

    if person_id is None:
        if prev:
            unenroll(prev, meeting_id)
        people_map.pop(speaker_id, None)
        names.pop(speaker_id, None)
        prev_match = matches.get(speaker_id) or SpeakerMatch()
        if not isinstance(prev_match, SpeakerMatch):
            prev_match = SpeakerMatch.model_validate(prev_match)
        matches[speaker_id] = SpeakerMatch(
            auto=False,
            score=None,
            enrolled=False,
            cleared=True,
            top=list(prev_match.top or []),
        )
    else:
        if vec is not None:
            rebind(prev, person_id, meeting_id, speaker_id, vec, speech_sec)
        elif prev and prev != person_id:
            unenroll(prev, meeting_id)
        people_map[speaker_id] = person_id
        prev_match = matches.get(speaker_id) or SpeakerMatch()
        matches[speaker_id] = SpeakerMatch(
            auto=False,
            score=prev_match.score,
            enrolled=vec is not None,
            cleared=False,
            top=list(prev_match.top or []),
        )
        bound = get_person(person_id)
        if bound is not None:
            from src.speakers.store import update_person

            update_person(
                person_id,
                last_meeting_id=meeting_id,
                last_speaker_id=speaker_id,
            )
        if vec is None:
            need_backfill = True

    people = list_people()
    names = rebuild_speaker_names(people_map, people)
    updated = _write_speaker_state(
        meeting_id,
        speaker_people=people_map,
        speaker_matches=matches,
        speaker_names=names,
    )
    if need_backfill:
        from src.meeting.store import update_meeting

        update_meeting(meeting_id, speaker_slots_status="computing")
        _schedule_enroll_from_audio(meeting_id, speaker_id, person_id)
        from src.meeting.store import get_meeting as _get

        return _get(meeting_id) or updated
    return updated


def commit_pending(
    meeting_id: str,
    *,
    segment_embeddings: Sequence[Sequence[float] | np.ndarray] | None = None,
):
    from src.meeting.store import get_meeting, get_transcript

    meeting = get_meeting(meeting_id)
    if meeting is None:
        raise FileNotFoundError(f"Meeting {meeting_id} not found")
    transcript = get_transcript(meeting_id)
    segments = transcript.segments if transcript else []
    people_map = dict(meeting.speaker_people or {})
    matches = dict(meeting.speaker_matches or {})
    slots = dict(meeting.speaker_slots or {})

    for spk, pid in people_map.items():
        current = matches.get(spk) or SpeakerMatch()
        if current.enrolled:
            continue
        if segment_embeddings is not None:
            vec, speech_sec = _slot_vector_from_segments(segments, spk, segment_embeddings)
        else:
            vec, speech_sec = meeting_slot_vector(meeting, spk)
        if vec is None and segment_embeddings is None:
            embs = try_embed_meeting_segments(meeting_id)
            vec, speech_sec = _slot_vector_from_segments(segments, spk, embs)
            if vec is not None:
                slots[spk] = SpeakerSlotVector(embedding=vec, speech_sec=speech_sec)
        if vec is None:
            continue
        enroll(pid, meeting_id, spk, vec, speech_sec)
        matches[spk] = SpeakerMatch(
            auto=current.auto,
            score=current.score,
            enrolled=True,
            top=list(current.top or []),
        )

    names = rebuild_speaker_names(people_map)
    return _write_speaker_state(
        meeting_id,
        speaker_people=people_map,
        speaker_matches=matches,
        speaker_names=names,
        speaker_slots=slots,
    )


def _schedule_enroll_from_audio(meeting_id: str, speaker_id: str, person_id: str) -> None:
    thread = threading.Thread(
        target=_enroll_slot_from_audio_safe,
        args=(meeting_id, speaker_id, person_id),
        name=f"spk-enroll-{meeting_id[:8]}",
        daemon=True,
    )
    thread.start()


def _enroll_slot_from_audio_safe(meeting_id: str, speaker_id: str, person_id: str) -> None:
    try:
        enroll_slot_from_audio(meeting_id, speaker_id, person_id)
    except Exception:
        logger.warning(
            "Background voiceprint enroll failed meeting=%s speaker=%s person=%s",
            meeting_id,
            speaker_id,
            person_id,
            exc_info=True,
        )


def enroll_slot_from_audio(meeting_id: str, speaker_id: str, person_id: str) -> bool:
    """Enroll from saved slots; one CAM++ backfill per meeting if missing."""
    from src.meeting.store import get_meeting, update_meeting

    slots = ensure_meeting_slots(meeting_id)
    slot = slots.get(speaker_id)
    if slot is None or not slot.embedding:
        return False
    vec, speech_sec = list(slot.embedding), float(slot.speech_sec)
    enroll(person_id, meeting_id, speaker_id, vec, speech_sec)
    meeting = get_meeting(meeting_id)
    if meeting is None:
        return True
    people_map = dict(meeting.speaker_people or {})
    if people_map.get(speaker_id) != person_id:
        return True
    matches = dict(meeting.speaker_matches or {})
    current = matches.get(speaker_id) or SpeakerMatch()
    matches[speaker_id] = SpeakerMatch(
        auto=current.auto,
        score=current.score,
        enrolled=True,
        top=list(current.top or []),
    )
    update_meeting(meeting_id, speaker_matches=matches)
    return True


def pick_preview(
    person_id: str,
    *,
    exclude_meeting_id: str | None = None,
    exclude_start: float | None = None,
) -> dict | None:
    from src.meeting.store import get_meeting, get_transcript
    from src.speakers.store import get_person

    person = get_person(person_id)
    if person is None:
        return None
    rows = list(reversed(person.recent))
    if person.last_meeting_id and not any(r.meeting_id == person.last_meeting_id for r in rows):
        rows.insert(
            0,
            Enrollment(
                meeting_id=person.last_meeting_id,
                speaker_id=person.last_speaker_id or "",
            ),
        )
    import random

    pool: list[dict] = []
    for row in rows:
        meeting = get_meeting(row.meeting_id)
        if meeting is None:
            continue
        audio = resolve_meeting_audio_path(meeting.audio_path)
        if audio is None:
            continue
        transcript = get_transcript(row.meeting_id)
        if transcript is None:
            continue
        speaker_id = row.speaker_id
        if not speaker_id and meeting.speaker_people:
            for spk, pid in meeting.speaker_people.items():
                if pid == person_id:
                    speaker_id = spk
                    break
        for seg in transcript.segments:
            if seg.speaker_id != speaker_id:
                continue
            if (float(seg.end) - float(seg.start)) < PLAY_MIN_SEC:
                continue
            pool.append(
                {
                    "meeting_id": row.meeting_id,
                    "speaker_id": speaker_id,
                    "start": float(seg.start),
                    "end": float(seg.end),
                    "audio_path": str(audio),
                }
            )
    if not pool:
        return None
    if exclude_meeting_id is not None and exclude_start is not None and len(pool) > 1:
        filtered = [
            c
            for c in pool
            if not (
                c["meeting_id"] == exclude_meeting_id
                and abs(float(c["start"]) - float(exclude_start)) < 0.05
            )
        ]
        if filtered:
            pool = filtered
    return random.choice(pool)


def person_meeting_rows(person) -> list[dict]:
    """Meetings this person has been bound to (recent + last seen)."""
    from src.meeting.store import get_meeting

    rows: list[dict] = []
    seen: set[str] = set()
    ordered = list(reversed(person.recent))
    if person.last_meeting_id and person.last_meeting_id not in {
        r.meeting_id for r in person.recent
    }:
        ordered.insert(
            0,
            Enrollment(
                meeting_id=person.last_meeting_id,
                speaker_id=person.last_speaker_id or "",
            ),
        )
    for row in ordered:
        if row.meeting_id in seen:
            continue
        seen.add(row.meeting_id)
        meeting = get_meeting(row.meeting_id)
        rows.append(
            {
                "meeting_id": row.meeting_id,
                "title": (meeting.title if meeting else "") or row.meeting_id[:8],
                "speaker_id": row.speaker_id,
                "speech_sec": row.speech_sec,
                "enrolled_at": row.enrolled_at,
            }
        )
    return rows


def resolve_assignee_person_id(
    *,
    meeting_id: str | None = None,
    candidate_id: str | None = None,
    assignee_label: str | None = None,
) -> str | None:
    """Map a meeting todo assignee (name / candidate) onto a People id."""
    from src.meeting.store import get_meeting
    from src.speakers.store import list_people, person_label

    label = (assignee_label or "").strip()
    meeting = get_meeting(meeting_id) if meeting_id else None
    if meeting and candidate_id and not label:
        for tab in meeting.tabs or []:
            if not isinstance(tab, dict):
                continue
            for cand in tab.get("todo_candidates") or []:
                if not isinstance(cand, dict):
                    continue
                if str(cand.get("candidate_id") or "") == str(candidate_id):
                    label = str(cand.get("assignee_label") or "").strip()
                    break
            if label:
                break
    if not label:
        return None

    key = label.casefold()
    if meeting:
        names = meeting.speaker_names or {}
        people_map = meeting.speaker_people or {}
        for spk, name in names.items():
            if str(name or "").strip().casefold() == key:
                pid = people_map.get(str(spk))
                if pid:
                    return str(pid)
        for spk, pid in people_map.items():
            if str(spk).casefold() == key:
                return str(pid)

    people = list_people()
    counts: dict[str, int] = {}
    for person in people:
        n = (person.display_name or "").strip().lower()
        counts[n] = counts.get(n, 0) + 1
    for person in people:
        if (person.display_name or "").strip().casefold() == key:
            return person.id
        if person_label(person, name_counts=counts).strip().casefold() == key:
            return person.id
    return None


def person_detail_dict(person) -> dict:
    from src.speakers.store import person_public_dict

    data = person_public_dict(person)
    data["meetings"] = person_meeting_rows(person)
    data["meeting_count"] = len(data["meetings"])
    return data
