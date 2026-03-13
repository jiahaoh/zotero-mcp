# Development Plan: Tags & Collections Reorganization

## Goal

Enable agentic tools (like Claude Code) to fully manage and reorganize Zotero library structure through tags and collections. Currently, tags have limited write support and collections are read-only. This plan adds comprehensive CRUD operations for both.

---

## Current State

| Capability | Tags | Collections |
|-----------|------|-------------|
| **List all** | `zotero_get_tags` | `zotero_get_collections` (with hierarchy) |
| **Search by** | `zotero_search_by_tag` (with `\|\|` and `-` operators) | `zotero_get_collection_items` |
| **Add to items** | `zotero_batch_update_tags` (add/remove) | Not supported |
| **Create** | Via batch_update_tags (implicit) | Not supported |
| **Rename** | Not supported | Not supported |
| **Delete** | Via batch_update_tags (remove from items) | Not supported |
| **Merge** | Not supported | Not supported |
| **Move items** | N/A | Not supported |
| **Create hierarchy** | N/A | Not supported |

### Key Files

- `src/zotero_mcp/server.py` — Tag tools (lines 160-249, 643-694, 766-910), Collection tools (lines 366-526)
- `src/zotero_mcp/client.py` — Tag/collection formatting in `format_item_metadata()` (lines 129-141)
- `src/zotero_mcp/local_db.py` — No tag/collection queries implemented

---

## Phase 1: Enhanced Tag Management

### 1.1 Add `zotero_rename_tag` tool

**Purpose:** Rename a tag across the entire library.

**Approach:**
- Find all items with the old tag name.
- For each item: remove old tag, add new tag, call `zot.update_item()`.
- Return count of items modified.

**Pyzotero shortcut:** Check if pyzotero has a built-in tag rename endpoint (Zotero Web API has `DELETE /tags?tag=oldname` but no direct rename).

**Files to modify:**
- `server.py` — Add `zotero_rename_tag` tool

### 1.2 Add `zotero_delete_tag` tool

**Purpose:** Remove a tag from all items in the library.

**Approach:**
- Use Zotero Web API: `DELETE /tags?tag=tagname` (removes tag library-wide).
- For local mode: find all items with tag, remove it from each, update.
- Return count of items affected.

**Files to modify:**
- `server.py` — Add `zotero_delete_tag` tool

### 1.3 Add `zotero_merge_tags` tool

**Purpose:** Merge multiple tags into one (e.g., "machine-learning", "ML", "Machine Learning" → "machine learning").

**Approach:**
- Accept `source_tags: list[str]` and `target_tag: str`.
- For each item with any source tag: remove source tags, add target tag if not present.
- Batch update with `zot.update_item()`.
- Return summary of changes.

**Files to modify:**
- `server.py` — Add `zotero_merge_tags` tool

### 1.4 Add `zotero_get_tag_statistics` tool

**Purpose:** Give the agent an overview of tag usage to inform reorganization decisions.

**Approach:**
- For each tag, count how many items use it.
- Identify: most-used tags, rarely-used tags (potential cleanup candidates), similar tag names (potential merge candidates).
- Use fuzzy matching (e.g., Levenshtein distance or difflib) to suggest merges.
- Return structured summary.

**Files to modify:**
- `server.py` — Add `zotero_get_tag_statistics` tool

---

## Phase 2: Collection Management

### 2.1 Add `zotero_create_collection` tool

**Purpose:** Create a new collection, optionally nested under a parent.

**Approach:**
- Use pyzotero: `zot.create_collections([{"name": name, "parentCollection": parent_key}])`
- Return the new collection's key.

**Parameters:**
- `name: str` — Collection name
- `parent_collection_key: str | None` — Parent for nesting (None = top-level)

**Files to modify:**
- `server.py` — Add `zotero_create_collection` tool

### 2.2 Add `zotero_add_items_to_collection` tool

**Purpose:** Add items to a collection (items can belong to multiple collections).

**Approach:**
- For each item key: fetch item, append collection key to `data["collections"]`, update.
- Use `zot.addto_collection(collection_key, [item_objects])` if available in pyzotero.

**Parameters:**
- `collection_key: str` — Target collection
- `item_keys: list[str]` — Items to add

**Files to modify:**
- `server.py` — Add `zotero_add_items_to_collection` tool

### 2.3 Add `zotero_remove_items_from_collection` tool

**Purpose:** Remove items from a collection (without deleting them from the library).

**Approach:**
- For each item: fetch item, remove collection key from `data["collections"]`, update.
- Or use pyzotero's `zot.deletefrom_collection(collection_key, item)`.

**Parameters:**
- `collection_key: str` — Collection to remove from
- `item_keys: list[str]` — Items to remove

**Files to modify:**
- `server.py` — Add `zotero_remove_items_from_collection` tool

### 2.4 Add `zotero_move_items_between_collections` tool

**Purpose:** Move items from one collection to another (remove from source, add to target).

**Approach:**
- Combine add + remove in a single tool for convenience.
- Atomic per-item: add to target first, then remove from source.

**Parameters:**
- `source_collection_key: str`
- `target_collection_key: str`
- `item_keys: list[str]`

**Files to modify:**
- `server.py` — Add `zotero_move_items_between_collections` tool

### 2.5 Add `zotero_rename_collection` tool

**Purpose:** Rename an existing collection.

**Approach:**
- Fetch collection: `zot.collection(key)`
- Update name: `collection["data"]["name"] = new_name`
- Save: `zot.update_collection(collection)`

**Files to modify:**
- `server.py` — Add `zotero_rename_collection` tool

### 2.6 Add `zotero_delete_collection` tool

**Purpose:** Delete a collection (with option to keep or delete items).

**Approach:**
- Use pyzotero: `zot.delete_collection(collection_key)`
- Items remain in library unless explicitly deleted.
- Warn if collection has subcollections.

**Parameters:**
- `collection_key: str`
- `delete_items: bool = False` — Whether to also trash the items

**Files to modify:**
- `server.py` — Add `zotero_delete_collection` tool

---

## Phase 3: Intelligent Reorganization Support

### 3.1 Add `zotero_suggest_organization` tool

**Purpose:** Analyze library and suggest organizational improvements.

**Approach:**
- Analyze items without any collection assignment ("unfiled items").
- Identify collections with very few items (merge candidates).
- Identify items that might belong in existing collections based on tags/titles.
- Use existing tag statistics to suggest tag cleanups.
- Return actionable suggestions the agent can execute with the tools above.

**Files to modify:**
- `server.py` — Add `zotero_suggest_organization` tool

### 3.2 Add `zotero_auto_tag_items` tool (stretch goal)

**Purpose:** Automatically suggest tags for items based on their content.

**Approach:**
- Extract keywords from title, abstract, and fulltext.
- Match against existing library tags.
- Suggest new tags based on content analysis.
- Apply tags in batch after agent confirmation.

### 3.3 Enhance `zotero_get_collections` with item counts

**Purpose:** Show how many items are in each collection for better overview.

**Approach:**
- For each collection, call `zot.num_collectionitems(key)` or count from cached data.
- Display count next to collection name in the hierarchy view.

**Files to modify:**
- `server.py` — Modify existing `zotero_get_collections` tool

---

## Implementation Order

```
Phase 1.1  rename_tag                    (~30 lines)   ← Start here
Phase 1.2  delete_tag                    (~25 lines)
Phase 1.3  merge_tags                    (~40 lines)
Phase 2.1  create_collection             (~25 lines)   ← Enable write ops
Phase 2.2  add_items_to_collection       (~35 lines)
Phase 2.3  remove_items_from_collection  (~30 lines)
Phase 2.5  rename_collection             (~20 lines)
Phase 2.6  delete_collection             (~25 lines)
Phase 2.4  move_items_between_collections(~30 lines)   ← Convenience wrapper
Phase 1.4  get_tag_statistics            (~50 lines)   ← Informs reorganization
Phase 3.3  collection item counts        (~15 lines)   ← Quick win
Phase 3.1  suggest_organization          (~80 lines)   ← Stretch
Phase 3.2  auto_tag_items                (~60 lines)   ← Stretch
```

## Testing Strategy

- Use `tests/explore_local_zotero.ipynb` pattern: test pyzotero collection/tag CRUD methods interactively.
- Create a test collection and test tags to verify operations without affecting real library data.
- Test edge cases: renaming to existing tag name, deleting non-empty collections, moving items that are in multiple collections.
- Verify operations work in both local and web API modes.

## Risks & Open Questions

1. **Pyzotero method availability** — Need to verify which collection CRUD methods pyzotero exposes. Some operations may require raw API calls.
2. **Rate limiting (web mode)** — Bulk operations (rename tag across 500 items) may hit Zotero Web API rate limits. Need to implement batching with backoff.
3. **Conflict resolution** — If Zotero desktop client is open and modifying the same items, `version` conflicts can occur. Pyzotero handles this via `If-Unmodified-Since-Version` headers, but we need proper error handling.
4. **Undo safety** — Destructive operations (delete collection, merge tags) should log what was changed so the agent can reverse them if needed. Consider returning a "rollback plan" in the tool response.
5. **Local mode limitations** — Some write operations may not work via the local API (`localhost:23119`). Need to test each operation in local mode specifically.
