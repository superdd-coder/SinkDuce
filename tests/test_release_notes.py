"""CHANGELOG section extraction for latest.json release notes (r2_upload)."""

import importlib.util
from pathlib import Path

_R2 = Path(__file__).resolve().parents[1] / "desktop" / "r2_upload.py"
_spec = importlib.util.spec_from_file_location("r2_upload", _R2)
r2_upload = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(r2_upload)


def _write(tmp_path, body: str) -> Path:
    p = tmp_path / "CHANGELOG.md"
    p.write_text(body, encoding="utf-8")
    return p


def test_extracts_matching_section(tmp_path):
    p = _write(
        tmp_path,
        "# Changelog\n\n## v1.5.1\n- A\n- B\n\n## v1.5.0\n- old\n",
    )
    assert r2_upload.extract_changelog_notes(p, "1.5.1") == "- A\n- B"


def test_missing_section_returns_empty(tmp_path):
    p = _write(tmp_path, "# Changelog\n\n## v1.5.0\n- old\n")
    assert r2_upload.extract_changelog_notes(p, "9.9.9") == ""


def test_no_changelog_file_returns_empty(tmp_path):
    assert r2_upload.extract_changelog_notes(tmp_path / "absent.md", "1.5.1") == ""


def test_version_prefix_does_not_overmatch(tmp_path):
    p = _write(tmp_path, "## v1.5.10\n- wrong\n\n## v1.5.1\n- right\n")
    assert r2_upload.extract_changelog_notes(p, "1.5.1") == "- right"


def test_prefix_without_v_matches(tmp_path):
    p = _write(tmp_path, "## 1.5.2\n- bare heading\n")
    assert r2_upload.extract_changelog_notes(p, "1.5.2") == "- bare heading"


def test_stops_at_next_heading_of_any_level(tmp_path):
    p = _write(
        tmp_path,
        "## v1.5.1\n- keep\n### detail\n- also keep\n## v1.5.0\n- stop\n",
    )
    notes = r2_upload.extract_changelog_notes(p, "1.5.1")
    assert "- keep" in notes and "- also keep" in notes and "- stop" not in notes
