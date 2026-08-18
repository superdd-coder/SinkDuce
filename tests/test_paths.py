"""Path confinement and resource-id allowlist."""

from __future__ import annotations

from pathlib import Path

import pytest


def test_confine_allows_child(tmp_path: Path):
    from src.paths import confine

    child = tmp_path / "a.txt"
    child.write_text("ok")
    assert confine(child, tmp_path) == child.resolve()


def test_confine_rejects_parent_escape(tmp_path: Path):
    from src.paths import confine

    with pytest.raises(ValueError, match="escapes"):
        confine(tmp_path / ".." / "etc" / "passwd", tmp_path)


def test_confine_rejects_prefix_trick(tmp_path: Path):
    from src.paths import confine

    root = tmp_path / "tmp"
    root.mkdir()
    impostor = tmp_path / "tmpfoo"
    impostor.mkdir()
    (impostor / "x").write_text("no")
    with pytest.raises(ValueError, match="escapes"):
        confine(impostor / "x", root)


def test_assert_resource_id_accepts_hex_and_col():
    from src.paths import assert_resource_id

    assert assert_resource_id("col_abc123") == "col_abc123"
    assert assert_resource_id("a" * 32) == "a" * 32


def test_assert_resource_id_rejects_traversal_and_empty():
    from src.paths import assert_resource_id

    with pytest.raises(ValueError):
        assert_resource_id("../x")
    with pytest.raises(ValueError):
        assert_resource_id("")
    with pytest.raises(ValueError):
        assert_resource_id("has space")
    with pytest.raises(ValueError):
        assert_resource_id("a/b")


def test_assert_data_rel_rejects_config_and_qdrant(tmp_path: Path, monkeypatch):
    from src import paths as paths_mod

    data = tmp_path / "data"
    data.mkdir()
    (data / "config.yaml").write_text("secret: 1")
    (data / "qdrant").mkdir()
    (data / "qdrant" / "x").write_text("db")
    ok = data / "collections" / "c1" / "files" / "f.bin"
    ok.parent.mkdir(parents=True)
    ok.write_bytes(b"x")

    monkeypatch.setattr(paths_mod, "DATA_DIR", data)

    with pytest.raises(ValueError):
        paths_mod.assert_readable_data_file(data / "config.yaml")
    with pytest.raises(ValueError):
        paths_mod.assert_readable_data_file(data / "qdrant" / "x")
    assert paths_mod.assert_readable_data_file(ok) == ok.resolve()


def test_delete_meeting_rejects_traversal():
    from src.meeting.store import delete_meeting

    with pytest.raises(ValueError):
        delete_meeting("..")
