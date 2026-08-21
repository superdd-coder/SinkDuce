"""WKWebView/Safari getUserMedia can throw OverconstrainedError: Invalid constraint.

Other users' Macs hit this on Record; the developer machine often already has
mic TCC + a default device that satisfies WebKit's implicit constraints.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
MIC_TS = FRONTEND / "src" / "lib" / "microphone.ts"
RECORDER_TS = FRONTEND / "src" / "hooks" / "use-audio-recorder.ts"
MEETING_VIEW = FRONTEND / "src" / "components" / "meeting" / "meeting-view.tsx"


def _node(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["node", "--experimental-strip-types", "--no-warnings", "-e", script],
        capture_output=True,
        text=True,
        cwd=str(FRONTEND),
        check=False,
    )


def test_microphone_helper_module_exists():
    assert MIC_TS.is_file(), "frontend/src/lib/microphone.ts must exist"


_DETECT = r"""
import { isInvalidMediaConstraint } from "./src/lib/microphone.ts";
const over = { name: "OverconstrainedError", message: "Invalid constraint" };
const plain = new Error("Invalid constraint");
const other = new Error("NotAllowedError");
if (!isInvalidMediaConstraint(over)) process.exit(2);
if (!isInvalidMediaConstraint(plain)) process.exit(3);
if (isInvalidMediaConstraint(other)) process.exit(4);
if (isInvalidMediaConstraint(null)) process.exit(5);
process.stdout.write("ok");
"""


def test_detects_webkit_invalid_constraint():
    proc = _node(_DETECT)
    assert proc.returncode == 0, proc.stderr or proc.stdout
    assert "ok" in proc.stdout


_MSG = r"""
import { microphoneErrorMessage } from "./src/lib/microphone.ts";
const raw = microphoneErrorMessage({
  name: "OverconstrainedError",
  message: "Invalid constraint",
});
if (/invalid constraint/i.test(raw)) {
  console.error("leaked raw message", raw);
  process.exit(2);
}
if (!/microphone/i.test(raw)) {
  console.error("message should mention microphone", raw);
  process.exit(3);
}
const denied = microphoneErrorMessage({ name: "NotAllowedError", message: "Permission denied" });
if (/invalid constraint/i.test(denied)) process.exit(4);
if (!/microphone/i.test(denied)) process.exit(5);
const missing = microphoneErrorMessage({ name: "NotFoundError", message: "Requested device not found" });
if (!/microphone/i.test(missing)) process.exit(6);
const passthrough = microphoneErrorMessage(new Error("Screen share was cancelled."));
if (passthrough !== "Screen share was cancelled.") {
  console.error("lost system-audio message", passthrough);
  process.exit(7);
}
process.stdout.write("ok");
"""


def test_error_message_does_not_show_invalid_constraint():
    proc = _node(_MSG)
    assert proc.returncode == 0, proc.stderr or proc.stdout
    assert "ok" in proc.stdout


_RETRY = r"""
import { requestMicrophoneStream } from "./src/lib/microphone.ts";

const calls = [];
async function fakeGetUserMedia(constraints) {
  calls.push(JSON.parse(JSON.stringify(constraints)));
  if (calls.length < 3) {
    const err = new Error("Invalid constraint");
    err.name = "OverconstrainedError";
    throw err;
  }
  return { id: "mic-ok", constraints };
}

const stream = await requestMicrophoneStream(fakeGetUserMedia);
if (stream.id !== "mic-ok") process.exit(2);
if (calls.length !== 3) {
  console.error("expected 3 attempts", calls);
  process.exit(3);
}
if (calls[0].audio !== true) process.exit(4);
if (calls[1].audio.echoCancellation !== false) process.exit(5);
if (JSON.stringify(calls[2].audio) !== "{}") process.exit(6);
if (calls.some((c) => c.video !== false)) process.exit(7);
process.stdout.write(JSON.stringify(calls));
"""


def test_request_microphone_retries_relaxed_constraints():
    proc = _node(_RETRY)
    assert proc.returncode == 0, proc.stderr or proc.stdout
    calls = json.loads(proc.stdout)
    assert len(calls) == 3


_DENIED = r"""
import { requestMicrophoneStream } from "./src/lib/microphone.ts";
async function deny() {
  const err = new Error("Permission denied");
  err.name = "NotAllowedError";
  throw err;
}
try {
  await requestMicrophoneStream(deny);
  process.exit(2);
} catch (e) {
  if (e.name !== "NotAllowedError") process.exit(3);
}
process.stdout.write("ok");
"""


def test_request_microphone_does_not_retry_permission_denied():
    proc = _node(_DENIED)
    assert proc.returncode == 0, proc.stderr or proc.stdout


_MIME = r"""
import { pickRecorderMimeType } from "./src/lib/microphone.ts";
const safari = (t) => t.startsWith("audio/mp4");
const chrome = (t) => t.includes("webm");
const none = () => false;
if (pickRecorderMimeType(chrome) !== "audio/webm;codecs=opus") process.exit(2);
if (pickRecorderMimeType(safari) !== "audio/mp4") process.exit(3);
if (pickRecorderMimeType(none) !== "") process.exit(4);
process.stdout.write("ok");
"""


def test_recorder_mime_falls_back_to_mp4_on_safari():
    proc = _node(_MIME)
    assert proc.returncode == 0, proc.stderr or proc.stdout


def test_audio_recorder_uses_microphone_helper():
    src = RECORDER_TS.read_text(encoding="utf-8")
    assert "requestMicrophoneStream" in src
    assert "microphoneErrorMessage" in src
    assert "pickRecorderMimeType" in src
    assert "getUserMedia({ audio: true })" not in src


def test_desktop_system_audio_avoids_blob_worklet():
    """WKWebView addModule(blob:) throws TypeError: Load failed."""
    helper = (
        ROOT / "frontend" / "src" / "lib" / "desktop-system-audio.ts"
    ).read_text(encoding="utf-8")
    assert "createObjectURL" not in helper
    assert "createScriptProcessor" in helper
    assert "Load failed" in helper or "isWkLoadFailed" in helper
    assert "warmupDesktopSystemAudio" in helper
    assert "/api/desktop/pcm" in helper
    assert "/api/desktop/sysaudio-warmup" in helper
    assert "helper is not running" in helper


def test_desktop_helper_feeds_raw_pcm_not_mediastream_roundtrip():
    """WKWebView ScriptProcessor→MediaStreamDestination is often silent.
    Desktop capture must take s16le from the helper fetch and push it to
    pcmChunksRef / live captions without a second Web Audio graph.
    """
    helper = (
        ROOT / "frontend" / "src" / "lib" / "desktop-system-audio.ts"
    ).read_text(encoding="utf-8")
    src = RECORDER_TS.read_text(encoding="utf-8")
    assert "onPcm" in helper
    assert "handlers?.onPcm" in helper or "handlers.onPcm" in helper
    desktop_idx = src.find("if (desktop)")
    else_idx = src.find("} else {", desktop_idx)
    desktop_block = src[desktop_idx:else_idx]
    assert "onPcm:" in desktop_block
    assert "attachPcmScriptProcessor" not in desktop_block
    assert "createMediaStreamSource" not in desktop_block


def test_desktop_pcm_capture_skips_blob_worklet():
    """Desktop PCM must not call audioWorklet.addModule(blob:) — WKWebView
    throws TypeError: Load failed and the toast shows that raw string.
    """
    src = RECORDER_TS.read_text(encoding="utf-8")
    assert "audioWorklet.addModule" in src
    assert "if (!desktop)" in src
    gated = src.split("if (!desktop)", 1)[1][:1200]
    assert "addModule" in gated
    desktop_idx = src.find("if (desktop)")
    else_idx = src.find("} else {", desktop_idx)
    desktop_block = src[desktop_idx:else_idx]
    assert "addModule" not in desktop_block
    assert "createObjectURL" not in desktop_block


def test_desktop_recording_skips_webkit_microphone():
    """WKWebView getUserMedia never shows the macOS TCC dialog and stays
    OverconstrainedError even after the user enables Microphone in Settings.
    Desktop must record via the native helper only.
    """
    src = RECORDER_TS.read_text(encoding="utf-8")
    desktop_idx = src.find("if (desktop)")
    else_idx = src.find("} else {", desktop_idx)
    mic_idx = src.find("requestMicrophoneStream(", desktop_idx)
    assert desktop_idx != -1
    assert else_idx > desktop_idx
    assert mic_idx > else_idx
    desktop_block = src[desktop_idx:else_idx]
    assert "startDesktopSystemAudio" in desktop_block
    assert "requestMicrophoneStream" not in desktop_block
    assert "getUserMedia" not in desktop_block


_WAV = r"""
import { pcmInt16ToWav } from "./src/lib/microphone.ts";
const samples = new Int16Array(16000);
for (let i = 0; i < samples.length; i++) samples[i] = i % 2 ? 1000 : -1000;
const blob = pcmInt16ToWav([samples], 16000);
const buf = new Uint8Array(await blob.arrayBuffer());
if (blob.type !== "audio/wav") process.exit(2);
if (buf.length !== 44 + 16000 * 2) {
  console.error("size", buf.length);
  process.exit(3);
}
const ascii = String.fromCharCode(...buf.subarray(0, 12));
if (!ascii.startsWith("RIFF") || ascii.slice(8) !== "WAVE") {
  console.error("header", ascii);
  process.exit(4);
}
const rate = buf[24] | (buf[25] << 8) | (buf[26] << 16) | (buf[27] << 24);
if (rate !== 16000) process.exit(5);
process.stdout.write("ok");
"""


def test_pcm_int16_encodes_16k_mono_wav():
    proc = _node(_WAV)
    assert proc.returncode == 0, proc.stderr or proc.stdout


def test_client_persists_live_pcm():
    client = (ROOT / "frontend" / "src" / "api" / "meeting.ts").read_text(encoding="utf-8")
    view = MEETING_VIEW.read_text(encoding="utf-8")
    assert "appendRecordingPcm" in client
    assert "finalizeMeetingRecording" in client
    assert "appendRecordingPcm" in view
    assert "finalizeMeetingRecording" in view
    assert "recording-pcm" in client


def test_recorder_buffers_pcm_for_upload():
    src = RECORDER_TS.read_text(encoding="utf-8")
    assert "pcmInt16ToWav" in src
    assert "pcmChunksRef" in src
    view = MEETING_VIEW.read_text(encoding="utf-8")
    assert "recording.webm" not in view
    assert "audioBlob.type" in view


def test_sysaudio_helper_requests_microphone_tcc():
    swift_path = ROOT / "desktop" / "sysaudio" / "main.swift"
    build_path = ROOT / "desktop" / "sysaudio" / "build.sh"
    if not swift_path.is_file():
        pytest.skip("desktop/ is local-only")
    swift = swift_path.read_text(encoding="utf-8")
    build = build_path.read_text(encoding="utf-8")
    assert "AVFoundation" in build
    assert "AVCaptureDevice" in swift
    assert "requestAccess" in swift
    assert "NSApp.activate(" not in swift
    assert "mic only" in swift or "tap skipped" in swift
    assert "AVAudioEngine" in swift
    assert "microphone_permission" in swift
    assert "runOnMain" in swift
    assert "/warmup" in swift
    # Input tap is silent on macOS unless the node is in the graph.
    assert "mainMixerNode" in swift
    assert "outputVolume" in swift
    # Warmup must not start+stop capture (that tears down the aggregate).
    warmup = swift.split("func warmup()", 1)[1].split("private func startTap", 1)[0]
    assert "self.stop()" not in warmup
    assert "mic.stop()" not in warmup
    assert "try start()" not in warmup
    # Mic must be written even if the Core Audio tap delivers zeros.
    assert "flushMix" in swift or "startWriter" in swift
    # Never pad a 20ms frame with zeros when both rings are short — that
    # chops phonemes and the next ASR sentence starts with the previous tail.
    flush = swift.split("private func flushMix()", 1)[1].split("private func startTap", 1)[0]
    assert "repeating: 0" not in flush
    assert "available" in flush or "have <" in flush


def test_sysaudio_helper_rebuilds_deaf_process_tap():
    """macOS 26.5 Process Tap can deliver zeros while speakers still play.

    Conservative recovery: rebuild tap+aggregate on a control queue, keep
    mic/writer running, one rebuild per silence streak, give up after 2 fails.
    """
    swift_path = ROOT / "desktop" / "sysaudio" / "main.swift"
    if not swift_path.is_file():
        pytest.skip("desktop/ is local-only")
    swift = swift_path.read_text(encoding="utf-8")

    assert "sinkduce.sysaudio.control" in swift
    assert 'reason: "watchdog"' in swift
    assert 'reason: "output-uid"' in swift
    assert "tap rebuild reason=" in swift
    assert "giving up" in swift
    assert "tap rebuild" in swift
    assert "25" in swift
    assert "0.01" in swift
    assert "0.005" in swift
    assert "tap=" in swift
    assert "t=" in swift
    assert "kAudioHardwarePropertyDefaultOutputDevice" in swift
    assert "AudioObjectAddPropertyListenerBlock" in swift
    # Device-list / sample-rate listeners are too noisy to trigger rebuild.
    assert "kAudioHardwarePropertyDevices" not in swift
    assert "rebuildTap" in swift or "func rebuildTap" in swift

    rebuild = swift.split("func rebuildTap", 1)[1].split("\n    private func ", 1)[0]
    assert "mic.stop" not in rebuild
    assert "stopWriter" not in rebuild
    assert "destroyAggregate" in rebuild
    assert "destroyTap" in rebuild
    assert "startTap" in rebuild
    # Full HAL teardown, not IOProc-only restart.
    assert rebuild.find("destroyAggregate") < rebuild.find("startTap")
    assert rebuild.find("destroyTap") < rebuild.find("startTap")


def test_meeting_view_toasts_start_recording_error():
    """React state is stale right after await startRecording(); toast must use the return value."""
    src = MEETING_VIEW.read_text(encoding="utf-8")
    assert "startRecording" in src
    # Must not toast recorder.error immediately after await (stale closure).
    start = src.find("handleStartRecording")
    chunk = src[start : start + 1800]
    assert "recorder.error" not in chunk
    assert "toast.error" in chunk
    assert 't("meeting.noAudioDetected")' in src
