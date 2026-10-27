#!/usr/bin/env python3
"""Keep Docker and the macOS desktop build on the same application tag.

Source of truth: pyproject.toml version.
GitHub Release asset: SinkDuce-macos-arm64-vX.Y.Z.dmg (DMG only, never the .app).

Usage (repo root; wrapper finds Python 3.10+):
  ./desktop/desktop_release version
  ./desktop/desktop_release check-git
  ./desktop/desktop_release stamp
  ./desktop/desktop_release upload       # GitHub Release + Cloudflare R2
  ./desktop/desktop_release upload-r2    # R2 only (retry)

R2 config lives in desktop/r2.env or the environment (see desktop/r2.env.template);
uploading needs an R2 API token (S3 keys) and an interpreter with boto3.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

def desktop_dmg_asset(version: str) -> str:
    ver = (version or "").strip().lstrip("vV")
    return f"SinkDuce-macos-arm64-v{ver}.dmg"
_VERSION_RE = re.compile(r'^version\s*=\s*"([^"]+)"', re.MULTILINE)


class ReleaseGitError(RuntimeError):
    pass


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def read_pyproject_version(pyproject: Path) -> str:
    text = pyproject.read_text(encoding="utf-8")
    match = _VERSION_RE.search(text)
    if not match:
        raise ReleaseGitError(f"no version in {pyproject}")
    return match.group(1)


def check_release_git(
    *,
    version: str,
    dirty: bool,
    exact_tag: str | None,
    allow_dev: bool,
) -> None:
    if allow_dev:
        return
    if dirty:
        raise ReleaseGitError(
            "working tree is dirty; commit or stash, or set SINKDUCE_PACK_DEV=1 for a local pack"
        )
    expected = f"v{version}"
    if exact_tag != expected:
        raise ReleaseGitError(
            f"HEAD is not {expected} (got {exact_tag or 'no tag'}); "
            "checkout the release tag, or set SINKDUCE_PACK_DEV=1"
        )


def assert_version_files(root: Path, version: str) -> None:
    pkg_path = root / "frontend" / "package.json"
    pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
    pkg_ver = str(pkg.get("version") or "")
    if pkg_ver != version:
        raise ReleaseGitError(
            f"frontend/package.json version {pkg_ver!r} != pyproject {version!r}"
        )


def stamp_desktop_versions(tauri_dir: Path, version: str) -> None:
    conf_path = tauri_dir / "tauri.conf.json"
    if conf_path.is_file():
        data = json.loads(conf_path.read_text(encoding="utf-8"))
        data["version"] = version
        conf_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    cargo_path = tauri_dir / "Cargo.toml"
    if cargo_path.is_file():
        cargo_path.write_text(
            _stamp_cargo_package_version(cargo_path.read_text(encoding="utf-8"), version),
            encoding="utf-8",
        )


def _stamp_cargo_package_version(text: str, version: str) -> str:
    in_package = False
    out: list[str] = []
    replaced = False
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_package = stripped == "[package]"
        if in_package and not replaced and re.match(r'^version\s*=\s*"', stripped):
            nl = "\n" if line.endswith("\n") else ""
            out.append(f'version = "{version}"{nl}')
            replaced = True
            continue
        out.append(line)
    return "".join(out)


def upload_command(tag: str, dmg_path: Path, asset_name: str | None = None) -> list[str]:
    # Upload a file whose basename is the GitHub asset name.
    # `path#name` on this gh version sets label, not name — then
    # pickDesktopDownloadUrl misses the DMG and the app never prompts.
    path = Path(dmg_path)
    name = asset_name or desktop_dmg_asset(tag)
    if path.name != name:
        raise ReleaseGitError(f"upload path must be named {name}, got {path.name}")
    return [
        "gh",
        "release",
        "upload",
        tag,
        str(path),
        "--clobber",
    ]


_BOTO3_PY_CANDIDATES = (
    sys.executable,
    "python3",
    "/opt/homebrew/bin/python3",
    "/usr/bin/python3",
)


def find_boto3_python() -> str:
    for candidate in _BOTO3_PY_CANDIDATES:
        if not candidate:
            continue
        try:
            result = subprocess.run(
                [candidate, "-c", "import boto3"],
                capture_output=True,
                timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0:
            return candidate
    raise ReleaseGitError(
        "no interpreter with boto3 found; install it into one of: "
        + ", ".join(str(c) for c in _BOTO3_PY_CANDIDATES)
    )


def r2_upload_release(root: Path, version: str, dmg_path: Path) -> None:
    script = root / "desktop" / "r2_upload.py"
    if not script.is_file():
        raise ReleaseGitError(f"missing {script}")
    py = find_boto3_python()
    cmd = [
        py,
        str(script),
        "--dmg",
        str(dmg_path),
        "--version",
        version,
    ]
    # In-app updater payload (…app.tar.gz + .sig) produced by
    # package_macos.sh next to the DMG; optional so an old DMG-only retry
    # still works (the release just loses the one-click update path).
    tar_gz = dmg_path.with_name(dmg_path.name.replace(".dmg", ".app.tar.gz"))
    tar_gz_sig = tar_gz.with_name(tar_gz.name + ".sig")
    if tar_gz.is_file() and tar_gz_sig.is_file():
        cmd += ["--app-tar-gz", str(tar_gz), "--app-tar-gz-sig", str(tar_gz_sig)]
    elif tar_gz.is_file() != tar_gz_sig.is_file():
        print(f"r2_upload: WARN {tar_gz.name} / .sig only half present — skipping updater payload")
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=3600,
    )
    if result.stdout.strip():
        print(result.stdout.strip())
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise ReleaseGitError(
            f"R2 upload failed (exit {result.returncode}); "
            f"GitHub release is already updated — retry just R2 with "
            f"./desktop/desktop_release upload-r2\n{detail}"
        )


def prepare_release_dmg(root: Path, version: str) -> tuple[str, Path]:
    dmg = root / "desktop" / "dist" / "SinkDuce.dmg"
    if not dmg.is_file():
        raise ReleaseGitError(
            f"missing {dmg}; run ./desktop/package_macos.sh on the v{version} tag first"
        )
    asset = desktop_dmg_asset(version)
    named = dmg.with_name(asset)
    if named.resolve() != dmg.resolve():
        shutil.copy2(dmg, named)
    return asset, named


def git_is_dirty(root: Path) -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return bool(result.stdout.strip())


def git_exact_tag(root: Path) -> str | None:
    result = subprocess.run(
        ["git", "describe", "--exact-match", "--tags", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    tag = result.stdout.strip()
    return tag or None


def _gate(root: Path, *, allow_dev: bool) -> str:
    version = read_pyproject_version(root / "pyproject.toml")
    check_release_git(
        version=version,
        dirty=git_is_dirty(root),
        exact_tag=git_exact_tag(root),
        allow_dev=allow_dev,
    )
    if not allow_dev:
        assert_version_files(root, version)
    return version


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    cmd = args[0] if args else "help"
    root = repo_root()
    try:
        if cmd == "version":
            print(read_pyproject_version(root / "pyproject.toml"))
            return 0
        if cmd == "check-git":
            allow_dev = os.environ.get("SINKDUCE_PACK_DEV") == "1"
            version = _gate(root, allow_dev=allow_dev)
            print(f"ok  v{version}" + (" (dev)" if allow_dev else ""))
            return 0
        if cmd == "stamp":
            version = read_pyproject_version(root / "pyproject.toml")
            stamp_desktop_versions(root / "desktop" / "tauri" / "src-tauri", version)
            print(f"stamped desktop shell {version}")
            return 0
        if cmd == "upload":
            version = _gate(root, allow_dev=False)
            tag = f"v{version}"
            asset, named = prepare_release_dmg(root, version)
            subprocess.run(upload_command(tag, named, asset), check=True)
            print(f"uploaded {asset} to {tag}")
            r2_upload_release(root, version, named)
            return 0
        if cmd == "upload-r2":
            version = _gate(root, allow_dev=False)
            asset, named = prepare_release_dmg(root, version)
            r2_upload_release(root, version, named)
            return 0
        print(__doc__.strip(), file=sys.stderr)
        return 2
    except ReleaseGitError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        print(f"error: command failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
