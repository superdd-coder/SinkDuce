from __future__ import annotations

import logging

from mcp.server.fastmcp import FastMCP

from src.mcp.lifespan import app_lifespan

logger = logging.getLogger(__name__)

mcp = FastMCP("sinkduce", lifespan=app_lifespan)

# ── Collections ──────────────────────────────────────────────
from src.mcp.tools.collections import (
    list_collections,
    create_collection,
    get_collection_config,
    update_collection_config,
    delete_collection,
)
mcp.add_tool(list_collections)
mcp.add_tool(create_collection)
mcp.add_tool(get_collection_config)
mcp.add_tool(update_collection_config)
mcp.add_tool(delete_collection)

# ── Documents ────────────────────────────────────────────────
from src.mcp.tools.documents import (
    list_documents,
    upload_document,
    upload_folder,
    delete_document,
    get_task_status,
)
mcp.add_tool(list_documents)
mcp.add_tool(upload_document)
mcp.add_tool(upload_folder)
mcp.add_tool(delete_document)
mcp.add_tool(get_task_status)

# ── Search & Query ───────────────────────────────────────────
from src.mcp.tools.search import (
    rag_query,
    search_chunks,
    get_query_history,
)
mcp.add_tool(rag_query)
mcp.add_tool(search_chunks)
mcp.add_tool(get_query_history)

# ── Summaries ────────────────────────────────────────────────
from src.mcp.tools.summaries import (
    get_collection_summary,
    get_project_description,
    get_doc_summary,
    get_conflicts,
    trigger_consolidate,
)
mcp.add_tool(get_collection_summary)
mcp.add_tool(get_project_description)
mcp.add_tool(get_doc_summary)
mcp.add_tool(get_conflicts)
mcp.add_tool(trigger_consolidate)


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
