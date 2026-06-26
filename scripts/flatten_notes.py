#!/usr/bin/env python3
"""Flatten data/notes/{collection}/{note_id}/ → data/notes/{note_id}/."""
import json
import shutil
from pathlib import Path

NOTES_DIR = Path(__file__).resolve().parent.parent / "data" / "notes"

def main():
    if not NOTES_DIR.exists():
        print("No notes directory"); return

    moved = 0
    for col_dir in sorted(NOTES_DIR.iterdir()):
        if not col_dir.is_dir(): continue
        for note_dir in sorted(col_dir.iterdir()):
            if not note_dir.is_dir(): continue
            note_id = note_dir.name
            target = NOTES_DIR / note_id
            if target.exists():
                print(f"  SKIP {note_id}: already at root")
                continue
            shutil.move(str(note_dir), str(target))
            moved += 1
            print(f"  {col_dir.name}/{note_id} → {note_id}")

        # Remove empty collection dir
        remaining = list(col_dir.iterdir())
        if not remaining:
            col_dir.rmdir()

    print(f"\nMoved {moved} notes.")

if __name__ == "__main__":
    main()
