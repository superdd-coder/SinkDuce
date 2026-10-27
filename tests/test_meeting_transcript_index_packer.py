"""Sentence packing for the meeting transcript Qdrant index."""

from __future__ import annotations


def _sent(n: int, text: str, speaker: str = "speaker1", t0: float = 0.0, t1: float = 1.0) -> dict:
    return {
        "sentence_id": f"stt_{n:04d}",
        "speaker": speaker,
        "start_time": t0,
        "end_time": t1,
        "original_text": text,
    }


def test_empty_sentences_yield_no_packs():
    from src.meeting.transcript_index import pack_sentences

    assert pack_sentences([], meeting_id="m1") == []


def test_short_utterances_fit_one_pack_with_global_ref_n():
    from src.meeting.transcript_index import pack_sentences

    sentences = [
        _sent(1, "预算还没批", t0=0, t1=1),
        _sent(2, "我想Q3发布", speaker="speaker2", t0=1, t1=2),
    ]
    packs = pack_sentences(sentences, meeting_id="m1")
    assert len(packs) == 1
    p = packs[0]
    assert p["meeting_id"] == "m1"
    assert p["pack_index"] == 0
    assert p["speakers"] == ["speaker1", "speaker2"]
    assert p["sentence_ids"] == ["stt_0001", "stt_0002"]
    assert p["start_time"] == 0
    assert p["end_time"] == 2
    assert [s["ref_n"] for s in p["sentences"]] == [1, 2]
    assert p["sentences"][1]["speaker_id"] == "speaker2"
    assert p["sentences"][1]["text"] == "我想Q3发布"


def test_packs_split_near_max_tokens_without_cutting_a_sentence():
    from src.meeting.transcript_index import pack_sentences

    # CJK ≈ 1 token/char. max=10, buffer 0.5 → hard 15.
    a = _sent(1, "一二三四五六七八")  # 8
    b = _sent(2, "九十一二三")  # 5, 8+5=13 <= 15, would exceed 10 so new pack? 
    # Design: fill until ~max, allow overflow to hard so a sentence is never split.
    # After a (8 < 10), adding b (5) → 13 <= 15, keep in same pack if we allow overflow
    # when current is non-empty and adding would exceed max but not hard.
    c = _sent(3, "甲乙丙丁戊己庚")  # 7; 13+7=20 > 15 → new pack
    packs = pack_sentences([a, b, c], meeting_id="m1", max_tokens=10, buffer_ratio=0.5)
    assert len(packs) == 2
    assert [s["text"] for s in packs[0]["sentences"]] == ["一二三四五六七八", "九十一二三"]
    assert [s["text"] for s in packs[1]["sentences"]] == ["甲乙丙丁戊己庚"]
    assert packs[1]["pack_index"] == 1
    assert packs[1]["sentences"][0]["ref_n"] == 3


def test_filler_attaches_to_previous_sentence_same_pack():
    from src.meeting.transcript_index import pack_sentences

    packs = pack_sentences(
        [_sent(1, "那我们延期"), _sent(2, "嗯")],
        meeting_id="m1",
    )
    assert len(packs) == 1
    assert [s["text"] for s in packs[0]["sentences"]] == ["那我们延期", "嗯"]


def test_lone_filler_still_makes_a_pack():
    from src.meeting.transcript_index import pack_sentences

    packs = pack_sentences([_sent(1, "嗯")], meeting_id="m1")
    assert len(packs) == 1
    assert packs[0]["sentences"][0]["text"] == "嗯"
