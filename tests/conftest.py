"""Shared pytest fixtures.  All ports read from .env via e2e_config."""

import os
import pytest
from tests.e2e_config import api_port, api_base as _api_base

# Integration tests that need a running server — not collected during unit test runs
collect_ignore = ["test_api.py", "test_smoke.py", "test_ui_automation.py", "test_e2e.py"]


def pytest_collection_modifyitems(config, items):
    """In CI, auto-skip tests that need external services (API server, Qdrant)."""
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
