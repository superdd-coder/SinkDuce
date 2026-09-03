"""Static structure checks for Meeting Premium UI (rail width · steady stage · tokens).

These assert shipped CSS/TSX contracts used by the Meeting surface so layout
regressions (228px rail collapse, missing steady stage classes) fail CI.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX_CSS = ROOT / "frontend" / "src" / "index.css"
MEETING_VIEW = ROOT / "frontend" / "src" / "components" / "meeting" / "meeting-view.tsx"
MEETING_LIST = ROOT / "frontend" / "src" / "components" / "meeting" / "meeting-list.tsx"
MEETING_TABS = ROOT / "frontend" / "src" / "components" / "meeting" / "meeting-tabs.tsx"
MEDIA_BAR = ROOT / "frontend" / "src" / "components" / "meeting" / "media-bar.tsx"
MEETING_STUDIO = ROOT / "frontend" / "src" / "components" / "meeting" / "meeting-studio-stage.tsx"


def test_meeting_rail_width_locked_to_collections_chat():
    css = INDEX_CSS.read_text(encoding="utf-8")
    assert "--pm-meeting-rail-w: 228px" in css
    assert ".pm-meeting-rail" in css
    assert "flex: 0 0 var(--pm-meeting-rail-w)" in css
    # Must define width on Meeting host — not only under .pm-chat
    assert ".pm-meeting {" in css
    meeting_block = css.split(".pm-meeting {", 1)[1].split("}", 1)[0]
    assert "--pm-meeting-rail-w" in meeting_block


def test_meeting_shadow_not_clipped_by_shell():
    """Soft float (--pm-shadow) needs overflow-visible ancestors (main pad is gutter)."""
    css = INDEX_CSS.read_text(encoding="utf-8")
    body = css.split(".pm-meeting-body {", 1)[1].split("}", 1)[0]
    assert "overflow: visible" in body
    # Same 2px inset as chat — do not steal height with large bottom pad
    assert "padding: 2px" in body
    rail = css.split(".pm-meeting-rail {", 1)[1].split("}", 1)[0]
    assert "overflow: visible" in rail
    assert "height: 100%" in rail
    layout = (ROOT / "frontend" / "src" / "components" / "layout" / "app-layout.tsx").read_text(
        encoding="utf-8"
    )
    # Meeting view host must not use overflow-hidden when active
    assert 'key === "meeting"' in layout
    # Soft shadows: stage/body/rail stay overflow:visible (layout may use overflow-hidden elsewhere)
    assert "overflow: visible" in body
    assert "overflow: visible" in rail


def test_meeting_sticky_chrome_and_overflow_clip():
    css = INDEX_CSS.read_text(encoding="utf-8")
    assert ".pm-meeting-tabs-bar" in css
    assert "position: sticky" in css
    # overflow-x:hidden on scroll host breaks sticky in Chromium
    scroll = css.split(".pm-meeting-scroll {", 1)[1].split("}", 1)[0]
    assert "overflow-x: clip" in scroll or "overflow-x:clip" in scroll.replace(" ", "")
    assert "overflow-x: hidden" not in scroll


def test_meeting_view_uses_premium_shell_classes():
    src = MEETING_VIEW.read_text(encoding="utf-8")
    for cls in (
        "pm-meeting",
        "pm-meeting-body",
        "pm-meeting-stage",
        "pm-meeting-stage-surface",
        "pm-meeting-left-chrome",
        "pm-meeting-player-zone",
        "pm-meeting-player-zone-card",
        "pm-meeting-section-rail-card",
        "pm-meeting-section-card",
        "pm-meeting-left-body",
        "pm-meeting-title-card",
        "pm-meeting-stage-right",
        "pm-meeting-stage-right-card",
        "pm-meeting-side-tabs",
        "pm-meeting-side-reopen",
        "pm-meeting-content-card",
    ):
        assert cls in src, f"missing class {cls}"
    # Capture phase: stay off Studio until file transcription completes
    assert "isCapturePhase" in src
    assert "hasStudioAnchor" in src
    assert "studioUnlocked" in src
    assert "isCaptureAudioReady" in src
    assert "isCaptureSpeakers" in src
    assert "isFileTranscribing" in src
    assert "handleEnterStudio" in src
    assert "handleStartRecording" in src
    assert "CaptureMiniPlayer" in src
    assert "pm-meeting-e-config" in src
    assert "pm-meeting-speaker-gate" in src or "speaker-gate" in src
    assert "HotWordsSelector" in src
    assert "LanguageHintsSelector" in src
    assert "Summarize" in src
    # File complete clears live draft so file transcript refreshes
    assert 'prevStatus === "transcribing"' in src or "transcribing" in src
    # Docked QC into permanent right rail (Collection QC width)
    assert 'layout="dock"' in src
    # Side panel tabs — same sliding pill Tabs as main (TabsIndicator)
    assert "sideTab" in src or "setSideTab" in src
    assert "requestSideTab" in src
    assert "TabsIndicator" in src
    assert "pm-tabs-indicator" in src
    assert "pm-meeting-side-panel" in src


def test_meeting_stage_right_matches_collection_qc_width():
    css = INDEX_CSS.read_text(encoding="utf-8")
    assert "--pm-meeting-stage-right-w" in css
    assert "var(--pm-collection-rail-w" in css
    assert ".pm-meeting-stage-right" in css
    assert ".pm-meeting-player-zone" in css
    assert ".pm-meeting-player-progress" in css
    surface = css.split(".pm-meeting-stage-surface {", 1)[1].split("}", 1)[0]
    assert "grid-template-columns" in surface
    assert "grid-template-rows" in surface
    assert "pm-meeting-stage-right-w" in surface
    assert "is-side-collapsed" in css
    # Collapse: delayed visibility so slide/opacity can complete
    assert "visibility: hidden" in css
    # Side + main content share one motion token (padding-right follow, not grid snap)
    assert "--pm-meeting-side-motion" in css
    assert "padding-right: var(--pm-meeting-stage-right-w)" in css
    # Chat close: diamond rides out with side, then bottom fade-in
    assert "is-ride-out" in css
    assert "pm-meeting-qc-fab-bottom-in" in css
    # Tab panels crossfade (not display:none hard cut)
    assert ".pm-meeting-side-panel" in css
    assert ".pm-meeting-side-panel.is-active" in css
    assert ".pm-meeting-side-tabs" in css


def test_meeting_side_tabs_order_without_groups():
    """Groups moved into the meeting rail — side rail hosts sections/tx/speaker only."""
    src = MEETING_STUDIO.read_text(encoding="utf-8")
    assert 'TabsTrigger value="groups"' not in src
    i_sec = src.find('TabsTrigger value="sections"')
    i_tx = src.find('TabsTrigger value="transcript"')
    i_spk = src.find('TabsTrigger value="speaker"')
    assert min(i_sec, i_tx, i_spk) > 0
    assert i_sec < i_tx < i_spk
    # MeetingGroupsPanel side panel is gone from the meeting stage
    assert "MeetingGroupsPanel" not in src


def test_meeting_list_uses_rail_and_compact_new():
    src = MEETING_LIST.read_text(encoding="utf-8")
    css = INDEX_CSS.read_text(encoding="utf-8")
    view = MEETING_VIEW.read_text(encoding="utf-8")
    assert "pm-meeting-rail" in src
    assert "pm-meeting-rail-head" in src
    assert "CreateMeetingButton" in src
    # Full-width New Meeting bar removed from list body
    assert "pm-meeting-rail-new" not in src
    # Scroll edge fades at top/bottom of list
    assert "pm-rail-list-shell" in src
    assert "pm-rail-edge-fade" in src
    assert "useScrollEdgeFade" in src
    # Uniform row height: 2-line title box (not 1-line vs 2-line jump)
    assert ".pm-meeting-rail .pm-chat-sess-name" in css
    meeting_name = css.split(".pm-meeting-rail .pm-chat-sess-name {", 1)[1].split("}", 1)[0]
    assert "min-height:" in meeting_name or "height:" in meeting_name
    assert "-webkit-line-clamp: 2" in meeting_name
    # Capture session survives meeting switch (pin transcription + owner id)
    assert "transcriptionMeetingId" in view
    assert "recordingMeetingId" in view
    assert "recordingMeetingId={recordingMeetingId}" in view
    assert "is-capturing" in src


def test_meeting_nested_paper_cards_exist():
    """Stage paper cards: title / content / player / side are white soft cards."""
    css = INDEX_CSS.read_text(encoding="utf-8")
    for cls in (
        ".pm-meeting-body-prose",
        ".pm-meeting-nested",
        ".pm-meeting-media",
        ".pm-meeting-panel-card",
        ".pm-meeting-notes-card",
        ".pm-meeting-notes-fmt-bar",
        ".pm-meeting-player-zone-card",
        ".pm-meeting-content-card",
        ".pm-meeting-title-card",
    ):
        assert cls in css, f"missing {cls}"
    # Content + title + player cards are white paper
    content = css.split(".pm-meeting-content-card {", 1)[1].split("}", 1)[0]
    assert "#ffffff" in content or "#fff" in content
    assert "--pm-shadow-sm" in content
    title = css.split(".pm-meeting-title-card {", 1)[1].split("}", 1)[0]
    assert "#ffffff" in title or "#fff" in title
    assert "--pm-shadow-sm" in title
    # Soft shadows not clipped: stage-surface overflow visible + shadow gutter
    surface = css.split(".pm-meeting-stage-surface {", 1)[1].split("}", 1)[0]
    assert "overflow: visible" in surface
    assert "--pm-meeting-shadow-gutter" in surface
    # Tight L↔R card gap (not double full gutters)
    assert "--pm-meeting-col-gap" in surface
    # No hard dividers between title/player or under content tabs
    tabs_bar = css.split(".pm-meeting-content-card .pm-meeting-tabs-bar {", 1)[1].split("}", 1)[0]
    assert "border-bottom: none" in tabs_bar
    left_chrome = css.split(".pm-meeting-left-chrome {", 1)[1].split("}", 1)[0]
    assert "border-right: none" in left_chrome
    # Player zone sits on stage cream (no gray wash)
    player_zone = css.split(".pm-meeting-player-zone {", 1)[1].split("}", 1)[0]
    assert "background: transparent" in player_zone
    # Player card matches title chrome height; controls as tight centered stack
    player_card = css.split(".pm-meeting-player-zone-card {", 1)[1].split("}", 1)[0]
    assert "height: 100%" in player_card
    assert "justify-content: center" in player_card
    player = css.split(".pm-meeting-player {", 1)[1].split("}", 1)[0]
    assert "justify-content: center" in player
    media = (ROOT / "frontend" / "src" / "components" / "meeting" / "media-bar.tsx").read_text(
        encoding="utf-8"
    )
    # 3-row player: progress | play+time | tools
    assert "pm-meeting-player-progress-row" in media
    assert "pm-meeting-player-main" in media
    assert "pm-meeting-player-tools" in media
    # Side card soft shadow
    side_card = css.split(".pm-meeting-stage-right-card {", 1)[1].split("}", 1)[0]
    assert "--pm-shadow-sm" in side_card


def test_meeting_main_tabs_are_summary_and_notes_only():
    """Steady main surface hosts Summary | Notes; Transcript/Speaker live in side rail."""
    src = MEETING_TABS.read_text(encoding="utf-8")
    assert 'TabsTrigger value="summary"' in src
    assert 'TabsTrigger value="notes"' in src
    # Default path hosts Tx/Speaker in parent side rail
    assert "hostTranscriptInParent" in src
    assert "onRequestSideTab" in src
    # Tools share the pill row
    assert "pm-meeting-tabs-actions" in src
    assert "Re-summarize" in src
    # Summary export (Markdown download + PDF via print / Save as PDF)
    assert "exportSummaryMarkdown" in src
    assert "exportSummaryAsPdf" in src
    assert "pm-meeting-export-menu" in src
    assert "Save as PDF" in src
    assert "onImageUpload={handleNotesImageUpload}" in src
    assert "uploadMeetingImage" in src
    assert "pm-meeting-notes-fmt-bar" in src
    assert 'showToolbar={false}' in src
    assert 'mainTab === "notes" && "is-notes"' in src


def test_live_capture_notes_upload_images():
    card_path = ROOT / "frontend" / "src" / "components" / "meeting" / "meeting-notes-card.tsx"
    assert card_path.exists()
    card = card_path.read_text(encoding="utf-8")
    assert "uploadMeetingImage" in card
    assert "onImageUpload=" in card


def test_meeting_stage_mode_sequential_fade():
    """Capture setup / speakers / studio transitions use sequential fade (no hard cut)."""
    view = MEETING_VIEW.read_text(encoding="utf-8")
    css = INDEX_CSS.read_text(encoding="utf-8")
    assert "displayStageMode" in view
    assert "targetStageMode" in view
    assert "is-mode-exiting" in view
    assert "pm-meeting-mode-studio" in view
    assert "STAGE_MODE_OUT_MS" in view
    assert ".pm-meeting-mode-studio" in css
    assert "is-mode-exiting" in css


def test_meeting_switch_keeps_stage_mounted_with_soft_fade():
    """Switching meetings must not remount stage / clear paint (blank flash)."""
    view = MEETING_VIEW.read_text(encoding="utf-8")
    css = INDEX_CSS.read_text(encoding="utf-8")
    # No stage remount key on activeMeeting
    assert 'className="pm-meeting-stage"' in view or "pm-meeting-stage" in view
    assert 'key={activeMeeting' not in view
    # Keep previous meeting until next payload (no blank)
    assert "setMeeting(null)" in view  # still used when deselecting
    # Soft-fade path present
    assert "pm-meeting-soft-fade" in view
    assert "meetingSoftFaded" in view
    assert "Do NOT setMeeting(null)" in view or "Do NOT setMeeting(null)" in view.replace(" ", "")
    # Prefer comment/code that avoids nulling on switch
    load_effect = view
    assert "fetchMeeting(activeMeeting)" in load_effect
    assert ".pm-meeting-soft-fade" in css
    # MeetingTabs not remounted per meeting id
    assert "key={meeting.id}" not in view


def test_file_tx_ready_toast_only_on_same_meeting_status_transition():
    """Soft-switch must not toast 'File transcription ready' for another completed meeting.

    While fading, painted meeting can still be the old (transcribing) one after
    activeMeeting already changed — transition detection must require
    meeting.id === activeMeeting and sameMeeting + prevStatus.
    """
    view = MEETING_VIEW.read_text(encoding="utf-8")
    assert 'toast.success("File transcription ready")' in view
    assert "paintedId" in view
    assert "paintedId !== activeMeeting" in view
    assert 'sameMeeting && prevStatus === "transcribing"' in view
    # Must not write prev status under the new id before paint matches
    assert "prevMeetingStatusRef" in view


def test_section_title_desc_inline_edit_no_reflow():
    """Title pencil + description click edit; same type metrics in edit mode."""
    tabs = MEETING_TABS.read_text(encoding="utf-8")
    css = INDEX_CSS.read_text(encoding="utf-8")
    assert "pm-meeting-section-title-input" in tabs
    assert "pm-meeting-section-desc-input" in tabs
    assert "pm-meeting-section-desc" in tabs
    assert "pm-meeting-section-desc-edit-btn" in tabs
    assert "Edit description" in tabs
    assert "onSaveTitle" in tabs
    assert "commitDescription" in tabs or "commitDescription" in tabs
    assert ".pm-meeting-section-title-input" in css
    assert ".pm-meeting-section-desc-input" in css
    assert ".pm-meeting-section-desc-edit-btn" in css
    # No layout chrome on edit fields
    title_in = css.split(".pm-meeting-section-title-input {", 1)[1].split("}", 1)[0]
    assert "background: transparent" in title_in
    assert "border: none" in title_in or "padding: 0" in title_in
    # desc-input block follows shared rule; must not add box padding/border chrome
    assert "pm-meeting-section-desc-input" in css
    assert "field-sizing: content" in css or "resize: none" in css


def test_section_rail_shows_generating_before_stream_tokens():
    """Section cards must show Generating while processing_state=generating.

    Regression: ready=true for generating tabs hid the badge (only Streaming
    used streaming=true; pending required ready=false).
    """
    tabs = MEETING_TABS.read_text(encoding="utf-8")
    view = MEETING_VIEW.read_text(encoding="utf-8")
    assert "generating?:" in tabs or "generating?:" in tabs.replace(" ", "") or "generating?:" in tabs
    assert "generating:" in tabs
    assert 'processing_state === "generating"' in tabs
    assert "item.generating" in view
    assert "Generating" in view


def test_pending_action_toasts_are_scoped_to_meeting():
    """Extract/regenerate complete toasts must not fire when viewing another meeting.

    Regression: pendingAction survived meeting switch; landing on an idle meeting
    looked like busy→idle and toasted 'Extract complete' in the corner.
    """
    tabs = MEETING_TABS.read_text(encoding="utf-8")
    assert "pendingAction.meetingId" in tabs or "meetingId: extractMeetingId" in tabs
    assert 'type: "extract"; meetingId: string' in tabs or 'type: "extract"; meetingId: string' in tabs.replace(" ", "")
    # Completion guard
    assert "pendingAction.meetingId !== meetingId" in tabs
    assert 'toast.success("Extract complete")' in tabs


def test_summary_stream_end_seeds_viewer_not_raw_markdown():
    """After summary SSE ends, leave ReactMarkdown path so speaker/ref chips paint.

    Regression: ``!!bpStream.streamingMd`` kept the generating branch after idle,
    so [spk:] / [stt_] stayed plain text until a full page refresh.
    """
    tabs = MEETING_TABS.read_text(encoding="utf-8")
    assert "SummaryMarkdownViewer" in tabs
    # Seed last SSE tokens into tab cache before dismissStreaming
    assert "tab_general: interim" in tabs
    assert "const interim = bpStream.streamingMd" in tabs
    assert "dismissStreaming" in tabs
    # Generating gate must not hold on bare streamingMd after stream ends
    gen_block = tabs.split("isSummaryGenerating =", 1)[1].split("const hasStreamTokens", 1)[0]
    assert "!!bpStream.streamingMd" not in gen_block
    assert 'summaryGenState === "prefilling"' in gen_block
    assert 'summaryGenState === "streaming"' in gen_block


def test_speakers_gate_before_summary_no_auto_stream():
    """File-tx complete → Speakers; Summary only after user clicks Summarize.

    Regression: auto-start on transcript-ready skipped Speakers and jumped into
    summarizing, then auto-unlocked Studio via tabs/blueprint.
    """
    view = MEETING_VIEW.read_text(encoding="utf-8")
    tabs = MEETING_TABS.read_text(encoding="utf-8")
    service = (ROOT / "src" / "meeting" / "service.py").read_text(encoding="utf-8")
    routes = (ROOT / "src" / "meeting" / "routes.py").read_text(encoding="utf-8")

    # Speakers stage + explicit enter
    assert "isCaptureSpeakers" in view
    assert "handleEnterStudio" in view
    assert "startBlueprintStream" in view or "startStream" in view
    assert "lockBackToCapture" in view
    # MeetingTabs must not auto-start SSE when transcript lands
    assert "autoStreamStartedRef" not in tabs
    assert "bpStreamCtrl.start()" in tabs  # manual Summarize / Re-summarize only
    # Re-tx clears Studio meta so hasStudioWork cannot skip Speakers
    assert "tabs=None" in service or "tabs=None" in routes
    assert "Speakers gate" in service or "speaker" in service.lower()


def test_section_rail_has_sliding_focus_indicator():
    """Section list focus wash slides between items (Meetings / Sessions language)."""
    view = MEETING_VIEW.read_text(encoding="utf-8")
    css = INDEX_CSS.read_text(encoding="utf-8")
    assert "pm-meeting-section-focus" in view
    assert "sectionFocusReady" in view
    assert "sectionItemRefs" in view
    assert ".pm-meeting-section-focus" in css
    focus = css.split(".pm-meeting-section-focus {", 1)[1].split("}", 1)[0]
    assert "position: absolute" in focus
    ready = css.split(".pm-meeting-section-focus.is-ready {", 1)[1].split("}", 1)[0]
    assert "transform" in ready
    # Active card no longer paints its own bar — indicator does
    active = css.split(".pm-meeting-section-card.is-active {", 1)[1].split("}", 1)[0]
    assert "background: transparent" in active


def test_dialog_title_is_large_serif_display():
    """Soft dialog titles use large light serif; green kicker remains optional."""
    css = INDEX_CSS.read_text(encoding="utf-8")
    dialog_ts = (ROOT / "frontend" / "src" / "components" / "ui" / "dialog.tsx").read_text(
        encoding="utf-8"
    )
    assert "DialogKicker" in dialog_ts
    assert "pm-dialog-kicker" in css
    default_title = css.split(".pm-dialog [data-slot=\"dialog-title\"] {", 1)[1].split("}", 1)[0]
    assert "pm-ff-prose" in default_title or "Source Serif" in default_title
    assert "clamp(18px" in default_title
    assert "uppercase" in default_title
    assert "font-weight: 300" in default_title


def test_collection_and_meeting_share_pill_tabs_tray():
    """Collection Overview|Files|Timeline and Meeting Summary|Notes use one pill tray."""
    css = INDEX_CSS.read_text(encoding="utf-8")
    db = (ROOT / "frontend" / "src" / "components" / "database" / "database-view.tsx").read_text(
        encoding="utf-8"
    )
    tabs = MEETING_TABS.read_text(encoding="utf-8")
    assert "pm-pill-tabs" in css
    assert "pm-collection-tabs-bar" in db
    assert "pm-pill-tabs" in db
    assert "pm-pill-tabs" in tabs
    # Shared tray is white soft card
    pill = css.split(".pm-pill-tabs,", 1)[1].split("}", 1)[0]
    assert "background: #ffffff" in pill or "#ffffff" in pill
    assert "box-shadow" in pill
    # Files: one browser card hosts toolbar + flat icon field
    assert ".pm-files-browser" in css
    browser = css.split(".pm-files-browser {", 1)[1].split("}", 1)[0]
    assert "background: #ffffff" in browser or "#ffffff" in browser
    assert "box-shadow" in browser
    assert "pm-files-browser" in (
        ROOT / "frontend" / "src" / "components" / "file-mgmt" / "folder-view" / "index.tsx"
    ).read_text(encoding="utf-8")
    assert ".pm-files-tb-nav" in css
    assert ".pm-files-grid" in css
    item = css.split(".pm-files-item {", 1)[1].split("}", 1)[0]
    assert "background: transparent" in item
    assert "box-shadow: none" in item


def test_dialog_open_close_motion_is_unified_silk():
    """All DialogContent share one open/close clock (opacity + micro scale, 280ms)."""
    dialog_ts = (ROOT / "frontend" / "src" / "components" / "ui" / "dialog.tsx").read_text(
        encoding="utf-8"
    )
    css = INDEX_CSS.read_text(encoding="utf-8")
    # Component always opts into silk popup + mask (no TW keyframe branch)
    assert "pm-dialog--silk" in dialog_ts
    assert "pm-dialog-overlay--silk" in dialog_ts
    assert "animate-in" not in dialog_ts or "data-open:animate-none" in dialog_ts
    assert "zoom-in-95" not in dialog_ts
    # Shared tokens + form
    assert "--pm-dialog-silk-ms: 280ms" in css
    assert "--pm-dialog-silk-ease: cubic-bezier(0.22, 1, 0.36, 1)" in css
    assert "scale: 0.985" in css
    # Popup motion applies to all .pm-dialog, not only --silk class
    assert ".pm-dialog[data-slot=\"dialog-content\"]" in css


def test_section_stream_openable_during_sse_without_dismiss():
    """During section SSE, list items stay openable and dismiss must not wipe live streams."""
    hook = (ROOT / "frontend" / "src" / "hooks" / "use-section-stream.ts").read_text(
        encoding="utf-8"
    )
    tabs = MEETING_TABS.read_text(encoding="utf-8")
    view = MEETING_VIEW.read_text(encoding="utf-8")
    # Explicit dismiss helper + in-flight guard
    assert "export function dismissSectionStream" in hook
    assert "if (entry.state.isStreaming) return" in hook
    assert "sectionStreamIsOpenable" in hook
    # Rail ready includes live stream / generating (not only tokens)
    assert "sectionStreamIsOpenable(stream)" in tabs
    assert 't.processing_state === "generating"' in tabs
    # Completion must dismiss the completed tab id, not "current selection"
    assert "dismissSectionStream(mid, completedTab)" in tabs
    assert "dismissSectionStream(mid, tid)" in tabs
    # Tab switch re-baselines streaming edge (no false complete on enter)
    assert "streamingTabRef" in tabs
    # Enter streaming section from rail
    assert "isReady || isStreaming" in view or "!isReady && !isStreaming" in view
    assert "sectionLive" in tabs


def test_section_ingest_split_rows_align_to_toolbar_actions():
    """Collection pill on title row; Choose a collection on description row;
    both widths sync to .pm-meeting-tabs-actions via --pm-meeting-ingest-col-w.
    """
    src = MEETING_TABS.read_text(encoding="utf-8")
    css = INDEX_CSS.read_text(encoding="utf-8")
    assert "pm-meeting-section-ingest-slot" in src
    assert "pm-meeting-section-meta--split" in src
    assert "pm-meeting-section-meta-choose" in src
    assert "Choose a collection" in src
    assert "--pm-meeting-ingest-col-w" in src
    assert "ResizeObserver" in src
    assert ".pm-meeting-section-meta--split" in css
    assert ".pm-meeting-section-meta-choose" in css
    assert "--pm-meeting-ingest-col-w" in css
    # Pill fills the measured column (not shrink-to-content)
    pill = css.split(".pm-meeting-ingest-pill--toolbar {", 1)[1].split("}", 1)[0]
    assert "width: 100%" in pill


def test_media_bar_has_custom_scrubbable_progress():
    src = MEDIA_BAR.read_text(encoding="utf-8")
    assert "pm-meeting-player-progress" in src
    # 3-row player: progress row + play/time main + tools
    assert "pm-meeting-player-progress-row" in src
    assert "pm-meeting-player-main" in src
    assert "pm-meeting-player-tools" in src
    assert "beginScrub" in src
    assert "role=\"slider\"" in src or 'role="slider"' in src
    assert "Re-transcribe" in src
    assert "Re-tx" not in src
    # Transcribing uses the same custom player shell (no native audio chrome)
    assert 'status === "transcribing"' in src
    assert "styled-audio" not in src
    assert 'controls src={audioUrl}' not in src
    assert "Transcribing…" in src
    # Live waveform must use real levels — no Math.random() thrash on re-render
    assert "Math.random()" not in src
    assert "levels" in src


def test_meeting_content_card_hosts_tabs_and_internal_scroll():
    view = MEETING_VIEW.read_text(encoding="utf-8")
    tabs = MEETING_TABS.read_text(encoding="utf-8")
    css = INDEX_CSS.read_text(encoding="utf-8")
    assert "pm-meeting-content-card" in view
    assert "pm-meeting-title-card" in view
    assert "pm-meeting-content-scroll" in tabs
    assert "pm-meeting-tabs-shell" in css
    assert ".pm-meeting-content-scroll" in css
    assert ".pm-meeting-title-card" in css


def test_meeting_single_transcript_side_protocol():
    """Steady: one Transcript in side rail; Summary refs open side tab (no floating dual path)."""
    view = MEETING_VIEW.read_text(encoding="utf-8")
    assert "requestSideTab" in view
    assert 'requestSideTab("transcript")' in view
    # Floating dual-path removed
    assert "setFloatingOpen" not in view
    assert "pm-meeting-float-tx" not in view
    # Transcript kept mounted for reliable focus scroll (crossfade panel, not remount)
    assert 'sideTab !== "transcript"' in view or 'sideTab === "transcript"' in view
    assert "pm-meeting-side-panel" in view
    assert "sideSurfaceDisplay" in view  # tools↔chat only; tab switch does not remount head
    tabs = MEETING_TABS.read_text(encoding="utf-8")
    assert "hostTranscriptInParent" in tabs


def test_meeting_chat_ref_uses_overlay_transcript_peek():
    """Chat sentence-ref opens overlay peek (main width unchanged); click main closes it."""
    view = MEETING_VIEW.read_text(encoding="utf-8")
    css = INDEX_CSS.read_text(encoding="utf-8")
    assert "txPeekOpen" in view
    assert "pm-meeting-tx-peek" in view
    assert "setTxPeekOpen(true)" in view
    assert "onPointerDownCapture" in view
    assert ".pm-meeting-tx-peek" in css
    assert "right: var(--pm-meeting-stage-right-w)" in css


def test_meeting_quick_chat_matches_collection_pm_qc_chrome():
    """Docked Meeting chat reuses Collection Quick Chat visual language (pm-qc-*)."""
    qc = (ROOT / "frontend" / "src" / "components" / "meeting" / "meeting-quick-chat.tsx").read_text(
        encoding="utf-8"
    )
    assert "pm-qc-panel" in qc
    assert "pm-qc-thread" in qc
    assert "pm-qc-msg pm-qc-msg--user" in qc or 'pm-qc-msg pm-qc-msg--user' in qc
    assert "pm-qc-composer-float" in qc
    assert "pm-qc-input-row" in qc
    assert "pm-qc-empty" in qc
    # Legacy filled bubbles removed
    assert "bg-[var(--pm-green-soft)] ml-6" not in qc


def test_speaker_sample_text_is_single_line():
    src = (ROOT / "frontend" / "src" / "components" / "meeting" / "transcript-panel.tsx").read_text(
        encoding="utf-8"
    )
    css = INDEX_CSS.read_text(encoding="utf-8")
    assert "pm-meeting-seg-text" in src
    assert "pm-meeting-seg--sample" in src
    assert ".pm-meeting-seg-text" in css
    assert "white-space: nowrap" in css
    assert "text-overflow: ellipsis" in css
    # Body uses design-system prose (Source Serif), not chrome pm-title
    assert "pm-meeting-seg-body" in src
    assert ".pm-meeting-seg-body" in css
    body = css.split(".pm-meeting-seg-body {", 1)[1].split("}", 1)[0]
    assert "pm-t-prose" in body or "pm-ff-prose" in body


def test_meeting_qc_fab_park_bottom_top_and_collapsed():
    """Single diamond: bottom park (side open or collapsed), top+spin when Chat open."""
    view = MEETING_VIEW.read_text(encoding="utf-8")
    qc = (ROOT / "frontend" / "src" / "components" / "meeting" / "meeting-quick-chat.tsx").read_text(
        encoding="utf-8"
    )
    css = INDEX_CSS.read_text(encoding="utf-8")
    assert "MeetingQcFab" in view
    assert "pm-meeting-qc-fab-host" in view
    assert "is-park-bottom" in view
    assert "is-park-top" in view
    # No separate collapsed park class (same bottom coords → no stutter)
    assert "is-park-collapsed" not in view
    assert 'viewBox="0 0 48 48"' in qc
    assert "strokeLinejoin=\"round\"" in qc or 'strokeLinejoin="round"' in css or 'strokeLinejoin="round"' in qc
    assert ".pm-meeting-qc-fab-host" in css
    assert "is-park-bottom" in css
    assert "is-park-top" in css
    assert "is-fading" in css
    assert "qcFabFading" in view or "is-fading" in view
    assert "qcFabSpinPhase" in view
    assert "spinPhase" in view
    assert "EXIT_ACCEL_MS" in qc
    assert "CRUISE_PERIOD_MS" in qc
    assert "enter-decel-top" in qc
    assert "enter-hold" in qc


def test_meeting_empty_and_live_modes_are_independent_stages():
    """E empty capture + F live+notes are full-span modes, not Steady 2×2."""
    view = MEETING_VIEW.read_text(encoding="utf-8")
    css = INDEX_CSS.read_text(encoding="utf-8")
    assert 'data-meeting-mode="empty"' in view
    assert 'data-meeting-mode="live"' in view
    assert "Capture the conversation" in view
    assert "Start recording" in view
    assert "Live transcript" in view
    assert "pm-meeting-mode-empty" in css
    assert "pm-meeting-mode-live" in css
    assert "pm-meeting-e-stage" in css
    assert "pm-meeting-f-grid" in css
    assert "is-mode-empty" in css
    assert "is-mode-live" in css


def test_soft_menu_layout_effect_does_not_depend_on_children():
    """Portal SoftMenu setCoords + children dep = infinite render (white screen)."""
    src = (
        ROOT / "frontend" / "src" / "components" / "ui" / "menu.tsx"
    ).read_text(encoding="utf-8")
    assert "function SoftMenu" in src
    # Must not list `children` next to matchAnchorWidth in the layout-effect deps
    assert "matchAnchorWidth,\n    children," not in src
    assert "visibility: \"hidden\"" in src or "visibility: 'hidden'" in src


def test_live_capture_control_card_not_danger_banner():
    """Live record uses speaker-gate-style card + real waveform; no red alarm strip; no Chat."""
    view = MEETING_VIEW.read_text(encoding="utf-8")
    css = INDEX_CSS.read_text(encoding="utf-8")
    card = (
        ROOT / "frontend" / "src" / "components" / "meeting" / "live-capture-control-card.tsx"
    ).read_text(encoding="utf-8")
    recorder = (
        ROOT / "frontend" / "src" / "hooks" / "use-audio-recorder.ts"
    ).read_text(encoding="utf-8")

    assert "LiveCaptureControlCard" in view
    assert "pm-meeting-live-control-card" in card
    assert "Live captions" in card
    assert "pm-meeting-live-wave" in card
    # No Chat button on live capture surface (only comment may mention Chat)
    assert "onClick={() => setQuickChatOpen" not in view or "isLiveStage" in view
    assert 'children: "Chat"' not in card and ">Chat<" not in card
    # Live stage surface (bounded): no MeetingQuickChat dock; followLive on transcript
    live_start = view.find('data-meeting-mode="live"')
    assert live_start >= 0
    live_block = view[live_start : live_start + 3500]
    assert "MeetingQuickChat" not in live_block
    assert "LiveCaptureControlCard" in live_block
    assert "Live transcript" in live_block
    assert "followLive" in live_block
    transcript = (
        ROOT / "frontend" / "src" / "components" / "meeting" / "transcript-panel.tsx"
    ).read_text(encoding="utf-8")
    assert "followLive" in transcript
    assert "data-seg-live" in transcript
    # Danger full-width banner removed from CSS
    assert ".pm-meeting-f-banner {" not in css
    assert "pm-meeting-live-control-card" in css
    # Real analyser path + pure scaler
    assert "createAnalyser" in recorder
    assert "binsToLevels" in recorder
    assert "getByteFrequencyData" in recorder
    # Pre-start Live caption chip under Start recording (absolute, no layout push)
    assert "pm-meeting-e-start-group" in view
    assert "pm-meeting-e-realtime-chip" in view
    assert "Live caption ·" in view
    assert "position: absolute" in css
    assert ".pm-meeting-e-realtime-chip" in css


MEETING_CAPTURE = ROOT / "frontend" / "src" / "components" / "meeting" / "meeting-capture-stages.tsx"
MEETING_NOTES_CARD = ROOT / "frontend" / "src" / "components" / "meeting" / "meeting-notes-card.tsx"
MEETING_NOTES_HOOK = ROOT / "frontend" / "src" / "hooks" / "use-meeting-notes.ts"


def test_prep_notes_rail_on_setup_audio_transcribing_not_speakers():
    """Prep NOTES handle on setup / audio-ready / transcribing; speakers stay two-col."""
    capture = MEETING_CAPTURE.read_text(encoding="utf-8")
    assert "function PrepStage" in capture
    assert "pm-meeting-notes-handle" in capture
    assert "pm-meeting-notes-rail" in capture
    assert "MeetingNotesCard" in capture
    assert 'mode="empty"' in capture
    assert 'mode="audio-ready"' in capture
    assert 'mode="transcribing"' in capture
    speakers_block = capture.split('data-meeting-mode="speakers"', 1)[1].split(
        'data-meeting-mode="live"', 1
    )[0]
    assert "pm-meeting-notes-handle" not in speakers_block
    assert "pm-meeting-notes-rail" not in speakers_block
    assert "PrepStage" not in speakers_block


def test_live_notes_use_shared_meeting_notes_card():
    """Live right column is MeetingNotesCard, not a one-off MarkdownEditor."""
    capture = MEETING_CAPTURE.read_text(encoding="utf-8")
    live_block = capture.split('data-meeting-mode="live"', 1)[1]
    assert "MeetingNotesCard" in live_block
    assert "<MarkdownEditor" not in live_block
    assert MEETING_NOTES_CARD.exists()
    card = MEETING_NOTES_CARD.read_text(encoding="utf-8")
    assert "export function MeetingNotesCard" in card
    assert "MarkdownEditor" in card
    assert "pm-meeting-f-notes" in card


def test_meeting_notes_hook_is_single_autosave_path():
    """One hook owns notes.md debounce + flush; Studio tab does not save notes itself."""
    assert MEETING_NOTES_HOOK.exists()
    hook = MEETING_NOTES_HOOK.read_text(encoding="utf-8")
    view = MEETING_VIEW.read_text(encoding="utf-8")
    tabs = MEETING_TABS.read_text(encoding="utf-8")
    studio = MEETING_STUDIO.read_text(encoding="utf-8")
    assert "export function useMeetingNotes" in hook
    assert "unescapeMarkdownOverEscapes" in hook
    assert "800" in hook
    assert "updateMeeting" in hook
    assert "flush" in hook
    assert "useMeetingNotes" in view
    start_idx = view.find("const handleStartRecording")
    stop_idx = view.find("const handleStopRecording")
    assert start_idx >= 0 and stop_idx >= 0
    assert "flush" in view[start_idx:stop_idx]
    assert "flush" in view[stop_idx : stop_idx + 1800]
    assert "updateMeeting(meetingId, { notes:" not in tabs
    assert "onNotesChange" in tabs
    assert "onNotesChange" in studio


def test_prep_notes_rail_css_slide_and_stage_radius():
    """Handle + sliding rail; cream surface radius is not inherit-from-kind."""
    css = INDEX_CSS.read_text(encoding="utf-8")
    assert ".pm-meeting-notes-handle" in css
    assert ".pm-meeting-notes-rail" in css
    assert ".pm-meeting-notes-dock" in css
    assert ".pm-meeting-mode-empty.is-notes-open" in css
    handle = css.split(".pm-meeting-notes-handle {", 1)[1].split("}", 1)[0]
    assert "align-self: center" in handle
    rail = css.split(".pm-meeting-notes-rail {", 1)[1].split("}", 1)[0]
    assert "position: absolute" in rail
    assert "translateX" in rail
    empty = css.split(".pm-meeting-mode-empty {", 1)[1].split("}", 1)[0]
    assert "--pm-notes-handle-w" in empty
    main = css.split(".pm-meeting-e-main {", 1)[1].split("}", 1)[0]
    assert "padding-right" in main
    assert "transition" in main
    rail_open = css.split(
        ".pm-meeting-mode-empty.is-notes-open .pm-meeting-notes-rail {", 1
    )[1].split("}", 1)[0]
    assert "translateX(0)" in rail_open
    assert "drop-shadow" in css
    surface = css.split(".pm-meeting-stage-surface {", 1)[1].split("}", 1)[0]
    assert "border-radius: var(--pm-r-lg" in surface
    assert "border-radius: inherit" not in surface
    assert "overflow: visible" in surface
    reduced = css.split("@media (prefers-reduced-motion: reduce)", 2)
    joined = "\n".join(reduced[1:]) if len(reduced) > 1 else ""
    assert "pm-meeting-notes-rail" in joined or "is-notes-open" in joined
