"""System Qdrant collection for meeting transcripts."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from qdrant_client.models import FieldCondition, MatchValue


def test_collection_name_is_internal():
    from src.meeting.transcript_index import (
        INTERNAL_QDRANT_COLLECTIONS,
        TRANSCRIPT_COLLECTION,
    )

    assert TRANSCRIPT_COLLECTION.startswith("__")
    assert "meeting" in TRANSCRIPT_COLLECTION
    assert TRANSCRIPT_COLLECTION in INTERNAL_QDRANT_COLLECTIONS
    assert "__summaries__" in INTERNAL_QDRANT_COLLECTIONS


def test_search_filter_meeting_only():
    from src.meeting.transcript_index import meeting_search_filter

    f = meeting_search_filter("mid_1")
    assert any(
        isinstance(c, FieldCondition) and c.key == "meeting_id"
        and isinstance(c.match, MatchValue) and c.match.value == "mid_1"
        for c in (f.must or [])
    )


def test_search_filter_speakers_contains():
    from src.meeting.transcript_index import meeting_search_filter
    from qdrant_client.models import MatchAny

    f = meeting_search_filter("mid_1", speaker_ids=["speaker2"])
    keys = []
    for c in f.must or []:
        if isinstance(c, FieldCondition):
            keys.append(c.key)
            if c.key == "speakers":
                assert isinstance(c.match, MatchAny)
                assert list(c.match.any) == ["speaker2"]
    assert "meeting_id" in keys
    assert "speakers" in keys


def test_ensure_collection_creates_when_missing():
    from src.meeting.transcript_index import TRANSCRIPT_COLLECTION, ensure_transcript_collection

    db = MagicMock()
    db.collection_exists.return_value = False
    ensure_transcript_collection(db, vector_size=8)
    db.create_collection.assert_called_once()
    args, kwargs = db.create_collection.call_args
    name = kwargs.get("name") or (args[0] if args else None)
    size = kwargs.get("vector_size")
    if size is None and len(args) > 1:
        size = args[1]
    assert name == TRANSCRIPT_COLLECTION
    assert size == 8


def test_ensure_collection_skips_when_present():
    from src.meeting.transcript_index import ensure_transcript_collection

    db = MagicMock()
    db.collection_exists.return_value = True
    ensure_transcript_collection(db, vector_size=8)
    db.create_collection.assert_not_called()


def test_ensure_collection_adds_payload_indexes_if_already_exists():
    from src.meeting.transcript_index import ensure_transcript_collection

    db = MagicMock()
    db.collection_exists.return_value = True
    db.client = MagicMock()
    ensure_transcript_collection(db, vector_size=8)
    db.create_collection.assert_not_called()
    names = [
        c.kwargs.get("field_name")
        for c in db.client.create_payload_index.call_args_list
    ]
    assert "meeting_id" in names
    assert "speakers" in names
