"""Premium chrome for group pick dialogs, rail tabs, member lists, and actions."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS = ROOT / "frontend" / "src" / "index.css"
LIST = ROOT / "frontend" / "src" / "components" / "meeting" / "meeting-list.tsx"
STAGE = ROOT / "frontend" / "src" / "components" / "meeting" / "meeting-group-stage.tsx"
META = ROOT / "frontend" / "src" / "components" / "meeting" / "meeting-groups-meta.tsx"
PICK = ROOT / "frontend" / "src" / "components" / "meeting" / "meeting-pick-list.tsx"


def test_rail_tabs_use_shared_pill_tabs():
    src = LIST.read_text(encoding="utf-8")
    assert "TabsTrigger" in src
    assert "TabsIndicator" in src
    assert "pm-meeting-rail-tab-thumb" not in src


def test_group_action_buttons_use_chip_tray_not_capture_pills():
    for path in (STAGE, META):
        src = path.read_text(encoding="utf-8")
        assert "pm-meeting-group-actions" in src
        assert "pm-meeting-pill is-compact" not in src


def test_group_and_member_lists_use_catalog_index():
    stage = STAGE.read_text(encoding="utf-8")
    meta = META.read_text(encoding="utf-8")
    pick = PICK.read_text(encoding="utf-8")
    assert "MeetingPickList" in stage
    assert "MeetingPickList" in meta
    assert "pm-chat-sess-row" not in stage
    assert "pm-chat-sess-row" not in meta
    assert "onRemove" in pick
    assert "index" in pick
    assert "index: String(mem.n)" not in stage


def test_group_members_title_uses_float_card_head():
    """Title sits in the 20px-radius float card — not the cramped nested section head."""
    src = STAGE.read_text(encoding="utf-8")
    assert "pm-meeting-rail-head" in src
    assert "pm-meeting-section-rail-head" not in src
    css = CSS.read_text(encoding="utf-8")
    block = css.split(".pm-meeting-rail-head {", 1)[1].split("}", 1)[0]
    assert "padding: 16px 14px 12px" in block


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
    """Top/bottom scroll masks use the overlay float color, not a white wash."""
    css = CSS.read_text(encoding="utf-8")
    assert ".pm-meeting-tx-overlay .pm-panel-scroll-shell" in css
    block = css.split(".pm-meeting-tx-overlay .pm-panel-scroll-shell", 1)[1][:900]
    assert "--pm-edge-fade-color" in block
    assert "#ffffff" not in block.split("{", 1)[1].split("}", 1)[0]
    assert "pm-float" in block


def test_overlay_title_aligns_with_player_and_uses_collapse_icon():
    src = STAGE.read_text(encoding="utf-8")
    assert "pm-meeting-tx-overlay-head" in src
    assert "pm-meeting-tx-overlay-title" in src
    assert "PanelRightClose" in src
    assert "<X " not in src
    css = CSS.read_text(encoding="utf-8")
    block = css.split(".pm-meeting-tx-overlay-head {", 1)[1].split("}", 1)[0]
    assert "12px" in block
    title = css.split(".pm-meeting-tx-overlay-title {", 1)[1].split("}", 1)[0]
    assert "pm-ff-prose" in title or "Source Serif" in title


def test_group_delete_asks_before_api():
    """Rail trash opens a confirm dialog; it does not call the delete API inline."""
    view = (ROOT / "frontend" / "src" / "components" / "meeting" / "meeting-view.tsx").read_text(
        encoding="utf-8"
    )
    overlays = (
        ROOT / "frontend" / "src" / "components" / "meeting" / "meeting-view-overlays.tsx"
    ).read_text(encoding="utf-8")
    rail = LIST.read_text(encoding="utf-8")
    idx = view.find("onDeleteGroup=")
    assert idx >= 0
    snippet = view[idx : idx + 220]
    assert "setDeleteGroupTarget" in snippet
    assert "apiDeleteMeetingGroup" not in snippet
    assert "deleteGroupTarget" in view
    assert "confirmDeleteGroup" in view
    assert "deleteGroupTarget" in overlays
    assert "meeting.deleteGroupQ" in overlays
    assert "meeting.deleteGroupBody" in overlays
    assert 't("meeting.deleteGroup")' in rail


def test_join_dialog_nests_pick_list_as_white_card():
    css = CSS.read_text(encoding="utf-8")
    assert ".pm-meeting-join-dialog .pm-meeting-pick-list" in css
    assert "background: #ffffff" in css.split(".pm-meeting-join-dialog .pm-meeting-pick-list", 1)[1][:800]
    assert ".pm-meeting-group-actions" in css
    assert ".pm-meeting-rail-tabs [data-slot=\"tabs-list\"]" in css or ".pm-meeting-rail-tabs [data-slot='tabs-list']" in css
