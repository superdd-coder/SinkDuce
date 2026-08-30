"""Person profile domain: meeting-level observation cards + aggregated short profiles.

Two-layer distillation:
  1. Observation card per MEETING (batch: every speaker slot in one LLM call),
     cached by the transcript-content hash and shared by all persons bound in
     that meeting — concurrent generation of several persons never extracts
     the same meeting twice (per-meeting lock).
  2. Profile = one aggregation call over the person's RECENT meetings
     ( newest-first window, ``RECENT_MEETING_CAP`` ).

Dirty state is DERIVED, not event-driven: a profile stores the fingerprint of
the (binding × transcript) input window it was built from; ``is_dirty()``
simply recomputes the current window fingerprint and compares. Binding
add/remove/rebind, meeting deletion, transcript edits, and the window sliding
(new meeting pushes the oldest out) all surface as a fingerprint mismatch.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from datetime import datetime, timezone

from src.speakers.models import MeetingObservations, PersonProfile

logger = logging.getLogger("speakers.profile")

RECENT_MEETING_CAP = 5
_OBS_CLIP_PER_SPEAKER = 3000
_OBS_CLIP_TOTAL = 12000
_MAX_OBSERVATIONS = 3

_GENERATING: set[str] = set()
_generating_lock = threading.Lock()
_extract_locks: dict[str, threading.Lock] = {}
_extract_meta_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cards_dir():
    from src.speakers.store import SPEAKERS_DIR

    return SPEAKERS_DIR / "cards"


def _profiles_dir():
    from src.speakers.store import SPEAKERS_DIR

    return SPEAKERS_DIR / "profiles"


def _card_path(meeting_id: str):
    from src.paths import assert_resource_id, confine

    assert_resource_id(meeting_id, name="meeting_id")
    cards = _cards_dir()
    return confine(cards / f"{meeting_id}.json", cards)


def _profile_path(person_id: str):
    from src.paths import assert_resource_id, confine

    assert_resource_id(person_id, name="person_id")
    profiles = _profiles_dir()
    return confine(profiles / f"{person_id}.json", profiles)


def _write_json(path, data: dict) -> None:
    from src.atomic_io import write_text_atomic

    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(path, json.dumps(data, ensure_ascii=False, indent=2))


def _read_json(path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("Corrupt profile file %s", path, exc_info=True)
        return None


# ── persistence ────────────────────────────────────────────────────────


def save_card(card: MeetingObservations) -> None:
    _write_json(_card_path(card.meeting_id), card.model_dump())


def get_card(meeting_id: str) -> MeetingObservations | None:
    data = _read_json(_card_path(meeting_id))
    if data is None:
        return None
    try:
        return MeetingObservations(**data)
    except Exception:
        logger.warning("Invalid card file %s", meeting_id, exc_info=True)
        return None


def save_profile(profile: PersonProfile) -> None:
    _write_json(_profile_path(profile.person_id), profile.model_dump())


def get_profile(person_id: str) -> PersonProfile | None:
    data = _read_json(_profile_path(person_id))
    if data is None:
        return None
    try:
        return PersonProfile(**data)
    except Exception:
        logger.warning("Invalid profile file %s", person_id, exc_info=True)
        return None


# ── binding window (pure, no LLM) ──────────────────────────────────────


def _binding_meetings(person_id: str) -> list[tuple[object, list[str], object]]:
    """[(meeting, bound slots with segments, transcript)] newest first."""
    from src.meeting.store import get_transcript, list_meetings

    rows: list[tuple[object, list[str], object]] = []
    for meeting in list_meetings():
        slots = {
            spk for spk, pid in (meeting.speaker_people or {}).items() if pid == person_id
        }
        if not slots:
            continue
        transcript = get_transcript(meeting.id)
        if transcript is None:
            continue
        spoken = {seg.speaker_id for seg in transcript.segments if seg.speaker_id}
        present = sorted(slots & spoken)
        if present:
            rows.append((meeting, present, transcript))
    rows.sort(key=lambda row: (row[0].created_at, row[0].id), reverse=True)
    return rows


def _recent_window(person_id: str, cap: int = RECENT_MEETING_CAP):
    return _binding_meetings(person_id)[:cap]


def effective_binding_keys(person_id: str) -> list[tuple[str, str]]:
    return sorted((m.id, s) for m, slots, _t in _binding_meetings(person_id) for s in slots)


def _slot_texts(transcript, slot: str) -> list[str]:
    return [seg.text for seg in transcript.segments if seg.speaker_id == slot]


def _slot_hash(transcript, slot: str) -> str:
    blob = "\n".join(_slot_texts(transcript, slot))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _batch_input_hash(transcript) -> str:
    """Hash over every speaker's utterances — any edit invalidates the card.

    Salted with a format version so stale cards from a broken parser version
    are rejected instead of being served from cache forever.
    """
    by_slot: dict[str, list[str]] = {}
    for seg in transcript.segments:
        if seg.speaker_id:
            by_slot.setdefault(seg.speaker_id, []).append(seg.text)
    blob = json.dumps({k: "\n".join(v) for k, v in sorted(by_slot.items())}, ensure_ascii=False)
    return hashlib.sha256(("obs-v2|" + blob).encode("utf-8")).hexdigest()


def _window_fingerprint(window) -> str:
    parts = []
    for meeting, slots, transcript in sorted(
        window, key=lambda row: (row[0].id, tuple(row[1]))
    ):
        for slot in slots:
            parts.append(f"{meeting.id}|{slot}|{_slot_hash(transcript, slot)}")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def is_dirty(person_id: str) -> bool:
    profile = get_profile(person_id)
    if profile is None or not profile.input_fingerprint:
        return True
    return _window_fingerprint(_recent_window(person_id)) != profile.input_fingerprint


# ── pipeline ───────────────────────────────────────────────────────────


def _resolve_llm():
    from src.meeting.service import _resolve_meeting_llm

    return _resolve_meeting_llm()


def _parse_json_object(raw: str, slots: list[str]) -> dict[str, list[str]] | None:
    """Parse the speaker→observations map out of a possibly noisy response.

    Returns the map (values may be empty lists — a legitimate "no signal"
    answer), or ``None`` when the response contains no recognizable map at
    all (a parse failure the caller must NOT cache).
    """
    import re

    from src.providers.llm.openai_compat import _extract_json_from_raw, _strip_think

    text = _strip_think(raw or "")
    parsed: dict | None = None
    json_str = _extract_json_from_raw(text)
    if json_str is not None:
        try:
            candidate = json.loads(json_str)
            if isinstance(candidate, dict):
                parsed = candidate
        except json.JSONDecodeError:
            parsed = None
    if parsed is None:
        # Fallback: pull each slot's array directly out of the noise.
        fallback: dict[str, list] = {}
        for slot in slots:
            match = re.search(
                rf'"{re.escape(slot)}"\s*:\s*\[(.*?)\]',
                text,
                re.DOTALL,
            )
            if match is None:
                continue
            try:
                items = json.loads("[" + match.group(1) + "]")
            except json.JSONDecodeError:
                continue
            if isinstance(items, list):
                fallback[slot] = items
        if fallback:
            parsed = fallback
    if parsed is None:
        return None

    out: dict[str, list[str]] = {}
    for slot in slots:
        value = parsed.get(slot, [])
        if not isinstance(value, list):
            value = []
        items = [str(item).strip() for item in value if str(item).strip()]
        out[slot] = items[:_MAX_OBSERVATIONS]
    return out


def _meeting_context(meeting_id: str) -> tuple[str, str]:
    """(title, YYYY-MM-DD) for prompt context; falls back to the raw id."""
    from src.meeting.store import get_meeting

    meeting = get_meeting(meeting_id)
    if meeting is None:
        return meeting_id[:8], ""
    date = ""
    try:
        date = meeting.created_at.strftime("%Y-%m-%d")
    except Exception:
        date = ""
    return meeting.title or meeting_id[:8], date


def _meeting_extract_lock(meeting_id: str) -> threading.Lock:
    with _extract_meta_lock:
        lock = _extract_locks.get(meeting_id)
        if lock is None:
            lock = threading.Lock()
            _extract_locks[meeting_id] = lock
        return lock


def _extract_meeting(meeting, transcript, *, llm) -> MeetingObservations | None:
    """Batch-extract one card covering every speaker slot (idempotent+locked)."""
    from src.prompts import MEETING_OBSERVATION_CARD_PROMPT

    batch_hash = _batch_input_hash(transcript)
    with _meeting_extract_lock(meeting.id):
        cached = get_card(meeting.id)
        if cached is not None and cached.input_hash == batch_hash:
            return cached

        by_slot: dict[str, list[str]] = {}
        for seg in transcript.segments:
            if seg.speaker_id:
                by_slot.setdefault(seg.speaker_id, []).append(seg.text)
        if not by_slot:
            return None
        blocks: list[str] = []
        total = 0
        for slot in sorted(by_slot):
            blob = "\n".join(by_slot[slot])
            clip = blob[:_OBS_CLIP_PER_SPEAKER]
            total += len(clip)
            if total > _OBS_CLIP_TOTAL:
                clip = clip[: max(0, _OBS_CLIP_TOTAL - (total - len(clip)))]
            blocks.append(f"## {slot}\n{clip}")
        title, date = _meeting_context(meeting.id)
        prompt = MEETING_OBSERVATION_CARD_PROMPT.format(
            meeting_title=title,
            meeting_date=date or "unknown date",
            speaker_blocks="\n\n".join(blocks),
        )
        try:
            raw = llm.generate(prompt, thinking=False)
        except TypeError:
            # Custom/injected engines without the thinking kwarg.
            raw = llm.generate(prompt)
        except Exception:
            logger.warning(
                "meeting observation extraction failed meeting=%s", meeting.id, exc_info=True
            )
            return None
        parsed = _parse_json_object(raw, sorted(by_slot))
        if parsed is None:
            # Parse failure: DO NOT cache — leave the meeting uncarded so the
            # profile stays dirty and the next run retries with full context.
            logger.warning(
                "observation card unparseable meeting=%s raw_head=%r",
                meeting.id,
                (raw or "")[:200],
            )
            return None
        speakers = {slot: parsed.get(slot, []) for slot in sorted(by_slot)}
        card = MeetingObservations(
            meeting_id=meeting.id,
            input_hash=batch_hash,
            speakers=speakers,
            extracted_at=_now(),
        )
        save_card(card)
        return card


def _cards_block(window, cards) -> str:
    """Aggregation input lines: per meeting, the person's joined observations."""
    lines = []
    for meeting, slots, _transcript in sorted(
        window, key=lambda row: (row[0].created_at, row[0].id)
    ):
        card = cards.get(meeting.id)
        if card is None:
            continue
        obs = [item for slot in slots for item in card.speakers.get(slot, [])]
        if not obs:
            continue
        title, date = _meeting_context(meeting.id)
        lines.append(f"- [{date or 'unknown'} · {title}] {'; '.join(obs)}")
    return "\n".join(lines)


def _aggregate(person, cards_block: str, *, llm, locale: str) -> str:
    from src.prompts import PERSON_PROFILE_AGGREGATION_PROMPT

    prompt = PERSON_PROFILE_AGGREGATION_PROMPT.format(
        person_name=person.display_name or "Unnamed",
        locale=locale,
        cards=cards_block,
    )
    try:
        return (llm.generate(prompt, thinking=False) or "").strip()
    except TypeError:
        return (llm.generate(prompt) or "").strip()


def _regenerate_core(
    person_id: str,
    *,
    llm=None,
    locale: str = "zh-CN",
    force: bool = False,
) -> PersonProfile:
    """Ensure cards exist for the recent window, then re-aggregate.

    ``force=False`` returns the cached profile when the window fingerprint is
    unchanged (zero LLM calls); ``force=True`` always re-aggregates. This is
    the thread entry point — it never waits on the generating flag.
    """
    from src.speakers.store import get_person

    person = get_person(person_id)
    if person is None:
        raise FileNotFoundError(f"Person {person_id} not found")

    window = _recent_window(person_id)
    if not force:
        existing = get_profile(person_id)
        if existing is not None and existing.input_fingerprint == _window_fingerprint(window):
            return existing

    engine = llm
    cached_cards: dict[str, MeetingObservations] = {}
    pending: list[tuple[object, object]] = []
    for meeting, _slots, transcript in window:
        card = get_card(meeting.id)
        if card is not None and card.input_hash == _batch_input_hash(transcript):
            cached_cards[meeting.id] = card
        else:
            pending.append((meeting, transcript))

    results: list[MeetingObservations | None] = []
    if pending:
        if engine is None:
            engine = _resolve_llm()
        if len(pending) == 1:
            results = [_extract_meeting(pending[0][0], pending[0][1], llm=engine)]
        else:
            # Independent meetings — extract concurrently (small pool keeps
            # provider rate limits happy).
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(max_workers=min(3, len(pending))) as pool:
                results = list(
                    pool.map(lambda row: _extract_meeting(row[0], row[1], llm=engine), pending)
                )

    cards = dict(cached_cards)
    for (meeting, _transcript), card in zip(pending, results):
        if card is not None:
            cards[meeting.id] = card

    carded_window = [row for row in window if row[0].id in cards]
    block = _cards_block(carded_window, cards)
    text = ""
    if block:
        if engine is None:
            engine = _resolve_llm()
        text = _aggregate(person, block, llm=engine, locale=locale)

    profile = PersonProfile(
        person_id=person_id,
        text=text,
        generated_at=_now(),
        source_count=len(carded_window),
        input_fingerprint=_window_fingerprint(carded_window),
    )
    save_profile(profile)
    return profile


def _wait_while_generating(person_id: str, timeout: float = 180.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with _generating_lock:
            if person_id not in _GENERATING:
                return
        time.sleep(0.05)


def regenerate_profile(
    person_id: str,
    *,
    llm=None,
    locale: str = "zh-CN",
    force: bool = False,
) -> PersonProfile:
    """Public entry: waits out any ongoing background regeneration first."""
    from src.speakers.store import get_person

    if get_person(person_id) is None:
        raise FileNotFoundError(f"Person {person_id} not found")
    _wait_while_generating(person_id)
    return _regenerate_core(person_id, llm=llm, locale=locale, force=force)


# ── state + background kick ────────────────────────────────────────────


def _is_generating(person_id: str) -> bool:
    with _generating_lock:
        return person_id in _GENERATING


def profile_state(person_id: str) -> dict:
    with _generating_lock:
        generating = person_id in _GENERATING
    profile = get_profile(person_id)
    if profile is None:
        return {
            "state": "generating" if generating else "none",
            "text": "",
            "generated_at": "",
            "source_count": 0,
            "dirty": True,
        }
    return {
        "state": "generating" if generating else "ready",
        "text": profile.text,
        "generated_at": profile.generated_at,
        "source_count": profile.source_count,
        "dirty": is_dirty(person_id),
    }


def start_regenerate(person_id: str, *, locale: str = "zh-CN", force: bool = False) -> dict:
    """Kick a background regeneration thread; idempotent while running.

    ``force=False`` (default) is the "pull latest" mode: a clean profile is
    returned as-is with no thread and no LLM call; ``force=True`` always
    re-aggregates (explicit user action).
    """
    from src.speakers.store import get_person

    if get_person(person_id) is None:
        raise FileNotFoundError(f"Person {person_id} not found")
    if not force:
        profile = get_profile(person_id)
        if profile is not None and profile.input_fingerprint and not is_dirty(person_id):
            return profile_state(person_id)
    with _generating_lock:
        already = person_id in _GENERATING
        if not already:
            _GENERATING.add(person_id)

    def _run() -> None:
        try:
            _regenerate_core(person_id, locale=locale, force=force)
        except Exception:
            logger.warning("profile regeneration failed person=%s", person_id, exc_info=True)
        finally:
            with _generating_lock:
                _GENERATING.discard(person_id)

    if not already:
        threading.Thread(
            target=_run,
            name=f"profile-regen-{person_id[:8]}",
            daemon=True,
        ).start()
    return profile_state(person_id)
