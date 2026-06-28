"""Tests for Phase 4 cleanup — verify deprecated code is removed.

Run: pytest tests/test_cleanup.py -v --tb=short
"""

from __future__ import annotations

import pytest


class TestCollectionConfigClean:
    def test_agent_enabled_removed_from_defaults(self):
        """DEFAULT_COLLECTION_CONFIG no longer contains agent_enabled."""
        from src.db.qdrant import get_default_collection_config
        defaults = get_default_collection_config()
        assert "agent_enabled" not in defaults
        assert "agent_max_iterations" not in defaults

    def test_self_rag_removed(self):
        """self_rag_enabled and self_rag_max_iterations are not present."""
        from src.db.qdrant import get_default_collection_config
        defaults = get_default_collection_config()
        assert "self_rag_enabled" not in defaults
        assert "self_rag_max_iterations" not in defaults


class TestDeletedFunctions:
    def test_agent_py_not_exist(self):
        """src/rag/agent.py is deleted."""
        from pathlib import Path
        agent_path = Path(__file__).parent.parent / "src" / "rag" / "agent.py"
        assert not agent_path.exists(), f"{agent_path} should not exist"

    def test_generate_removed(self):
        """GENERATE_* prompts should not be importable from agent_prompts."""
        try:
            from src.rag.agent_prompts import GENERATE_SYSTEM
            has_generate_system = True
        except ImportError:
            has_generate_system = False
        assert not has_generate_system, "GENERATE_SYSTEM should not be importable"

        try:
            from src.rag.agent_prompts import GENERATE_USER
            has_generate_user = True
        except ImportError:
            has_generate_user = False
        assert not has_generate_user, "GENERATE_USER should not be importable"

    def test_retrieve_standard_removed(self):
        """retrieve_standard should not be importable from collection_utils."""
        try:
            from src.rag.collection_utils import retrieve_standard
            has_retrieve_standard = True
        except ImportError:
            has_retrieve_standard = False
        assert not has_retrieve_standard, "retrieve_standard should not be importable"
