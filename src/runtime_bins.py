"""Resolvable paths for bundled native binaries (ffmpeg)."""

from __future__ import annotations

import os
import shutil


def ffmpeg_bin() -> str:
    """``SINKDUCE_FFMPEG`` if set, else ``PATH``, else the name ``ffmpeg``."""
    raw = (os.environ.get("SINKDUCE_FFMPEG") or "").strip()
    if raw:
        return raw
    found = shutil.which("ffmpeg")
    return found or "ffmpeg"
