"""Update dialog: Docker keeps compose; desktop downloads the Release DMG."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIALOG = ROOT / "frontend" / "src" / "components" / "layout" / "update-dialog.tsx"
HOOK = ROOT / "frontend" / "src" / "hooks" / "use-update-check.ts"
CLIENT = ROOT / "frontend" / "src" / "api" / "client.ts"
PICKER = ROOT / "frontend" / "src" / "lib" / "update-release.ts"


def test_picker_module_exports_versioned_dmg_name():
    text = PICKER.read_text(encoding="utf-8")
    assert "desktopDmgAssetName" in text
    assert "SinkDuce-macos-arm64-v" in text
    assert "export function pickDesktopDownloadUrl" in text
    assert "export function shouldOfferUpdate" in text


def test_github_release_type_keeps_assets():
    text = CLIENT.read_text(encoding="utf-8")
    assert "assets" in text
    assert "browser_download_url" in text


def test_update_check_attaches_download_url_and_desktop_flag():
    text = HOOK.read_text(encoding="utf-8")
    assert "pickDesktopDownloadUrl" in text
    assert "shouldOfferUpdate" in text
    assert "getHealth" in text
    assert "downloadUrl" in text
    assert "desktop" in text


def test_dialog_branches_desktop_download_vs_docker_compose():
    text = DIALOG.read_text(encoding="utf-8")
    assert "update.desktop" in text
    assert "update.downloadUrl" in text
    assert "Download" in text
    assert "git pull &&" in text
    assert "docker compose pull" in text
    assert "Cmd+Q" in text
