"""Pure tests for live capture waveform scaling (use-audio-recorder binsToLevels)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


_NODE = r"""
function binsToLevels(bins, barCount = 24) {
  const n = bins.length;
  if (n === 0 || barCount <= 0) return Array.from({ length: barCount }, () => 0);
  const out = [];
  for (let i = 0; i < barCount; i++) {
    const start = Math.floor((i * n) / barCount);
    const end = Math.max(start + 1, Math.floor(((i + 1) * n) / barCount));
    let sum = 0;
    for (let j = start; j < end; j++) sum += bins[j] ?? 0;
    const avg = sum / (end - start);
    const v = Math.max(0, (avg - 8) / 180);
    out.push(Math.min(1, v));
  }
  return out;
}

const silence = new Array(64).fill(0);
const loud = new Array(64).fill(200);
const s = binsToLevels(silence, 8);
const l = binsToLevels(loud, 8);
if (!s.every((v) => v === 0)) {
  console.error("silence not flat", s);
  process.exit(2);
}
if (!l.every((v) => v > 0.9)) {
  console.error("loud not high", l);
  process.exit(3);
}
const mid = binsToLevels([0, 0, 100, 100, 0, 0, 50, 50], 4);
if (mid.length !== 4) process.exit(4);
process.stdout.write(JSON.stringify({ s, l, mid }));
"""


def test_bins_to_levels_silence_flat_loud_high():
    proc = subprocess.run(
        ["node", "-e", _NODE],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(ROOT / "frontend"),
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    assert "s" in proc.stdout


def test_recorder_exports_bins_to_levels():
    src = (ROOT / "frontend" / "src" / "hooks" / "use-audio-recorder.ts").read_text(
        encoding="utf-8"
    )
    assert "export function binsToLevels" in src
    assert "AUDIO_LEVEL_BAR_COUNT" in src


def test_desktop_recording_uses_system_audio_helper():
    """WKWebView cannot Share audio; desktop mixes ScreenCaptureKit helper PCM."""
    src = (ROOT / "frontend" / "src" / "hooks" / "use-audio-recorder.ts").read_text(
        encoding="utf-8"
    )
    helper = (
        ROOT / "frontend" / "src" / "lib" / "desktop-system-audio.ts"
    ).read_text(encoding="utf-8")
    assert "startDesktopSystemAudio" in src
    assert "if (desktop)" in src
    assert "getDisplayMedia" in src
    desktop_idx = src.find("if (desktop)")
    helper_idx = src.find("startDesktopSystemAudio", desktop_idx)
    display_idx = src.find("getDisplayMedia", desktop_idx)
    assert desktop_idx != -1
    assert helper_idx > desktop_idx
    assert display_idx > helper_idx
    assert "system_audio" in helper
    assert "/pcm" in helper
