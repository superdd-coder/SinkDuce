from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)


@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[None]:
    """Initialize SinkDuce services on MCP server startup."""
    from src.services import init_services

    logger.info("MCP server: initializing services")
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, init_services)
    logger.info("MCP server: services initialized")
    try:
        yield
    finally:
        logger.info("MCP server: shutting down")
