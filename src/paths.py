"""Filesystem confinement and resource-id checks."""

from __future__ import annotations

import re
from pathlib import Path

from src.config import DATA_DIR

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_BLOCKED_DATA_NAMES = frozenset({"config.yaml"})


def confine(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    root_resolved = root.resolve()
    if not resolved.is_relative_to(root_resolved):
        raise ValueError("path escapes root")
    return resolved


def assert_resource_id(value: str, *, name: str = "id") -> str:
    if not value or not _ID_RE.match(value):
        raise ValueError(f"invalid {name}")
    return value


def assert_readable_data_file(path: Path, *, data_root: Path | None = None) -> Path:
    """Allow a file under data/, except config, qdrant storage, and sqlite DBs."""
    root = (data_root or DATA_DIR).resolve()
    resolved = confine(path, root)
    if resolved.name in _BLOCKED_DATA_NAMES:
        raise ValueError("path not allowed")
    if resolved.suffix == ".db":
        raise ValueError("path not allowed")
    try:
        rel = resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("path escapes root") from exc
    if rel.parts and rel.parts[0] == "qdrant":
        raise ValueError("path not allowed")
    return resolved
