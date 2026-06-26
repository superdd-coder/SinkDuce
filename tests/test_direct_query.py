"""Step 1: DirectQueryModule — atomic retrieval across collections."""

import pytest
from unittest.mock import MagicMock, patch, call

from src.rag.retriever import RetrievedChunk


# ── helpers ──────────────────────────────────────────────────────────────

def _make_chunk(text, score, metadata=None):
    m = metadata or {}
    m.setdefault("id", f"id-{hash(text) % 10000}")
    return RetrievedChunk(text=text, score=score, metadata=m)


def _make_chunk_with_id(chunk_id, text, score, **extra_meta):
    return RetrievedChunk(text=text, score=score, metadata={"id": chunk_id, **extra_meta})


# ── TestDirectQueryNormal ────────────────────────────────────────────────

class TestDirectQueryNormal:
    """正常场景"""

    def test_single_collection_normal(self):
        """单 collection normal 模式：断言返回 DirectQueryResult，chunks 有 text/score/metadata"""
        from src.rag.direct_query import DirectQueryModule, DirectQueryResult

        mock_retriever = MagicMock()
        mock_retriever.retrieve.return_value = [
            _make_chunk("hello world", 0.95, {"source": "doc1.md"}),
            _make_chunk("foo bar", 0.80, {"source": "doc1.md"}),
        ]
        mock_db = MagicMock()
        mock_db.get_collection_config.return_value = {"chunk_mode": "normal"}

        dm = DirectQueryModule(retriever=mock_retriever, db=mock_db)
        result = dm.retrieve("test query", ["col_a"], top_k=5)

        assert isinstance(result, DirectQueryResult)
        assert len(result.chunks) == 2
        for c in result.chunks:
            assert isinstance(c.text, str)
            assert isinstance(c.score, float)
            assert isinstance(c.metadata, dict)
            assert c.metadata["collection"] == "col_a"

    def test_single_collection_parent_child(self):
        """单 collection parent_child 模式：断言调了 _retrieve_parent_child，返回 parent chunks + child_groups"""
        from src.rag.direct_query import DirectQueryModule, DirectQueryResult

        mock_retriever = MagicMock()
        mock_db = MagicMock()
        mock_db.get_collection_config.return_value = {"chunk_mode": "parent_child"}

        dm = DirectQueryModule(retriever=mock_retriever, db=mock_db)

        # Patch _retrieve_parent_child
        parent_chunk = _make_chunk_with_id("pid-1", "parent text", 0.90)
        child_groups = {"pid-1": [{"id": "cid-1", "text": "child text", "score": 0.90}]}
        with patch.object(dm, "_retrieve_parent_child", return_value=([parent_chunk], child_groups)):
            result = dm.retrieve("test query", ["col_a"], top_k=5)

        assert isinstance(result, DirectQueryResult)
        assert len(result.chunks) == 1
        assert result.chunks[0].metadata["id"] == "pid-1"
        assert result.chunks[0].metadata["collection"] == "col_a"
        assert "pid-1" in result.child_groups
        assert len(result.child_groups["pid-1"]) == 1

    def test_multi_collection_mixed_mode(self):
        """col1=normal, col2=parent_child：断言两种结果都被合并，按 score 降序"""
        from src.rag.direct_query import DirectQueryModule

        mock_retriever = MagicMock()
        mock_retriever.retrieve.return_value = [
            _make_chunk("normal chunk", 0.70),
        ]
        mock_db = MagicMock()
        mock_db.get_collection_config.side_effect = [
            {"chunk_mode": "normal"},
            {"chunk_mode": "parent_child"},
        ]

        dm = DirectQueryModule(retriever=mock_retriever, db=mock_db)

        pc_chunk = _make_chunk_with_id("pid-x", "pc parent text", 0.95)
        with patch.object(dm, "_retrieve_parent_child", return_value=([pc_chunk], {})):
            result = dm.retrieve("test", ["col_a", "col_b"], top_k=10)

        # Both results merged
        assert len(result.chunks) == 2
        # pc chunk (0.95) should come before normal chunk (0.70)
        assert result.chunks[0].score >= result.chunks[1].score

    def test_multi_collection_all_normal(self):
        """3 个 normal collection：断言所有 collection 都被检索，结果合并"""
        from src.rag.direct_query import DirectQueryModule

        mock_retriever = MagicMock()
        mock_retriever.retrieve.side_effect = [
            [_make_chunk(f"colA-{i}", 0.9 - i * 0.1) for i in range(2)],
            [_make_chunk(f"colB-{i}", 0.8 - i * 0.1) for i in range(2)],
            [_make_chunk(f"colC-{i}", 0.7 - i * 0.1) for i in range(2)],
        ]
        mock_db = MagicMock()
        mock_db.get_collection_config.return_value = {"chunk_mode": "normal"}

        dm = DirectQueryModule(retriever=mock_retriever, db=mock_db)
        result = dm.retrieve("test", ["col_a", "col_b", "col_c"], top_k=10)

        assert len(result.chunks) == 6
        assert mock_retriever.retrieve.call_count == 3

    def test_no_text_dedup(self):
        """两个 collection 返回相同 text 不同 score：断言两条都保留，metadata["collection"] 不同"""
        from src.rag.direct_query import DirectQueryModule

        mock_retriever = MagicMock()
        mock_retriever.retrieve.side_effect = [
            [_make_chunk("same text", 0.90, {"source": "a.md"})],
            [_make_chunk("same text", 0.80, {"source": "b.md"})],
        ]
        mock_db = MagicMock()
        mock_db.get_collection_config.return_value = {"chunk_mode": "normal"}

        dm = DirectQueryModule(retriever=mock_retriever, db=mock_db)
        result = dm.retrieve("test", ["col_a", "col_b"], top_k=10)

        # Both chunks kept (no text dedup)
        assert len(result.chunks) == 2
        assert result.chunks[0].text == "same text"
        assert result.chunks[1].text == "same text"
        collections = {c.metadata["collection"] for c in result.chunks}
        assert collections == {"col_a", "col_b"}

    def test_rerank_called(self):
        """传了 reranker 且 rerank_top_k 有值：断言 reranker.rerank 被调用，参数正确"""
        from src.rag.direct_query import DirectQueryModule

        mock_retriever = MagicMock()
        mock_retriever.retrieve.return_value = [
            _make_chunk("text A", 0.90),
            _make_chunk("text B", 0.80),
            _make_chunk("text C", 0.70),
        ]
        mock_db = MagicMock()
        mock_db.get_collection_config.return_value = {"chunk_mode": "normal"}
        mock_reranker = MagicMock()
        mock_reranker.rerank.return_value = [
            _make_chunk("text C", 0.99),
            _make_chunk("text A", 0.88),
        ]

        dm = DirectQueryModule(retriever=mock_retriever, db=mock_db, reranker=mock_reranker)
        result = dm.retrieve("test", ["col_a"], top_k=10, rerank_enabled=True, rerank_top_k=2)

        mock_reranker.rerank.assert_called_once()
        call_args = mock_reranker.rerank.call_args
        assert call_args[0][0] == "test"  # query
        assert len(call_args[0][1]) == 3   # all 3 chunks passed to reranker
        assert call_args[1]["top_k"] == 2
        assert len(result.chunks) == 2

    def test_rerank_not_called_when_none(self):
        """reranker=None：断言不崩，不回退"""
        from src.rag.direct_query import DirectQueryModule

        mock_retriever = MagicMock()
        mock_retriever.retrieve.return_value = [
            _make_chunk("text", 0.90),
        ]
        mock_db = MagicMock()
        mock_db.get_collection_config.return_value = {"chunk_mode": "normal"}

        dm = DirectQueryModule(retriever=mock_retriever, db=mock_db, reranker=None)
        result = dm.retrieve("test", ["col_a"], top_k=5)

        assert len(result.chunks) == 1
        # Should not crash

    def test_score_sorting_desc(self):
        """合并后按 score 降序排列：断言 chunks[i].score >= chunks[i+1].score"""
        from src.rag.direct_query import DirectQueryModule

        mock_retriever = MagicMock()
        mock_retriever.retrieve.side_effect = [
            [_make_chunk("A", 0.60)],
            [_make_chunk("B", 0.95)],
            [_make_chunk("C", 0.30)],
        ]
        mock_db = MagicMock()
        mock_db.get_collection_config.return_value = {"chunk_mode": "normal"}

        dm = DirectQueryModule(retriever=mock_retriever, db=mock_db)
        result = dm.retrieve("test", ["c1", "c2", "c3"], top_k=10)

        for i in range(len(result.chunks) - 1):
            assert result.chunks[i].score >= result.chunks[i + 1].score

    def test_top_k_truncation(self):
        """top_k=3，返回 5 个 chunk：断言最终返回 3 个"""
        from src.rag.direct_query import DirectQueryModule

        mock_retriever = MagicMock()
        mock_retriever.retrieve.return_value = [
            _make_chunk(f"chunk-{i}", 0.9 - i * 0.1) for i in range(5)
        ]
        mock_db = MagicMock()
        mock_db.get_collection_config.return_value = {"chunk_mode": "normal"}

        dm = DirectQueryModule(retriever=mock_retriever, db=mock_db)
        result = dm.retrieve("test", ["col_a"], top_k=3)

        assert len(result.chunks) == 3


# ── TestDirectQueryEdgeCases ─────────────────────────────────────────────

class TestDirectQueryEdgeCases:
    """边界情况"""

    def test_empty_collections_list(self):
        """collections=[]：断言返回空 DirectQueryResult，不崩溃"""
        from src.rag.direct_query import DirectQueryModule, DirectQueryResult

        mock_retriever = MagicMock()
        mock_db = MagicMock()
        dm = DirectQueryModule(retriever=mock_retriever, db=mock_db)

        result = dm.retrieve("test", [], top_k=5)
        assert isinstance(result, DirectQueryResult)
        assert result.chunks == []
        assert result.child_groups == {}

    def test_nonexistent_collection(self):
        """collection 不存在：断言返回空结果或抛明确异常（不静默吞掉）"""
        from src.rag.direct_query import DirectQueryModule

        mock_retriever = MagicMock()
        mock_db = MagicMock()
        # get_collection_config succeeds but retriever raises
        mock_db.get_collection_config.return_value = {"chunk_mode": "normal"}
        mock_retriever.retrieve.side_effect = RuntimeError("collection not found")

        dm = DirectQueryModule(retriever=mock_retriever, db=mock_db)
        result = dm.retrieve("test", ["nonexistent"], top_k=5)

        # Should not crash — returns empty result (skipped)
        assert result.chunks == []

    def test_no_embedding_override(self):
        """embedding_overrides=None 或 {}：断言正常检索，不报错"""
        from src.rag.direct_query import DirectQueryModule

        mock_retriever = MagicMock()
        mock_retriever.retrieve.return_value = [
            _make_chunk("text", 0.90),
        ]
        mock_db = MagicMock()
        mock_db.get_collection_config.return_value = {"chunk_mode": "normal"}

        dm = DirectQueryModule(retriever=mock_retriever, db=mock_db)
        # None
        result = dm.retrieve("test", ["col_a"], top_k=5, embedding_overrides=None)
        assert len(result.chunks) == 1
        # Empty dict
        result = dm.retrieve("test", ["col_a"], top_k=5, embedding_overrides={})
        assert len(result.chunks) == 1

    def test_min_score_filtering(self):
        """min_score=0.5：断言 score<0.5 的 chunk 被过滤"""
        from src.rag.direct_query import DirectQueryModule

        mock_retriever = MagicMock()
        # Retriever itself will filter by min_score; we simulate it returning
        # only chunks >= 0.5
        mock_retriever.retrieve.return_value = [
            _make_chunk("good", 0.90),
            _make_chunk("also good", 0.55),
        ]
        mock_db = MagicMock()
        mock_db.get_collection_config.return_value = {"chunk_mode": "normal"}

        dm = DirectQueryModule(retriever=mock_retriever, db=mock_db)
        result = dm.retrieve("test", ["col_a"], top_k=5, min_score=0.5)

        # All returned chunks should have score >= 0.5
        for c in result.chunks:
            assert c.score >= 0.5

    def test_hybrid_search_mode(self):
        """search_mode="hybrid"：断言 retriever 被调时 search_mode="hybrid" """
        from src.rag.direct_query import DirectQueryModule

        mock_retriever = MagicMock()
        mock_retriever.retrieve.return_value = [
            _make_chunk("hybrid chunk", 0.80),
        ]
        mock_db = MagicMock()
        mock_db.get_collection_config.return_value = {"chunk_mode": "normal"}

        dm = DirectQueryModule(retriever=mock_retriever, db=mock_db)
        dm.retrieve("test", ["col_a"], top_k=5, search_mode="hybrid")

        call_kwargs = mock_retriever.retrieve.call_args
        assert call_kwargs[1]["search_mode"] == "hybrid"

    def test_sparse_llm_tokenize_passed(self):
        """sparse_llm_tokenize=True：断言 llm_for_sparse 被传入 retriever"""
        from src.rag.direct_query import DirectQueryModule

        mock_retriever = MagicMock()
        mock_retriever.retrieve.return_value = [
            _make_chunk("sparse chunk", 0.80),
        ]
        mock_db = MagicMock()
        mock_db.get_collection_config.return_value = {"chunk_mode": "normal"}
        mock_llm = MagicMock()

        dm = DirectQueryModule(retriever=mock_retriever, db=mock_db)
        dm.retrieve("test", ["col_a"], top_k=5, sparse_llm_tokenize=True, llm_for_sparse=mock_llm)

        call_kwargs = mock_retriever.retrieve.call_args
        assert call_kwargs[1]["llm"] is mock_llm

    def test_parent_child_empty_results(self):
        """parent_child 检索返回 0 个 child：断言不崩，返回空结果"""
        from src.rag.direct_query import DirectQueryModule, DirectQueryResult

        mock_retriever = MagicMock()
        mock_db = MagicMock()
        mock_db.get_collection_config.return_value = {"chunk_mode": "parent_child"}

        dm = DirectQueryModule(retriever=mock_retriever, db=mock_db)
        with patch.object(dm, "_retrieve_parent_child", return_value=([], {})):
            result = dm.retrieve("test", ["col_a"], top_k=5)

        assert isinstance(result, DirectQueryResult)
        assert result.chunks == []
        assert result.child_groups == {}


# ── TestDirectQueryErrorHandling ─────────────────────────────────────────

class TestDirectQueryErrorHandling:
    """错误处理"""

    def test_retriever_raises_on_one_collection(self):
        """col1 检索成功，col2 抛异常：断言 col1 结果正常返回，col2 被跳过（不整个崩溃）"""
        from src.rag.direct_query import DirectQueryModule

        mock_retriever = MagicMock()
        mock_retriever.retrieve.side_effect = [
            [_make_chunk("col1 chunk", 0.90)],
            RuntimeError("col2 failed"),
        ]
        mock_db = MagicMock()
        mock_db.get_collection_config.return_value = {"chunk_mode": "normal"}

        dm = DirectQueryModule(retriever=mock_retriever, db=mock_db)
        result = dm.retrieve("test", ["col_ok", "col_bad"], top_k=10)

        assert len(result.chunks) == 1
        assert result.chunks[0].metadata["collection"] == "col_ok"

    def test_reranker_raises(self):
        """reranker.rerank 抛异常：断言不崩溃，降级为未重排序的结果"""
        from src.rag.direct_query import DirectQueryModule

        mock_retriever = MagicMock()
        mock_retriever.retrieve.return_value = [
            _make_chunk("text A", 0.90),
            _make_chunk("text B", 0.80),
        ]
        mock_db = MagicMock()
        mock_db.get_collection_config.return_value = {"chunk_mode": "normal"}
        mock_reranker = MagicMock()
        mock_reranker.rerank.side_effect = RuntimeError("rerank failed")

        dm = DirectQueryModule(retriever=mock_retriever, db=mock_db, reranker=mock_reranker)
        result = dm.retrieve("test", ["col_a"], top_k=10, rerank_top_k=5)

        # Should not crash, returns un-reranked results
        assert len(result.chunks) == 2

    def test_db_config_read_fails(self):
        """db.get_collection_config 抛异常：断言有合理降级（默认 normal 模式）"""
        from src.rag.direct_query import DirectQueryModule

        mock_retriever = MagicMock()
        mock_retriever.retrieve.return_value = [
            _make_chunk("text", 0.90),
        ]
        mock_db = MagicMock()
        mock_db.get_collection_config.side_effect = RuntimeError("db down")

        dm = DirectQueryModule(retriever=mock_retriever, db=mock_db)
        result = dm.retrieve("test", ["col_a"], top_k=5)

        # Should default to normal mode and succeed
        assert len(result.chunks) == 1
