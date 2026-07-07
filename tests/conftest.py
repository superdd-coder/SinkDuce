"""Shared pytest fixtures.  All ports read from .env via e2e_config."""

import os
import pytest
from tests.e2e_config import api_port, api_base as _api_base

# Integration tests that need a running server — not collected during unit test runs
collect_ignore = ["test_api.py", "test_smoke.py", "test_ui_automation.py", "test_e2e.py"]


def pytest_configure(config):
    """In CI, mock Qdrant client before any test module is imported,
    so import-time connection attempts don't fail."""
    if os.environ.get("CI"):
        from unittest.mock import MagicMock

        # Mock the Qdrant client constructor so it never tries to connect
        mock_client = MagicMock()
        mock_client.search.return_value = []
        mock_client.scroll.return_value = ([], None)
        mock_client.get_collections.return_value = MagicMock(collections=[])
        mock_client.create_collection.return_value = True
        mock_client.collection_exists.return_value = False
        mock_client.get_collection.return_value = MagicMock()
        mock_client.count.return_value = MagicMock(count=0)

        MockQdrantClient = MagicMock(return_value=mock_client)

        try:
            import qdrant_client
            qdrant_client.QdrantClient = MockQdrantClient
        except ImportError:
            pass

        # Also patch the module-level reference in src.db.qdrant
        try:
            import src.db.qdrant as db_qdrant
            db_qdrant.QdrantClient = MockQdrantClient
        except ImportError:
            pass


def pytest_collection_modifyitems(config, items):
    """In CI, auto-skip tests that need the running API server."""
    if os.environ.get("CI"):
        skip_marker = pytest.mark.skip(reason="CI: no external services available")
        for item in items:
            if "api_base" in getattr(item, "fixturenames", ()):
                item.add_marker(skip_marker)


@pytest.fixture(scope="session")
def api_base():
    return _api_base()


@pytest.fixture(scope="session")
def api_port():
    return api_port()
