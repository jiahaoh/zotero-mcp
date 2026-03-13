"""
Zotero MCP server implementation.

This module creates the FastMCP server instance and imports tool modules
that register their tools via ``@mcp.tool()`` decorators.

Note: ChatGPT requires specific tool names "search" and "fetch", which are
provided by the connectors tool module.
"""

import asyncio
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastmcp import FastMCP


@asynccontextmanager
async def server_lifespan(server: FastMCP):
    """Manage server startup and shutdown lifecycle."""
    sys.stderr.write("Starting Zotero MCP server...\n")

    # Check for semantic search auto-update on startup
    try:
        from zotero_mcp.semantic_search import create_semantic_search

        config_path = Path.home() / ".config" / "zotero-mcp" / "config.json"

        if config_path.exists():
            search = create_semantic_search(str(config_path))

            if search.should_update_database():
                sys.stderr.write("Auto-updating semantic search database...\n")

                # Run update in background to avoid blocking server startup
                async def background_update():
                    try:
                        stats = search.update_database(extract_fulltext=False)
                        sys.stderr.write(
                            f"Database update completed: {stats.get('processed_items', 0)} items processed\n"
                        )
                    except Exception as e:
                        sys.stderr.write(f"Background database update failed: {e}\n")

                # Start background task
                asyncio.create_task(background_update())

    except Exception as e:
        sys.stderr.write(f"Warning: Could not check semantic search auto-update: {e}\n")

    yield {}

    sys.stderr.write("Shutting down Zotero MCP server...\n")


# Create an MCP server (fastmcp 2.14+ no longer accepts `dependencies`)
mcp = FastMCP("Zotero", lifespan=server_lifespan)

# ---------------------------------------------------------------------------
# Import tool modules — each registers its tools on the ``mcp`` instance.
# These imports MUST come after ``mcp`` is defined to avoid circular imports.
# ---------------------------------------------------------------------------
from zotero_mcp.tools import (  # noqa: E402, F401
    collections,
    connectors,
    library,
    notes,
    retrieval,
    search,
    semantic,
    tags,
)
