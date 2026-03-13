"""Tag management tools: get_tags, rename, delete, merge, batch_update, statistics, suggest_organization."""

import json
import re
from collections import defaultdict
from difflib import SequenceMatcher

from fastmcp import Context

from zotero_mcp.client import get_zotero_client
from zotero_mcp.server import mcp
from zotero_mcp.utils import parse_limit

_TAG_NORM_RE = re.compile(r"[\s\-_]+")


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

        def _normalize_tag_list(
            raw_value: list[str] | str | None, field_name: str
        ) -> list[str]:
            if raw_value is None:
                return []
            parsed_value = raw_value
            if isinstance(parsed_value, str):
                try:
                    parsed_value = json.loads(parsed_value)
                    ctx.info(f"Parsed {field_name} from JSON string: {parsed_value}")
                except json.JSONDecodeError:
                    raise ValueError(
                        f"{field_name} appears to be malformed JSON: {raw_value}"
                    )
            if not isinstance(parsed_value, list):
                raise ValueError(
                    f"{field_name} must be a JSON array or a list of strings"
                )
            return [str(t).strip() for t in parsed_value if str(t).strip()]

        try:
            add_tags = _normalize_tag_list(add_tags, "add_tags")
            remove_tags = _normalize_tag_list(remove_tags, "remove_tags")
        except ValueError as e:
            return f"Error: {e}"

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


def find_similar_tags(
    tags: list[str], threshold: float
) -> list[tuple[str, str, float]]:
    """Return pairs of tags whose names are similar enough to be merge candidates.

    Uses case-insensitive comparison and ``difflib.SequenceMatcher`` to find
    tags that look like duplicates (e.g. "Machine Learning" vs "machine-learning").

    Returns a list of (tag_a, tag_b, similarity_score) tuples, sorted by
    score descending.  At most 50 pairs are returned to keep output readable.
    """

    # Normalise: lowercase, collapse whitespace/hyphens/underscores
    def _norm(t: str) -> str:
        return _TAG_NORM_RE.sub(" ", t.strip().lower())

    normed = {t: _norm(t) for t in tags}

    # Exact-match-after-normalisation is always a candidate (score = 1.0),
    # so group by normalised form first for an O(n) pass.
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
