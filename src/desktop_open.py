"""Open http(s) URLs in the system browser (desktop WebView cannot)."""

from __future__ import annotations

import subprocess
import sys
from urllib.parse import urlparse


def allowed_external_url(url: str) -> str | None:
    raw = (url or "").strip()
    if not raw:
        return None
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"}:
        return None
    if not parsed.netloc:
        return None
    return raw


def open_external_url(url: str) -> None:
    allowed = allowed_external_url(url)
    if not allowed:
        raise ValueError("url must be http(s)")
    if sys.platform == "darwin":
        subprocess.Popen(["open", allowed])
        return
    if sys.platform == "win32":
        subprocess.Popen(["cmd", "/c", "start", "", allowed])
        return
    subprocess.Popen(["xdg-open", allowed])
