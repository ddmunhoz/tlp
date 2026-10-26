"""Meals — the meal-prep menu: CRUD + the kcal snapshot."""
import pytest

from src import meals


def _component(name="Rice", grams=100, kcal_per_g=3.5, barcode="111", unit="g", **nutrients):
    return {"name": name, "grams": grams, "kcal_per_g": kcal_per_g,
            "barcode": barcode, "unit": unit, **nutrients}


def test_create_and_get_meal(conn):
    mid = meals.create_meal(conn, "Chili night",
                            [_component(), _component(name="Beans", grams=200, kcal_per_g=1.0)])
    meal = meals.get_meal(conn, mid)
    assert meal["name"] == "Chili night"
    assert len(meal["items"]) == 2
    # 100×3.5 + 200×1.0 = 550 kcal
    assert meal["total_kcal"] == 550


def test_meal_requires_name_and_items(conn):
    with pytest.raises(meals.MealError):
        meals.create_meal(conn, "  ", [_component()])
    with pytest.raises(meals.MealError):
        meals.create_meal(conn, "Empty", [])


def test_bad_component_rejected(conn):
    with pytest.raises(meals.MealError):
        meals.create_meal(conn, "M", [{"name": "X", "grams": 0, "kcal_per_g": 1}])
    with pytest.raises(meals.MealError):
        meals.create_meal(conn, "M", [{"name": "", "grams": 10, "kcal_per_g": 1}])


def test_update_and_delete_meal(conn):
    mid = meals.create_meal(conn, "M", [_component()])
    meals.update_meal(conn, mid, "M2", [_component(name="Oats", grams=50, kcal_per_g=4.0)])
    meal = meals.get_meal(conn, mid)
    assert meal["name"] == "M2" and meal["items"][0]["name"] == "Oats"
    assert meals.delete_meal(conn, mid) is True
    assert meals.get_meal(conn, mid) is None


def test_list_meals_newest_first_with_totals(conn):
    meals.create_meal(conn, "First", [_component(grams=100, kcal_per_g=2.0)])   # 200
    meals.create_meal(conn, "Second", [_component(grams=100, kcal_per_g=5.0)])  # 500
    listed = meals.list_meals(conn)
    assert [m["name"] for m in listed] == ["Second", "First"]
    assert listed[0]["total_kcal"] == 500


def test_snapshot_keeps_calories_independent_of_stock(conn):
    # a meal's calories come from the stored kcal_per_g snapshot, not live stock
    mid = meals.create_meal(conn, "Ghost", [_component(name="Rice", barcode="000",
                                                       grams=100, kcal_per_g=3.5)])
    assert meals.get_meal(conn, mid)["total_kcal"] == 350


def test_nutrient_snapshot_totals(conn):
    # per-gram nutrients snapshot and total as grams × per-gram, summed over lines
    comps = [
        _component(name="Lentils", grams=200, kcal_per_g=1.2,
                   protein_per_g=0.25, fiber_per_g=0.08, calcium_per_g=0.35),  # mg/g
        _component(name="Spinach", grams=100, kcal_per_g=0.23,
                   protein_per_g=0.03, calcium_per_g=0.99),                     # no fibre
    ]
    mid = meals.create_meal(conn, "Bowl", comps)
    n = meals.get_meal(conn, mid)["nutrients"]
    assert round(n["proteins"], 2) == 53.0     # 200×0.25 + 100×0.03
    assert round(n["fiber"], 2) == 16.0        # only Lentils carried fibre: 200×0.08
    assert round(n["calcium"], 2) == 169.0     # 200×0.35 + 100×0.99


def test_nutrient_absent_when_no_line_carries_it(conn):
    mid = meals.create_meal(conn, "Plain", [_component()])   # no nutrient fields
    n = meals.get_meal(conn, mid)["nutrients"]
    assert n == {}                                            # nothing to draw a bar from


def test_planned_servings_persist_and_clamp(conn):
    mid = meals.create_meal(conn, "Chili", [_component()])
    assert meals.get_meal(conn, mid)["planned_servings"] == 0     # default
    assert meals.set_planned(conn, mid, 3) == 3
    assert meals.get_meal(conn, mid)["planned_servings"] == 3
    assert meals.set_planned(conn, mid, -5) == 0                  # clamped non-negative
    assert meals.set_planned(conn, mid, "bad") == 0               # non-int -> 0


def test_set_planned_missing_meal(conn):
    assert meals.set_planned(conn, 999, 2) is None
