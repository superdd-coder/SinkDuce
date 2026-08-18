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
    assert 'data-meeting-mode="studio"' not in view


def test_studio_live_partial_only_while_recording():
    """Studio must not paint a stuck Live card from leftover currentPartial."""
    view = (MEETING / "meeting-view.tsx").read_text(encoding="utf-8")
    assert "studioPartialText" in view
    assert "partialText={studioPartialText}" in view
    assert "partialText={transcription.currentPartial}" not in view


def test_meeting_studio_stage_extracted():
    studio = (MEETING / "meeting-studio-stage.tsx").read_text(encoding="utf-8")
    view = (MEETING / "meeting-view.tsx").read_text(encoding="utf-8")
    assert "export function MeetingStudioStage" in studio
    assert 'data-meeting-mode="studio"' in studio
    assert "<MeetingTabs" in studio
    assert "<MediaBar" in studio
    assert "<MeetingQcFab" in studio
    assert "<MeetingStudioStage" in view
    assert 'data-meeting-mode="studio"' not in view
    assert "<MeetingTabs" not in view
    assert not any(line.lstrip().startswith("<MediaBar") for line in view.splitlines())


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
    assert (
        'from "./file-detail-parts"' in dialog
        or 'from "./file-detail-parts"' in (DETAIL / "file-detail-side-rail.tsx").read_text(
            encoding="utf-8"
        )
    )


def test_file_detail_main_pane_extracted():
    pane = (DETAIL / "file-detail-main-pane.tsx").read_text(encoding="utf-8")
    dialog = (DETAIL / "file-detail-dialog.tsx").read_text(encoding="utf-8")
    assert "export function FileDetailMainPane" in pane
    assert 'value="raw"' in pane
    assert 'value="source"' in pane
    assert 'value="summary"' in pane
    assert 'value="chunks"' in pane
    assert 'value="ingest"' in pane
    assert "developerMode" in pane
    assert "<RawFileViewer" in pane
    assert "<FileDetailMainPane" in dialog
    assert "<RawFileViewer" not in dialog
    assert 'value="chunks"' not in dialog


def test_file_detail_side_rail_extracted():
    rail = (DETAIL / "file-detail-side-rail.tsx").read_text(encoding="utf-8")
    dialog = (DETAIL / "file-detail-dialog.tsx").read_text(encoding="utf-8")
    assert "export function FileDetailSideRail" in rail
    assert "pm-ws-side" in rail
    assert "Metadata" in rail
    assert "pm-ws-side-actions" in rail
    assert "<FileDetailSideRail" in dialog
    assert "pm-ws-side-actions" not in dialog
    assert "Could not load file management metadata." not in dialog


def test_file_detail_overlays_extracted():
    overlays = (DETAIL / "file-detail-overlays.tsx").read_text(encoding="utf-8")
    dialog = (DETAIL / "file-detail-dialog.tsx").read_text(encoding="utf-8")
    assert "export function FileDetailTitleChrome" in overlays
    assert "export function FileDetailRollbackDialog" in overlays
    assert "pm-ws-chrome" in overlays
    assert "Roll back to this version?" in overlays
    assert "<FileDetailTitleChrome" in dialog
    assert "<FileDetailRollbackDialog" in dialog
    assert "pm-ws-chrome" not in dialog
    assert "Roll back to this version?" not in dialog


def test_meeting_view_overlays_extracted():
    overlays = (MEETING / "meeting-view-overlays.tsx").read_text(encoding="utf-8")
    view = (MEETING / "meeting-view.tsx").read_text(encoding="utf-8")
    assert "export function MeetingViewOverlays" in overlays
    assert "Delete meeting?" in overlays
    assert "Re-transcribe meeting?" in overlays
    assert "pm-meeting-section-tip" in overlays
    assert "<MeetingViewOverlays" in view
    assert "Delete meeting?" not in view
    assert "Re-transcribe meeting?" not in view
    assert "pm-meeting-section-tip" not in view
