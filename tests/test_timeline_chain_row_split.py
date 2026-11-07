"""SP-04: Timeline ChainRow lives in chain-row.tsx; drop-id contract unchanged."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TV = ROOT / "frontend" / "src" / "components" / "file-mgmt" / "timeline-view"
ROW = TV / "chain-row.tsx"
IDX = TV / "index.tsx"
CSS = ROOT / "frontend" / "src" / "index.css"
MENU = TV / "groups-menu.tsx"


def test_chain_row_module_exists_and_exports():
    row = ROW.read_text(encoding="utf-8")
    assert ROW.is_file()
    assert "export function ChainRow" in row
    assert "export const parseEndDropId" in row
    assert "export const parseStartDropId" in row
    assert "__end__:" in row
    assert "__start__:" in row


def test_merge_dialog_does_not_create_end_before_submit():
    """Dismissing Merge must not leave a branch-closing end marker."""
    idx = IDX.read_text(encoding="utf-8")
    merge = idx.split("const mergeBranch = useCallback", 1)[1].split(
        "const ro=useCallback", 1
    )[0]
    assert "createNode" not in merge
    assert "setEcOpen(true)" in merge
    assert "const done = !!bc.merge_node_id" in idx
    assert "discardMergePrepare" in idx


def test_timeline_index_imports_chain_row_and_does_not_define_it():
    idx = IDX.read_text(encoding="utf-8")
    assert "from './chain-row'" in idx
    assert "function ChainRow" not in idx
    assert "function SW(" not in idx
    assert "parseEndDropId" in idx
    assert "parseStartDropId" in idx


def test_groups_menu_opens_without_a_page_scrim():
    """The shared menu closes on outside click without veiling the page."""
    src = MENU.read_text(encoding="utf-8")
    assert "<SoftMenu open={open} portal" in src
    assert "onClose={() => setOpen(false)}" in src
    assert "pm-timeline-menu-scrim" not in src


def test_add_node_and_live_notes_toolbars_stay_put():
    """Dialog / live-notes format strips must not sticky-jump over prose."""
    css = CSS.read_text(encoding="utf-8")
    add_bar = css.split(".pm-add-node-md-host .pm-fmt-toolbar {", 1)[1].split("}", 1)[0]
    assert "position: relative !important" in add_bar
    live = css.split(".pm-meeting-f-notes .pm-fmt-toolbar {", 1)[1].split("}", 1)[0]
    assert "background: #ffffff !important" in live
    assert ".pm-meeting-f-notes .pm-fmt-toolbar::after" in css


def test_message_dialog_pins_format_bar_outside_editor():
    """Add-message toolbar lives in .pm-msg-fmt-bar, not sticky inside ProseMirror."""
    src = (
        ROOT
        / "frontend"
        / "src"
        / "components"
        / "file-mgmt"
        / "folder-view"
        / "message-editor-dialog.tsx"
    ).read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")
    assert "pm-msg-fmt-bar" in src
    assert "showToolbar={false}" in src
    assert "pinned" in src
    bar = css.split(".pm-msg-fmt-bar {", 1)[1].split("}", 1)[0]
    assert "flex-shrink: 0" in bar
