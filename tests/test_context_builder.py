"""Step 2: ContextBuilder — cluster-formatted context string."""

import pytest
from src.rag.retriever import RetrievedChunk


# ── helpers ──────────────────────────────────────────────────────────────

def _ck(text, score=0.90, **meta):
    """Create a RetrievedChunk with concise defaults."""
    defaults = {
        "id": f"id-{hash(text) % 100000:05d}",
        "collection": "test_collection",
        "source": "doc.md",
        "chunk_index": 1,
        "summary": "A test document",
        "uploaded_at": "2026-06-20",
    }
    defaults.update(meta)
    defaults.setdefault("id", f"id-{hash(text) % 100000:05d}")
    return RetrievedChunk(text=text, score=score, metadata=defaults)


def _cb(chunks, gap_indicators=True):
    """Shortcut to build_context."""
    from src.rag.context_builder import build_context
    return build_context(chunks, gap_indicators=gap_indicators)


# ── TestContextBuilderBasic ───────────────────────────────────────────

class TestContextBuilderBasic:
    """基本格式"""

    def test_basic_clustering(self):
        """2 chunk 同 source, 1 chunk 不同 source：断言有 "## Database:", "### Source:" """
        chunks = [
            _ck("text A", source="doc1.md", chunk_index=1),
            _ck("text B", source="doc1.md", chunk_index=2),
            _ck("text C", source="doc2.md", chunk_index=1),
        ]
        result = _cb(chunks)
        assert "## Database: test_collection" in result
        assert result.count("### Source:") == 2

    def test_chunk_index_in_output(self):
        """断言每个 chunk 有 "#1", "#2" 等序号"""
        chunks = [
            _ck("first", chunk_index=1),
            _ck("second", chunk_index=2),
        ]
        result = _cb(chunks)
        assert "Chunk #1" in result
        assert "Chunk #2" in result

    def test_score_in_output(self):
        """断言每个 chunk 显示 score"""
        chunks = [_ck("text", score=0.87)]
        result = _cb(chunks)
        assert "score: 0.87" in result

    def test_chunk_id_in_output(self):
        """断言每个 chunk 显示 Qdrant point ID（metadata["id"]）"""
        chunks = [_ck("text", id="qdrant-point-abc")]
        result = _cb(chunks)
        assert "id: qdrant-point-abc" in result

    def test_context_field_in_output(self):
        """chunk 有 metadata["context"] 时：断言显示 "[Context: ...]" """
        chunks = [_ck("text", context="This is the surrounding context")]
        result = _cb(chunks)
        assert "[Context: This is the surrounding context]" in result

    def test_text_in_output(self):
        """断言 chunk.text 原文出现在输出中"""
        chunks = [_ck("hello world this is chunk text")]
        result = _cb(chunks)
        assert "hello world this is chunk text" in result

    def test_summary_at_source_level(self):
        """同 source 3 个 chunk 有相同 summary：断言 "Document summary:" 只出现 1 次，在 source header 下"""
        chunks = [
            _ck("a", source="doc.md", summary="Summary X", chunk_index=1),
            _ck("b", source="doc.md", summary="Summary X", chunk_index=2),
            _ck("c", source="doc.md", summary="Summary X", chunk_index=3),
        ]
        result = _cb(chunks)
        assert result.count("Document summary:") == 1
        # summary appears under the source header, before chunks
        summary_pos = result.index("Document summary:")
        chunk1_pos = result.index("Chunk #1")
        assert summary_pos < chunk1_pos

    def test_upload_date_in_output(self):
        """chunk 有 metadata["uploaded_at"] 时：断言显示 "Uploaded:" """
        chunks = [_ck("text", uploaded_at="2026-01-15")]
        result = _cb(chunks)
        assert "Uploaded: 2026-01-15" in result

    def test_multiple_sources_under_same_collection(self):
        """同一 collection 下有 2 个不同 source：断言两个 "### Source:" header"""
        chunks = [
            _ck("a", source="a.md", chunk_index=1),
            _ck("b", source="b.md", chunk_index=1),
        ]
        result = _cb(chunks)
        assert "### Source: a.md" in result
        assert "### Source: b.md" in result


# ── TestContextBuilderGapIndicator ────────────────────────────────────

class TestContextBuilderGapIndicator:
    """断层指示器"""

    def test_gap_detected(self):
        """同 source chunk #1, #2, #7：断言 "[Note: 4 intermediate chunks (#3–#6)" """
        chunks = [
            _ck("a", source="doc.md", chunk_index=1),
            _ck("b", source="doc.md", chunk_index=2),
            _ck("c", source="doc.md", chunk_index=7),
        ]
        result = _cb(chunks)
        assert "[Note:" in result
        assert "4 intermediate chunk" in result
        assert "#3" in result
        assert "#6" in result

    def test_no_gap_when_contiguous(self):
        """chunk #1, #2, #3 连续：断言不出现 "[Note:" """
        chunks = [
            _ck("a", source="doc.md", chunk_index=1),
            _ck("b", source="doc.md", chunk_index=2),
            _ck("c", source="doc.md", chunk_index=3),
        ]
        result = _cb(chunks)
        assert "[Note:" not in result

    def test_gap_at_start(self):
        """chunk #5, #6, #7：无 gap，尽管前面有缺失（前面不属于 retained_chunks）"""
        chunks = [
            _ck("a", source="doc.md", chunk_index=5),
            _ck("b", source="doc.md", chunk_index=6),
            _ck("c", source="doc.md", chunk_index=7),
        ]
        result = _cb(chunks)
        # No gap between contiguous chunks — no gap indicator needed
        assert "[Note:" not in result

    def test_multiple_gaps(self):
        """chunk #1, #3, #5：两个 gap（#2 和 #4 各一个）"""
        chunks = [
            _ck("a", source="doc.md", chunk_index=1),
            _ck("b", source="doc.md", chunk_index=3),
            _ck("c", source="doc.md", chunk_index=5),
        ]
        result = _cb(chunks)
        assert result.count("[Note:") == 2
        assert "1 intermediate chunk" in result

    def test_gap_indicator_disabled(self):
        """gap_indicators=False：断言不出现 "[Note:" """
        chunks = [
            _ck("a", source="doc.md", chunk_index=1),
            _ck("b", source="doc.md", chunk_index=5),
        ]
        result = _cb(chunks, gap_indicators=False)
        assert "[Note:" not in result

    def test_single_chunk_no_gap(self):
        """只有 1 个 chunk：断言不崩溃，不出现 gap 提示"""
        chunks = [_ck("a", chunk_index=1)]
        result = _cb(chunks)
        assert "[Note:" not in result


# ── TestContextBuilderEdgeCases ───────────────────────────────────────

class TestContextBuilderEdgeCases:
    """边界"""

    def test_empty_list(self):
        """chunks=[] => "" """
        from src.rag.context_builder import build_context
        result = build_context([])
        assert result == ""

    def test_chunk_missing_metadata(self):
        """chunk.metadata 缺失某些字段：断言不崩溃，缺失字段不显示或显示 "unknown" """
        c = RetrievedChunk(text="bare text", score=0.5, metadata={})
        result = _cb([c])
        assert "bare text" in result
        # Should not crash

    def test_chunk_without_id(self):
        """metadata["id"] 为空：断言不崩溃"""
        chunks = [_ck("text", id="")]  # empty id
        result = _cb(chunks)
        assert "Chunk #" in result  # still shows chunk number
        # No crash

    def test_special_chars_text(self):
        """Text with special characters: renders correctly without corruption"""
        chunks = [_ck("Text with special characters: em-dash —, bullet •, euro €")]
        result = _cb(chunks)
        assert "em-dash" in result
        assert "bullet" in result

    def test_very_long_chunk_text(self):
        """chunk.text 10000+ 字符：断言不被截断，完整输出"""
        long_text = "A" * 10001
        chunks = [_ck(long_text)]
        result = _cb(chunks)
        assert long_text in result

    def test_mixed_parent_child_chunks(self):
        """包含 chunk_type="parent" 和 "child" 两种：断言按 chunk_index 排序正确"""
        chunks = [
            _ck("child B", chunk_type="child", chunk_index=2),
            _ck("parent", chunk_type="parent", chunk_index=1),
            _ck("child A", chunk_type="child", chunk_index=1),
        ]
        result = _cb(chunks)
        # All chunks present
        assert "child B" in result
        assert "parent" in result
        assert "child A" in result


# ── TestContextBuilderMultiCollection ─────────────────────────────────

class TestContextBuilderMultiCollection:
    """多 collection"""

    def test_multi_collection_hint(self):
        """chunks 来自 2+ collection：断言 "[IMPORTANT: The following context comes from" """
        chunks = [
            _ck("a", collection="col_A"),
            _ck("b", collection="col_B"),
        ]
        result = _cb(chunks)
        assert "[IMPORTANT: The following context comes from" in result
        assert "2 DIFFERENT collections" in result

    def test_single_collection_no_hint(self):
        """只有 1 个 collection：断言不出现 "[IMPORTANT:" """
        chunks = [
            _ck("a", collection="my_col"),
            _ck("b", collection="my_col"),
        ]
        result = _cb(chunks)
        assert "[IMPORTANT:" not in result

    def test_collection_name_in_database_header(self):
        """断言 "## Database: {collection_name}" 出现"""
        chunks = [_ck("text", collection="my_knowledge_base")]
        result = _cb(chunks)
        assert "## Database: my_knowledge_base" in result
