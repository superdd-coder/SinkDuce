"""Embed-input text for meeting transcript packs (body ≠ payload)."""

from __future__ import annotations


def test_sanitize_locator_strips_refs_and_speaker_tags():
    from src.meeting.transcript_index import sanitize_locator

    raw = "Q3 delay [ref:12] after budget [spk:0] talk"
    out = sanitize_locator(raw)
    assert "[ref:" not in out
    assert "[spk:" not in out
    assert "Q3 delay" in out
    assert "budget" in out


def test_sanitize_locator_keeps_a_short_english_phrase_intact():
    from src.meeting.transcript_index import sanitize_locator

    raw = "Closing remarks and request for pricing to both parties"
    out = sanitize_locator(raw)
    assert "both parties" in out
    assert not out.endswith(" to")
    assert not out.endswith(" and")


def test_sanitize_locator_soft_cuts_at_phrase_boundary():
    from src.meeting.transcript_index import sanitize_locator

    raw = (
        "Preliminary costing shows RM2.30 insufficient, "
        "need two pricing scenarios including emergency budget "
        + (" extra" * 40)
    )
    out = sanitize_locator(raw)
    assert "insufficient" in out
    assert not out.endswith(" extra")
    assert not out.rstrip(",").endswith((" to", " and", " for", " with", " of"))


def test_embed_text_is_title_context_then_spoken_lines_only():
    from src.meeting.transcript_index import embed_text_for_pack

    pack = {
        "sentences": [
            {"text": "预算还没批", "speaker_id": "speaker1", "ref_n": 1},
            {"text": "那我们延期", "speaker_id": "speaker2", "ref_n": 2},
        ]
    }
    text = embed_text_for_pack(
        pack, title="周会", locator="Q3 delay [spk:0] [ref:3]"
    )
    assert text.startswith("周会")
    assert "Context: Q3 delay" in text
    assert "[spk:" not in text
    assert "[ref:" not in text
    assert "speaker1" not in text
    assert "stt_" not in text
    assert "预算还没批" in text
    assert "那我们延期" in text


def test_embed_text_omits_empty_locator_and_title():
    from src.meeting.transcript_index import embed_text_for_pack

    pack = {"sentences": [{"text": "hello"}]}
    text = embed_text_for_pack(pack)
    assert text == "hello"
    assert "Context:" not in text
