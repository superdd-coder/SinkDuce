"""Tests for meeting summary → note display/distill preprocessing."""

from src.notes.service import (
    prepare_meeting_summary_for_note,
    freeze_speakers_in_note_content,
    meeting_source_id,
    parse_meeting_source_id,
)


def test_distill_keeps_spk_strips_refs_and_priority():
    """Distill path: keep [spk:ID], strip stt refs + priority."""
    raw = (
        "### Points\n"
        "- [spk:0] proposed Q3 [stt_0001,stt_0002] [priority: high]\n"
        "- Speaker 1 agreed 【stt_0010】 [priority: medium]\n"
    )
    out = prepare_meeting_summary_for_note(
        raw, {"0": "Alice", "1": "Bob"}, resolve_speakers=False
    )
    assert "[spk:0]" in out
    assert "[spk:1]" in out  # Speaker 1 normalized to [spk:1]
    assert "Alice" not in out
    assert "Bob" not in out
    assert "stt_" not in out
    assert "priority" not in out.lower()
    assert "[," not in out


def test_freeze_resolves_speakers():
    raw = (
        "### Points\n"
        "- [spk:0] proposed Q3 [stt_0001] [priority: high]\n"
        "- Speaker 1 agreed\n"
    )
    out = prepare_meeting_summary_for_note(
        raw, {"0": "Alice", "1": "Bob"}, resolve_speakers=True
    )
    assert "Alice" in out
    assert "Bob" in out
    assert "[spk:" not in out
    assert "stt_" not in out


def test_prepare_no_comma_bracket_leftovers():
    """Bare stt strip must never leave [,,,,,,,,,,,]."""
    raw = (
        "while [spk:4] noted that Bioenergy is different "
        "[stt_0001,stt_0002,stt_0003,stt_0004,stt_0005,stt_0006,"
        "stt_0007,stt_0008,stt_0009,stt_0010,stt_0011]. "
        "[spk:2] confirmed understanding "
        "[stt_0012,stt_0013,stt_0014,stt_0015,stt_0016,stt_0017,stt_0018,stt_0019]."
    )
    out = prepare_meeting_summary_for_note(raw, {"4": "Dana", "2": "Chris"})
    assert "[spk:4]" in out
    assert "[spk:2]" in out
    assert "stt_" not in out
    assert ",,," not in out


def test_prepare_fixes_backslash_and_keeps_spk():
    raw = r"while \Speaker 4\ noted that Bioenergy [,,,,,,,,,,,]. \Speaker 2\ confirmed [,,,,,,,,]."
    out = prepare_meeting_summary_for_note(raw, {"4": "Dana", "2": "Chris"})
    assert "[spk:4]" in out
    assert "[spk:2]" in out
    assert "\\Speaker" not in out
    assert "[," not in out


def test_prepare_unescapes_markdown_brackets_keeps_spk():
    raw = r"\[spk:4\] said hello \[stt_0001,stt_0002\]"
    out = prepare_meeting_summary_for_note(raw, {"4": "Dana"})
    assert "[spk:4]" in out
    assert "Dana" not in out
    assert "stt_" not in out
    assert "\\[" not in out


def test_freeze_speakers_in_note_content_noop_without_spk():
    body = "Hello world with **bold** and a list\n- item"
    assert freeze_speakers_in_note_content(body) == body


def test_meeting_source_id_roundtrip():
    assert meeting_source_id("m1", "tab_01") == "meeting:m1:tab_01"
    assert parse_meeting_source_id("meeting:m1:tab_01") == ("m1", "tab_01")
    assert parse_meeting_source_id("meeting:m1") == ("m1", "tab_general")
    assert parse_meeting_source_id("note-xyz") is None
