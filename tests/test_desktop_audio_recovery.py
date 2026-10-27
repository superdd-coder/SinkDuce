"""Desktop recovery contracts; no microphone or TCC settings are touched."""

import subprocess
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]


def test_packager_signs_helpers_with_existing_edition_identity():
    script = (ROOT / "desktop/package_macos.sh").read_text()
    signing = script.find('codesign --force --sign - --identifier "$SYSAUDIO_ID"')
    assert signing > -1, "Sign both helper copies explicitly before sealing the app"
    assert 'SYSAUDIO_ID="app.sinkduce.desktop"' in script
    assert 'SYSAUDIO_ID="app.sinkduce.desktop.ee.sysaudio"' in script
    assert signing < script.index('codesign --force --deep --sign - "$APP"')


@pytest.fixture
def desktop_client(monkeypatch):
    from src.api.routes import desktop

    monkeypatch.setenv("SINKDUCE_DESKTOP", "1")
    monkeypatch.setenv("SINKDUCE_SYS_AUDIO", "http://127.0.0.1:18950")
    app = FastAPI()
    app.include_router(desktop.router)
    return TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50123))


def helper_response(monkeypatch, status, payload):
    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda req: httpx.Response(status, json=payload))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real_client(transport=transport, **kw))


def test_pcm_preserves_permission_error_code(desktop_client, monkeypatch):
    helper_response(monkeypatch, 403, {"error": "microphone_permission", "message": "Enable microphone"})
    res = desktop_client.get("/api/desktop/pcm")
    assert res.status_code == 403
    assert res.json()["detail"] == {"code": "microphone_permission", "message": "Enable microphone"}


def test_pending_permission_is_distinct_from_denial(desktop_client, monkeypatch):
    helper_response(monkeypatch, 409, {"error": "permission_pending", "message": "Waiting for permission"})
    res = desktop_client.get("/api/desktop/pcm")
    assert res.status_code == 409
    assert isinstance(res.json()["detail"], dict)
    assert res.json()["detail"]["code"] == "permission_pending"


def test_silent_permission_status_is_proxied(desktop_client, monkeypatch):
    payload = {"microphone": "authorized", "screen_recording": False, "capturing": False}
    helper_response(monkeypatch, 200, payload)
    res = desktop_client.get("/api/desktop/audio-permissions")
    assert res.status_code == 200
    assert res.json() == payload


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS privacy settings action")
def test_settings_action_only_opens_fixed_privacy_panes(desktop_client, monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda args, **kw: calls.append(args))
    for target, pane in [("microphone", "Privacy_Microphone"), ("screen_recording", "Privacy_ScreenCapture")]:
        res = desktop_client.post("/api/desktop/audio-settings", json={"target": target})
        assert res.status_code == 200
        assert calls[-1] == ["open", f"x-apple.systempreferences:com.apple.preference.security?{pane}"]
    count = len(calls)
    assert desktop_client.post("/api/desktop/audio-settings", json={"target": "file:///tmp/anything"}).status_code == 422
    assert len(calls) == count


def test_watchdog_respects_initial_child_still_starting(monkeypatch):
    from src import desktop_sysaudio as mod

    monkeypatch.setenv("SINKDUCE_SYSAUDIO_PID", "456")
    monkeypatch.setattr(mod.os, "kill", lambda pid, sig: None)
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout="S"))
    assert mod._initial_helper_alive() is True
    def dead(pid, sig):
        raise ProcessLookupError()
    monkeypatch.setattr(mod.os, "kill", dead)
    assert mod._initial_helper_alive() is False


def test_exited_initial_helper_does_not_disable_watchdog(monkeypatch):
    from src import desktop_sysaudio as mod

    monkeypatch.setenv("SINKDUCE_SYSAUDIO_PID", "456")
    monkeypatch.setattr(mod.os, "kill", lambda pid, sig: None)
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout="Z"))
    assert mod._initial_helper_alive() is False


def test_invalid_explicit_helper_path_does_not_switch_editions(tmp_path, monkeypatch):
    from src import desktop_sysaudio as mod

    monkeypatch.setenv("SINKDUCE_SYSAUDIO_BIN", str(tmp_path / "missing-helper"))
    assert mod._helper_bin() is None


def test_audio_settings_are_local_only(desktop_client, monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: calls.append(args))
    remote = TestClient(desktop_client.app, base_url="http://127.0.0.1", client=("192.0.2.10", 50123))
    assert remote.post("/api/desktop/audio-settings", json={"target": "microphone"}).status_code == 404
    assert not calls


@pytest.mark.skipif(shutil.which("swiftc") is None, reason="Swift compiler unavailable")
def test_native_permission_and_attempt_state(tmp_path):
    source = ROOT / "desktop/sysaudio/CaptureState.swift"
    assert source.exists(), "Native pending-permission and capture-attempt state must be testable without TCC"
    harness = tmp_path / "main.swift"
    harness.write_text('''
import Foundation
let request = MicrophonePermissionRequest()
var callback: ((Bool) -> Void)?
var asks = 0
func ask(_ done: @escaping (Bool) -> Void) { asks += 1; callback = done }
assert(request.resolve(status: "notDetermined", request: ask) == "pending")
assert(request.resolve(status: "notDetermined", request: ask) == "pending")
assert(asks == 1)
callback?(false)
assert(request.resolve(status: "denied", request: ask) == "denied")
assert(request.resolve(status: "authorized", request: ask) == "authorized")
assert(request.resolve(status: "notDetermined", request: ask) == "pending")
assert(asks == 2)
let attempts = CaptureAttemptState()
let first = attempts.begin()
assert(attempts.isCurrent(first))
attempts.cancel()
assert(!attempts.isCurrent(first))
let second = attempts.begin()
assert(second != first && attempts.isCurrent(second))
assert(!attempts.isCurrent(first))
print("ok")
''')
    binary = tmp_path / "state-test"
    subprocess.run(["swiftc", "-swift-version", "5", str(source), str(harness), "-o", str(binary)], check=True, capture_output=True, text=True)
    assert subprocess.check_output([str(binary)], text=True).strip() == "ok"


@pytest.mark.skipif(shutil.which("rustc") is None, reason="Rust compiler unavailable")
def test_canonical_helper_launch_environment(tmp_path):
    source = ROOT / "desktop/tauri/src-tauri/src/audio_helper.rs"
    assert source.exists(), "Initial launch and watchdog must share one helper configuration"
    binary = tmp_path / "helper-test"
    subprocess.run(["rustc", "--edition=2021", "--test", str(source), "-o", str(binary)], check=True, capture_output=True, text=True)
    subprocess.run([str(binary)], check=True, capture_output=True, text=True)
