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


def test_meeting_view_toasts_start_recording_error():
    """React state is stale right after await startRecording(); toast must use the return value."""
    src = MEETING_VIEW.read_text(encoding="utf-8")
    assert "startRecording" in src
    # Must not toast recorder.error immediately after await (stale closure).
    start = src.find("handleStartRecording")
    chunk = src[start : start + 1800]
    assert "recorder.error" not in chunk
    assert "toast.error" in chunk
