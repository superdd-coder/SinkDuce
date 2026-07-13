"""MCP server — registers all 40 atomic tools and exposes the HTTP sub-app.

Tool inventory (40 total, by domain):

Collections (5):
    list_collections, get_collection, create_collection,
    update_collection_config, delete_collection

Documents (7):
    list_documents, upload_document, upload_document_content,
    delete_document, get_file_chunks, get_document_text,
    set_document_definitive

Search (3):
    search_direct_chunks, search_agentic_chunks, get_query_history

Tasks (5):
    get_task_status, list_tasks, cancel_task, retry_task, clear_completed_tasks

Summaries (4):
    get_collection_summary, get_doc_summary, get_conflicts, trigger_consolidate

Notes (6):
    list_notes, get_note, create_note, update_note, delete_note, trigger_propagation

Meetings (6):
    list_meetings, get_meeting, create_meeting, update_meeting,
    delete_meeting, start_meeting_summary

Hot Words (5):
    list_hot_words_libraries, get_hot_words_library, create_hot_words_library,
    update_hot_words_library, delete_hot_words_library

Total: 5 + 7 + 3 + 5 + 4 + 6 + 6 + 5 = 41 tools.

Architecture
------------
The :class:`FastMCP` instance is created at import time. Tool registration
happens via plain imports + :meth:`FastMCP.add_tool` calls in this file,
so the tool list is discoverable in one place.

The sub-app is mounted by ``src.main`` under ``/mcp`` and shares the main
app's lifespan (services, task_manager) — no MCP-specific lifespan is
required.
"""

from __future__ import annotations

import logging

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

mcp = FastMCP("sinkduce")

# ── Collections ──────────────────────────────────────────────
from src.mcp.tools.collections import (
    list_collections,
    get_collection,
    create_collection,
    update_collection_config,
    delete_collection,
)
for _t in (list_collections, get_collection, create_collection, update_collection_config, delete_collection):
    mcp.add_tool(_t)

# ── Documents ────────────────────────────────────────────────
from src.mcp.tools.documents import (
    list_documents,
    upload_document,
    upload_document_content,
    delete_document,
    get_file_chunks,
    get_document_text,
    set_document_definitive,
)
for _t in (list_documents, upload_document, upload_document_content, delete_document, get_file_chunks, get_document_text, set_document_definitive):
    mcp.add_tool(_t)

# ── Search & Query ───────────────────────────────────────────
from src.mcp.tools.search import (
    search_direct_chunks,
    search_agentic_chunks,
    get_query_history,
)
for _t in (search_direct_chunks, search_agentic_chunks, get_query_history):
    mcp.add_tool(_t)

# ── Tasks ────────────────────────────────────────────────────
from src.mcp.tools.tasks import (
    get_task_status,
    list_tasks,
    cancel_task,
    retry_task,
    clear_completed_tasks,
)
for _t in (get_task_status, list_tasks, cancel_task, retry_task, clear_completed_tasks):
    mcp.add_tool(_t)

# ── Summaries ────────────────────────────────────────────────
from src.mcp.tools.summaries import (
    get_collection_summary,
    get_doc_summary,
    get_conflicts,
    trigger_consolidate,
)
for _t in (get_collection_summary, get_doc_summary, get_conflicts, trigger_consolidate):
    mcp.add_tool(_t)

# ── Notes ────────────────────────────────────────────────────
from src.mcp.tools.notes import (
    list_notes,
    get_note,
    create_note,
    update_note,
    delete_note,
    trigger_propagation,
)
for _t in (list_notes, get_note, create_note, update_note, delete_note, trigger_propagation):
    mcp.add_tool(_t)

# ── Meetings ─────────────────────────────────────────────────
from src.mcp.tools.meetings import (
    list_meetings,
    get_meeting,
    create_meeting,
    update_meeting,
    delete_meeting,
    start_meeting_summary,
)
for _t in (list_meetings, get_meeting, create_meeting, update_meeting, delete_meeting, start_meeting_summary):
    mcp.add_tool(_t)

# ── Hot Words ────────────────────────────────────────────────
from src.mcp.tools.hot_words import (
    list_hot_words_libraries,
    get_hot_words_library,
    create_hot_words_library,
    update_hot_words_library,
    delete_hot_words_library,
)
for _t in (list_hot_words_libraries, get_hot_words_library, create_hot_words_library, update_hot_words_library, delete_hot_words_library):
    mcp.add_tool(_t)


def get_http_app(mount_path: str = "/mcp"):
    """Return an ASGI app that serves the FastMCP Streamable HTTP endpoint.

    Usage in ``src/main.py``::

        from src.mcp.server import get_http_app
        app.add_route("/mcp", get_http_app(), methods=["GET", "POST", "DELETE"])
        app.add_route(
            "/mcp/{path:path}", get_http_app(), methods=["GET", "POST", "DELETE"]
        )

    The returned app wraps the FastMCP sub-app with a small ASGI middleware
    that strips ``mount_path`` from ``scope["path"]`` before delegating to
    the inner app — so the inner route ``/mcp`` can stay unchanged and the
    outer route can match both ``/mcp`` and ``/mcp/anything``.

    Implementation notes
    --------------------
    1. FastMCP's ``streamable_http_app()`` registers its route at ``/mcp``
       without an explicit ``methods=`` argument, so Starlette defaults to
       ``["GET"]``.  MCP Streamable HTTP requires ``POST`` (and optionally
       ``DELETE`` for session termination).  We re-register the inner route
       with ``["GET", "POST", "DELETE"]`` so all three methods work.

    2. ``Starlette.Mount`` builds its path regex as ``<prefix>/{path:path}``
       which forces a trailing slash and never matches the bare mount path
       (e.g. ``/mcp``).  Using ``app.mount("/mcp", subapp)`` therefore drops
       every request that hits exactly ``/mcp``.  To keep the public URL
       clean we expose the sub-app via ``app.add_route`` (no mount) and
       rewrite the path inside the ASGI wrapper.

    The sub-app shares the main app's lifespan (services and task_manager
    are singletons in ``src.services`` and ``src.tasks.task_manager``).
    """
    from starlette.routing import Route

    base = mcp.streamable_http_app()
    new_routes = []
    replaced = False
    for route in base.router.routes:
        if isinstance(route, Route) and route.path == "/mcp":
            # Mounting via ``app.add_route`` + the ASGI wrapper below rewrites
            # ``scope["path"]`` so the inner app always sees ``/``. Re-register
            # the inner route at ``/`` with all Streamable HTTP methods so MCP
            # clients can connect.
            new_routes.append(
                Route(
                    "/",
                    route.endpoint,
                    methods=["GET", "POST", "DELETE"],
                )
            )
            replaced = True
        else:
            new_routes.append(route)
    if replaced:
        base.router.routes = new_routes

    mount_prefix = mount_path.rstrip("/") or "/"

    class _MCPASGIApp:
        """ASGI wrapper that strips ``mount_prefix`` from ``scope['path']`` and
        forwards to the FastMCP sub-app.

        Starlette's ``Route`` distinguishes between plain ``async def`` callables
        (treated as ``func(request) -> Response``) and ASGI callables (any
        non-function object with ``__call__(scope, receive, send)``). By using
        a class instance we ensure we are routed through the ASGI branch.
        """

        def __init__(self, inner):
            self._inner = inner

        async def __call__(self, scope, receive, send):
            if scope["type"] in ("http", "websocket"):
                path = scope.get("path", "") or "/"
                if path == mount_prefix:
                    scope = {**scope, "path": "/"}
                elif path.startswith(mount_prefix + "/"):
                    scope = {**scope, "path": path[len(mount_prefix):]}
            return await self._inner(scope, receive, send)

    return _MCPASGIApp(base)


def session_lifespan():
    """Async context manager that runs the FastMCP session manager.

    FastMCP's ``StreamableHTTPSessionManager`` keeps per-session background
    tasks alive in an ``anyio`` task group.  That task group must be running
    before ``handle_request`` is called — otherwise every request fails with
    ``RuntimeError: Task group is not initialized``.

    Usage in ``src/main.py``'s lifespan::

        from src.mcp.server import session_lifespan
        async with session_lifespan():
            yield
    """
    from contextlib import asynccontextmanager

    # Force lazy initialization of the session manager by calling
    # streamable_http_app() once. After this, mcp._session_manager is set.
    _ = get_http_app()

    @asynccontextmanager
    async def _runner():
        sm = mcp._session_manager
        if sm is None:
            # No HTTP app ever requested (unlikely after get_http_app()) — yield
            # a no-op so the lifespan still works.
            yield
            return
        async with sm.run():
            yield

    return _runner()


__all__ = ["mcp", "get_http_app", "session_lifespan"]