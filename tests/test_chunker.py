from src.rag.chunker import TextChunker


def test_basic_chunking():
    chunker = TextChunker(chunk_size=50, chunk_overlap=10)
    text = "This is a test. " * 20
    chunks = chunker.chunk(text)
    assert len(chunks) > 1


def test_chunk_metadata():
    chunker = TextChunker(chunk_size=50, chunk_overlap=10)
    text = "Hello world. " * 10
    chunks = chunker.chunk_with_metadata(text, source="test.txt")
    assert all("source" in c.metadata for c in chunks)
    assert all("chunk_index" in c.metadata for c in chunks)


def test_empty_text():
    chunker = TextChunker()
    chunks = chunker.chunk("")
    assert chunks == []
