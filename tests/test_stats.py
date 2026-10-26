"""Pantry forecasting — the calories-in vs calories-per-day maths."""
import json
from datetime import date

from src import stats


PEOPLE = {"men": 1, "women": 1, "children": 0}
PER = {"man": 2000, "woman": 2000, "child": 1500}   # 4000 kcal/day for this house


def _item(calories=None, units=1, expired=False):
    return {"calories": calories, "units": units, "expired": expired}


def test_daily_need_sums_the_household():
    assert stats.daily_need(PEOPLE, PER) == 4000
    assert stats.daily_need({"men": 2, "women": 0, "children": 3}, PER) == 2 * 2000 + 3 * 1500


def test_forecast_counts_edible_calories_over_daily_need():
    items = [_item(calories=1000, units=8)]   # 8000 kcal on hand
    f = stats.forecast(items, PEOPLE, PER, today=date(2026, 1, 1))
    assert f["total_kcal"] == 8000
    assert f["counted"] == 1
    assert f["daily_need"] == 4000
    assert f["days"] == 2.0
    assert f["runs_out"] == "2026-01-03"


def test_forecast_skips_missing_and_can_exclude_expired():
    items = [
        _item(calories=1000, units=1),   # counted
        _item(calories=None, units=5),   # missing calorie data
        _item(calories=999, units=1, expired=True),   # expired, set aside
    ]
    f = stats.forecast(items, PEOPLE, PER, include_expired=False)
    assert f["counted"] == 1
    assert f["missing"] == 1
    assert f["expired"] == 1
    assert f["total_kcal"] == 1000


def test_forecast_skips_items_flagged_exclude_stats():
    items = [
        _item(calories=1000, units=1),                              # counted
        {"calories": 5000, "units": 1, "expired": False, "exclude_stats": 1},  # left out
    ]
    f = stats.forecast(items, PEOPLE, PER)
    assert f["counted"] == 1
    assert f["total_kcal"] == 1000


def test_all_aggregations_honour_exclude_stats():
    it = {
        "exclude_stats": 1, "expired": False, "units": 1, "calories": 500,
        "weight_value": 100, "weight_unit": "l", "is_fluid": 1, "shelf": "A",
        "nutrition": '{"nutriscore": "e", "nova_group": 4, "per_100g": {"fat": 10}}',
    }
    people = {"men": 1, "women": 0, "children": 0}
    assert stats.forecast([it], people, {"man": 2000, "woman": 0, "child": 0})["counted"] == 0
    assert stats.water_forecast([it], people, {"man": 2, "woman": 0, "child": 0})["counted"] == 0
    assert stats.nutrient_breakdown([it])["counted"] == 0
    assert stats.calories_by_shelf([it])["rows"] == []


def test_forecast_includes_expired_by_default():
    items = [_item(calories=1000, units=1), _item(calories=500, units=1, expired=True)]
    f = stats.forecast(items, PEOPLE, PER)   # include_expired defaults True
    assert f["counted"] == 2
    assert f["expired"] == 0
    assert f["total_kcal"] == 1500


def test_item_kcal_prefers_explicit_calories():
    assert stats.item_kcal({"calories": 320, "nutrition": None}) == 320


def test_item_kcal_derives_from_energy_and_weight():
    # 250 kcal / 100 g × 400 g = 1000 kcal for the package, even with no calories set
    item = {
        "calories": None,
        "weight_value": 400, "weight_unit": "g",
        "nutrition": '{"per_100g": {"energy_kcal": 250}}',
    }
    assert stats.item_kcal(item) == 1000.0


def test_item_kcal_scales_kg_to_grams():
    item = {
        "calories": "",
        "weight_value": 2, "weight_unit": "kg",
        "nutrition": '{"per_100g": {"energy_kcal": 100}}',
    }
    assert stats.item_kcal(item) == 2000.0   # 100 kcal/100g × 2000 g


def test_item_kcal_none_when_nothing_to_go_on():
    assert stats.item_kcal({"calories": None, "nutrition": None}) is None
    # energy present but no weight -> can't scale it
    assert stats.item_kcal({
        "calories": None, "weight_value": None,
        "nutrition": '{"per_100g": {"energy_kcal": 250}}',
    }) is None


def test_forecast_counts_items_with_only_energy_facts():
    items = [{
        "calories": None, "units": 2, "expired": False,
        "weight_value": 400, "weight_unit": "g",
        "nutrition": '{"per_100g": {"energy_kcal": 250}}',
    }]
    f = stats.forecast(items, PEOPLE, PER)
    assert f["counted"] == 1
    assert f["missing"] == 0
    assert f["total_kcal"] == 2000.0   # 1000 kcal/unit × 2 units


def test_nutrient_breakdown_scales_and_splits_macros():
    items = [{
        "expired": False, "units": 2, "weight_value": 100, "weight_unit": "g",
        "nutrition": '{"nutriscore": "c", "per_100g": '
                     '{"fat": 10, "carbohydrates": 20, "proteins": 5, "sugars": 8, "salt": 1, "fiber": 3}}',
    }]
    b = stats.nutrient_breakdown(items)
    # scale = 100 g × 2 units / 100 = 2  ->  fat 20 g, carb 40 g, protein 10 g
    assert b["counted"] == 1
    assert b["grams"]["fat"] == 20
    assert b["grams"]["carbohydrates"] == 40
    assert b["grams"]["proteins"] == 10
    assert b["grams"]["sugars"] == 16
    # energy: fat 180 + carb 160 + protein 40 = 380 kcal
    assert round(b["macro_total_kcal"]) == 380
    fat = next(s for s in b["macro_segments"] if s["key"] == "fat")
    assert round(fat["pct"]) == 47          # 180 / 380
    assert b["grades"]["c"] == 1 and b["graded"] == 1


def test_nutrient_breakdown_skips_dataless_and_can_exclude_expired():
    items = [
        {"expired": True, "units": 1, "weight_value": 100, "weight_unit": "g",
         "nutrition": '{"per_100g": {"fat": 10}}'},                     # expired
        {"expired": False, "units": 1, "weight_value": 100, "weight_unit": "g",
         "nutrition": None},                                            # no data
    ]
    b = stats.nutrient_breakdown(items, include_expired=False)
    assert b["counted"] == 0
    assert b["graded"] == 0
    assert b["macro_total_kcal"] == 0


def test_nutrient_breakdown_includes_expired_by_default():
    items = [{"expired": True, "units": 1, "weight_value": 100, "weight_unit": "g",
              "nutrition": '{"per_100g": {"fat": 10}}'}]
    b = stats.nutrient_breakdown(items)
    assert b["counted"] == 1
    assert b["grams"]["fat"] == 10


def test_nutrient_breakdown_counts_nova_groups():
    items = [
        {"expired": False, "units": 1, "weight_value": 100, "weight_unit": "g",
         "nutrition": '{"nova_group": 4, "per_100g": {"fat": 1}}'},
        {"expired": False, "units": 1, "weight_value": 100, "weight_unit": "g",
         "nutrition": '{"nova_group": 1, "per_100g": {"fat": 1}}'},
    ]
    b = stats.nutrient_breakdown(items)
    assert b["nova"][4] == 1 and b["nova"][1] == 1
    assert b["nova_total"] == 2 and b["nova_max"] == 1


def test_calories_by_shelf_totals_and_sorts():
    items = [
        {"expired": False, "units": 2, "calories": 100, "shelf": "Pantry"},   # 200
        {"expired": False, "units": 1, "calories": 500, "shelf": "Freezer"},  # 500
        {"expired": False, "units": 1, "calories": 50, "shelf": None},        # 50 -> Unshelved
        {"expired": True, "units": 9, "calories": 999, "shelf": "Pantry"},    # excluded
    ]
    by = stats.calories_by_shelf(items, include_expired=False)
    assert by["rows"][0] == {"shelf": "Freezer", "kcal": 500}   # biggest first
    assert by["max"] == 500
    shelves = {r["shelf"]: r["kcal"] for r in by["rows"]}
    assert shelves == {"Freezer": 500, "Pantry": 200, "Unshelved": 50}


def test_food_by_year_groups_units_and_sorts():
    items = [
        {"expired": False, "units": 2, "expire_date": "2027-05-01"},
        {"expired": False, "units": 3, "expire_date": "2026-11-20"},
        {"expired": False, "units": 1, "expire_date": "2027-01-15"},   # same year as first
        {"expired": False, "units": 4, "expire_date": None},           # undated bucket
        {"expired": True, "units": 9, "expire_date": "2025-01-01"},    # excluded
        {"expired": False, "units": 5, "exclude_stats": True, "expire_date": "2026-01-01"},
    ]
    by = stats.food_by_year(items, include_expired=False)
    assert by["rows"] == [
        {"year": 2026, "units": 3},
        {"year": 2027, "units": 3},   # 2 + 1
        {"year": None, "units": 4},   # undated last
    ]
    assert by["max"] == 4


def test_food_by_year_includes_expired_by_default():
    items = [{"expired": True, "units": 2, "expire_date": "2025-06-01"}]
    by = stats.food_by_year(items)
    assert by["rows"] == [{"year": 2025, "units": 2}]


def _nut_item(units=1, weight_value=100, weight_unit="g", expired=False, **per_100):
    return {
        "units": units, "weight_value": weight_value, "weight_unit": weight_unit,
        "expired": expired, "nutrition": json.dumps({"per_100g": per_100}),
    }


TARGETS = {"proteins": 50.0, "fiber": 30.0, "calcium": 1000.0,
           "iron": 14.0, "potassium": 3500.0}


def test_nutrient_supply_days_and_sort():
    # 2 units × 100 g × 25 g protein/100 = 50 g protein total; household of 2 needs
    # 2 × 50 = 100 g/day -> 0.5 days. Fibre: 2×100×15/100 = 30 g; need 60 -> 0.5 too.
    items = [_nut_item(units=2, proteins=25, fiber=15)]
    s = stats.nutrient_supply(items, headcount=2, targets=TARGETS, calorie_days=10)
    by = {r["key"]: r for r in s["rows"]}
    assert by["proteins"]["days"] == 0.5
    assert by["fiber"]["days"] == 0.5
    # calories are NOT a row here — the tower is nutrients only
    assert "energy" not in by
    # but calorie_days is still the yardstick: nutrients below it are flagged short
    assert by["proteins"]["short"] is True


def test_nutrient_supply_drops_uncarried_and_respects_exclusions():
    items = [
        _nut_item(units=1, proteins=10),                      # protein only
        {"units": 1, "exclude_stats": True, "weight_value": 100,
         "weight_unit": "g", "nutrition": json.dumps({"per_100g": {"iron": 99}})},
        _nut_item(units=1, calcium=500, expired=True),        # expired
    ]
    s = stats.nutrient_supply(items, headcount=1, targets=TARGETS,
                              include_expired=False, calorie_days=None)
    keys = {r["key"] for r in s["rows"]}
    assert keys == {"proteins"}          # iron excluded, calcium expired-out, no cal row


def test_nutrient_supply_no_household_yields_no_rows():
    items = [_nut_item(proteins=10)]
    assert stats.nutrient_supply(items, headcount=0, targets=TARGETS)["rows"] == []


def test_intake_load_flags_over_guideline():
    # 400 g salt over 10 days = 40 g/day for a 2-person house (limit 10 g) -> over
    rows = stats.intake_load({"sugars": 100.0, "salt": 400.0}, days=10, people=2)
    salt = next(r for r in rows if r["key"] == "salt")
    assert salt["per_day"] == 40.0 and salt["limit"] == 10.0 and salt["over"] is True
    sugar = next(r for r in rows if r["key"] == "sugars")
    assert sugar["per_day"] == 10.0 and sugar["over"] is False   # 10/day vs 100 limit


def test_intake_load_none_without_basis():
    assert stats.intake_load({"salt": 5}, days=None, people=2) is None
    assert stats.intake_load({"salt": 5}, days=10, people=0) is None


def test_filter_by_nutrition_macro_and_nova():
    items = [
        {"name": "A", "nutrition": '{"nova_group": 4, "per_100g": {"fat": 10, "proteins": 0}}'},
        {"name": "B", "nutrition": '{"nova_group": 1, "per_100g": {"fat": 0, "proteins": 8}}'},
        {"name": "C", "nutrition": None},
    ]
    fat = [it["name"] for it in stats.filter_by_nutrition(items, macro="fat")]
    assert fat == ["A"]                       # only A has fat > 0
    prot = [it["name"] for it in stats.filter_by_nutrition(items, macro="proteins")]
    assert prot == ["B"]
    nova4 = [it["name"] for it in stats.filter_by_nutrition(items, nova=4)]
    assert nova4 == ["A"]


def test_filter_by_nutrition_grade():
    items = [
        {"name": "Soda", "nutrition": '{"nutriscore": "e", "per_100g": {"fat": 1}}'},
        {"name": "Kale", "nutrition": '{"nutriscore": "a", "per_100g": {"fat": 1}}'},
        {"name": "Plain", "nutrition": None},
    ]
    assert [it["name"] for it in stats.filter_by_nutrition(items, grade="e")] == ["Soda"]
    assert [it["name"] for it in stats.filter_by_nutrition(items, grade="a")] == ["Kale"]


def test_water_forecast_counts_only_flagged_fluids():
    items = [
        {"is_fluid": 1, "expired": False, "units": 2, "weight_value": 1.5, "weight_unit": "l"},   # 3 L
        {"is_fluid": 1, "expired": False, "units": 1, "weight_value": 500, "weight_unit": "ml"},  # 0.5 L
        {"is_fluid": 0, "expired": False, "units": 1, "weight_value": 2, "weight_unit": "l"},      # not fluid
    ]
    people = {"men": 1, "women": 1, "children": 0}
    per = {"man": 3.5, "woman": 3.5, "child": 1.5}                 # 7 L/day
    f = stats.water_forecast(items, people, per, today=date(2026, 1, 1))
    assert round(f["total_l"], 2) == 3.5 and f["counted"] == 2
    assert f["daily_need"] == 7.0 and f["days"] == 0.5
    assert f["runs_out"] == "2026-01-01"


def test_water_forecast_no_people_has_no_estimate():
    items = [{"is_fluid": 1, "expired": False, "units": 1, "weight_value": 1, "weight_unit": "l"}]
    f = stats.water_forecast(items, {"men": 0, "women": 0, "children": 0},
                             {"man": 3, "woman": 3, "child": 1})
    assert f["days"] is None and f["daily_need"] == 0


def test_water_forecast_can_exclude_expired():
    items = [{"is_fluid": 1, "expired": True, "units": 1, "weight_value": 2, "weight_unit": "l"}]
    people = {"men": 1, "women": 0, "children": 0}
    per = {"man": 2.0, "woman": 2.0, "child": 1.0}
    assert stats.water_forecast(items, people, per, include_expired=False)["counted"] == 0
    assert stats.water_forecast(items, people, per)["counted"] == 1


def test_forecast_with_no_people_has_no_estimate():
    f = stats.forecast([_item(calories=500)], {"men": 0, "women": 0, "children": 0}, PER)
    assert f["daily_need"] == 0
    assert f["days"] is None
    assert f["runs_out"] is None
