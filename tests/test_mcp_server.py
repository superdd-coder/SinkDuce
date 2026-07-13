"""Tests for the MCP server registration and HTTP sub-app.

Smoke tests verifying:
- All 40 atomic tools registered with FastMCP
- HTTP sub-app can be retrieved (for FastAPI mount under /mcp)
- Each tool module imports cleanly and exposes async tools
- Tool signatures match the documented contract
- Every tool can be invoked with mocked services and returns JSON
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest


# ── Tool registration ──────────────────────────────────────────


class TestToolRegistration:
    """All 40 atomic tools must be registered with the FastMCP server."""

    EXPECTED_TOOLS = {
        # Collections (5)
        "list_collections",
        "get_collection",
        "create_collection",
        "update_collection_config",
        "delete_collection",
        # Documents (7)
        "list_documents",
        "upload_document",
        "upload_document_content",
        "delete_document",
        "get_file_chunks",
        "get_document_text",
        "set_document_definitive",
        # Search (3)
        "search_direct_chunks",
        "search_agentic_chunks",
        "get_query_history",
        # Tasks (5)
        "get_task_status",
        "list_tasks",
        "cancel_task",
        "retry_task",
        "clear_completed_tasks",
        # Summaries (4)
        "get_collection_summary",
        "get_doc_summary",
        "get_conflicts",
        "trigger_consolidate",
        # Notes (6)
        "list_notes",
        "get_note",
        "create_note",
        "update_note",
        "delete_note",
        "trigger_propagation",
        # Meetings (6)
        "list_meetings",
        "get_meeting",
        "create_meeting",
        "update_meeting",
        "delete_meeting",
        "start_meeting_summary",
        # Hot Words (5)
        "list_hot_words_libraries",
        "get_hot_words_library",
        "create_hot_words_library",
        "update_hot_words_library",
        "delete_hot_words_library",
    }

    def test_all_40_tools_registered(self):
        from src.mcp.server import mcp
        tools = mcp._tool_manager._tools
        assert len(tools) == 41, (
            f"Expected 41 tools, got {len(tools)}: {sorted(tools.keys())}"
        )

    def test_tool_set_matches_expected(self):
        from src.mcp.server import mcp
        tools = mcp._tool_manager._tools
        actual = set(tools.keys())
        missing = self.EXPECTED_TOOLS - actual
        extra = actual - self.EXPECTED_TOOLS
        assert not missing, f"Missing tools: {missing}"
        assert not extra, f"Unexpected tools: {extra}"

    def test_no_legacy_tools_registered(self):
        """Tools removed in the restructure must NOT be registered."""
        from src.mcp.server import mcp
        tools = mcp._tool_manager._tools
        legacy = {
            "upload_folder",
            "get_collection_config",  # merged into get_collection
            "get_project_description",  # merged into list_collections/get_collection
            "search_knowledge_base",  # split into direct + agentic
            "search_chunks",  # split into direct + agentic
            "rag_query",  # removed entirely
        }
        for name in legacy:
            assert name not in tools, f"Legacy tool {name!r} should not be registered"

    def test_every_tool_is_async_callable(self):
        from src.mcp.server import mcp
        tools = mcp._tool_manager._tools
        for name, entry in tools.items():
            fn = entry.fn if hasattr(entry, "fn") else entry
            assert asyncio.iscoroutinefunction(fn), (
                f"Tool {name!r} must be an async function"
            )


# ── HTTP sub-app ───────────────────────────────────────────────


class TestHttpSubApp:
    def test_get_http_app_returns_asgi_app(self):
        from src.mcp.server import get_http_app
        app = get_http_app()
        # FastMCP streamable_http_app() returns a Starlette ASGI app
        assert app is not None
        assert callable(app)

    def test_get_http_app_idempotent(self):
        from src.mcp.server import get_http_app
        a = get_http_app()
        b = get_http_app()
        # Either returns the same instance or a fresh one — both must be usable
        assert a is not None and b is not None


# ── Module imports ─────────────────────────────────────────────


class TestModuleImports:
    """Each tool module must be importable and expose its tools."""

    def test_collections_imports(self):
        from src.mcp.tools.collections import (
            list_collections, get_collection, create_collection,
            update_collection_config, delete_collection,
        )
        for fn in (list_collections, get_collection, create_collection,
                   update_collection_config, delete_collection):
            assert asyncio.iscoroutinefunction(fn)

    def test_documents_imports(self):
        from src.mcp.tools.documents import (
            list_documents, upload_document, upload_document_content,
            delete_document, get_file_chunks, get_document_text,
        )
        for fn in (list_documents, upload_document, upload_document_content,
                   delete_document, get_file_chunks, get_document_text):
            assert asyncio.iscoroutinefunction(fn)

    def test_search_imports(self):
        from src.mcp.tools.search import (
            search_direct_chunks, search_agentic_chunks, get_query_history,
        )
        for fn in (search_direct_chunks, search_agentic_chunks, get_query_history):
            assert asyncio.iscoroutinefunction(fn)

    def test_tasks_imports(self):
        from src.mcp.tools.tasks import (
            get_task_status, list_tasks, cancel_task, retry_task,
            clear_completed_tasks,
        )
        for fn in (get_task_status, list_tasks, cancel_task, retry_task,
                   clear_completed_tasks):
            assert asyncio.iscoroutinefunction(fn)

    def test_summaries_imports(self):
        from src.mcp.tools.summaries import (
            get_collection_summary, get_doc_summary, get_conflicts,
            trigger_consolidate,
        )
        for fn in (get_collection_summary, get_doc_summary, get_conflicts,
                   trigger_consolidate):
            assert asyncio.iscoroutinefunction(fn)

    def test_notes_imports(self):
        from src.mcp.tools.notes import (
            list_notes, get_note, create_note, update_note,
            delete_note, trigger_propagation,
        )
        for fn in (list_notes, get_note, create_note, update_note,
                   delete_note, trigger_propagation):
            assert asyncio.iscoroutinefunction(fn)

    def test_meetings_imports(self):
        from src.mcp.tools.meetings import (
            list_meetings, get_meeting, create_meeting, update_meeting,
            delete_meeting, start_meeting_summary,
        )
        for fn in (list_meetings, get_meeting, create_meeting, update_meeting,
                   delete_meeting, start_meeting_summary):
            assert asyncio.iscoroutinefunction(fn)

    def test_hot_words_imports(self):
        from src.mcp.tools.hot_words import (
            list_hot_words_libraries, get_hot_words_library,
            create_hot_words_library, update_hot_words_library,
            delete_hot_words_library,
        )
        for fn in (list_hot_words_libraries, get_hot_words_library,
                   create_hot_words_library, update_hot_words_library,
                   delete_hot_words_library):
            assert asyncio.iscoroutinefunction(fn)


# ── Tool signatures (param introspection) ──────────────────────


class TestToolSignatures:
    """Spot-check signatures for tools with security-sensitive params."""

    def test_update_collection_config_does_not_accept_chunk_mode(self):
        """Destructive field guard: chunk_mode must not be a parameter."""
        from src.mcp.tools.collections import update_collection_config
        import inspect
        sig = inspect.signature(update_collection_config)
        assert "chunk_mode" not in sig.parameters
        assert "embedding_provider" not in sig.parameters
        assert "embedding_model" not in sig.parameters

    def test_update_collection_config_accepts_safe_fields(self):
        from src.mcp.tools.collections import update_collection_config
        import inspect
        sig = inspect.signature(update_collection_config)
        assert "chunk_size" in sig.parameters
        assert "search_mode" in sig.parameters
        assert "agent_enabled" in sig.parameters

    def test_create_collection_accepts_destructive_fields(self):
        """Creation is allowed to set embedding params — destruction only matters on update."""
        from src.mcp.tools.collections import create_collection
        import inspect
        sig = inspect.signature(create_collection)
        assert "chunk_mode" in sig.parameters
        assert "embedding_model" in sig.parameters

    def test_search_tools_have_collection_param(self):
        from src.mcp.tools.search import search_direct_chunks
        import inspect
        sig = inspect.signature(search_direct_chunks)
        # Either  or  must be present (not both required)
        assert "collection" in sig.parameters or "collections" in sig.parameters

    def test_delete_collection_has_collection_param(self):
        from src.mcp.tools.collections import delete_collection
        import inspect
        sig = inspect.signature(delete_collection)
        assert "collection" in sig.parameters


# ── Smoke: every tool returns JSON-serializable output ─────────


# Per-tool placeholder kwargs for required parameters that cannot use the
# default mock values.  All other tools accept placeholders from
# :data:`_PLACEHOLDER_KWARGS` below.
_TOOL_KWARGS_OVERRIDES = {
    "create_note": {"title": "test"},
    "update_note": {"note_id": "test-id", "content": "test"},
    "create_meeting": {"title": "test"},
    "update_meeting": {"meeting_id": "test-id"},
    "upload_document_content": {"filename": "test.txt", "content_b64": "aGVsbG8="},
    "upload_document": {"file_path": "/tmp/test.txt"},
    "create_hot_words_library": {"name": "test"},
    "update_hot_words_library": {"library_id": "test-id"},
}

_PLACEHOLDER_KWARGS = {
    "task_id": "test-id",
    "meeting_id": "test-id",
    "note_id": "test-id",
    "library_id": "test-id",
    "collection": "default",
    "source": "test.md",
    "filename": "test.txt",
    "content_b64": "aGVsbG8=",
    "file_path": "/tmp/test.txt",
    "name": "test",
    "query": "test",
    "title": "test",
}


def _build_kwargs(fn):
    """Build a kwargs dict from ``fn``'s required parameters using placeholder values."""
    import inspect
    sig = inspect.signature(fn)
    kwargs = {}
    for pname, param in sig.parameters.items():
        if param.default is inspect.Parameter.empty:
            if pname in _PLACEHOLDER_KWARGS:
                kwargs[pname] = _PLACEHOLDER_KWARGS[pname]
            else:
                pytest.skip(f"Cannot parameterize required arg {pname!r}")
                return None
    return kwargs


def _build_mock_services():
    """Build a fully-mocked services singleton.

    Patches:
    - services.db.* — collection CRUD
    - services.embedding.* — embedding provider
    - services.direct_query — direct retrieval
    - services.agentic_query — agentic pipeline
    - src.collections.store — collection metadata
    - src.notes.store — notes store
    - src.rag.summary_manager.SummaryManager — summary manager class
    - src.rag.collection_utils.get_embedding_overrides — embedding overrides
    """
    mock_svc = MagicMock()
    mock_svc.db = MagicMock()
    mock_svc.db.collection_exists.return_value = False
    mock_svc.db.list_collections.return_value = []
    mock_svc.db.get_collection_info.return_value = {"points_count": 0}
    mock_svc.db.get_collection_config.return_value = {"embedding": {"default": {}}}
    mock_svc.db.update_collection_config.return_value = {}
    mock_svc.db.delete_collection.return_value = None
    mock_svc.db.delete_by_filter.return_value = 0
    mock_svc.embedding = MagicMock()
    mock_svc.embedding.dimensions = 1024
    mock_svc.direct_query = MagicMock()
    mock_svc.direct_query.retrieve.return_value = MagicMock(chunks=[])
    mock_svc.agentic_query = MagicMock()
    mock_svc.agentic_query.run.return_value = MagicMock(all_chunks=[], tasks=[], images={})
    return mock_svc


def _patch_context():
    """Return a context manager that patches all downstream dependencies."""
    from contextlib import ExitStack
    from src.collections import store as cstore
    from src.notes import store as nstore
    from src.rag import summary_manager as sm_mod
    from src.rag import collection_utils as cu_mod

    mock_svc = _build_mock_services()
    mock_cstore = MagicMock()
    mock_cstore.get_collection_meta.return_value = {
        "id": "default", "name": "default", "qdrant_name": "default",
    }
    mock_cstore.generate_id.return_value = "new-col-id"
    mock_cstore.create_collection_meta.return_value = None
    mock_cstore.delete_collection_meta.return_value = None

    mock_nstore = MagicMock()
    mock_nstore.list_notes.return_value = []
    mock_nstore.get_note.return_value = None
    mock_nstore.create_note.return_value = MagicMock(id="n1")
    mock_nstore.update_note.return_value = MagicMock(id="n1")
    mock_nstore.delete_note.return_value = None
    mock_nstore.list_notes_by_collection.return_value = []
    mock_nstore.propagate_note.return_value = None

    mock_sm = MagicMock()
    mock_sm.get_collection_summary.return_value = None
    mock_sm.get_doc_summary.return_value = None
    mock_sm.get_conflicts.return_value = []
    mock_sm.get_project_description.return_value = ""

    mock_get_overrides = MagicMock(return_value={})

    stack = ExitStack()
    stack.enter_context(patch("src.services.services", mock_svc, create=True))
    stack.enter_context(patch.object(cstore, "get_collection_meta", mock_cstore.get_collection_meta))
    stack.enter_context(patch.object(cstore, "generate_id", mock_cstore.generate_id))
    stack.enter_context(patch.object(cstore, "create_collection_meta", mock_cstore.create_collection_meta))
    stack.enter_context(patch.object(cstore, "delete_collection_meta", mock_cstore.delete_collection_meta))
    stack.enter_context(patch.object(nstore, "list_notes", mock_nstore.list_notes))
    stack.enter_context(patch.object(nstore, "get_note", mock_nstore.get_note))
    stack.enter_context(patch.object(nstore, "create_note", mock_nstore.create_note))
    stack.enter_context(patch.object(nstore, "update_note", mock_nstore.update_note))
    stack.enter_context(patch.object(nstore, "delete_note", mock_nstore.delete_note))
    stack.enter_context(patch.object(sm_mod, "SummaryManager", return_value=mock_sm))
    stack.enter_context(patch.object(cu_mod, "get_embedding_overrides", mock_get_overrides))
    stack.enter_context(patch("src.tasks.task_manager.create_task", return_value=MagicMock(id="t1")))
    stack.enter_context(patch("src.tasks.task_manager.get_task", return_value=None))
    stack.enter_context(patch("src.tasks.task_manager.get_all_tasks", return_value=[]))
    stack.enter_context(patch("src.tasks.task_manager.cancel_task", return_value=True))
    stack.enter_context(patch("src.tasks.task_manager.retry_task", return_value=None))
    stack.enter_context(patch("src.tasks.task_manager.clear_completed_tasks", return_value=None))
    stack.enter_context(patch("src.tasks.task_manager._task_args", {}))
    return stack


class TestToolsReturnJson:
    """Smoke test: each tool can be invoked with mocked services and returns JSON."""

    @pytest.mark.parametrize("tool_name", sorted(TestToolRegistration.EXPECTED_TOOLS))
    def test_tool_returns_valid_json(self, tool_name):
        from src.mcp.server import mcp
        tools = mcp._tool_manager._tools
        entry = tools[tool_name]
        fn = entry.fn if hasattr(entry, "fn") else entry

        kwargs = dict(_TOOL_KWARGS_OVERRIDES.get(tool_name, {}))
        placeholder_kwargs = _build_kwargs(fn)
        if placeholder_kwargs is None:
            return  # skipped
        for k, v in placeholder_kwargs.items():
            kwargs.setdefault(k, v)

        with _patch_context():
            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(fn(**kwargs))
            finally:
                loop.close()

        # The result must be a JSON string parseable as dict OR list
        data = json.loads(result)
        assert isinstance(data, (dict, list)), (
            f"Tool {tool_name!r} should return a JSON object or array, "
            f"got {type(data).__name__}"
        )
