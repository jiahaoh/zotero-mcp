"""Shared fixtures for Zotero MCP tool tests.

All fixtures mock pyzotero so that **no real Zotero library is touched**.
"""

from unittest.mock import MagicMock, patch

import pytest

from fastmcp.tools.tool import FunctionTool


def unwrap(tool_or_fn):
    """Return the raw function behind a FastMCP ``@mcp.tool()`` decorator.

    If the object is already a plain function, return it unchanged.
    """
    if isinstance(tool_or_fn, FunctionTool):
        return tool_or_fn.fn
    return tool_or_fn


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------

def make_item(
    key: str,
    *,
    tags: list[str] | None = None,
    collections: list[str] | None = None,
    item_type: str = "journalArticle",
    title: str = "Test Item",
):
    """Create a realistic Zotero item dict (as returned by pyzotero)."""
    return {
        "key": key,
        "data": {
            "key": key,
            "itemType": item_type,
            "title": title,
            "tags": [{"tag": t} for t in (tags or [])],
            "collections": list(collections or []),
            "creators": [],
            "date": "2024",
        },
        "meta": {},
    }


def make_collection(
    key: str,
    name: str,
    *,
    parent: str | None = None,
    num_items: int = 0,
):
    """Create a realistic Zotero collection dict."""
    data: dict = {"key": key, "name": name}
    if parent:
        data["parentCollection"] = parent
    return {
        "key": key,
        "data": data,
        "meta": {"numItems": num_items},
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_ctx():
    """A fake MCP Context — only ``info()`` and ``error()`` are called."""
    return MagicMock()


@pytest.fixture()
def mock_zot():
    """Patch ``get_zotero_client`` so every tool gets a MagicMock Zotero client.

    Yields the mock client so tests can configure return values.
    """
    zot = MagicMock()
    # Patch get_zotero_client in every tool module that imports it.
    targets = [
        "zotero_mcp.tools.search.get_zotero_client",
        "zotero_mcp.tools.retrieval.get_zotero_client",
        "zotero_mcp.tools.collections.get_zotero_client",
        "zotero_mcp.tools.tags.get_zotero_client",
        "zotero_mcp.tools.notes.get_zotero_client",
        "zotero_mcp.tools.library.get_zotero_client",
        "zotero_mcp.tools.connectors.get_zotero_client",
    ]
    patches = [patch(t, return_value=zot) for t in targets]
    for p in patches:
        p.start()
    yield zot
    for p in patches:
        p.stop()
