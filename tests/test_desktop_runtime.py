"""Desktop runtime env overrides. Unset SINKDUCE_* must match Docker defaults."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.config import (
    get_data_dir,
    get_frontend_dist,
    get_models_dir,
    health_payload,
    is_desktop_runtime,
    resolve_bind_host,
    resolve_bind_port,
    resolve_qdrant_host,
    resolve_qdrant_port,
)
from src.runtime_bins import ffmpeg_bin


def test_data_dir_defaults_to_cwd_data(monkeypatch):
    monkeypatch.delenv("SINKDUCE_DATA", raising=False)
    assert get_data_dir() == Path("data").resolve()


def test_data_dir_follows_sinkduce_data(tmp_path, monkeypatch):
    target = tmp_path / "app-data"
    monkeypatch.setenv("SINKDUCE_DATA", str(target))
    assert get_data_dir() == target.resolve()


def test_app_version_not_placeholder():
    from src.api.routes.config import _get_app_version

    version = _get_app_version()
    assert version != "0.0.0"
    assert version[0].isdigit()


def test_frontend_dist_override_and_default(tmp_path, monkeypatch):
    monkeypatch.delenv("SINKDUCE_FRONTEND_DIST", raising=False)
    assert get_frontend_dist() == (
        Path(__file__).resolve().parents[1] / "frontend" / "dist"
    )
    monkeypatch.setenv("SINKDUCE_FRONTEND_DIST", str(tmp_path / "spa"))
    assert get_frontend_dist() == (tmp_path / "spa").resolve()


def test_models_dir_follows_hf_home_then_data_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("HF_HOME", raising=False)
    monkeypatch.delenv("SINKDUCE_DATA", raising=False)
    assert get_models_dir() == Path("data").resolve() / "models"

    monkeypatch.setenv("SINKDUCE_DATA", str(tmp_path / "app"))
    assert get_models_dir() == (tmp_path / "app" / "models").resolve()

    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))
    assert get_models_dir() == (tmp_path / "hf").resolve()


def test_data_dir_expands_user_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("SINKDUCE_DATA", "~/sink-data")
    assert get_data_dir() == (tmp_path / "sink-data").resolve()


def test_qdrant_override_prefers_env(monkeypatch):
    monkeypatch.setenv("SINKDUCE_QDRANT_HOST", "127.0.0.1")
    monkeypatch.setenv("SINKDUCE_QDRANT_PORT", "6335")
    assert resolve_qdrant_host("qdrant") == "127.0.0.1"
    assert resolve_qdrant_port(6333) == 6335


def test_qdrant_falls_back_to_yaml_when_unset(monkeypatch):
    monkeypatch.delenv("SINKDUCE_QDRANT_HOST", raising=False)
    monkeypatch.delenv("SINKDUCE_QDRANT_PORT", raising=False)
    monkeypatch.delenv("QDRANT_HOST", raising=False)
    monkeypatch.setenv("QDRANT_HOST", "someone-elses-qdrant")
    assert resolve_qdrant_host("qdrant") == "qdrant"
    assert resolve_qdrant_port(6333) == 6333


def test_bind_override_and_default(monkeypatch):
    monkeypatch.delenv("SINKDUCE_HOST", raising=False)
    monkeypatch.delenv("SINKDUCE_PORT", raising=False)
    assert resolve_bind_host("0.0.0.0") == "0.0.0.0"
    assert resolve_bind_port(18900) == 18900
    monkeypatch.setenv("SINKDUCE_HOST", "127.0.0.1")
    monkeypatch.setenv("SINKDUCE_PORT", "18910")
    assert resolve_bind_host("0.0.0.0") == "127.0.0.1"
    assert resolve_bind_port(18900) == 18910


def test_ffmpeg_bin_env_then_which(tmp_path, monkeypatch):
    custom = tmp_path / "ffmpeg"
    custom.write_text("#!/bin/sh\n")
    custom.chmod(0o755)
    monkeypatch.setenv("SINKDUCE_FFMPEG", str(custom))
    assert ffmpeg_bin() == str(custom)

    monkeypatch.delenv("SINKDUCE_FFMPEG", raising=False)
    monkeypatch.setattr("src.runtime_bins.shutil.which", lambda _n: "/usr/bin/ffmpeg")
    assert ffmpeg_bin() == "/usr/bin/ffmpeg"

    monkeypatch.setattr("src.runtime_bins.shutil.which", lambda _n: None)
    assert ffmpeg_bin() == "ffmpeg"


def test_collection_constants_use_data_dir():
    from src.collections.store import COLLECTIONS_DIR
    from src.config import DATA_DIR
    from src.meeting.store import MEETINGS_DIR
    from src.notes.store import NOTES_DIR

    assert COLLECTIONS_DIR == DATA_DIR / "collections"
    assert MEETINGS_DIR == DATA_DIR / "meetings"
    assert NOTES_DIR == DATA_DIR / "notes"


def test_session_store_default_follows_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("SINKDUCE_DATA", str(tmp_path))
    from src.db.sessions import SessionStore

    store = SessionStore()
    assert store._db_path == tmp_path.resolve() / "sessions.db"


def test_sysaudio_helper_reachable_false_on_closed_port():
    from src.config import sysaudio_helper_reachable

    assert sysaudio_helper_reachable("http://127.0.0.1:1") is False
    assert sysaudio_helper_reachable("") is False


def test_sysaudio_watchdog_noop_outside_desktop(monkeypatch):
    monkeypatch.delenv("SINKDUCE_DESKTOP", raising=False)
    from src import desktop_sysaudio as mod

    monkeypatch.setattr(mod, "_thread", None)
    mod.start_sysaudio_watchdog()
    assert mod._thread is None


def test_desktop_flag_and_health_payload(monkeypatch):
    monkeypatch.delenv("SINKDUCE_DESKTOP", raising=False)
    monkeypatch.delenv("SINKDUCE_PORT", raising=False)
    monkeypatch.delenv("SINKDUCE_HOST", raising=False)
    assert is_desktop_runtime() is False
    payload = health_payload()
    assert payload["status"] == "ok"
    assert "desktop" not in payload
    assert isinstance(payload["port"], int)
    assert payload["mcp_url"] == f"http://{payload['host']}:{payload['port']}/mcp"

    monkeypatch.setenv("SINKDUCE_DESKTOP", "1")
    monkeypatch.setenv("SINKDUCE_HOST", "127.0.0.1")
    monkeypatch.setenv("SINKDUCE_PORT", "18910")
    assert is_desktop_runtime() is True
    assert health_payload() == {
        "status": "ok",
        "host": "127.0.0.1",
        "port": 18910,
        "mcp_url": "http://127.0.0.1:18910/mcp",
        "desktop": True,
    }

    monkeypatch.setenv("SINKDUCE_SYS_AUDIO", "http://127.0.0.1:18950/")
    monkeypatch.setattr("src.config.sysaudio_helper_reachable", lambda _url: True)
    assert health_payload() == {
        "status": "ok",
        "host": "127.0.0.1",
        "port": 18910,
        "mcp_url": "http://127.0.0.1:18910/mcp",
        "desktop": True,
        "system_audio": "http://127.0.0.1:18950",
    }

    monkeypatch.setattr("src.config.sysaudio_helper_reachable", lambda _url: False)
    omitted = health_payload()
    assert omitted["desktop"] is True
    assert "system_audio" not in omitted

    monkeypatch.delenv("SINKDUCE_DESKTOP", raising=False)
    assert "system_audio" not in health_payload()
    assert health_payload()["port"] == 18910


def test_health_mock_update_flag_follows_data_file(tmp_path, monkeypatch):
    monkeypatch.setenv("SINKDUCE_DATA", str(tmp_path))
    monkeypatch.setenv("SINKDUCE_DESKTOP", "1")
    monkeypatch.setenv("SINKDUCE_HOST", "127.0.0.1")
    monkeypatch.setenv("SINKDUCE_PORT", "18910")
    assert "mock_update" not in health_payload()
    (tmp_path / "mock-update").write_text("", encoding="utf-8")
    assert health_payload()["mock_update"] is True


def test_health_wildcard_bind_advertises_loopback(monkeypatch):
    monkeypatch.delenv("SINKDUCE_DESKTOP", raising=False)
    monkeypatch.setenv("SINKDUCE_HOST", "0.0.0.0")
    monkeypatch.setenv("SINKDUCE_PORT", "18900")
    payload = health_payload()
    assert payload["host"] == "127.0.0.1"
    assert payload["port"] == 18900
    assert payload["mcp_url"] == "http://127.0.0.1:18900/mcp"


# ── sysaudio helper lifecycle: no orphans after the app quits ──────────────


def _free_tcp_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def reset_sysaudio_module():
    import threading

    from src import desktop_sysaudio as mod

    original = (mod._stop, mod._thread, mod._proc)
    mod._stop = threading.Event()
    mod._thread = None
    mod._proc = None
    yield mod
    mod._stop.set()
    proc = mod._proc
    if proc is not None and proc.poll() is None:
        proc.kill()
        proc.wait(5)
    mod._stop, mod._thread, mod._proc = original


def test_stop_sysaudio_watchdog_kills_spawned_helper(
    tmp_path, monkeypatch, reset_sysaudio_module
):
    import time

    mod = reset_sysaudio_module
    fake = tmp_path / "fake-sysaudio"
    fake.write_text("#!/bin/sh\nsleep 60\n")
    fake.chmod(0o755)
    port = _free_tcp_port()
    monkeypatch.setenv("SINKDUCE_DATA", str(tmp_path))
    monkeypatch.setenv("SINKDUCE_DESKTOP", "1")
    monkeypatch.setenv("SINKDUCE_SYS_AUDIO", f"http://127.0.0.1:{port}/")
    monkeypatch.setenv("SINKDUCE_SYSAUDIO_BIN", str(fake))

    mod.start_sysaudio_watchdog()
    deadline = time.time() + 10
    while (mod._proc is None or mod._proc.poll() is not None) and time.time() < deadline:
        time.sleep(0.1)
    assert mod._proc is not None
    assert mod._proc.poll() is None

    proc = mod._proc
    mod.stop_sysaudio_watchdog()
    assert mod._proc is None
    deadline = time.time() + 5
    while proc.poll() is None and time.time() < deadline:
        time.sleep(0.05)
    assert proc.poll() is not None


def test_shutdown_helper_terminates_detached_child(reset_sysaudio_module):
    import subprocess
    import time

    mod = reset_sysaudio_module
    proc = subprocess.Popen(["/bin/sh", "-c", "sleep 60"], start_new_session=True)
    try:
        with mod._lock:
            mod._proc = proc
        mod._shutdown_helper()
        deadline = time.time() + 5
        while proc.poll() is None and time.time() < deadline:
            time.sleep(0.05)
        assert proc.poll() is not None
        assert mod._proc is None
    finally:
        if proc.poll() is None:
            proc.kill()
