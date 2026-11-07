"""Authenticated global keyword search; per-category visibility lives in the service."""
from typing import Literal
from fastapi import APIRouter, Query
from src.search.workspace import search_workspace

router = APIRouter()


@router.get("/search/workspace")
def workspace_search(
    q: str = Query(default="", max_length=200),
    category: Literal["all", "projects", "files", "meetings", "minutes", "notes"] = "all",
    limit: int = Query(default=8, ge=1, le=50),
    offset: int = Query(default=0, ge=0, le=100000),
):
    # A sync endpoint uses FastAPI's context-preserving worker pool.
    return search_workspace(q, category, limit, offset)
