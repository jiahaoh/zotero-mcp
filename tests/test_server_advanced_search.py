import pytest

from zotero_mcp.tools import search as search_mod
from conftest import unwrap

advanced_search = unwrap(search_mod.advanced_search)

# These tests are designed for upstream's client-side filtering implementation
# of advanced_search. Our version uses Zotero saved searches, so these tests
# are skipped until we adopt upstream's approach.
pytestmark = pytest.mark.skip(
    reason="advanced_search uses saved searches, not client-side filtering"
)


class DummyContext:
    def info(self, *_args, **_kwargs):
        return None

    def error(self, *_args, **_kwargs):
        return None

    def warn(self, *_args, **_kwargs):
        return None


class FakeZotero:
    def __init__(self, items):
        self._items = items

    def items(self, start=0, limit=100, **_kwargs):
        return self._items[start : start + limit]


def test_advanced_search_filters_items(monkeypatch):
    fake_items = [
        {
            "key": "AAA11111",
            "data": {
                "itemType": "journalArticle",
                "title": "Quantum Networks and Learning",
                "date": "2024",
                "creators": [{"firstName": "Jane", "lastName": "Doe"}],
                "tags": [{"tag": "physics"}],
            },
        },
        {
            "key": "BBB22222",
            "data": {
                "itemType": "journalArticle",
                "title": "Classical Literature Review",
                "date": "2018",
                "creators": [{"firstName": "Alex", "lastName": "Smith"}],
                "tags": [{"tag": "history"}],
            },
        },
        {
            "key": "CCC33333",
            "data": {
                "itemType": "attachment",
                "title": "Ignored Attachment",
                "date": "2024",
                "creators": [],
                "tags": [],
            },
        },
    ]
    monkeypatch.setattr(search_mod, "get_zotero_client", lambda: FakeZotero(fake_items))

    result = advanced_search(
        conditions=[
            {"field": "title", "operation": "contains", "value": "quantum"},
            {"field": "year", "operation": "isGreaterThan", "value": "2020"},
        ],
        join_mode="all",
        limit=10,
        ctx=DummyContext(),
    )

    assert "Quantum Networks and Learning" in result
    assert "Classical Literature Review" not in result
    assert "Ignored Attachment" not in result


def test_advanced_search_rejects_unknown_operation(monkeypatch):
    monkeypatch.setattr(search_mod, "get_zotero_client", lambda: FakeZotero([]))

    result = advanced_search(
        conditions=[{"field": "title", "operation": "regex", "value": ".*"}],
        ctx=DummyContext(),
    )

    assert "Unsupported operation" in result
