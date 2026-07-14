"""MCP server lifespan.

Note: services and task_manager are now initialized in the main FastAPI app's
lifespan (``src.main.lifespan``). This MCP-specific lifespan is a no-op kept
for potential future MCP-only lifecycle hooks.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)


@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[None]:
    """MCP server lifespan (noop). All heavy initialization happens in the
    main FastAPI app so that the ASGI-mounted MCP sub-app inherits the
    singleton services and task_manager.
    """
    logger.debug("MCP server starting (singleton services from main app)")
    try:
        yield
    finally:
        logger.debug("MCP server stopping")
