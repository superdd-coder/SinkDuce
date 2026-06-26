"""Session 2 Step 3: MCP tools migration tests."""

import json
import pytest
from unittest.mock import MagicMock, patch

from src.rag.retriever import RetrievedChunk
from src.rag.agentic_query import AgenticQueryResult


def _ck(text="chunk", cid="c1", score=0.9):
    return RetrievedChunk(text=text, score=score, metadata={"id": cid, "source": "doc.md", "collection": "test"})


class TestSearchKnowledgeBase:
    def test_returns_json_string(self):
        from src.mcp.tools.search import search_knowledge_base
        import asyncio

        mock_svc = MagicMock()
        mock_svc.direct_query = MagicMock()
        from src.services import services as real_services
        mock_svc.agentic_query = MagicMock()
        mock_svc.agentic_query.run.return_value = AgenticQueryResult(
            answer="test answer", all_chunks=[_ck()], tasks=[],
        )
        with patch("src.services.services", mock_svc):
            result = asyncio.new_event_loop().run_until_complete(
                search_knowledge_base("test query")
            )
        data = json.loads(result)
        assert isinstance(data, dict)
        assert "answer" in data

    def test_answer_mode(self):
        from src.mcp.tools.search import search_knowledge_base
        import asyncio

        mock_svc = MagicMock()
        mock_svc.agentic_query = MagicMock()
        mock_svc.agentic_query.run.return_value = AgenticQueryResult(
            answer="Detailed answer with facts", all_chunks=[_ck()], tasks=[],
        )
        with patch("src.services.services", mock_svc):
            result = asyncio.new_event_loop().run_until_complete(
                search_knowledge_base("test", generate_answer=True)
            )
        data = json.loads(result)
        assert data["answer"] is not None
        assert len(data["answer"]) > 0

    def test_chunks_only_mode(self):
        from src.mcp.tools.search import search_knowledge_base
        import asyncio

        mock_svc = MagicMock()
        mock_svc.agentic_query = MagicMock()
        mock_svc.agentic_query.run.return_value = AgenticQueryResult(
            answer=None, all_chunks=[_ck("data", "c1"), _ck("data2", "c2")], tasks=[],
        )
        with patch("src.services.services", mock_svc):
            result = asyncio.new_event_loop().run_until_complete(
                search_knowledge_base("test", generate_answer=False)
            )
        data = json.loads(result)
        assert data["answer"] is None
        assert len(data["sources"]) == 2

    def test_no_collection_param(self):
        from src.mcp.tools.search import search_knowledge_base
        import inspect
        sig = inspect.signature(search_knowledge_base)
        assert "collection" not in sig.parameters


class TestSearchChunks:
    def test_uses_direct_module(self):
        from src.mcp.tools.search import search_chunks
        import asyncio

        mock_svc = MagicMock()
        mock_dq = MagicMock()
        mock_dq.retrieve.return_value = MagicMock(chunks=[_ck()], child_groups={})
        mock_svc.direct_query = mock_dq
        mock_svc.agentic_query = MagicMock()

        with patch("src.services.services", mock_svc):
            with patch("src.rag.collection_utils.get_embedding_overrides", return_value={}):
                result = asyncio.new_event_loop().run_until_complete(
                    search_chunks("test", collection="my_col")
                )
        mock_dq.retrieve.assert_called_once()
        data = json.loads(result)
        assert "results" in data

    def test_collection_param_required(self):
        from src.mcp.tools.search import search_chunks
        import inspect
        sig = inspect.signature(search_chunks)
        assert "collection" in sig.parameters


class TestMCPToolRegistration:
    def test_search_knowledge_base_registered(self):
        """确认 mcp server 注册了 search_knowledge_base tool"""
        # Check that the function exists and is importable
        from src.mcp.tools.search import search_knowledge_base
        assert callable(search_knowledge_base)

    def test_rag_query_removed(self):
        """确认 rag_query 不再注册（不可 import）"""
        with pytest.raises(ImportError):
            from src.mcp.tools.search import rag_query
