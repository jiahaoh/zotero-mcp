"""Tests for collection management tools and suggest_organization."""

from zotero_mcp.tools.collections import (
    add_items_to_collection as _add_items_to_collection,
    create_collection as _create_collection,
    delete_collection as _delete_collection,
    move_items_between_collections as _move_items_between_collections,
    remove_items_from_collection as _remove_items_from_collection,
    rename_collection as _rename_collection,
)
from zotero_mcp.tools.tags import suggest_organization as _suggest_organization

from conftest import make_collection, make_item, unwrap

# Unwrap FastMCP FunctionTool → plain functions
create_collection = unwrap(_create_collection)
add_items_to_collection = unwrap(_add_items_to_collection)
remove_items_from_collection = unwrap(_remove_items_from_collection)
move_items_between_collections = unwrap(_move_items_between_collections)
rename_collection = unwrap(_rename_collection)
delete_collection = unwrap(_delete_collection)
suggest_organization = unwrap(_suggest_organization)


# ═══════════════════════════════════════════════════════════════════════════
# create_collection
# ═══════════════════════════════════════════════════════════════════════════


class TestCreateCollection:
    def test_rejects_empty_name(self, mock_zot, mock_ctx):
        result = create_collection(name="", ctx=mock_ctx)
        assert "Error" in result

    def test_creates_top_level_collection(self, mock_zot, mock_ctx):
        mock_zot.create_collections.return_value = {
            "successful": {"0": {"key": "NEW1", "data": {"key": "NEW1", "name": "Papers"}}},
            "unchanged": {},
            "failed": {},
        }

        result = create_collection(name="Papers", ctx=mock_ctx)

        assert "Collection Created" in result
        assert "Papers" in result
        assert "NEW1" in result
        assert "top-level" in result
        mock_zot.create_collections.assert_called_once_with([{"name": "Papers"}])

    def test_creates_nested_collection(self, mock_zot, mock_ctx):
        mock_zot.create_collections.return_value = {
            "successful": {"0": {"key": "NEW2", "data": {"key": "NEW2", "name": "Sub"}}},
            "unchanged": {},
            "failed": {},
        }

        result = create_collection(
            name="Sub", parent_collection_key="PARENT1", ctx=mock_ctx
        )

        assert "PARENT1" in result
        mock_zot.create_collections.assert_called_once_with(
            [{"name": "Sub", "parentCollection": "PARENT1"}]
        )

    def test_handles_api_failure(self, mock_zot, mock_ctx):
        mock_zot.create_collections.return_value = {
            "successful": {},
            "unchanged": {},
            "failed": {"0": {"code": 400, "message": "Invalid parent"}},
        }

        result = create_collection(name="Bad", parent_collection_key="NOPE", ctx=mock_ctx)

        assert "Error" in result


# ═══════════════════════════════════════════════════════════════════════════
# add_items_to_collection
# ═══════════════════════════════════════════════════════════════════════════


class TestAddItemsToCollection:
    def test_rejects_empty_inputs(self, mock_zot, mock_ctx):
        assert "Error" in add_items_to_collection(
            collection_key="", item_keys=["K1"], ctx=mock_ctx
        )
        assert "Error" in add_items_to_collection(
            collection_key="C1", item_keys=[], ctx=mock_ctx
        )

    def test_adds_items_to_collection(self, mock_zot, mock_ctx):
        item = make_item("K1", collections=[])
        mock_zot.item.return_value = item

        result = add_items_to_collection(
            collection_key="C1", item_keys=["K1"], ctx=mock_ctx
        )

        assert "Added:** 1" in result
        assert "C1" in item["data"]["collections"]
        mock_zot.update_item.assert_called_once_with(item)

    def test_skips_item_already_in_collection(self, mock_zot, mock_ctx):
        item = make_item("K1", collections=["C1"])
        mock_zot.item.return_value = item

        result = add_items_to_collection(
            collection_key="C1", item_keys=["K1"], ctx=mock_ctx
        )

        assert "Added:** 0" in result
        assert "Skipped:** 1" in result
        mock_zot.update_item.assert_not_called()

    def test_handles_per_item_failure(self, mock_zot, mock_ctx):
        mock_zot.item.side_effect = Exception("not found")

        result = add_items_to_collection(
            collection_key="C1", item_keys=["BAD"], ctx=mock_ctx
        )

        assert "Skipped:** 1" in result


# ═══════════════════════════════════════════════════════════════════════════
# remove_items_from_collection
# ═══════════════════════════════════════════════════════════════════════════


class TestRemoveItemsFromCollection:
    def test_rejects_empty_inputs(self, mock_zot, mock_ctx):
        assert "Error" in remove_items_from_collection(
            collection_key="", item_keys=["K1"], ctx=mock_ctx
        )

    def test_removes_items_from_collection(self, mock_zot, mock_ctx):
        item = make_item("K1", collections=["C1", "C2"])
        mock_zot.item.return_value = item

        result = remove_items_from_collection(
            collection_key="C1", item_keys=["K1"], ctx=mock_ctx
        )

        assert "Removed:** 1" in result
        assert "C1" not in item["data"]["collections"]
        assert "C2" in item["data"]["collections"]

    def test_skips_item_not_in_collection(self, mock_zot, mock_ctx):
        item = make_item("K1", collections=["OTHER"])
        mock_zot.item.return_value = item

        result = remove_items_from_collection(
            collection_key="C1", item_keys=["K1"], ctx=mock_ctx
        )

        assert "Removed:** 0" in result
        assert "Skipped:** 1" in result
        mock_zot.update_item.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════
# move_items_between_collections
# ═══════════════════════════════════════════════════════════════════════════


class TestMoveItemsBetweenCollections:
    def test_rejects_same_source_and_target(self, mock_zot, mock_ctx):
        result = move_items_between_collections(
            source_collection_key="C1",
            target_collection_key="C1",
            item_keys=["K1"],
            ctx=mock_ctx,
        )
        assert "Error" in result

    def test_rejects_empty_inputs(self, mock_zot, mock_ctx):
        result = move_items_between_collections(
            source_collection_key="",
            target_collection_key="C2",
            item_keys=["K1"],
            ctx=mock_ctx,
        )
        assert "Error" in result

    def test_moves_item_between_collections(self, mock_zot, mock_ctx):
        item = make_item("K1", collections=["SRC"])
        mock_zot.item.return_value = item

        result = move_items_between_collections(
            source_collection_key="SRC",
            target_collection_key="DST",
            item_keys=["K1"],
            ctx=mock_ctx,
        )

        assert "Moved:** 1" in result
        assert "DST" in item["data"]["collections"]
        assert "SRC" not in item["data"]["collections"]

    def test_item_already_in_target_but_not_source(self, mock_zot, mock_ctx):
        """Item is already in target and not in source — no changes needed."""
        item = make_item("K1", collections=["DST"])
        mock_zot.item.return_value = item

        result = move_items_between_collections(
            source_collection_key="SRC",
            target_collection_key="DST",
            item_keys=["K1"],
            ctx=mock_ctx,
        )

        # No update needed — neither add nor remove changed anything
        mock_zot.update_item.assert_not_called()

    def test_handles_per_item_failure(self, mock_zot, mock_ctx):
        mock_zot.item.side_effect = Exception("boom")

        result = move_items_between_collections(
            source_collection_key="SRC",
            target_collection_key="DST",
            item_keys=["K1"],
            ctx=mock_ctx,
        )

        assert "Failed:** 1" in result


# ═══════════════════════════════════════════════════════════════════════════
# rename_collection
# ═══════════════════════════════════════════════════════════════════════════


class TestRenameCollection:
    def test_rejects_empty_inputs(self, mock_zot, mock_ctx):
        assert "Error" in rename_collection(
            collection_key="", new_name="New", ctx=mock_ctx
        )
        assert "Error" in rename_collection(
            collection_key="C1", new_name="", ctx=mock_ctx
        )

    def test_renames_collection(self, mock_zot, mock_ctx):
        coll = make_collection("C1", "Old Name")
        mock_zot.collection.return_value = coll

        result = rename_collection(
            collection_key="C1", new_name="New Name", ctx=mock_ctx
        )

        assert "Old Name" in result
        assert "New Name" in result
        assert coll["data"]["name"] == "New Name"
        mock_zot.update_collection.assert_called_once_with(coll)


# ═══════════════════════════════════════════════════════════════════════════
# delete_collection
# ═══════════════════════════════════════════════════════════════════════════


class TestDeleteCollection:
    def test_rejects_empty_key(self, mock_zot, mock_ctx):
        assert "Error" in delete_collection(collection_key="", ctx=mock_ctx)

    def test_deletes_collection_without_items(self, mock_zot, mock_ctx):
        coll = make_collection("C1", "My Collection")
        mock_zot.collection.return_value = coll
        mock_zot.collections_sub.return_value = []

        result = delete_collection(collection_key="C1", ctx=mock_ctx)

        assert "My Collection" in result
        assert "deleted" in result.lower()
        mock_zot.delete_collection.assert_called_once_with(coll)

    def test_warns_about_subcollections(self, mock_zot, mock_ctx):
        coll = make_collection("C1", "Parent")
        sub = make_collection("C2", "Child")
        mock_zot.collection.return_value = coll
        mock_zot.collections_sub.return_value = [sub]

        result = delete_collection(collection_key="C1", ctx=mock_ctx)

        assert "Warning" in result
        assert "Child" in result

    def test_trashes_items_when_requested(self, mock_zot, mock_ctx):
        coll = make_collection("C1", "Doomed")
        item = make_item("K1")
        mock_zot.collection.return_value = coll
        mock_zot.collections_sub.return_value = []
        mock_zot.collection_items.return_value = [item]

        result = delete_collection(
            collection_key="C1", delete_items=True, ctx=mock_ctx
        )

        assert "Items trashed" in result
        assert item["data"]["deleted"] is True
        mock_zot.update_item.assert_called_once()
        mock_zot.delete_collection.assert_called_once()

    def test_skips_attachments_when_trashing(self, mock_zot, mock_ctx):
        coll = make_collection("C1", "Mixed")
        attachment = make_item("A1", item_type="attachment")
        mock_zot.collection.return_value = coll
        mock_zot.collections_sub.return_value = []
        mock_zot.collection_items.return_value = [attachment]

        delete_collection(collection_key="C1", delete_items=True, ctx=mock_ctx)

        mock_zot.update_item.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════
# suggest_organization
# ═══════════════════════════════════════════════════════════════════════════


class TestSuggestOrganization:
    def test_reports_unfiled_items(self, mock_zot, mock_ctx):
        filed = make_item("K1", collections=["C1"], title="Filed Paper")
        unfiled = make_item("K2", collections=[], title="Loose Paper")
        mock_zot.everything.return_value = [filed, unfiled]
        mock_zot.collections.return_value = []

        result = suggest_organization(ctx=mock_ctx)

        assert "Unfiled Items — 1 of 2" in result
        assert "Loose Paper" in result

    def test_reports_small_collections(self, mock_zot, mock_ctx):
        mock_zot.everything.return_value = []
        mock_zot.collections.return_value = [
            make_collection("C1", "Tiny", num_items=1),
            make_collection("C2", "Big", num_items=50),
        ]

        result = suggest_organization(ctx=mock_ctx)

        assert "Small Collections" in result
        assert "Tiny" in result
        assert "Big" not in result

    def test_reports_untagged_items(self, mock_zot, mock_ctx):
        tagged = make_item("K1", tags=["science"])
        untagged = make_item("K2", tags=[], title="No Tags")
        mock_zot.everything.return_value = [tagged, untagged]
        mock_zot.collections.return_value = []

        result = suggest_organization(ctx=mock_ctx)

        assert "Untagged Items — 1" in result
        assert "No Tags" in result

    def test_clean_library(self, mock_zot, mock_ctx):
        """All items are filed and tagged, no small collections."""
        item = make_item("K1", tags=["ok"], collections=["C1"])
        mock_zot.everything.return_value = [item]
        mock_zot.collections.return_value = [
            make_collection("C1", "Good", num_items=10),
        ]

        result = suggest_organization(ctx=mock_ctx)

        assert "All items are filed" in result
        assert "All items have at least one tag" in result
        assert "No very small collections" in result
