"""Installer contracts exercised with temporary application directories only."""
import base64
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "desktop/install.sh"


def _run_functions(tmp_path, body):
    script = SCRIPT.read_text()
    definitions, marker, _ = script.rpartition('\nmain "$@"')
    assert marker, "Installer functions must be testable without running a live installation"
    return subprocess.run(["sh", "-c", definitions + "\n" + body], cwd=tmp_path, capture_output=True)


def test_unusable_replacement_preserves_current_app(tmp_path):
    current = tmp_path / "current.app"
    current.mkdir()
    (current / "marker").write_text("initial")
    result = _run_functions(tmp_path, 'replace_app missing.app current.app previous.app')
    assert result.returncode != 0
    assert (current / "marker").read_text() == "initial"


def test_install_swaps_only_after_replacement_is_ready(tmp_path):
    for name in ("ready.app", "current.app"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "marker").write_text(name)
    result = _run_functions(tmp_path, 'replace_app ready.app current.app previous.app')
    assert result.returncode == 0
    assert (tmp_path / "current.app/marker").read_text() == "ready.app"
    assert (tmp_path / "previous.app/marker").read_text() == "current.app"


def test_installer_uses_desktop_updater_public_key():
    config = json.loads((ROOT / "desktop/tauri/src-tauri/tauri.conf.json").read_text())
    key = base64.b64decode(config["plugins"]["updater"]["pubkey"]).decode().splitlines()[1]
    script = SCRIPT.read_text()
    assert key in script
    assert "com.apple.quarantine" not in script


def test_failed_move_restores_previous_bundle(tmp_path):
    for name in ("current.app", "ready.app"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "marker").write_text(name)
    body = '''
mv() {
    if [ "$1" = ready.app ]; then return 1; fi
    /bin/mv "$@"
}
replace_app ready.app current.app previous.app
'''
    assert _run_functions(tmp_path, body).returncode != 0
    assert (tmp_path / "current.app/marker").read_text() == "current.app"
    assert (tmp_path / "ready.app/marker").read_text() == "ready.app"


def test_version_policy_rejects_rollback(tmp_path):
    assert _run_functions(tmp_path, "is_downgrade 2.10.1 2.9.9").returncode == 0
    assert _run_functions(tmp_path, "is_downgrade 2.9.9 2.10.1").returncode != 0
    assert _run_functions(tmp_path, "is_downgrade 2.10.1 2.10.1").returncode != 0


def test_publication_requires_signed_payload(tmp_path):
    import sys
    result = subprocess.run([sys.executable, ROOT / "desktop/r2_upload.py", "--dmg",
                             tmp_path / "sample.dmg", "--version", "1.2.3"], capture_output=True)
    assert result.returncode == 2
    assert b"--app-tar-gz" in result.stderr and b"--app-tar-gz-sig" in result.stderr
