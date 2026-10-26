"""Pantry forecasting — the "how long will it last?" maths.

Kept deliberately dumb: total up the edible calories on the shelves, divide by
what the household burns in a day. Water and micronutrients are a separate story
for later; this is a calories-only estimate.
"""
import json
from datetime import date, timedelta


def _base_grams(weight_value, weight_unit):
    """Net weight in the per-100 basis unit (g or ml)."""
    if weight_value in (None, ""):
        return None
    try:
        v = float(weight_value)
    except (TypeError, ValueError):
        return None
    return v * 1000 if weight_unit in ("kg", "l") else v


def item_kcal(item):
    """Best kcal we can pin on ONE unit of this item, or None if we truly can't.

    Prefer the explicit `calories` figure. Failing that, derive it from the stored
    nutrition — per-100 energy scaled by the net weight — which is exactly the sum
    the Open Food Facts import does. That way an item carrying energy facts but no
    saved calories (e.g. OFF had no net weight, you typed one in later) still counts.
    """
    cal = item.get("calories")
    if cal not in (None, ""):
        try:
            return float(cal)
        except (TypeError, ValueError):
            pass

    nutrition = item.get("nutrition")
    if isinstance(nutrition, str) and nutrition.strip():
        try:
            blob = json.loads(nutrition)
        except (ValueError, TypeError):
            blob = None
        if blob:
            per_100 = (blob.get("per_100g") or {}).get("energy_kcal")
            base = _base_grams(item.get("weight_value"), item.get("weight_unit"))
            if per_100 not in (None, "") and base is not None:
                try:
                    return float(per_100) * base / 100.0
                except (TypeError, ValueError):
                    return None
    return None


def daily_need(people, per_person):
    """Calories the household gets through in a day.

    people:     {"men": n, "women": n, "children": n}
    per_person: {"man": kcal, "woman": kcal, "child": kcal}
    """
    return (
        people["men"] * per_person["man"]
        + people["women"] * per_person["woman"]
        + people["children"] * per_person["child"]
    )


def forecast(items, people, per_person, today=None, include_expired=True):
    """Estimate how many days the pantry covers the household.

    `items` are item dicts that already carry an `expired` flag (set by the view).
    We count entries that have a calorie figure — an entry contributes
    `calories * units`. Expired stock is counted too unless `include_expired` is
    False (the Settings toggle), in which case it's set aside and reported.
    Items with no calories can't be counted either way.
    """
    total_kcal = 0.0
    counted = 0
    missing = 0
    expired = 0
    for it in items:
        if it.get("exclude_stats"):
            continue           # user chose to keep this item out of the numbers
        if not include_expired and it.get("expired"):
            expired += 1
            continue
        kcal = item_kcal(it)   # explicit calories, or derived from energy × weight
        if kcal is None:
            missing += 1
            continue
        try:
            total_kcal += kcal * int(it["units"])
            counted += 1
        except (TypeError, ValueError):
            missing += 1

    need = daily_need(people, per_person)
    days = (total_kcal / need) if need > 0 else None

    runs_out = None
    if days is not None:
        base = today or date.today()
        runs_out = (base + timedelta(days=int(days))).isoformat()

    return {
        "daily_need": need,
        "total_kcal": total_kcal,
        "counted": counted,
        "missing": missing,
        "expired": expired,
        "days": days,
        "runs_out": runs_out,
        "people_total": people["men"] + people["women"] + people["children"],
    }


def _fluid_litres(item):
    """Volume of a fluid item in litres. Water density ~1, so g≈ml and kg≈l —
    `_base_grams` already folds kg/l up to the per-100 basis, so /1000 gives litres."""
    base = _base_grams(item.get("weight_value"), item.get("weight_unit"))
    if base is None:
        return 0.0
    try:
        units = int(item.get("units") or 1)
    except (TypeError, ValueError):
        units = 1
    return base * units / 1000.0


def water_forecast(items, people, per_person, today=None, include_expired=True):
    """How many days the pantry's drinking fluids cover the household.

    Only items flagged `is_fluid` count. `per_person` is litres/day per head.
    Mirrors `forecast` (expired counted unless `include_expired` is False).
    """
    need = (
        people["men"] * per_person["man"]
        + people["women"] * per_person["woman"]
        + people["children"] * per_person["child"]
    )
    total_l = 0.0
    counted = 0
    for it in items:
        if it.get("exclude_stats"):
            continue
        if not include_expired and it.get("expired"):
            continue
        if not it.get("is_fluid"):
            continue
        total_l += _fluid_litres(it)
        counted += 1

    days = (total_l / need) if need > 0 else None
    runs_out = None
    if days is not None:
        runs_out = ((today or date.today()) + timedelta(days=int(days))).isoformat()

    return {
        "daily_need": need,
        "total_l": total_l,
        "days": days,
        "runs_out": runs_out,
        "counted": counted,
    }


# --- nutrient distribution (the statistics-page charts) ----------------------

_MACROS = [("fat", "Fat"), ("carbohydrates", "Carbs"), ("proteins", "Protein")]
_ATWATER = {"fat": 9.0, "carbohydrates": 4.0, "proteins": 4.0}   # kcal per gram
_HIGHLIGHTS = ("sugars", "fiber", "salt")
_GRADES = ("a", "b", "c", "d", "e")


def _nutrition(item):
    """Parse an item's stored nutrition blob (a JSON string) into a dict."""
    blob = item.get("nutrition")
    if isinstance(blob, dict):
        return blob
    if isinstance(blob, str) and blob.strip():
        try:
            return json.loads(blob)
        except (ValueError, TypeError):
            return None
    return None


def nutrient_breakdown(items, include_expired=True):
    """Roll the pantry's per-100 nutrition up into totals for the charts.

    Everything is scaled to the stock on hand — `per_100 × net weight × units /
    100` — over items that carry the data. Expired stock is included unless
    `include_expired` is False. Returns macro totals (grams + an energy split for
    the donut), a few highlight figures, and the Nutri-Score spread.
    """
    grams = {key: 0.0 for key, _ in _MACROS}
    for h in _HIGHLIGHTS:
        grams[h] = 0.0
    grades = {g: 0 for g in _GRADES}
    nova = {1: 0, 2: 0, 3: 0, 4: 0}
    counted = 0
    graded = 0
    nova_total = 0

    for it in items:
        if it.get("exclude_stats"):
            continue
        if not include_expired and it.get("expired"):
            continue
        blob = _nutrition(it)
        if not blob:
            continue
        per_100 = blob.get("per_100g") or {}
        base = _base_grams(it.get("weight_value"), it.get("weight_unit"))
        try:
            units = int(it.get("units") or 1)
        except (TypeError, ValueError):
            units = 1

        if base is not None:
            scale = base * units / 100.0
            got = False
            for key in grams:
                v = per_100.get(key)
                if v in (None, ""):
                    continue
                try:
                    grams[key] += float(v) * scale
                    got = True
                except (TypeError, ValueError):
                    pass
            if got:
                counted += 1

        grade = (blob.get("nutriscore") or "").lower()
        if grade in grades:
            grades[grade] += 1
            graded += 1

        try:
            ng = int(blob.get("nova_group"))
        except (TypeError, ValueError):
            ng = None
        if ng in nova:
            nova[ng] += 1
            nova_total += 1

    total_kcal = sum(grams[key] * _ATWATER[key] for key, _ in _MACROS)
    segments = []
    for key, label in _MACROS:
        kcal = grams[key] * _ATWATER[key]
        segments.append({
            "key": key,
            "label": label,
            "grams": grams[key],
            "kcal": kcal,
            "pct": (kcal / total_kcal * 100) if total_kcal else 0.0,
        })

    return {
        "counted": counted,
        "graded": graded,
        "grams": grams,
        "macro_segments": segments,
        "macro_total_kcal": total_kcal,
        "grades": grades,
        "grade_max": max(grades.values()) if grades else 0,
        "nova": nova,
        "nova_total": nova_total,
        "nova_max": max(nova.values()) if nova else 0,
    }


def filter_by_nutrition(items, macro=None, nova=None, grade=None):
    """Narrow a list of items to those matching a clicked chart slice.

    `macro` keeps items whose per-100 figure for that nutrient is above zero;
    `nova` keeps items in that processing group; `grade` keeps items with that
    Nutri-Score. Items without the relevant fact drop out (you can't attribute
    them to a slice).
    """
    out = []
    for it in items:
        blob = _nutrition(it) or {}
        per_100 = blob.get("per_100g") or {}
        if macro:
            try:
                if float(per_100.get(macro)) <= 0:
                    continue
            except (TypeError, ValueError):
                continue
        if nova is not None:
            try:
                if int(blob.get("nova_group")) != nova:
                    continue
            except (TypeError, ValueError):
                continue
        if grade:
            if (blob.get("nutriscore") or "").lower() != grade:
                continue
        out.append(it)
    return out


def calories_by_shelf(items, include_expired=True):
    """Kcal on each shelf, biggest first — the per-shelf bar chart.

    Uses the same best-effort per-unit kcal as the forecast (explicit calories or
    energy × weight), summed over units. Expired stock is included unless
    `include_expired` is False; items with no calorie basis are skipped.
    """
    totals = {}
    for it in items:
        if it.get("exclude_stats"):
            continue
        if not include_expired and it.get("expired"):
            continue
        kcal = item_kcal(it)
        if kcal is None:
            continue
        try:
            units = int(it.get("units") or 1)
        except (TypeError, ValueError):
            units = 1
        shelf = it.get("shelf") or "Unshelved"
        totals[shelf] = totals.get(shelf, 0.0) + kcal * units

    rows = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
    return {
        "rows": [{"shelf": s, "kcal": v} for s, v in rows],
        "max": rows[0][1] if rows else 0,
    }


# the "get enough" nutrients the days-of-supply tower tracks. Each is a stored
# per-100 key, a display label, its unit, and the settings key holding the daily
# per-adult target. (Risk nutrients like sugar/salt are deliberately excluded —
# "more days of salt" is backwards; they live in the load chart instead.)
SUPPLY_NUTRIENTS = [
    ("proteins",  "Protein",   "g",  "protein_per_adult"),
    ("fiber",     "Fibre",     "g",  "fiber_per_adult"),
    ("calcium",   "Calcium",   "mg", "calcium_per_adult"),
    ("iron",      "Iron",      "mg", "iron_per_adult"),
    ("potassium", "Potassium", "mg", "potassium_per_adult"),
]


def nutrient_supply(items, headcount, targets, include_expired=True, calorie_days=None):
    """How many days each key nutrient lasts the household — the supply tower.

    For every nutrient we total what the pantry holds (`per_100 × net weight ×
    units ÷ 100`, same scaling as the other charts) and divide by the household's
    daily need (`headcount × per-adult target`, children counted as adults). The
    result is "days of supply", so the tower reads shortest-lasting first.

    `calorie_days` (the forecast's days-of-food) is the yardstick, not a row: any
    nutrient that runs out sooner is flagged `short` (the hero already shows the
    days-of-food number, so it needs no bar of its own here). A nutrient no item
    carries is dropped. Returns rows sorted shortest-first, one bar per nutrient.
    """
    totals = {key: 0.0 for key, _, _, _ in SUPPLY_NUTRIENTS}
    counts = {key: 0 for key, _, _, _ in SUPPLY_NUTRIENTS}

    for it in items:
        if it.get("exclude_stats"):
            continue
        if not include_expired and it.get("expired"):
            continue
        blob = _nutrition(it)
        if not blob:
            continue
        per_100 = blob.get("per_100g") or {}
        base = _base_grams(it.get("weight_value"), it.get("weight_unit"))
        if base is None:
            continue
        try:
            units = int(it.get("units") or 1)
        except (TypeError, ValueError):
            units = 1
        scale = base * units / 100.0
        for key in totals:
            v = per_100.get(key)
            if v in (None, ""):
                continue
            try:
                totals[key] += float(v) * scale
                counts[key] += 1
            except (TypeError, ValueError):
                pass

    rows = []
    for key, label, unit, _skey in SUPPLY_NUTRIENTS:
        if counts[key] == 0 or headcount <= 0:
            continue
        target = targets.get(key)
        try:
            need = headcount * float(target)
        except (TypeError, ValueError):
            continue
        if need <= 0:
            continue
        days = totals[key] / need
        rows.append({
            "key": key, "label": label, "unit": unit,
            "days": days, "total": totals[key], "counted": counts[key],
            "short": calorie_days is not None and days < calorie_days,
        })

    rows.sort(key=lambda r: r["days"])
    return {
        "rows": rows,
        "max": max((r["days"] for r in rows), default=0),
        "reference_days": calorie_days,
    }


def food_by_year(items, include_expired=True):
    """Count pantry entries by the year their stock expires — the by-year chart.

    Groups items on the year of `expire_date`, summing units so a batch of five
    tins counts as five. Undated stock is bucketed under "No date" (not tied to a
    year, so it isn't clickable in the chart). Expired stock is included unless
    `include_expired` is False. Rows come back oldest year first, with the
    undated bucket last.
    """
    totals = {}
    undated = 0
    for it in items:
        if it.get("exclude_stats"):
            continue
        if not include_expired and it.get("expired"):
            continue
        try:
            units = int(it.get("units") or 1)
        except (TypeError, ValueError):
            units = 1
        exp = it.get("expire_date")
        year = None
        if exp:
            try:
                year = date.fromisoformat(str(exp)).year
            except (ValueError, TypeError):
                year = None
        if year is None:
            undated += units
        else:
            totals[year] = totals.get(year, 0) + units

    rows = [{"year": y, "units": n} for y, n in sorted(totals.items())]
    if undated:
        rows.append({"year": None, "units": undated})
    return {
        "rows": rows,
        "max": max((r["units"] for r in rows), default=0),
    }


# rough daily guideline per person (WHO-ish): free sugars ~50 g, salt ~5 g.
_GUIDELINE = {"sugars": ("Sugars", 50.0), "salt": ("Salt", 5.0)}


def intake_load(grams, days, people):
    """Per-day household sugar & salt against a rough guideline.

    If the pantry lasts `days` for `people`, the household eats `total / days` of
    each per day; we compare that to `people × per-head guideline`. Returns None
    when there's no time/household basis to place it against.
    """
    if not days or not people:
        return None
    rows = []
    for key, (label, per_head) in _GUIDELINE.items():
        per_day = grams.get(key, 0.0) / days
        limit = per_head * people
        rows.append({
            "key": key,
            "label": label,
            "per_day": per_day,
            "limit": limit,
            "pct": (per_day / limit * 100) if limit else 0.0,
            "over": per_day > limit,
        })
    return rows
