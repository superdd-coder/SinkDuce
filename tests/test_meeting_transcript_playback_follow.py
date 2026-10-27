"""Group overlay / transcript playback follow: keep the playing sentence focused."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PANEL = ROOT / "frontend" / "src" / "components" / "meeting" / "transcript-panel.tsx"
GROUP = ROOT / "frontend" / "src" / "components" / "meeting" / "meeting-group-stage.tsx"
MEDIA = ROOT / "frontend" / "src" / "components" / "meeting" / "media-bar.tsx"
LIB = ROOT / "frontend" / "src" / "lib" / "transcript-playback.ts"


def test_playing_index_helper_exists():
    src = LIB.read_text(encoding="utf-8")
    assert "def findPlayingSegmentIndex" not in src  # TS, not Python
    assert "export function findPlayingSegmentIndex" in src


def test_transcript_tab_follows_playing_sentence():
    src = PANEL.read_text(encoding="utf-8")
    assert "findPlayingSegmentIndex" in src
    assert "scrollSegmentIntoView" in src
    assert "playbackTime <= 0" not in src
    assert 'origIdx === playingIdx && "is-playing"' in src
    assert "focusedIdx !== origIdx && \"is-playing\"" not in src


def test_group_overlay_does_not_seek_to_zero_before_cite():
    src = GROUP.read_text(encoding="utf-8")
    assert "seekPlayer(0)" not in src
    assert "onTimeUpdate={setPlaybackTime}" in src
    assert "playbackTime={playbackTime}" in src
    assert "flex min-h-0 flex-1 flex-col overflow-hidden" in src


def test_seek_to_emits_time_update():
    src = MEDIA.read_text(encoding="utf-8")
    assert "pendingSeekRef" in src
    seek = src.split("seekTo(time: number, end?: number)", 1)[1].split("}))", 1)[0]
    assert "onTimeUpdateRef.current?.(t)" in seek
