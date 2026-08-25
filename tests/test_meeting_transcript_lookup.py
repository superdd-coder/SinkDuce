"""Speaker filter + empty-hit preview for transcript lookup."""

from __future__ import annotations


def test_speaker_ids_from_question_matches_display_names():
    from src.meeting.transcript_index import speaker_ids_from_question

    mapping = {"speaker1": "Alice", "speaker2": "Bob", "speaker3": "Bobby"}
    assert speaker_ids_from_question("Bob 说 Q3 怎么样", mapping) == ["speaker2"]
    assert set(speaker_ids_from_question("Alice 和 Bob 怎么看", mapping)) == {
        "speaker1",
        "speaker2",
    }
    # Longer name wins over prefix of another name
    assert speaker_ids_from_question("Bobby 说了什么", mapping) == ["speaker3"]
    assert speaker_ids_from_question("Q3 业绩", mapping) == []


def test_lookup_empty_filtered_hits_adds_unfiltered_preview():
    from src.meeting.transcript_index import run_transcript_lookup

    preview_pack = {
        "pack_index": 2,
        "sentences": [
            {
                "ref_n": 9,
                "speaker_id": "speaker3",
                "text": "Q3 很好",
                "sentence_id": "stt_0009",
            }
        ],
        "speakers": ["speaker3"],
        "meeting_id": "m1",
    }
    calls: list[tuple] = []

    def search_fn(query, *, speaker_ids, top_k, skip_rerank=False):
        calls.append((query, tuple(speaker_ids or []), top_k, skip_rerank))
        if speaker_ids:
            return []
        return [preview_pack]

    sentences = [
        {"sentence_id": f"stt_{i:04d}", "speaker": "speaker1", "original_text": f"x{i}"}
        for i in range(1, 10)
    ]
    sentences[8] = {
        "sentence_id": "stt_0009",
        "speaker": "speaker3",
        "original_text": "Q3 很好",
    }
    result = run_transcript_lookup(
        meeting_id="m1",
        query="Q3 业绩",
        speaker_ids=["speaker2"],
        speaker_scope="auto",
        sentences=sentences,
        search_fn=search_fn,
    )
    assert result["hits"] == []
    assert result["filter_applied"]["speaker_ids"] == ["speaker2"]
    assert result["preview_unfiltered"]
    assert "[ref:9]" in result["preview_unfiltered"]
    assert calls[0][1] == ("speaker2",)
    assert calls[0][3] is False
    assert calls[1][1] == ()
    assert calls[1][2] == 3
    assert calls[1][3] is True


def test_lookup_with_hits_does_not_preview():
    from src.meeting.transcript_index import run_transcript_lookup

    pack = {
        "pack_index": 0,
        "sentences": [
            {"ref_n": 1, "speaker_id": "speaker2", "text": "Q3 延期", "sentence_id": "stt_0001"}
        ],
        "speakers": ["speaker2"],
        "meeting_id": "m1",
    }

    def search_fn(query, *, speaker_ids, top_k, skip_rerank=False):
        return [pack]

    sentences = [
        {"sentence_id": "stt_0001", "speaker": "speaker2", "original_text": "Q3 延期"}
    ]
    result = run_transcript_lookup(
        meeting_id="m1",
        query="Q3",
        speaker_ids=["speaker2"],
        sentences=sentences,
        search_fn=search_fn,
    )
    assert result["hits"]
    assert "[ref:1]" in result["context"]
    assert not result.get("preview_unfiltered")


def test_speaker_scope_all_skips_filter():
    from src.meeting.transcript_index import run_transcript_lookup

    seen = []

    def search_fn(query, *, speaker_ids, top_k, skip_rerank=False):
        seen.append(speaker_ids)
        return []

    run_transcript_lookup(
        meeting_id="m1",
        query="Q3",
        speaker_ids=["speaker2"],
        speaker_scope="all",
        sentences=[],
        search_fn=search_fn,
    )
    assert seen == [None]


def test_load_sentences_falls_back_to_transcript_segments():
    from unittest.mock import MagicMock, patch

    from src.meeting.transcript_index import load_sentences_for_meeting

    seg = MagicMock()
    seg.speaker_id = "s1"
    seg.text = "hello"
    seg.start = 0
    seg.end = 1
    transcript = MagicMock()
    transcript.segments = [seg]
    with patch("src.meeting.store.get_sentences", return_value=None), patch(
        "src.meeting.store.get_transcript", return_value=transcript
    ):
        rows = load_sentences_for_meeting("m1")
    assert len(rows) == 1
    assert rows[0]["original_text"] == "hello"
    assert rows[0]["speaker"] == "s1"


def test_hit_count_from_lookup_json():
    from src.meeting.transcript_index import hit_count_from_lookup_json

    assert hit_count_from_lookup_json('{"hit_count": 4, "context": "x"}') == 4
    assert hit_count_from_lookup_json('{"hit_count": 0, "message": "none"}') == 0
    assert hit_count_from_lookup_json("not-json") == 0


def _pack(mid: str, idx: int, ref_n: int, text: str) -> dict:
    return {
        "meeting_id": mid,
        "pack_index": idx,
        "sentences": [
            {"ref_n": ref_n, "speaker_id": "s1", "text": text, "sentence_id": f"stt_{ref_n:04d}"}
        ],
    }


def test_transcript_pack_key():
    from src.meeting.transcript_index import transcript_pack_key

    assert transcript_pack_key(_pack("m1", 2, 3, "x")) == "m1:2"


def test_apply_turn_pack_dedupe_first_call_keeps_all():
    from src.meeting.transcript_index import apply_turn_pack_dedupe

    hits = [_pack("m", 0, 1, "A"), _pack("m", 1, 2, "B")]
    result = {"hits": hits, "context": "ALL", "preview_unfiltered": ""}
    out, found = apply_turn_pack_dedupe(result, [], set())
    assert out["context"] == "ALL"
    assert found == {"m:0", "m:1"}
    assert [p["pack_index"] for p in out["hits"]] == [0, 1]


def test_apply_turn_pack_dedupe_keeps_only_unseen_packs():
    from src.meeting.transcript_index import apply_turn_pack_dedupe

    sentences = [
        {"original_text": "A", "speaker": "s1"},
        {"original_text": "B", "speaker": "s1"},
        {"original_text": "C", "speaker": "s1"},
    ]
    hits = [_pack("m", 0, 1, "A"), _pack("m", 1, 2, "B"), _pack("m", 2, 3, "C")]
    result = {"hits": hits, "context": "ALL", "preview_unfiltered": ""}
    out, found = apply_turn_pack_dedupe(result, sentences, {"m:0", "m:1"}, glue=0)
    assert found == {"m:0", "m:1", "m:2"}
    assert [p["pack_index"] for p in out["hits"]] == [2]
    assert "[ref:3]" in out["context"]
    assert "[ref:1]" not in out["context"]


def test_apply_turn_pack_dedupe_all_seen_clears_context():
    from src.meeting.transcript_index import apply_turn_pack_dedupe

    hits = [_pack("m", 0, 1, "A")]
    result = {"hits": hits, "context": "A", "preview_unfiltered": ""}
    out, found = apply_turn_pack_dedupe(result, [], {"m:0"})
    assert found == {"m:0"}
    assert out["hits"] == []
    assert out["context"] == ""


def test_execute_lookup_json_has_no_pagination_note():
    import json
    from unittest.mock import MagicMock, patch

    from src.meeting.transcript_index import execute_lookup_json

    meeting = MagicMock()
    meeting.speaker_names = {}
    with patch("src.meeting.store.get_meeting", return_value=meeting), patch(
        "src.meeting.transcript_index.load_sentences_for_meeting", return_value=[]
    ), patch("src.services.services") as services, patch(
        "src.meeting.transcript_index.run_transcript_lookup"
    ) as run:
        services.retriever = None
        services.reranker = None
        run.return_value = {
            "hits": [{"sentences": [{"ref_n": 1}]}],
            "filter_applied": {},
            "context": "[ref:1] s1: hi",
            "preview_unfiltered": "",
        }
        data = json.loads(execute_lookup_json("m1", "next steps"))
    assert "note" not in data
    blob = json.dumps(data).lower()
    assert "next page" not in blob
    assert "later parts" not in blob


def test_execute_lookup_json_omits_seen_packs_and_returns_keys():
    import json
    from unittest.mock import MagicMock, patch

    from src.meeting.transcript_index import lookup_json_and_keys

    meeting = MagicMock()
    meeting.speaker_names = {}
    hits = [
        _pack("m1", 0, 1, "A"),
        _pack("m1", 1, 2, "B"),
        _pack("m1", 2, 3, "C"),
    ]
    sentences = [
        {"original_text": "A", "speaker": "s1"},
        {"original_text": "B", "speaker": "s1"},
        {"original_text": "C", "speaker": "s1"},
    ]
    with patch("src.meeting.store.get_meeting", return_value=meeting), patch(
        "src.meeting.transcript_index.load_sentences_for_meeting",
        return_value=sentences,
    ), patch("src.services.services") as services, patch(
        "src.meeting.transcript_index.run_transcript_lookup"
    ) as run:
        services.retriever = None
        services.reranker = None
        run.return_value = {
            "hits": hits,
            "filter_applied": {},
            "context": "ALL",
            "preview_unfiltered": "",
        }
        raw, found = lookup_json_and_keys(
            "m1", "q", seen_pack_keys={"m1:0", "m1:1"}
        )
    data = json.loads(raw)
    assert found == {"m1:0", "m1:1", "m1:2"}
    assert data["hit_count"] == 1
    assert "[ref:3]" in data["context"]
    assert "additional" in data["message"].lower()
