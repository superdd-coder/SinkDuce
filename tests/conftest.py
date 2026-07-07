"""Shared pytest fixtures.  All ports read from .env via e2e_config."""

import os
import pytest
from tests.e2e_config import api_port, api_base as _api_base

# Integration tests that need a running server — not collected during unit test runs
collect_ignore = ["test_api.py", "test_smoke.py", "test_ui_automation.py", "test_e2e.py"]


def pytest_configure(config):
    """In CI, mock Qdrant client before any test module is imported,
    so import-time connection attempts don't fail."""
    if not os.environ.get("CI"):
        return

    from unittest.mock import MagicMock

    mock_qdrant = MagicMock()
    mock_qdrant.search.return_value = []
    mock_qdrant.scroll.return_value = ([], None)
    mock_qdrant.get_collections.return_value = MagicMock(collections=[])
    mock_qdrant.create_collection.return_value = True
    mock_qdrant.collection_exists.return_value = False
    mock_qdrant.get_collection.return_value = MagicMock()
    mock_qdrant.count.return_value = MagicMock(count=0)

    MockQdrantClient = MagicMock(return_value=mock_qdrant)

    try:
        import qdrant_client
        qdrant_client.QdrantClient = MockQdrantClient
    except ImportError:
        pass

    try:
        import src.db.qdrant as db_qdrant
        db_qdrant.QdrantClient = MockQdrantClient
    except ImportError:
        pass


def pytest_collection_modifyitems(config, items):
    """In CI, skip tests that need a running API server."""
    if not os.environ.get("CI"):
        return

    skip = pytest.mark.skip(reason="CI: no external services available")

    # Files that are entirely integration tests (need real API server + Qdrant)
    ci_skip_files = {
        "test_collection_id.py",
        "test_direct_query.py",
    }

    for item in items:
        fname = os.path.basename(item.fspath)
        if fname in ci_skip_files:
            item.add_marker(skip)
        elif "api_base" in getattr(item, "fixturenames", ()):
            item.add_marker(skip)


@pytest.fixture(scope="session")
def api_base():
    return _api_base()


@pytest.fixture(scope="session")
def api_port():
    return api_port()
