"""Premium chrome for the merged meeting rail, group stage, and group dialogs."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS = ROOT / "frontend" / "src" / "index.css"
LIST = ROOT / "frontend" / "src" / "components" / "meeting" / "meeting-list.tsx"
STAGE = ROOT / "frontend" / "src" / "components" / "meeting" / "meeting-group-stage.tsx"
VIEW = ROOT / "frontend" / "src" / "components" / "meeting" / "meeting-view.tsx"
OVERLAYS = ROOT / "frontend" / "src" / "components" / "meeting" / "meeting-view-overlays.tsx"


def test_rail_is_one_merged_list_no_tabs():
    """Groups sit beside meetings in a single rail — the tab pair is gone."""
    src = LIST.read_text(encoding="utf-8")
    assert "TabsTrigger" not in src
    assert "onRailTab" not in src
    # Group rows are visually distinct, expandable, and match the meeting
    # rows' two-line anatomy (same height): folder marker + name / meta.
    assert "is-group" in src
    assert "pm-rail-group-marker" in src
    assert "FolderOpen" in src
    assert "pm-rail-group-body" in src
    assert "pm-chat-sess-name" in src
    # Expand/collapse silk motion: branch stays mounted, grid rows animate
    assert "pm-rail-group-branch-wrap" in src
    assert "grid-template-rows" in CSS.read_text(encoding="utf-8")
    assert "aria-hidden={!isExpanded}" in src


def test_group_row_hover_actions_delete_and_new_meeting():
    src = LIST.read_text(encoding="utf-8")
    assert "onDeleteGroup" in src
    assert "onCreateMeetingInGroup" in src
    assert "pm-rail-actions" in src
    assert "FilePlus2" in src


def test_meeting_row_hover_uses_text_new_group_and_hover_colors():
    """Ungrouped meeting rows: New Group is a green text button; delete/archive
    stay icons, single white pill (no layered button backgrounds)."""
    src = LIST.read_text(encoding="utf-8")
    assert "onCreateGroupFromMeeting" in src
    assert 't("meeting.newGroupBtn")' in src
    assert "pm-rail-act is-green is-text" in src
    assert "pm-rail-act is-danger" in src
    # Icons only — the create-group action is text, no folder-plus icon
    assert "FolderPlus" not in src
    css = CSS.read_text(encoding="utf-8")
    pill = css.split(".pm-rail-actions {", 1)[1].split("}", 1)[0]
    # One layer: pure white pill, buttons transparent
    assert "background: #ffffff" in pill
    act = css.split(".pm-rail-act {", 1)[1].split("}", 1)[0]
    assert "background: transparent" in act
    danger = css.split(".pm-rail-act.is-danger:hover {", 1)[1].split("}", 1)[0]
    assert "--pm-danger" in danger
    # Selecting a row must not pin the pill — reveal on hover, or when a
    # pill button holds keyboard focus (never the row's own focus)
    assert ".pm-chat-sess-row:focus-within .pm-rail-actions" not in css
    assert ".pm-rail-actions:focus-within" in css


def test_meeting_archive_toggle_lives_in_rail():
    src = LIST.read_text(encoding="utf-8")
    assert "onToggleArchiveMeeting" in src
    assert "ArchiveRestore" in src
    assert "is-archived" in src


def test_group_click_focus_gated_collapse_and_indicator_math():
    """Only the focused group collapses on click; the pill measures against
    the list box (member rows nest inside the positioned branch)."""
    src = LIST.read_text(encoding="utf-8")
    assert "handleGroupActivate" in src
    assert "isFocused" in src
    assert "aria-expanded" in src
    assert "getBoundingClientRect" in src
    assert "listEl.scrollTop" in src
    # offsetTop would be relative to the branch container — must not be used
    assert "activeEl.offsetTop" not in src


def test_drag_moves_membership_never_order():
    src = LIST.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")
    assert "draggable" in src
    assert "onDragStart" in src
    assert "onDropMeetingInGroup" in src
    assert "onRemoveMeetingFromGroup" in src
    # Drop handlers only call membership callbacks — no list reorder props
    assert "onReorder" not in src
    assert "onMoveMeeting" not in src
    # Archived ungrouped meetings + archived groups fold into a collapsed
    # section at the bottom; archived groups are not drag destinations
    assert "pm-rail-archive-head" in src
    assert "archivedRows" in src
    assert "droppableGroups" in src
    assert "groups.filter((g) => !g.archived)" in src
    # Ungrouped drag: floating pick-group panel with edge auto-scroll;
    # grouped drag: the SAME temp sidebar hosts an empty Library-style drop
    # zone (dashed green, armed = "release to remove")
    assert "pm-rail-dnd-panel" in src
    assert "pm-rail-dnd-dropzone" in src
    assert "FolderMinus" in src
    assert "is-armed" in src
    assert 't("meeting.dndRemoveFromGroup")' in src
    assert "scrollTop" in src
    assert "requestAnimationFrame" in src
    assert src.index("pm-rail-dnd-panel") < src.index("<Dialog")
    # Full-page dim while dragging; the two sidebars stay bright. The app
    # nav/header sit outside the view stacking context, so they dim via the
    # body class instead of the in-view scrim.
    assert "pm-rail-dnd-scrim" in src
    assert 'document.body.classList.add("pm-dnd-active")' in src
    scrim = css.split(".pm-rail-dnd-scrim {", 1)[1].split("}", 1)[0]
    assert "position: fixed" in scrim
    assert "body.pm-dnd-active .pm-shell-nav::after" in css
    assert "body.pm-dnd-active .pm-shell-header::after" in css
    # Header escapes the fixed scrim (nav parity) — no double-dim band on top
    header_escape = css.split("body.pm-dnd-active .pm-shell-header {", 1)[1].split("}", 1)[0]
    assert "z-index: 30" in header_escape
    # The old full-screen remove mask is gone — the sidebar zone replaces it
    assert "pm-rail-dnd-mask" not in src
    assert "pm-rail-dnd-mask" not in css
    # Whole sidebar is the drop surface; the dashed frame wraps the ENTIRE
    # sidebar and only appears while the drag hovers it (armed)
    dropzone = css.split(".pm-rail-dnd-dropzone {", 1)[1].split("}", 1)[0]
    assert "border" not in dropzone
    panel_armed = css.split(".pm-rail-dnd-panel.is-armed {", 1)[1].split("}", 1)[0]
    assert "dashed" in panel_armed
    assert "outline-offset: -7px" in panel_armed
    # Panel title reuses the rail head (margin-aligned) and the group cards
    # reuse the full-height rail row anatomy
    assert 'className="pm-meeting-rail-head pm-rail-head"' in src
    assert '"pm-chat-sess-row is-group"' in src
    # Group time = newest member meeting's created_at (sort + display)
    assert "groupLatestCreatedAt" in src
    assert "last_chat_at || g.updated_at" not in src
    assert "pm-rail-dnd-group" not in src


def test_group_stage_title_card_with_labeled_action_card():
    """Group page: side action card with three labeled buttons (create /
    archive / delete), colorless at rest — colors appear on hover."""
    src = STAGE.read_text(encoding="utf-8")
    assert "pm-meeting-title-card" in src
    assert "pm-meeting-title-actions" in src
    assert "updateMeetingGroup" in src
    assert "pm-meeting-card-btn is-cta" in src
    assert "pm-meeting-card-btn is-danger" in src
    assert 't("meeting.createMeetingBtn")' in src
    assert 't("meeting.deleteGroup")' in src
    assert "FilePlus2" in src
    assert "ArchiveRestore" in src
    css = CSS.read_text(encoding="utf-8")
    card = css.split(".pm-meeting-title-actions {", 1)[1].split("}", 1)[0]
    # Card hugs the widest label with padding as the surrounding margin
    assert "width: fit-content" in card
    assert "padding: 8px" in card
    btn = css.split(".pm-meeting-card-btn {", 1)[1].split("}", 1)[0]
    assert "width: 100%" in btn
    # No resting color — green/red only on hover
    assert ".pm-meeting-card-btn.is-cta {" not in css
    assert ".pm-meeting-card-btn.is-danger {" not in css
    hover_green = css.split(".pm-meeting-card-btn.is-cta:hover {", 1)[1].split("}", 1)[0]
    assert "--pm-green" in hover_green
    hover_danger = css.split(".pm-meeting-card-btn.is-danger:hover {", 1)[1].split("}", 1)[0]
    assert "--pm-danger" in hover_danger
    head = css.split(".pm-meeting-group-head {", 1)[1].split("}", 1)[0]
    assert "align-items: stretch" in head


def test_group_stage_has_no_member_side_rail():
    """Right member sidebar is gone; the cite transcript opens in its old spot."""
    src = STAGE.read_text(encoding="utf-8")
    assert "MeetingPickList" not in src
    assert "pm-meeting-group-actions" not in src
    # Dedicated animated column in the original sidebar position (not a
    # floating overlay over the chat text)
    assert "pm-meeting-tx-side-col" in src
    assert "pm-meeting-tx-side-inner" in src
    css = CSS.read_text(encoding="utf-8")
    col = css.split(".pm-meeting-tx-side-col.is-open {", 1)[1].split("}", 1)[0]
    assert "clamp(272px, 30vw, 360px)" in col
    base = css.split(".pm-meeting-tx-side-col {", 1)[1].split("}", 1)[0]
    assert "width: 0" in base
    assert "transition: width" in base


def test_transcript_overlay_collapses_with_exit_motion():
    """Collapse keeps the overlay mounted and fades it; visibility waits out the motion."""
    src = STAGE.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")
    assert "pm-meeting-tx-overlay" in src
    assert "is-open" in src
    assert "onTransitionEnd" in src
    assert "e.target !== e.currentTarget" in src
    assert "invisible pointer-events-none opacity-0" not in src
    assert "transition-opacity duration-200" not in src
    block = css.split(".pm-meeting-tx-overlay {", 1)[1].split("}", 1)[0]
    assert "opacity" in block
    assert "transform" in block
    assert "visibility" in block
    assert "cubic-bezier" in block
    assert ".pm-meeting-tx-overlay.is-open" in css


def test_overlay_transcript_edge_fades_match_cream_surface():
    """Panel surface is white; list fades match it, and the nested search /
    player cards get a warm-gray fill so they read on white."""
    css = CSS.read_text(encoding="utf-8")
    assert ".pm-meeting-tx-overlay .pm-panel-scroll-shell" in css
    block = css.split(".pm-meeting-tx-overlay .pm-panel-scroll-shell", 1)[1][:900]
    assert "--pm-edge-fade-color: #ffffff" in block
    # Shadow is carried by the inner card (hugs the panel exactly — a shadow
    # on the wider column painted a ghost card outline into the gap); the
    # inner tracks the column width so the column needs no clipping
    col = css.split(".pm-meeting-tx-side-col {", 1)[1].split("}", 1)[0]
    assert "box-shadow" not in col
    assert "overflow" not in col
    inner = css.split(".pm-meeting-tx-side-inner {", 1)[1].split("}", 1)[0]
    assert "background: #ffffff" in inner
    assert "border-radius: 20px" in inner
    assert "box-shadow: var(--pm-shadow)" in inner
    assert "width: calc(100% - 10px)" in inner
    overlay = css.split(".pm-meeting-tx-overlay {", 1)[1].split("}", 1)[0]
    assert "box-shadow" not in overlay
    assert "background" not in overlay
    # Both group-stage cards are white and their list fades fade to white
    chat_card = css.split(".pm-meeting-group-chat-card {", 1)[1].split("}", 1)[0]
    assert "background: #ffffff" in chat_card
    assert ".pm-meeting-group-chat-card .pm-panel-scroll-shell" in css
    # Nested cards on white: search input + player get the warm-gray fill
    assert '[data-slot="input"]' in css.split(".pm-meeting-tx-overlay [", 1)[1][:200] or ".pm-meeting-tx-overlay [data-slot=" in css
    player = css.split(".pm-meeting-tx-player {", 1)[1].split("}", 1)[0]
    assert "color-mix" in player
    stage_src = STAGE.read_text(encoding="utf-8")
    assert "pm-meeting-tx-player" in stage_src
    assert "bg-white" not in stage_src


def test_overlay_title_aligns_with_player_and_uses_collapse_icon():
    src = STAGE.read_text(encoding="utf-8")
    assert "pm-meeting-tx-overlay-head" in src
    assert "pm-meeting-tx-overlay-title" in src
    assert "PanelRightClose" in src
    # The collapse affordance inside the overlay is PanelRightClose — scope the
    # check to the overlay markup (title editing elsewhere may use X).
    i = src.find("pm-meeting-tx-overlay")
    overlay = src[i : i + 3500]
    assert "<X " not in overlay
    css = CSS.read_text(encoding="utf-8")
    block = css.split(".pm-meeting-tx-overlay-head {", 1)[1].split("}", 1)[0]
    assert "12px" in block
    title = css.split(".pm-meeting-tx-overlay-title {", 1)[1].split("}", 1)[0]
    assert "pm-ff-prose" in title or "Source Serif" in title


def test_group_stage_stays_mounted_when_switching_to_meetings():
    """Keep Group Chat mounted (hidden) so SSE survives Meetings tab / view switch."""
    view = VIEW.read_text(encoding="utf-8")
    assert "{paintGroupStage && paintGroupId ? (" not in view
    assert "paintGroupId" in view
    assert "<MeetingGroupStage" in view
    wrap = view.split("<MeetingGroupStage")[0][-500:]
    assert "hidden" in wrap
    assert "aria-hidden" in wrap


def test_group_delete_asks_before_api():
    """Rail trash opens a confirm dialog; it does not call the delete API inline."""
    view = VIEW.read_text(encoding="utf-8")
    rail = LIST.read_text(encoding="utf-8")
    idx = view.find("onDeleteGroup=")
    assert idx >= 0
    snippet = view[idx : idx + 220]
    assert "setDeleteGroupTarget" in snippet
    assert "apiDeleteMeetingGroup" not in snippet
    assert "deleteGroupTarget" in view
    assert "confirmDeleteGroup" in view
    assert "deleteGroupTarget" in OVERLAYS.read_text(encoding="utf-8")
    assert "meeting.deleteGroupQ" in OVERLAYS.read_text(encoding="utf-8")
    assert "meeting.deleteGroupBody" in OVERLAYS.read_text(encoding="utf-8")
    assert 't("meeting.deleteGroup")' in rail


def test_delete_group_dialog_warns_cascade():
    """Copy tells the user member meetings are deleted with the group."""
    import json

    for locale in ("en", "zh-CN"):
        data = json.loads(
            (ROOT / "frontend" / "src" / "i18n" / f"{locale}.json").read_text(encoding="utf-8")
        )
        body = data["meeting"]["deleteGroupBody"].lower()
        assert "meeting" in body or "会议" in body


def test_group_dialog_is_white_card():
    css = CSS.read_text(encoding="utf-8")
    assert ".pm-meeting-group-dialog.pm-dialog" in css
    assert ".pm-meeting-title-actions" in css
