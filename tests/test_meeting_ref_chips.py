"""Meeting sentence-ref chips: expand [N-M] ranges instead of splitting on '-'."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "frontend" / "src" / "lib" / "meeting-ref-chips.ts"
QC = ROOT / "frontend" / "src" / "components" / "meeting" / "meeting-quick-chat.tsx"

_NODE = r"""
import { parseMeetingRefGroups } from './frontend/src/lib/meeting-ref-chips.ts'
const raw = process.argv[1] ?? ''
process.stdout.write(JSON.stringify(parseMeetingRefGroups(raw)))
"""


def _parse(raw: str) -> list[dict]:
    proc = subprocess.run(
        [
            "node",
            "--experimental-strip-types",
            "--input-type=module",
            "-e",
            _NODE,
            raw,
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    return json.loads(proc.stdout)


def test_range_becomes_one_chip_not_head_and_tail():
    """The QC bug: [1-5] was split on '-' into buttons 1 and 5."""
    groups = _parse("1-5")
    assert len(groups) == 1
    assert groups[0]["label"] == "1-5"
    assert groups[0]["ids"] == [
        "stt_0001",
        "stt_0002",
        "stt_0003",
        "stt_0004",
        "stt_0005",
    ]


def test_comma_list_of_consecutive_ids_still_collapses():
    groups = _parse("1,2,3,4,5")
    assert len(groups) == 1
    assert groups[0]["label"] == "1-5"


def test_mixed_list_keeps_gap_and_expands_range():
    groups = _parse("47, 78-86")
    assert [g["label"] for g in groups] == ["47", "78-86"]
    assert groups[1]["ids"][0] == "stt_0078"
    assert groups[1]["ids"][-1] == "stt_0086"
    assert len(groups[1]["ids"]) == 9


def test_nonconsecutive_commas_stay_separate():
    groups = _parse("67,70")
    assert [g["label"] for g in groups] == ["67", "70"]


def test_stt_prefixed_range_and_list():
    assert [g["label"] for g in _parse("stt_0001-stt_0005")] == ["1-5"]
    assert [g["label"] for g in _parse("stt_0067-0070")] == ["67-70"]
    assert [g["label"] for g in _parse("stt_0001,stt_0002,stt_0003")] == ["1-3"]


def test_wide_range_stays_one_chip():
    """Do not reintroduce head/tail chips when the span exceeds the expand cap."""
    groups = _parse("734-860")
    assert len(groups) == 1
    assert groups[0]["label"] == "734-860"
    assert groups[0]["ids"][0] == "stt_0734"


def test_quick_chat_uses_shared_parser_not_hyphen_split():
    src = QC.read_text(encoding="utf-8")
    assert LIB.is_file()
    assert "parseMeetingRefGroups" in src
    assert "split(/[,–-]/" not in src
