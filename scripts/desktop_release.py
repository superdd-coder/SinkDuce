#!/usr/bin/env python3
"""Keep Docker and the macOS desktop build on the same application tag.

Source of truth: pyproject.toml version.
GitHub Release asset: SinkDuce-macos-arm64.dmg

Usage (repo root; wrapper finds Python 3.10+):
  ./scripts/desktop_release version
  ./scripts/desktop_release check-git
  ./scripts/desktop_release stamp
  ./scripts/desktop_release upload
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

DESKTOP_DMG_ASSET = "SinkDuce-macos-arm64.dmg"
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


def upload_command(tag: str, dmg_path: Path, asset_name: str = DESKTOP_DMG_ASSET) -> list[str]:
    return [
        "gh",
        "release",
        "upload",
        tag,
        f"{dmg_path}#{asset_name}",
        "--clobber",
    ]


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
            dmg = root / "desktop" / "dist" / "SinkDuce.dmg"
            if not dmg.is_file():
                raise ReleaseGitError(
                    f"missing {dmg}; run ./desktop/package_macos.sh on the v{version} tag first"
                )
            tag = f"v{version}"
            subprocess.run(upload_command(tag, dmg), check=True)
            print(f"uploaded {DESKTOP_DMG_ASSET} to {tag}")
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
