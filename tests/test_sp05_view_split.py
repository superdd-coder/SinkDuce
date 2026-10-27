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


def test_file_detail_and_chat_source_do_not_subscribe_whole_app_store():
    """Chat token writes must not re-render the file preview (flicker)."""
    dialog = (DETAIL / "file-detail-dialog.tsx").read_text(encoding="utf-8")
    src_panel = (
        ROOT / "frontend" / "src" / "components" / "chat" / "source-detail-panel.tsx"
    ).read_text(encoding="utf-8")
    raw = (
        ROOT / "frontend" / "src" / "components" / "file-mgmt" / "raw-file-viewer.tsx"
    ).read_text(encoding="utf-8")
    assert "useAppStore()" not in dialog
    assert "useAppStore()" not in src_panel
    assert "ParseTextViewer" in src_panel
    assert "TiptapEditor" not in src_panel
    assert 'mo.observe(root, { childList: true, subtree: true })' not in raw


def test_folder_view_does_not_subscribe_whole_file_mgmt_store():
    """Other-file ingest polls must not re-render FolderView (chunks flicker)."""
    view = (
        ROOT
        / "frontend"
        / "src"
        / "components"
        / "file-mgmt"
        / "folder-view"
        / "index.tsx"
    ).read_text(encoding="utf-8")
    dialog = (DETAIL / "file-detail-dialog.tsx").read_text(encoding="utf-8")
    assert "useFileMgmtStore()" not in view
    assert "useShallow" in view
    assert "memo(" in dialog


def test_parse_text_viewer_is_lightweight():
    viewer = (DETAIL / "parse-text-viewer.tsx").read_text(encoding="utf-8")
    assert "pm-ws-parse-pre" in viewer
    assert "splitExtractParts" in viewer
    assert "ChunkMd" not in viewer
    assert "useEditor" not in viewer
    assert "TiptapEditor" not in viewer


def test_raw_viewer_can_preview_images():
    raw = (
        ROOT / "frontend" / "src" / "components" / "file-mgmt" / "raw-file-viewer.tsx"
    ).read_text(encoding="utf-8")
    assert '"png"' in raw
    assert "isImagePreviewFilename" in raw


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
    assert "ParseTextViewer" in pane
    assert "TiptapEditor" not in pane
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


def test_people_picker_shows_note_while_typing_and_add_saves():
    picker = (
        ROOT / "frontend" / "src" / "components" / "meeting" / "people-picker.tsx"
    ).read_text(encoding="utf-8")
    css = (ROOT / "frontend" / "src" / "index.css").read_text(encoding="utf-8")
    assert "needDisambiguator" not in picker
    assert "Type a name to add" not in picker
    assert "Type a name to add the first person" not in picker
    assert "query.trim()" in picker
    assert "pm-people-picker-note" in picker
    name_css = css.split(".pm-people-picker-name {", 1)[1].split("}", 1)[0]
    assert "font-weight: 300" in name_css


def test_speakers_tab_opens_people_manager_inline():
    panel = (
        ROOT / "frontend" / "src" / "components" / "meeting" / "transcript-panel.tsx"
    ).read_text(encoding="utf-8")
    assert "PeopleManager" in panel
    assert "pm-speakers-people-btn" in panel
    assert 'setSidebarView' not in panel
    assert "llm_provider" not in panel


def test_people_delete_pill_expands_to_confirm():
    people = (
        ROOT / "frontend" / "src" / "components" / "llm-provider" / "people-manager.tsx"
    ).read_text(encoding="utf-8")
    css = (ROOT / "frontend" / "src" / "index.css").read_text(encoding="utf-8")
    assert "pm-people-delete" in people
    assert "pm-people-delete-ask" in people
    assert "is-armed" in people
    assert ".pm-people-delete {" in css
    assert ".pm-people-delete.is-armed" in css
    assert "max-width" in css.split(".pm-people-delete {", 1)[1][:800]


def test_people_and_hot_words_rails_use_collection_name_type():
    """People / Hot words list names match Collections (pm-rail-name, 300 serif)."""
    people = (
        ROOT / "frontend" / "src" / "components" / "llm-provider" / "people-manager.tsx"
    ).read_text(encoding="utf-8")
    hot = (
        ROOT / "frontend" / "src" / "components" / "llm-provider" / "hot-words-manager.tsx"
    ).read_text(encoding="utf-8")
    css = (ROOT / "frontend" / "src" / "index.css").read_text(encoding="utf-8")
    assert "pm-rail-name" in people
    assert "pm-people-meeting-title pm-rail-name" in people
    assert "pm-rail-name" in hot
    assert ".pm-people-meeting-title {" in css
    rail_block = css.split(".pm-rail-name,", 1)[1].split("}", 1)[0]
    assert "pm-people-meeting-title" in rail_block
    assert "font-weight: 300" in css.split(".pm-rail-name,", 1)[1].split(".pm-rail-list-shell", 1)[0]


def test_meeting_ingest_menu_uses_rail_name_type():
    """Choose-a-collection dropdown names match Collections rail (serif 300)."""
    tabs = (MEETING / "meeting-tabs.tsx").read_text(encoding="utf-8")
    css = (ROOT / "frontend" / "src" / "index.css").read_text(encoding="utf-8")
    assert "pm-meeting-ingest-menu-name" in tabs
    assert ".pm-meeting-ingest-menu-name {" in css
    name_css = css.split(".pm-meeting-ingest-menu-name {", 1)[1].split("}", 1)[0]
    assert "pm-ff-prose" in name_css
    assert "font-weight: 300" in name_css
    assert "font-size: 14px" in name_css


def test_file_detail_and_folder_toolbar_menus_share_type_and_motion():
    """File-detail Archive/Delete use folder title type; folder menus use SoftMenu + inset hover."""
    parts = (DETAIL / "file-detail-parts.tsx").read_text(encoding="utf-8")
    toolbar = (
        ROOT
        / "frontend"
        / "src"
        / "components"
        / "file-mgmt"
        / "folder-view"
        / "toolbar.tsx"
    ).read_text(encoding="utf-8")
    css = (ROOT / "frontend" / "src" / "index.css").read_text(encoding="utf-8")
    assert "pm-files-menu-item-title" in parts
    assert "pm-files-menu-item-desc" in parts
    assert "SoftMenu" in toolbar
    assert "openMenu === \"archive\" && (" not in toolbar
    menu_css = css.split(".pm-files-menu {", 1)[1].split("}", 1)[0]
    assert "padding: 4px 0" not in menu_css
    assert "padding: 4px" in menu_css
    item_css = css.split(".pm-files-menu-item {", 1)[1].split("}", 1)[0]
    assert "border-radius" in item_css
    parts = (DETAIL / "file-detail-parts.tsx").read_text(encoding="utf-8")
    rail = (DETAIL / "file-detail-side-rail.tsx").read_text(encoding="utf-8")
    assert 'from "@/components/ui/menu"' not in parts
    assert 'cn("pm-files-menu-item"' in parts
    assert "pm-files-menu" in rail
    title_css = css.split(".pm-files-menu-item-title {", 1)[1].split("}", 1)[0]
    desc_css = css.split(".pm-files-menu-item-desc {", 1)[1].split("}", 1)[0]
    assert "pm-t-title" in title_css
    assert "pm-t-meta" in desc_css
    assert "font-weight: 300" in desc_css
    ws_desc = css.split(".pm-workspace .pm-files-menu-item-desc {", 1)[1].split("}", 1)[0]
    assert "font-weight: 300" in ws_desc
    assert "font-size: 11px" in ws_desc
    assert "!important" in ws_desc


def test_folder_message_reopen_lives_in_toolbar_row():
    """Collapsed Messages pill sits in the toolbar band, right-aligned with fade."""
    folder = (
        ROOT
        / "frontend"
        / "src"
        / "components"
        / "file-mgmt"
        / "folder-view"
        / "index.tsx"
    ).read_text(encoding="utf-8")
    css = (ROOT / "frontend" / "src" / "index.css").read_text(encoding="utf-8")
    toolbar_block = folder.split("pm-files-browser-toolbar", 1)[1].split(
        "pm-files-browser-body", 1
    )[0]
    assert "pm-files-msg-reopen" in toolbar_block
    assert "pm-files-msg-reopen" not in folder.split("</aside>", 1)[1]
    band = css.split(".pm-files-browser-toolbar {", 1)[1].split("}", 1)[0]
    assert "align-items: center" in band
    assert "--pm-files-tb-inset" in band
    reopen = css.split(".pm-files-msg-reopen {", 1)[1].split("}", 1)[0]
    assert "top: 4px" not in reopen
    assert "right: 4px" not in reopen
    assert "opacity" in reopen
    assert "margin-left: auto" in reopen
    pill = css.split(".pm-files-toolbar {", 1)[1].split("}", 1)[0]
    assert "width: 100%" in pill
    assert "0 0 0 1px" not in pill
    toolbar_src = (
        ROOT
        / "frontend"
        / "src"
        / "components"
        / "file-mgmt"
        / "folder-view"
        / "toolbar.tsx"
    ).read_text(encoding="utf-8")
    assert "trailing" in toolbar_src
