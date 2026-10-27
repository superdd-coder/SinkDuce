"""Locator prompt shares General Summary transcript prefix for cache."""

from __future__ import annotations


def test_locator_prompt_shares_transcript_block_not_hot_words_or_notes():
    from src.prompts import MEETING_GENERAL_SUMMARY_PROMPT, MEETING_TRANSCRIPT_LOCATOR_PROMPT

    tx = "[1] [spk:0] 预算还没批\n[2] [spk:1] 那我们延期"
    summary = MEETING_GENERAL_SUMMARY_PROMPT.format(
        transcript=tx, notes="note", hot_words="term"
    )
    locator = MEETING_TRANSCRIPT_LOCATOR_PROMPT.format(
        transcript=tx, packs="[0]\n预算还没批"
    )
    prefix = f"<transcript>\n{tx}\n</transcript>\n"
    assert summary.startswith(prefix)
    assert locator.startswith(prefix)
    assert "<hot-words>" not in locator
    assert "<user-meeting-note>" not in locator
    assert "<task>" in locator


def test_apply_locator_batch_writes_sanitized_context():
    from src.meeting.transcript_index import apply_locator_batch

    packs = [{"sentences": [{"text": "a"}]}, {"sentences": [{"text": "b"}]}]
    apply_locator_batch(
        packs,
        {0: "Q3 delay [spk:0]", 1: "hiring [ref:3]"},
        offset=0,
    )
    assert "[spk:" not in packs[0]["context"]
    assert "Q3 delay" in packs[0]["context"]
    assert "[ref:" not in packs[1]["context"]
