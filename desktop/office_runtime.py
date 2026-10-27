#!/usr/bin/env python3
"""Prepare a pinned, app-private Writer runtime; end users need no Office install."""
from __future__ import annotations

import argparse
import hashlib
import http.client
import plistlib
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path

VERSION = "26.8.1"
ARCHIVE = f"LibreOffice_{VERSION}_MacOS_aarch64.dmg"
SHA256 = "ca074e0b13571efb30a31fc531c5f95e997030637325e5d5de92d67aefdf0dfc"
URLS = (
    f"https://download.documentfoundation.org/libreoffice/stable/{VERSION}/mac/aarch64/{ARCHIVE}",
    f"https://downloadarchive.documentfoundation.org/libreoffice/old/{VERSION}/mac/aarch64/{ARCHIVE}",
)
VENDOR_REQUIREMENT = '=anchor apple generic and certificate leaf[subject.OU] = "7P5S3ZLCN7"'


def verify_archive(path: Path, expected: str = SHA256) -> None:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != expected:
        raise ValueError(f"Writer installer checksum mismatch: {path.name}")


def check_layout(app: Path) -> Path:
    binary = app / "Contents/MacOS/soffice"
    if not binary.is_file() or not binary.stat().st_mode & 0o111:
        raise ValueError("Writer runtime is missing its executable")
    for name in ("LICENSE", "NOTICE"):
        if not (app / "Contents/Resources" / name).is_file():
            raise ValueError(f"Writer runtime is missing {name}")
    with (app / "Contents/Info.plist").open("rb") as source:
        version = str(plistlib.load(source).get("CFBundleShortVersionString", ""))
    if version != VERSION and not version.startswith(VERSION + "."):
        raise ValueError(f"Expected Writer {VERSION}, found {version}")
    return binary


def verify_source(app: Path) -> None:
    check_layout(app)
    subprocess.run(["codesign", "--verify", "--deep", "--strict", "-R", VENDOR_REQUIREMENT, str(app)],
                   check=True, capture_output=True, timeout=120)


def copy_runtime(source: Path, destination: Path) -> Path:
    """Retain the vendor layout, frameworks and license files as one sealed unit."""
    check_layout(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".writer-", dir=destination.parent) as work:
        staged = Path(work) / "payload"
        shutil.copytree(source, staged / "LibreOffice.app", symlinks=True)
        if destination.exists():
            shutil.rmtree(destination)
        staged.replace(destination)
    return destination / "LibreOffice.app/Contents/MacOS/soffice"


def bundle_export_font(frontend: Path, destination: Path) -> Path:
    """Writer's own font search path also needs the viewer's existing CJK font."""
    fonts = sorted(frontend.glob('**/ppt-font-cjk*.otf'))
    if not fonts:
        raise ValueError('The frontend CJK font is missing from the desktop payload')
    target = destination / 'LibreOffice.app/Contents/Resources/fonts/truetype/NotoSansCJKSC.otf'
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(fonts[0], target)
    return target


def download_archive(cache: Path) -> Path:
    archive = cache / ARCHIVE
    if archive.is_file():
        verify_archive(archive)
        return archive
    last_error = None
    for url in URLS:
        with tempfile.NamedTemporaryFile(dir=cache, suffix=".part", delete=False) as output:
            temporary = Path(output.name)
        try:
            print(f"Downloading Writer {VERSION} from the vendor", flush=True)
            with urllib.request.urlopen(url, timeout=60) as response, temporary.open("wb") as output:
                total = 0
                for chunk in iter(lambda: response.read(1024 * 1024), b""):
                    total += len(chunk)
                    if total > 1024 * 1024 * 1024:
                        raise ValueError("Writer installer exceeds the expected size")
                    output.write(chunk)
            verify_archive(temporary)
            temporary.replace(archive)
            return archive
        except (OSError, ValueError, http.client.HTTPException) as error:
            last_error = error
        finally:
            temporary.unlink(missing_ok=True)
    raise RuntimeError("Could not download the verified Writer runtime") from last_error


def prepare_runtime(cache: Path) -> Path:
    cache.mkdir(parents=True, exist_ok=True)
    app = cache / "runtime/LibreOffice.app"
    if app.is_dir():
        verify_source(app)
        return app
    installer = download_archive(cache)
    result = subprocess.run(["hdiutil", "attach", "-readonly", "-nobrowse", "-plist", str(installer)],
                            check=True, capture_output=True, timeout=120)
    mounts = [Path(item["mount-point"]) for item in plistlib.loads(result.stdout).get("system-entities", [])
              if item.get("mount-point")]
    try:
        source = next((mount / "LibreOffice.app" for mount in mounts if (mount / "LibreOffice.app").is_dir()), None)
        if source is None:
            raise ValueError("Writer application was not found in the vendor installer")
        verify_source(source)
        copy_runtime(source, cache / "runtime")
    finally:
        for mount in mounts:
            subprocess.run(["hdiutil", "detach", str(mount)], check=True, capture_output=True, timeout=60)
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--frontend-dist", type=Path, required=True)
    options = parser.parse_args()
    app = prepare_runtime(options.cache.resolve())
    binary = copy_runtime(app, options.destination.resolve())
    bundle_export_font(options.frontend_dist.resolve(), options.destination.resolve())
    print(f"Bundled Writer {VERSION}: {binary}")


if __name__ == "__main__":
    main()
