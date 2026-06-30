"""Collection management tools."""

from fastmcp import Context

from zotero_mcp.client import get_web_zotero_client, get_zotero_client
from zotero_mcp.server import mcp
from zotero_mcp.utils import format_item_list, is_local_mode, parse_limit


def _get_write_zot():
    """Return a write-capable Zotero client.

    In local mode the default client is read-only (localhost:23119 doesn't
    support POST/PATCH/DELETE).  Fall back to the web API client when
    available, otherwise return the default client (caller will get an error
    on write).
    """
    if is_local_mode():
        web = get_web_zotero_client()
        if web is not None:
            return web
    return get_zotero_client()


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
        write_zot = _get_write_zot()

        payload: dict = {"name": name}
        if parent_collection_key:
            payload["parentCollection"] = parent_collection_key

        result = write_zot.create_collections([payload])

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
        write_zot = _get_write_zot()

        added = 0
        skipped = 0
        for key in item_keys:
            try:
                try:
                    item = write_zot.item(key)
                except Exception:
                    item = zot.item(key)
                collections = item["data"].get("collections", [])
                if collection_key in collections:
                    skipped += 1
                    continue
                collections.append(collection_key)
                item["data"]["collections"] = collections
                write_zot.update_item(item)
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
        write_zot = _get_write_zot()

        # Pre-fetch collection members so we can detect items whose
        # ``collections`` field isn't populated by the local API.
        try:
            coll_items = zot.collection_items(collection_key, limit=100)
            coll_member_keys = {ci.get("key") for ci in coll_items}
        except Exception:
            coll_member_keys = set()

        removed = 0
        skipped = 0
        for key in item_keys:
            try:
                # Read from whichever client has the data; prefer write
                # client so the version tag matches for the update call.
                try:
                    item = write_zot.item(key)
                except Exception:
                    item = zot.item(key)
                collections = item["data"].get("collections", [])
                if collection_key not in collections:
                    # Local API may omit the collections field for items
                    # added via the connector or after recent sync.  Fall
                    # back to the collection-items query to confirm.
                    if key not in coll_member_keys:
                        skipped += 1
                        continue
                    # Item IS in the collection — patch the list so the
                    # update below sends the correct value.
                    collections.append(collection_key)
                collections.remove(collection_key)
                item["data"]["collections"] = collections
                write_zot.update_item(item)
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
        write_zot = _get_write_zot()

        moved = 0
        failed = 0
        for key in item_keys:
            try:
                try:
                    item = write_zot.item(key)
                except Exception:
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
                    write_zot.update_item(item)
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
        write_zot = _get_write_zot()

        collection = write_zot.collection(collection_key)
        old_name = collection["data"].get("name", "Unnamed")
        collection["data"]["name"] = new_name
        write_zot.update_collection(collection)

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
        write_zot = _get_write_zot()

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
                    write_zot.update_item(item)
                    trashed += 1
                except Exception as e:
                    ctx.error(f"Failed to trash item {item.get('key', '?')}: {e}")

        write_zot.delete_collection(collection)

        items_msg = f"\n**Items trashed:** {trashed}" if delete_items else ""
        return (
            f"# Collection Deleted\n\n"
            f"**{coll_name}** (`{collection_key}`) has been deleted.{items_msg}{warning}"
        )
    except Exception as e:
        ctx.error(f"Error deleting collection: {str(e)}")
        return f"Error deleting collection: {str(e)}"
