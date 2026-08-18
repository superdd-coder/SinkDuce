from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from src.config import DATA_DIR

from .models import HotWordItem, HotWordsLibrary

logger = logging.getLogger("hot_words.store")
HOTWORDS_DIR = DATA_DIR / "hot_words"
SETTINGS_PATH = HOTWORDS_DIR / "_settings.json"
# JSON files that are not library documents
_SKIP_FILENAMES = frozenset({"_settings.json"})

MEETING_PEOPLE_ID = "system:meeting-people"
MEETING_PEOPLE_NAME = "Meeting People"
_MEETING_PEOPLE_MIN_MEETINGS = 2


def _write_json(path: Path, data: dict) -> None:
    from src.atomic_io import write_text_atomic

    write_text_atomic(path, json.dumps(data, ensure_ascii=False, indent=2))


def _read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _lib_to_dict(lib: HotWordsLibrary) -> dict:
    return lib.model_dump()


def _dict_to_lib(data: dict) -> HotWordsLibrary:
    return HotWordsLibrary(**data)


def _read_settings() -> dict:
    data = _read_json(SETTINGS_PATH)
    return data if isinstance(data, dict) else {}


def _write_settings(data: dict) -> None:
    _write_json(SETTINGS_PATH, data)


def is_system_library(library_id: str | None) -> bool:
    return (library_id or "") == MEETING_PEOPLE_ID


def _library_exists(library_id: str) -> bool:
    if is_system_library(library_id):
        return True
    return (HOTWORDS_DIR / f"{library_id}.json").is_file()


def _filter_known_ids(ids: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in ids:
        lid = str(raw or "").strip()
        if not lid or lid in seen or not _library_exists(lid):
            continue
        seen.add(lid)
        out.append(lid)
    return out


def get_pinned_library_ids() -> list[str]:
    """Libraries copied onto new meetings. Meeting People is pinned by default."""
    settings = _read_settings()
    if "pinned_library_ids" in settings:
        raw = settings.get("pinned_library_ids") or []
        if not isinstance(raw, list):
            raw = []
        return _filter_known_ids([str(x) for x in raw])
    pins = [MEETING_PEOPLE_ID]
    legacy = settings.get("default_library_id")
    if isinstance(legacy, str) and legacy.strip() and legacy.strip() != MEETING_PEOPLE_ID:
        pins.append(legacy.strip())
    return _filter_known_ids(pins)


def set_pinned_library_ids(library_ids: list[str] | None) -> list[str]:
    """Replace the pin list. Unknown ids raise FileNotFoundError."""
    incoming = [str(x).strip() for x in (library_ids or []) if str(x).strip()]
    for lid in incoming:
        if not _library_exists(lid):
            raise FileNotFoundError(f"Hot words library {lid} not found")
    pins = _filter_known_ids(incoming)
    settings = _read_settings()
    settings["pinned_library_ids"] = pins
    settings["default_library_id"] = next(
        (lid for lid in pins if not is_system_library(lid)),
        pins[0] if pins else None,
    )
    _write_settings(settings)
    return pins


def get_default_library_id() -> str | None:
    """First pinned user library, else first pin (compat for old clients)."""
    pins = get_pinned_library_ids()
    for lid in pins:
        if not is_system_library(lid):
            return lid
    return pins[0] if pins else None


def set_default_library_id(library_id: str | None) -> str | None:
    """Compat: pin this library (keeping Meeting People if already pinned)."""
    if not library_id:
        pins = [lid for lid in get_pinned_library_ids() if is_system_library(lid)]
        set_pinned_library_ids(pins)
        return None
    lid = str(library_id).strip()
    if not _library_exists(lid):
        raise FileNotFoundError(f"Hot words library {lid} not found")
    pins = get_pinned_library_ids()
    if lid not in pins:
        pins.append(lid)
    set_pinned_library_ids(pins)
    return lid


def person_assignment_counts() -> dict[str, int]:
    """How many meetings bind each person_id via speaker_people."""
    from src.meeting.store import list_meetings

    counts: dict[str, int] = {}
    for meeting in list_meetings():
        seen: set[str] = set()
        for pid in (meeting.speaker_people or {}).values():
            key = str(pid or "").strip()
            if not key or key in seen:
                continue
            seen.add(key)
            counts[key] = counts.get(key, 0) + 1
    return counts


def build_meeting_people_library() -> HotWordsLibrary:
    """Virtual read-only library: People names assigned in ≥2 meetings."""
    from src.speakers.store import list_people

    counts = person_assignment_counts()
    seen: set[str] = set()
    names: list[str] = []
    for person in list_people():
        if counts.get(person.id, 0) < _MEETING_PEOPLE_MIN_MEETINGS:
            continue
        name = (person.display_name or "").strip()
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        names.append(name)
    names.sort(key=str.casefold)
    return HotWordsLibrary(
        id=MEETING_PEOPLE_ID,
        name=MEETING_PEOPLE_NAME,
        description="Names from People assigned in at least two meetings. Read-only.",
        words=[HotWordItem(text=name, weight=4) for name in names],
        created_at="",
        updated_at="",
    )


def meeting_library_ids(meeting) -> list[str]:
    """Selected libraries on a meeting. Falls back to the legacy single id."""
    ids = getattr(meeting, "hot_words_library_ids", None)
    if ids:
        return [str(x) for x in ids if x]
    one = getattr(meeting, "hot_words_library_id", None)
    return [str(one)] if one else []


def collect_hot_words(library_ids: list[str] | None) -> list[dict]:
    """Concatenate libraries; same text keeps the higher weight."""
    picked: dict[str, HotWordItem] = {}
    for lid in library_ids or []:
        lib = get_library(lid)
        if lib is None:
            continue
        for word in lib.words:
            text = (word.text or "").strip()
            if not text:
                continue
            key = text.casefold()
            prev = picked.get(key)
            if prev is None or word.weight > prev.weight:
                picked[key] = word
    return [w.model_dump() for w in picked.values()]


def collect_meeting_hot_words(meeting) -> list[dict]:
    return collect_hot_words(meeting_library_ids(meeting))


def hot_words_prompt_text(meeting) -> str:
    words = collect_meeting_hot_words(meeting)
    if not words:
        return "(None)"
    return ", ".join(w["text"] for w in words)


def apply_pinned_libraries(meeting_id: str):
    """Copy current pins onto a newly created meeting."""
    from src.meeting.store import update_meeting

    pins = get_pinned_library_ids()
    return update_meeting(
        meeting_id,
        hot_words_library_ids=pins,
        hot_words_library_id=pins[0] if pins else None,
    )


def create_library(
    name: str,
    description: str = "",
    words: list[HotWordItem] | list[dict] | None = None,
) -> HotWordsLibrary:
    from src.identity import authorize, get_actor

    authorize(get_actor(), "hot_words.create", {})
    now = datetime.now(timezone.utc).isoformat()
    parsed_words: list[HotWordItem] = []
    if words:
        for w in words:
            if isinstance(w, HotWordItem):
                parsed_words.append(w)
            elif isinstance(w, dict):
                parsed_words.append(HotWordItem(**w))
    lib = HotWordsLibrary(
        id=uuid.uuid4().hex,
        name=name,
        description=description,
        words=parsed_words,
        created_at=now,
        updated_at=now,
    )
    _write_json(HOTWORDS_DIR / f"{lib.id}.json", _lib_to_dict(lib))
    logger.info(
        "Created hot words library id=%s name=%s words=%d",
        lib.id,
        lib.name,
        len(parsed_words),
    )
    return lib


def get_library(library_id: str) -> HotWordsLibrary | None:
    if is_system_library(library_id):
        return build_meeting_people_library()
    data = _read_json(HOTWORDS_DIR / f"{library_id}.json")
    if data is None:
        return None
    return _dict_to_lib(data)


def list_libraries() -> list[HotWordsLibrary]:
    libs: list[HotWordsLibrary] = [build_meeting_people_library()]
    if not HOTWORDS_DIR.exists():
        return libs
    for entry in sorted(HOTWORDS_DIR.iterdir(), key=lambda e: e.stat().st_mtime, reverse=True):
        if not entry.is_file() or not entry.suffix == ".json":
            continue
        if entry.name in _SKIP_FILENAMES or entry.name.startswith("_"):
            continue
        data = _read_json(entry)
        if data is None:
            continue
        lib = _dict_to_lib(data)
        if is_system_library(lib.id):
            continue
        libs.append(lib)
    return libs


def update_library(library_id: str, **fields) -> HotWordsLibrary:
    from src.identity import authorize, get_actor

    if is_system_library(library_id):
        raise PermissionError("Meeting People is a system library and cannot be edited")
    authorize(get_actor(), "hot_words.update", {"library_id": library_id})
    lib = get_library(library_id)
    if lib is None:
        raise FileNotFoundError(f"Hot words library {library_id} not found")
    for key, value in fields.items():
        if key == "words" and isinstance(value, list):
            setattr(lib, key, [HotWordItem(**w) if isinstance(w, dict) else w for w in value])
        else:
            setattr(lib, key, value)
    lib.updated_at = datetime.now(timezone.utc).isoformat()
    _write_json(HOTWORDS_DIR / f"{lib.id}.json", _lib_to_dict(lib))
    return lib


def delete_library(library_id: str) -> bool:
    from src.identity import authorize, get_actor

    if is_system_library(library_id):
        raise PermissionError("Meeting People is a system library and cannot be deleted")
    authorize(get_actor(), "hot_words.delete", {"library_id": library_id})
    path = HOTWORDS_DIR / f"{library_id}.json"
    if not path.exists():
        return False
    path.unlink()
    pins = [lid for lid in get_pinned_library_ids() if lid != library_id]
    set_pinned_library_ids(pins)
    return True


def library_summary(
    lib: HotWordsLibrary,
    default_id: str | None = None,
    pinned_ids: list[str] | None = None,
) -> dict:
    pins = pinned_ids if pinned_ids is not None else get_pinned_library_ids()
    if default_id is None:
        default_id = get_default_library_id()
    pinned = lib.id in pins
    return {
        "id": lib.id,
        "name": lib.name,
        "description": lib.description,
        "word_count": len(lib.words),
        "is_system": is_system_library(lib.id),
        "is_pinned": pinned,
        "is_default": pinned,
        "created_at": lib.created_at,
        "updated_at": lib.updated_at,
    }
