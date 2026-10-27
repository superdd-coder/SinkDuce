"""MCP search archive gate (src/mcp/tools/search.py + collections.py).

Named archived collections error with an include_archived hint; the switch
releases them. list_collections reports the archived flag.

TDD for the collection archive feature — spec:
development_doc/2026-09-13-collection-archive-design.md

Run this file ALONE (mcp tests pollute event loops when batched with
chatbox tests — see project memory).
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

import pytest

from src.collections import store as collections_store


@pytest.fixture(autouse=True)
def _archived(monkeypatch):
    monkeypatch.setattr(collections_store, "archived_collection_ids", lambda: {"col_arch"})


def _mock_services():
    svc = MagicMock()
    svc.db.collection_exists.return_value = True
    svc.db.list_collections.return_value = ["col_active", "col_arch"]
    svc.db.get_collection_config.return_value = {"project_description": "P"}
    svc.db.get_collection_info.return_value = {"points_count": 3}
    dq = MagicMock()
    dq.retrieve.return_value = MagicMock(chunks=[], context="")
    svc.direct_query = dq
    svc.agentic_query = MagicMock()
    return svc


class TestSearchDirectChunksArchiveGate:
    def test_named_archived_errors_with_hint(self, monkeypatch):
        from src.mcp.tools.search import search_direct_chunks
        from src.services import services

        svc = _mock_services()
        monkeypatch.setattr(services, "db", svc.db, raising=False)
        monkeypatch.setattr(services, "direct_query", svc.direct_query, raising=False)

        out = asyncio.run(search_direct_chunks(query="q", collection="col_arch"))
        payload = json.loads(out)
        assert "error" in payload
        assert "col_arch" in payload["error"]
        assert "is archived" in payload["error"]
        assert "include_archived" in payload["error"]
        svc.direct_query.retrieve.assert_not_called()

    def test_switch_releases_named_archived(self, monkeypatch):
        from src.mcp.tools.search import search_direct_chunks
        from src.services import services

        svc = _mock_services()
        monkeypatch.setattr(services, "db", svc.db, raising=False)
        monkeypatch.setattr(services, "direct_query", svc.direct_query, raising=False)

        out = asyncio.run(search_direct_chunks(
            query="q", collection="col_arch", include_archived=True,
        ))
        payload = json.loads(out)
        assert "error" not in payload
        svc.direct_query.retrieve.assert_called_once()
        assert svc.direct_query.retrieve.call_args.args[1] == ["col_arch"]

    def test_active_collection_untouched(self, monkeypatch):
        from src.mcp.tools.search import search_direct_chunks
        from src.services import services

        svc = _mock_services()
        monkeypatch.setattr(services, "db", svc.db, raising=False)
        monkeypatch.setattr(services, "direct_query", svc.direct_query, raising=False)

        out = asyncio.run(search_direct_chunks(query="q", collection="col_active"))
        payload = json.loads(out)
        assert "error" not in payload
        svc.direct_query.retrieve.assert_called_once()


class TestSearchAgenticChunksArchiveSwitch:
    def test_switch_forwarded_to_agentic_run(self, monkeypatch):
        from src.mcp.tools.search import search_agentic_chunks
        from src.services import services

        svc = _mock_services()
        monkeypatch.setattr(services, "agentic_query", svc.agentic_query, raising=False)
        svc.agentic_query.run.return_value = MagicMock(
            answer=None, context="", all_chunks=[], tasks=[], images=None,
        )

        asyncio.run(search_agentic_chunks(query="q", include_archived=True))
        kwargs = svc.agentic_query.run.call_args.kwargs
        assert kwargs.get("include_archived") is True

    def test_default_is_false(self, monkeypatch):
        from src.mcp.tools.search import search_agentic_chunks
        from src.services import services

        svc = _mock_services()
        monkeypatch.setattr(services, "agentic_query", svc.agentic_query, raising=False)
        svc.agentic_query.run.return_value = MagicMock(
            answer=None, context="", all_chunks=[], tasks=[], images=None,
        )

        asyncio.run(search_agentic_chunks(query="q"))
        kwargs = svc.agentic_query.run.call_args.kwargs
        assert kwargs.get("include_archived") is False


class TestListCollectionsArchivedFlag:
    def test_payload_marks_archived(self, monkeypatch):
        from src.mcp.tools.collections import list_collections
        from src.services import services
        from src.notes import store as notes_store

        svc = _mock_services()
        monkeypatch.setattr(services, "db", svc.db, raising=False)
        monkeypatch.setattr(notes_store, "list_notes", lambda: [])
        monkeypatch.setattr(
            collections_store, "list_collections_meta",
            lambda: [
                {"id": "col_active", "name": "Active"},
                {"id": "col_arch", "name": "Archived One", "archived": True},
            ],
        )
        # get_collection_meta reads the (real) data dir — point entries at mocks
        monkeypatch.setattr(
            collections_store, "get_collection_meta",
            lambda cid: {
                "col_active": {"id": "col_active", "name": "Active"},
                "col_arch": {"id": "col_arch", "name": "Archived One", "archived": True},
            }.get(cid),
        )

        out = asyncio.run(list_collections())
        payload = json.loads(out)
        by_id = {c["id"]: c for c in payload}
        assert by_id["col_active"]["archived"] is False
        assert by_id["col_arch"]["archived"] is True
