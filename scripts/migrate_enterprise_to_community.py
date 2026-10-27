#!/usr/bin/env python3
"""Back up and remove permission rules for the legacy restricted core profile.

Full Community upgrades retain these rules and do not need this conversion.

Dry run: uv run --frozen python scripts/migrate_enterprise_to_community.py
Apply:   uv run --frozen python scripts/migrate_enterprise_to_community.py --apply --confirm-loss-of-isolation
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.atomic_io import write_text_atomic
from src.edition import assert_community_compatible
from src.identity.acl import default_domain_acl


def affected_files(data_dir: Path) -> list[Path]:
    files = [
        data_dir / "identity" / name
        for name in ("edition.json", "roles.json", "domain_acl.json")
    ]
    files.append(data_dir / "pairing" / "tokens.json")
    files.extend(sorted((data_dir / "collections").glob("*/meta.json")))
    return [path for path in files if path.is_file()]


def _read_object(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"cannot read {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"invalid JSON object in {path}")
    return value


def migrate(data_dir: Path, backup: Path, *, confirm: bool) -> None:
    """Apply an explicit downgrade after an exclusive, mode-0600 backup."""
    if not confirm:
        raise ValueError("confirmation of lost permission isolation is required")
    data_dir = data_dir.resolve()
    files = affected_files(data_dir)
    if any(path.is_symlink() for path in files):
        raise ValueError("permission files must be regular files, not symlinks")
    # Validate every file before any write. Fail closed on unknown shapes.
    parsed = {path: _read_object(path) for path in files}
    roles_path = data_dir / "identity" / "roles.json"
    domain_path = data_dir / "identity" / "domain_acl.json"
    token_path = data_dir / "pairing" / "tokens.json"
    if roles_path in parsed and not isinstance(parsed[roles_path].get("roles"), list):
        raise ValueError(f"invalid roles in {roles_path}")
    if domain_path in parsed and not isinstance(parsed[domain_path].get("domains"), dict):
        raise ValueError(f"invalid domain ACL in {domain_path}")
    if token_path in parsed and not isinstance(parsed[token_path].get("tokens"), list):
        raise ValueError(f"invalid tokens in {token_path}")
    for path, record in parsed.items():
        if path == token_path and not all(isinstance(item, dict) for item in record["tokens"]):
            raise ValueError(f"invalid tokens in {path}")

    backup.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "wb") as file, tarfile.open(fileobj=file, mode="w:gz") as archive:
        for path in files:
            archive.add(path, arcname=path.relative_to(data_dir))

    if roles_path in parsed:
        write_text_atomic(roles_path, json.dumps({"version": 1, "roles": []}, indent=2) + "\n")
    if domain_path in parsed:
        write_text_atomic(domain_path, json.dumps({"version": 1, "domains": default_domain_acl()}, indent=2) + "\n")
    if token_path in parsed:
        raw = parsed[token_path]
        stamp = datetime.now(timezone.utc).isoformat()
        for item in raw["tokens"]:
            if item.get("name") != "__local__" and item.get("role", "member") != "member":
                item["revoked_at"] = item.get("revoked_at") or stamp
        write_text_atomic(token_path, json.dumps(raw, ensure_ascii=False, indent=2) + "\n")
    for path, record in parsed.items():
        if path.parent.parent == data_dir / "collections" and "acl" in record:
            del record["acl"]
            write_text_atomic(path, json.dumps(record, ensure_ascii=False, indent=2) + "\n")

    # Remove the marker last so a partial conversion still blocks restricted core startup.
    (data_dir / "identity" / "edition.json").unlink(missing_ok=True)
    assert_community_compatible(data_dir)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-loss-of-isolation", action="store_true")
    args = parser.parse_args()
    data_dir = args.data_dir.resolve()
    files = affected_files(data_dir)
    print(f"Permission files to back up and migrate: {len(files)}")
    for path in files:
        print(path.relative_to(data_dir))
    if not args.apply:
        print("Dry run only. Existing data was not changed.")
        return
    if not args.confirm_loss_of_isolation:
        parser.error("--apply requires --confirm-loss-of-isolation")
    backup = args.backup or data_dir / "edition-backups" / f"permissions-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.tar.gz"
    migrate(data_dir, backup, confirm=True)
    print(f"Backup: {backup}")


if __name__ == "__main__":
    main()
