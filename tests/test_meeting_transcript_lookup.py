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


def test_merge_hit_packs_keeps_first_and_appends_new():
    from src.meeting.transcript_index import merge_hit_packs

    a = _pack("m1", 1, 1, "a")
    dup = _pack("m1", 1, 1, "dup")
    b = _pack("m1", 2, 2, "b")
    out = merge_hit_packs([a], [dup, b])
    assert [p["pack_index"] for p in out] == [1, 2]
    assert out[0]["sentences"][0]["text"] == "a"


def test_select_new_hits_drops_weak_leftovers_against_prior():
    from src.meeting.transcript_index import select_new_hits

    prior = [
        {**_pack("m1", 1, 1, "good"), "_score": 0.82},
        {**_pack("m1", 3, 3, "ok"), "_score": 0.70},
    ]
    weak = [
        {**_pack("m1", 9, 9, "chatter"), "_score": 0.20},
        {**_pack("m1", 10, 10, "noise"), "_score": 0.12},
    ]
    assert select_new_hits(weak, prior_hits=prior) == []
    strong = [{**_pack("m1", 2, 2, "two"), "_score": 0.65}]
    out = select_new_hits(strong, prior_hits=prior)
    assert [p["pack_index"] for p in out] == [2]


def test_select_new_hits_first_call_keeps_full_list():
    from src.meeting.transcript_index import select_new_hits

    hits = [_pack("m1", i, i + 1, f"t{i}") for i in range(10)]
    assert len(select_new_hits(hits, prior_hits=None)) == 10


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
        raw, found, _kept = lookup_json_and_keys(
            "m1", "q", seen_pack_keys={"m1:0", "m1:1"}
        )
    data = json.loads(raw)
    assert found == {"m1:0", "m1:1", "m1:2"}
    assert data["hit_count"] == 1
    assert "[ref:3]" in data["context"]
    assert "already-returned packs were not repeated" in data["message"].lower()


def test_select_diverse_hits_spreads_same_meeting_and_caps_per_meeting():
    from src.meeting.transcript_index import select_diverse_hits

    hits = [_pack("m1", i, i + 1, f"t{i}") for i in range(8)]
    hits.extend(_pack("m2", i, i + 1, f"u{i}") for i in range(8))
    out = select_diverse_hits(hits, top_k=6, max_per_meeting=3, min_index_gap=3)
    assert len(out) == 6
    by_mid: dict[str, list[int]] = {}
    for h in out:
        by_mid.setdefault(h["meeting_id"], []).append(int(h["pack_index"]))
    assert set(by_mid) == {"m1", "m2"}
    assert all(len(v) <= 3 for v in by_mid.values())
    for idxs in by_mid.values():
        ordered = sorted(idxs)
        gaps = [b - a for a, b in zip(ordered, ordered[1:])]
        assert all(g >= 3 for g in gaps)


def test_execute_meetings_lookup_omits_seen_packs_and_returns_keys():
    import json
    from unittest.mock import MagicMock, patch

    from src.meeting.models import Meeting
    from src.meeting.transcript_index import execute_meetings_lookup_json_and_keys

    ready = Meeting(id="m1", title="Ready", transcript_index_status="ready")
    hits = [_pack("m1", 0, 1, "A"), _pack("m1", 1, 2, "B"), _pack("m1", 2, 3, "C")]
    sentences = [
        {"original_text": "A", "speaker": "s1"},
        {"original_text": "B", "speaker": "s1"},
        {"original_text": "C", "speaker": "s1"},
    ]
    with (
        patch("src.meeting.store.get_meeting", return_value=ready),
        patch(
            "src.meeting.transcript_index.load_sentences_for_meeting",
            return_value=sentences,
        ),
        patch("src.services.services") as services,
        patch(
            "src.meeting.transcript_index.search_group_transcript_packs",
            return_value=hits,
        ) as search,
    ):
        services.retriever = MagicMock()
        services.reranker = MagicMock()
        raw, found, _kept = execute_meetings_lookup_json_and_keys(
            ["m1"], "q", seen_pack_keys={"m1:0", "m1:1"}
        )
    data = json.loads(raw)
    assert found == {"m1:2"} or found == {"m1:0", "m1:1", "m1:2"}
    assert data["hit_count"] == 1
    assert "[ref:3]" in data["context"]
    assert "[ref:1]" not in data["context"]
    assert "already-returned packs were not repeated" in (data.get("message") or "").lower()
    kwargs = search.call_args.kwargs
    assert kwargs.get("exclude_pack_keys") == {"m1:0", "m1:1"} or set(
        kwargs.get("exclude_pack_keys") or []
    ) == {"m1:0", "m1:1"}
    assert kwargs.get("skip_rerank") is False


def test_execute_meetings_lookup_all_seen_says_no_additional():
    import json
    from unittest.mock import MagicMock, patch

    from src.meeting.models import Meeting
    from src.meeting.transcript_index import execute_meetings_lookup_json_and_keys

    ready = Meeting(id="m1", title="Ready", transcript_index_status="ready")
    hits = [_pack("m1", 0, 1, "A")]
    with (
        patch("src.meeting.store.get_meeting", return_value=ready),
        patch(
            "src.meeting.transcript_index.load_sentences_for_meeting",
            return_value=[{"original_text": "A", "speaker": "s1"}],
        ),
        patch("src.services.services") as services,
        patch(
            "src.meeting.transcript_index.search_group_transcript_packs",
            return_value=hits,
        ),
    ):
        services.retriever = MagicMock()
        services.reranker = MagicMock()
        raw, found, _kept = execute_meetings_lookup_json_and_keys(
            ["m1"], "q", seen_pack_keys={"m1:0"}
        )
    data = json.loads(raw)
    assert data["hit_count"] == 0
    assert data.get("context") in ("", None)
    assert "no new packs" in (data.get("message") or "").lower()
    assert found == {"m1:0"} or found == set()


def test_execute_meetings_lookup_no_new_keeps_prior_excerpts():
    import json
    from unittest.mock import MagicMock, patch

    from src.meeting.models import Meeting
    from src.meeting.transcript_index import (
        LOOKUP_NO_NEW_PACKS_MSG,
        execute_meetings_lookup_json_and_keys,
    )

    ready = Meeting(id="m1", title="Ready", transcript_index_status="ready")
    prior = [_pack("m1", 0, 1, "keep me")]
    hits = [_pack("m1", 0, 1, "keep me")]
    with (
        patch("src.meeting.store.get_meeting", return_value=ready),
        patch(
            "src.meeting.transcript_index.load_sentences_for_meeting",
            return_value=[{"original_text": "keep me", "speaker": "s1"}],
        ),
        patch("src.services.services") as services,
        patch(
            "src.meeting.transcript_index.search_group_transcript_packs",
            return_value=hits,
        ),
    ):
        services.retriever = MagicMock()
        services.reranker = MagicMock()
        raw, _found, kept = execute_meetings_lookup_json_and_keys(
            ["m1"], "q", seen_pack_keys={"m1:0"}, prior_hits=prior
        )
    data = json.loads(raw)
    assert data["hit_count"] >= 1
    assert "keep me" in (data.get("context") or "")
    assert data.get("message") == LOOKUP_NO_NEW_PACKS_MSG
    assert any(p.get("pack_index") == 0 for p in kept)


def test_lookup_json_no_new_keeps_prior_excerpts():
    import json
    from unittest.mock import MagicMock, patch

    from src.meeting.transcript_index import LOOKUP_NO_NEW_PACKS_MSG, lookup_json_and_keys

    meeting = MagicMock()
    meeting.speaker_names = {}
    prior = [_pack("m1", 0, 1, "keep me")]
    sentences = [{"original_text": "keep me", "speaker": "s1"}]
    with patch("src.meeting.store.get_meeting", return_value=meeting), patch(
        "src.meeting.transcript_index.load_sentences_for_meeting",
        return_value=sentences,
    ), patch("src.services.services") as services, patch(
        "src.meeting.transcript_index.run_transcript_lookup"
    ) as run:
        services.retriever = None
        services.reranker = None
        run.return_value = {
            "hits": prior,
            "filter_applied": {},
            "context": "keep me",
            "preview_unfiltered": "",
        }
        raw, found, kept = lookup_json_and_keys(
            "m1", "q", seen_pack_keys={"m1:0"}, prior_hits=prior
        )
    data = json.loads(raw)
    assert found == {"m1:0"}
    assert "keep me" in data["context"]
    assert data["message"] == LOOKUP_NO_NEW_PACKS_MSG
    assert kept


def test_stitch_group_hits_reports_only_packs_that_fit():
    from src.meeting.transcript_index import (
        stitch_group_hits_and_keys,
        transcript_pack_key,
    )

    hits = [
        {
            "meeting_id": "m_early",
            "pack_index": 0,
            "sentences": [
                {"ref_n": 1, "speaker_id": "s", "text": "early " * 400},
            ],
        },
        {
            "meeting_id": "m_late",
            "pack_index": 7,
            "sentences": [
                {"ref_n": 1, "speaker_id": "s", "text": "late unique price"},
            ],
        },
    ]
    meta = {
        "m_early": {
            "n": 1,
            "title": "Early",
            "date": "2026-01-01",
            "sentences": [{"speaker": "s", "original_text": "early " * 400}],
        },
        "m_late": {
            "n": 2,
            "title": "Late",
            "date": "2026-12-01",
            "sentences": [{"speaker": "s", "original_text": "late unique price"}],
        },
    }
    text, shown = stitch_group_hits_and_keys(hits, meta, glue=0, cap_tokens=80)
    keys = {transcript_pack_key(p) for p in shown}
    assert "m_early:0" in keys
    assert "m_late:7" not in keys
    assert "late unique price" not in text


def test_stitch_keeps_later_high_score_pack_in_same_meeting():
    """Top packs in one meeting all land — not only the earliest by pack_index."""
    from src.meeting.transcript_index import stitch_group_hits_and_keys

    kwh = {
        "meeting_id": "m1",
        "pack_index": 4,
        "_score": 0.50,
        "sentences": [
            {
                "ref_n": 1,
                "speaker_id": "s",
                "text": "kilowatt hour range " * 80,
            },
        ],
    }
    price = {
        "meeting_id": "m1",
        "pack_index": 16,
        "_score": 0.84,
        "sentences": [
            {"ref_n": 40, "speaker_id": "s", "text": "230 plus ringgit offer price"},
        ],
    }
    others = [
        {
            "meeting_id": f"m{i}",
            "pack_index": 0,
            "_score": 0.2,
            "sentences": [
                {"ref_n": 1, "speaker_id": "s", "text": f"other meeting {i} filler"},
            ],
        }
        for i in range(2, 6)
    ]
    meta = {
        "m1": {
            "n": 1,
            "title": "Pricing",
            "date": "2026-08-20",
            "sentences": [
                {"speaker": "s", "original_text": "kilowatt hour range " * 80},
                *[{"speaker": "s", "original_text": f"gap {i}"} for i in range(2, 40)],
                {"speaker": "s", "original_text": "230 plus ringgit offer price"},
            ],
        },
    }
    for i in range(2, 6):
        meta[f"m{i}"] = {
            "n": i,
            "title": f"Other {i}",
            "date": "2026-01-01",
            "sentences": [{"speaker": "s", "original_text": f"other meeting {i} filler"}],
        }
    text, shown = stitch_group_hits_and_keys([kwh, price, *others], meta, glue=0)
    assert "230 plus ringgit offer price" in text
    assert {p["pack_index"] for p in shown if p["meeting_id"] == "m1"} == {4, 16}


def test_lookup_seen_keys_exclude_unshown_meeting_packs():
    import json
    from datetime import datetime, timezone
    from unittest.mock import MagicMock, patch

    from src.meeting.models import Meeting
    from src.meeting.transcript_index import execute_meetings_lookup_json_and_keys

    early = Meeting(
        id="m_early",
        title="Early",
        transcript_index_status="ready",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    late = Meeting(
        id="m_late",
        title="Late",
        transcript_index_status="ready",
        created_at=datetime(2026, 12, 1, tzinfo=timezone.utc),
    )

    def get_meeting(mid):
        return {"m_early": early, "m_late": late}.get(mid)

    hits = [
        _pack("m_early", 0, 1, "early " * 400),
        _pack("m_late", 7, 1, "late unique price"),
    ]

    def load(mid):
        if mid == "m_early":
            return [{"original_text": "early " * 400, "speaker": "s1"}]
        return [{"original_text": "late unique price", "speaker": "s1"}]

    with (
        patch("src.meeting.store.get_meeting", side_effect=get_meeting),
        patch(
            "src.meeting.transcript_index.load_sentences_for_meeting",
            side_effect=load,
        ),
        patch("src.services.services") as services,
        patch(
            "src.meeting.transcript_index.search_group_transcript_packs",
            return_value=hits,
        ) as search,
    ):
        services.retriever = MagicMock()
        services.reranker = MagicMock()
        raw, found, kept = execute_meetings_lookup_json_and_keys(
            ["m_early", "m_late"], "q", cap_tokens=80
        )
        mixed = execute_meetings_lookup_json_and_keys(
            ["m_early", "m_late"],
            "q",
            seen_pack_keys={"m_early:0"},
        )
    data = json.loads(raw)
    assert "m_early:0" in found
    assert "late unique price" not in (data.get("context") or "")
    assert any(p.get("meeting_id") == "m_late" for p in kept)
    assert mixed[1]
    assert search.call_args_list[-1].kwargs.get("skip_rerank") is False


def test_lookup_skips_rerank_only_when_all_meetings_already_shown():
    from datetime import datetime, timezone
    from unittest.mock import MagicMock, patch

    from src.meeting.models import Meeting
    from src.meeting.transcript_index import execute_meetings_lookup_json_and_keys

    m1 = Meeting(
        id="m1",
        title="One",
        transcript_index_status="ready",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    with (
        patch("src.meeting.store.get_meeting", return_value=m1),
        patch(
            "src.meeting.transcript_index.load_sentences_for_meeting",
            return_value=[{"original_text": "A", "speaker": "s1"}],
        ),
        patch("src.services.services") as services,
        patch(
            "src.meeting.transcript_index.search_group_transcript_packs",
            return_value=[_pack("m1", 2, 1, "C")],
        ) as search,
    ):
        services.retriever = MagicMock()
        services.reranker = MagicMock()
        execute_meetings_lookup_json_and_keys(
            ["m1"], "q", seen_pack_keys={"m1:0"}
        )
    assert search.call_args.kwargs.get("skip_rerank") is False


def test_second_lookup_restitch_merges_by_meeting_and_pack_order():
    import json
    from datetime import datetime, timezone
    from unittest.mock import MagicMock, patch

    from src.meeting.models import Meeting
    from src.meeting.transcript_index import execute_meetings_lookup_json_and_keys

    m1 = Meeting(
        id="m1",
        title="Meeting One",
        transcript_index_status="ready",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    m2 = Meeting(
        id="m2",
        title="Meeting Two",
        transcript_index_status="ready",
        created_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
    )

    def get_meeting(mid):
        return {"m1": m1, "m2": m2}.get(mid)

    prior = [
        _pack("m1", 1, 1, "one"),
        _pack("m1", 3, 3, "three"),
        _pack("m1", 4, 4, "four"),
        _pack("m2", 4, 4, "two-four"),
        _pack("m2", 5, 5, "two-five"),
    ]
    newly = [
        _pack("m1", 2, 2, "two"),
        _pack("m2", 8, 8, "two-eight"),
    ]

    def load(mid):
        if mid == "m1":
            texts = {1: "one", 2: "two", 3: "three", 4: "four"}
        else:
            texts = {4: "two-four", 5: "two-five", 8: "two-eight"}
        rows = []
        for i in range(1, 10):
            rows.append(
                {"original_text": texts.get(i, f"s{i}"), "speaker": "s1"}
            )
        return rows

    with (
        patch("src.meeting.store.get_meeting", side_effect=get_meeting),
        patch(
            "src.meeting.transcript_index.load_sentences_for_meeting",
            side_effect=load,
        ),
        patch("src.services.services") as services,
        patch(
            "src.meeting.transcript_index.search_group_transcript_packs",
            return_value=newly,
        ) as search,
    ):
        services.retriever = MagicMock()
        services.reranker = MagicMock()
        raw, found, kept = execute_meetings_lookup_json_and_keys(
            ["m1", "m2"],
            "q",
            prior_hits=prior,
        )
    data = json.loads(raw)
    ctx = data["context"]
    assert ctx.index("## Meeting: Meeting One") < ctx.index("## Meeting: Meeting Two")
    one = ctx.split("## Meeting: Meeting Two")[0]
    two = ctx.split("## Meeting: Meeting Two")[1]
    assert [one.index(f"[ref:{n}]") for n in (1, 2, 3, 4)] == sorted(
        one.index(f"[ref:{n}]") for n in (1, 2, 3, 4)
    )
    assert [two.index(f"[ref:{n}]") for n in (4, 5, 8)] == sorted(
        two.index(f"[ref:{n}]") for n in (4, 5, 8)
    )
    assert "merged" in (data.get("message") or "").lower()
    assert found == {
        "m1:1",
        "m1:2",
        "m1:3",
        "m1:4",
        "m2:4",
        "m2:5",
        "m2:8",
    }
    assert {f"{p['meeting_id']}:{p['pack_index']}" for p in kept} == found
    excluded = set(search.call_args.kwargs.get("exclude_pack_keys") or [])
    assert {"m1:1", "m1:3", "m1:4", "m2:4", "m2:5"} <= excluded


def test_retry_does_not_merge_low_score_topk_padding():
    import json
    from datetime import datetime, timezone
    from unittest.mock import MagicMock, patch

    from src.meeting.models import Meeting
    from src.meeting.transcript_index import execute_meetings_lookup_json_and_keys

    m1 = Meeting(
        id="m1",
        title="Ready",
        transcript_index_status="ready",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    prior = [{**_pack("m1", 1, 1, "price range"), "_score": 0.8}]
    junk = [{**_pack("m1", 9, 9, "okay yeah"), "_score": 0.15}]
    with (
        patch("src.meeting.store.get_meeting", return_value=m1),
        patch(
            "src.meeting.transcript_index.load_sentences_for_meeting",
            return_value=[{"original_text": "price range", "speaker": "s1"}],
        ),
        patch("src.services.services") as services,
        patch(
            "src.meeting.transcript_index.search_group_transcript_packs",
            return_value=junk,
        ),
    ):
        services.retriever = MagicMock()
        services.reranker = MagicMock()
        raw, found, kept = execute_meetings_lookup_json_and_keys(
            ["m1"], "q", prior_hits=prior
        )
    data = json.loads(raw)
    assert "okay yeah" not in (data.get("context") or "")
    assert "no new packs" in (data.get("message") or "").lower()
    assert found == {"m1:1"}
    assert [f"{p['meeting_id']}:{p['pack_index']}" for p in kept] == ["m1:1"]
