from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .models import HotWordItem, HotWordsLibrary

logger = logging.getLogger("hot_words.store")
HOTWORDS_DIR = Path("data").resolve() / "hot_words"
SETTINGS_PATH = HOTWORDS_DIR / "_settings.json"
# JSON files that are not library documents
_SKIP_FILENAMES = frozenset({"_settings.json"})


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


def get_default_library_id() -> str | None:
    """Return the configured default library id, or None if unset/missing."""
    raw = _read_settings().get("default_library_id")
    if not raw or not isinstance(raw, str):
        return None
    lib_id = raw.strip()
    if not lib_id:
        return None
    if get_library(lib_id) is None:
        return None
    return lib_id


def set_default_library_id(library_id: str | None) -> str | None:
    """Set (or clear) the default hot-words library. Returns the stored id."""
    settings = _read_settings()
    if not library_id:
        settings["default_library_id"] = None
        _write_settings(settings)
        return None
    lib = get_library(library_id)
    if lib is None:
        raise FileNotFoundError(f"Hot words library {library_id} not found")
    settings["default_library_id"] = library_id
    _write_settings(settings)
    return library_id


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
    data = _read_json(HOTWORDS_DIR / f"{library_id}.json")
    if data is None:
        return None
    return _dict_to_lib(data)


def list_libraries() -> list[HotWordsLibrary]:
    if not HOTWORDS_DIR.exists():
        return []
    libs: list[HotWordsLibrary] = []
    for entry in sorted(HOTWORDS_DIR.iterdir(), key=lambda e: e.stat().st_mtime, reverse=True):
        if not entry.is_file() or not entry.suffix == ".json":
            continue
        if entry.name in _SKIP_FILENAMES or entry.name.startswith("_"):
            continue
        data = _read_json(entry)
        if data is not None:
            libs.append(_dict_to_lib(data))
    return libs


def update_library(library_id: str, **fields) -> HotWordsLibrary:
    from src.identity import authorize, get_actor

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

    authorize(get_actor(), "hot_words.delete", {"library_id": library_id})
    path = HOTWORDS_DIR / f"{library_id}.json"
    if not path.exists():
        return False
    settings = _read_settings()
    was_default = settings.get("default_library_id") == library_id
    path.unlink()
    if was_default:
        settings["default_library_id"] = None
        _write_settings(settings)
    return True


def library_summary(lib: HotWordsLibrary, default_id: str | None = None) -> dict:
    if default_id is None:
        default_id = get_default_library_id()
    return {
        "id": lib.id,
        "name": lib.name,
        "description": lib.description,
        "word_count": len(lib.words),
        "is_default": bool(default_id and lib.id == default_id),
        "created_at": lib.created_at,
        "updated_at": lib.updated_at,
    }
