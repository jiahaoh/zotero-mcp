import os
import re
from typing import Dict, List

html_re = re.compile(r"<.*?>")


def format_creators(creators: list[dict[str, str]]) -> str:
    """
    Format creator names into a string.

    Args:
        creators: List of creator objects from Zotero.

    Returns:
        Formatted string with creator names.
    """
    names = []
    for creator in creators:
        if "firstName" in creator and "lastName" in creator:
            names.append(f"{creator['lastName']}, {creator['firstName']}")
        elif "name" in creator:
            names.append(creator["name"])
    return "; ".join(names) if names else "No authors listed"


def is_local_mode() -> bool:
    """Return True if running in local mode.

    Local mode is enabled when environment variable `ZOTERO_LOCAL` is set to a
    truthy value ("true", "yes", or "1", case-insensitive).
    """
    value = os.getenv("ZOTERO_LOCAL", "")
    return value.lower() in {"true", "yes", "1"}


def parse_limit(limit: int | str | None) -> int | None:
    """Coerce a limit value that may arrive as a string into an int.

    MCP tool parameters sometimes arrive as strings even when typed as int.
    Returns None if the input is None (meaning "no limit").
    """
    if limit is None:
        return None
    if isinstance(limit, str):
        return int(limit)
    return limit


def format_item_list(
    items: list[dict],
    *,
    heading_level: int = 2,
    include_abstract: bool = True,
    abstract_max_length: int = 200,
    include_tags: bool = True,
    include_date_added: bool = False,
) -> list[str]:
    """Format a list of Zotero items as markdown lines.

    Returns a list of strings (one per line) ready to be joined with ``"\\n".join()``.

    Args:
        items: Zotero API item dicts (each with ``"data"`` and ``"key"``).
        heading_level: Number of ``#`` for each item heading (2 = ``##``).
        include_abstract: Whether to append an abstract snippet.
        abstract_max_length: Max characters for the abstract before truncation.
        include_tags: Whether to show tags.
        include_date_added: Whether to show the ``dateAdded`` field.
    """
    hashes = "#" * heading_level
    output: list[str] = []
    for i, item in enumerate(items, 1):
        data = item.get("data", {})
        title = data.get("title", "Untitled")
        item_type = data.get("itemType", "unknown")
        date = data.get("date", "No date")
        key = item.get("key", "")

        creators = data.get("creators", [])
        creators_str = format_creators(creators)

        output.append(f"{hashes} {i}. {title}")
        output.append(f"**Type:** {item_type}")
        output.append(f"**Item Key:** {key}")
        output.append(f"**Date:** {date}")
        if include_date_added:
            output.append(f"**Added:** {data.get('dateAdded', 'Unknown')}")
        output.append(f"**Authors:** {creators_str}")

        if include_abstract:
            if abstract := data.get("abstractNote"):
                snippet = (
                    abstract[:abstract_max_length] + "..."
                    if len(abstract) > abstract_max_length
                    else abstract
                )
                output.append(f"**Abstract:** {snippet}")

        if include_tags:
            if tags := data.get("tags"):
                tag_list = [f"`{tag['tag']}`" for tag in tags]
                if tag_list:
                    output.append(f"**Tags:** {' '.join(tag_list)}")

        output.append("")  # blank line between items
    return output


def clean_html(raw_html: str) -> str:
    """
    Remove HTML tags from a string.

    Args:
        raw_html: String containing HTML content.
    Returns:
        Cleaned string without HTML tags.
    """
    clean_text = re.sub(html_re, "", raw_html)
    return clean_text
