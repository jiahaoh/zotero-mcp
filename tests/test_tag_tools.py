"""Tests for tag management tools: rename, delete, merge, statistics."""

from zotero_mcp.server import (
    delete_tag as _delete_tag,
    find_similar_tags,
    get_tag_statistics as _get_tag_statistics,
    merge_tags as _merge_tags,
    rename_tag as _rename_tag,
)

from conftest import make_item, unwrap

# Unwrap FastMCP FunctionTool → plain functions
rename_tag = unwrap(_rename_tag)
delete_tag = unwrap(_delete_tag)
merge_tags = unwrap(_merge_tags)
get_tag_statistics = unwrap(_get_tag_statistics)


# ═══════════════════════════════════════════════════════════════════════════
# rename_tag
# ═══════════════════════════════════════════════════════════════════════════


class TestRenameTag:
    def test_rejects_empty_old_tag(self, mock_zot, mock_ctx):
        result = rename_tag(old_tag="", new_tag="new", ctx=mock_ctx)
        assert "Error" in result
        mock_zot.update_item.assert_not_called()

    def test_rejects_same_tags(self, mock_zot, mock_ctx):
        result = rename_tag(old_tag="same", new_tag="same", ctx=mock_ctx)
        assert "Error" in result

    def test_no_items_with_tag(self, mock_zot, mock_ctx):
        mock_zot.everything.return_value = []
        result = rename_tag(old_tag="nonexistent", new_tag="new", ctx=mock_ctx)
        assert "No items found" in result

    def test_renames_tag_on_matching_items(self, mock_zot, mock_ctx):
        item = make_item("K1", tags=["old-tag", "keep"])
        mock_zot.everything.return_value = [item]

        result = rename_tag(old_tag="old-tag", new_tag="new-tag", ctx=mock_ctx)

        assert "**1**" in result
        tag_names = {t["tag"] for t in item["data"]["tags"]}
        assert "new-tag" in tag_names
        assert "old-tag" not in tag_names
        assert "keep" in tag_names
        mock_zot.update_item.assert_called_once_with(item)

    def test_skips_item_already_carrying_new_tag(self, mock_zot, mock_ctx):
        """If item has both old and new tags, old is removed but new is not duplicated."""
        item = make_item("K1", tags=["old", "new"])
        mock_zot.everything.return_value = [item]

        rename_tag(old_tag="old", new_tag="new", ctx=mock_ctx)

        tag_names = [t["tag"] for t in item["data"]["tags"]]
        assert tag_names.count("new") == 1
        assert "old" not in tag_names

    def test_skips_attachments(self, mock_zot, mock_ctx):
        attachment = make_item("A1", tags=["old"], item_type="attachment")
        mock_zot.everything.return_value = [attachment]

        result = rename_tag(old_tag="old", new_tag="new", ctx=mock_ctx)

        assert "**0**" in result
        mock_zot.update_item.assert_not_called()

    def test_continues_after_per_item_failure(self, mock_zot, mock_ctx):
        item1 = make_item("K1", tags=["old"])
        item2 = make_item("K2", tags=["old"])
        mock_zot.everything.return_value = [item1, item2]
        mock_zot.update_item.side_effect = [Exception("conflict"), None]

        result = rename_tag(old_tag="old", new_tag="new", ctx=mock_ctx)

        # item2 still updated successfully
        assert "**1**" in result
        assert mock_zot.update_item.call_count == 2


# ═══════════════════════════════════════════════════════════════════════════
# delete_tag
# ═══════════════════════════════════════════════════════════════════════════


class TestDeleteTag:
    def test_rejects_empty_tag(self, mock_zot, mock_ctx):
        result = delete_tag(tag="", ctx=mock_ctx)
        assert "Error" in result

    def test_no_items_with_tag(self, mock_zot, mock_ctx):
        mock_zot.everything.return_value = []
        result = delete_tag(tag="gone", ctx=mock_ctx)
        assert "No items found" in result

    def test_removes_tag_from_items(self, mock_zot, mock_ctx):
        item = make_item("K1", tags=["remove-me", "stay"])
        mock_zot.everything.return_value = [item]

        result = delete_tag(tag="remove-me", ctx=mock_ctx)

        assert "**1**" in result
        tag_names = {t["tag"] for t in item["data"]["tags"]}
        assert "remove-me" not in tag_names
        assert "stay" in tag_names

    def test_skips_item_without_tag(self, mock_zot, mock_ctx):
        """Item returned by search but tag already gone (race condition)."""
        item = make_item("K1", tags=["other"])
        mock_zot.everything.return_value = [item]

        result = delete_tag(tag="missing", ctx=mock_ctx)

        assert "**0**" in result
        mock_zot.update_item.assert_not_called()

    def test_skips_attachments(self, mock_zot, mock_ctx):
        attachment = make_item("A1", tags=["target"], item_type="attachment")
        mock_zot.everything.return_value = [attachment]

        result = delete_tag(tag="target", ctx=mock_ctx)

        assert "**0**" in result
        mock_zot.update_item.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════
# merge_tags
# ═══════════════════════════════════════════════════════════════════════════


class TestMergeTags:
    def test_rejects_empty_sources(self, mock_zot, mock_ctx):
        result = merge_tags(source_tags=[], target_tag="t", ctx=mock_ctx)
        assert "Error" in result

    def test_rejects_empty_target(self, mock_zot, mock_ctx):
        result = merge_tags(source_tags=["a"], target_tag="", ctx=mock_ctx)
        assert "Error" in result

    def test_merges_multiple_source_tags(self, mock_zot, mock_ctx):
        item1 = make_item("K1", tags=["ml", "other"])
        item2 = make_item("K2", tags=["ML", "other"])
        # First source tag search returns item1, second returns item2
        mock_zot.everything.side_effect = [[item1], [item2]]

        result = merge_tags(
            source_tags=["ml", "ML"], target_tag="machine-learning", ctx=mock_ctx
        )

        assert "**2**" in result
        for item in [item1, item2]:
            tag_names = {t["tag"] for t in item["data"]["tags"]}
            assert "machine-learning" in tag_names
            assert "ml" not in tag_names
            assert "ML" not in tag_names
            assert "other" in tag_names

    def test_deduplicates_items_across_source_tags(self, mock_zot, mock_ctx):
        """Same item found via multiple source tags → updated only once."""
        shared_item = make_item("K1", tags=["tag-a", "tag-b"])
        mock_zot.everything.side_effect = [[shared_item], [shared_item]]

        result = merge_tags(
            source_tags=["tag-a", "tag-b"], target_tag="merged", ctx=mock_ctx
        )

        assert "**1**" in result
        mock_zot.update_item.assert_called_once()

    def test_does_not_duplicate_target_tag(self, mock_zot, mock_ctx):
        """Item already has target tag — should not get a second copy."""
        item = make_item("K1", tags=["old", "target"])
        mock_zot.everything.side_effect = [[item]]

        merge_tags(source_tags=["old"], target_tag="target", ctx=mock_ctx)

        tag_names = [t["tag"] for t in item["data"]["tags"]]
        assert tag_names.count("target") == 1

    def test_skips_attachments(self, mock_zot, mock_ctx):
        attachment = make_item("A1", tags=["old"], item_type="attachment")
        mock_zot.everything.side_effect = [[attachment]]

        result = merge_tags(
            source_tags=["old"], target_tag="new", ctx=mock_ctx
        )

        assert "**0**" in result
        mock_zot.update_item.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════
# find_similar_tags  (pure function — no mocking needed)
# ═══════════════════════════════════════════════════════════════════════════


class TestFindSimilarTags:
    def test_empty_list(self):
        assert find_similar_tags([], 0.8) == []

    def test_case_only_difference(self):
        """'Machine Learning' vs 'machine learning' should score 1.0."""
        results = find_similar_tags(["Machine Learning", "machine learning"], 0.8)
        assert len(results) == 1
        assert results[0][2] == 1.0

    def test_hyphen_vs_space(self):
        """'deep-learning' vs 'deep learning' normalize to the same string."""
        results = find_similar_tags(["deep-learning", "deep learning"], 0.8)
        assert len(results) == 1
        assert results[0][2] == 1.0

    def test_underscore_vs_space(self):
        results = find_similar_tags(["natural_language", "natural language"], 0.8)
        assert len(results) == 1
        assert results[0][2] == 1.0

    def test_completely_different_tags(self):
        results = find_similar_tags(["physics", "cooking"], 0.8)
        assert results == []

    def test_high_threshold_filters_weak_matches(self):
        results = find_similar_tags(["reinforcement", "reinforcing"], 0.95)
        assert results == []

    def test_moderate_similarity(self):
        results = find_similar_tags(["reinforcement", "reinforcing"], 0.6)
        assert len(results) == 1
        assert 0.6 <= results[0][2] < 1.0

    def test_max_50_results(self):
        """Even with many tags, output is capped at 50 pairs."""
        # Generate tags that are all very similar
        tags = [f"tag{i:03d}" for i in range(200)]
        results = find_similar_tags(tags, 0.5)
        assert len(results) <= 50

    def test_sorted_by_score_descending(self):
        results = find_similar_tags(
            ["neural network", "neural-networks", "neuroscience"],
            0.5,
        )
        scores = [r[2] for r in results]
        assert scores == sorted(scores, reverse=True)


# ═══════════════════════════════════════════════════════════════════════════
# get_tag_statistics
# ═══════════════════════════════════════════════════════════════════════════


class TestGetTagStatistics:
    def test_empty_library(self, mock_zot, mock_ctx):
        mock_zot.everything.return_value = []
        result = get_tag_statistics(ctx=mock_ctx)
        assert "No tags found" in result

    def test_reports_top_tags_and_rare_tags(self, mock_zot, mock_ctx):
        items = [
            make_item("K1", tags=["popular", "rare"]),
            make_item("K2", tags=["popular"]),
            make_item("K3", tags=["popular"]),
        ]
        mock_zot.everything.return_value = items

        result = get_tag_statistics(top_n=5, rare_threshold=1, ctx=mock_ctx)

        assert "Total unique tags" in result
        assert "`popular`" in result
        assert "Rarely-Used" in result
        assert "`rare`" in result

    def test_attachments_excluded_from_counts(self, mock_zot, mock_ctx):
        items = [
            make_item("K1", tags=["real"]),
            make_item("A1", tags=["real", "attachment-tag"], item_type="attachment"),
        ]
        mock_zot.everything.return_value = items

        result = get_tag_statistics(ctx=mock_ctx)

        assert "`real` — 1 items" in result
        assert "attachment-tag" not in result
