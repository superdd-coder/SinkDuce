"""Release helpers: one git tag = Docker image = desktop DMG asset."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "desktop_release.py"


def _load():
    spec = importlib.util.spec_from_file_location("desktop_release", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_desktop_release_script_exists():
    assert SCRIPT.is_file()


def test_reads_pyproject_version(tmp_path: Path):
    mod = _load()
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nname = "sinkduce"\nversion = "1.2.3"\n', encoding="utf-8")
    assert mod.read_pyproject_version(pyproject) == "1.2.3"


def test_stable_dmg_asset_name():
    mod = _load()
    assert mod.DESKTOP_DMG_ASSET == "SinkDuce-macos-arm64.dmg"


def test_upload_command_uses_github_name_alias(tmp_path: Path):
    mod = _load()
    dmg = tmp_path / "SinkDuce.dmg"
    dmg.write_bytes(b"x")
    cmd = mod.upload_command("v1.2.3", dmg)
    assert cmd[:4] == ["gh", "release", "upload", "v1.2.3"]
    assert f"{dmg}#{mod.DESKTOP_DMG_ASSET}" in cmd
    assert "--clobber" in cmd


def test_check_release_git_rejects_dirty_and_wrong_tag():
    mod = _load()
    with pytest.raises(mod.ReleaseGitError, match="dirty"):
        mod.check_release_git(version="1.2.3", dirty=True, exact_tag="v1.2.3", allow_dev=False)
    with pytest.raises(mod.ReleaseGitError, match="v1.2.3"):
        mod.check_release_git(version="1.2.3", dirty=False, exact_tag=None, allow_dev=False)
    with pytest.raises(mod.ReleaseGitError, match="v1.2.3"):
        mod.check_release_git(version="1.2.3", dirty=False, exact_tag="v1.2.2", allow_dev=False)
    mod.check_release_git(version="1.2.3", dirty=False, exact_tag="v1.2.3", allow_dev=False)
    mod.check_release_git(version="1.2.3", dirty=True, exact_tag=None, allow_dev=True)


def test_assert_frontend_package_version_matches(tmp_path: Path):
    mod = _load()
    pkg = tmp_path / "frontend" / "package.json"
    pkg.parent.mkdir()
    pkg.write_text(json.dumps({"name": "frontend", "version": "1.2.2"}), encoding="utf-8")
    with pytest.raises(mod.ReleaseGitError, match="package.json"):
        mod.assert_version_files(tmp_path, "1.2.3")
    pkg.write_text(json.dumps({"name": "frontend", "version": "1.2.3"}), encoding="utf-8")
    mod.assert_version_files(tmp_path, "1.2.3")


def test_stamp_desktop_versions(tmp_path: Path):
    mod = _load()
    tauri = tmp_path / "tauri.conf.json"
    cargo = tmp_path / "Cargo.toml"
    tauri.write_text(
        json.dumps({"productName": "SinkDuce", "version": "0.0.1"}),
        encoding="utf-8",
    )
    cargo.write_text(
        '[package]\nname = "sinkduce-desktop"\nversion = "0.0.1"\n\n'
        '[dependencies]\ntauri-build = { version = "2" }\n',
        encoding="utf-8",
    )
    mod.stamp_desktop_versions(tmp_path, "1.2.3")
    assert json.loads(tauri.read_text(encoding="utf-8"))["version"] == "1.2.3"
    cargo_text = cargo.read_text(encoding="utf-8")
    assert 'version = "1.2.3"' in cargo_text
    assert 'tauri-build = { version = "2" }' in cargo_text
