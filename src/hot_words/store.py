from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .models import HotWordItem, HotWordsLibrary

logger = logging.getLogger("hot_words.store")
HOTWORDS_DIR = Path("data").resolve() / "hot_words"


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _lib_to_dict(lib: HotWordsLibrary) -> dict:
    return lib.model_dump()


def _dict_to_lib(data: dict) -> HotWordsLibrary:
    return HotWordsLibrary(**data)


def create_library(name: str, description: str = "") -> HotWordsLibrary:
    now = datetime.now(timezone.utc).isoformat()
    lib = HotWordsLibrary(
        id=uuid.uuid4().hex,
        name=name,
        description=description,
        words=[],
        created_at=now,
        updated_at=now,
    )
    _write_json(HOTWORDS_DIR / f"{lib.id}.json", _lib_to_dict(lib))
    logger.info("Created hot words library id=%s name=%s", lib.id, lib.name)
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
        data = _read_json(entry)
        if data is not None:
            libs.append(_dict_to_lib(data))
    return libs


def update_library(library_id: str, **fields) -> HotWordsLibrary:
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
    path = HOTWORDS_DIR / f"{library_id}.json"
    if not path.exists():
        return False
    path.unlink()
    return True
