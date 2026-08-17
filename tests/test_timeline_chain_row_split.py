"""SP-04: Timeline ChainRow lives in chain-row.tsx; drop-id contract unchanged."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TV = ROOT / "frontend" / "src" / "components" / "file-mgmt" / "timeline-view"
ROW = TV / "chain-row.tsx"
IDX = TV / "index.tsx"


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
