"""Group transcript lookup: one retrieve, per-meeting speaker OR, stitch by meeting."""

from __future__ import annotations

from src.meeting.transcript_index import group_search_filter, stitch_group_hits


def _clause_keys(filt):
    must = list(filt.must or [])
    return {getattr(c, "key", None) for c in must}


def test_filter_no_speaker_is_match_any_meeting_ids():
    filt = group_search_filter(
        [{"meeting_id": "a", "speaker_ids": None}, {"meeting_id": "b", "speaker_ids": None}]
    )
    assert filt.should in (None, [])
    must = list(filt.must or [])
    assert len(must) == 1
    assert must[0].key == "meeting_id"
    assert set(must[0].match.any) == {"a", "b"}


def test_filter_speaker_or_uses_local_ids():
    filt = group_search_filter(
        [
            {"meeting_id": "a", "speaker_ids": ["0"]},
            {"meeting_id": "b", "speaker_ids": ["1"]},
        ]
    )
    should = list(filt.should or [])
    assert len(should) == 2
    by_mid = {}
    for clause in should:
        mid = None
        spk = None
        for c in clause.must:
            if c.key == "meeting_id":
                mid = c.match.value
            if c.key == "speakers":
                spk = list(c.match.any)
        by_mid[mid] = spk
    assert by_mid == {"a": ["0"], "b": ["1"]}


def test_filter_mapped_in_a_mention_in_b():
    filt = group_search_filter(
        [
            {"meeting_id": "a", "speaker_ids": ["0"]},
            {"meeting_id": "b", "speaker_ids": None},
        ]
    )
    should = list(filt.should or [])
    assert len(should) == 2
    mention = next(c for c in should if "speakers" not in _clause_keys(c))
    mids = [c.match.value for c in mention.must if c.key == "meeting_id"]
    assert mids == ["b"]


def test_stitch_groups_by_meeting_with_metadata_and_gaps():
    hits = [
        {
            "meeting_id": "m_b",
            "pack_index": 0,
            "sentences": [
                {"ref_n": 5, "speaker_id": "0", "text": "改成 2.80"},
            ],
        },
        {
            "meeting_id": "m_a",
            "pack_index": 1,
            "sentences": [
                {"ref_n": 18, "speaker_id": "0", "text": "下周补数据"},
            ],
        },
        {
            "meeting_id": "m_a",
            "pack_index": 0,
            "sentences": [
                {"ref_n": 12, "speaker_id": "0", "text": "2.30 不够"},
            ],
        },
    ]
    meta = {
        "m_a": {
            "n": 1,
            "title": "报价讨论",
            "date": "2026-08-12",
            "speakers": {"0": "Jetro"},
            "sentences": [
                {"ref_n": i, "speaker": "0", "original_text": f"s{i}"}
                for i in range(1, 20)
            ],
        },
        "m_b": {
            "n": 2,
            "title": "预处理跟进",
            "date": "2026-08-19",
            "speakers": {"0": "Alex"},
            "sentences": [
                {"ref_n": i, "speaker": "0", "original_text": f"t{i}"}
                for i in range(1, 8)
            ],
        },
    }
    # overwrite spoken text for hit refs
    meta["m_a"]["sentences"][11]["original_text"] = "2.30 不够"
    meta["m_a"]["sentences"][17]["original_text"] = "下周补数据"
    meta["m_b"]["sentences"][4]["original_text"] = "改成 2.80"

    text = stitch_group_hits(hits, meta)
    assert text.index("## Meeting: 报价讨论") < text.index("## Meeting: 预处理跟进")
    assert "n: 1" in text
    assert "id: m_a" in text
    assert "omitted" in text.lower() or "..." in text
    assert "[ref:12]" in text
    assert "[ref:18]" in text
    assert "[ref:5]" in text


def test_group_search_calls_retrieve_once():
    from unittest.mock import MagicMock

    from src.meeting.transcript_index import search_group_transcript_packs
    from src.rag.retriever import RetrievedChunk

    chunk = RetrievedChunk(
        text="hello",
        score=0.9,
        metadata={
            "meeting_id": "a",
            "pack_index": 0,
            "sentences": [{"ref_n": 1, "speaker_id": "0", "text": "hello"}],
        },
    )
    retriever = MagicMock()
    retriever.retrieve.return_value = [chunk]
    packs = search_group_transcript_packs(
        "hello",
        meetings=[{"meeting_id": "a", "speaker_ids": None}, {"meeting_id": "b", "speaker_ids": None}],
        retriever=retriever,
    )
    assert packs
    assert retriever.retrieve.call_count == 1
    kwargs = retriever.retrieve.call_args.kwargs
    assert kwargs["filter_condition"].should in (None, [])
    assert kwargs["filter_condition"].must[0].match.any == ["a", "b"] or set(
        kwargs["filter_condition"].must[0].match.any
    ) == {"a", "b"}


def test_group_lookup_rejects_outside_member_and_skips_unindexed():
    import json
    from unittest.mock import MagicMock, patch

    from src.meeting.models import Meeting, MeetingGroup, MeetingGroupMember
    from src.meeting.transcript_index import execute_group_lookup_json

    group = MeetingGroup(
        id="g1",
        title="G",
        members=[
            MeetingGroupMember(meeting_id="m_ready", n=1),
            MeetingGroupMember(meeting_id="m_building", n=2),
        ],
    )
    ready = Meeting(id="m_ready", title="Ready", transcript_index_status="ready")
    building = Meeting(id="m_building", title="WIP", transcript_index_status="building")

    def get_meeting(mid):
        return {"m_ready": ready, "m_building": building}.get(mid)

    retriever = MagicMock()
    retriever.retrieve.return_value = []

    with (
        patch("src.meeting.group_store.get_group", return_value=group),
        patch("src.meeting.store.get_meeting", side_effect=get_meeting),
        patch("src.meeting.transcript_index.load_sentences_for_meeting", return_value=[]),
        patch("src.services.services") as services,
    ):
        services.retriever = retriever
        services.reranker = None
        bad = execute_group_lookup_json(
            "g1", "price", meeting_ids=["not_in_group"]
        )
        assert json.loads(bad).get("error")
        ok = json.loads(
            execute_group_lookup_json("g1", "price", meeting_ids=None)
        )
        assert "WIP" in " ".join(ok.get("unindexed") or [])
        retriever.retrieve.assert_called_once()
        mids = retriever.retrieve.call_args.kwargs["filter_condition"].must[0].match.any
        assert list(mids) == ["m_ready"] or set(mids) == {"m_ready"}


def test_group_cites_every_hit_sentence_in_retrieve_order():
    from src.meeting.transcript_index import group_cites_from_hits

    hits = [
        {
            "meeting_id": "m_b",
            "sentences": [{"ref_n": 6, "sentence_id": "stt_0006", "text": "2.80"}],
        },
        {
            "meeting_id": "m_a",
            "sentences": [{"ref_n": 12, "sentence_id": "stt_0012", "text": "2.30"}],
        },
        {
            "meeting_id": "m_b",
            "sentences": [{"ref_n": 9, "sentence_id": "stt_0009", "text": "later"}],
        },
    ]
    meta = {
        "m_a": {"n": 1, "title": "Alpha", "date": "2026-08-01"},
        "m_b": {"n": 2, "title": "Beta", "date": "2026-08-12"},
    }
    cites = group_cites_from_hits(hits, meta)
    assert cites == [
        {
            "n": 2,
            "meeting_id": "m_b",
            "ref_n": 6,
            "sentence_id": "stt_0006",
            "title": "Beta",
            "date": "2026-08-12",
        },
        {
            "n": 1,
            "meeting_id": "m_a",
            "ref_n": 12,
            "sentence_id": "stt_0012",
            "title": "Alpha",
            "date": "2026-08-01",
        },
        {
            "n": 2,
            "meeting_id": "m_b",
            "ref_n": 9,
            "sentence_id": "stt_0009",
            "title": "Beta",
            "date": "2026-08-12",
        },
    ]


def test_group_lookup_json_includes_cites():
    import json
    from unittest.mock import MagicMock, patch

    from src.meeting.models import Meeting, MeetingGroup, MeetingGroupMember
    from src.meeting.transcript_index import execute_group_lookup_json
    from src.rag.retriever import RetrievedChunk

    group = MeetingGroup(
        id="g1",
        title="G",
        members=[MeetingGroupMember(meeting_id="m_ready", n=1)],
    )
    ready = Meeting(id="m_ready", title="Ready", transcript_index_status="ready")
    chunk = RetrievedChunk(
        text="hello",
        score=0.9,
        metadata={
            "meeting_id": "m_ready",
            "pack_index": 0,
            "sentences": [
                {"ref_n": 3, "sentence_id": "stt_0003", "text": "hello"}
            ],
        },
    )
    retriever = MagicMock()
    retriever.retrieve.return_value = [chunk]
    with (
        patch("src.meeting.group_store.get_group", return_value=group),
        patch("src.meeting.store.get_meeting", return_value=ready),
        patch("src.meeting.transcript_index.load_sentences_for_meeting", return_value=[]),
        patch("src.services.services") as services,
    ):
        services.retriever = retriever
        services.reranker = None
        data = json.loads(execute_group_lookup_json("g1", "hello"))
    assert data["cites"][0]["n"] == 1
    assert data["cites"][0]["sentence_id"] == "stt_0003"
    assert "[n:k]" in data["cite_as"]
