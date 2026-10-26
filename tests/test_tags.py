"""Tags — the product-level labels + their links, and the tag-source meal path."""
import pytest

from src import database, meals, tags


def test_normalize_and_clean_names():
    assert tags.normalize("  Rice ") == "rice"
    assert tags.normalize(None) == ""
    # dedupe case-insensitively, drop blanks, preserve first-seen order
    assert tags.clean_names(["Rice", " rice ", "", "Grain"]) == ["rice", "grain"]


def test_product_key_matches_database_helper():
    # tags key products the same way the planner does
    assert tags.product_key("111", "Rice") == database.product_key("111", "Rice")
    assert tags.product_key("", "Arroz Basmati") == "name:arroz basmati"


def test_get_or_create_is_idempotent(conn):
    a = tags.get_or_create(conn, "Rice")
    b = tags.get_or_create(conn, " rice ")   # same tag, different casing/space
    assert a == b
    with pytest.raises(tags.TagError):
        tags.get_or_create(conn, "   ")


def test_set_and_read_product_tags(conn):
    key = database.product_key("111", "Arroz Basmati")
    tags.set_product_tags(conn, key, ["Rice", "grain", "rice"])
    assert tags.tags_for_product(conn, key) == ["grain", "rice"]

    # setting again replaces: drop grain, keep rice
    tags.set_product_tags(conn, key, ["rice"])
    assert tags.tags_for_product(conn, key) == ["rice"]


def test_orphan_tags_are_pruned(conn):
    key = database.product_key("111", "Rice")
    tags.set_product_tags(conn, key, ["rice", "grain"])
    tags.set_product_tags(conn, key, ["rice"])          # grain now unused
    assert tags.all_names(conn) == ["rice"]             # pruned from the vocabulary


def test_tags_for_products_batch_map(conn):
    tags.set_product_tags(conn, "111", ["rice"])
    tags.set_product_tags(conn, "222", ["rice", "canned"])
    m = tags.tags_for_products(conn, ["111", "222", "333"])
    assert m == {"111": ["rice"], "222": ["canned", "rice"]}   # 333 untagged -> absent


def test_product_keys_for_tag_pools_members(conn):
    tags.set_product_tags(conn, "111", ["rice"])
    tags.set_product_tags(conn, "name:arroz branco", ["rice"])
    rice = tags.get_or_create(conn, "rice")
    assert tags.product_keys_for_tag(conn, rice) == {"111", "name:arroz branco"}


def test_list_tags_usage_counts(conn):
    tags.set_product_tags(conn, "111", ["rice", "grain"])
    tags.set_product_tags(conn, "222", ["rice"])
    rows = {t["name"]: t["usage"] for t in tags.list_tags(conn)}
    assert rows == {"rice": 2, "grain": 1}


def test_move_product_tags_on_key_change(conn):
    tags.set_product_tags(conn, "old", ["rice"])
    tags.move_product_tags(conn, "old", "new")
    assert tags.tags_for_product(conn, "old") == []
    assert tags.tags_for_product(conn, "new") == ["rice"]


def test_rename_merges_into_existing(conn):
    tags.set_product_tags(conn, "111", ["rice"])
    tags.set_product_tags(conn, "222", ["grain"])
    grain = tags.get_or_create(conn, "grain")
    tags.rename_tag(conn, grain, "rice")               # merge grain -> rice
    assert tags.all_names(conn) == ["rice"]
    rice = tags.get_or_create(conn, "rice")
    assert tags.product_keys_for_tag(conn, rice) == {"111", "222"}


def test_delete_tag_removes_links(conn):
    tags.set_product_tags(conn, "111", ["rice"])
    rice = tags.get_or_create(conn, "rice")
    assert tags.delete_tag(conn, rice) is True
    assert tags.tags_for_product(conn, "111") == []


def test_tag_referenced_by_meal_survives_prune(conn):
    # a meal targets a tag; even after no product carries it, the tag stays (name kept)
    rice = tags.get_or_create(conn, "rice")
    tags.set_product_tags(conn, "111", ["rice"])
    meals.create_meal(conn, "Bowl", [{
        "name": "rice", "grams": 100, "kcal_per_g": 3.5,
        "source_type": "tag", "tag_id": rice,
    }])
    tags.set_product_tags(conn, "111", [])   # nothing carries rice now
    assert "rice" in tags.all_names(conn)     # not pruned — the meal still needs it


def test_meal_persists_tag_source(conn):
    rice = tags.get_or_create(conn, "rice")
    mid = meals.create_meal(conn, "Bowl", [{
        "name": "rice", "grams": 150, "kcal_per_g": 3.5,
        "source_type": "tag", "tag_id": rice,
    }])
    line = meals.get_meal(conn, mid)["items"][0]
    assert line["source_type"] == "tag"
    assert line["tag_id"] == rice
    assert line["barcode"] is None


def test_tag_meal_component_requires_tag_id(conn):
    with pytest.raises(meals.MealError):
        meals.create_meal(conn, "Bowl", [{
            "name": "rice", "grams": 100, "kcal_per_g": 3.5, "source_type": "tag",
        }])


def test_add_tags_to_keys_keeps_existing(conn):
    tags.set_product_tags(conn, "111", ["rice"])
    n = tags.add_tags_to_keys(conn, ["111", "222"], ["Grain", "grain"])  # dedupes
    assert n == 1
    assert tags.tags_for_product(conn, "111") == ["grain", "rice"]   # rice kept
    assert tags.tags_for_product(conn, "222") == ["grain"]


def test_product_key_strips_name_fallback():
    # padded form input must key to the same product as the stripped DB row
    assert database.product_key("", " Rice ") == database.product_key("", "Rice")


def test_rename_merge_repoints_meal_lines(conn):
    tags.set_product_tags(conn, "111", ["rice"])
    tags.set_product_tags(conn, "222", ["arroz"])
    arroz = tags.get_or_create(conn, "arroz")
    rice = tags.get_or_create(conn, "rice")
    mid = meals.create_meal(conn, "Bowl", [{
        "name": "arroz", "grams": 100, "kcal_per_g": 3.5,
        "source_type": "tag", "tag_id": arroz,
    }])
    tags.rename_tag(conn, arroz, "rice")   # merge arroz -> rice
    line = meals.get_meal(conn, mid)["items"][0]
    assert line["tag_id"] == rice          # meal followed the merge
    assert line["name"] == "rice"


def test_plain_rename_updates_meal_line_name(conn):
    tags.set_product_tags(conn, "111", ["rice"])
    rice = tags.get_or_create(conn, "rice")
    mid = meals.create_meal(conn, "Bowl", [{
        "name": "rice", "grams": 100, "kcal_per_g": 3.5,
        "source_type": "tag", "tag_id": rice,
    }])
    tags.rename_tag(conn, rice, "grains")
    line = meals.get_meal(conn, mid)["items"][0]
    assert line["tag_id"] == rice and line["name"] == "grains"


def test_add_tags_to_keys_no_orphans_on_empty(conn):
    assert tags.add_tags_to_keys(conn, [], ["zzz"]) == 0
    assert tags.add_tags_to_keys(conn, ["111"], []) == 0
    assert tags.all_names(conn) == []      # nothing minted
