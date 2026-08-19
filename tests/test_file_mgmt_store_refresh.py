"""Folder-view store: delete folder must refresh the file list (orphans at root)."""

from __future__ import annotations

from pathlib import Path

STORE = (
    Path(__file__).resolve().parents[1]
    / "frontend"
    / "src"
    / "stores"
    / "file-mgmt-store.ts"
)


def test_remove_folder_refreshes_files_after_delete():
    """Deleting a folder unmounts sole-path files to root — UI must reload files."""
    src = STORE.read_text(encoding="utf-8")
    block = src.split("removeFolder: async", 1)[1].split("refreshFiles:", 1)[0]
    assert "deleteFolder" in block
    assert "refreshFiles" in block
    assert "currentFolderFiles: []" not in block
