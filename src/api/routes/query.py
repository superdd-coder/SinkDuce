"""Legacy query endpoints.

POST /query and POST /query/stream are gone (410). Use
POST /api/sessions/{id}/messages. GET /history still reads
data/history/history.jsonl for MCP / leftover clients.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from src.api.schemas import QueryRequest

router = APIRouter()

HISTORY_DIR = Path("data/history")
HISTORY_DIR.mkdir(parents=True, exist_ok=True)


def _deprecated_response():
    return JSONResponse(
        status_code=410,
        content={
            "error": "Gone",
            "message": "This endpoint is deprecated. Please migrate to POST /api/sessions/{id}/messages",
            "migration_doc": "https://github.com/superdd-coder/sinkduce",
        },
        headers={"Deprecation": "true", "Sunset": "Sat, 01 Aug 2026 00:00:00 GMT"},
    )


@router.post("/query", include_in_schema=False)
def query(req: QueryRequest = None):
    """Deprecated. Use POST /api/sessions/{id}/messages instead."""
    return _deprecated_response()


@router.post("/query/stream", include_in_schema=False)
def query_stream(req: QueryRequest = None):
    """Deprecated. Use POST /api/sessions/{id}/messages instead."""
    return _deprecated_response()


@router.get("/history")
def get_history(limit: int = 50):
    file = HISTORY_DIR / "history.jsonl"
    if not file.exists():
        return []
    entries = []
    for line in file.read_text().strip().split("\n"):
        if line:
            entries.append(json.loads(line))
    return entries[-limit:]
