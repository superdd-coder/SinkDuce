"""Stitch retrieved packs into Quick Chat [ref:N] windows."""

from __future__ import annotations


def _sent(n: int, text: str, speaker: str = "s1") -> dict:
    return {
        "sentence_id": f"stt_{n:04d}",
        "speaker": speaker,
        "original_text": text,
        "start_time": float(n),
        "end_time": float(n) + 0.5,
    }


def test_format_segments_uses_global_ref_n_when_present():
    from src.chatbox.meeting_context import format_segments_for_chat

    text = format_segments_for_chat(
        [
            {"ref_n": 12, "speaker_id": "speaker1", "text": "预算还没批"},
            {"ref_n": 13, "speaker": "speaker2", "original_text": "那我们延期"},
        ]
    )
    assert text == (
        "[ref:12] speaker1: 预算还没批\n"
        "[ref:13] speaker2: 那我们延期"
    )


def test_adjacent_packs_merge_without_ellipsis():
    from src.meeting.transcript_index import pack_sentences, stitch_packs

    sentences = [_sent(i, f"句{i}") for i in range(1, 7)]
    packs = pack_sentences(sentences, meeting_id="m", max_tokens=8, buffer_ratio=0.0)
    # Two consecutive packs as hits
    assert len(packs) >= 2
    out = stitch_packs(sentences, [packs[0], packs[1]], glue=0)
    assert "..." not in out
    assert "[ref:1]" in out


def test_gapped_packs_join_with_ellipsis():
    from src.meeting.transcript_index import pack_sentences, stitch_packs

    sentences = [_sent(i, f"句{i}一二三四五") for i in range(1, 9)]
    packs = pack_sentences(sentences, meeting_id="m", max_tokens=6, buffer_ratio=0.0)
    assert len(packs) >= 3
    out = stitch_packs(sentences, [packs[0], packs[-1]], glue=0)
    assert "\n...\n" in out
    assert out.index("[ref:1]") < out.index("...")


def test_glue_adds_neighbor_sentences_from_full_list():
    from src.meeting.transcript_index import pack_sentences, stitch_packs

    sentences = [_sent(i, f"句{i}") for i in range(1, 8)]
    packs = pack_sentences(sentences, meeting_id="m", max_tokens=4, buffer_ratio=0.0)
    # Hit a middle pack if possible
    mid = packs[len(packs) // 2]
    first_ref = mid["sentences"][0]["ref_n"]
    last_ref = mid["sentences"][-1]["ref_n"]
    out = stitch_packs(sentences, [mid], glue=2)
    # ±2 around the island
    lo = max(1, first_ref - 2)
    hi = min(7, last_ref + 2)
    assert f"[ref:{lo}]" in out
    assert f"[ref:{hi}]" in out


def test_stitch_uses_hit_pack_sentences_when_store_list_empty():
    """Retrieved packs already carry spoken lines — do not require sentences.json."""
    from src.meeting.transcript_index import stitch_packs

    pack = {
        "pack_index": 0,
        "sentences": [
            {
                "ref_n": 4,
                "speaker_id": "s1",
                "text": "Q3 延期了",
                "sentence_id": "stt_0004",
            }
        ],
    }
    out = stitch_packs([], [pack], glue=2)
    assert "[ref:4]" in out
    assert "Q3 延期了" in out
