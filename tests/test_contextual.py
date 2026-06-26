from unittest.mock import MagicMock
from src.rag.contextual import ContextualRetrieval
from src.rag.chunker import Chunk


def test_add_context():
    import json as _json

    combined_json = _json.dumps({
        "short_summary": "A document about Python programming.",
        "structured_summary": "===DATA===\n- Python was created in 1991\n===FACTS===\n- Python supports multiple paradigms\n===INSIGHTS===\n- None identified",
    })

    cr = ContextualRetrieval(llm=MagicMock(), context_window=1)
    cr._generate_summary = MagicMock(return_value={
        "short_summary": "A document about Python programming.",
        "structured_summary": "===DATA===\n- Python was created in 1991\n===FACTS===\n- Python supports multiple paradigms\n===INSIGHTS===\n- None identified",
    })
    cr._generate_context = MagicMock(return_value="Context for chunk")

    chunks = [
        Chunk(text="Python is a popular language.", metadata={"chunk_index": 0, "source": "test.txt"}),
        Chunk(text="It supports multiple paradigms.", metadata={"chunk_index": 1, "source": "test.txt"}),
    ]
    result = cr.add_context(chunks, full_document="Python is a popular language. It supports multiple paradigms.")

    assert len(result) == 2
    assert result[0].metadata["summary"] == "A document about Python programming."
    assert "_structured_summary" in result[0].metadata
    assert "context" in result[0].metadata
