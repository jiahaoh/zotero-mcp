"""Notes and annotations tools: get_notes, create_note, create_annotation, get_annotations."""

import os

from fastmcp import Context

from zotero_mcp.client import get_zotero_client
from zotero_mcp.server import mcp
from zotero_mcp.utils import clean_html, is_local_mode, parse_limit


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

        # Use note_title as a visible heading so the argument is not ignored.
        clean_title = (note_title or "").strip()
        if clean_title:
            safe_title = (
                clean_title.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
            )
            html_content = f"<h1>{safe_title}</h1>{html_content}"

        # Prepare the note data
        note_data = {
            "itemType": "note",
            "parentItem": item_key,
            "note": html_content,
            "tags": [{"tag": tag} for tag in (tags or [])],
        }

        # In local mode, the local API does not support POST to create items,
        # and the connector/saveItems endpoint ignores parentItem (creating
        # standalone notes instead of child notes). If an API key is available,
        # use the web API which properly supports parentItem.
        if is_local_mode():
            from zotero_mcp.client import get_web_zotero_client

            web_zot = get_web_zotero_client()
            if web_zot is not None:
                result = web_zot.create_items([note_data])
                if "success" in result and result["success"]:
                    successful = result["success"]
                    if len(successful) > 0:
                        note_key = next(iter(successful.keys()))
                        return f'Successfully created note for "{parent_title}"\n\nNote key: {note_key}'
                    else:
                        return f"Note creation response was successful but no key was returned: {result}"
                else:
                    return f"Failed to create note: {result.get('failed', 'Unknown error')}"
            else:
                # Fallback: connector endpoint (note will NOT be attached as child)
                import requests

                port = os.getenv("ZOTERO_LOCAL_PORT", "23119")
                connector_url = f"http://127.0.0.1:{port}/connector/saveItems"
                payload = {
                    "items": [
                        {
                            "itemType": "note",
                            "note": html_content,
                            "tags": [tag for tag in (tags or [])],
                            "parentItem": item_key,
                        }
                    ],
                    "uri": "about:blank",
                }
                resp = requests.post(
                    connector_url,
                    headers={"Content-Type": "application/json"},
                    json=payload,
                    timeout=30,
                )
                if resp.status_code == 201:
                    return (
                        f'Note created for "{parent_title}" but may not be attached as a child item. '
                        f"Set ZOTERO_API_KEY and ZOTERO_LIBRARY_ID to enable proper child note creation."
                    )
                else:
                    return f"Failed to create note via local connector (HTTP {resp.status_code}): {resp.text}"
        else:
            # Remote API: use pyzotero's create_items
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
    name="zotero_create_annotation",
    description="Create a highlight annotation on a PDF or EPUB attachment with optional comment.",
)
def create_annotation(
    attachment_key: str,
    page: int,
    text: str,
    comment: str | None = None,
    color: str = "#ffd400",
    *,
    ctx: Context,
) -> str:
    """
    Create a highlight annotation on a PDF or EPUB attachment.

    This tool handles multiple storage configurations:
    - Zotero Cloud Storage: Downloads file via Web API
    - WebDAV Storage: Downloads file via local Zotero (requires Zotero desktop running)
    - Annotations are always created via the Web API (required for write operations)

    Args:
        attachment_key: Attachment key (e.g., "NHZFE5A7")
        page: For PDF: 1-indexed page number. For EPUB: 1-indexed chapter number.
        text: Exact text to highlight (used to find coordinates/CFI)
        comment: Optional comment on the annotation
        color: Highlight color in hex format (default: "#ffd400" yellow)
        ctx: MCP context

    Returns:
        Confirmation message with the new annotation key
    """
    import tempfile

    from zotero_mcp.client import get_local_zotero_client, get_web_zotero_client
    from zotero_mcp.pdf_utils import (
        build_annotation_position,
        find_text_position,
        get_page_label,
        verify_pdf_attachment,
    )

    try:
        ctx.info(f"Creating annotation on attachment {attachment_key}, page {page}")

        # Get clients for different operations
        local_client = get_local_zotero_client()
        web_client = get_web_zotero_client()

        # REQUIREMENT: Web API is required for creating annotations
        if not web_client:
            return (
                "Error: Web API credentials required for creating annotations.\n\n"
                "Please configure the following environment variables:\n"
                "- ZOTERO_API_KEY: Your Zotero API key (from zotero.org/settings/keys)\n"
                "- ZOTERO_LIBRARY_ID: Your library ID\n"
                "- ZOTERO_LIBRARY_TYPE: 'user' or 'group'\n\n"
                "Note: Zotero's local API is read-only and cannot create annotations."
            )

        # Use web client for metadata (it has the credentials)
        metadata_client = web_client

        # Verify the attachment exists and is a PDF/EPUB
        try:
            attachment = metadata_client.item(attachment_key)
            attachment_data = attachment.get("data", {})

            if attachment_data.get("itemType") != "attachment":
                return f"Error: Item {attachment_key} is not an attachment"

            content_type = attachment_data.get("contentType", "")
            supported_types = {
                "application/pdf": "pdf",
                "application/epub+zip": "epub",
            }
            if content_type not in supported_types:
                return f"Error: Attachment {attachment_key} is not a PDF or EPUB (type: {content_type})"

            file_type = supported_types[content_type]
            filename = attachment_data.get("filename", f"{attachment_key}.{file_type}")

        except Exception as e:
            return f"Error: No attachment found with key: {attachment_key} ({e})"

        # Download the file to a temporary location
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = os.path.join(tmpdir, filename)
            ctx.info(f"Downloading file to {file_path}")

            download_errors = []
            downloaded = False

            # Source 1: Try local Zotero first (works for WebDAV and local storage)
            if local_client and not downloaded:
                try:
                    ctx.info("Trying local Zotero (WebDAV/local storage)...")
                    local_client.dump(attachment_key, filename=filename, path=tmpdir)
                    if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
                        downloaded = True
                        ctx.info("File downloaded via local Zotero")
                except Exception as e:
                    download_errors.append(f"Local Zotero: {e}")

            # Source 2: Try Web API (works for Zotero Cloud Storage)
            if not downloaded:
                try:
                    ctx.info("Trying Zotero Web API (cloud storage)...")
                    web_client.dump(attachment_key, filename=filename, path=tmpdir)
                    if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
                        downloaded = True
                        ctx.info("File downloaded via Web API")
                except Exception as e:
                    download_errors.append(f"Web API: {e}")

            if not downloaded:
                error_details = "\n".join(f"  - {err}" for err in download_errors)
                return (
                    f"Error: Could not download attachment.\n\n"
                    f"Attempted sources:\n{error_details}\n\n"
                    "Possible solutions:\n"
                    "- **Zotero Cloud Storage**: Ensure file syncing is enabled\n"
                    "- **WebDAV Storage**: Ensure Zotero desktop is running\n"
                    "- **Linked files**: Linked attachments cannot be accessed remotely"
                )

            # Verify the file is valid
            if file_type == "pdf":
                if not verify_pdf_attachment(file_path):
                    return "Error: Downloaded file is not a valid PDF"
            else:  # epub
                from zotero_mcp.epub_utils import verify_epub_attachment

                if not verify_epub_attachment(file_path):
                    return "Error: Downloaded file is not a valid EPUB"

            # Search for the text and get position data
            search_preview = text[:50] + "..." if len(text) > 50 else text
            location_type = "page" if file_type == "pdf" else "chapter"
            ctx.info(
                f"Searching for text in {location_type} {page}: '{search_preview}'"
            )

            if file_type == "pdf":
                position_data = find_text_position(file_path, page, text)
            else:  # epub
                from zotero_mcp.epub_utils import find_text_in_epub

                position_data = find_text_in_epub(file_path, page, text)

            if "error" in position_data:
                # Build debug info message
                debug_lines = [
                    f"Error: {position_data['error']}",
                    "",
                    f'Text searched: "{text[:100]}{"..." if len(text) > 100 else ""}"',
                ]

                best_score = position_data.get("best_score", 0)
                best_match = position_data.get("best_match")

                if best_score >= 0.5 and best_match:
                    debug_lines.append("")
                    debug_lines.append("=" * 50)
                    debug_lines.append(f"DID YOU MEAN (score: {best_score:.0%}):")
                    debug_lines.append("")
                    suggestion = best_match[:150].strip()
                    if len(best_match) > 150:
                        suggestion += "..."
                    debug_lines.append(f'  "{suggestion}"')
                    debug_lines.append("")
                    if position_data.get("page_found"):
                        debug_lines.append(
                            f"  (Found on page {position_data['page_found']})"
                        )
                    debug_lines.append("=" * 50)
                    debug_lines.append("")
                    debug_lines.append(
                        "TIP: Copy the exact text from the PDF instead of paraphrasing."
                    )
                elif best_score > 0:
                    debug_lines.append("")
                    debug_lines.append("Debug info:")
                    debug_lines.append(
                        f"  Best match score: {best_score:.2f} (too low for suggestion)"
                    )
                    if best_match:
                        preview = best_match[:80]
                        debug_lines.append(f'  Best match text: "{preview}..."')
                    found_location = position_data.get(
                        "page_found"
                    ) or position_data.get("chapter_found")
                    if found_location:
                        debug_lines.append(
                            f"  Found in {location_type}: {found_location}"
                        )

                searched = position_data.get("pages_searched") or position_data.get(
                    "chapters_searched"
                )
                if searched:
                    debug_lines.append(
                        f"  {location_type.title()}s searched: {searched}"
                    )

                if best_score < 0.5:
                    debug_lines.extend(
                        [
                            "",
                            "Tips:",
                            f"- Copy the exact text from the {file_type.upper()} (don't paraphrase)",
                            "- Try a shorter, unique phrase from the beginning",
                            f"- Check that the {location_type} number is correct",
                        ]
                    )

                return "\n".join(debug_lines)

            # Build annotation data based on file type
            if file_type == "pdf":
                page_label = get_page_label(file_path, page)
                annotation_position = build_annotation_position(
                    position_data["pageIndex"], position_data["rects"]
                )
                sort_index = position_data["sort_index"]
            else:  # epub
                page_label = ""
                annotation_position = position_data["annotation_position"]
                chapter = position_data.get("chapter_found", page)
                char_position = position_data.get("char_position", chapter * 1000)
                sort_index = f"{chapter:05d}|{char_position:08d}"

            # Prepare the annotation data
            annotation_data = {
                "itemType": "annotation",
                "parentItem": attachment_key,
                "annotationType": "highlight",
                "annotationText": text,
                "annotationComment": comment or "",
                "annotationColor": color,
                "annotationSortIndex": sort_index,
                "annotationPosition": annotation_position,
            }
            if page_label:
                annotation_data["annotationPageLabel"] = page_label

            ctx.info("Creating annotation via Web API...")

            # Create the annotation using web client
            result = web_client.create_items([annotation_data])

            if "success" in result and result["success"]:
                successful = result["success"]
                if len(successful) > 0:
                    annotation_key = list(successful.values())[0]
                    location_label = "Page" if file_type == "pdf" else "Chapter"
                    response = [
                        "Successfully created highlight annotation",
                        "",
                        f"**Annotation Key:** {annotation_key}",
                        f"**{location_label}:** {page_label}",
                    ]
                    if file_type == "epub":
                        chapter_found = position_data.get("chapter_found", page)
                        if chapter_found != page:
                            response.append(
                                f"**Note:** Text was found in chapter {chapter_found} (you specified {page})"
                            )
                        chapter_href = position_data.get("chapter_href", "")
                        if chapter_href:
                            response.append(f"**Section:** {chapter_href}")
                    response.append(
                        f'**Text:** "{text[:100]}{"..." if len(text) > 100 else ""}"'
                    )
                    if comment:
                        response.append(f"**Comment:** {comment}")
                    response.append(f"**Color:** {color}")
                    return "\n".join(response)
                else:
                    return f"Annotation creation response was successful but no key was returned: {result}"
            else:
                failed_info = result.get("failed", {})
                return f"Failed to create annotation: {failed_info}"

    except Exception as e:
        ctx.error(f"Error creating annotation: {str(e)}")
        return f"Error creating annotation: {str(e)}"
