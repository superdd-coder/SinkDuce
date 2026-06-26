#!/usr/bin/env python3
"""Migrate old data/uploads/* to data/files/{file_id}/ structure.

Old format (pre-file_id):
  data/uploads/contract.docx
  data/uploads/contract.docx.parsed.txt

New format:
  data/files/{file_id}/
    original          ← renamed from contract.docx
    parsed.txt        ← renamed from contract.docx.parsed.txt
    filename.txt      ← contains original filename (for preview)

Existing files in Qdrant with source={filename} are NOT migrated —
they remain as legacy plain-filename sources. Only the on-disk layout
changes for new uploads.
"""

import shutil
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OLD_DIR = ROOT / "data" / "uploads"
NEW_DIR = ROOT / "data" / "files"


def main():
    if not OLD_DIR.exists():
        print(f"{OLD_DIR} does not exist — nothing to migrate")
        return

    files = sorted(OLD_DIR.iterdir())
    if not files:
        print(f"{OLD_DIR} is empty — nothing to migrate")
        return

    NEW_DIR.mkdir(parents=True, exist_ok=True)
    migrated = 0

    for f in files:
        if not f.is_file():
            continue
        name = f.name

        # Determine base name (strip .parsed.* suffixes)
        if name.endswith(".parsed.meta.json"):
            base = name[: -len(".parsed.meta.json")]
        elif name.endswith(".parsed.txt"):
            base = name[: -len(".parsed.txt")]
        else:
            base = name

        file_id = uuid.uuid4().hex
        file_dir = NEW_DIR / file_id
        file_dir.mkdir(parents=True, exist_ok=True)

        if name == base:
            target = file_dir / "original"
        elif name.endswith(".parsed.txt"):
            target = file_dir / "parsed.txt"
        elif name.endswith(".parsed.meta.json"):
            target = file_dir / "parsed.meta.json"
        else:
            target = file_dir / name

        # Preserve original filename
        (file_dir / "filename.txt").write_text(base)
        shutil.move(str(f), str(target))
        print(f"  {name} → files/{file_id}/{target.name}")
        migrated += 1

    print(f"\nMigrated {migrated} files.")
    remaining = list(OLD_DIR.iterdir())
    if not remaining:
        OLD_DIR.rmdir()
        print(f"Removed empty {OLD_DIR}")


if __name__ == "__main__":
    main()
