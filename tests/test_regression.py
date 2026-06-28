"""Step 8: Regression tests — verify removed/moved/kept/added artifacts."""

import os
import pytest


# ── TestAgentNodesRemovedFunctions ────────────────────────────────────────

class TestAgentNodesRemovedFunctions:
    """确认删除的函数不可 import"""

    def test_node_generate_removed(self):
        with pytest.raises(ImportError):
            from src.rag.agent_nodes import node_generate

    def test_node_generate_stream_removed(self):
        with pytest.raises(ImportError):
            from src.rag.agent_nodes import node_generate_stream

    def test_node_retrieve_and_rerank_removed(self):
        with pytest.raises(ImportError):
            from src.rag.agent_nodes import node_retrieve_and_rerank

    def test_retrieve_across_collections_removed(self):
        with pytest.raises(ImportError):
            from src.rag.agent_nodes import _retrieve_across_collections

    def test_merge_and_rerank_removed(self):
        with pytest.raises(ImportError):
            from src.rag.agent_nodes import _merge_and_rerank

    def test_node_parallel_sub_queries_removed(self):
        with pytest.raises(ImportError):
            from src.rag.agent_nodes import node_parallel_sub_queries


# ── TestAgentNodesKeptFunctions ───────────────────────────────────────────

class TestAgentNodesKeptFunctions:
    """确认保留的函数仍可 import"""

    def test_node_combined_grade_importable(self):
        from src.rag.agent_nodes import node_combined_grade

    def test_node_update_retained_info_removed(self):
        with pytest.raises(ImportError):
            from src.rag.agent_nodes import node_update_retained_info

    def test_node_llm_grade_removed(self):
        with pytest.raises(ImportError):
            from src.rag.agent_nodes import node_llm_grade

    def test_node_check_and_rewrite_importable(self):
        from src.rag.agent_nodes import node_check_and_rewrite

    def test_dedup_by_id_importable(self):
        from src.rag.agent_nodes import _dedup_by_id

    def test_parse_json_importable(self):
        from src.rag.agent_nodes import _parse_json

    def test_llm_generate_json_importable(self):
        from src.rag.agent_nodes import _llm_generate_json


# ── TestAgentFileRemoved ──────────────────────────────────────────────────

class TestAgentFileRemoved:
    """确认 agent.py 已删除"""

    def test_agent_py_not_exist(self):
        assert not os.path.exists("src/rag/agent.py")


# ── TestAgentPromptsCleaned ───────────────────────────────────────────────

class TestAgentPromptsCleaned:
    """确认 GENERATE prompt 已删除"""

    def test_generate_removed(self):
        with pytest.raises(ImportError):
            from src.rag.agent_prompts import GENERATE_SYSTEM

    def test_new_prompts_exist(self):
        from src.rag.agent_prompts import GRADE_COMBINED_SYSTEM, GRADE_COMBINED_USER, REWRITE_SYSTEM


# ── TestCollectionUtilsCleaned ────────────────────────────────────────────

class TestCollectionUtilsCleaned:
    """确认检索函数已从 collection_utils 移除"""

    def test_retrieve_standard_removed(self):
        with pytest.raises(ImportError):
            from src.rag.collection_utils import retrieve_standard

    def test_retrieve_parent_child_removed(self):
        """Migrated to direct_query.py"""
        with pytest.raises(ImportError):
            from src.rag.collection_utils import retrieve_parent_child

    def test_retrieve_parent_child_multi_removed(self):
        with pytest.raises(ImportError):
            from src.rag.collection_utils import retrieve_parent_child_multi

    def test_get_embedding_overrides_kept(self):
        from src.rag.collection_utils import get_embedding_overrides


# ── TestNewModulesImportable ──────────────────────────────────────────────

class TestNewModulesImportable:
    """所有新模块可 import"""

    def test_direct_query(self):
        from src.rag.direct_query import DirectQueryModule, DirectQueryResult

    def test_context_builder(self):
        from src.rag.context_builder import build_context

    def test_rewrite_loop(self):
        from src.rag.rewrite_loop import RewriteLoop, RewriteLoopResult

    def test_catalog(self):
        from src.rag.catalog import CollectionCatalog, CatalogEntry

    def test_decomposer(self):
        from src.rag.decomposer import Decomposer, AtomicQuery

    def test_aggregator(self):
        from src.rag.aggregator import Aggregator, SubQueryResult

    def test_agentic_query(self):
        from src.rag.agentic_query import AgenticQueryService, AgenticQueryResult


# ── TestConfigUpdated ─────────────────────────────────────────────────────

class TestConfigUpdated:
    """配置新增字段"""

    def test_function_call_model_ids_on_provider(self):
        from src.config import LLMProviderConfig
        assert 'function_call_model_ids' in LLMProviderConfig.model_fields

    def test_rag_config_present(self):
        from src.config import AppConfig
        assert 'rag' in AppConfig.model_fields
        assert 'enrichment' in AppConfig.model_fields
