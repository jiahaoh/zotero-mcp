from zotero_mcp.tools import search as search_mod
from zotero_mcp.tools import tags as tags_mod
from conftest import unwrap

search_notes = unwrap(search_mod.search_notes)
batch_update_tags = unwrap(tags_mod.batch_update_tags)


class DummyContext:
    def info(self, *_args, **_kwargs):
        return None

    def error(self, *_args, **_kwargs):
        return None

    def warn(self, *_args, **_kwargs):
        return None


class FakeZoteroForNotes:
    def __init__(self, notes, parent_items):
        self._notes = notes
        self._parent_items = parent_items
        self.params = {}

    def add_parameters(self, **kwargs):
        self.params.update(kwargs)

    def items(self, **_kwargs):
        return self._notes

    def item(self, key):
        return self._parent_items[key]


class FakeZoteroForTags:
    def __init__(self, items):
        self._items = items
        self.updated = []

    def add_parameters(self, **_kwargs):
        return None

    def items(self):
        return self._items

    def update_item(self, item):
        self.updated.append(item)
        return {"success": True}


def test_search_notes_filters_annotation_blocks(monkeypatch):
    notes = [
        {
            "key": "NOTE0001",
            "data": {
                "itemType": "note",
                "note": "<p>A quantum-computing note.</p>",
                "parentItem": "ITEM0001",
            },
        },
        {
            "key": "NOTE0002",
            "data": {
                "itemType": "note",
                "note": "<p>This note is unrelated.</p>",
                "parentItem": "ITEM0002",
            },
        },
    ]
    parent_items = {
        "ITEM0001": {"data": {"title": "Quantum Book"}},
        "ITEM0002": {"data": {"title": "Other Book"}},
    }
    fake_zot = FakeZoteroForNotes(notes, parent_items)

    monkeypatch.setattr(search_mod, "get_zotero_client", lambda: fake_zot)
    # Mock get_annotations in the notes module (called by search_notes)
    from zotero_mcp.tools import notes as notes_mod

    monkeypatch.setattr(
        notes_mod,
        "get_annotations",
        lambda **_kwargs: (
            "# Annotations\n\n"
            "## Annotation 1\n"
            "**Text:** quantum tunneling\n\n"
            "## Annotation 2\n"
            "**Text:** unrelated topic\n"
        ),
    )

    result = search_notes(query="quantum", limit=20, ctx=DummyContext())

    assert "Annotation 1" in result
    assert "Annotation 2" not in result
    assert "NOTE0001" in result
    assert "NOTE0002" not in result


def test_batch_update_tags_validates_json_array(monkeypatch):
    items = [
        {
            "key": "ITEM0001",
            "data": {
                "itemType": "journalArticle",
                "tags": [{"tag": "old"}],
            },
        }
    ]
    monkeypatch.setattr(tags_mod, "get_zotero_client", lambda: FakeZoteroForTags(items))

    result = batch_update_tags(
        query="anything",
        add_tags='{"not":"a-list"}',
        remove_tags=None,
        limit=5,
        ctx=DummyContext(),
    )

    assert "must be a JSON array or a list of strings" in result or "malformed JSON" in result
