from __future__ import annotations

import json
import os
import socket
import uuid
from pathlib import Path

from src.config import DATA_DIR

PAIRING_DIR = DATA_DIR / "pairing"


def _tokens_path() -> Path:
    return PAIRING_DIR / "tokens.json"


def _box_path() -> Path:
    return PAIRING_DIR / "box.json"


def load() -> list[dict]:
    path = _tokens_path()
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return list(data.get("tokens", []))


def save(records: list[dict]) -> None:
    from src.atomic_io import write_text_atomic

    write_text_atomic(
        _tokens_path(),
        json.dumps({"version": 1, "tokens": records}, ensure_ascii=False, indent=2),
    )


def load_box() -> dict:
    path = _box_path()
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_box(box: dict) -> None:
    from src.atomic_io import write_text_atomic

    write_text_atomic(_box_path(), json.dumps(box, ensure_ascii=False, indent=2))


def ensure_box() -> dict:
    """盒子身份：box_id 永久不变；box_name 默认取主机名，可在设置页改。"""
    box = load_box()
    if not box.get("box_id"):
        default_name = socket.gethostname() or "SinkDuce Box"
        box = {"box_id": uuid.uuid4().hex, "box_name": os.environ.get("SINKDUCE_BOX_NAME") or default_name}
        save_box(box)
    return box
