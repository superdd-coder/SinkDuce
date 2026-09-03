"""Keep the desktop PCM helper alive.

WKWebView fetch to a dead 127.0.0.1:18950 throws TypeError: Load failed.
Tauri starts the helper once; if it exits, recording breaks until Cmd+Q.
"""

from __future__ import annotations

import atexit
import logging
import os
import socket
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_stop = threading.Event()
_thread: threading.Thread | None = None
_proc: subprocess.Popen | None = None
_lock = threading.Lock()


def _helper_bin() -> Path | None:
    env = (os.environ.get("SINKDUCE_SYSAUDIO_BIN") or "").strip()
    if env:
        p = Path(env).expanduser()
        if p.is_file():
            return p
    cwd = Path.cwd()
    candidates = [
        cwd.parent.parent / "MacOS" / "sinkduce-sysaudio",
        Path("/Applications/SinkDuce.app/Contents/MacOS/sinkduce-sysaudio"),
        cwd.parent / "sysaudio",
    ]
    for p in candidates:
        if p.is_file():
            return p
    return None


def _port_from_url(url: str) -> int | None:
    raw = (url or "").strip()
    if not raw:
        return None
    parsed = urlparse(raw if "://" in raw else f"http://{raw}")
    return parsed.port


def helper_listening(port: int, timeout: float = 0.2) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def _spawn(bin_path: Path, port: int) -> subprocess.Popen | None:
    log_dir = Path(os.environ.get("SINKDUCE_DATA") or "data") / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_f = open(log_dir / "sysaudio.log", "a", encoding="utf-8")
    except OSError:
        log_f = subprocess.DEVNULL
    try:
        proc = subprocess.Popen(
            [str(bin_path), "--port", str(port)],
            stdout=log_f,
            stderr=log_f,
            start_new_session=True,
        )
        logger.info("Started desktop audio helper %s --port %s pid=%s", bin_path, port, proc.pid)
        return proc
    except OSError as exc:
        logger.warning("Could not start desktop audio helper: %s", exc)
        return None


def _watch() -> None:
    from src.config import is_desktop_runtime

    if not is_desktop_runtime():
        return
    url = (os.environ.get("SINKDUCE_SYS_AUDIO") or "").strip()
    port = _port_from_url(url)
    if not port:
        return
    bin_path = _helper_bin()
    if bin_path is None:
        logger.warning("Desktop audio helper binary not found")
        return

    global _proc
    # Let Tauri's helper bind first so we do not steal its port.
    if _stop.wait(1.5):
        return
    while not _stop.is_set():
        if not helper_listening(port):
            with _lock:
                if _proc is not None:
                    rc = _proc.poll()
                    if rc is None:
                        pass
                    else:
                        _proc = None
                if _proc is None and not _stop.is_set() and not helper_listening(port):
                    _proc = _spawn(bin_path, port)
        if _stop.wait(2.0):
            return


def _shutdown_helper() -> None:
    """Kill the helper we spawned — a detached child must not outlive us."""
    global _proc
    with _lock:
        proc = _proc
        _proc = None
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
        try:
            proc.wait(2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(2)
    except OSError:
        pass


atexit.register(_shutdown_helper)


def start_sysaudio_watchdog() -> None:
    """No-op outside the desktop app. Daemon thread otherwise."""
    from src.config import is_desktop_runtime

    global _thread
    if not is_desktop_runtime():
        return
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_watch, name="sysaudio-watch", daemon=True)
    _thread.start()


def stop_sysaudio_watchdog() -> None:
    _stop.set()
    _shutdown_helper()
