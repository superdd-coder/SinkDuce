"""Session image snapshot store for cross-turn image memory.

Tool searches that return images keep two things:

- The tool message's **persisted text** carries the original ``:::image``
  blocks (image_id + file_id + OCR + description) — the canonical,
  byte-stable history form.
- The exact rendered payload (``data:<mime>;base64,<b64>``) is snapshotted
  here **once**, so history hydration can re-attach identical bytes on
  every later turn without re-encoding — the byte stability is what keeps
  provider prefix (KV) caches valid.

Files: ``<base_dir>/<session_id>/<image_id>.txt``. Write-once: an existing
snapshot is never overwritten (re-encoding would break cache prefixes).
"""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9._-]")


def default_base_dir() -> Path:
    from src.config import DATA_DIR

    return Path(DATA_DIR) / "session_images"


def _session_dir(session_id: str, base_dir: Path | None) -> Path:
    root = Path(base_dir) if base_dir is not None else default_base_dir()
    safe = _SAFE_ID_RE.sub("_", session_id or "_")
    return root / safe


def _image_filename(image_id: str) -> str:
    return (_SAFE_ID_RE.sub("_", image_id or "_") or "_") + ".txt"


def snapshot_session_images(
    session_id: str,
    images: dict[str, str],
    *,
    base_dir: Path | None = None,
) -> None:
    """Write-once snapshot of data-URL payloads keyed by image_id."""
    if not session_id or not images:
        return
    dir_ = _session_dir(session_id, base_dir)
    try:
        dir_.mkdir(parents=True, exist_ok=True)
        for image_id, data_url in images.items():
            if not image_id or not data_url:
                continue
            path = dir_ / _image_filename(image_id)
            if path.exists():
                continue  # write-once: identical bytes every later turn
            path.write_text(data_url, encoding="utf-8")
    except Exception:
        logger.exception("Failed to snapshot session images session=%s", session_id)


def load_session_image(
    session_id: str,
    image_id: str,
    *,
    base_dir: Path | None = None,
) -> str | None:
    path = _session_dir(session_id, base_dir) / _image_filename(image_id)
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def purge_session_images(session_id: str, *, base_dir: Path | None = None) -> None:
    if not session_id:
        return
    dir_ = _session_dir(session_id, base_dir)
    try:
        if dir_.exists():
            shutil.rmtree(dir_)
    except Exception:
        logger.exception("Failed to purge session images session=%s", session_id)
