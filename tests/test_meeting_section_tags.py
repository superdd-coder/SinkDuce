"""Parallel section streams must merge T-tags, not last-write-wins."""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture
def meetings_dir(tmp_path):
    d = tmp_path / "meetings"
    with patch("src.meeting.store.MEETINGS_DIR", d):
        yield d


def _seed(meeting_id: str) -> None:
    from src.meeting.store import save_sentences

    save_sentences(
        meeting_id,
        [
            {
                "sentence_id": "m_stt_0001",
                "original_text": "one",
                "section_tags": [],
            },
            {
                "sentence_id": "m_stt_0002",
                "original_text": "two",
                "section_tags": [],
            },
            {
                "sentence_id": "m_stt_0003",
                "original_text": "three",
                "section_tags": ["tab_99"],
            },
        ],
    )


def test_apply_section_tags_merges_across_tabs(meetings_dir):
    from src.meeting.store import apply_section_tags, get_sentences

    mid = "meet-tags"
    _seed(mid)
    apply_section_tags(mid, "tab_01", ["m_stt_0001", "m_stt_0002"])
    apply_section_tags(mid, "tab_02", ["m_stt_0002"])

    by_id = {s["sentence_id"]: s["section_tags"] for s in get_sentences(mid)}
    assert by_id["m_stt_0001"] == ["tab_01"]
    assert by_id["m_stt_0002"] == ["tab_01", "tab_02"]
    assert by_id["m_stt_0003"] == ["tab_99"]


def test_apply_section_tags_replaces_same_tab(meetings_dir):
    from src.meeting.store import apply_section_tags, get_sentences

    mid = "meet-retag"
    _seed(mid)
    apply_section_tags(mid, "tab_01", ["m_stt_0001", "m_stt_0002"])
    apply_section_tags(mid, "tab_01", ["m_stt_0003"])

    by_id = {s["sentence_id"]: s["section_tags"] for s in get_sentences(mid)}
    assert by_id["m_stt_0001"] == []
    assert by_id["m_stt_0002"] == []
    assert "tab_01" in by_id["m_stt_0003"]
    assert "tab_99" in by_id["m_stt_0003"]


def test_section_stream_uses_locked_tag_merge():
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "meeting"
        / "generation.py"
    ).read_text(encoding="utf-8")
    assert "apply_section_tags" in src
    assert "save_sentences(" not in src.split("def generate_section_stream")[1].split(
        "def _persist_section_done"
    )[0]
