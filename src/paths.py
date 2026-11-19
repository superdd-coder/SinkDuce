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
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ValueError(f"invalid {name}")
    return value


def assert_readable_data_file(path: Path, *, data_root: Path | None = None) -> Path:
    """Keep credentials, authorization state and database files out of MCP imports."""
    root = (data_root or DATA_DIR).resolve()
    resolved = confine(path, root)
    if resolved.name.lower() in _BLOCKED_DATA_NAMES:
        raise ValueError("path not allowed")
    if re.search(r"\.(?:db|sqlite3?)(?:-(?:wal|shm|journal))?$", resolved.name, re.IGNORECASE):
        raise ValueError("path not allowed")
    try:
        rel = resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("path escapes root") from exc
    if rel.parts and rel.parts[0].lower() in {"qdrant", "pairing", "hub", "identity"}:
        raise ValueError("path not allowed")
    return resolved
