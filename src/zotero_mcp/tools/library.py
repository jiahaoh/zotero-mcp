"""Library management tools: list_libraries, switch_library, list_feeds, get_feed_items, add_to_library."""

import os
import re
import uuid

from fastmcp import Context

from zotero_mcp.client import (
    clear_active_library,
    get_active_library,
    get_zotero_client,
    set_active_library,
)
from zotero_mcp.server import mcp
from zotero_mcp.utils import clean_html, is_local_mode, parse_limit

# ---------------------------------------------------------------------------
# Helpers for zotero_add_to_library
# ---------------------------------------------------------------------------

_DOI_RE = re.compile(r"10\.\d{4,9}/[^\s]+")


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


def _get_write_client():
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
    """Resolve a Zotero collection key to the connector internal ID."""
    import httpx

    try:
        resp = httpx.post(
            "http://localhost:23119/connector/getSelectedCollection",
            json={},
            timeout=5,
        )
        if resp.status_code != 200:
            return None
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

    Returns the item key on success, or *None* on failure.
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


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------


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
