"""Search hydrates spoken text before rerank so empty payload.text is not dropped."""

from __future__ import annotations

from unittest.mock import MagicMock

from src.rag.reranker import Reranker
from src.rag.retriever import RetrievedChunk


def test_search_transcript_packs_reranks_when_payload_text_missing():
    from src.meeting.transcript_index import search_transcript_packs

    chunk = RetrievedChunk(
        text="",
        score=0.9,
        metadata={
            "meeting_id": "m1",
            "pack_index": 0,
            "speakers": ["s1"],
            "sentences": [
                {
                    "ref_n": 1,
                    "speaker_id": "s1",
                    "text": "Q3 延期了",
                    "sentence_id": "stt_0001",
                }
            ],
        },
    )
    retriever = MagicMock()
    retriever.retrieve.return_value = [chunk]
    provider = MagicMock()
    provider.rerank.return_value = [(0, 0.99)]
    reranker = Reranker(provider=provider, top_k=10)

    packs = search_transcript_packs(
        "Q3",
        meeting_id="m1",
        retriever=retriever,
        reranker=reranker,
    )
    assert packs
    assert packs[0]["sentences"][0]["text"] == "Q3 延期了"
    provider.rerank.assert_called_once()
    docs = provider.rerank.call_args.args[1]
    assert any("Q3 延期了" in doc for doc in docs)
