"""Regression coverage for indexed file chunks shared by Agent REST and MCP."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.mark.parametrize("entrypoint", ["view", "mcp", "rest"])
def test_file_chunks_preserve_point_ids_with_original_passages(monkeypatch, entrypoint):
    from src import documents_views, services as services_module
    from src.file_mgmt import files

    source = "__file__:file-example"
    points = [
        {
            "id": "point-later",
            "payload": {
                "text": "Later original passage.",
                "source": source,
                "chunk_type": "normal",
                "chunk_index": 1,
                "char_offset": 100,
                "context": "Later passage context.",
            },
        },
        {
            "id": "point-earlier",
            "payload": {
                "text": "Earlier original passage.",
                "source": source,
                "chunk_type": "normal",
                "chunk_index": 0,
                "char_offset": 0,
                "context": "Earlier passage context.",
            },
        },
    ]
    db = SimpleNamespace(scroll_points=Mock(return_value=(points, None)))
    monkeypatch.setattr(services_module, "services", SimpleNamespace(db=db))
    monkeypatch.setattr(documents_views, "require_collection", lambda collection: None)
    monkeypatch.setattr(files, "archived_or_unassigned_file_ids", lambda *args: set())

    if entrypoint == "view":
        result = documents_views.file_chunks_view("col-example", file_id="file-example")
    elif entrypoint == "mcp":
        from src.mcp.tools.documents import get_file_chunks

        response = asyncio.run(get_file_chunks("col-example", file_id="file-example"))
        result = response.structuredContent
    else:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from src.api.routes.agent import agent_router

        app = FastAPI()
        app.include_router(agent_router, prefix="/api")
        with TestClient(app) as client:
            response = client.get(
                "/api/agent/collections/col-example/chunks",
                params={"file_id": "file-example"},
            )
        assert response.status_code == 200
        result = response.json()

    assert result["file_id"] == "file-example"
    assert result["source"] == source
    assert result["total"] == 2
    assert result["chunks"] == [
        {
            "id": "point-earlier",
            "text": "Earlier original passage.",
            "chunk_type": "normal",
            "chunk_index": 0,
            "char_offset": 0,
            "source": source,
            "parent_id": None,
            "context": "Earlier passage context.",
        },
        {
            "id": "point-later",
            "text": "Later original passage.",
            "chunk_type": "normal",
            "chunk_index": 1,
            "char_offset": 100,
            "source": source,
            "parent_id": None,
            "context": "Later passage context.",
        },
    ]
