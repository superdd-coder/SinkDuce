"""files.json legacy migration — new entries import, imported entries skip probing.

Run: pytest tests/test_file_mgmt_files_json_migration.py -v --tb=short
"""

from __future__ import annotations

import json
import shutil
import sqlite3

import pytest

from src.file_mgmt.store import COLLECTIONS_DIR, init_collection_db


COLL = "fjson-mig-1"


@pytest.fixture(autouse=True)
def cleanup_test_collection():
    yield
    d = COLLECTIONS_DIR / COLL
    if d.is_dir():
        shutil.rmtree(d, ignore_errors=True)


def _setup() -> None:
    from src.collections.store import create_collection_meta

    create_collection_meta(COLL, "Test files.json migration")
    init_collection_db(COLL)


def _write_files_json(entries: dict) -> None:
    fj = COLLECTIONS_DIR / COLL / "files.json"
    fj.parent.mkdir(parents=True, exist_ok=True)
    fj.write_text(json.dumps(entries, ensure_ascii=False))


def _conn() -> sqlite3.Connection:
    from src.file_mgmt.store import get_db

    return get_db(COLL)


def _entry(label: str, source: str = "") -> dict:
    e = {"source": source or f"__file__:{label}", "source_label": f"{label}.md"}
    e["storage_name"] = f"{label}.md"
    e["file_type"] = "file"
    return e


def test_new_entries_are_imported():
    """A never-seen files.json entry lands in SQLite (file + version row)."""
    _setup()
    _write_files_json({"fjsonnew1": _entry("hello")})

    from src.file_mgmt.store import _migrate_files_json_import

    _migrate_files_json_import(COLL)

    with _conn() as conn:
        row = conn.execute(
            "SELECT current_version_id, unsupported FROM files WHERE file_id=?",
            ("fjsonnew1",),
        ).fetchone()
        assert row is not None, "new files.json entry must be imported"
        assert row["current_version_id"]
        ver = conn.execute(
            "SELECT storage_file_id FROM file_versions WHERE version_id=?",
            (row["current_version_id"],),
        ).fetchone()
        assert ver is not None and ver["storage_file_id"] == "hello.md"


def test_imported_entries_skip_disk_probing(monkeypatch, caplog):
    """Repeat migration over already-imported entries must not touch the disk.

    The legacy loop re-probed every files.json entry (discover_content_blob /
    resolve_version_blob) on every listing request — seconds per folder open
    in Docker. Once everything is imported, the next run is a no-op that
    must perform zero blob probing.
    """
    import src.file_mgmt.storage_paths as sp

    _setup()
    _write_files_json(
        {
            "fjsonold1": _entry("alpha"),
            "fjsonold2": _entry("Meeting X", source="__note__:note-1"),
        }
    )

    from src.file_mgmt.store import _migrate_files_json_import

    _migrate_files_json_import(COLL)  # first run imports both

    with _conn() as conn:
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM files WHERE file_id IN ('fjsonold1','fjsonold2')"
        ).fetchone()["c"]
    assert n == 2, "setup: both entries must be imported before the probe check"

    probes = {"discover": 0, "resolve": 0}

    def _no_discover(*a, **k):
        probes["discover"] += 1
        return None

    def _no_resolve(*a, **k):
        probes["resolve"] += 1
        return None

    monkeypatch.setattr(sp, "discover_content_blob", _no_discover)
    monkeypatch.setattr(sp, "resolve_version_blob", _no_resolve)

    with caplog.at_level("ERROR"):
        _migrate_files_json_import(COLL)

    assert not any(
        "Migration of files.json" in r.message for r in caplog.records
    ), "migration must not fail silently"
    assert probes == {"discover": 0, "resolve": 0}, (
        "already-imported entries must be skipped without disk probing; "
        f"got {probes}"
    )


def test_mixed_run_imports_only_new(monkeypatch):
    """A run with both imported and new entries probes/imports new ones only."""
    import src.file_mgmt.storage_paths as sp

    _setup()
    _write_files_json({"fjsonmix1": _entry("old")})

    from src.file_mgmt.store import _migrate_files_json_import

    _migrate_files_json_import(COLL)

    new_entry = _entry("fresh")

    def _fake_discover(collection_id, file_id, version_id=None):
        assert file_id == "fjsonmix2", "probing must target new entries only"
        return None

    monkeypatch.setattr(sp, "discover_content_blob", _fake_discover)

    _write_files_json({"fjsonmix1": _entry("old"), "fjsonmix2": new_entry})
    _migrate_files_json_import(COLL)

    with _conn() as conn:
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM files WHERE file_id='fjsonmix2'"
        ).fetchone()["c"]
    assert n == 1, "new entry must still be imported alongside existing ones"
