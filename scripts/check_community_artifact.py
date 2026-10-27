#!/usr/bin/env python3
"""Validate the community identity and declared management feature assembly."""

from __future__ import annotations

import argparse
import json
import sys
import tarfile
from pathlib import Path
from zipfile import ZipFile

ENTERPRISE_ENDPOINTS = (
    b"/identity/roles",
    b"/identity/domain-acl",
    b"/identity/collection-acl/",
    b"/pairing/set-role",
)
MANAGEMENT_MODULES = (
    "ee/__init__.py",
    "ee/api/identity.py",
    "ee/api/pairing.py",
    "ee/identity/roles.py",
    "ee/identity/acl.py",
    "ee/pairing/service.py",
)


def management_enabled(marker: dict) -> bool:
    if not isinstance(marker, dict) or marker.get("edition") != "community":
        raise SystemExit("community edition marker missing or invalid")
    features = marker.get("features", ["permission_management"])
    if not isinstance(features, list) or any(feature != "permission_management" for feature in features):
        raise SystemExit("invalid community feature set")
    return "permission_management" in features


def check_js(name: str, contents: bytes, *, permission_management: bool = True) -> set[bytes]:
    found = {endpoint for endpoint in ENTERPRISE_ENDPOINTS if endpoint in contents}
    if not permission_management:
        for endpoint in found:
            raise SystemExit(f"enterprise endpoint {endpoint.decode()} found in {name}")
    return found


def check_management_contents(modules: set[str], endpoints: set[bytes]) -> None:
    missing = set(MANAGEMENT_MODULES) - modules
    if missing:
        raise SystemExit(f"management modules missing from community artifact: {sorted(missing)}")
    if set(ENTERPRISE_ENDPOINTS) - endpoints:
        raise SystemExit("management frontend missing from community artifact")


def check_tree(root: Path, *, frontend_only: bool = False, python_prefix: Path | None = None) -> None:
    marker = root / "edition.json"
    permission_management = management_enabled(
        json.loads(marker.read_text(encoding="utf-8")) if marker.is_file()
        else {"edition": "community"} if frontend_only else {}
    )
    if not frontend_only:
        if not permission_management and (root / "ee").exists():
            raise SystemExit(f"management source found in restricted core artifact: {root / 'ee'}")
        if not permission_management and python_prefix is not None:
            for site_packages in python_prefix.glob("lib/python*/site-packages"):
                if (site_packages / "ee").exists():
                    raise SystemExit(f"enterprise Python package found in {site_packages / 'ee'}")
    dist = root / "frontend" / "dist"
    if not dist.is_dir():
        raise SystemExit(f"frontend build missing: {dist}")
    endpoints = set()
    for js in dist.rglob("*.js"):
        endpoints.update(check_js(str(js), js.read_bytes(), permission_management=permission_management))
    if permission_management:
        modules = set(MANAGEMENT_MODULES) if frontend_only else {
            relative for relative in MANAGEMENT_MODULES if (root / relative).is_file()
        }
        check_management_contents(modules, endpoints)


def check_wheel(path: Path) -> None:
    with ZipFile(path) as wheel:
        missing = set(MANAGEMENT_MODULES) - set(wheel.namelist())
    if missing:
        raise SystemExit(f"management modules missing from community wheel: {sorted(missing)}")


def check_image_stream() -> None:
    """Inspect every Docker image layer from `docker save IMAGE | this-script image-stream`."""
    layers = 0
    js_files = 0
    marker_found = False
    permission_management = True
    modules: set[str] = set()
    endpoints: set[bytes] = set()
    ee_found = False
    with tarfile.open(fileobj=sys.stdin.buffer, mode="r|") as archive:
        for blob in archive:
            if not blob.isfile() or not blob.name.startswith("blobs/sha256/"):
                continue
            source = archive.extractfile(blob)
            if source is None:
                continue
            # OCI archives also contain JSON blobs. They are not layer tarballs.
            try:
                with tarfile.open(fileobj=source, mode="r|*") as layer:
                    layers += 1
                    for member in layer:
                        name = member.name.removeprefix("./")
                        if name == "app/ee" or name.startswith("app/ee/"):
                            ee_found = True
                            if member.isfile():
                                modules.add(name.removeprefix("app/"))
                        if name == "app/edition.json" and member.isfile():
                            file = layer.extractfile(member)
                            if file is None:
                                raise SystemExit("invalid community edition marker in Docker layer")
                            permission_management = management_enabled(json.loads(file.read()))
                            marker_found = True
                        if name.startswith("app/frontend/dist/") and name.endswith(".js") and member.isfile():
                            file = layer.extractfile(member)
                            if file is not None:
                                js_files += 1
                                endpoints.update(check_js(name, file.read()))
            except tarfile.ReadError:
                continue
    if not layers or not js_files or not marker_found:
        raise SystemExit(f"incomplete Docker image scan: {layers} layers, {js_files} frontend JS files, marker={marker_found}")
    if permission_management:
        check_management_contents(modules, endpoints)
    elif ee_found or endpoints:
        raise SystemExit("management implementation found in restricted core image")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    tree = sub.add_parser("tree")
    tree.add_argument("root", type=Path)
    tree.add_argument("--frontend-only", action="store_true")
    tree.add_argument("--python-prefix", type=Path)
    sub.add_parser("wheel").add_argument("path", type=Path)
    sub.add_parser("image-stream")
    args = parser.parse_args()
    if args.command == "tree":
        check_tree(args.root, frontend_only=args.frontend_only, python_prefix=args.python_prefix)
    elif args.command == "wheel":
        check_wheel(args.path)
    else:
        check_image_stream()


if __name__ == "__main__":
    main()
