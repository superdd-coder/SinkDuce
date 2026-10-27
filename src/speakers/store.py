from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from src.config import DATA_DIR
from src.paths import assert_resource_id, confine

from .models import Enrollment, Person

logger = logging.getLogger("speakers.store")
SPEAKERS_DIR = DATA_DIR / "speakers"
_SKIP_PREFIX = "_"


def _settings_path() -> Path:
    return SPEAKERS_DIR / "_settings.json"


def _person_path(person_id: str) -> Path:
    assert_resource_id(person_id, name="person_id")
    return confine(SPEAKERS_DIR / f"{person_id}.json", SPEAKERS_DIR)


def _write_json(path: Path, data: dict) -> None:
    from src.atomic_io import write_text_atomic

    write_text_atomic(path, json.dumps(data, ensure_ascii=False, indent=2))


def _read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_settings() -> dict:
    data = _read_json(_settings_path())
    return data if isinstance(data, dict) else {}


def _write_settings(data: dict) -> None:
    SPEAKERS_DIR.mkdir(parents=True, exist_ok=True)
    _write_json(_settings_path(), data)


def get_me_person_id() -> str | None:
    raw = _read_settings().get("me_person_id")
    if not raw or not isinstance(raw, str):
        return None
    pid = raw.strip()
    if not pid or get_person(pid) is None:
        return None
    return pid


def set_me_person_id(person_id: str | None) -> str | None:
    settings = _read_settings()
    if not person_id:
        settings["me_person_id"] = None
        _write_settings(settings)
        return None
    pid = str(person_id).strip()
    if get_person(pid) is None:
        raise FileNotFoundError(f"Person {pid} not found")
    settings["me_person_id"] = pid
    _write_settings(settings)
    return pid


def create_person(display_name: str, disambiguator: str = "") -> Person:
    from src.identity import authorize, get_actor

    # people 域：新建人物（含会议内联新建）归 create 档（默认含 member）。
    authorize(get_actor(), "people.create", {})
    now = _now()
    person = Person(
        id=uuid.uuid4().hex,
        display_name=(display_name or "").strip(),
        disambiguator=(disambiguator or "").strip(),
        created_at=now,
        updated_at=now,
    )
    SPEAKERS_DIR.mkdir(parents=True, exist_ok=True)
    _write_json(_person_path(person.id), person.model_dump())
    logger.info("Created person id=%s name=%s", person.id, person.display_name)
    return person


def get_person(person_id: str) -> Person | None:
    try:
        path = _person_path(person_id)
    except ValueError:
        return None
    try:
        data = _read_json(path)
    except (json.JSONDecodeError, OSError):
        logger.warning("Corrupt person file %s", person_id, exc_info=True)
        return None
    if data is None:
        return None
    return Person(**data)


def list_people(q: str | None = None) -> list[Person]:
    if not SPEAKERS_DIR.exists():
        return []
    people: list[Person] = []
    needle = (q or "").strip().lower()
    for entry in SPEAKERS_DIR.iterdir():
        if not entry.is_file() or entry.suffix != ".json":
            continue
        if entry.name.startswith(_SKIP_PREFIX):
            continue
        try:
            data = _read_json(entry)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Skipping corrupt person file %s: %s", entry.name, exc)
            continue
        if not data or not isinstance(data, dict):
            continue
        try:
            person = Person(**data)
        except Exception:
            logger.warning("Skipping invalid person file %s", entry.name, exc_info=True)
            continue
        if needle:
            blob = f"{person.display_name} {person.disambiguator}".lower()
            if needle not in blob:
                continue
        people.append(person)
    people.sort(key=lambda p: (p.display_name.lower(), p.disambiguator.lower(), p.id))
    return people


def update_person(person_id: str, **fields) -> Person:
    """低层原语：写人物档案（**不在这里授权**）。

    三类语义完全不同的调用方共用它，单点检查分不清该查哪一档（2026-09-16 审计）：

    * **enroll**（写 ``centroid``/``recent``/``speech_sec``）→ 属 ``people.read``
      —— "查看与标注"语义上含写声纹（用户 2026-09-16 定）；
    * **assign_speaker**（写 ``last_meeting_id``）→ 同属 ``people.read``；
    * **资料编辑**（写 ``display_name``/``disambiguator``）→ 属 ``people.write``。

    所以授权上移到**路由层**（``PATCH /speakers/{person_id}`` / ``PUT /speakers/me``
    检 ``people.write``），内部调用由 ``assign_speaker`` / ``commit`` 入口的
    ``people.read`` 把住。见设计 §3.3④。
    """
    person = get_person(person_id)
    if person is None:
        raise FileNotFoundError(f"Person {person_id} not found")
    for key, value in fields.items():
        if key == "recent" and isinstance(value, list):
            parsed: list[Enrollment] = []
            for row in value:
                if isinstance(row, Enrollment):
                    parsed.append(row)
                elif isinstance(row, dict):
                    parsed.append(Enrollment(**row))
            setattr(person, key, parsed)
        else:
            setattr(person, key, value)
    person.updated_at = _now()
    _write_json(_person_path(person.id), person.model_dump())
    return person


def delete_person(person_id: str) -> bool:
    from src.identity import authorize, get_actor

    authorize(get_actor(), "people.delete", {"person_id": person_id})
    try:
        path = _person_path(person_id)
    except ValueError:
        return False
    if not path.exists():
        return False
    was_me = (_read_settings().get("me_person_id") or "") == person_id
    path.unlink()
    if was_me:
        set_me_person_id(None)
    return True


def speaker_display_name(person: Person) -> str:
    """Name shown on a bound speaker / in Summary. Note is picker-only."""
    return (person.display_name or "").strip() or "Unnamed"


def person_label(person: Person, *, name_counts: dict[str, int] | None = None) -> str:
    """Picker / People-library label. May include the note to tell homonyms apart."""
    name = speaker_display_name(person)
    extra = (person.disambiguator or "").strip()
    if extra:
        return f"{name} · {extra}"
    counts = name_counts
    if counts is None:
        counts = {}
        for other in list_people():
            key = (other.display_name or "").strip().lower()
            counts[key] = counts.get(key, 0) + 1
    if counts.get(name.lower(), 0) > 1:
        stamp = ""
        if person.recent:
            stamp = (person.recent[-1].enrolled_at or "")[:10]
        if not stamp and person.last_meeting_id:
            stamp = person.last_meeting_id[:8]
        if stamp:
            return f"{name} · {stamp}"
    return name


def slot_speech_sec(meeting_id: str, speaker_id: str) -> float:
    """Real speaking time for one bound slot, summed from transcript segments.

    ``Enrollment.speech_sec`` is the voiceprint SAMPLE duration (capped at
    ``SAMPLE_BUDGET_SEC`` = 60s) — right for centroid weighting, wrong as a
    display of how long the person actually spoke.
    """
    from src.meeting.store import get_transcript

    if not speaker_id:
        return 0.0
    transcript = get_transcript(meeting_id)
    if transcript is None:
        return 0.0
    return float(
        sum(
            max(0.0, float(seg.end) - float(seg.start))
            for seg in transcript.segments
            if (seg.speaker_id or "") == speaker_id
        )
    )


def person_public_dict(person: Person, *, name_counts: dict[str, int] | None = None) -> dict:
    from src.speakers.service import person_meeting_rows

    return {
        "id": person.id,
        "display_name": person.display_name,
        "disambiguator": person.disambiguator,
        "label": person_label(person, name_counts=name_counts),
        "has_voiceprint": bool(person.centroid),
        "last_meeting_id": person.last_meeting_id,
        # Real speech time over the bound meetings — NOT person.speech_sec,
        # which sums the per-meeting 60s voiceprint samples.
        "speech_sec": sum(r["speech_sec"] for r in person_meeting_rows(person)),
        "meeting_count": len({r.meeting_id for r in person.recent} | (
            {person.last_meeting_id} if person.last_meeting_id else set()
        )),
        "is_me": get_me_person_id() == person.id,
    }
