# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Zotero MCP is a Python MCP server exposing 37 tools for searching, retrieving, and managing Zotero library items. Supports local Zotero API and web API, with optional semantic search via ChromaDB.

## Build & Development

```bash
# Install (preferred)
uv venv --python 3.10
uv pip install -e ".[dev]"

# Optional extras
pip install -e ".[pdf]"          # PDF conversion
pip install -e ".[semantic]"     # Semantic search
pip install -e ".[annotations]"  # PDF/EPUB annotation creation
pip install -e ".[all]"          # Everything

# Run server
zotero-mcp serve --transport stdio

# Tests & formatting
pytest tests/ -v
black src/ && isort src/

# Semantic search DB
zotero-mcp update-db
zotero-mcp db-status
```

## Architecture

**Entry point:** `zotero_mcp.cli:main()`

**Tool modules in `src/zotero_mcp/tools/`** — each registers tools via `@mcp.tool()` on the shared `mcp` instance:

| Module | Tools | Purpose |
|--------|------:|---------|
| `search.py` | 4 | search_items, search_by_tag, advanced_search, search_notes |
| `retrieval.py` | 4 | get_item_metadata, get_item_fulltext, get_recent, get_item_children |
| `collections.py` | 8 | Collection CRUD + get/list |
| `tags.py` | 8 | Tag CRUD + statistics + suggest_organization |
| `notes.py` | 4 | get_notes, create_note, get_annotations, create_annotation |
| `library.py` | 5 | list/switch libraries, feeds, add_to_library |
| `semantic.py` | 3 | semantic_search, update/status DB |
| `connectors.py` | 2 | ChatGPT search/fetch wrappers |

**Core modules:**

- **`server.py`** (~65 lines) — Creates `mcp = FastMCP(...)` instance + lifespan, then imports tool modules. Tool modules import `mcp` from here.
- **`client.py`** — Zotero client factory (`get_zotero_client()`, `get_web_zotero_client()`, `get_local_zotero_client()`), formatting utilities.
- **`utils.py`** — Shared helpers: `format_creators()`, `format_item_list()`, `parse_limit()`, `is_local_mode()`, `clean_html()`.
- **`local_db.py`** — Direct SQLite reader for `zotero.sqlite`. Used for semantic search indexing and feed/library queries.
- **`semantic_search.py`** — ChromaDB vector search with auto-update.
- **`pdf_utils.py`** / **`epub_utils.py`** — Text position finding for annotation creation.

## Key Patterns

- **Circular import avoidance:** `server.py` creates `mcp` first, then imports tool modules at the bottom. Tool modules do `from zotero_mcp.server import mcp`.
- **Dual mode:** Local (SQLite + localhost:23119) vs web (Zotero API + key). Use `is_local_mode()` from utils.py.
- **SQLite locking:** `local_db.py` uses `?immutable=1` to bypass Zotero's exclusive locks.
- **Optional dependency guards:** Heavy packages are optional extras. Guard imports with `try/except ImportError`. Never add them to core `dependencies`.
- **Client caching:** `get_zotero_client()` caches instances. Cache invalidated by `set_active_library()` / `clear_active_library()`.
- **Test mocking:** Tests use `conftest.py` with `unwrap()` to extract raw functions from `@mcp.tool()` decorators. Mock `get_zotero_client` in each tool module's namespace.

## Dependencies

Core: `pyzotero`, `mcp`, `python-dotenv`, `pydantic`, `requests`, `fastmcp` (6 packages).

| Extra | Purpose |
|-------|---------|
| `pdf` | PDF-to-markdown (markitdown) |
| `semantic` | Vector search (chromadb, sentence-transformers) |
| `annotations` | PDF/EPUB annotations (pymupdf, ebooklib, tiktoken) |
| `openai` / `gemini` | Embedding providers |
| `all` | Everything above |
| `dev` | all + pytest, black, isort |

## Code Style

- **Black** (line-length 88, Python 3.10+), **isort** (black profile)
- CI enforces formatting on push to main/dev and PRs
