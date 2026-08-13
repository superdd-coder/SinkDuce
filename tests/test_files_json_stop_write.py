"""Step 1 of files.json retirement: no new index writes from production.

Reads, lazy JSON→SQLite import, and remove_* cleanup may remain.
file_index.add / save / update_source_label stay implemented for tests
and old-library tools; business paths must not call them.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"

_WRITE_CALL = re.compile(
    r"\b(?:add_file_index|save_file_index|update_source_label)\s*\("
)


def test_production_does_not_write_files_json_entries():
    hits: list[str] = []
    for path in SRC.rglob("*.py"):
        if path.name == "file_index.py":
            continue
        text = path.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            stripped = line.lstrip()
            if stripped.startswith("#") or stripped.startswith(("def ", "async def ")):
                continue
            if _WRITE_CALL.search(line):
                hits.append(f"{path.relative_to(REPO)}:{i}:{stripped}")
    assert hits == [], "production still writes files.json:\n" + "\n".join(hits)
