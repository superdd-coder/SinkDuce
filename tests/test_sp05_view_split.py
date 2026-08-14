"""SP-05 first slice: extract existing subcomponents from large views."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MEETING = ROOT / "frontend" / "src" / "components" / "meeting"
DETAIL = ROOT / "frontend" / "src" / "components" / "file-mgmt" / "file-detail"


def test_capture_mini_player_extracted():
    player = (MEETING / "capture-mini-player.tsx").read_text(encoding="utf-8")
    view = (MEETING / "meeting-view.tsx").read_text(encoding="utf-8")
    assert "export const CaptureMiniPlayer" in player
    assert "export type CaptureMiniPlayerHandle" in player
    assert 'from "./capture-mini-player"' in view
    assert "function CaptureMiniPlayer" not in view
    assert "export type CaptureMiniPlayerHandle" not in view


def test_file_detail_utils_extracted():
    utils = (DETAIL / "file-detail-utils.ts").read_text(encoding="utf-8")
    dialog = (DETAIL / "file-detail-dialog.tsx").read_text(encoding="utf-8")
    assert "export function parseFileIdFromSource" in utils
    assert "export function buildTimeline" in utils
    assert "function parseFileIdFromSource" not in dialog
    assert "function buildTimeline" not in dialog
    assert 'from "./file-detail-utils"' in dialog


def test_meeting_capture_stages_extracted():
    stages = (MEETING / "meeting-capture-stages.tsx").read_text(encoding="utf-8")
    view = (MEETING / "meeting-view.tsx").read_text(encoding="utf-8")
    assert "export function MeetingCaptureStages" in stages
    assert 'data-meeting-mode="empty"' in stages
    assert 'data-meeting-mode="audio-ready"' in stages
    assert 'data-meeting-mode="transcribing"' in stages
    assert 'data-meeting-mode="speakers"' in stages
    assert 'data-meeting-mode="live"' in stages
    assert "<MeetingCaptureStages" in view
    assert 'data-meeting-mode="empty"' not in view
    assert 'data-meeting-mode="studio"' in view


def test_file_detail_parts_extracted():
    parts = (DETAIL / "file-detail-parts.tsx").read_text(encoding="utf-8")
    dialog = (DETAIL / "file-detail-dialog.tsx").read_text(encoding="utf-8")
    for name in (
        "LogMsgDeleteButton",
        "ActionMenuItem",
        "SummarySection",
        "PathRow",
        "NodeRow",
    ):
        assert f"export function {name}" in parts
        assert f"function {name}" not in dialog
    assert 'from "./file-detail-parts"' in dialog
