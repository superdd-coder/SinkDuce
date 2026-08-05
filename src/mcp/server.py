"""MCP server — registers atomic tools and exposes the HTTP sub-app.

Tool inventory by domain:

Collections (5):
    list_collections, get_collection, create_collection,
    update_collection_config, delete_collection

Documents (6) — legacy file-index / Qdrant-oriented:
    list_documents, upload_document_from_staging,
    delete_document, get_file_chunks, get_document_text,
    set_document_definitive

File Management L1 (13) — library tree + timeline + folder/file/version + upload:
    list_library_tree, get_timeline, list_folders, list_files, get_file,
    list_file_versions, list_chains, get_chain, get_node, list_groups,
    upload_file_from_staging, upload_file_version_from_staging,
    set_file_definitive

Search (3):
    search_direct_chunks, search_agentic_chunks, get_query_history

Tasks (5):
    get_task_status, list_tasks, cancel_task, retry_task, clear_completed_tasks

Summaries (4):
    get_collection_summary, get_doc_summary, get_conflicts, trigger_consolidate

Notes (6):
    list_notes, get_note, create_note, update_note, delete_note, trigger_propagation

Meetings (9):
    list_meetings, get_meeting, get_section, get_meeting_transcript,
    create_meeting, update_meeting, delete_meeting,
    start_meeting_summary, upload_meeting_audio_from_staging

Hot Words (5):
    list_hot_words_libraries, get_hot_words_library, create_hot_words_library,
    update_hot_words_library, delete_hot_words_library

Total: 5 + 6 + 13 + 3 + 5 + 4 + 6 + 9 + 5 = 56 tools.

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

# MCP Python SDK: public surface is FastMCP (mcp.server.fastmcp).
# Some forks/docs refer to MCPServer; accept either so local pytest and
# Docker images with different package layouts both import.
try:
    from mcp.server.mcpserver import MCPServer as _MCPCls  # type: ignore
except ImportError:  # pragma: no cover
    from mcp.server.fastmcp import FastMCP as _MCPCls

logger = logging.getLogger(__name__)

# Server-level agent guide (shown with tools/list / initialize).
_MCP_INSTRUCTIONS = """
SinkDuce MCP — use **collection IDs** (from list_collections), never display names.

## Preferred tool map

| Goal | Use first | Avoid / secondary |
|------|-----------|-------------------|
| Browse folders + files | **list_library_tree** | list_folders + N× list_files; list_documents (legacy) |
| Flat unique file list + mounts | **list_files**(scope=all) | list_documents |
| Timeline / node graph | **get_timeline** | list_chains + N× get_chain |
| One node attachments/messages | **get_node** | — |
| Read full document text | **get_document_text**(file_id=…) | inventing text from search only |
| See what was indexed | **get_file_chunks**(file_id=…) | — |
| Find files by question | **search_direct_chunks** (simple) / **search_agentic_chunks** (multi-hop) | browsing entire tree first when query is clear |
| Version history + which history is readable | **list_file_versions** (blob_available) | calling get_document_text on every version blindly |
| File paths / nodes / messages | **get_file** | — |

## Rules of thumb

1. Always resolve collection **ID** with list_collections first.
2. Prefer **file_id** over hand-built source strings. Canonical Qdrant source is ``__file__:{file_id}`` (``file:{id}`` is an accepted alias).
3. list_library_tree: ``file_count`` is always real; ``files=[]`` + ``truncated=true`` means payload omitted, not empty folder.
4. get_document_text: only pin version_id when list_file_versions shows blob_available=true; else extract_status=blob_missing. Chunks are the **current index only**.
5. Multi-mount: one file_id can appear under several folders/nodes — not multiple copies.
6. After MCP code deploy, restart this server and refresh the client tool schema.

Write tools (upload_*, delete_*, create_*) change data — confirm intent before destructive calls.
""".strip()

# FastMCP accepts name + instructions; ignore extra kwargs if a custom class differs.
try:
    mcp = _MCPCls(
        name="sinkduce",
        instructions=_MCP_INSTRUCTIONS,
    )
except TypeError:  # pragma: no cover
    mcp = _MCPCls(name="sinkduce")

# ── Resolve base URL for docs that mention the HTTP API ──────
from src.config import get_config as _get_config
_cfg = _get_config()
_base_url = f"http://localhost:{_cfg.server.api_port}"

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
    upload_document_from_staging,
    delete_document,
    get_file_chunks,
    get_document_text,
    set_document_definitive,
)
for _t in (list_documents, upload_document_from_staging, delete_document, get_file_chunks, get_document_text, set_document_definitive):
    desc = _t.__doc__
    if desc and "{base_url}" in desc:
        desc = desc.replace("{base_url}", _base_url)
    mcp.add_tool(_t, description=desc)

# ── File Management (L1) ─────────────────────────────────────
from src.mcp.tools.file_mgmt import (
    list_library_tree,
    get_timeline,
    list_folders,
    list_files as fm_list_files,
    get_file,
    list_file_versions,
    list_chains,
    get_chain,
    get_node,
    list_groups,
    upload_file_from_staging,
    upload_file_version_from_staging,
    set_file_definitive,
)
# list_files is registered under the function name; import alias avoids
# clashing with documents helpers if any. Tool public name = fn.__name__.
for _t in (
    list_library_tree,
    get_timeline,
    list_folders,
    fm_list_files,
    get_file,
    list_file_versions,
    list_chains,
    get_chain,
    get_node,
    list_groups,
    upload_file_from_staging,
    upload_file_version_from_staging,
    set_file_definitive,
):
    desc = _t.__doc__
    if desc and "{base_url}" in desc:
        desc = desc.replace("{base_url}", _base_url)
    mcp.add_tool(_t, description=desc)

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
    get_section,
    get_meeting_transcript,
    create_meeting,
    update_meeting,
    delete_meeting,
    start_meeting_summary,
    upload_meeting_audio_from_staging,
)
for _t in (list_meetings, get_meeting, get_section, get_meeting_transcript, create_meeting, update_meeting, delete_meeting, start_meeting_summary, upload_meeting_audio_from_staging):
    desc = _t.__doc__
    if desc and "{base_url}" in desc:
        desc = desc.replace("{base_url}", _base_url)
    mcp.add_tool(_t, description=desc)

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


# Cached ASGI wrapper. MCP SDK's streamable_http_app() creates a *new*
# StreamableHTTPSessionManager on every call and overwrites mcp.session_manager.
# If main mounts one instance and session_lifespan() builds another, the mounted
# app's manager never enters run() → every /mcp request 500s with
# "Task group is not initialized".
_http_app_cache: dict[str, object] = {}


def get_http_app(mount_path: str = "/mcp"):
    """Return a **singleton** ASGI app for the FastMCP Streamable HTTP endpoint.

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

    3. **Must be singleton**: only one ``streamable_http_app()`` call per
       process so ``session_lifespan()`` runs the same manager the routes use.

    The sub-app shares the main app's lifespan (services and task_manager
    are singletons in ``src.services`` and ``src.tasks.task_manager``).
    """
    from starlette.routing import Route

    key = mount_path.rstrip("/") or "/"
    cached = _http_app_cache.get(key)
    if cached is not None:
        return cached

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

    mount_prefix = key

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

    wrapper = _MCPASGIApp(base)
    _http_app_cache[key] = wrapper
    return wrapper


def session_lifespan():
    """Async context manager that runs the FastMCP session manager.

    FastMCP's ``StreamableHTTPSessionManager`` keeps per-session background
    tasks alive in an ``anyio`` task group.  That task group must be running
    before ``handle_request`` is called — otherwise every request fails with
    ``RuntimeError: Task group is not initialized``.

    Usage in ``src.main``'s lifespan::

        from src.mcp.server import session_lifespan
        async with session_lifespan():
            yield

    Important: call :func:`get_http_app` only once (it is cached) so the
    manager entered here is the same instance mounted on the FastAPI routes.
    """
    from contextlib import asynccontextmanager

    # Ensure the singleton HTTP app (and its session manager) exists. Prefer
    # reusing the instance already created at import time in main.py.
    _ = get_http_app()

    @asynccontextmanager
    async def _runner():
        sm = mcp.session_manager
        if sm is None:
            # No HTTP app ever requested (unlikely after get_http_app()) — yield
            # a no-op so the lifespan still works.
            yield
            return
        async with sm.run():
            yield

    return _runner()


__all__ = ["mcp", "get_http_app", "session_lifespan"]