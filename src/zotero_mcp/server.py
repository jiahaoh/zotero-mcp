"""
Zotero MCP server implementation.

Note: ChatGPT requires specific tool names "search" and "fetch", and so they
are defined and used and piped through to the main server tools. See bottom of file for details.
"""

import asyncio
import json
import os
import re
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, List, Literal, Optional, Union

from fastmcp import Context, FastMCP

from zotero_mcp.client import (
    clear_active_library,
    convert_to_markdown,
    format_item_metadata,
    generate_bibtex,
    get_active_library,
    get_attachment_details,
    get_zotero_client,
    set_active_library,
)
from zotero_mcp.utils import (
    clean_html,
    format_creators,
    format_item_list,
    is_local_mode,
    parse_limit,
)


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


@mcp.tool(
    name="zotero_search_items",
    description="Search for items in your Zotero library, given a query string.",
)
def search_items(
    query: str,
    qmode: Literal["titleCreatorYear", "everything"] = "titleCreatorYear",
    item_type: str = "-attachment",  # Exclude attachments by default
    limit: int | str | None = 10,
    tag: list[str] | None = None,
    *,
    ctx: Context,
) -> str:
    """
    Search for items in your Zotero library.

    Args:
        query: Search query string
        qmode: Query mode (titleCreatorYear or everything)
        item_type: Type of items to search for. Use "-attachment" to exclude attachments.
        limit: Maximum number of results to return
        tag: List of tags conditions to filter by
        ctx: MCP context

    Returns:
        Markdown-formatted search results
    """
    try:
        if not query.strip():
            return "Error: Search query cannot be empty"

        tag_condition_str = ""
        if tag:
            tag_condition_str = f" with tags: '{', '.join(tag)}'"
        else:
            tag = []

        ctx.info(f"Searching Zotero for '{query}'{tag_condition_str}")
        zot = get_zotero_client()

        limit = parse_limit(limit)

        # Search using the query parameters
        zot.add_parameters(
            q=query, qmode=qmode, itemType=item_type, limit=limit, tag=tag
        )
        results = zot.items()

        if not results:
            return f"No items found matching query: '{query}'{tag_condition_str}"

        # Format results as markdown
        output = [f"# Search Results for '{query}'", f"{tag_condition_str}", ""]
        output.extend(format_item_list(results))

        return "\n".join(output)

    except Exception as e:
        ctx.error(f"Error searching Zotero: {str(e)}")
        return f"Error searching Zotero: {str(e)}"


@mcp.tool(
    name="zotero_search_by_tag",
    description="Search for items in your Zotero library by tag. "
    "Conditions are ANDed, each term supports disjunction`||` and exclusion`-`.",
)
def search_by_tag(
    tag: list[str],
    item_type: str = "-attachment",
    limit: int | str | None = 10,
    *,
    ctx: Context,
) -> str:
    """
    Search for items in your Zotero library by tag。
    Conditions are ANDed, each term supports disjunction`||` and exclusion`-`.

    Args:
        tag: List of tag conditions. Items are returned only if they satisfy
            ALL conditions in the list. Each tag condition can be expressed
            in two ways:
                As alternatives: tag1 || tag2 (matches items with either tag1 OR tag2)
                As exclusions: -tag (matches items that do NOT have this tag)
            For example, a tag field with ["research || important", "-draft"] would
            return items that:
                Have either "research" OR "important" tags, AND
                Do NOT have the "draft" tag
        item_type: Type of items to search for. Use "-attachment" to exclude attachments.
        limit: Maximum number of results to return
        ctx: MCP context

    Returns:
        Markdown-formatted search results
    """
    try:
        if not tag:
            return "Error: Tag cannot be empty"

        ctx.info(f"Searching Zotero for tag '{tag}'")
        zot = get_zotero_client()

        limit = parse_limit(limit)

        # Search using the query parameters
        zot.add_parameters(q="", tag=tag, itemType=item_type, limit=limit)
        results = zot.items()

        if not results:
            return f"No items found with tag: '{tag}'"

        # Format results as markdown
        output = [f"# Search Results for Tag: '{tag}'", ""]
        output.extend(format_item_list(results))

        return "\n".join(output)

    except Exception as e:
        ctx.error(f"Error searching Zotero: {str(e)}")
        return f"Error searching Zotero: {str(e)}"


@mcp.tool(
    name="zotero_get_item_metadata",
    description="Get detailed metadata for a specific Zotero item by its key.",
)
def get_item_metadata(
    item_key: str,
    include_abstract: bool = True,
    format: Literal["markdown", "bibtex"] = "markdown",
    *,
    ctx: Context,
) -> str:
    """
    Get detailed metadata for a Zotero item.

    Args:
        item_key: Zotero item key/ID
        include_abstract: Whether to include the abstract in the output (markdown format only)
        format: Output format - 'markdown' for detailed metadata or 'bibtex' for BibTeX citation
        ctx: MCP context

    Returns:
        Formatted item metadata (markdown or BibTeX)
    """
    try:
        ctx.info(f"Fetching metadata for item {item_key} in {format} format")
        zot = get_zotero_client()

        item = zot.item(item_key)
        if not item:
            return f"No item found with key: {item_key}"

        if format == "bibtex":
            return generate_bibtex(item)
        else:
            return format_item_metadata(item, include_abstract)

    except Exception as e:
        ctx.error(f"Error fetching item metadata: {str(e)}")
        return f"Error fetching item metadata: {str(e)}"


@mcp.tool(
    name="zotero_get_item_fulltext",
    description="Get the full text content of a Zotero item by its key.",
)
def get_item_fulltext(item_key: str, *, ctx: Context) -> str:
    """
    Get the full text content of a Zotero item.

    Args:
        item_key: Zotero item key/ID
        ctx: MCP context

    Returns:
        Markdown-formatted item full text
    """
    try:
        ctx.info(f"Fetching full text for item {item_key}")
        zot = get_zotero_client()

        # First get the item metadata
        item = zot.item(item_key)
        if not item:
            return f"No item found with key: {item_key}"

        # Get item metadata in markdown format
        metadata = format_item_metadata(item, include_abstract=True)

        # Try to get attachment details
        attachment = get_attachment_details(zot, item)
        if not attachment:
            return f"{metadata}\n\n---\n\nNo suitable attachment found for this item."

        ctx.info(f"Found attachment: {attachment.key} ({attachment.content_type})")

        # Try fetching full text from Zotero's full text index first
        try:
            full_text_data = zot.fulltext_item(attachment.key)
            if (
                full_text_data
                and "content" in full_text_data
                and full_text_data["content"]
            ):
                ctx.info("Successfully retrieved full text from Zotero's index")
                return (
                    f"{metadata}\n\n---\n\n## Full Text\n\n{full_text_data['content']}"
                )
        except Exception as fulltext_error:
            ctx.info(f"Couldn't retrieve indexed full text: {str(fulltext_error)}")

        # If we couldn't get indexed full text, try to download and convert the file
        try:
            ctx.info(f"Attempting to download and convert attachment {attachment.key}")

            # Download the file to a temporary location
            import os
            import tempfile

            with tempfile.TemporaryDirectory() as tmpdir:
                file_path = os.path.join(
                    tmpdir, attachment.filename or f"{attachment.key}.pdf"
                )
                zot.dump(
                    attachment.key, filename=os.path.basename(file_path), path=tmpdir
                )

                if os.path.exists(file_path):
                    ctx.info(f"Downloaded file to {file_path}, converting to markdown")
                    converted_text = convert_to_markdown(file_path)
                    return f"{metadata}\n\n---\n\n## Full Text\n\n{converted_text}"
                else:
                    return f"{metadata}\n\n---\n\nFile download failed."
        except Exception as download_error:
            ctx.error(f"Error downloading/converting file: {str(download_error)}")
            return f"{metadata}\n\n---\n\nError accessing attachment: {str(download_error)}"

    except Exception as e:
        ctx.error(f"Error fetching item full text: {str(e)}")
        return f"Error fetching item full text: {str(e)}"


@mcp.tool(
    name="zotero_get_collections",
    description="List all collections in your Zotero library.",
)
def get_collections(limit: int | str | None = None, *, ctx: Context) -> str:
    """
    List all collections in your Zotero library.

    Args:
        limit: Maximum number of collections to return
        ctx: MCP context

    Returns:
        Markdown-formatted list of collections
    """
    try:
        ctx.info("Fetching collections")
        zot = get_zotero_client()

        limit = parse_limit(limit)

        collections = zot.collections(limit=limit)

        # Always return the header, even if empty
        output = ["# Zotero Collections", ""]

        if not collections:
            output.append("No collections found in your Zotero library.")
            return "\n".join(output)

        # Create a mapping of collection IDs to their data
        collection_map = {c["key"]: c for c in collections}

        # Create a mapping of parent to child collections
        # Only add entries for collections that actually exist
        hierarchy = {}
        for coll in collections:
            parent_key = coll["data"].get("parentCollection")
            # Handle various representations of "no parent"
            if parent_key in ["", None] or not parent_key:
                parent_key = None  # Normalize to None

            if parent_key not in hierarchy:
                hierarchy[parent_key] = []
            hierarchy[parent_key].append(coll["key"])

        # Function to recursively format collections
        def format_collection(key, level=0):
            if key not in collection_map:
                return []

            coll = collection_map[key]
            name = coll["data"].get("name", "Unnamed Collection")

            # Create indentation for hierarchy
            indent = "  " * level
            num_items = coll.get("meta", {}).get("numItems", "")
            count_str = f" [{num_items} items]" if num_items != "" else ""
            lines = [f"{indent}- **{name}**{count_str} (Key: {key})"]

            # Add children if they exist
            child_keys = hierarchy.get(key, [])
            for child_key in sorted(child_keys):  # Sort for consistent output
                lines.extend(format_collection(child_key, level + 1))

            return lines

        # Start with top-level collections (those with None as parent)
        top_level_keys = hierarchy.get(None, [])

        if not top_level_keys:
            # If no clear hierarchy, just list all collections
            output.append("Collections (flat list):")
            for coll in sorted(collections, key=lambda x: x["data"].get("name", "")):
                name = coll["data"].get("name", "Unnamed Collection")
                key = coll["key"]
                num_items = coll.get("meta", {}).get("numItems", "")
                count_str = f" [{num_items} items]" if num_items != "" else ""
                output.append(f"- **{name}**{count_str} (Key: {key})")
        else:
            # Display hierarchical structure
            for key in sorted(top_level_keys):
                output.extend(format_collection(key))

        return "\n".join(output)

    except Exception as e:
        ctx.error(f"Error fetching collections: {str(e)}")
        error_msg = f"Error fetching collections: {str(e)}"
        return f"# Zotero Collections\n\n{error_msg}"


@mcp.tool(
    name="zotero_get_collection_items",
    description="Get all items in a specific Zotero collection.",
)
def get_collection_items(
    collection_key: str, limit: int | str | None = 50, *, ctx: Context
) -> str:
    """
    Get all items in a specific Zotero collection.

    Args:
        collection_key: The collection key/ID
        limit: Maximum number of items to return
        ctx: MCP context

    Returns:
        Markdown-formatted list of items in the collection
    """
    try:
        ctx.info(f"Fetching items for collection {collection_key}")
        zot = get_zotero_client()

        # First get the collection details
        try:
            collection = zot.collection(collection_key)
            collection_name = collection["data"].get("name", "Unnamed Collection")
        except Exception:
            collection_name = f"Collection {collection_key}"

        limit = parse_limit(limit)

        # Then get the items
        items = zot.collection_items(collection_key, limit=limit)
        if not items:
            return f"No items found in collection: {collection_name} (Key: {collection_key})"

        # Format items as markdown
        output = [f"# Items in Collection: {collection_name}", ""]
        output.extend(
            format_item_list(items, include_abstract=False, include_tags=False)
        )

        return "\n".join(output)

    except Exception as e:
        ctx.error(f"Error fetching collection items: {str(e)}")
        return f"Error fetching collection items: {str(e)}"


@mcp.tool(
    name="zotero_create_collection",
    description="Create a new collection in your Zotero library, optionally nested under a parent collection.",
)
def create_collection(
    name: str,
    parent_collection_key: str | None = None,
    *,
    ctx: Context,
) -> str:
    """
    Create a new collection.

    Args:
        name: Name of the new collection
        parent_collection_key: Key of the parent collection for nesting (None = top-level)
        ctx: MCP context

    Returns:
        Confirmation with the new collection's key
    """
    try:
        if not name:
            return "Error: Collection name must be provided"

        ctx.info(f"Creating collection '{name}'")
        zot = get_zotero_client()

        payload: dict = {"name": name}
        if parent_collection_key:
            payload["parentCollection"] = parent_collection_key

        result = zot.create_collections([payload])

        # pyzotero returns a dict with 'successful', 'unchanged', 'failed' keys
        successful = result.get("successful", {})
        if successful:
            new_coll = list(successful.values())[0]
            new_key = new_coll.get(
                "key", new_coll.get("data", {}).get("key", "unknown")
            )
            parent_info = (
                f" under parent `{parent_collection_key}`"
                if parent_collection_key
                else " (top-level)"
            )
            return (
                f"# Collection Created\n\n"
                f"**{name}**{parent_info}\n"
                f"**Key:** `{new_key}`"
            )

        failed = result.get("failed", {})
        if failed:
            errors = "; ".join(str(v) for v in failed.values())
            return f"Error creating collection: {errors}"

        return "Error: Unexpected response from Zotero API"
    except Exception as e:
        ctx.error(f"Error creating collection: {str(e)}")
        return f"Error creating collection: {str(e)}"


@mcp.tool(
    name="zotero_add_items_to_collection",
    description="Add items to a Zotero collection. Items can belong to multiple collections.",
)
def add_items_to_collection(
    collection_key: str,
    item_keys: list[str],
    *,
    ctx: Context,
) -> str:
    """
    Add items to a collection.

    Args:
        collection_key: Key of the target collection
        item_keys: List of item keys to add
        ctx: MCP context

    Returns:
        Summary of items added
    """
    try:
        if not collection_key or not item_keys:
            return "Error: collection_key and item_keys must be provided"

        ctx.info(f"Adding {len(item_keys)} items to collection {collection_key}")
        zot = get_zotero_client()

        added = 0
        skipped = 0
        for key in item_keys:
            try:
                item = zot.item(key)
                collections = item["data"].get("collections", [])
                if collection_key in collections:
                    skipped += 1
                    continue
                collections.append(collection_key)
                item["data"]["collections"] = collections
                zot.update_item(item)
                added += 1
            except Exception as e:
                ctx.error(f"Failed to add item {key}: {e}")
                skipped += 1

        return (
            f"# Items Added to Collection\n\n"
            f"**Collection:** `{collection_key}`\n"
            f"**Added:** {added} | **Skipped:** {skipped}"
        )
    except Exception as e:
        ctx.error(f"Error adding items to collection: {str(e)}")
        return f"Error adding items to collection: {str(e)}"


@mcp.tool(
    name="zotero_remove_items_from_collection",
    description="Remove items from a Zotero collection without deleting them from the library.",
)
def remove_items_from_collection(
    collection_key: str,
    item_keys: list[str],
    *,
    ctx: Context,
) -> str:
    """
    Remove items from a collection (items remain in the library).

    Args:
        collection_key: Key of the collection to remove from
        item_keys: List of item keys to remove
        ctx: MCP context

    Returns:
        Summary of items removed
    """
    try:
        if not collection_key or not item_keys:
            return "Error: collection_key and item_keys must be provided"

        ctx.info(f"Removing {len(item_keys)} items from collection {collection_key}")
        zot = get_zotero_client()

        removed = 0
        skipped = 0
        for key in item_keys:
            try:
                item = zot.item(key)
                collections = item["data"].get("collections", [])
                if collection_key not in collections:
                    skipped += 1
                    continue
                collections.remove(collection_key)
                item["data"]["collections"] = collections
                zot.update_item(item)
                removed += 1
            except Exception as e:
                ctx.error(f"Failed to remove item {key}: {e}")
                skipped += 1

        return (
            f"# Items Removed from Collection\n\n"
            f"**Collection:** `{collection_key}`\n"
            f"**Removed:** {removed} | **Skipped:** {skipped}"
        )
    except Exception as e:
        ctx.error(f"Error removing items from collection: {str(e)}")
        return f"Error removing items from collection: {str(e)}"


@mcp.tool(
    name="zotero_move_items_between_collections",
    description="Move items from one Zotero collection to another (remove from source, add to target).",
)
def move_items_between_collections(
    source_collection_key: str,
    target_collection_key: str,
    item_keys: list[str],
    *,
    ctx: Context,
) -> str:
    """
    Move items between collections (add to target first, then remove from source).

    Args:
        source_collection_key: Key of the source collection
        target_collection_key: Key of the target collection
        item_keys: List of item keys to move
        ctx: MCP context

    Returns:
        Summary of the move operation
    """
    try:
        if not source_collection_key or not target_collection_key or not item_keys:
            return "Error: source_collection_key, target_collection_key, and item_keys must be provided"
        if source_collection_key == target_collection_key:
            return "Error: Source and target collections are the same"

        ctx.info(
            f"Moving {len(item_keys)} items from {source_collection_key} → {target_collection_key}"
        )
        zot = get_zotero_client()

        moved = 0
        failed = 0
        for key in item_keys:
            try:
                item = zot.item(key)
                collections = item["data"].get("collections", [])
                changed = False
                if target_collection_key not in collections:
                    collections.append(target_collection_key)
                    changed = True
                if source_collection_key in collections:
                    collections.remove(source_collection_key)
                    changed = True
                if changed:
                    item["data"]["collections"] = collections
                    zot.update_item(item)
                    moved += 1
            except Exception as e:
                ctx.error(f"Failed to move item {key}: {e}")
                failed += 1

        return (
            f"# Items Moved\n\n"
            f"**From:** `{source_collection_key}` → **To:** `{target_collection_key}`\n"
            f"**Moved:** {moved} | **Failed:** {failed}"
        )
    except Exception as e:
        ctx.error(f"Error moving items between collections: {str(e)}")
        return f"Error moving items between collections: {str(e)}"


@mcp.tool(
    name="zotero_rename_collection",
    description="Rename an existing Zotero collection.",
)
def rename_collection(
    collection_key: str,
    new_name: str,
    *,
    ctx: Context,
) -> str:
    """
    Rename a collection.

    Args:
        collection_key: Key of the collection to rename
        new_name: The new name for the collection
        ctx: MCP context

    Returns:
        Confirmation of the rename
    """
    try:
        if not collection_key or not new_name:
            return "Error: collection_key and new_name must be provided"

        ctx.info(f"Renaming collection {collection_key} → '{new_name}'")
        zot = get_zotero_client()

        collection = zot.collection(collection_key)
        old_name = collection["data"].get("name", "Unnamed")
        collection["data"]["name"] = new_name
        zot.update_collection(collection)

        return (
            f"# Collection Renamed\n\n"
            f"`{old_name}` → `{new_name}` (Key: `{collection_key}`)"
        )
    except Exception as e:
        ctx.error(f"Error renaming collection: {str(e)}")
        return f"Error renaming collection: {str(e)}"


@mcp.tool(
    name="zotero_delete_collection",
    description="Delete a Zotero collection. Items remain in the library unless delete_items is True.",
)
def delete_collection(
    collection_key: str,
    delete_items: bool = False,
    *,
    ctx: Context,
) -> str:
    """
    Delete a collection.

    Args:
        collection_key: Key of the collection to delete
        delete_items: If True, also trash the items in the collection
        ctx: MCP context

    Returns:
        Confirmation of the deletion
    """
    try:
        if not collection_key:
            return "Error: collection_key must be provided"

        ctx.info(f"Deleting collection {collection_key}")
        zot = get_zotero_client()

        # Get collection info before deleting
        collection = zot.collection(collection_key)
        coll_name = collection["data"].get("name", "Unnamed")

        # Check for subcollections
        subcollections = zot.collections_sub(collection_key)
        if subcollections:
            sub_names = [s["data"].get("name", "?") for s in subcollections]
            warning = (
                f"\n\n**Warning:** This collection has {len(subcollections)} "
                f"subcollection(s): {', '.join(sub_names)}. "
                f"They will also be deleted."
            )
        else:
            warning = ""

        # Optionally trash items first
        trashed = 0
        if delete_items:
            items = zot.collection_items(collection_key)
            for item in items:
                if item["data"].get("itemType") == "attachment":
                    continue
                try:
                    item["data"]["deleted"] = True
                    zot.update_item(item)
                    trashed += 1
                except Exception as e:
                    ctx.error(f"Failed to trash item {item.get('key', '?')}: {e}")

        zot.delete_collection(collection)

        items_msg = f"\n**Items trashed:** {trashed}" if delete_items else ""
        return (
            f"# Collection Deleted\n\n"
            f"**{coll_name}** (`{collection_key}`) has been deleted.{items_msg}{warning}"
        )
    except Exception as e:
        ctx.error(f"Error deleting collection: {str(e)}")
        return f"Error deleting collection: {str(e)}"


@mcp.tool(
    name="zotero_get_item_children",
    description="Get all child items (attachments, notes) for a specific Zotero item.",
)
def get_item_children(item_key: str, *, ctx: Context) -> str:
    """
    Get all child items (attachments, notes) for a specific Zotero item.

    Args:
        item_key: Zotero item key/ID
        ctx: MCP context

    Returns:
        Markdown-formatted list of child items
    """
    try:
        ctx.info(f"Fetching children for item {item_key}")
        zot = get_zotero_client()

        # First get the parent item details
        try:
            parent = zot.item(item_key)
            parent_title = parent["data"].get("title", "Untitled Item")
        except Exception:
            parent_title = f"Item {item_key}"

        # Then get the children
        children = zot.children(item_key)
        if not children:
            return f"No child items found for: {parent_title} (Key: {item_key})"

        # Format children as markdown
        output = [f"# Child Items for: {parent_title}", ""]

        # Group children by type
        attachments = []
        notes = []
        others = []

        for child in children:
            data = child.get("data", {})
            item_type = data.get("itemType", "unknown")

            if item_type == "attachment":
                attachments.append(child)
            elif item_type == "note":
                notes.append(child)
            else:
                others.append(child)

        # Format attachments
        if attachments:
            output.append("## Attachments")
            for i, att in enumerate(attachments, 1):
                data = att.get("data", {})
                title = data.get("title", "Untitled")
                key = att.get("key", "")
                content_type = data.get("contentType", "Unknown")
                filename = data.get("filename", "")

                output.append(f"{i}. **{title}**")
                output.append(f"   - Key: {key}")
                output.append(f"   - Type: {content_type}")
                if filename:
                    output.append(f"   - Filename: {filename}")
                output.append("")

        # Format notes
        if notes:
            output.append("## Notes")
            for i, note in enumerate(notes, 1):
                data = note.get("data", {})
                title = data.get("title", "Untitled Note")
                key = note.get("key", "")
                note_text = data.get("note", "")

                # Clean up HTML in notes
                note_text = note_text.replace("<p>", "").replace("</p>", "\n\n")
                note_text = note_text.replace("<br/>", "\n").replace("<br>", "\n")

                # Limit note length for display
                if len(note_text) > 500:
                    note_text = note_text[:500] + "...\n\n(Note truncated)"

                output.append(f"{i}. **{title}**")
                output.append(f"   - Key: {key}")
                output.append(f"   - Content:\n```\n{note_text}\n```")
                output.append("")

        # Format other item types
        if others:
            output.append("## Other Items")
            for i, other in enumerate(others, 1):
                data = other.get("data", {})
                title = data.get("title", "Untitled")
                key = other.get("key", "")
                item_type = data.get("itemType", "unknown")

                output.append(f"{i}. **{title}**")
                output.append(f"   - Key: {key}")
                output.append(f"   - Type: {item_type}")
                output.append("")

        return "\n".join(output)

    except Exception as e:
        ctx.error(f"Error fetching item children: {str(e)}")
        return f"Error fetching item children: {str(e)}"


@mcp.tool(
    name="zotero_get_tags", description="Get all tags used in your Zotero library."
)
def get_tags(limit: int | str | None = None, *, ctx: Context) -> str:
    """
    Get all tags used in your Zotero library.

    Args:
        limit: Maximum number of tags to return
        ctx: MCP context

    Returns:
        Markdown-formatted list of tags
    """
    try:
        ctx.info("Fetching tags")
        zot = get_zotero_client()

        limit = parse_limit(limit)

        tags = zot.tags(limit=limit)
        if not tags:
            return "No tags found in your Zotero library."

        # Format tags as markdown
        output = ["# Zotero Tags", ""]

        # Sort tags alphabetically
        sorted_tags = sorted(tags)

        # Group tags alphabetically
        current_letter = None
        for tag in sorted_tags:
            first_letter = tag[0].upper() if tag else "#"

            if first_letter != current_letter:
                current_letter = first_letter
                output.append(f"## {current_letter}")

            output.append(f"- `{tag}`")

        return "\n".join(output)

    except Exception as e:
        ctx.error(f"Error fetching tags: {str(e)}")
        return f"Error fetching tags: {str(e)}"


@mcp.tool(
    name="zotero_list_libraries",
    description="List all accessible Zotero libraries (user library, group libraries, and RSS feeds). Use this to discover available libraries before switching with zotero_switch_library.",
)
def list_libraries(*, ctx: Context) -> str:
    """
    List all accessible Zotero libraries.

    In local mode, reads directly from the SQLite database.
    In web mode, queries groups via the Zotero API.

    Returns:
        Markdown-formatted list of libraries with item counts.
    """
    try:
        ctx.info("Listing accessible libraries")
        local = is_local_mode()
        override = get_active_library()

        output = ["# Zotero Libraries", ""]

        # Show active library context
        if override:
            output.append(
                f"> **Active library:** ID={override['library_id']}, "
                f"type={override['library_type']}"
            )
            output.append("")

        if local:
            from zotero_mcp.local_db import LocalZoteroReader

            reader = LocalZoteroReader()
            try:
                libraries = reader.get_libraries()

                # User library
                user_libs = [l for l in libraries if l["type"] == "user"]
                if user_libs:
                    output.append("## User Library")
                    for lib in user_libs:
                        output.append(
                            f"- **My Library** — {lib['itemCount']} items "
                            f"(libraryID={lib['libraryID']})"
                        )
                    output.append("")

                # Group libraries
                group_libs = [l for l in libraries if l["type"] == "group"]
                if group_libs:
                    output.append("## Group Libraries")
                    for lib in group_libs:
                        desc = (
                            f" — {lib['groupDescription']}"
                            if lib.get("groupDescription")
                            else ""
                        )
                        output.append(
                            f"- **{lib['groupName']}** — {lib['itemCount']} items "
                            f"(groupID={lib['groupID']}){desc}"
                        )
                    output.append("")

                # Feeds
                feed_libs = [l for l in libraries if l["type"] == "feed"]
                if feed_libs:
                    output.append("## RSS Feeds")
                    for lib in feed_libs:
                        output.append(
                            f"- **{lib['feedName']}** — {lib['itemCount']} items "
                            f"(libraryID={lib['libraryID']})"
                        )
                    output.append("")
            finally:
                reader.close()
        else:
            # Web mode: query groups via pyzotero
            zot = get_zotero_client()
            output.append("## User Library")
            output.append(
                f"- **My Library** (libraryID={os.getenv('ZOTERO_LIBRARY_ID', '?')})"
            )
            output.append("")

            try:
                groups = zot.groups()
                if groups:
                    output.append("## Group Libraries")
                    for group in groups:
                        gdata = group.get("data", {})
                        output.append(
                            f"- **{gdata.get('name', 'Unknown')}** "
                            f"(groupID={group.get('id', '?')})"
                        )
                    output.append("")
            except Exception:
                output.append("*Could not retrieve group libraries.*\n")

            output.append("*Note: RSS feeds are only accessible in local mode.*")

        output.append("")
        output.append("Use `zotero_switch_library` to switch to a different library.")

        return "\n".join(output)

    except Exception as e:
        ctx.error(f"Error listing libraries: {str(e)}")
        return f"Error listing libraries: {str(e)}"


@mcp.tool(
    name="zotero_switch_library",
    description="Switch the active Zotero library context. All subsequent tool calls will operate on the selected library. Use zotero_list_libraries first to see available options. Pass library_type='default' to reset to the original environment variable configuration.",
)
def switch_library(
    library_id: str,
    library_type: str = "group",
    *,
    ctx: Context,
) -> str:
    """
    Switch the active library for all subsequent MCP tool calls.

    Args:
        library_id: The library/group ID to switch to.
            For user library: "0" (local mode) or your user ID (web mode).
            For group libraries: the groupID (e.g. "6069773").
        library_type: "user", "group", or "default" to reset to env var defaults.
        ctx: MCP context

    Returns:
        Confirmation message with active library details.
    """
    try:
        if library_type == "default":
            clear_active_library()
            ctx.info("Reset to default library configuration")
            return (
                "Switched back to default library configuration "
                f"(ZOTERO_LIBRARY_ID={os.getenv('ZOTERO_LIBRARY_ID', '0')}, "
                f"ZOTERO_LIBRARY_TYPE={os.getenv('ZOTERO_LIBRARY_TYPE', 'user')})"
            )

        error = validate_library_switch(library_id, library_type)
        if error:
            return error

        set_active_library(library_id, library_type)
        ctx.info(f"Switched to library {library_id} (type={library_type})")

        # Verify the switch works by making a test call
        try:
            zot = get_zotero_client()
            zot.add_parameters(limit=1)
            zot.items()
            return (
                f"Successfully switched to library **{library_id}** "
                f"(type={library_type}). All tools now operate on this library."
            )
        except Exception as e:
            # Roll back on failure
            clear_active_library()
            return (
                f"Error: Could not access library {library_id} "
                f"(type={library_type}): {e}. Reverted to default library."
            )

    except Exception as e:
        ctx.error(f"Error switching library: {str(e)}")
        return f"Error switching library: {str(e)}"


def validate_library_switch(library_id: str, library_type: str) -> str | None:
    """Validate a library switch request before applying it.

    Returns an error message string if the switch should be rejected,
    or None if the switch is valid and should proceed.
    """
    if library_type not in ("user", "group", "feed"):
        return f"Invalid library_type '{library_type}'. Must be 'user', 'group', or 'feed'."

    # In local mode, verify the library actually exists in the database
    local = is_local_mode()
    if local:
        try:
            from zotero_mcp.local_db import LocalZoteroReader

            reader = LocalZoteroReader()
            try:
                libraries = reader.get_libraries()
                if library_type == "group":
                    valid_ids = {
                        str(l["groupID"]) for l in libraries if l["type"] == "group"
                    }
                    if library_id not in valid_ids:
                        return (
                            f"Group '{library_id}' not found. "
                            f"Available groups: {', '.join(sorted(valid_ids))}"
                        )
                elif library_type == "feed":
                    valid_ids = {
                        str(l["libraryID"]) for l in libraries if l["type"] == "feed"
                    }
                    if library_id not in valid_ids:
                        return (
                            f"Feed with libraryID '{library_id}' not found. "
                            f"Available feeds: {', '.join(sorted(valid_ids))}"
                        )
            finally:
                reader.close()
        except Exception:
            pass  # If DB unavailable, skip validation — the test call will catch it

    return None


@mcp.tool(
    name="zotero_list_feeds",
    description="List all RSS feed subscriptions in your local Zotero installation. Shows feed names, URLs, item counts, and last check times. Local mode only.",
)
def list_feeds(*, ctx: Context) -> str:
    """
    List all RSS feed subscriptions from the local Zotero database.

    Returns:
        Markdown-formatted list of RSS feeds.
    """
    try:
        local = is_local_mode()
        if not local:
            return "RSS feeds are only accessible in local mode (ZOTERO_LOCAL=true)."

        ctx.info("Listing RSS feeds")
        from zotero_mcp.local_db import LocalZoteroReader

        reader = LocalZoteroReader()
        try:
            feeds = reader.get_feeds()
            if not feeds:
                return "No RSS feeds found in your Zotero installation."

            output = ["# RSS Feeds", ""]
            for feed in feeds:
                last_check = feed["lastCheck"] or "never"
                error = (
                    f" (error: {feed['lastCheckError']})"
                    if feed.get("lastCheckError")
                    else ""
                )
                output.append(f"### {feed['name']}")
                output.append(f"- **URL:** {feed['url']}")
                output.append(f"- **Items:** {feed['itemCount']}")
                output.append(f"- **Last checked:** {last_check}{error}")
                output.append(f"- **Library ID:** {feed['libraryID']}")
                output.append("")

            output.append(
                "Use `zotero_get_feed_items` with a feed's library ID to view its items."
            )
            return "\n".join(output)
        finally:
            reader.close()

    except Exception as e:
        ctx.error(f"Error listing feeds: {str(e)}")
        return f"Error listing feeds: {str(e)}"


@mcp.tool(
    name="zotero_get_feed_items",
    description="Get items from a specific RSS feed by its library ID. Use zotero_list_feeds first to find feed library IDs. Local mode only.",
)
def get_feed_items(
    library_id: int,
    limit: int = 20,
    *,
    ctx: Context,
) -> str:
    """
    Retrieve items from a specific RSS feed.

    Args:
        library_id: The libraryID of the feed (from zotero_list_feeds).
        limit: Maximum number of items to return.
        ctx: MCP context

    Returns:
        Markdown-formatted list of feed items.
    """
    try:
        local = is_local_mode()
        if not local:
            return (
                "RSS feed items are only accessible in local mode (ZOTERO_LOCAL=true)."
            )

        ctx.info(f"Fetching items from feed (libraryID={library_id})")
        from zotero_mcp.local_db import LocalZoteroReader

        reader = LocalZoteroReader()
        try:
            # Verify this is actually a feed
            feeds = reader.get_feeds()
            feed_info = next((f for f in feeds if f["libraryID"] == library_id), None)
            if not feed_info:
                valid_ids = [str(f["libraryID"]) for f in feeds]
                return (
                    f"No feed found with libraryID={library_id}. "
                    f"Valid feed IDs: {', '.join(valid_ids)}"
                )

            items = reader.get_feed_items(library_id, limit=limit)
            if not items:
                return f"No items found in feed '{feed_info['name']}'."

            output = [
                f"# Feed: {feed_info['name']}",
                f"**URL:** {feed_info['url']}",
                "",
            ]

            for item in items:
                read_status = "Read" if item.get("readTime") else "Unread"
                title = item.get("title") or "Untitled"
                output.append(f"### {title}")
                output.append(f"- **Status:** {read_status}")
                if item.get("creators"):
                    output.append(f"- **Authors:** {item['creators']}")
                if item.get("url"):
                    output.append(f"- **URL:** {item['url']}")
                output.append(f"- **Added:** {item.get('dateAdded', 'unknown')}")
                if item.get("abstract"):
                    abstract = clean_html(item["abstract"])
                    if len(abstract) > 200:
                        abstract = abstract[:200] + "..."
                    output.append(f"- **Abstract:** {abstract}")
                output.append("")

            return "\n".join(output)
        finally:
            reader.close()

    except Exception as e:
        ctx.error(f"Error fetching feed items: {str(e)}")
        return f"Error fetching feed items: {str(e)}"


# ---------------------------------------------------------------------------
# Helpers for zotero_add_to_library
# ---------------------------------------------------------------------------

_DOI_RE = re.compile(r"10\.\d{4,9}/[^\s]+")
_TAG_NORM_RE = re.compile(r"[\s\-_]+")


def _extract_doi_from_url(url: str) -> str | None:
    """Extract a DOI from a URL (doi.org, publisher sites, etc.)."""
    if not url:
        return None
    # Direct doi.org links
    if "doi.org/" in url:
        idx = url.index("doi.org/") + len("doi.org/")
        candidate = url[idx:].strip().rstrip("/")
        if candidate:
            return candidate
    # Embedded DOI in publisher URLs (Nature, Science, bioRxiv, etc.)
    m = _DOI_RE.search(url)
    return m.group(0).rstrip(".,;)") if m else None


def _parse_feed_creators(creators_str: str | None) -> list[dict]:
    """Parse 'Last, First; Last2, First2' into Zotero creator dicts."""
    if not creators_str:
        return []
    creators = []
    for part in creators_str.split(";"):
        part = part.strip()
        if not part:
            continue
        if "," in part:
            last, first = part.split(",", 1)
            creators.append(
                {
                    "creatorType": "author",
                    "firstName": first.strip(),
                    "lastName": last.strip(),
                }
            )
        else:
            creators.append(
                {
                    "creatorType": "author",
                    "firstName": "",
                    "lastName": part.strip(),
                }
            )
    return creators


def _map_s2_to_zotero(
    paper: dict, template: dict, feed_item: dict | None = None
) -> dict:
    """Map Semantic Scholar paper data onto a Zotero item template.

    Falls back to feed_item metadata for any field S2 doesn't provide.
    """
    fi = feed_item or {}

    # --- Title ---
    template["title"] = (
        paper.get("title") or fi.get("title") or template.get("title", "")
    )

    # --- Abstract ---
    abstract = paper.get("abstract") or ""
    if not abstract and fi.get("abstract"):
        abstract = clean_html(fi["abstract"])
    template["abstractNote"] = abstract

    # --- Creators ---
    s2_authors = paper.get("authors") or []
    if s2_authors:
        creators = []
        for author in s2_authors:
            name = (author.get("name") or "").strip()
            if not name:
                continue
            # Split "First Middle Last" — last token is lastName, rest is firstName
            parts = name.rsplit(" ", 1)
            if len(parts) == 2:
                creators.append(
                    {
                        "creatorType": "author",
                        "firstName": parts[0],
                        "lastName": parts[1],
                    }
                )
            else:
                creators.append(
                    {"creatorType": "author", "firstName": "", "lastName": name}
                )
        template["creators"] = creators
    elif fi.get("creators"):
        template["creators"] = _parse_feed_creators(fi["creators"])

    # --- DOI ---
    ext_ids = paper.get("externalIds") or {}
    doi = ext_ids.get("DOI") or fi.get("doi") or ""
    template["DOI"] = doi

    # --- Date ---
    template["date"] = (
        paper.get("publicationDate")
        or (str(paper["year"]) if paper.get("year") else "")
        or fi.get("dateAdded", "")
    )

    # --- Journal / venue ---
    journal = paper.get("journal") or {}
    template["publicationTitle"] = journal.get("name") or paper.get("venue") or ""
    if journal.get("volume"):
        template["volume"] = journal["volume"]
    if journal.get("pages"):
        template["pages"] = journal["pages"]

    # --- URL ---
    template["url"] = fi.get("url") or ""

    # --- Extra IDs (PMID, arXiv, etc.) ---
    extra_parts = []
    if ext_ids.get("PubMed"):
        extra_parts.append(f"PMID: {ext_ids['PubMed']}")
    if ext_ids.get("ArXiv"):
        extra_parts.append(f"arXiv: {ext_ids['ArXiv']}")
    if ext_ids.get("PubMedCentral"):
        extra_parts.append(f"PMCID: {ext_ids['PubMedCentral']}")
    if extra_parts:
        template["extra"] = "\n".join(extra_parts)

    return template


def _get_write_client() -> "zotero.Zotero | None":
    """Return a *web-API* Zotero client for write operations.

    In local mode the default pyzotero client is read-only (the local HTTP
    server does not support POST/PATCH/DELETE).  If ZOTERO_API_KEY is set we
    can create a web-API client instead.  Returns *None* when no API key is
    available.
    """
    from pyzotero import zotero

    api_key = os.getenv("ZOTERO_API_KEY")
    if not api_key:
        return None
    # Try to discover the web library ID.
    library_id = os.getenv("ZOTERO_LIBRARY_ID")
    library_type = os.getenv("ZOTERO_LIBRARY_TYPE", "user")
    if not library_id or library_id == "0":
        # Attempt to read the real user ID from the local database.
        try:
            from zotero_mcp.local_db import LocalZoteroReader

            reader = LocalZoteroReader()
            conn = reader._get_connection()
            row = conn.execute(
                "SELECT value FROM settings WHERE setting='account' AND key='userID'"
            ).fetchone()
            reader.close()
            if row:
                library_id = str(row["value"])
        except Exception:
            pass
    if not library_id or library_id == "0":
        return None
    return zotero.Zotero(library_id=library_id, library_type=library_type, api_key=api_key)


def _resolve_connector_collection_id(collection_key: str) -> str | None:
    """Resolve a Zotero collection key (e.g. ``CEKUW9JD``) to the connector
    internal ID (e.g. ``C38``) used by ``/connector/updateSession``."""
    import httpx

    try:
        resp = httpx.post(
            "http://localhost:23119/connector/getSelectedCollection",
            json={},
            timeout=5,
        )
        if resp.status_code != 200:
            return None
        # Walk the local database to map key → internal connector ID.
        # The connector returns targets with numeric IDs like "C38" but not
        # collection keys.  We query SQLite for the mapping instead.
        from zotero_mcp.local_db import LocalZoteroReader

        reader = LocalZoteroReader()
        conn = reader._get_connection()
        row = conn.execute(
            "SELECT collectionID FROM collections WHERE key = ?",
            (collection_key,),
        ).fetchone()
        reader.close()
        if row:
            return f"C{row['collectionID']}"
        return None
    except Exception:
        return None


def _create_item_via_connector(
    template: dict, collection_key: str | None = None
) -> str | None:
    """Create a Zotero item via the local connector API.

    Uses a two-step session pattern:
      1. ``POST /connector/saveItems`` — creates the item.
      2. ``POST /connector/updateSession`` — moves it to *collection_key*.

    Returns the item key on success (found by searching after creation),
    or *None* on failure.
    """
    import httpx

    session_id = uuid.uuid4().hex
    payload: dict = {
        "sessionID": session_id,
        "items": [template],
        "uri": template.get("url") or "https://zotero-mcp.local",
    }
    try:
        resp = httpx.post(
            "http://localhost:23119/connector/saveItems",
            json=payload,
            timeout=15,
        )
        if resp.status_code != 201:
            return None
    except Exception:
        return None

    # Step 2: assign to collection via updateSession
    if collection_key:
        target_id = _resolve_connector_collection_id(collection_key)
        if target_id:
            try:
                httpx.post(
                    "http://localhost:23119/connector/updateSession",
                    json={"sessionID": session_id, "target": target_id},
                    timeout=5,
                )
            except Exception:
                pass  # non-fatal; item was still created

    # The connector returns 201 with an empty body, so we search for the
    # created item by title to obtain its key.
    try:
        zot = get_zotero_client()
        title = template.get("title", "")
        if title:
            hits = zot.items(
                q=title,
                qmode="titleCreatorYear",
                limit=5,
                sort="dateAdded",
                direction="desc",
            )
            for hit in hits:
                if hit.get("data", {}).get("title") == title:
                    return hit["key"]
        return None
    except Exception:
        return None


def _download_pdf(pdf_url: str) -> str | None:
    """Download a PDF from *pdf_url* to a temp file; return path or None."""
    import tempfile

    import httpx

    try:
        with httpx.Client(follow_redirects=True, timeout=30) as client:
            resp = client.get(pdf_url)
            resp.raise_for_status()
            if "pdf" not in resp.headers.get("content-type", ""):
                return None
            tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
            tmp.write(resp.content)
            tmp.close()
            return tmp.name
    except Exception:
        return None


def _fetch_s2_paper(doi: str | None, title: str | None) -> dict | None:
    """Query Semantic Scholar for paper metadata.

    Tries DOI lookup first, then falls back to title search.
    Returns the paper dict or None on failure.
    """
    import httpx

    base = "https://api.semanticscholar.org/graph/v1/paper"
    fields = "title,authors,year,abstract,externalIds,openAccessPdf,venue,publicationDate,journal"

    try:
        with httpx.Client(timeout=15) as client:
            if doi:
                resp = client.get(f"{base}/DOI:{doi}", params={"fields": fields})
                if resp.status_code == 200:
                    return resp.json()
            if title:
                resp = client.get(
                    f"{base}/search",
                    params={"query": title, "fields": fields, "limit": "3"},
                )
                if resp.status_code == 200:
                    data = resp.json().get("data", [])
                    if data:
                        return data[0]
    except Exception:
        pass
    return None


@mcp.tool(
    name="zotero_add_to_library",
    description=(
        "Add a paper to your Zotero library from an RSS feed item, DOI, or URL. "
        "Enriches metadata via Semantic Scholar and optionally downloads the open-access PDF."
    ),
)
def add_to_library(
    title: str | None = None,
    feed_library_id: int | None = None,
    doi: str | None = None,
    url: str | None = None,
    collection_key: str | None = None,
    item_type: str = "journalArticle",
    download_pdf: bool = True,
    *,
    ctx: Context,
) -> str:
    """
    Add a paper to your Zotero library.

    Three input modes (at least one required):
      1. title + feed_library_id — locate a feed item by title
      2. doi — look up by DOI directly
      3. url — extract DOI from a URL

    Args:
        title: Title to search for in a feed.
        feed_library_id: Library ID of the RSS feed to search in.
        doi: DOI of the paper to add.
        url: URL of the paper (DOI will be extracted).
        collection_key: Optional collection key to add the item to.
        item_type: Zotero item type (default: journalArticle).
        download_pdf: Whether to attempt downloading the open-access PDF.
        ctx: MCP context.

    Returns:
        Summary of the created item.
    """
    warnings: list[str] = []
    feed_item: dict | None = None

    # --- 1. Validate inputs ---
    if not title and not doi and not url:
        return "Error: Provide at least one of: title (+feed_library_id), doi, or url."

    # --- 2. Feed mode: locate feed item ---
    if title and feed_library_id is not None:
        local = is_local_mode()
        if not local:
            return "Error: Feed mode requires ZOTERO_LOCAL=true."

        from zotero_mcp.local_db import LocalZoteroReader

        reader = LocalZoteroReader()
        try:
            matches = reader.search_feed_items_by_title(feed_library_id, title, limit=5)
            if not matches:
                recent = reader.get_feed_items(feed_library_id, limit=5)
                suggestions = [f'  - "{r.get("title", "?")}"' for r in recent]
                return f'No feed item matching "{title}". Recent titles:\n' + "\n".join(
                    suggestions
                )
            feed_item = matches[0]
            ctx.info(f"Found feed item: {feed_item.get('title')}")
            if not doi:
                doi = feed_item.get("doi")
            if not url:
                url = feed_item.get("url")
        finally:
            reader.close()

    # --- 3. Extract DOI from URL ---
    if not doi and url:
        doi = _extract_doi_from_url(url)
        if doi:
            ctx.info(f"Extracted DOI from URL: {doi}")

    # --- 4. Enrich via Semantic Scholar ---
    paper_title = title or (feed_item.get("title") if feed_item else None)
    s2_paper = _fetch_s2_paper(doi, paper_title)
    if not s2_paper:
        warnings.append("Semantic Scholar lookup failed; using available metadata.")

    # --- 5. Build Zotero item template ---
    local = is_local_mode()
    write_zot = _get_write_client() if local else None
    zot = write_zot or get_zotero_client()

    try:
        template = zot.item_template(item_type)
    except Exception:
        # Local Zotero API doesn't support /items/new — build manually
        template = {
            "itemType": item_type,
            "title": "",
            "creators": [],
            "abstractNote": "",
            "publicationTitle": "",
            "publisher": "",
            "date": "",
            "volume": "",
            "issue": "",
            "pages": "",
            "DOI": "",
            "url": "",
            "ISSN": "",
            "extra": "",
            "tags": [],
            "collections": [],
            "relations": {},
        }
    template = _map_s2_to_zotero(s2_paper or {}, template, feed_item)

    if collection_key:
        template["collections"] = [collection_key]

    # --- 6. Create item ---
    item_key: str | None = None
    created_title = template.get("title", "Untitled")
    use_connector = False

    if write_zot:
        # Web API client available — full write support
        ctx.info("Using web API for item creation")
        try:
            result = write_zot.create_items([template])
            if "success" in result and result["success"]:
                idx = next(iter(result["success"].keys()))
                item_key = result["success"][idx]
        except Exception as e:
            return f"Error creating item via web API: {e}"
    elif local:
        # Local-only mode: fall back to connector API (with session-based
        # collection assignment)
        ctx.info("Using Zotero connector API for item creation")
        use_connector = True
        item_key = _create_item_via_connector(template, collection_key)
    else:
        # Remote mode — use default client
        try:
            result = zot.create_items([template])
            if "success" in result and result["success"]:
                idx = next(iter(result["success"].keys()))
                item_key = result["success"][idx]
        except Exception as e:
            return f"Error creating item: {e}"

    if not item_key:
        return "Failed to create item. Check Zotero is running and accessible."

    # --- 7. Download & attach PDF (best-effort) ---
    pdf_status = "not attempted"
    if download_pdf:
        pdf_url = None
        if s2_paper:
            oa = s2_paper.get("openAccessPdf")
            if isinstance(oa, dict):
                pdf_url = oa.get("url")
        if pdf_url:
            ctx.info(f"Downloading PDF from {pdf_url}")
            pdf_path = _download_pdf(pdf_url)
            if pdf_path:
                attach_zot = write_zot or (zot if not use_connector else None)
                if attach_zot:
                    try:
                        attach_zot.attachment_simple([pdf_path], parentid=item_key)
                        pdf_status = "attached"
                    except Exception as e:
                        warnings.append(f"PDF attachment failed: {e}")
                        pdf_status = "download succeeded, attach failed"
                else:
                    warnings.append(
                        "PDF downloaded but cannot attach in local-only mode "
                        "(set ZOTERO_API_KEY for full support)."
                    )
                    pdf_status = "download succeeded, attach skipped (no API key)"
                try:
                    os.unlink(pdf_path)
                except OSError:
                    pass
            else:
                pdf_status = "download failed"
                warnings.append("Could not download PDF.")
        else:
            pdf_status = "no open-access PDF found"

    # --- 8. Return summary ---
    lines = [
        f"**Item created:** {created_title}",
        f"- **Key:** {item_key}",
    ]
    if doi:
        lines.append(f"- **DOI:** {doi}")
    if collection_key:
        lines.append(f"- **Collection:** {collection_key}")
    lines.append(f"- **PDF:** {pdf_status}")
    if warnings:
        lines.append("\n**Warnings:**")
        for w in warnings:
            lines.append(f"- {w}")
    return "\n".join(lines)


@mcp.tool(
    name="zotero_get_recent",
    description="Get recently added items to your Zotero library.",
)
def get_recent(limit: int | str = 10, *, ctx: Context) -> str:
    """
    Get recently added items to your Zotero library.

    Args:
        limit: Number of items to return
        ctx: MCP context

    Returns:
        Markdown-formatted list of recent items
    """
    try:
        ctx.info(f"Fetching {limit} recent items")
        zot = get_zotero_client()

        limit = parse_limit(limit)

        # Ensure limit is a reasonable number
        if limit <= 0:
            limit = 10
        elif limit > 100:
            limit = 100

        # Get recent items
        items = zot.items(limit=limit, sort="dateAdded", direction="desc")
        if not items:
            return "No items found in your Zotero library."

        # Format items as markdown
        output = [f"# {limit} Most Recently Added Items", ""]
        output.extend(
            format_item_list(
                items,
                include_abstract=False,
                include_tags=False,
                include_date_added=True,
            )
        )

        return "\n".join(output)

    except Exception as e:
        ctx.error(f"Error fetching recent items: {str(e)}")
        return f"Error fetching recent items: {str(e)}"


@mcp.tool(
    name="zotero_batch_update_tags",
    description="Batch update tags across multiple items matching a search query.",
)
def batch_update_tags(
    query: str,
    add_tags: list[str] | str | None = None,
    remove_tags: list[str] | str | None = None,
    limit: int | str = 50,
    *,
    ctx: Context,
) -> str:
    """
    Batch update tags across multiple items matching a search query.

    Args:
        query: Search query to find items to update
        add_tags: List of tags to add to matched items (can be list or JSON string)
        remove_tags: List of tags to remove from matched items (can be list or JSON string)
        limit: Maximum number of items to process
        ctx: MCP context

    Returns:
        Summary of the batch update
    """
    try:
        if not query:
            return "Error: Search query cannot be empty"

        if not add_tags and not remove_tags:
            return "Error: You must specify either tags to add or tags to remove"

        # Debug logging... commented out for now but could be useful in future.
        # ctx.info(f"add_tags type: {type(add_tags)}, value: {add_tags}")
        # ctx.info(f"remove_tags type: {type(remove_tags)}, value: {remove_tags}")

        # Handle case where add_tags might be a JSON string instead of list
        if add_tags and isinstance(add_tags, str):
            try:
                import json

                add_tags = json.loads(add_tags)
                ctx.info(f"Parsed add_tags from JSON string: {add_tags}")
            except json.JSONDecodeError:
                return (
                    f"Error: add_tags appears to be malformed JSON string: {add_tags}"
                )

        # Handle case where remove_tags might be a JSON string instead of list
        if remove_tags and isinstance(remove_tags, str):
            try:
                import json

                remove_tags = json.loads(remove_tags)
                ctx.info(f"Parsed remove_tags from JSON string: {remove_tags}")
            except json.JSONDecodeError:
                return f"Error: remove_tags appears to be malformed JSON string: {remove_tags}"

        ctx.info(f"Batch updating tags for items matching '{query}'")
        zot = get_zotero_client()

        limit = parse_limit(limit)

        # Search for items matching the query
        zot.add_parameters(q=query, limit=limit)
        items = zot.items()

        if not items:
            return f"No items found matching query: '{query}'"

        # Initialize counters
        updated_count = 0
        skipped_count = 0
        added_tag_counts = {tag: 0 for tag in (add_tags or [])}
        removed_tag_counts = {tag: 0 for tag in (remove_tags or [])}

        # Process each item
        for item in items:
            # Skip attachments if they were included in the results
            if item["data"].get("itemType") == "attachment":
                skipped_count += 1
                continue

            # Get current tags
            current_tags = item["data"].get("tags", [])
            current_tag_values = {t["tag"] for t in current_tags}

            # Track if this item needs to be updated
            needs_update = False

            # Process tags to remove
            if remove_tags:
                new_tags = []
                for tag_obj in current_tags:
                    tag = tag_obj["tag"]
                    if tag in remove_tags:
                        removed_tag_counts[tag] += 1
                        needs_update = True
                    else:
                        new_tags.append(tag_obj)
                current_tags = new_tags

            # Process tags to add
            if add_tags:
                for tag in add_tags:
                    if tag and tag not in current_tag_values:
                        current_tags.append({"tag": tag})
                        added_tag_counts[tag] += 1
                        needs_update = True

            # Update the item if needed
            # Since we are logging errors we might as well log the update.
            if needs_update:
                try:
                    item["data"]["tags"] = current_tags
                    ctx.info(
                        f"Updating item {item.get('key', 'unknown')} with tags: {current_tags}"
                    )
                    result = zot.update_item(item)
                    ctx.info(f"Update result: {result}")
                    updated_count += 1
                except Exception as e:
                    ctx.error(
                        f"Failed to update item {item.get('key', 'unknown')}: {str(e)}"
                    )
                    # Continue with other items instead of failing completely
                    skipped_count += 1
            else:
                skipped_count += 1

        # Format the response
        response = ["# Batch Tag Update Results", ""]
        response.append(f"Query: '{query}'")
        response.append(f"Items processed: {len(items)}")
        response.append(f"Items updated: {updated_count}")
        response.append(f"Items skipped: {skipped_count}")

        if add_tags:
            response.append("\n## Tags Added")
            for tag, count in added_tag_counts.items():
                response.append(f"- `{tag}`: {count} items")

        if remove_tags:
            response.append("\n## Tags Removed")
            for tag, count in removed_tag_counts.items():
                response.append(f"- `{tag}`: {count} items")

        return "\n".join(response)

    except Exception as e:
        ctx.error(f"Error in batch tag update: {str(e)}")
        return f"Error in batch tag update: {str(e)}"


@mcp.tool(
    name="zotero_rename_tag",
    description="Rename a tag across all items in your Zotero library.",
)
def rename_tag(
    old_tag: str,
    new_tag: str,
    *,
    ctx: Context,
) -> str:
    """
    Rename a tag across all items in the library.

    For each item that has the old tag, the old tag is removed and the new tag
    is added (unless the item already carries the new tag).

    Args:
        old_tag: The tag name to rename from
        new_tag: The tag name to rename to
        ctx: MCP context

    Returns:
        Summary of the rename operation
    """
    try:
        if not old_tag or not new_tag:
            return "Error: Both old_tag and new_tag must be provided"
        if old_tag == new_tag:
            return "Error: old_tag and new_tag are the same"

        ctx.info(f"Renaming tag '{old_tag}' → '{new_tag}'")
        zot = get_zotero_client()

        # Find all items with the old tag
        items = zot.everything(zot.items(tag=old_tag))
        if not items:
            return f"No items found with tag '{old_tag}'"

        updated = 0
        for item in items:
            if item["data"].get("itemType") == "attachment":
                continue
            tags = item["data"].get("tags", [])
            tag_values = {t["tag"] for t in tags}
            if old_tag not in tag_values:
                continue

            new_tags = [t for t in tags if t["tag"] != old_tag]
            if new_tag not in tag_values:
                new_tags.append({"tag": new_tag})
            item["data"]["tags"] = new_tags
            try:
                zot.update_item(item)
                updated += 1
            except Exception as e:
                ctx.error(f"Failed to update item {item.get('key', '?')}: {e}")

        return (
            f"# Tag Renamed\n\n"
            f"Renamed `{old_tag}` → `{new_tag}` across **{updated}** items."
        )
    except Exception as e:
        ctx.error(f"Error renaming tag: {str(e)}")
        return f"Error renaming tag: {str(e)}"


@mcp.tool(
    name="zotero_delete_tag",
    description="Remove a tag from all items in your Zotero library.",
)
def delete_tag(
    tag: str,
    *,
    ctx: Context,
) -> str:
    """
    Delete a tag from every item in the library.

    Args:
        tag: The tag name to delete
        ctx: MCP context

    Returns:
        Summary of the delete operation
    """
    try:
        if not tag:
            return "Error: tag must be provided"

        ctx.info(f"Deleting tag '{tag}' from all items")
        zot = get_zotero_client()

        items = zot.everything(zot.items(tag=tag))
        if not items:
            return f"No items found with tag '{tag}'"

        updated = 0
        for item in items:
            if item["data"].get("itemType") == "attachment":
                continue
            tags = item["data"].get("tags", [])
            new_tags = [t for t in tags if t["tag"] != tag]
            if len(new_tags) == len(tags):
                continue
            item["data"]["tags"] = new_tags
            try:
                zot.update_item(item)
                updated += 1
            except Exception as e:
                ctx.error(f"Failed to update item {item.get('key', '?')}: {e}")

        return f"# Tag Deleted\n\n" f"Removed tag `{tag}` from **{updated}** items."
    except Exception as e:
        ctx.error(f"Error deleting tag: {str(e)}")
        return f"Error deleting tag: {str(e)}"


@mcp.tool(
    name="zotero_merge_tags",
    description="Merge multiple tags into one target tag across your Zotero library.",
)
def merge_tags(
    source_tags: list[str],
    target_tag: str,
    *,
    ctx: Context,
) -> str:
    """
    Merge several source tags into a single target tag.

    For every item carrying any of the source tags, those tags are removed and
    the target tag is added (if not already present).

    Args:
        source_tags: List of tag names to merge from
        target_tag: The tag name to merge into
        ctx: MCP context

    Returns:
        Summary of the merge operation
    """
    try:
        if not source_tags or not target_tag:
            return "Error: source_tags and target_tag must be provided"

        ctx.info(f"Merging tags {source_tags} → '{target_tag}'")
        zot = get_zotero_client()

        source_set = set(source_tags)
        updated = 0
        seen_keys: set[str] = set()

        for src in source_tags:
            items = zot.everything(zot.items(tag=src))
            for item in items:
                key = item.get("key", "")
                if key in seen_keys:
                    continue
                seen_keys.add(key)

                if item["data"].get("itemType") == "attachment":
                    continue

                tags = item["data"].get("tags", [])
                tag_values = {t["tag"] for t in tags}
                if not tag_values & source_set:
                    continue

                new_tags = [t for t in tags if t["tag"] not in source_set]
                if target_tag not in tag_values - source_set:
                    new_tags.append({"tag": target_tag})
                item["data"]["tags"] = new_tags
                try:
                    zot.update_item(item)
                    updated += 1
                except Exception as e:
                    ctx.error(f"Failed to update item {key}: {e}")

        return (
            f"# Tags Merged\n\n"
            f"Merged {', '.join(f'`{s}`' for s in source_tags)} → `{target_tag}` "
            f"across **{updated}** items."
        )
    except Exception as e:
        ctx.error(f"Error merging tags: {str(e)}")
        return f"Error merging tags: {str(e)}"


@mcp.tool(
    name="zotero_get_tag_statistics",
    description="Get tag usage statistics: counts, rarely-used tags, and similar tag names that may be merge candidates.",
)
def get_tag_statistics(
    top_n: int = 20,
    rare_threshold: int = 2,
    similarity_threshold: float = 0.8,
    *,
    ctx: Context,
) -> str:
    """
    Analyse tag usage across the library.

    Args:
        top_n: Number of most-used tags to show
        rare_threshold: Tags with this many items or fewer are flagged as rare
        similarity_threshold: Minimum similarity ratio (0-1) for merge suggestions
        ctx: MCP context

    Returns:
        Markdown-formatted tag statistics report
    """
    try:
        ctx.info("Gathering tag statistics")
        zot = get_zotero_client()

        # Fetch all items to count tags
        items = zot.everything(zot.items())
        tag_counts: dict[str, int] = {}
        for item in items:
            if item["data"].get("itemType") == "attachment":
                continue
            for t in item["data"].get("tags", []):
                name = t["tag"]
                tag_counts[name] = tag_counts.get(name, 0) + 1

        if not tag_counts:
            return "No tags found in your Zotero library."

        sorted_tags = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)
        total_tags = len(sorted_tags)

        output = ["# Tag Statistics", ""]
        output.append(f"**Total unique tags:** {total_tags}")
        output.append(f"**Total tag assignments:** {sum(tag_counts.values())}")
        output.append("")

        # Most-used tags
        output.append(f"## Top {min(top_n, total_tags)} Tags")
        for name, count in sorted_tags[:top_n]:
            output.append(f"- `{name}` — {count} items")

        # Rarely-used tags
        rare = [(n, c) for n, c in sorted_tags if c <= rare_threshold]
        if rare:
            output.append(
                f"\n## Rarely-Used Tags (≤ {rare_threshold} items) — {len(rare)} tags"
            )
            for name, count in rare:
                output.append(f"- `{name}` — {count} items")

        merge_suggestions = find_similar_tags(
            list(tag_counts.keys()), similarity_threshold
        )
        if merge_suggestions:
            output.append("\n## Potential Merge Candidates")
            for tag_a, tag_b, score in merge_suggestions:
                output.append(
                    f"- `{tag_a}` ↔ `{tag_b}` "
                    f"(similarity: {score:.0%}, items: {tag_counts[tag_a]}+{tag_counts[tag_b]})"
                )

        return "\n".join(output)
    except Exception as e:
        ctx.error(f"Error getting tag statistics: {str(e)}")
        return f"Error getting tag statistics: {str(e)}"


def find_similar_tags(
    tags: list[str], threshold: float
) -> list[tuple[str, str, float]]:
    """Return pairs of tags whose names are similar enough to be merge candidates.

    Uses case-insensitive comparison and ``difflib.SequenceMatcher`` to find
    tags that look like duplicates (e.g. "Machine Learning" vs "machine-learning").

    Returns a list of (tag_a, tag_b, similarity_score) tuples, sorted by
    score descending.  At most 50 pairs are returned to keep output readable.
    """
    from difflib import SequenceMatcher

    # Normalise: lowercase, collapse whitespace/hyphens/underscores
    def _norm(t: str) -> str:
        return _TAG_NORM_RE.sub(" ", t.strip().lower())

    normed = {t: _norm(t) for t in tags}

    # Exact-match-after-normalisation is always a candidate (score = 1.0),
    # so group by normalised form first for an O(n) pass.
    from collections import defaultdict

    groups: dict[str, list[str]] = defaultdict(list)
    for tag, norm in normed.items():
        groups[norm].append(tag)

    results: list[tuple[str, str, float]] = []

    # Pairs that are identical after normalisation
    for norm, members in groups.items():
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                results.append((members[i], members[j], 1.0))

    # Pairwise fuzzy comparison on unique normalised forms
    unique_norms = list(groups.keys())
    for i in range(len(unique_norms)):
        for j in range(i + 1, len(unique_norms)):
            a, b = unique_norms[i], unique_norms[j]
            # Quick length-based prune: very different lengths rarely match
            if max(len(a), len(b)) > 2 * min(len(a), len(b)):
                continue
            score = SequenceMatcher(None, a, b).ratio()
            if score >= threshold:
                tag_a = groups[a][0]
                tag_b = groups[b][0]
                results.append((tag_a, tag_b, score))

    results.sort(key=lambda x: x[2], reverse=True)
    return results[:50]


@mcp.tool(
    name="zotero_suggest_organization",
    description="Analyze your Zotero library and suggest organizational improvements: unfiled items, small collections, and tag cleanup opportunities.",
)
def suggest_organization(
    *,
    ctx: Context,
) -> str:
    """
    Analyze the library and return actionable organization suggestions.

    Returns:
        Markdown-formatted list of suggestions
    """
    try:
        ctx.info("Analyzing library organization")
        zot = get_zotero_client()

        output = ["# Library Organization Suggestions", ""]

        # 1. Unfiled items — items not in any collection
        all_items = zot.everything(zot.items(itemType="-attachment"))
        unfiled = [item for item in all_items if not item["data"].get("collections")]
        output.append(f"## Unfiled Items — {len(unfiled)} of {len(all_items)} items")
        if unfiled:
            for item in unfiled[:15]:
                title = item["data"].get("title", "Untitled")
                key = item.get("key", "")
                output.append(f"- `{key}` {title}")
            if len(unfiled) > 15:
                output.append(f"- … and {len(unfiled) - 15} more")
        else:
            output.append("All items are filed in at least one collection.")
        output.append("")

        # 2. Small collections (potential merge/cleanup candidates)
        collections = zot.collections()
        small: list[tuple[str, str, int]] = []
        for coll in collections:
            num = coll.get("meta", {}).get("numItems", 0)
            if num <= 3:
                small.append((coll["data"].get("name", "?"), coll["key"], num))
        output.append(f"## Small Collections (≤ 3 items) — {len(small)} collections")
        if small:
            for name, key, count in sorted(small, key=lambda x: x[2]):
                output.append(f"- **{name}** (`{key}`) — {count} items")
        else:
            output.append("No very small collections found.")
        output.append("")

        # 3. Untagged items
        untagged = [item for item in all_items if not item["data"].get("tags")]
        output.append(f"## Untagged Items — {len(untagged)} items")
        if untagged:
            for item in untagged[:10]:
                title = item["data"].get("title", "Untitled")
                key = item.get("key", "")
                output.append(f"- `{key}` {title}")
            if len(untagged) > 10:
                output.append(f"- … and {len(untagged) - 10} more")
        else:
            output.append("All items have at least one tag.")
        output.append("")

        # 4. Summary
        output.append("## Quick Actions")
        if unfiled:
            output.append(
                f"- Use `zotero_add_items_to_collection` to file the {len(unfiled)} unfiled items"
            )
        if small:
            output.append(
                f"- Consider merging or deleting the {len(small)} small collections"
            )
        if untagged:
            output.append(
                f"- Use `zotero_batch_update_tags` to tag the {len(untagged)} untagged items"
            )
        output.append(
            "- Run `zotero_get_tag_statistics` for detailed tag cleanup suggestions"
        )

        return "\n".join(output)
    except Exception as e:
        ctx.error(f"Error analyzing organization: {str(e)}")
        return f"Error analyzing organization: {str(e)}"


@mcp.tool(
    name="zotero_advanced_search",
    description="Perform an advanced search with multiple criteria.",
)
def advanced_search(
    conditions: list[dict[str, str]],
    join_mode: Literal["all", "any"] = "all",
    sort_by: str | None = None,
    sort_direction: Literal["asc", "desc"] = "asc",
    limit: int | str = 50,
    *,
    ctx: Context,
) -> str:
    """
    Perform an advanced search with multiple criteria.

    Args:
        conditions: List of search condition dictionaries, each containing:
                   - field: The field to search (title, creator, date, tag, etc.)
                   - operation: The operation to perform (is, isNot, contains, etc.)
                   - value: The value to search for
        join_mode: Whether all conditions must match ("all") or any condition can match ("any")
        sort_by: Field to sort by (dateAdded, dateModified, title, creator, etc.)
        sort_direction: Direction to sort (asc or desc)
        limit: Maximum number of results to return
        ctx: MCP context

    Returns:
        Markdown-formatted search results
    """
    try:
        if not conditions:
            return "Error: No search conditions provided"

        ctx.info(f"Performing advanced search with {len(conditions)} conditions")
        zot = get_zotero_client()

        # Prepare search parameters
        params = {}

        # Add sorting parameters if specified
        if sort_by:
            params["sort"] = sort_by
            params["direction"] = sort_direction

        limit = parse_limit(limit)

        # Add limit parameter
        params["limit"] = limit

        # Build search conditions
        search_conditions = []
        for i, condition in enumerate(conditions):
            if (
                "field" not in condition
                or "operation" not in condition
                or "value" not in condition
            ):
                return f"Error: Condition {i+1} is missing required fields (field, operation, value)"

            # Map common field names to Zotero API fields if needed
            field = condition["field"]
            operation = condition["operation"]
            value = condition["value"]

            # Handle special fields
            if field == "author" or field == "creator":
                field = "creator"
            elif field == "year":
                field = "date"
                # Convert year to partial date format for matching
                value = str(value)

            search_conditions.append(
                {"condition": field, "operator": operation, "value": value}
            )

        # Add join mode condition
        search_conditions.append(
            {"condition": "joinMode", "operator": join_mode, "value": ""}
        )

        # Create a saved search
        search_name = f"temp_search_{uuid.uuid4().hex[:8]}"
        saved_search = zot.saved_search(search_name, search_conditions)

        # Extract the search key from the result
        if not saved_search.get("success"):
            return f"Error creating saved search: {saved_search.get('failed', 'Unknown error')}"

        search_key = next(iter(saved_search.get("success", {}).values()), None)

        # Execute the saved search
        try:
            results = zot.collection_items(search_key)
        finally:
            # Clean up the temporary saved search
            try:
                zot.delete_saved_search([search_key])
            except Exception as cleanup_error:
                ctx.warn(f"Error cleaning up saved search: {str(cleanup_error)}")

        # Format the results
        if not results:
            return "No items found matching the search criteria."

        output = ["# Advanced Search Results", ""]
        output.append(f"Found {len(results)} items matching the search criteria:")
        output.append("")

        # Add search criteria summary
        output.append("## Search Criteria")
        output.append(f"Join mode: {join_mode.upper()}")

        for i, condition in enumerate(conditions, 1):
            output.append(
                f"{i}. {condition['field']} {condition['operation']} \"{condition['value']}\""
            )

        output.append("")

        # Format results
        output.append("## Results")
        output.extend(
            format_item_list(results, heading_level=3, abstract_max_length=150)
        )

        return "\n".join(output)

    except Exception as e:
        ctx.error(f"Error in advanced search: {str(e)}")
        return f"Error in advanced search: {str(e)}"


@mcp.tool(
    name="zotero_get_annotations",
    description="Get all annotations for a specific item or across your entire Zotero library.",
)
def get_annotations(
    item_key: str | None = None,
    use_pdf_extraction: bool = False,
    limit: int | str | None = None,
    *,
    ctx: Context,
) -> str:
    """
    Get annotations from your Zotero library.

    Args:
        item_key: Optional Zotero item key/ID to filter annotations by parent item
        use_pdf_extraction: Whether to attempt direct PDF extraction as a fallback
        limit: Maximum number of annotations to return
        ctx: MCP context

    Returns:
        Markdown-formatted list of annotations
    """
    try:
        # Initialize Zotero client
        zot = get_zotero_client()

        # Prepare annotations list
        annotations = []
        parent_title = "Untitled Item"

        # If an item key is provided, use specialized retrieval
        if item_key:
            # First, verify the item exists and get its details
            try:
                parent = zot.item(item_key)
                parent_title = parent["data"].get("title", "Untitled Item")
                ctx.info(f"Fetching annotations for item: {parent_title}")
            except Exception:
                return f"Error: No item found with key: {item_key}"

            # Initialize annotation sources
            better_bibtex_annotations = []
            zotero_api_annotations = []
            pdf_annotations = []

            # Try Better BibTeX method (local Zotero only)
            if is_local_mode():
                try:
                    # Import Better BibTeX dependencies
                    from zotero_mcp.better_bibtex_client import (
                        ZoteroBetterBibTexAPI,
                        get_color_category,
                        process_annotation,
                    )

                    # Initialize Better BibTeX client
                    bibtex = ZoteroBetterBibTexAPI()

                    # Check if Zotero with Better BibTeX is running
                    if bibtex.is_zotero_running():
                        # Extract citation key
                        citation_key = None

                        # Try to find citation key in Extra field
                        try:
                            extra_field = parent["data"].get("extra", "")
                            for line in extra_field.split("\n"):
                                if line.lower().startswith("citation key:"):
                                    citation_key = line.replace(
                                        "Citation Key:", ""
                                    ).strip()
                                    break
                                elif line.lower().startswith("citationkey:"):
                                    citation_key = line.replace(
                                        "citationkey:", ""
                                    ).strip()
                                    break
                        except Exception as e:
                            ctx.warn(
                                f"Error extracting citation key from Extra field: {e}"
                            )

                        # Fallback to searching by title if no citation key found
                        if not citation_key:
                            title = parent["data"].get("title", "")
                            try:
                                if title:
                                    # Use the search_citekeys method
                                    search_results = bibtex.search_citekeys(title)

                                    # Find the matching item
                                    for result in search_results:
                                        ctx.info(f"Checking result: {result}")

                                        # Try to match with item key if possible
                                        if result.get("citekey"):
                                            citation_key = result["citekey"]
                                            break
                            except Exception as e:
                                ctx.warn(f"Error searching for citation key: {e}")

                        # Process annotations if citation key found
                        if citation_key:
                            try:
                                # Determine library
                                library = "*"  # Default all libraries
                                search_results = bibtex._make_request(
                                    "item.search", [citation_key]
                                )
                                if search_results:
                                    matched_item = next(
                                        (
                                            item
                                            for item in search_results
                                            if item.get("citekey") == citation_key
                                        ),
                                        None,
                                    )
                                    if matched_item:
                                        library = matched_item.get("library", "*")

                                # Get attachments
                                attachments = bibtex.get_attachments(
                                    citation_key, library
                                )

                                # Process annotations from attachments
                                for attachment in attachments:
                                    annotations = (
                                        bibtex.get_annotations_from_attachment(
                                            attachment
                                        )
                                    )

                                    for anno in annotations:
                                        processed = process_annotation(anno, attachment)
                                        if processed:
                                            # Create Zotero-like annotation object
                                            bibtex_anno = {
                                                "key": processed.get("id", ""),
                                                "data": {
                                                    "itemType": "annotation",
                                                    "annotationType": processed.get(
                                                        "type", "highlight"
                                                    ),
                                                    "annotationText": processed.get(
                                                        "annotatedText", ""
                                                    ),
                                                    "annotationComment": processed.get(
                                                        "comment", ""
                                                    ),
                                                    "annotationColor": processed.get(
                                                        "color", ""
                                                    ),
                                                    "parentItem": item_key,
                                                    "tags": [],
                                                    "_pdf_page": processed.get(
                                                        "page", 0
                                                    ),
                                                    "_pageLabel": processed.get(
                                                        "pageLabel", ""
                                                    ),
                                                    "_attachment_title": attachment.get(
                                                        "title", ""
                                                    ),
                                                    "_color_category": get_color_category(
                                                        processed.get("color", "")
                                                    ),
                                                    "_from_better_bibtex": True,
                                                },
                                            }
                                            better_bibtex_annotations.append(
                                                bibtex_anno
                                            )

                                ctx.info(
                                    f"Retrieved {len(better_bibtex_annotations)} annotations via Better BibTeX"
                                )
                            except Exception as e:
                                ctx.warn(
                                    f"Error processing Better BibTeX annotations: {e}"
                                )
                except Exception as bibtex_error:
                    ctx.warn(f"Error initializing Better BibTeX: {bibtex_error}")

            # Fallback to Zotero API annotations
            if not better_bibtex_annotations:
                try:
                    # Get child annotations via Zotero API
                    children = zot.children(item_key)
                    zotero_api_annotations = [
                        item
                        for item in children
                        if item.get("data", {}).get("itemType") == "annotation"
                    ]
                    ctx.info(
                        f"Retrieved {len(zotero_api_annotations)} annotations via Zotero API"
                    )
                except Exception as api_error:
                    ctx.warn(f"Error retrieving Zotero API annotations: {api_error}")

            # PDF Extraction fallback
            if use_pdf_extraction and not (
                better_bibtex_annotations or zotero_api_annotations
            ):
                try:
                    import tempfile
                    import uuid

                    from zotero_mcp.pdfannots_helper import (
                        ensure_pdfannots_installed,
                        extract_annotations_from_pdf,
                    )

                    # Ensure PDF annotation tool is installed
                    if ensure_pdfannots_installed():
                        # Get PDF attachments
                        children = zot.children(item_key)
                        pdf_attachments = [
                            item
                            for item in children
                            if item.get("data", {}).get("contentType")
                            == "application/pdf"
                        ]

                        # Extract annotations from PDFs
                        for attachment in pdf_attachments:
                            with tempfile.TemporaryDirectory() as tmpdir:
                                att_key = attachment.get("key", "")
                                file_path = os.path.join(tmpdir, f"{att_key}.pdf")
                                zot.dump(att_key, file_path)

                                if os.path.exists(file_path):
                                    extracted = extract_annotations_from_pdf(
                                        file_path, tmpdir
                                    )

                                    for ext in extracted:
                                        # Skip empty annotations
                                        if not ext.get("annotatedText") and not ext.get(
                                            "comment"
                                        ):
                                            continue

                                        # Create Zotero-like annotation object
                                        pdf_anno = {
                                            "key": f"pdf_{att_key}_{ext.get('id', uuid.uuid4().hex[:8])}",
                                            "data": {
                                                "itemType": "annotation",
                                                "annotationType": ext.get(
                                                    "type", "highlight"
                                                ),
                                                "annotationText": ext.get(
                                                    "annotatedText", ""
                                                ),
                                                "annotationComment": ext.get(
                                                    "comment", ""
                                                ),
                                                "annotationColor": ext.get("color", ""),
                                                "parentItem": item_key,
                                                "tags": [],
                                                "_pdf_page": ext.get("page", 0),
                                                "_from_pdf_extraction": True,
                                                "_attachment_title": attachment.get(
                                                    "data", {}
                                                ).get("title", "PDF"),
                                            },
                                        }

                                        # Handle image annotations
                                        if ext.get("type") == "image" and ext.get(
                                            "imageRelativePath"
                                        ):
                                            pdf_anno["data"]["_image_path"] = (
                                                os.path.join(
                                                    tmpdir, ext.get("imageRelativePath")
                                                )
                                            )

                                        pdf_annotations.append(pdf_anno)

                        ctx.info(
                            f"Retrieved {len(pdf_annotations)} annotations via PDF extraction"
                        )
                except Exception as pdf_error:
                    ctx.warn(f"Error during PDF annotation extraction: {pdf_error}")

            # Combine annotations from all sources
            annotations = (
                better_bibtex_annotations + zotero_api_annotations + pdf_annotations
            )

        else:
            # Retrieve all annotations in the library
            limit = parse_limit(limit)
            zot.add_parameters(itemType="annotation", limit=limit or 50)
            annotations = zot.everything(zot.items())

        # Handle no annotations found
        if not annotations:
            return f"No annotations found{f' for item: {parent_title}' if item_key else ''}."

        # Generate markdown output
        output = [f"# Annotations{f' for: {parent_title}' if item_key else ''}", ""]

        for i, anno in enumerate(annotations, 1):
            data = anno.get("data", {})

            # Annotation details
            anno_type = data.get("annotationType", "Unknown type")
            anno_text = data.get("annotationText", "")
            anno_comment = data.get("annotationComment", "")
            anno_color = data.get("annotationColor", "")
            anno_key = anno.get("key", "")

            # Parent item context for library-wide retrieval
            parent_info = ""
            if not item_key and (parent_key := data.get("parentItem")):
                try:
                    parent = zot.item(parent_key)
                    parent_title = parent["data"].get("title", "Untitled")
                    parent_info = f' (from "{parent_title}")'
                except Exception:
                    parent_info = f" (parent key: {parent_key})"

            # Annotation source details
            source_info = ""
            if data.get("_from_better_bibtex", False):
                source_info = " (extracted via Better BibTeX)"
            elif data.get("_from_pdf_extraction", False):
                source_info = " (extracted directly from PDF)"

            # Attachment context
            attachment_info = ""
            if "_attachment_title" in data and data["_attachment_title"]:
                attachment_info = f" in {data['_attachment_title']}"

            # Build markdown annotation entry
            output.append(
                f"## Annotation {i}{parent_info}{attachment_info}{source_info}"
            )
            output.append(f"**Type:** {anno_type}")
            output.append(f"**Key:** {anno_key}")

            # Color information
            if anno_color:
                output.append(f"**Color:** {anno_color}")
                if "_color_category" in data and data["_color_category"]:
                    output.append(f"**Color Category:** {data['_color_category']}")

            # Page information
            if "_pdf_page" in data:
                label = data.get("_pageLabel", str(data["_pdf_page"]))
                output.append(f"**Page:** {data['_pdf_page']} (Label: {label})")

            # Annotation content
            if anno_text:
                output.append(f"**Text:** {anno_text}")

            if anno_comment:
                output.append(f"**Comment:** {anno_comment}")

            # Image annotation
            if "_image_path" in data and os.path.exists(data["_image_path"]):
                output.append(
                    "**Image:** This annotation includes an image (not displayed in this interface)"
                )

            # Tags
            if tags := data.get("tags"):
                tag_list = [f"`{tag['tag']}`" for tag in tags]
                if tag_list:
                    output.append(f"**Tags:** {' '.join(tag_list)}")

            output.append("")  # Empty line between annotations

        return "\n".join(output)

    except Exception as e:
        ctx.error(f"Error fetching annotations: {str(e)}")
        return f"Error fetching annotations: {str(e)}"


@mcp.tool(
    name="zotero_get_notes",
    description="Retrieve notes from your Zotero library, with options to filter by parent item.",
)
def get_notes(
    item_key: str | None = None,
    limit: int | str | None = 20,
    truncate: bool = True,
    *,
    ctx: Context,
) -> str:
    """
    Retrieve notes from your Zotero library.

    Args:
        item_key: Optional Zotero item key/ID to filter notes by parent item
        limit: Maximum number of notes to return
        truncate: Whether to truncate long notes for display
        ctx: MCP context

    Returns:
        Markdown-formatted list of notes
    """
    try:
        ctx.info(f"Fetching notes{f' for item {item_key}' if item_key else ''}")
        zot = get_zotero_client()

        # Prepare search parameters
        params = {"itemType": "note"}

        limit = parse_limit(limit)

        # Get notes
        notes = []
        if item_key:
            notes = (
                zot.children(item_key, **params)
                if not limit
                else zot.children(item_key, limit=limit, **params)
            )
        else:
            notes = (
                zot.items(**params) if not limit else zot.items(limit=limit, **params)
            )

        if not notes:
            return f"No notes found{f' for item {item_key}' if item_key else ''}."

        # Generate markdown output
        output = [f"# Notes{f' for Item: {item_key}' if item_key else ''}", ""]

        for i, note in enumerate(notes, 1):
            data = note.get("data", {})
            note_key = note.get("key", "")

            # Parent item context
            parent_info = ""
            if parent_key := data.get("parentItem"):
                try:
                    parent = zot.item(parent_key)
                    parent_title = parent["data"].get("title", "Untitled")
                    parent_info = f' (from "{parent_title}")'
                except Exception:
                    parent_info = f" (parent key: {parent_key})"

            # Prepare note text
            note_text = data.get("note", "")

            # Clean up HTML formatting
            note_text = clean_html(note_text)

            # Limit note length for display
            if truncate and len(note_text) > 500:
                note_text = note_text[:500] + "..."

            # Build markdown entry
            output.append(f"## Note {i}{parent_info}")
            output.append(f"**Key:** {note_key}")

            # Tags
            if tags := data.get("tags"):
                tag_list = [f"`{tag['tag']}`" for tag in tags]
                if tag_list:
                    output.append(f"**Tags:** {' '.join(tag_list)}")

            output.append(f"**Content:**\n{note_text}")
            output.append("")  # Empty line between notes

        return "\n".join(output)

    except Exception as e:
        ctx.error(f"Error fetching notes: {str(e)}")
        return f"Error fetching notes: {str(e)}"


@mcp.tool(
    name="zotero_search_notes",
    description="Search for notes across your Zotero library.",
)
def search_notes(query: str, limit: int | str | None = 20, *, ctx: Context) -> str:
    """
    Search for notes in your Zotero library.

    Args:
        query: Search query string
        limit: Maximum number of results to return
        ctx: MCP context

    Returns:
        Markdown-formatted search results
    """
    try:
        if not query.strip():
            return "Error: Search query cannot be empty"

        ctx.info(f"Searching Zotero notes for '{query}'")
        zot = get_zotero_client()

        # Search for notes and annotations

        limit = parse_limit(limit)

        # First search notes
        zot.add_parameters(q=query, itemType="note", limit=limit or 20)
        notes = zot.items()

        # Then search annotations (reusing the get_annotations function)
        annotation_results = get_annotations(
            item_key=None,  # Search all annotations
            use_pdf_extraction=True,
            limit=limit or 20,
            ctx=ctx,
        )

        # Parse the annotation results to extract annotation items
        # This is a bit hacky and depends on the exact formatting of get_annotations
        # You might want to modify get_annotations to return a more structured result
        annotation_lines = annotation_results.split("\n")
        current_annotation = None
        annotations = []

        for line in annotation_lines:
            if line.startswith("## "):
                if current_annotation:
                    annotations.append(current_annotation)
                current_annotation = {"lines": [line], "type": "annotation"}
            elif current_annotation is not None:
                current_annotation["lines"].append(line)

        if current_annotation:
            annotations.append(current_annotation)

        # Format results
        output = [f"# Search Results for '{query}'", ""]

        # Filter and highlight notes
        query_lower = query.lower()
        note_results = []

        for note in notes:
            data = note.get("data", {})
            note_text = data.get("note", "").lower()

            if query_lower in note_text:
                # Prepare full note details
                note_result = {"type": "note", "key": note.get("key", ""), "data": data}
                note_results.append(note_result)

        # Combine and sort results
        all_results = note_results + annotations

        for i, result in enumerate(all_results, 1):
            if result["type"] == "note":
                # Note formatting
                data = result["data"]
                key = result["key"]

                # Parent item context
                parent_info = ""
                if parent_key := data.get("parentItem"):
                    try:
                        parent = zot.item(parent_key)
                        parent_title = parent["data"].get("title", "Untitled")
                        parent_info = f' (from "{parent_title}")'
                    except Exception:
                        parent_info = f" (parent key: {parent_key})"

                # Note text with query highlight
                note_text = data.get("note", "")
                note_text = note_text.replace("<p>", "").replace("</p>", "\n\n")
                note_text = note_text.replace("<br/>", "\n").replace("<br>", "\n")

                # Highlight query in note text
                try:
                    # Find first occurrence of query and extract context
                    text_lower = note_text.lower()
                    pos = text_lower.find(query_lower)
                    if pos >= 0:
                        # Extract context around the query
                        start = max(0, pos - 100)
                        end = min(len(note_text), pos + 200)
                        context = note_text[start:end]

                        # Highlight the query in the context
                        highlighted = context.replace(
                            context[
                                context.lower()
                                .find(query_lower) : context.lower()
                                .find(query_lower)
                                + len(query)
                            ],
                            f"**{context[context.lower().find(query_lower):context.lower().find(query_lower)+len(query)]}**",
                        )

                        note_text = highlighted + "..."
                except Exception:
                    # Fallback to first 500 characters if highlighting fails
                    note_text = note_text[:500] + "..."

                output.append(f"## Note {i}{parent_info}")
                output.append(f"**Key:** {key}")

                # Tags
                if tags := data.get("tags"):
                    tag_list = [f"`{tag['tag']}`" for tag in tags]
                    if tag_list:
                        output.append(f"**Tags:** {' '.join(tag_list)}")

                output.append(f"**Content:**\n{note_text}")
                output.append("")

            elif result["type"] == "annotation":
                # Add the entire annotation block
                output.extend(result["lines"])
                output.append("")

        return "\n".join(output) if output else f"No results found for '{query}'"

    except Exception as e:
        ctx.error(f"Error searching notes: {str(e)}")
        return f"Error searching notes: {str(e)}"


@mcp.tool(name="zotero_create_note", description="Create a new note for a Zotero item.")
def create_note(
    item_key: str,
    note_title: str,
    note_text: str,
    tags: list[str] | None = None,
    *,
    ctx: Context,
) -> str:
    """
    Create a new note for a Zotero item.

    Args:
        item_key: Zotero item key/ID to attach the note to
        note_title: Title for the note
        note_text: Content of the note (can include simple HTML formatting)
        tags: List of tags to apply to the note
        ctx: MCP context

    Returns:
        Confirmation message with the new note key
    """
    try:
        ctx.info(f"Creating note for item {item_key}")
        zot = get_zotero_client()

        # First verify the parent item exists
        try:
            parent = zot.item(item_key)
            parent_title = parent["data"].get("title", "Untitled Item")
        except Exception:
            return f"Error: No item found with key: {item_key}"

        # Format the note content with proper HTML
        # If the note_text already has HTML, use it directly
        if "<p>" in note_text or "<div>" in note_text:
            html_content = note_text
        else:
            # Convert plain text to HTML paragraphs - avoiding f-strings with replacements
            paragraphs = note_text.split("\n\n")
            html_parts = []
            for p in paragraphs:
                # Replace newlines with <br/> tags
                p_with_br = p.replace("\n", "<br/>")
                html_parts.append("<p>" + p_with_br + "</p>")
            html_content = "".join(html_parts)

        # Prepare the note data
        note_data = {
            "itemType": "note",
            "parentItem": item_key,
            "note": html_content,
            "tags": [{"tag": tag} for tag in (tags or [])],
        }

        # Create the note
        result = zot.create_items([note_data])

        # Check if creation was successful
        if "success" in result and result["success"]:
            successful = result["success"]
            if len(successful) > 0:
                note_key = next(iter(successful.keys()))
                return f'Successfully created note for "{parent_title}"\n\nNote key: {note_key}'
            else:
                return f"Note creation response was successful but no key was returned: {result}"
        else:
            return f"Failed to create note: {result.get('failed', 'Unknown error')}"

    except Exception as e:
        ctx.error(f"Error creating note: {str(e)}")
        return f"Error creating note: {str(e)}"


@mcp.tool(
    name="zotero_semantic_search",
    description="Prioritized search tool. Perform semantic search over your Zotero library using AI-powered embeddings.",
)
def semantic_search(
    query: str,
    limit: int = 10,
    filters: dict[str, str] | str | None = None,
    *,
    ctx: Context,
) -> str:
    """
    Perform semantic search over your Zotero library.

    Args:
        query: Search query text - can be concepts, topics, or natural language descriptions
        limit: Maximum number of results to return (default: 10)
        filters: Optional metadata filters as dict or JSON string. Example: {"item_type": "note"}
        ctx: MCP context

    Returns:
        Markdown-formatted search results with similarity scores
    """
    try:
        if not query.strip():
            return "Error: Search query cannot be empty"

        # Parse and validate filters parameter
        if filters is not None:
            # Handle JSON string input
            if isinstance(filters, str):
                try:
                    filters = json.loads(filters)
                    ctx.info(f"Parsed JSON string filters: {filters}")
                except json.JSONDecodeError as e:
                    return f"Error: Invalid JSON in filters parameter: {str(e)}"

            # Validate it's a dictionary
            if not isinstance(filters, dict):
                return 'Error: filters parameter must be a dictionary or JSON string. Example: {"item_type": "note"}'

            # Automatically translate common field names
            if "itemType" in filters:
                filters["item_type"] = filters.pop("itemType")
                ctx.info(
                    f"Automatically translated 'itemType' to 'item_type': {filters}"
                )

            # Additional field name translations can be added here
            # Example: if "creatorType" in filters:
            #     filters["creator_type"] = filters.pop("creatorType")

        ctx.info(f"Performing semantic search for: '{query}'")

        # Import semantic search module
        from pathlib import Path

        from zotero_mcp.semantic_search import create_semantic_search

        # Determine config path
        config_path = Path.home() / ".config" / "zotero-mcp" / "config.json"

        # Create semantic search instance
        search = create_semantic_search(str(config_path))

        # Perform search
        results = search.search(query=query, limit=limit, filters=filters)

        if results.get("error"):
            return f"Semantic search error: {results['error']}"

        search_results = results.get("results", [])

        if not search_results:
            return f"No semantically similar items found for query: '{query}'"

        # Format results as markdown
        output = [f"# Semantic Search Results for '{query}'", ""]
        output.append(f"Found {len(search_results)} similar items:")
        output.append("")

        for i, result in enumerate(search_results, 1):
            similarity_score = result.get("similarity_score", 0)
            _ = result.get("metadata", {})
            zotero_item = result.get("zotero_item", {})

            if zotero_item:
                data = zotero_item.get("data", {})
                title = data.get("title", "Untitled")
                item_type = data.get("itemType", "unknown")
                key = result.get("item_key", "")

                # Format creators
                creators = data.get("creators", [])
                creators_str = format_creators(creators)

                output.append(f"## {i}. {title}")
                output.append(f"**Similarity Score:** {similarity_score:.3f}")
                output.append(f"**Type:** {item_type}")
                output.append(f"**Item Key:** {key}")
                output.append(f"**Authors:** {creators_str}")

                # Add date if available
                if date := data.get("date"):
                    output.append(f"**Date:** {date}")

                # Add abstract snippet if present
                if abstract := data.get("abstractNote"):
                    abstract_snippet = (
                        abstract[:200] + "..." if len(abstract) > 200 else abstract
                    )
                    output.append(f"**Abstract:** {abstract_snippet}")

                # Add tags if present
                if tags := data.get("tags"):
                    tag_list = [f"`{tag['tag']}`" for tag in tags]
                    if tag_list:
                        output.append(f"**Tags:** {' '.join(tag_list)}")

                # Show matched text snippet
                matched_text = result.get("matched_text", "")
                if matched_text:
                    snippet = (
                        matched_text[:300] + "..."
                        if len(matched_text) > 300
                        else matched_text
                    )
                    output.append(f"**Matched Content:** {snippet}")

                output.append("")  # Empty line between items
            else:
                # Fallback if full Zotero item not available
                output.append(f"## {i}. Item {result.get('item_key', 'Unknown')}")
                output.append(f"**Similarity Score:** {similarity_score:.3f}")
                if error := result.get("error"):
                    output.append(f"**Error:** {error}")
                output.append("")

        return "\n".join(output)

    except Exception as e:
        ctx.error(f"Error in semantic search: {str(e)}")
        return f"Error in semantic search: {str(e)}"


@mcp.tool(
    name="zotero_update_search_database",
    description="Update the semantic search database with latest Zotero items.",
)
def update_search_database(
    force_rebuild: bool = False, limit: int | None = None, *, ctx: Context
) -> str:
    """
    Update the semantic search database.

    Args:
        force_rebuild: Whether to rebuild the entire database from scratch
        limit: Limit number of items to process (useful for testing)
        ctx: MCP context

    Returns:
        Update status and statistics
    """
    try:
        ctx.info("Starting semantic search database update...")

        # Import semantic search module
        from pathlib import Path

        from zotero_mcp.semantic_search import create_semantic_search

        # Determine config path
        config_path = Path.home() / ".config" / "zotero-mcp" / "config.json"

        # Create semantic search instance
        search = create_semantic_search(str(config_path))

        # Perform update with no fulltext extraction (for speed)
        stats = search.update_database(
            force_full_rebuild=force_rebuild, limit=limit, extract_fulltext=False
        )

        # Format results
        output = ["# Database Update Results", ""]

        if stats.get("error"):
            output.append(f"**Error:** {stats['error']}")
        else:
            output.append(f"**Total items:** {stats.get('total_items', 0)}")
            output.append(f"**Processed:** {stats.get('processed_items', 0)}")
            output.append(f"**Added:** {stats.get('added_items', 0)}")
            output.append(f"**Updated:** {stats.get('updated_items', 0)}")
            output.append(f"**Skipped:** {stats.get('skipped_items', 0)}")
            output.append(f"**Errors:** {stats.get('errors', 0)}")
            output.append(f"**Duration:** {stats.get('duration', 'Unknown')}")

            if stats.get("start_time"):
                output.append(f"**Started:** {stats['start_time']}")
            if stats.get("end_time"):
                output.append(f"**Completed:** {stats['end_time']}")

        return "\n".join(output)

    except Exception as e:
        ctx.error(f"Error updating search database: {str(e)}")
        return f"Error updating search database: {str(e)}"


@mcp.tool(
    name="zotero_get_search_database_status",
    description="Get status information about the semantic search database.",
)
def get_search_database_status(*, ctx: Context) -> str:
    """
    Get semantic search database status.

    Args:
        ctx: MCP context

    Returns:
        Database status information
    """
    try:
        ctx.info("Getting semantic search database status...")

        # Import semantic search module
        from pathlib import Path

        from zotero_mcp.semantic_search import create_semantic_search

        # Determine config path
        config_path = Path.home() / ".config" / "zotero-mcp" / "config.json"

        # Create semantic search instance
        search = create_semantic_search(str(config_path))

        # Get status
        status = search.get_database_status()

        # Format results
        output = ["# Semantic Search Database Status", ""]

        collection_info = status.get("collection_info", {})
        output.append("## Collection Information")
        output.append(f"**Name:** {collection_info.get('name', 'Unknown')}")
        output.append(f"**Document Count:** {collection_info.get('count', 0)}")
        output.append(
            f"**Embedding Model:** {collection_info.get('embedding_model', 'Unknown')}"
        )
        output.append(
            f"**Database Path:** {collection_info.get('persist_directory', 'Unknown')}"
        )

        if collection_info.get("error"):
            output.append(f"**Error:** {collection_info['error']}")

        output.append("")

        update_config = status.get("update_config", {})
        output.append("## Update Configuration")
        output.append(f"**Auto Update:** {update_config.get('auto_update', False)}")
        output.append(
            f"**Frequency:** {update_config.get('update_frequency', 'manual')}"
        )
        output.append(f"**Last Update:** {update_config.get('last_update', 'Never')}")
        output.append(f"**Should Update Now:** {status.get('should_update', False)}")

        if update_config.get("update_days"):
            output.append(
                f"**Update Interval:** Every {update_config['update_days']} days"
            )

        return "\n".join(output)

    except Exception as e:
        ctx.error(f"Error getting database status: {str(e)}")
        return f"Error getting database status: {str(e)}"


# --- Minimal wrappers for ChatGPT connectors ---
# These are required for ChatGPT custom MCP servers via web "connectors"
# specific tools required are "search" and "fetch"
# See: https://platform.openai.com/docs/mcp


def _extract_item_key_from_input(value: str) -> str | None:
    """Extract a Zotero item key from a Zotero URL, web URL, or bare key.
    Returns None if no plausible key is found.
    """
    if not value:
        return None
    text = value.strip()

    # Common patterns:
    # - zotero://select/items/<KEY>
    # - zotero://select/library/items/<KEY>
    # - https://www.zotero.org/.../items/<KEY>
    # - bare <KEY>
    patterns = [
        r"zotero://select/(?:library/)?items/([A-Za-z0-9]{8})",
        r"/items/([A-Za-z0-9]{8})(?:[^A-Za-z0-9]|$)",
        r"\b([A-Za-z0-9]{8})\b",
    ]

    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return None


@mcp.tool(
    name="search",
    description="ChatGPT-compatible search wrapper. Performs semantic search and returns JSON results.",
)
def chatgpt_connector_search(query: str, *, ctx: Context) -> str:
    """
    Returns a JSON-encoded string with shape {"results": [{"id","title","url"}, ...]}.
    The MCP runtime wraps this string as a single text content item.
    """
    try:
        default_limit = 10

        from zotero_mcp.semantic_search import create_semantic_search

        config_path = Path.home() / ".config" / "zotero-mcp" / "config.json"
        search = create_semantic_search(str(config_path))

        result_list: list[dict[str, str]] = []
        results = search.search(query=query, limit=default_limit, filters=None) or {}
        for r in results.get("results", []):
            item_key = r.get("item_key") or ""
            title = ""
            if r.get("zotero_item"):
                data = (r.get("zotero_item") or {}).get("data", {})
                title = data.get("title", "")
            if not title:
                title = f"Zotero Item {item_key}" if item_key else "Zotero Item"
            url = f"zotero://select/items/{item_key}" if item_key else ""
            result_list.append(
                {
                    "id": item_key or uuid.uuid4().hex[:8],
                    "title": title,
                    "url": url,
                }
            )

        return json.dumps({"results": result_list}, separators=(",", ":"))
    except Exception as e:
        ctx.error(f"Error in search wrapper: {str(e)}")
        return json.dumps({"results": []}, separators=(",", ":"))


@mcp.tool(
    name="fetch",
    description="ChatGPT-compatible fetch wrapper. Retrieves fulltext/metadata for a Zotero item by ID.",
)
def connector_fetch(id: str, *, ctx: Context) -> str:
    """
    Returns a JSON-encoded string with shape {"id","title","text","url","metadata":{...}}.
    The MCP runtime wraps this string as a single text content item.
    """
    try:
        item_key = (id or "").strip()
        if not item_key:
            return json.dumps(
                {
                    "id": id,
                    "title": "",
                    "text": "",
                    "url": "",
                    "metadata": {"error": "missing item key"},
                },
                separators=(",", ":"),
            )

        # Fetch item metadata for title and context
        zot = get_zotero_client()
        try:
            item = zot.item(item_key)
            data = item.get("data", {}) if item else {}
        except Exception:
            item = None
            data = {}

        title = data.get("title", f"Zotero Item {item_key}")
        zotero_url = f"zotero://select/items/{item_key}"
        # Prefer web URL for connectors; fall back to zotero:// if unknown
        lib_type = (os.getenv("ZOTERO_LIBRARY_TYPE", "user") or "user").lower()
        lib_id = os.getenv("ZOTERO_LIBRARY_ID", "")
        if lib_type not in ["user", "group"]:
            lib_type = "user"
        web_url = (
            f"https://www.zotero.org/{'users' if lib_type=='user' else 'groups'}/{lib_id}/items/{item_key}"
            if lib_id
            else ""
        )
        url = web_url or zotero_url

        # Use existing tool to get best-effort fulltext/markdown
        text_md = get_item_fulltext(item_key=item_key, ctx=ctx)
        # Extract the actual full text section if present, else keep as-is
        text_clean = text_md
        try:
            marker = "## Full Text"
            pos = text_md.find(marker)
            if pos >= 0:
                text_clean = text_md[pos + len(marker) :].lstrip("\n #")
        except Exception:
            pass
        if (not text_clean or len(text_clean.strip()) < 40) and data:
            abstract = data.get("abstractNote", "")
            creators = data.get("creators", [])
            byline = format_creators(creators)
            text_clean = (
                f"{title}\n\n"
                + (f"Authors: {byline}\n" if byline else "")
                + (f"Abstract:\n{abstract}" if abstract else "")
            ) or text_md

        metadata = {
            "itemType": data.get("itemType", ""),
            "date": data.get("date", ""),
            "key": item_key,
            "doi": data.get("DOI", ""),
            "authors": format_creators(data.get("creators", [])),
            "tags": [t.get("tag", "") for t in (data.get("tags", []) or [])],
            "zotero_url": zotero_url,
            "web_url": web_url,
            "source": "zotero-mcp",
        }

        return json.dumps(
            {
                "id": item_key,
                "title": title,
                "text": text_clean,
                "url": url,
                "metadata": metadata,
            },
            separators=(",", ":"),
        )
    except Exception as e:
        ctx.error(f"Error in fetch wrapper: {str(e)}")
        return json.dumps(
            {
                "id": id,
                "title": "",
                "text": "",
                "url": "",
                "metadata": {"error": str(e)},
            },
            separators=(",", ":"),
        )
