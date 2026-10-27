from unittest.mock import MagicMock
from src.rag.retriever import Retriever


def test_retrieve():
    embedding = MagicMock()
    embedding.embed_query.return_value = [0.1] * 512

    db = MagicMock()
    db.search.return_value = [
        {"id": "1", "score": 0.9, "payload": {"text": "hello", "source": "a.txt"}},
        {"id": "2", "score": 0.8, "payload": {"text": "world", "source": "b.txt"}},
    ]

    retriever = Retriever(db=db, embedding=embedding)
    results = retriever.retrieve("test query", collection="default", top_k=5)

    assert len(results) == 2
    assert results[0].text == "hello"
    assert results[0].score == 0.9
    embedding.embed_query.assert_called_once_with("test query")
    db.search.assert_called_once()
