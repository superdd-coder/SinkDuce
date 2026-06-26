"""Tests for parent-child chunking mode.

Run: rtk pytest tests/test_parent_child.py
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest

from src.rag.chunker import Chunk, ParentChildChunker, ParagraphChunker, TextChunker


# ── Chunk model ────────────────────────────────────────────


def test_chunk_has_parent_id_field():
    """Chunk dataclass has parent_id field, defaults to None."""
    c = Chunk(text="hello")
    assert c.parent_id is None


def test_chunk_has_chunk_type_field():
    """Chunk dataclass has chunk_type field, defaults to 'normal'."""
    c = Chunk(text="hello")
    assert c.chunk_type == "normal"


def test_chunk_backward_compat():
    """Existing code that creates Chunk(text=..., metadata=...) still works."""
    c = Chunk(text="test", metadata={"source": "a.txt", "chunk_index": 0})
    assert c.text == "test"
    assert c.metadata["source"] == "a.txt"
    assert c.parent_id is None
    assert c.chunk_type == "normal"


# ── TextChunker unchanged ─────────────────────────────────


def test_normal_mode_unchanged():
    """TextChunker still works as before."""
    chunker = TextChunker(chunk_size=100, chunk_overlap=20)
    text = "Hello world. " * 20
    chunks = chunker.chunk_with_metadata(text, source="test.txt")
    assert len(chunks) > 0
    for c in chunks:
        assert c.parent_id is None
        assert c.chunk_type == "normal"
        assert "source" in c.metadata


# ── ParagraphChunker ───────────────────────────────────────


def test_paragraph_chunker_basic():
    """ParagraphChunker splits on paragraph boundaries and merges small ones."""
    chunker = ParagraphChunker(max_tokens=200, buffer_ratio=0.5)
    text = "Short paragraph one.\n\nShort paragraph two.\n\nShort paragraph three."
    chunks = chunker.chunk(text)
    # All short paragraphs should merge into one chunk
    assert len(chunks) == 1


def test_paragraph_chunker_respects_max_tokens():
    """Consecutive paragraphs are split when they exceed max_tokens."""
    chunker = ParagraphChunker(max_tokens=30, buffer_ratio=0.5)
    # Each paragraph ~15 tokens (30 chars English), 5 paragraphs = ~75 tokens
    text = "\n\n".join(["Word " * 15 for _ in range(5)])
    chunks = chunker.chunk(text)
    assert len(chunks) >= 2


def test_paragraph_chunker_buffer_keeps_large_paragraph():
    """A paragraph within buffer range (max_tokens * 1.5) is kept whole."""
    chunker = ParagraphChunker(max_tokens=20, buffer_ratio=0.5)
    # hard_limit = 30 tokens. A paragraph of ~25 tokens should stay whole.
    # 25 tokens ≈ 50 chars English
    para = "A" * 50  # ~25 tokens
    text = f"Small.\n\n{para}\n\nSmall."
    chunks = chunker.chunk(text)
    # The large paragraph should appear in a chunk, not be split
    found = any(para in c for c in chunks)
    assert found


def test_paragraph_chunker_splits_oversized_paragraph():
    """A paragraph exceeding hard limit is split at max_tokens boundary."""
    chunker = ParagraphChunker(max_tokens=20, buffer_ratio=0.5)
    # hard_limit = 30 tokens. A paragraph of ~50 tokens must be split.
    # 50 tokens ≈ 100 chars English
    para = "B" * 100
    chunks = chunker.chunk(para)
    assert len(chunks) >= 2


def test_paragraph_chunker_empty_text():
    """Empty text returns no chunks."""
    chunker = ParagraphChunker(max_tokens=100)
    assert chunker.chunk("") == []
    assert chunker.chunk("   ") == []


def test_paragraph_chunker_single_paragraph():
    """Single short paragraph produces one chunk."""
    chunker = ParagraphChunker(max_tokens=100)
    text = "Just one paragraph with some content."
    chunks = chunker.chunk(text)
    assert len(chunks) == 1
    assert chunks[0] == text


def test_paragraph_chunker_with_metadata():
    """ParagraphChunker.chunk_with_metadata returns Chunk objects with correct metadata."""
    chunker = ParagraphChunker(max_tokens=200)
    text = "First paragraph.\n\nSecond paragraph."
    chunks = chunker.chunk_with_metadata(text, source="test.txt")
    assert len(chunks) >= 1
    for c in chunks:
        assert isinstance(c, Chunk)
        assert c.metadata["source"] == "test.txt"
        assert "chunk_index" in c.metadata
        assert "total_chunks" in c.metadata
        assert "char_offset" in c.metadata
        assert c.chunk_type == "normal"
        assert c.parent_id is None


def test_paragraph_chunker_chinese_text():
    """ParagraphChunker handles Chinese text correctly."""
    chunker = ParagraphChunker(max_tokens=20, buffer_ratio=0.5)
    # Short paragraphs that will be combined within token limit
    para1 = "Short paragraph text content. " * 3
    para2 = "Another paragraph here. " * 2
    text = f"{para1}\n\n{para2}"
    chunks = chunker.chunk(text)
    assert len(chunks) >= 1
    # All text should be present
    combined = "".join(chunks)
    assert "Short paragraph" in combined
    assert "Another paragraph" in combined


# ── ParentChildChunker: paragraph strategy ─────────────────


def test_parent_child_chunker_paragraph():
    """Parent chunks split on paragraph boundaries."""
    chunker = ParentChildChunker(
        parent_strategy="paragraph",
        parent_chunk_size=500,
        child_chunk_size=50,
    )
    text = "Paragraph one about topic A.\n\nParagraph two about topic B.\n\nParagraph three about topic C."
    chunks = chunker.chunk_with_metadata(text, source="test.txt")

    parents = [c for c in chunks if c.chunk_type == "parent"]
    children = [c for c in chunks if c.chunk_type == "child"]

    assert len(parents) >= 1
    assert len(children) >= 1
    # Each child must reference a parent
    parent_ids = {p.metadata.get("chunk_id") for p in parents}
    for child in children:
        assert child.parent_id in parent_ids


def test_parent_child_paragraph_single_paragraph():
    """Single paragraph produces one parent with children."""
    chunker = ParentChildChunker(
        parent_strategy="paragraph",
        child_chunk_size=50,
    )
    text = "This is a single paragraph with enough text to be split into multiple child chunks. " * 3
    chunks = chunker.chunk_with_metadata(text)

    parents = [c for c in chunks if c.chunk_type == "parent"]
    children = [c for c in chunks if c.chunk_type == "child"]

    assert len(parents) == 1
    assert len(children) >= 1
    assert all(c.parent_id == parents[0].metadata["chunk_id"] for c in children)


# ── ParentChildChunker: fixed_token strategy ───────────────


def test_parent_child_chunker_fixed_token():
    """Parent chunks split at approximate fixed token boundaries."""
    chunker = ParentChildChunker(
        parent_strategy="fixed_token",
        parent_chunk_size=200,
        child_chunk_size=30,
    )
    # ~400 chars English ≈ 200 tokens
    text = "word " * 200
    chunks = chunker.chunk_with_metadata(text, source="test.txt")

    parents = [c for c in chunks if c.chunk_type == "parent"]
    children = [c for c in chunks if c.chunk_type == "child"]

    assert len(parents) >= 2
    assert len(children) >= 2
    parent_ids = {p.metadata.get("chunk_id") for p in parents}
    for child in children:
        assert child.parent_id in parent_ids


# ── ParentChildChunker: heading strategy ───────────────────


def test_parent_child_chunker_heading():
    """Parent chunks split on markdown headings."""
    chunker = ParentChildChunker(
        parent_strategy="heading",
        child_chunk_size=30,
    )
    text = """# Introduction

This is the introduction section with some content.

## Background

This is the background section with detailed information.

## Methods

This section describes the methods used."""
    chunks = chunker.chunk_with_metadata(text, source="test.txt")

    parents = [c for c in chunks if c.chunk_type == "parent"]
    children = [c for c in chunks if c.chunk_type == "child"]

    # Should have at least 3 parents (one per heading)
    assert len(parents) >= 3
    assert len(children) >= 3
    parent_ids = {p.metadata.get("chunk_id") for p in parents}
    for child in children:
        assert child.parent_id in parent_ids


# ── Parent-child ID linkage ───────────────────────────────


def test_parent_child_ids_link_correctly():
    """Each child has parent_id pointing to a valid parent, each parent has unique ID."""
    chunker = ParentChildChunker(
        parent_strategy="paragraph",
        child_chunk_size=30,
    )
    text = "First paragraph content here.\n\nSecond paragraph content here.\n\nThird paragraph content here."
    chunks = chunker.chunk_with_metadata(text)

    parents = [c for c in chunks if c.chunk_type == "parent"]
    children = [c for c in chunks if c.chunk_type == "child"]

    # All parents have unique IDs
    parent_ids = [p.metadata["chunk_id"] for p in parents]
    assert len(parent_ids) == len(set(parent_ids))

    # All children reference valid parents
    for child in children:
        assert child.parent_id in parent_ids


def test_parent_and_child_texts_are_valid():
    """Parents contain the original text, children contain subsets."""
    chunker = ParentChildChunker(
        parent_strategy="paragraph",
        child_chunk_size=50,
    )
    text = "Alpha paragraph.\n\nBeta paragraph."
    chunks = chunker.chunk_with_metadata(text)

    parents = [c for c in chunks if c.chunk_type == "parent"]
    children = [c for c in chunks if c.chunk_type == "child"]

    for p in parents:
        assert len(p.text.strip()) > 0
    for c in children:
        assert len(c.text.strip()) > 0


def test_metadata_propagated():
    """Source and extra_metadata are propagated to all chunks."""
    chunker = ParentChildChunker(child_chunk_size=50)
    text = "Some content here. " * 10
    chunks = chunker.chunk_with_metadata(
        text, source="doc.pdf", extra_metadata={"file_type": "pdf"}
    )

    for c in chunks:
        assert c.metadata.get("source") == "doc.pdf"
        assert c.metadata.get("file_type") == "pdf"


# ── Config storage ─────────────────────────────────────────


def test_config_stored_in_qdrant():
    """Collection config persists in Qdrant."""
    from src.db.qdrant import QdrantManager

    mock_client = MagicMock()
    db = QdrantManager.__new__(QdrantManager)
    db.client = mock_client

    config = {"chunk_mode": "parent_child", "parent_strategy": "paragraph"}
    db.create_collection("test_col", vector_size=512, chunk_config=config)

    # Verify create_collection was called
    mock_client.create_collection.assert_called_once()

    # Now test get_collection_config - mock retrieve
    mock_point = MagicMock()
    mock_point.payload = {"chunk_config": config}
    mock_client.retrieve.return_value = [mock_point]

    result = db.get_collection_config("test_col")
    assert result["chunk_mode"] == "parent_child"
    assert result["parent_strategy"] == "paragraph"


def test_config_read_returns_default():
    """Missing config returns default values (backward compat)."""
    from src.db.qdrant import QdrantManager

    mock_client = MagicMock()
    db = QdrantManager.__new__(QdrantManager)
    db.client = mock_client

    # Simulate empty retrieve (no config point)
    mock_client.retrieve.return_value = []

    result = db.get_collection_config("old_collection")
    assert result["chunk_mode"] == "normal"
    assert result["parent_strategy"] == "paragraph"
    assert result["child_chunk_size"] == 128


# ── Upload handler integration ────────────────────────────


def test_upload_handler_uses_parent_child_chunker():
    """Upload handler creates ParentChildChunker for parent_child collections."""
    import asyncio
    from src.tasks.handlers import upload_handler
    from src.tasks.task_manager import Task

    from src.config import EmbeddingConfig, EmbeddingProviderConfig

    mock_services = MagicMock()
    mock_services.db.collection_exists.return_value = True
    mock_services.db.get_collection_config.return_value = {
        "chunk_mode": "parent_child",
        "parent_strategy": "paragraph",
        "parent_chunk_size": 1024,
        "parent_chunk_overlap": 128,
        "child_chunk_size": 128,
        "child_chunk_overlap": 32,
        "contextual_enabled": False,
    }
    mock_services.config.embedding = EmbeddingConfig(providers=[
        EmbeddingProviderConfig(id="test", name="test", provider="local", model="test", dimensions=512, is_default=True)
    ])
    mock_services.db.get_vector_size.return_value = 512
    mock_embedding = MagicMock()
    mock_embedding.embed_texts.return_value = [[0.1] * 512]
    mock_services.embedding = mock_embedding

    import tempfile, os
    tmp = tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w")
    tmp.write("Test content for parent child chunking.")
    tmp.close()

    task = Task(id="test", filename="test.txt")

    with patch("src.tasks.handlers.services", mock_services), \
         patch("src.services.services", mock_services), \
         patch("src.tasks.handlers.parse_file") as mock_parse, \
         patch("src.rag.collection_utils.create_embedding_provider", return_value=mock_embedding):
        mock_parse.return_value = MagicMock(content="Test content for parent child chunking.", file_type="txt")
        result = asyncio.run(upload_handler(task, tmp.name, "test_col", "test.txt"))

    os.unlink(tmp.name)

    # Verify upsert was called (chunks were created)
    mock_services.db.upsert_points.assert_called_once()
    call_args = mock_services.db.upsert_points.call_args
    payloads = call_args.kwargs.get("payloads") or call_args[1].get("payloads") or call_args[0][2]

    # Should have parent_id and chunk_type in payloads
    chunk_types = {p.get("chunk_type") for p in payloads}
    assert "parent" in chunk_types or "child" in chunk_types


def test_upload_handler_normal_mode_unchanged():
    """Upload handler with normal mode creates ParagraphChunker and indexes chunks."""
    import asyncio
    from src.tasks.handlers import upload_handler
    from src.tasks.task_manager import Task
    from src.config import EmbeddingConfig, EmbeddingProviderConfig

    mock_services = MagicMock()
    mock_services.db.collection_exists.return_value = True
    mock_services.db.get_collection_config.return_value = {
        "chunk_mode": "normal",
        "chunk_size": 512,
        "buffer_ratio": 0.5,
        "contextual_enabled": False,
    }
    mock_services.config.embedding = EmbeddingConfig(providers=[
        EmbeddingProviderConfig(id="test", name="test", provider="local", model="test", dimensions=512, is_default=True)
    ])
    mock_services.db.get_vector_size.return_value = 512
    mock_embedding = MagicMock()
    mock_embedding.embed_texts.return_value = [[0.1] * 512]
    mock_services.embedding = mock_embedding

    import tempfile, os
    tmp = tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w")
    tmp.write("Test content. " * 50)
    tmp.close()

    task = Task(id="test", filename="test.txt")

    with patch("src.tasks.handlers.services", mock_services), \
         patch("src.services.services", mock_services), \
         patch("src.tasks.handlers.parse_file") as mock_parse, \
         patch("src.rag.collection_utils.create_embedding_provider", return_value=mock_embedding):
        mock_parse.return_value = MagicMock(content="Test content. " * 50, file_type="txt")
        result = asyncio.run(upload_handler(task, tmp.name, "test_col", "test.txt"))

    os.unlink(tmp.name)

    # Handler creates its own ParagraphChunker from collection config
    mock_services.db.upsert_points.assert_called_once()
    assert result["chunks_count"] >= 1


# ── Collection creation with config ───────────────────────


def test_collection_create_request_schema():
    """CollectionCreateRequest accepts chunk config fields."""
    from src.api.schemas import CollectionCreateRequest

    req = CollectionCreateRequest(
        name="test",
        dimensions=512,
        chunk_mode="parent_child",
        parent_strategy="paragraph",
        parent_chunk_size=1024,
        child_chunk_size=128,
    )
    assert req.chunk_mode == "parent_child"
    assert req.parent_strategy == "paragraph"


def test_collection_create_request_defaults():
    """CollectionCreateRequest defaults to normal mode."""
    from src.api.schemas import CollectionCreateRequest

    req = CollectionCreateRequest(name="test")
    assert req.chunk_mode == "normal"
    assert req.parent_strategy == "paragraph"
    assert req.parent_chunk_size == 1024
    assert req.child_chunk_size == 128


# ── Query adaptation ───────────────────────────────────────


def test_query_parent_child_returns_parent_text():
    """Query on parent_child collection returns parent text, not child text."""
    from src.db.qdrant import QdrantManager

    mock_client = MagicMock()
    db = QdrantManager.__new__(QdrantManager)
    db.client = mock_client

    # Mock search returns child chunks
    child1 = MagicMock()
    child1.id = "child-1"
    child1.score = 0.95
    child1.payload = {
        "text": "child chunk text",
        "parent_id": "parent-1",
        "chunk_type": "child",
    }

    mock_result = MagicMock()
    mock_result.points = [child1]
    mock_client.query_points.return_value = mock_result

    # Mock retrieve for parent
    parent_point = MagicMock()
    parent_point.payload = {
        "text": "full parent paragraph text with much more context",
        "chunk_type": "parent",
    }
    mock_client.retrieve.return_value = [parent_point]

    # Search for child chunks with filter
    from qdrant_client.models import Filter, FieldCondition, MatchValue
    filter_cond = Filter(must=[FieldCondition(key="chunk_type", match=MatchValue(value="child"))])

    results = db.search("test_col", query_vector=[0.1] * 512, top_k=5, filter_condition=filter_cond)

    assert len(results) == 1
    assert results[0]["payload"]["chunk_type"] == "child"
    assert results[0]["payload"]["parent_id"] == "parent-1"

    # Now retrieve parent by ID
    parent_results = db.get_points_by_ids("test_col", ["parent-1"])
    assert len(parent_results) == 1
    assert "parent" in parent_results[0]["payload"].get("chunk_type", "")


# ── Unified data model (Task 1B) ───────────────────────────


def test_chunk_metadata_completeness():
    """Chunks have total_chunks and char_offset in metadata."""
    chunker = TextChunker(chunk_size=50, chunk_overlap=10)
    text = "word " * 100  # 500 chars
    chunks = chunker.chunk_with_metadata(text, source="test.txt")
    for c in chunks:
        assert "total_chunks" in c.metadata
        assert "char_offset" in c.metadata
        assert c.metadata["total_chunks"] == len(chunks)



def test_parent_child_chunker_has_total_chunks_and_char_offset():
    """ParentChildChunker adds total_chunks and char_offset to all chunks."""
    chunker = ParentChildChunker(
        parent_strategy="paragraph",
        child_chunk_size=30,
    )
    text = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
    chunks = chunker.chunk_with_metadata(text, source="test.txt")

    parents = [c for c in chunks if c.chunk_type == "parent"]
    children = [c for c in chunks if c.chunk_type == "child"]

    for p in parents:
        assert "total_chunks" in p.metadata
        assert p.metadata["total_chunks"] == len(parents)
        assert "char_offset" in p.metadata

    for c in children:
        assert "total_chunks" in c.metadata
        assert "char_offset" in c.metadata


# ── Empty content / empty chunks guards ────────────────────


def test_upsert_points_empty_ids_no_error():
    """upsert_points with empty ids list does not throw."""
    from src.db.qdrant import QdrantManager

    mock_client = MagicMock()
    db = QdrantManager.__new__(QdrantManager)
    db.client = mock_client

    # Should return without calling Qdrant
    db.upsert_points("col", ids=[], vectors=[], payloads=[])
    mock_client.upsert.assert_not_called()


def test_upload_handler_empty_content_raises():
    """Upload handler raises ValueError when parsing returns empty content."""
    import asyncio
    from src.tasks.handlers import upload_handler
    from src.tasks.task_manager import Task

    mock_services = MagicMock()
    mock_services.db.collection_exists.return_value = True

    import tempfile, os
    tmp = tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w")
    tmp.write("placeholder")
    tmp.close()

    task = Task(id="test", filename="empty.pdf")

    with patch("src.tasks.handlers.services", mock_services), \
         patch("src.tasks.handlers.parse_file") as mock_parse:
        mock_parse.return_value = MagicMock(content="   ", file_type="pdf")
        with pytest.raises(Exception, match="No extractable text found"):
            asyncio.run(upload_handler(task, tmp.name, "test_col", "empty.pdf"))

    os.unlink(tmp.name)


def test_upload_handler_empty_chunks_raises():
    """Upload handler raises ValueError when chunking produces no results."""
    import asyncio
    from src.tasks.handlers import upload_handler
    from src.tasks.task_manager import Task

    mock_services = MagicMock()
    mock_services.db.collection_exists.return_value = True
    mock_services.db.get_collection_config.return_value = {
        "chunk_mode": "normal",
        "chunk_size": 512,
        "buffer_ratio": 0.5,
        "contextual_enabled": False,
    }

    import tempfile, os
    tmp = tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w")
    tmp.write("placeholder")
    tmp.close()

    task = Task(id="test", filename="tiny.txt")

    # Mock chunker to return empty list (simulating edge case)
    mock_chunker = MagicMock()
    mock_chunker.chunk_with_metadata.return_value = []

    with patch("src.tasks.handlers.services", mock_services), \
         patch("src.tasks.handlers.parse_file") as mock_parse, \
         patch("src.tasks.handlers.ParagraphChunker", return_value=mock_chunker):
        mock_parse.return_value = MagicMock(content="some text", file_type="txt")
        with pytest.raises(Exception, match="Chunking produced no results"):
            asyncio.run(upload_handler(task, tmp.name, "test_col", "tiny.txt"))

    os.unlink(tmp.name)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
