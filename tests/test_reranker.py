from unittest.mock import MagicMock
from src.rag.reranker import Reranker
from src.rag.retriever import RetrievedChunk


def test_rerank():
    provider = MagicMock()
    provider.rerank.return_value = [(1, 0.95), (0, 0.80)]

    reranker = Reranker(provider=provider, top_k=2)
    chunks = [
        RetrievedChunk(text="chunk A", score=0.8, metadata={}),
        RetrievedChunk(text="chunk B", score=0.7, metadata={}),
    ]
    result = reranker.rerank("query", chunks)

    assert len(result) == 2
    assert result[0].text == "chunk B"
    assert result[0].score == 0.95
