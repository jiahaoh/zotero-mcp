# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Zotero MCP is a Python Model Context Protocol (MCP) server that exposes ~20 tools for searching, retrieving, and managing Zotero library items. It supports both local Zotero API and web API, with optional semantic search via ChromaDB vector embeddings.

## Build & Development Commands

```bash
# Create virtual environment and install (preferred)
uv venv --python 3.10
uv pip install -e ".[dev]" ipykernel

# Install in development mode (alternative)
pip install -e .

# Run the server
zotero-mcp serve --transport stdio
zotero-mcp serve --transport streamable-http --host 0.0.0.0 --port 8000

# Interactive setup wizard
zotero-mcp setup

# Semantic search database management
zotero-mcp update-db
zotero-mcp db-status
zotero-mcp db-inspect --stats

# Format code
black src/
isort src/

# Run pre-commit hooks
pre-commit run --all-files
```

**Interactive exploration notebook:** `tests/explore_local_zotero.ipynb` — walks through local API and direct SQLite access step by step.

**No test suite exists yet.** The project uses pytest as dev dependency but has no test files.

## Architecture

**Entry point:** `zotero_mcp.cli:main()` — CLI with subcommands (serve, setup, update, update-db, etc.)

**Core modules in `src/zotero_mcp/`:**

- **`server.py`** (~2100 lines) — The FastMCP server instance and all tool definitions. Tools are registered via `@mcp.tool()` decorators. This is the largest file and contains search, retrieval, annotation, and semantic search tools.
- **`client.py`** — Zotero client factory (`get_zotero_client()`) and formatting utilities (BibTeX generation, markdown conversion, attachment handling). All tools in server.py call through this.
- **`semantic_search.py`** — `ZoteroSemanticSearch` class managing ChromaDB vector search with auto-update scheduling.
- **`chroma_client.py`** — ChromaDB client with pluggable embedding functions (sentence-transformers default, OpenAI, Gemini, HuggingFace).
- **`local_db.py`** — Direct SQLite reader for Zotero's local `zotero.sqlite` database. Returns `ZoteroItem` dataclass instances.
- **`cli.py`** — argparse-based CLI, environment variable loading, config file detection.
- **`setup_helper.py`** — Interactive wizard for configuring Claude Desktop integration and semantic search.
- **`better_bibtex_client.py`** — JSON-RPC client for Better BibTeX plugin (port 23119).
- **`updater.py`** — Smart update system that detects installation method (uv/pipx/conda/pip).

## Key Patterns

- **FastMCP lifespan:** `server_lifespan` in server.py handles startup/shutdown (e.g., semantic search auto-update).
- **Dual mode:** Tools work in both local mode (direct SQLite + local API) and web mode (Zotero Web API with API key). Controlled by `ZOTERO_LOCAL` env var.
- **Two local data paths:**
  - **pyzotero local API** (`client.py`): HTTP requests to `localhost:23119`. Used by all MCP tools. Requires Zotero running. Supports both user (`library_id=0`) and group (`library_type="group"`, `library_id=<groupID>`) libraries.
  - **Direct SQLite** (`local_db.py`): Reads `~/Zotero/zotero.sqlite` directly. Used only for semantic search bulk indexing. Does not require Zotero running.
- **SQLite locking:** Zotero uses rollback journal mode (not WAL), so it holds exclusive locks while running. `local_db.py` uses `?immutable=1` to bypass this, accepting potentially stale reads.
- **Config hierarchy:** CLI args > standalone config (`~/.config/zotero-mcp/config.json`) > Claude Desktop config > env vars > defaults.
- **Python 3.10+:** Uses `X | Y` union syntax throughout. Target version enforced by black and pyupgrade.
- **Version:** Defined in `src/zotero_mcp/_version.py`.

## Environment Variables

| Variable | Purpose |
|---|---|
| `ZOTERO_LOCAL` | Use local API (true/false) |
| `ZOTERO_API_KEY` | Web API key |
| `ZOTERO_LIBRARY_ID` | Library ID: `0` for local user library, or a group ID (find via Zotero website URL or `SELECT groupID, name FROM groups` in zotero.sqlite) |
| `ZOTERO_LIBRARY_TYPE` | `"user"` (default) or `"group"` for shared libraries |
| `ZOTERO_DB_PATH` | Custom zotero.sqlite path |
| `ZOTERO_EMBEDDING_MODEL` | "default", "openai", or "gemini" |
| `OPENAI_API_KEY` | For OpenAI embeddings |
| `GEMINI_API_KEY` | For Gemini embeddings |

## Code Style

- **Black** with line-length 88, target Python 3.10+
- **isort** with black-compatible profile
- **pyupgrade** with `--py310-plus` via pre-commit
