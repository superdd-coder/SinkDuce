"""Desktop PDF conversion uses the app payload, independent of host installations."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import plistlib
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def packer():
    path = ROOT / "desktop" / "office_runtime.py"
    assert path.is_file(), "The desktop build must prepare its own Writer runtime"
    spec = importlib.util.spec_from_file_location("desktop_office_runtime", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_desktop_prefers_its_bundled_writer_over_an_unrelated_host_install(tmp_path, monkeypatch):
    from src.chat_attachments import rendering

    binary = tmp_path / "Application Resources" / "soffice"
    binary.parent.mkdir()
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)
    monkeypatch.setenv("SINKDUCE_LIBREOFFICE", str(binary))
    monkeypatch.setattr(rendering.shutil, "which", lambda _: "/unrelated/soffice")
    assert rendering.office_binary() == str(binary)


def test_missing_explicit_desktop_runtime_does_not_launch_a_host_application(tmp_path, monkeypatch):
    from src.chat_attachments import rendering

    monkeypatch.setenv("SINKDUCE_LIBREOFFICE", str(tmp_path / "absent"))
    monkeypatch.setattr(rendering.shutil, "which", lambda _: "/unrelated/soffice")
    assert rendering.office_binary() is None


def test_non_executable_desktop_runtime_is_unavailable(tmp_path, monkeypatch):
    from src.chat_attachments import rendering

    binary = tmp_path / "soffice"
    binary.write_text("not executable")
    monkeypatch.setenv("SINKDUCE_LIBREOFFICE", str(binary))
    monkeypatch.setattr(rendering.shutil, "which", lambda _: "/unrelated/soffice")
    assert rendering.office_binary() is None


def test_server_keeps_using_its_installed_writer(monkeypatch):
    from src.chat_attachments import rendering

    monkeypatch.delenv("SINKDUCE_LIBREOFFICE", raising=False)
    monkeypatch.setattr(rendering.shutil, "which", lambda name: "/usr/bin/soffice" if name == "soffice" else None)
    assert rendering.office_binary() == "/usr/bin/soffice"


def test_installer_integrity_is_checked_before_mounting(tmp_path):
    module = packer()
    installer = tmp_path / "sample.dmg"
    installer.write_bytes(b"independent synthetic installer")
    digest = hashlib.sha256(installer.read_bytes()).hexdigest()
    module.verify_archive(installer, digest)
    installer.write_bytes(b"changed")
    with pytest.raises(ValueError, match="checksum"):
        module.verify_archive(installer, digest)


def test_vendor_signature_requirement_is_passed_as_an_expression(monkeypatch, tmp_path):
    module = packer()
    monkeypatch.setattr(module, "check_layout", lambda app: app)
    commands = []
    monkeypatch.setattr(module.subprocess, "run", lambda command, **kwargs: commands.append(command))
    module.verify_source(tmp_path / "Vendor.app")
    command = commands[0]
    requirement = command[command.index("-R") + 1]
    assert requirement.startswith("=anchor apple generic"), "codesign treats a requirement without '=' as a filename"


def test_corrupt_cached_installer_is_rejected_before_mounting(tmp_path, monkeypatch):
    module = packer()
    (tmp_path / module.ARCHIVE).write_bytes(b"corrupt installer")
    def unexpected_mount(*args, **kwargs):
        pytest.fail("An unverified installer must never be mounted")
    monkeypatch.setattr(module.subprocess, "run", unexpected_mount)
    with pytest.raises(ValueError, match="checksum"):
        module.prepare_runtime(tmp_path)


def test_unverified_download_is_removed_and_cannot_be_bundled(tmp_path, monkeypatch):
    module = packer()
    monkeypatch.setattr(module, "URLS", ("https://example.test/installer",))
    monkeypatch.setattr(module.urllib.request, "urlopen", lambda *args, **kwargs: io.BytesIO(b"incorrect archive"))
    with pytest.raises(RuntimeError, match="verified Writer runtime"):
        module.download_archive(tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_payload_keeps_writer_layout_and_license_notices(tmp_path):
    module = packer()
    source = tmp_path / "vendor" / "LibreOffice.app"
    binary = source / "Contents" / "MacOS" / "soffice"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)
    resources = source / "Contents" / "Resources"
    resources.mkdir()
    for name in ("LICENSE", "NOTICE"):
        (resources / name).write_text(f"Synthetic {name}")
    (source / "Contents" / "Info.plist").write_bytes(plistlib.dumps({"CFBundleShortVersionString": module.VERSION}))
    (resources / "linked-notice").symlink_to("NOTICE")
    target = tmp_path / "Application Resources" / "libreoffice"
    result = module.copy_runtime(source, target)
    assert result == target / "LibreOffice.app" / "Contents" / "MacOS" / "soffice"
    assert result.stat().st_mode & 0o111
    assert (target / "LibreOffice.app" / "Contents" / "Resources" / "NOTICE").read_text() == "Synthetic NOTICE"
    assert (target / "LibreOffice.app" / "Contents" / "Resources" / "linked-notice").is_symlink()
    assert binary.exists(), "Preparing a payload must preserve the host installation"


def test_packaging_includes_and_checks_writer_before_sealing_the_app():
    script = (ROOT / "desktop" / "package_macos.sh").read_text()
    bundle = script.find('desktop/office_runtime.py"')
    assert bundle >= 0, "Both desktop editions must include the conversion runtime"
    assert bundle < script.index('codesign --force --deep --sign - "$APP"')
    assert '--destination "$RES/libreoffice"' in script
    assert 'codesign --verify --deep --strict "$APP"' in script


def test_packager_verifies_pdf_with_the_bundled_python_and_writer():
    script = (ROOT / "desktop" / "package_macos.sh").read_text()
    assert 'desktop/verify_office_runtime.py"' in script
    assert 'PYTHONHOME="$RES/python"' in script
    assert 'SINKDUCE_LIBREOFFICE="$RES/libreoffice/LibreOffice.app/Contents/MacOS/soffice"' in script


def test_export_runtime_reuses_the_viewers_cjk_font_without_changing_the_source(tmp_path):
    module = packer()
    frontend = tmp_path / 'frontend'
    (frontend / 'assets').mkdir(parents=True)
    font = frontend / 'assets' / 'ppt-font-cjk-sample.otf'
    font.write_bytes(b'synthetic font fixture')
    destination = tmp_path / 'libreoffice'
    result = module.bundle_export_font(frontend, destination)
    assert result == destination / 'LibreOffice.app/Contents/Resources/fonts/truetype/NotoSansCJKSC.otf'
    assert result.read_bytes() == font.read_bytes()
    assert font.exists()
    with pytest.raises(ValueError, match='CJK font'):
        module.bundle_export_font(tmp_path / 'absent', destination)


def test_launcher_configures_the_app_private_runtime():
    launcher = (ROOT / "desktop" / "tauri" / "src-tauri" / "src" / "main.rs").read_text()
    assert "mod office_runtime;" in launcher
    assert "office_runtime::configure_backend(&mut cmd, app_root);" in launcher


def test_rust_runtime_configuration_contract(tmp_path):
    source = ROOT / "desktop" / "tauri" / "src-tauri" / "src" / "office_runtime.rs"
    assert source.is_file()
    output = tmp_path / "office-runtime-tests"
    subprocess.run(["rustc", "--test", str(source), "-o", str(output)], check=True, capture_output=True)
    result = subprocess.run([str(output)], check=True, capture_output=True, text=True)
    assert "test result: ok" in result.stdout
