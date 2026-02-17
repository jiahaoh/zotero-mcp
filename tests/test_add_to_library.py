"""Tests for zotero_add_to_library tool and its helper functions."""

from unittest.mock import MagicMock, patch

import pytest
from conftest import unwrap

from zotero_mcp.server import (
    _download_pdf,
    _extract_doi_from_url,
    _fetch_s2_paper,
    _map_s2_to_zotero,
    _parse_feed_creators,
)
from zotero_mcp.server import add_to_library as _add_to_library

add_to_library = unwrap(_add_to_library)


# ═══════════════════════════════════════════════════════════════════════════
# _extract_doi_from_url
# ═══════════════════════════════════════════════════════════════════════════


class TestExtractDoiFromUrl:
    def test_doi_org_link(self):
        assert (
            _extract_doi_from_url("https://doi.org/10.1038/s41586-024-07")
            == "10.1038/s41586-024-07"
        )

    def test_doi_org_trailing_slash(self):
        assert (
            _extract_doi_from_url("https://doi.org/10.1038/s41586-024-07/")
            == "10.1038/s41586-024-07"
        )

    def test_embedded_doi_in_nature(self):
        url = "https://www.nature.com/articles/10.1038/s41587-024-02123-4"
        assert _extract_doi_from_url(url) == "10.1038/s41587-024-02123-4"

    def test_biorxiv_url(self):
        url = "https://www.biorxiv.org/content/10.1101/2024.01.01.123456v1"
        assert _extract_doi_from_url(url) == "10.1101/2024.01.01.123456v1"

    def test_no_doi(self):
        assert _extract_doi_from_url("https://example.com/paper") is None

    def test_none_input(self):
        assert _extract_doi_from_url(None) is None

    def test_empty_string(self):
        assert _extract_doi_from_url("") is None


# ═══════════════════════════════════════════════════════════════════════════
# _parse_feed_creators
# ═══════════════════════════════════════════════════════════════════════════


class TestParseFeedCreators:
    def test_single_author(self):
        result = _parse_feed_creators("Smith, John")
        assert result == [
            {"creatorType": "author", "firstName": "John", "lastName": "Smith"}
        ]

    def test_multiple_authors(self):
        result = _parse_feed_creators("Smith, John; Doe, Jane")
        assert len(result) == 2
        assert result[1]["firstName"] == "Jane"
        assert result[1]["lastName"] == "Doe"

    def test_no_comma(self):
        result = _parse_feed_creators("Consortium")
        assert result == [
            {"creatorType": "author", "firstName": "", "lastName": "Consortium"}
        ]

    def test_none_input(self):
        assert _parse_feed_creators(None) == []

    def test_empty_string(self):
        assert _parse_feed_creators("") == []


# ═══════════════════════════════════════════════════════════════════════════
# _map_s2_to_zotero
# ═══════════════════════════════════════════════════════════════════════════


def _base_template():
    """Minimal Zotero journalArticle template."""
    return {
        "itemType": "journalArticle",
        "title": "",
        "creators": [],
        "abstractNote": "",
        "publicationTitle": "",
        "date": "",
        "volume": "",
        "pages": "",
        "DOI": "",
        "url": "",
        "extra": "",
        "collections": [],
    }


def _s2_paper():
    """Sample Semantic Scholar paper dict."""
    return {
        "title": "A Great Paper",
        "authors": [
            {"name": "Alice Smith"},
            {"name": "Bob Jones"},
        ],
        "year": 2024,
        "abstract": "This paper does things.",
        "externalIds": {
            "DOI": "10.1234/test",
            "PubMed": "99999",
            "ArXiv": "2401.00001",
        },
        "openAccessPdf": {"url": "https://example.com/paper.pdf"},
        "venue": "Nature",
        "publicationDate": "2024-03-15",
        "journal": {"name": "Nature", "volume": "625", "pages": "100-110"},
    }


class TestMapS2ToZotero:
    def test_maps_title(self):
        t = _map_s2_to_zotero(_s2_paper(), _base_template())
        assert t["title"] == "A Great Paper"

    def test_maps_authors(self):
        t = _map_s2_to_zotero(_s2_paper(), _base_template())
        assert len(t["creators"]) == 2
        assert t["creators"][0]["firstName"] == "Alice"
        assert t["creators"][0]["lastName"] == "Smith"

    def test_maps_doi(self):
        t = _map_s2_to_zotero(_s2_paper(), _base_template())
        assert t["DOI"] == "10.1234/test"

    def test_maps_journal(self):
        t = _map_s2_to_zotero(_s2_paper(), _base_template())
        assert t["publicationTitle"] == "Nature"
        assert t["volume"] == "625"
        assert t["pages"] == "100-110"

    def test_maps_date(self):
        t = _map_s2_to_zotero(_s2_paper(), _base_template())
        assert t["date"] == "2024-03-15"

    def test_maps_extra_ids(self):
        t = _map_s2_to_zotero(_s2_paper(), _base_template())
        assert "PMID: 99999" in t["extra"]
        assert "arXiv: 2401.00001" in t["extra"]

    def test_fallback_to_feed_title(self):
        t = _map_s2_to_zotero({}, _base_template(), {"title": "Feed Title"})
        assert t["title"] == "Feed Title"

    def test_fallback_to_feed_creators(self):
        t = _map_s2_to_zotero(
            {}, _base_template(), {"creators": "Doe, Jane; Smith, John"}
        )
        assert len(t["creators"]) == 2
        assert t["creators"][0]["lastName"] == "Doe"

    def test_fallback_to_feed_abstract(self):
        t = _map_s2_to_zotero(
            {}, _base_template(), {"abstract": "<p>HTML abstract</p>"}
        )
        assert t["abstractNote"] == "HTML abstract"

    def test_empty_paper_and_no_feed(self):
        t = _map_s2_to_zotero({}, _base_template())
        assert t["title"] == ""
        assert t["creators"] == []


# ═══════════════════════════════════════════════════════════════════════════
# _fetch_s2_paper (mocked HTTP)
# ═══════════════════════════════════════════════════════════════════════════


class TestFetchS2Paper:
    @patch("httpx.Client")
    def test_doi_lookup(self, mock_client_cls):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"title": "Found Paper"}
        mock_client = MagicMock()
        mock_client.get.return_value = mock_resp
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = _fetch_s2_paper("10.1234/test", None)
        assert result == {"title": "Found Paper"}

    @patch("httpx.Client")
    def test_title_fallback(self, mock_client_cls):
        mock_resp_doi = MagicMock()
        mock_resp_doi.status_code = 404

        mock_resp_search = MagicMock()
        mock_resp_search.status_code = 200
        mock_resp_search.json.return_value = {"data": [{"title": "Searched Paper"}]}

        mock_client = MagicMock()
        mock_client.get.side_effect = [mock_resp_doi, mock_resp_search]
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = _fetch_s2_paper("10.0000/missing", "Searched Paper")
        assert result["title"] == "Searched Paper"

    @patch("httpx.Client")
    def test_returns_none_on_exception(self, mock_client_cls):
        mock_client_cls.side_effect = Exception("Network error")
        assert _fetch_s2_paper("10.1234/x", "title") is None


# ═══════════════════════════════════════════════════════════════════════════
# add_to_library (integration-level with mocked deps)
# ═══════════════════════════════════════════════════════════════════════════


class TestAddToLibrary:
    def test_no_inputs_returns_error(self, mock_zot, mock_ctx):
        result = add_to_library(ctx=mock_ctx)
        assert "Error" in result

    @patch("zotero_mcp.server._fetch_s2_paper")
    def test_doi_mode_creates_item(self, mock_s2, mock_zot, mock_ctx):
        mock_s2.return_value = _s2_paper()
        mock_zot.item_template.return_value = _base_template()
        mock_zot.create_items.return_value = {"success": {"0": "NEWKEY1"}}

        result = add_to_library(doi="10.1234/test", download_pdf=False, ctx=mock_ctx)

        assert "NEWKEY1" in result
        assert "A Great Paper" in result
        mock_zot.create_items.assert_called_once()

    @patch("zotero_mcp.server._fetch_s2_paper")
    def test_url_mode_extracts_doi(self, mock_s2, mock_zot, mock_ctx):
        mock_s2.return_value = _s2_paper()
        mock_zot.item_template.return_value = _base_template()
        mock_zot.create_items.return_value = {"success": {"0": "NEWKEY2"}}

        result = add_to_library(
            url="https://doi.org/10.1234/test", download_pdf=False, ctx=mock_ctx
        )

        assert "NEWKEY2" in result
        mock_s2.assert_called_once_with("10.1234/test", None)

    @patch("zotero_mcp.server._fetch_s2_paper")
    def test_collection_key_passed_to_template(self, mock_s2, mock_zot, mock_ctx):
        mock_s2.return_value = _s2_paper()
        mock_zot.item_template.return_value = _base_template()
        mock_zot.create_items.return_value = {"success": {"0": "NEWKEY3"}}

        result = add_to_library(
            doi="10.1234/test",
            collection_key="COL123",
            download_pdf=False,
            ctx=mock_ctx,
        )

        created = mock_zot.create_items.call_args[0][0][0]
        assert created["collections"] == ["COL123"]
        assert "COL123" in result

    @patch("zotero_mcp.server._fetch_s2_paper")
    def test_s2_failure_still_creates_item(self, mock_s2, mock_zot, mock_ctx):
        mock_s2.return_value = None
        mock_zot.item_template.return_value = _base_template()
        mock_zot.create_items.return_value = {"success": {"0": "NEWKEY4"}}

        result = add_to_library(doi="10.1234/test", download_pdf=False, ctx=mock_ctx)

        assert "NEWKEY4" in result
        assert "Semantic Scholar lookup failed" in result

    @patch("zotero_mcp.server._download_pdf")
    @patch("zotero_mcp.server._fetch_s2_paper")
    def test_pdf_download_and_attach(self, mock_s2, mock_dl, mock_zot, mock_ctx):
        mock_s2.return_value = _s2_paper()
        mock_dl.return_value = "/tmp/fake.pdf"
        mock_zot.item_template.return_value = _base_template()
        mock_zot.create_items.return_value = {"success": {"0": "NEWKEY5"}}

        result = add_to_library(doi="10.1234/test", download_pdf=True, ctx=mock_ctx)

        assert "attached" in result
        mock_zot.attachment_simple.assert_called_once_with(
            ["/tmp/fake.pdf"], parentid="NEWKEY5"
        )

    @patch("zotero_mcp.server._fetch_s2_paper")
    def test_feed_mode_requires_local(self, mock_s2, mock_zot, mock_ctx):
        with patch.dict("os.environ", {"ZOTERO_LOCAL": "false"}):
            result = add_to_library(title="Test", feed_library_id=2, ctx=mock_ctx)
        assert "ZOTERO_LOCAL" in result

    @patch("zotero_mcp.server._fetch_s2_paper")
    @patch("zotero_mcp.local_db.LocalZoteroReader")
    def test_feed_mode_not_found(self, mock_reader_cls, mock_s2, mock_zot, mock_ctx):
        reader = MagicMock()
        reader.search_feed_items_by_title.return_value = []
        reader.get_feed_items.return_value = [
            {"title": "Recent Paper A"},
            {"title": "Recent Paper B"},
        ]
        mock_reader_cls.return_value = reader

        with patch.dict("os.environ", {"ZOTERO_LOCAL": "true"}):
            result = add_to_library(
                title="Nonexistent", feed_library_id=2, ctx=mock_ctx
            )

        assert "No feed item matching" in result
        assert "Recent Paper A" in result

    @patch("zotero_mcp.server._create_item_via_connector")
    @patch("zotero_mcp.server._fetch_s2_paper")
    @patch("zotero_mcp.local_db.LocalZoteroReader")
    def test_feed_mode_success(
        self, mock_reader_cls, mock_s2, mock_connector, mock_zot, mock_ctx
    ):
        reader = MagicMock()
        reader.search_feed_items_by_title.return_value = [
            {
                "title": "Feed Paper",
                "doi": "10.9999/feed",
                "url": "https://example.com",
                "abstract": "<p>Summary</p>",
                "creators": "Doe, Jane",
            }
        ]
        mock_reader_cls.return_value = reader
        mock_s2.return_value = _s2_paper()
        mock_zot.item_template.return_value = _base_template()
        # In local mode without API key, connector path is used
        mock_connector.return_value = "NEWKEY6"

        with patch.dict("os.environ", {"ZOTERO_LOCAL": "true"}):
            result = add_to_library(
                title="Feed Paper",
                feed_library_id=2,
                download_pdf=False,
                ctx=mock_ctx,
            )

        assert "NEWKEY6" in result
        mock_s2.assert_called_once_with("10.9999/feed", "Feed Paper")
        mock_connector.assert_called_once()
