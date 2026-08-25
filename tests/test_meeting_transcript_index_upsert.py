"""Upsert packed transcript points into the system collection."""

from __future__ import annotations

from unittest.mock import MagicMock


def test_index_meeting_transcripts_upserts_points_and_deletes_old():
    from src.meeting.transcript_index import (
        TRANSCRIPT_COLLECTION,
        index_meeting_transcripts,
    )

    db = MagicMock()
    db.collection_exists.return_value = True
    embedding = MagicMock()
    embedding.dimensions = 4
    embedding.embed_texts.return_value = [[0.1, 0.2, 0.3, 0.4]]

    sentences = [
        {
            "sentence_id": "stt_0001",
            "speaker": "s1",
            "original_text": "hello world",
            "start_time": 0,
            "end_time": 1,
        }
    ]
    n = index_meeting_transcripts(
        meeting_id="m1",
        sentences=sentences,
        title="Standup",
        transcript="[1] [spk:s1] hello world",
        db=db,
        embedding=embedding,
        llm=None,
    )
    assert n == 1
    db.delete_by_filter.assert_called_once_with(
        TRANSCRIPT_COLLECTION, "meeting_id", "m1"
    )
    db.upsert_points.assert_called_once()
    kwargs = db.upsert_points.call_args.kwargs
    assert kwargs["collection"] == TRANSCRIPT_COLLECTION
    assert len(kwargs["ids"]) == 1
    assert kwargs["payloads"][0]["meeting_id"] == "m1"
    assert kwargs["payloads"][0]["sentences"][0]["text"] == "hello world"
    embedding.embed_texts.assert_called_once()
    embedded = embedding.embed_texts.call_args[0][0][0]
    assert "hello world" in embedded
    assert "Standup" in embedded


def test_index_payload_has_spoken_text_and_current_flags():
    """Retriever/reranker read payload['text']; archive filter needs current flags."""
    from src.meeting.transcript_index import index_meeting_transcripts

    db = MagicMock()
    db.collection_exists.return_value = True
    embedding = MagicMock()
    embedding.dimensions = 4
    embedding.embed_texts.return_value = [[0.1, 0.2, 0.3, 0.4]]

    index_meeting_transcripts(
        meeting_id="m1",
        sentences=[
            {
                "sentence_id": "stt_0001",
                "speaker": "s1",
                "original_text": "hello world",
                "start_time": 0,
                "end_time": 1,
            }
        ],
        title="Standup",
        db=db,
        embedding=embedding,
        llm=None,
    )
    payload = db.upsert_points.call_args.kwargs["payloads"][0]
    assert "hello world" in payload["text"]
    assert payload["archived"] is False
    assert payload["is_current"] is True
