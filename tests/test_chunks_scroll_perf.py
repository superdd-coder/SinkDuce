"""Chunks tab: collapsed tiles stay cheap so the list can scroll without jank."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DETAIL = ROOT / "frontend" / "src" / "components" / "file-mgmt" / "file-detail"
UTILS = ROOT / "frontend" / "src" / "lib" / "utils.ts"
CSS = ROOT / "frontend" / "src" / "index.css"


def test_collapsed_preview_helper_is_plain_text():
    src = UTILS.read_text(encoding="utf-8")
    assert "export function collapsedChunkPreview" in src
    assert "stripImageBlocks" in src


def test_chunks_tab_uses_preview_until_expanded():
    pane = (DETAIL / "file-detail-main-pane.tsx").read_text(encoding="utf-8")
    assert "collapsedChunkPreview" in pane
    assert "pm-ws-chunk-list" in pane
    assert "pm-ws-chunk-tile" in pane
    # Collapsed tiles must not pay for the markdown pipeline.
    assert "expanded ?" in pane or "expanded &&" in pane
    chunks_tab = pane.split("{/* Chunks", 1)[1]
    # Native overflow — Base UI ScrollArea forces layout of every child.
    assert "<ScrollArea" not in chunks_tab.split("{developerMode", 1)[0]
    assert "transition-all" not in chunks_tab.split("{developerMode", 1)[0]


def test_chunk_inspect_defers_tooltip_until_hover():
    inspect = (DETAIL / "chunk-inspect.tsx").read_text(encoding="utf-8")
    assert "onPointerEnter" in inspect
    assert "setArmed" in inspect or "armed" in inspect


def test_chunk_tiles_use_content_visibility():
    css = CSS.read_text(encoding="utf-8")
    assert ".pm-ws-chunk-tile" in css
    tile = css.split(".pm-ws-chunk-tile", 1)[1][:400]
    assert "content-visibility" in tile
    assert "contain-intrinsic-size" in tile
    assert ".pm-ws-chunk-list" in css
