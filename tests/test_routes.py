"""Route-level smoke + validation tests via the Flask test client."""
import json
import os

from tests.conftest import sample_item


def test_home_shows_the_three_doors(client):
    resp = client.get("/")
    assert resp.status_code == 200
    for label in (b"Scan", b"Pantry", b"Statistics"):
        assert label in resp.data


def test_meals_page_renders(client):
    resp = client.get("/meals")
    assert resp.status_code == 200
    assert b"Build a meal" in resp.data


def test_save_edit_and_delete_meal(client):
    import json
    client.post("/items", data=sample_item(name="Rice", barcode="555", units=2,
                                           weight_value=400, weight_unit="g", calories=1400))
    comps = json.dumps([{"name": "Rice", "barcode": "555", "grams": 500,
                         "kcal_per_g": 3.5, "unit": "g"}])
    resp = client.post("/meals", data={"name": "Bowl", "components": comps},
                       follow_redirects=True)
    assert b"Meal saved" in resp.data
    assert b"Bowl" in resp.data                       # shows in the menu
    # the edit page for a saved meal renders and prefills (tojson used to crash here)
    resp = client.get("/meals/1/edit")
    assert resp.status_code == 200
    assert b"Edit meal" in resp.data and b"Rice" in resp.data
    # saving a meal never touches pantry stock
    it = client.get("/pantry").data
    assert b"Rice" in it
    # delete it
    resp = client.post("/meals/1/delete", follow_redirects=True)
    assert b"removed from your menu" in resp.data


def test_meal_card_flags_unsourced_component(client):
    import json
    # product exists -> can source it; save a meal from it
    client.post("/items", data=sample_item(name="Rice", barcode="777", units=1,
                                           weight_value=400, weight_unit="g", calories=1400))
    comps = json.dumps([{"name": "Rice", "barcode": "777", "grams": 300,
                         "kcal_per_g": 3.5, "unit": "g"}])
    client.post("/meals", data={"name": "Bowl", "components": comps})
    assert b"menu-warn" not in client.get("/meals").data     # still in stock, no flag
    # delete the product from the pantry -> meal can't be cooked from stock
    client.post("/items/1/delete")
    page = client.get("/meals").data
    assert b"menu-warn" in page                              # ⚠️ marker shows


def test_meals_builder_has_nutrient_bars(client):
    resp = client.get("/meals/new")            # builder moved to its own page
    assert b'class="nbars"' in resp.data
    assert b"Protein" in resp.data and b"Calcium" in resp.data


def test_meals_menu_is_default_and_links_to_builder(client):
    resp = client.get("/meals")
    assert resp.status_code == 200
    assert b"Your menu" in resp.data
    assert b'href="/meals/new"' in resp.data     # Build a meal button
    assert b'class="meal-builder"' not in resp.data   # builder not on the menu page


def test_meal_plan_endpoint_persists_shared_servings(client):
    import json
    client.post("/items", data=sample_item(name="Rice", barcode="900", units=2,
                                           weight_value=500, weight_unit="g", calories=1750))
    comps = json.dumps([{"name": "Rice", "barcode": "900", "grams": 200,
                         "kcal_per_g": 3.5, "unit": "g"}])
    client.post("/meals", data={"name": "Bowl", "components": comps})
    # set a shared plan via the endpoint
    r = client.post("/meals/1/plan", data={"servings": "3"})
    assert r.status_code == 200 and r.get_json()["servings"] == 3
    # persisted: the stepper renders with the stored value + planner scaffolding
    page = client.get("/meals").data
    assert b"js-plan-data" in page and b"js-plan-step" in page
    assert b'value="3"' in page
    # unknown meal -> 404 json
    assert client.post("/meals/999/plan", data={"servings": "1"}).status_code == 404


def test_meal_needs_items(client):
    resp = client.post("/meals", data={"name": "Empty", "components": "[]"},
                       follow_redirects=True)
    assert b"at least one item" in resp.data


def test_statistics_renders(client):
    resp = client.get("/statistics")
    assert resp.status_code == 200
    assert b"days of food" in resp.data


def test_statistics_estimates_days_from_calories(client):
    # default household budgets 4500 kcal/day (1 man 2500 + 1 woman 2000)
    client.post("/items", data=sample_item(calories=4500, units=2))   # 9000 kcal
    resp = client.get("/statistics")
    assert resp.status_code == 200
    assert b"days of food" in resp.data
    assert b"9,000" in resp.data          # grouped total kcal on the shelves


def test_pantry_search_by_name(client):
    client.post("/items", data=sample_item(name="Black Beans"))
    client.post("/items", data=sample_item(name="White Rice"))
    resp = client.get("/pantry?q=bean")            # case-insensitive substring
    assert b"Black Beans" in resp.data and b"White Rice" not in resp.data
    assert b"Filtered" in resp.data


def test_pantry_search_combines_name_and_macro(client):
    client.post("/items", data=sample_item(name="Peanut Butter",
                                            nutrition='{"per_100g": {"fat": 20}}'))
    client.post("/items", data=sample_item(name="Peanut Puffs",
                                            nutrition='{"per_100g": {"fat": 0}}'))
    resp = client.get("/pantry?q=peanut&macro=fat")
    assert b"Peanut Butter" in resp.data and b"Peanut Puffs" not in resp.data


def test_pantry_filters_by_nova(client):
    ultra = '{"nova_group": 4, "per_100g": {"fat": 5}}'
    fresh = '{"nova_group": 1, "per_100g": {"fat": 1}}'
    client.post("/items", data=sample_item(name="Chips", nutrition=ultra))
    client.post("/items", data=sample_item(name="Apples", nutrition=fresh))
    resp = client.get("/pantry?nova=4")
    assert b"Chips" in resp.data
    assert b"Apples" not in resp.data
    assert b"Filtered" in resp.data           # banner shows
    # clearing brings everything back
    both = client.get("/pantry").data
    assert b"Chips" in both and b"Apples" in both


def test_pantry_filters_by_macro(client):
    fatty = '{"per_100g": {"fat": 20, "proteins": 0}}'
    lean = '{"per_100g": {"fat": 0, "proteins": 15}}'
    client.post("/items", data=sample_item(name="Butter", nutrition=fatty))
    client.post("/items", data=sample_item(name="Tofu", nutrition=lean))
    resp = client.get("/pantry?macro=fat")
    assert b"Butter" in resp.data and b"Tofu" not in resp.data


def test_pantry_filters_by_nutriscore(client):
    soda = '{"nutriscore": "e", "per_100g": {"fat": 5}}'
    spinach = '{"nutriscore": "a", "per_100g": {"fat": 1}}'
    client.post("/items", data=sample_item(name="Soda", nutrition=soda))
    client.post("/items", data=sample_item(name="Spinach", nutrition=spinach))
    resp = client.get("/pantry?grade=e")
    assert b"Soda" in resp.data and b"Spinach" not in resp.data
    assert b"Nutri-Score E" in resp.data       # banner label


def test_pantry_filters_by_expiry_year(client):
    client.post("/items", data=sample_item(name="Old Beans", expire_date="2026-08-01"))
    client.post("/items", data=sample_item(name="New Rice", expire_date="2027-03-01"))
    resp = client.get("/pantry?year=2026")
    assert b"Old Beans" in resp.data and b"New Rice" not in resp.data
    assert b"expires 2026" in resp.data          # banner label


def test_statistics_shows_food_by_year(client):
    client.post("/items", data=sample_item(name="Beans", expire_date="2027-01-01"))
    resp = client.get("/statistics")
    assert b"Food by expiry year" in resp.data
    assert b"2027" in resp.data


def test_statistics_shows_days_of_nutrition(client):
    client.post("/items", data=sample_item(
        name="Lentils", weight_value=500, weight_unit="g", calories=1750,
        nutrition='{"per_100g": {"energy_kcal": 350, "proteins": 25, "fiber": 8}}'))
    resp = client.get("/statistics")
    assert b"Days of nutrition" in resp.data
    assert b"Protein" in resp.data and b"Fibre" in resp.data


def test_pantry_filters_by_extra_nutrient(client):
    client.post("/items", data=sample_item(name="Bran",
                                            nutrition='{"per_100g": {"fiber": 12}}'))
    client.post("/items", data=sample_item(name="Oil",
                                            nutrition='{"per_100g": {"fiber": 0}}'))
    resp = client.get("/pantry?macro=fiber")
    assert b"Bran" in resp.data and b"Oil" not in resp.data
    assert b"contains fibre" in resp.data          # banner label


def test_nutrient_targets_saved_from_settings(client):
    client.post("/settings", data={"protein_per_adult": "60", "iron_per_adult": "12"},
                follow_redirects=True)
    page = client.get("/settings").data
    assert b'name="protein_per_adult"' in page and b'value="60"' in page


def test_pantry_bad_filter_is_ignored(client):
    client.post("/items", data=sample_item(name="Beans"))
    resp = client.get("/pantry?nova=nonsense&macro=bogus&grade=z")
    assert resp.status_code == 200
    assert b"Beans" in resp.data
    assert b"Filtered" not in resp.data        # invalid filter -> no banner


def test_statistics_counts_expired_by_default_and_can_exclude(client):
    # an expired item (past date) that carries calories
    client.post("/items", data=sample_item(name="OldMilk", calories=1000,
                                            units=1, expire_date="2020-01-01"))
    assert b"across 1 counted item" in client.get("/statistics").data     # counted by default
    # flip the exclude-expired toggle on
    client.post("/settings", data={
        "expiry_warn_days": "7", "backup_keep": "5", "exclude_expired": "yes",
    })
    assert b"across 0 counted items" in client.get("/statistics").data     # now left out


def test_excluded_item_is_left_out_of_statistics(client):
    client.post("/items", data=sample_item(name="Counted", calories=4500, units=1))
    client.post("/items", data=sample_item(name="Hidden", calories=9999, units=1,
                                           exclude_stats="yes"))
    resp = client.get("/statistics")
    assert resp.status_code == 200
    assert b"across 1 counted item" in resp.data     # only the non-excluded item
    # but the excluded item still lives in the pantry
    assert b"Hidden" in client.get("/pantry").data


def test_statistics_shows_days_of_water(client):
    client.post("/items", data=sample_item(name="Spring Water", weight_value=2,
                                            weight_unit="l", is_fluid="yes"))
    resp = client.get("/statistics")
    assert resp.status_code == 200
    assert b"days of water" in resp.data
    # the water metric drills into the pantry filtered to fluids
    assert b"fluid=1" in resp.data


def test_pantry_fluid_filter_shows_only_drinking_fluids(client):
    client.post("/items", data=sample_item(name="Spring Water", weight_value=2,
                                            weight_unit="l", is_fluid="yes"))
    client.post("/items", data=sample_item(name="Dry Beans", calories=4500))
    resp = client.get("/pantry?fluid=1")
    assert resp.status_code == 200
    assert b"Spring Water" in resp.data
    assert b"Dry Beans" not in resp.data


def test_pantry_excluded_filter_shows_only_excluded_items(client):
    client.post("/items", data=sample_item(name="Counted Beans", calories=4500))
    client.post("/items", data=sample_item(name="Hidden Sauce", calories=200,
                                           exclude_stats="yes"))
    resp = client.get("/pantry?excluded=1")
    assert resp.status_code == 200
    assert b"Hidden Sauce" in resp.data
    assert b"Counted Beans" not in resp.data


def test_save_water_settings(client):
    resp = client.post("/settings", data={
        "expiry_warn_days": "7", "backup_keep": "5",
        "water_per_man": "4.0", "water_per_woman": "3.0", "water_per_child": "1.2",
    }, follow_redirects=True)
    assert b"Settings saved" in resp.data
    assert b'value="4.0"' in resp.data
    assert b'value="3.0"' in resp.data


def test_save_household_settings(client):
    resp = client.post("/settings", data={
        "household_men": "3", "household_women": "2", "household_children": "4",
        "cal_per_man": "2600", "cal_per_woman": "2100", "cal_per_child": "1400",
    }, follow_redirects=True)
    assert b"Settings saved" in resp.data
    assert b'value="3"' in resp.data       # men persisted onto the form
    assert b'value="2600"' in resp.data    # kcal/day per man persisted


def test_pantry_empty(client):
    resp = client.get("/pantry")
    assert resp.status_code == 200
    assert b"The shelves are bare" in resp.data


def test_new_item_form_renders(client):
    resp = client.get("/items/new")
    assert resp.status_code == 200
    assert b"Add item" in resp.data


def test_create_item_via_post_loops_to_add_form(client):
    resp = client.post("/items", data=sample_item(), follow_redirects=True)
    assert resp.status_code == 200
    assert b"Item added" in resp.data      # flash confirms the save
    assert b"Add item" in resp.data        # and we looped back to a fresh form
    # the item really persisted
    assert b"Canned beans" in client.get("/pantry").data


def test_create_item_validation_error_returns_400(client):
    resp = client.post("/items", data=sample_item(name=""))
    assert resp.status_code == 400
    assert b"name is required" in resp.data


def test_edit_and_update_item(client):
    client.post("/items", data=sample_item())
    # first (and only) item has id 1
    resp = client.get("/items/1/edit")
    assert resp.status_code == 200
    resp = client.post("/items/1", data=sample_item(name="Chickpeas"), follow_redirects=True)
    assert b"Chickpeas" in resp.data
    assert b"Item updated" in resp.data


def test_edit_missing_item_404(client):
    resp = client.get("/items/999/edit")
    assert resp.status_code == 404


def test_delete_item(client):
    client.post("/items", data=sample_item())
    resp = client.post("/items/1/delete", follow_redirects=True)
    assert resp.status_code == 200
    assert b"Item removed" in resp.data
    assert b"The shelves are bare" in resp.data


def test_settings_page_renders(client):
    resp = client.get("/settings")
    assert resp.status_code == 200
    assert b"Settings" in resp.data


def test_save_settings(client):
    resp = client.post(
        "/settings", data={"expiry_warn_days": "3", "backup_keep": "5"},
        follow_redirects=True,
    )
    assert b"Settings saved" in resp.data
    assert b'value="3"' in resp.data


def test_csv_export_endpoint(client):
    client.post("/items", data=sample_item(name="Rice"))
    resp = client.get("/export.csv")
    assert resp.status_code == 200
    assert resp.mimetype == "text/csv"
    assert b"Rice" in resp.data


def test_backup_now_endpoint(client):
    client.post("/items", data=sample_item())
    resp = client.post("/backup", follow_redirects=True)
    assert resp.status_code == 200
    assert b"Backup created" in resp.data


# --- shelves ---

def test_shelves_page_renders(client):
    resp = client.get("/shelves")
    assert resp.status_code == 200
    assert b"No shelves yet" in resp.data


def test_create_shelf_via_post(client):
    resp = client.post("/shelves", data={"name": "Freezer"}, follow_redirects=True)
    assert b"Shelf added" in resp.data
    assert b"Freezer" in resp.data


def test_duplicate_shelf_shows_error(client):
    client.post("/shelves", data={"name": "Freezer"})
    resp = client.post("/shelves", data={"name": "Freezer"}, follow_redirects=True)
    assert b"already exists" in resp.data


def test_rename_shelf_via_post(client):
    client.post("/shelves", data={"name": "Freezer"})
    resp = client.post("/shelves/1", data={"name": "Chest freezer"}, follow_redirects=True)
    assert b"Shelf renamed" in resp.data
    assert b"Chest freezer" in resp.data


def test_item_shows_under_its_shelf(client):
    client.post("/shelves", data={"name": "Freezer"})
    client.post("/items", data=sample_item(name="Peas", shelf_id=1))
    resp = client.get("/pantry")
    body = resp.data
    # the shelf heading and the item both show
    assert b"Freezer" in body
    assert b"Peas" in body


def test_delete_shelf_unshelves_items(client):
    client.post("/shelves", data={"name": "Freezer"})
    client.post("/items", data=sample_item(name="Peas", shelf_id=1))
    resp = client.post("/shelves/1/delete", follow_redirects=True)
    assert b"now unshelved" in resp.data
    # item survives, now under the Unshelved group
    resp = client.get("/pantry")
    assert b"Peas" in resp.data
    assert b"Unshelved" in resp.data


def test_item_form_lists_shelves(client):
    client.post("/shelves", data={"name": "Freezer"})
    resp = client.get("/items/new")
    assert b"Unshelved" in resp.data
    assert b"Freezer" in resp.data


# --- bulk move ---

def test_move_items_between_shelves(client):
    client.post("/shelves", data={"name": "Freezer"})  # id 1
    client.post("/items", data=sample_item(name="Peas"))   # id 1, unshelved
    client.post("/items", data=sample_item(name="Corn"))   # id 2, unshelved
    resp = client.post(
        "/items/move", data={"item_ids": ["1", "2"], "shelf_id": "1"},
        follow_redirects=True,
    )
    assert b"Moved 2 items" in resp.data
    # both now sit under the Freezer heading
    assert b"Freezer" in resp.data


def test_delete_items_bulk(client):
    client.post("/items", data=sample_item(name="Peas"))
    client.post("/items", data=sample_item(name="Corn"))
    client.post("/items", data=sample_item(name="Keep"))
    resp = client.post("/items/delete", data={"item_ids": ["1", "2"]},
                       follow_redirects=True)
    assert b"Removed 2 items" in resp.data
    body = client.get("/pantry").data
    assert b"Peas" not in body and b"Corn" not in body
    assert b"Keep" in body            # untouched


def test_delete_items_nothing_selected(client):
    resp = client.post("/items/delete", data={}, follow_redirects=True)
    assert b"Nothing selected to delete" in resp.data


def test_move_items_nothing_selected(client):
    resp = client.post("/items/move", data={"shelf_id": "0"}, follow_redirects=True)
    assert b"Nothing selected" in resp.data


def test_move_items_bad_shelf_flashes_error(client):
    client.post("/items", data=sample_item(name="Peas"))
    resp = client.post(
        "/items/move", data={"item_ids": ["1"], "shelf_id": "999"},
        follow_redirects=True,
    )
    # flash renders through Jinja, so the apostrophe comes back HTML-escaped
    assert b"doesn&#39;t exist" in resp.data


# --- pagination + new setting ---

def test_pantry_paginates_a_long_shelf(client):
    client.post("/settings", data={
        "expiry_warn_days": "7", "backup_keep": "5", "shelf_page_size": "2",
    })
    for name in ("Alpha", "Bravo", "Charlie"):
        client.post("/items", data=sample_item(name=name))
    # page 1 of the Unshelved shelf shows the pager
    resp = client.get("/pantry")
    assert b"Page 1 of 2" in resp.data
    # second page reachable via the shelf's own page param (unshelved = s0)
    resp = client.get("/pantry?s0_page=2")
    assert b"Page 2 of 2" in resp.data


def test_save_shelf_page_size_setting(client):
    resp = client.post("/settings", data={
        "expiry_warn_days": "7", "backup_keep": "5", "shelf_page_size": "40",
    }, follow_redirects=True)
    assert b"Settings saved" in resp.data
    assert b'value="40"' in resp.data


# --- scan an item you already own: quick +/- ---

def test_pantry_lookup_found_and_missing(client):
    client.post("/items", data=sample_item(barcode="555"))
    found = client.get("/api/pantry/555").get_json()
    assert found["found"] is True
    assert "/items/" in found["url"]
    assert client.get("/api/pantry/000").get_json()["found"] is False


def test_adjust_resolve_new_barcode_falls_through_to_add_form(client):
    resp = client.get("/items/adjust?barcode=000", follow_redirects=True)
    # nothing on the shelves -> the normal add form, primed with the barcode
    assert resp.status_code == 200
    assert b"Add item" in resp.data


def test_adjust_resolve_single_goes_straight_to_adjust(client):
    client.post("/items", data=sample_item(name="Beans", barcode="555"))
    resp = client.get("/items/adjust?barcode=555", follow_redirects=True)
    assert resp.status_code == 200
    assert b"Beans" in resp.data


def test_adjust_resolve_many_shows_the_picker(client):
    client.post("/items", data=sample_item(name="Beans", barcode="555", expire_date="2027-01-01"))
    client.post("/items", data=sample_item(name="Beans", barcode="555", expire_date="2028-01-01"))
    resp = client.get("/items/adjust?barcode=555")
    assert resp.status_code == 200
    assert b"Pick an entry" in resp.data


def test_change_units_increments(client):
    client.post("/items", data=sample_item(units=2))
    resp = client.post("/items/1/units", data={"delta": "1"}, follow_redirects=True)
    assert resp.status_code == 200
    # the adjust screen shows the new count in the counter
    assert b">3<" in resp.data


def test_change_units_adds_the_units_field_amount(client):
    client.post("/items", data=sample_item(units=2))
    # the "+" menu sends how many to add, not a delta — add 3 -> 5
    resp = client.post("/items/1/units", data={"units": "3"}, follow_redirects=True)
    assert resp.status_code == 200
    assert b">5<" in resp.data


def test_change_units_to_zero_removes_and_returns_to_pantry(client):
    client.post("/items", data=sample_item(units=1))
    resp = client.post("/items/1/units", data={"delta": "-1"}, follow_redirects=True)
    assert b"removed from the pantry" in resp.data
    assert b"The shelves are bare" in resp.data


def test_restock_adds_a_batch_with_new_expiry(client):
    client.post("/items", data=sample_item(name="Beans", barcode="555"))
    resp = client.post(
        "/items/1/restock", data={"expire_date": "2029-01-01", "units": "3"},
        follow_redirects=True,
    )
    assert b"fresh batch" in resp.data
    # a second entry now shares the barcode
    assert b"2029-01-01" in resp.data          # landed on the new batch's adjust screen
    assert client.get("/api/pantry/555").get_json()["found"] is True


# --- barcode lookup + nutrition ---

def test_product_lookup_found(client, monkeypatch):
    monkeypatch.setattr("src.offfacts.lookup",
                        lambda code, country=None: {"name": "Beans", "barcode": code})
    resp = client.get("/api/products/123")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["found"] is True
    assert body["product"]["name"] == "Beans"


def test_product_lookup_not_found(client, monkeypatch):
    monkeypatch.setattr("src.offfacts.lookup", lambda code, country=None: None)
    resp = client.get("/api/products/000")
    assert resp.status_code == 404
    assert resp.get_json()["found"] is False


def test_off_health_probe(client, monkeypatch):
    monkeypatch.setattr("src.offfacts.diagnose",
                        lambda country=None: {"reachable": True, "error": None})
    resp = client.get("/api/health/off")
    assert resp.status_code == 200
    assert resp.get_json()["reachable"] is True


def test_save_off_country_setting(client):
    resp = client.post("/settings", data={
        "expiry_warn_days": "7", "backup_keep": "5",
        "shelf_page_size": "25", "off_country": "ca",
    }, follow_redirects=True)
    assert b"Settings saved" in resp.data
    # the Canada option comes back selected
    assert b'value="ca" selected' in resp.data


def test_create_item_stores_barcode_and_nutrition(client):
    nutrition = json.dumps({"source": "openfoodfacts", "nutriscore": "a",
                            "per_100g": {"proteins": 8}})
    client.post("/items", data=sample_item(barcode="737628064502", nutrition=nutrition))
    # the barcode round-trips onto the edit form
    resp = client.get("/items/1/edit")
    assert b"737628064502" in resp.data


def test_create_item_stores_calories(client):
    client.post("/items", data=sample_item(calories=430))
    resp = client.get("/items/1/edit")
    assert b'name="calories"' in resp.data
    assert b"430" in resp.data          # prefilled on the edit form


def test_create_item_bad_nutrition_json_rejected(client):
    resp = client.post("/items", data=sample_item(nutrition="{not json"))
    assert resp.status_code == 400
    assert b"valid JSON" in resp.data


def test_create_item_downloads_image_on_save(client, monkeypatch):
    # pretend the download succeeds and lands as this filename
    monkeypatch.setattr("src.offfacts.download_image",
                        lambda url, barcode, dest: "737628064502.jpg")
    client.post("/items", data=sample_item(
        barcode="737628064502", image_url="http://x/front.jpg"))
    resp = client.get("/items/1/edit")
    # stored photo path surfaces via the /media/ image src on the edit form
    assert b"737628064502.jpg" in resp.data


def test_media_serves_a_stored_image(client):
    from src import database
    with open(os.path.join(database.images_dir(), "x.png"), "wb") as fh:
        fh.write(b"PNGDATA")
    resp = client.get("/media/x.png")
    assert resp.status_code == 200
    assert resp.data == b"PNGDATA"


# ---- tags ------------------------------------------------------------------

def test_item_save_assigns_and_shows_tags(client):
    client.post("/items", data=sample_item(name="Arroz Basmati", barcode="111",
                                            tags=["Rice", "grain"]),
                follow_redirects=True)
    page = client.get("/pantry").data
    assert b"tag-pill" in page
    assert b"rice" in page and b"grain" in page   # normalised to lower-case


def test_item_edit_prefills_tags(client):
    client.post("/items", data=sample_item(name="Rice", barcode="222", tags=["rice"]))
    form = client.get("/items/1/edit").data
    # the existing tag renders as a chip with a hidden input carrying it
    assert b'name="tags" value="rice"' in form


def test_pantry_filter_by_tag(client):
    client.post("/items", data=sample_item(name="White Rice", barcode="1", tags=["rice"]))
    client.post("/items", data=sample_item(name="Black Beans", barcode="2", tags=["legume"]))
    filtered = client.get("/pantry?tag=rice").data
    assert b"White Rice" in filtered
    assert b"Black Beans" not in filtered


def test_tags_manage_rename_and_delete(client):
    client.post("/items", data=sample_item(name="Rice", barcode="1", tags=["rice"]))
    assert b"rice" in client.get("/tags").data

    client.post("/tags/1", data={"name": "grains"}, follow_redirects=True)
    assert b"grains" in client.get("/tags").data

    client.post("/tags/1/delete", follow_redirects=True)
    assert client.get("/pantry?tag=grains").status_code == 200
    assert b"tag-pill" not in client.get("/pantry").data   # link gone from every item


def test_export_csv_includes_tags(client):
    client.post("/items", data=sample_item(name="Rice", barcode="9", tags=["rice", "grain"]))
    csv = client.get("/export.csv").data.decode()
    assert "tags" in csv.splitlines()[0]                 # header
    assert "grain;rice" in csv                            # joined, alphabetical


def test_meal_with_tag_ingredient_pools_stock(client, conn):
    from src import tags

    # two different rices, both tagged "rice", 1000 g each in stock
    client.post("/items", data=sample_item(name="Arroz Basmati", barcode="A",
                                           units=1, weight_value=1000, calories=3500,
                                           tags=["rice"]))
    client.post("/items", data=sample_item(name="Arroz Branco", barcode="B",
                                           units=1, weight_value=1000, calories=3500,
                                           tags=["rice"]))
    rice = tags.get_or_create(conn, "rice")

    comps = json.dumps([{"name": "rice", "grams": 200, "kcal_per_g": 3.5,
                         "source_type": "tag", "tag_id": rice}])
    client.post("/meals", data={"name": "Rice bowl", "components": comps})

    page = client.get("/meals").data.decode()
    # the planner pools BOTH rices under the tag key: 1000 + 1000 = 2000 g available
    assert '"tag:%d": 2000' % rice in page or '"tag:%d": 2000.0' % rice in page
    assert "\\ud83c\\udff7" in page or "tag:%d" % rice in page   # tag pool present


def test_meal_tag_line_unsourced_when_no_stock(client, conn):
    from src import tags

    client.post("/items", data=sample_item(name="Rice", barcode="A", tags=["rice"]))
    rice = tags.get_or_create(conn, "rice")
    comps = json.dumps([{"name": "rice", "grams": 100, "kcal_per_g": 3.5,
                         "source_type": "tag", "tag_id": rice}])
    client.post("/meals", data={"name": "Bowl", "components": comps})
    assert b"menu-warn" not in client.get("/meals").data     # in stock -> no flag

    client.post("/items/1/delete")                            # last rice gone
    assert b"menu-warn" in client.get("/meals").data          # tag line now unsourced


def test_bulk_tag_selected_items(client):
    # two batches of one product (same barcode) + a different product
    client.post("/items", data=sample_item(name="Arroz Basmati", barcode="R1"))
    client.post("/items", data=sample_item(name="Arroz Basmati", barcode="R1"))
    client.post("/items", data=sample_item(name="Black Beans", barcode="B1"))
    client.post("/items/tag", data={"item_ids": ["1", "2", "3"], "tags": "rice, grain"},
                follow_redirects=True)
    page = client.get("/pantry?tag=rice").data
    assert b"Arroz Basmati" in page
    assert b"Black Beans" in page          # got 'rice' too (it was selected)
    # both tags landed on the products
    assert b"tagged \xe2\x80\x9cgrain\xe2\x80\x9d" not in page   # sanity: filter label differs
    assert client.get("/pantry?tag=grain").status_code == 200


def test_bulk_tag_requires_selection_and_tags(client):
    client.post("/items", data=sample_item(name="Rice", barcode="R1"))
    r = client.post("/items/tag", data={"tags": "rice"}, follow_redirects=True)
    assert b"Nothing selected" in r.data
    r = client.post("/items/tag", data={"item_ids": ["1"], "tags": "  "}, follow_redirects=True)
    assert b"Type a tag" in r.data


def test_search_is_accent_insensitive(client):
    client.post("/items", data=sample_item(name="Açúcar Kirkland", barcode="S1"))
    target = "Açúcar".encode()   # UTF-8 bytes as they appear in the response
    assert target in client.get("/pantry?q=acucar").data      # ASCII query -> accented item
    assert target in client.get("/pantry?q=açúcar").data      # accented query works too
    assert target in client.get("/pantry?q=KIRKLAND").data    # case-insensitive


def test_search_matches_tags(client):
    # a product whose NAME has no "rice", but tagged rice -> found by searching "rice"
    client.post("/items", data=sample_item(name="Arroz Basmati", barcode="R1", tags=["rice"]))
    client.post("/items", data=sample_item(name="Black Beans", barcode="B1"))
    page = client.get("/pantry?q=rice").data
    assert b"Arroz Basmati" in page
    assert b"Black Beans" not in page


def test_search_folds_data_search_blob(client):
    # the client live-search index (data-search) is folded name + tags
    client.post("/items", data=sample_item(name="Café Bom", barcode="C1", tags=["Bebida"]))
    page = client.get("/pantry?tag=bebida").data
    assert b'data-search="cafe bom bebida"' in page


def test_unsourced_tag_meal_keeps_snapshot_calories(client, conn):
    from src import tags as tags_mod

    client.post("/items", data=sample_item(name="Rice", barcode="R1", calories=1400,
                                           weight_value=400, tags=["rice"]))
    rice = tags_mod.get_or_create(conn, "rice")
    comps = json.dumps([{"name": "rice", "grams": 100, "kcal_per_g": 3.5,
                         "source_type": "tag", "tag_id": rice}])
    client.post("/meals", data={"name": "Bowl", "components": comps})

    client.post("/items/1/delete")               # tag now has nothing in stock
    page = client.get("/meals").data
    assert b"menu-warn" in page                   # flagged unsourced...
    assert b"350 kcal" in page                    # ...but keeps 100 g x 3.5 snapshot


# ---- filter pickers: tag + expiry (proposal 0012) ---------------------------

def test_pantry_tag_picker_summary_pill(client):
    client.post("/items", data=sample_item(name="White Rice", barcode="1", tags=["rice"]))
    client.post("/items", data=sample_item(name="Black Beans", barcode="2", tags=["legume"]))
    page = client.get("/pantry").data.decode()
    # one summary pill, reading "Any" while nothing is filtered, and not lit up
    assert "Tag · Any" in page
    assert "js-fpick-btn is-on" not in page
    # every tag is still a chip link inside the panel, so no-JS keeps working
    assert 'href="/pantry?tag=rice"' in page
    assert 'href="/pantry?tag=legume"' in page

    page = client.get("/pantry?tag=rice").data.decode()
    assert "Tag · rice" in page
    assert "js-fpick-btn is-on" in page


def test_pantry_tag_picker_unknown_tag_is_harmless(client):
    client.post("/items", data=sample_item(name="White Rice", barcode="1", tags=["rice"]))
    resp = client.get("/pantry?tag=nope")
    assert resp.status_code == 200
    page = resp.data.decode()
    assert "White Rice" not in page          # nothing carries that tag
    assert "Tag · nope" in page              # the pill still says what you asked for


def test_pantry_tag_picker_chips_keep_other_filters_and_escape_names(client):
    client.post("/items", data=sample_item(name="Café em grão", barcode="1",
                                            expire_date="2031-05-01", tags=["café em grão"]))
    page = client.get("/pantry?year=2031&fluid=1").data.decode()
    # the chip carries the active year + type filter along, and the tag name is
    # url-escaped (space + accent) rather than dumped raw into the href
    assert 'href="/pantry?year=2031&amp;fluid=1&amp;tag=caf%C3%A9+em+gr%C3%A3o"' in page
    # the pill shows the tag as typed, not the escaped form
    page = client.get("/pantry?tag=caf%C3%A9+em+gr%C3%A3o").data.decode()
    assert "Tag · café em grão" in page


def test_pantry_expiry_picker_summary_pill(client):
    client.post("/items", data=sample_item(name="Beans", barcode="1", expire_date="2031-05-01"))
    client.post("/items", data=sample_item(name="Rice", barcode="2", expire_date="2033-01-01"))
    page = client.get("/pantry").data.decode()
    assert "Expiry · Any" in page
    assert page.count("js-fpick-btn is-on") == 0
    # the years are still plain chip links inside the panel
    assert 'href="/pantry?year=2031"' in page and 'href="/pantry?year=2033"' in page

    page = client.get("/pantry?year=2031").data.decode()
    assert "Expiry · 2031" in page
    assert "Tag · Any" in page or "search__lbl\">Tag" not in page   # no tags in this db
    assert page.count("js-fpick-btn is-on") == 1                    # only the expiry pill lights
    assert "Beans" in page and "Rice" not in page


def test_item_form_tag_suggestions_are_a_fixed_strip(client):
    """0013: the 'used before' pills live in a fixed-height strip that never collapses,
    with a 'nothing matches' note the script toggles instead of hiding the box — so the
    page can't change height under the keyboard while you type a tag."""
    client.post("/items", data=sample_item(name="White Rice", barcode="1", tags=["rice"]))
    page = client.get("/items/new").data.decode()
    assert "tag-suggest--strip js-tag-suggest" in page
    assert 'data-tag="rice"' in page
    assert "js-tag-suggest-none" in page and "nothing used before matches" in page


# ---- 0015: meals per day -----------------------------------------------------

def _plan_a_bowl(client, servings):
    """one meal, 500 g of rice a portion, with `servings` dialled in on the planner."""
    client.post("/items", data=sample_item(name="Rice", barcode="555", units=10,
                                           weight_value=400, weight_unit="g", calories=1400))
    comps = json.dumps([{"name": "Rice", "barcode": "555", "grams": 500,
                         "kcal_per_g": 3.5, "unit": "g"}])
    client.post("/meals", data={"name": "Bowl", "components": comps})
    client.post("/meals/1/plan", data={"servings": str(servings)})


def _hero_meal_days(page):
    import re
    m = re.search(rb'hero-metric__num">(\d+)</span>\s*<span class="hero-metric__unit">days of meals', page)
    assert m, "meals tile missing from the hero"
    return int(m.group(1))


def test_statistics_meals_tile_divides_by_meals_per_day(client):
    _plan_a_bowl(client, servings=4)          # default household: 1 man + 1 woman
    page = client.get("/statistics").data
    assert _hero_meal_days(page) == 2         # 4 ÷ (2 people × 1 a day)
    assert b"4 meals" in page                 # the raw count sits under the days
    assert b"1 a day for 2 people" in page

    client.post("/settings", data={"meals_per_day": "2"})
    page = client.get("/statistics").data
    assert _hero_meal_days(page) == 1         # 4 ÷ (2 × 2)
    assert b"4 meals" in page
    assert b"2 a day for 2 people" in page


def test_meals_header_and_plan_json_carry_meals_per_day(client):
    _plan_a_bowl(client, servings=4)
    client.post("/settings", data={"meals_per_day": "2"})
    page = client.get("/meals").data
    assert b'<span class="js-menu-days">1</span>' in page      # 4 ÷ (2 × 2)
    import re
    blob = re.search(rb'class="js-plan-data">(.*?)</script>', page, re.S).group(1)
    assert json.loads(blob)["meals_per_day"] == 2


def test_settings_meals_per_day_rejects_zero_and_junk(client):
    _plan_a_bowl(client, servings=4)
    for bad in ("0", "abc", "-3"):
        client.post("/settings", data={"meals_per_day": bad})
        resp = client.get("/statistics")
        assert resp.status_code == 200                # no zero-division, no 500
        assert _hero_meal_days(resp.data) == 2        # treated as 1 a day
        # and the settings box shows what we'll actually use, not the junk
        box = client.get("/settings").data
        assert b'name="meals_per_day" min="1" step="1"\n                       value="1"' in box


def test_meals_days_round_half_up_on_both_pages(client):
    _plan_a_bowl(client, servings=6)
    client.post("/settings", data={"meals_per_day": "2"})
    # 6 ÷ (2 × 2) = 1.5 -> 2 on the hero (Jinja int(x+0.5)) and the meals header
    assert _hero_meal_days(client.get("/statistics").data) == 2
    assert b'<span class="js-menu-days">2</span>' in client.get("/meals").data
    assert b"6 meals" in client.get("/statistics").data


def test_meals_days_with_nobody_home(client):
    _plan_a_bowl(client, servings=4)
    client.post("/settings", data={"household_men": "0", "household_women": "0",
                                   "household_children": "0", "meals_per_day": "2"})
    resp = client.get("/statistics")
    assert resp.status_code == 200                    # nothing to divide by -> no crash
    # the hero steps aside for the household prompt, same as days of food does
    assert b"Set up your household first" in resp.data
    assert b"days of meals" not in resp.data
