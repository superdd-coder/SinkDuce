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


# ── 事件循环卫生（2026-09-18 补）────────────────────────────────
#
# **问题**：本仓没有 pytest-asyncio，异步测试普遍用 ``asyncio.run()``。它会
# **关闭并把当前事件循环置空**（``set_event_loop(None)``）。于是**下一个**测试若用
# 老式 ``asyncio.get_event_loop()``（本仓仍有多处），就会
# ``RuntimeError: There is no current event loop in thread 'MainThread'``。
#
# 这是**顺序相关的假失败**：单独跑绿、挨在 ``asyncio.run`` 之后跑就红。
# 实测它在新增测试文件后暴露出来（那些文件自己全绿，却把后面那个拖红）。
#
# **修在这里而不是逐个测试改**：37 个测试文件都在用 ``asyncio.run``，
# 一处兜底 vs 几十处各修一遍。留下的循环是干净的、未运行的，不影响任何既有行为。


@pytest.fixture(autouse=True)
def _keep_an_event_loop_available():
    """每个测试结束后确保存在一个可用的（未运行的）事件循环。

    只在**缺失时**补一个：不动任何测试自己设置的循环，行为对既有用例零影响。
    """
    yield
    import asyncio

    try:
        asyncio.get_event_loop_policy().get_event_loop()
    except (RuntimeError, DeprecationWarning):
        asyncio.set_event_loop(asyncio.new_event_loop())


@pytest.fixture(scope="session")
def api_base():
    return _api_base()


@pytest.fixture(scope="session")
def api_port():
    return api_port()
