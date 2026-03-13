# Development Plan: Multi-Library Access (Group Libraries & RSS Feeds)

## Goal

Enable the MCP to access multiple Zotero libraries in a single session, including:
- The user's personal library
- Group libraries
- RSS feed subscriptions

Currently, the MCP is locked to a single library configured via environment variables (`ZOTERO_LIBRARY_ID`, `ZOTERO_LIBRARY_TYPE`). This plan adds runtime library discovery and switching.

---

## Current State

| Aspect | Status | Details |
|--------|--------|---------|
| Single user library | Supported | `ZOTERO_LIBRARY_ID=0`, `ZOTERO_LIBRARY_TYPE=user` |
| Single group library | Supported | Requires manual env var config per group |
| Multi-library switching | Not supported | `get_zotero_client()` reads env vars each call, no runtime override |
| RSS feeds | Not supported | Zotero stores feeds in SQLite (`feeds`, `feedItems` tables), not queried |
| Library discovery | Not supported | No tool to list available libraries/groups |

### Key Files

- `src/zotero_mcp/client.py` — `get_zotero_client()` factory (lines 30-61)
- `src/zotero_mcp/server.py` — All 20+ tool definitions, each calls `get_zotero_client()`
- `src/zotero_mcp/local_db.py` — Direct SQLite reader, only queries item-related tables
- `src/zotero_mcp/cli.py` — Environment variable loading and config hierarchy

---

## Phase 1: Library Discovery Tools

### 1.1 Add `zotero_list_libraries` tool

**Purpose:** Let the agent discover all accessible libraries (user + groups).

**Approach — Local mode:**
- Query Zotero's SQLite database for group libraries:
  ```sql
  SELECT groupID, name, description, libraryID FROM groups;
  ```
- Always include the user library (ID=0) in results.
- Return a list with: library ID, name, type (`user`/`group`), and item count.

**Approach — Web mode:**
- Use pyzotero: `zot.groups()` returns all groups the user belongs to.
- Combine with user library info.

**Files to modify:**
- `server.py` — Add new `@mcp.tool()` function
- `local_db.py` — Add `get_groups()` method for SQLite access

### 1.2 Add `zotero_switch_library` tool

**Purpose:** Allow the agent to switch the active library context at runtime.

**Approach:**
- Store active library config in a module-level or context variable (not env vars).
- Modify `get_zotero_client()` to accept optional `library_id` and `library_type` overrides.
- When switching, validate the library exists (via the discovery tool's data).

**Design decision:** Two possible approaches:
1. **Per-tool parameter** — Add optional `library_id` param to every existing tool.
2. **Global switch** — One `switch_library` tool changes context for all subsequent calls.

> **Recommended: Global switch** — Simpler for the agent, fewer parameter changes, matches how users think about "working in a library." Per-tool overrides could be added later for cross-library queries.

**Files to modify:**
- `client.py` — Refactor `get_zotero_client()` to support runtime overrides
- `server.py` — Add `zotero_switch_library` tool, update `get_zotero_client()` calls

---

## Phase 2: RSS Feed Access

### 2.1 Understand Zotero's RSS data model

Zotero stores RSS feeds in its SQLite database using these tables (not currently accessed by `local_db.py`):

- **`feeds`** — Feed subscriptions (URL, title, last update time, etc.)
- **`feedItems`** — Individual feed entries linked to items table
- **`libraries`** — Each feed has its own `libraryID` with `type = 'feed'`

> **Important:** RSS feeds are NOT accessible via pyzotero's local API (`localhost:23119`) or the Zotero Web API. They are **local-only** and must be read directly from SQLite.

### 2.2 Add `zotero_list_feeds` tool

**Purpose:** List all RSS feed subscriptions.

**Approach:**
- Query SQLite:
  ```sql
  SELECT f.*, l.libraryID
  FROM feeds f
  JOIN libraries l ON f.libraryID = l.libraryID
  WHERE l.type = 'feed';
  ```
- Return: feed title, URL, last check time, item count.

**Files to modify:**
- `local_db.py` — Add `get_feeds()` method
- `server.py` — Add `zotero_list_feeds` tool

### 2.3 Add `zotero_get_feed_items` tool

**Purpose:** Retrieve items from a specific RSS feed.

**Approach:**
- Query SQLite to join `feedItems` with `items` table:
  ```sql
  SELECT i.*, fi.readTimestamp, fi.translatedTimestamp
  FROM feedItems fi
  JOIN items i ON fi.itemID = i.itemID
  WHERE fi.itemID IN (
    SELECT itemID FROM items WHERE libraryID = ?
  )
  ORDER BY i.dateAdded DESC
  LIMIT ?;
  ```
- Format results similarly to existing search results (title, authors, date, URL).
- Include read/unread status from `feedItems.readTimestamp`.

**Files to modify:**
- `local_db.py` — Add `get_feed_items(library_id, limit)` method
- `server.py` — Add `zotero_get_feed_items` tool

### 2.4 Add `zotero_save_feed_item` tool (optional, stretch goal)

**Purpose:** Save a feed item to the user's library (or a group library) as a proper Zotero item.

**Approach:**
- Use pyzotero's `create_items()` to create a new item from feed item metadata.
- This bridges the RSS → library workflow.

---

## Phase 3: Cross-Library Search (Stretch Goal)

### 3.1 Add `zotero_search_all_libraries` tool

**Purpose:** Search across all accessible libraries simultaneously.

**Approach:**
- Iterate over discovered libraries, run search in each, merge results.
- Tag results with their source library for clarity.
- Consider parallel execution for performance.

### 3.2 Extend semantic search to multi-library

- Update `semantic_search.py` to index multiple libraries into separate ChromaDB collections.
- Allow cross-library semantic search with library filtering.

---

## Implementation Order

```
Phase 1.1  list_libraries         (~50 lines)   ← Start here
Phase 1.2  switch_library         (~80 lines)   ← Core enabler
Phase 2.2  list_feeds             (~40 lines)   ← SQLite only
Phase 2.3  get_feed_items         (~60 lines)   ← SQLite only
Phase 2.4  save_feed_item         (~40 lines)   ← Optional
Phase 3    cross-library search   (~100 lines)  ← Stretch
```

## Testing Strategy

- Use `tests/explore_local_zotero.ipynb` pattern: create a new notebook for interactive exploration of group/feed SQLite tables.
- Verify against a real Zotero database with at least one group library and one RSS feed subscription.
- Test library switching doesn't leak state between tools.

## Risks & Open Questions

1. **RSS feed schema stability** — Zotero's internal SQLite schema is not a public API. Feed table structure may change between Zotero versions. Need to verify against Zotero 7's actual schema.
2. **Local API limitations** — pyzotero's local API may not support all group library operations. Need to test which endpoints work.
3. **Concurrent access** — Switching libraries mid-conversation could confuse the agent if context isn't clear. The `switch_library` tool should return a confirmation message with the active library name.
4. **Web mode group access** — Web API requires separate API keys per group in some configurations. Need to handle auth gracefully.
