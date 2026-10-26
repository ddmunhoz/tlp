"""CRUD + validation for pantry items — happy paths and the ways it should fail."""
import os

import pytest

from src import database, models, shelves
from tests.conftest import sample_item


def _write_image(name):
    """Drop a throwaway image file into the (per-test) images dir."""
    path = os.path.join(database.images_dir(), name)
    with open(path, "wb") as fh:
        fh.write(b"IMGDATA")
    return path


def test_create_and_get_item(conn):
    item_id = models.create_item(conn, sample_item())
    item = models.get_item(conn, item_id)
    assert item["name"] == "Canned beans"
    assert item["units"] == 3
    assert item["weight_value"] == 400
    assert item["weight_unit"] == "g"
    # reserved-for-later columns should come back null, not blow up
    assert item["barcode"] is None
    assert item["nutrition"] is None


def test_units_defaults_to_one_when_blank(conn):
    item_id = models.create_item(conn, sample_item(units=""))
    assert models.get_item(conn, item_id)["units"] == 1


def test_list_orders_by_soonest_expiry(conn):
    models.create_item(conn, sample_item(name="Later", expire_date="2027-06-01"))
    models.create_item(conn, sample_item(name="Sooner", expire_date="2026-09-01"))
    names = [i["name"] for i in models.list_items(conn)]
    assert names == ["Sooner", "Later"]


def test_update_item(conn):
    item_id = models.create_item(conn, sample_item())
    ok = models.update_item(conn, item_id, sample_item(name="Chickpeas", units=5))
    assert ok is True
    item = models.get_item(conn, item_id)
    assert item["name"] == "Chickpeas"
    assert item["units"] == 5


def test_update_missing_item_returns_false(conn):
    assert models.update_item(conn, 999, sample_item()) is False


def test_delete_item(conn):
    item_id = models.create_item(conn, sample_item())
    assert models.delete_item(conn, item_id) is True
    assert models.get_item(conn, item_id) is None
    assert models.delete_item(conn, item_id) is False


def test_delete_removes_the_unreferenced_image(conn):
    img = _write_image("111.jpg")
    item_id = models.create_item(conn, sample_item(barcode="111", photo_path="111.jpg"))
    models.delete_item(conn, item_id)
    assert not os.path.exists(img)   # nothing else points at it -> gone


def test_delete_keeps_an_image_a_sibling_still_uses(conn):
    img = _write_image("222.jpg")
    a = models.create_item(conn, sample_item(barcode="222", photo_path="222.jpg"))
    b = models.create_item(conn, sample_item(barcode="222", photo_path="222.jpg"))
    models.delete_item(conn, a)
    assert os.path.exists(img)       # b still references it
    models.delete_item(conn, b)
    assert not os.path.exists(img)   # last referrer gone -> cleaned up


def test_update_prunes_the_photo_it_replaced(conn):
    old = _write_image("old.jpg")
    _write_image("new.jpg")
    item_id = models.create_item(conn, sample_item(photo_path="old.jpg"))
    models.update_item(conn, item_id, sample_item(photo_path="new.jpg"))
    assert not os.path.exists(old)
    assert os.path.exists(os.path.join(database.images_dir(), "new.jpg"))


# --- failure paths ---

def test_missing_name_rejected(conn):
    with pytest.raises(models.ValidationError):
        models.create_item(conn, sample_item(name="  "))


def test_missing_expire_date_rejected(conn):
    with pytest.raises(models.ValidationError):
        models.create_item(conn, sample_item(expire_date=""))


def test_bad_expire_date_rejected(conn):
    with pytest.raises(models.ValidationError):
        models.create_item(conn, sample_item(expire_date="not-a-date"))


def test_non_positive_weight_rejected(conn):
    with pytest.raises(models.ValidationError):
        models.create_item(conn, sample_item(weight_value=0))


def test_unknown_weight_unit_rejected(conn):
    with pytest.raises(models.ValidationError):
        models.create_item(conn, sample_item(weight_unit="oz"))


def test_zero_units_rejected(conn):
    with pytest.raises(models.ValidationError):
        models.create_item(conn, sample_item(units=0))


# --- shelves ---

def test_item_can_be_assigned_to_a_shelf(conn):
    sid = shelves.create_shelf(conn, "Freezer")
    item_id = models.create_item(conn, sample_item(shelf_id=sid))
    item = models.get_item(conn, item_id)
    assert item["shelf_id"] == sid
    # list_items resolves the shelf name for callers
    listed = next(i for i in models.list_items(conn) if i["id"] == item_id)
    assert listed["shelf"] == "Freezer"


def test_blank_shelf_means_unshelved(conn):
    item_id = models.create_item(conn, sample_item(shelf_id=""))
    assert models.get_item(conn, item_id)["shelf_id"] is None


def test_bogus_shelf_id_rejected(conn):
    with pytest.raises(models.ValidationError):
        models.create_item(conn, sample_item(shelf_id=4242))


def test_deleting_shelf_unshelves_its_items(conn):
    sid = shelves.create_shelf(conn, "Freezer")
    item_id = models.create_item(conn, sample_item(shelf_id=sid))
    shelves.delete_shelf(conn, sid)
    assert models.get_item(conn, item_id)["shelf_id"] is None


def test_group_by_shelf_buckets_and_orders(conn):
    freezer = shelves.create_shelf(conn, "Freezer")
    models.create_item(conn, sample_item(name="Peas", shelf_id=freezer))
    models.create_item(conn, sample_item(name="Loose crackers"))  # unshelved

    groups = models.group_by_shelf(models.list_items(conn), shelves.list_shelves(conn))
    assert [g["name"] for g in groups] == ["Freezer", "Unshelved"]
    assert groups[0]["items"][0]["name"] == "Peas"
    assert groups[1]["items"][0]["name"] == "Loose crackers"


# --- bulk move ---

def test_delete_items_batch(conn):
    a = models.create_item(conn, sample_item(name="A"))
    b = models.create_item(conn, sample_item(name="B"))
    keep = models.create_item(conn, sample_item(name="Keep"))
    assert models.delete_items(conn, [a, b, 9999]) == 2   # bogus id skipped
    assert models.get_item(conn, a) is None
    assert models.get_item(conn, keep) is not None


def test_delete_items_empty_is_noop(conn):
    assert models.delete_items(conn, []) == 0


def test_move_items_reassigns_a_batch(conn):
    freezer = shelves.create_shelf(conn, "Freezer")
    a = models.create_item(conn, sample_item(name="Peas"))
    b = models.create_item(conn, sample_item(name="Corn"))
    moved = models.move_items(conn, [a, b], freezer)
    assert moved == 2
    assert models.get_item(conn, a)["shelf_id"] == freezer
    assert models.get_item(conn, b)["shelf_id"] == freezer


def test_move_items_to_unshelved(conn):
    freezer = shelves.create_shelf(conn, "Freezer")
    a = models.create_item(conn, sample_item(shelf_id=freezer))
    assert models.move_items(conn, [a], None) == 1
    assert models.get_item(conn, a)["shelf_id"] is None


def test_move_items_rejects_bogus_shelf(conn):
    a = models.create_item(conn, sample_item())
    with pytest.raises(models.ValidationError):
        models.move_items(conn, [a], 4242)
    # the item shouldn't have budged
    assert models.get_item(conn, a)["shelf_id"] is None


def test_move_items_empty_selection_is_a_noop(conn):
    assert models.move_items(conn, [], None) == 0


# --- pagination ---

def _group(n):
    """A throwaway shelf group with n numbered items."""
    return {"id": 1, "name": "Shelf", "items": [{"name": "i%d" % i} for i in range(n)]}


def test_paginate_slices_to_page_size():
    groups = models.paginate_groups([_group(25)], 10, {"s1_page": "2"})
    g = groups[0]
    assert g["pages"] == 3
    assert g["page"] == 2
    assert g["total"] == 25
    assert len(g["items"]) == 10
    assert g["items"][0]["name"] == "i10"
    assert g["has_prev"] and g["has_next"]


def test_paginate_clamps_out_of_range_page():
    groups = models.paginate_groups([_group(5)], 10, {"s1_page": "99"})
    g = groups[0]
    assert g["pages"] == 1
    assert g["page"] == 1
    assert len(g["items"]) == 5
    assert not g["has_next"]


def test_paginate_zero_size_shows_everything():
    groups = models.paginate_groups([_group(40)], 0, {})
    g = groups[0]
    assert g["pages"] == 1
    assert len(g["items"]) == 40


def test_is_fluid_defaults_off_and_can_be_set(conn):
    plain = models.create_item(conn, sample_item())
    assert models.get_item(conn, plain)["is_fluid"] == 0
    wet = models.create_item(conn, sample_item(weight_unit="l", is_fluid="yes"))
    assert models.get_item(conn, wet)["is_fluid"] == 1


def test_exclude_stats_defaults_off_and_can_be_set(conn):
    plain = models.create_item(conn, sample_item())
    assert models.get_item(conn, plain)["exclude_stats"] == 0
    hidden = models.create_item(conn, sample_item(exclude_stats="yes"))
    assert models.get_item(conn, hidden)["exclude_stats"] == 1


# --- barcode lookup + quick unit adjust ---

def test_items_by_barcode_matches_and_orders(conn):
    models.create_item(conn, sample_item(name="Later", barcode="111", expire_date="2027-06-01"))
    models.create_item(conn, sample_item(name="Sooner", barcode="111", expire_date="2026-09-01"))
    models.create_item(conn, sample_item(name="Other", barcode="999"))
    hits = models.items_by_barcode(conn, "111")
    assert [h["name"] for h in hits] == ["Sooner", "Later"]   # soonest expiry first


def test_items_by_barcode_blank_matches_nothing(conn):
    models.create_item(conn, sample_item(barcode=""))
    assert models.items_by_barcode(conn, "") == []


def test_adjust_units_up_and_down(conn):
    item_id = models.create_item(conn, sample_item(units=2))
    assert models.adjust_units(conn, item_id, 1) == 3
    assert models.adjust_units(conn, item_id, -1) == 2
    assert models.get_item(conn, item_id)["units"] == 2


def test_adjust_units_to_zero_removes_item(conn):
    item_id = models.create_item(conn, sample_item(units=1))
    assert models.adjust_units(conn, item_id, -1) == 0
    assert models.get_item(conn, item_id) is None


def test_adjust_units_missing_item_returns_none(conn):
    assert models.adjust_units(conn, 999, 1) is None


def test_restock_clones_into_a_new_entry(conn):
    item_id = models.create_item(conn, sample_item(name="Beans", barcode="777", units=1))
    new_id = models.restock(conn, item_id, "2028-01-01", 4)
    assert new_id != item_id
    fresh = models.get_item(conn, new_id)
    assert fresh["name"] == "Beans"
    assert fresh["barcode"] == "777"
    assert fresh["units"] == 4
    assert fresh["expire_date"] == "2028-01-01"
    # both batches now answer to the same barcode
    assert len(models.items_by_barcode(conn, "777")) == 2


def test_restock_bad_date_rejected(conn):
    item_id = models.create_item(conn, sample_item())
    with pytest.raises(models.ValidationError):
        models.restock(conn, item_id, "not-a-date", 1)


# --- nutrition blob ---

def test_valid_nutrition_json_is_stored(conn):
    blob = '{"nutriscore": "b", "per_100g": {"fat": 3}}'
    item_id = models.create_item(conn, sample_item(nutrition=blob))
    assert models.get_item(conn, item_id)["nutrition"] == blob


def test_bad_nutrition_json_rejected(conn):
    with pytest.raises(models.ValidationError):
        models.create_item(conn, sample_item(nutrition="{nope"))


# --- calories ---

def test_calories_stored(conn):
    item_id = models.create_item(conn, sample_item(calories=430))
    assert models.get_item(conn, item_id)["calories"] == 430


def test_blank_calories_rejected(conn):
    with pytest.raises(models.ValidationError):
        models.create_item(conn, sample_item(calories=""))


def test_zero_calories_allowed(conn):
    # water etc. legitimately has 0 kcal — allowed, just not blank
    item_id = models.create_item(conn, sample_item(calories=0))
    assert models.get_item(conn, item_id)["calories"] == 0


def test_bad_calories_rejected(conn):
    with pytest.raises(models.ValidationError):
        models.create_item(conn, sample_item(calories="lots"))


def test_init_db_migrates_old_table_to_add_calories(data_dir):
    from src import database
    conn = database.get_connection()
    # a pantry_items table from before calories existed
    conn.executescript(
        "CREATE TABLE pantry_items (id INTEGER PRIMARY KEY, name TEXT, units INTEGER,"
        " weight_value REAL, weight_unit TEXT, expire_date TEXT, manufacture_date TEXT,"
        " photo_path TEXT, nutrition TEXT, shelf_id INTEGER, barcode TEXT,"
        " created_at TEXT, updated_at TEXT);"
    )
    conn.commit()
    assert "calories" not in {r["name"] for r in conn.execute("PRAGMA table_info(pantry_items)")}
    database.init_db(conn)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(pantry_items)")}
    assert {"calories", "is_fluid", "exclude_stats"} <= cols
    # and the meals tables get created
    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"meals", "meal_items"} <= tables
    conn.close()
