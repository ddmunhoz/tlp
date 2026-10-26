"""Meals — the meal-prep menu.

A meal is a named plan: a list of components, each a product (barcode + name) with
a portion in grams/ml and a snapshot of its kcal-per-gram. The snapshot keeps a
saved meal's calories stable even if that product later leaves the pantry. Meals
are plans only — they never touch pantry stock.
"""
from datetime import datetime


class MealError(ValueError):
    """Raised when meal data doesn't pass muster."""


# nutrient snapshots we carry per meal line. Maps the display/per-100 key (the same
# key stats + the pantry filter use) to the meal_items column that snapshots it as a
# per-gram figure. Native units: g for protein/fibre, mg for the minerals.
NUTRIENT_COLUMNS = {
    "proteins": "protein_per_g",
    "fiber": "fiber_per_g",
    "calcium": "calcium_per_g",
    "iron": "iron_per_g",
    "potassium": "potassium_per_g",
}


def _meal_nutrients(items):
    """Total each snapshotted nutrient across a meal's lines (grams × per-gram).

    Keyed by the display key. A line missing a nutrient just doesn't contribute to
    it (mirrors how the statistics skip absent facts). Returns only nutrients at
    least one line carried, so an all-blank nutrient draws no bar."""
    totals = {}
    for key, col in NUTRIENT_COLUMNS.items():
        vals = [it for it in items if it.get(col) is not None]
        if vals:
            totals[key] = sum(it["grams"] * it[col] for it in vals)
    return totals


# ---- read ----

def list_meals(conn):
    """All saved meals, newest first, each with its components attached."""
    meals = [dict(r) for r in conn.execute(
        "SELECT * FROM meals ORDER BY id DESC"
    ).fetchall()]
    for m in meals:
        m["items"] = _items_for(conn, m["id"])
        m["total_kcal"] = sum(it["grams"] * it["kcal_per_g"] for it in m["items"])
        m["nutrients"] = _meal_nutrients(m["items"])
    return meals


def get_meal(conn, meal_id):
    row = conn.execute("SELECT * FROM meals WHERE id = ?", (meal_id,)).fetchone()
    if row is None:
        return None
    meal = dict(row)
    meal["items"] = _items_for(conn, meal_id)
    meal["total_kcal"] = sum(it["grams"] * it["kcal_per_g"] for it in meal["items"])
    meal["nutrients"] = _meal_nutrients(meal["items"])
    return meal


def _items_for(conn, meal_id):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM meal_items WHERE meal_id = ? ORDER BY id", (meal_id,)
    ).fetchall()]


# ---- write ----

def _clean_components(components):
    """Validate the incoming component list into clean rows.

    Each component needs a name, positive grams, and a non-negative kcal_per_g.
    barcode + unit are optional passthroughs (unit defaults to 'g').

    A component may be sourced from a single product (the default) or from a *tag*
    (`source_type='tag'` + a positive `tag_id`) — a tag line pools every product
    carrying that tag. Its kcal/nutrient figures are the live average of the tag's
    in-stock members, snapshotted here only as a fallback; the view recomputes them.
    """
    clean = []
    for c in components or []:
        name = (c.get("name") or "").strip()
        if not name:
            raise MealError("each meal item needs a name")
        try:
            grams = float(c.get("grams"))
            kcal_per_g = float(c.get("kcal_per_g"))
        except (TypeError, ValueError):
            raise MealError("meal item grams and kcal_per_g must be numbers")
        if grams <= 0:
            raise MealError("meal item grams must be greater than 0")
        if kcal_per_g < 0:
            raise MealError("meal item kcal_per_g can't be negative")
        unit = (c.get("unit") or "g").strip().lower()
        if unit not in ("g", "ml"):
            unit = "g"

        source_type = "tag" if (c.get("source_type") == "tag") else "product"
        if source_type == "tag":
            try:
                tag_id = int(c.get("tag_id"))
            except (TypeError, ValueError):
                raise MealError("a tag meal item needs a tag")
            if tag_id <= 0:
                raise MealError("a tag meal item needs a tag")
            barcode = None            # a tag line isn't tied to one product/barcode
        else:
            tag_id = None
            barcode = (c.get("barcode") or "").strip() or None

        row = {"name": name, "grams": grams, "kcal_per_g": kcal_per_g,
               "barcode": barcode, "unit": unit,
               "source_type": source_type, "tag_id": tag_id}
        # optional per-gram nutrient snapshots — absent/blank/negative -> NULL
        for col in NUTRIENT_COLUMNS.values():
            raw = c.get(col)
            try:
                v = float(raw)
            except (TypeError, ValueError):
                v = None
            row[col] = v if (v is not None and v >= 0) else None
        clean.append(row)
    if not clean:
        raise MealError("a meal needs at least one item")
    return clean


def create_meal(conn, name, components):
    name = (name or "").strip()
    if not name:
        raise MealError("meal name is required")
    rows = _clean_components(components)
    now = datetime.now().isoformat(timespec="seconds")
    cur = conn.execute(
        "INSERT INTO meals (name, created_at, updated_at) VALUES (?, ?, ?)",
        (name, now, now),
    )
    meal_id = cur.lastrowid
    _insert_items(conn, meal_id, rows)
    conn.commit()
    return meal_id


def update_meal(conn, meal_id, name, components):
    if get_meal(conn, meal_id) is None:
        return False
    name = (name or "").strip()
    if not name:
        raise MealError("meal name is required")
    rows = _clean_components(components)
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        "UPDATE meals SET name = ?, updated_at = ? WHERE id = ?",
        (name, now, meal_id),
    )
    conn.execute("DELETE FROM meal_items WHERE meal_id = ?", (meal_id,))
    _insert_items(conn, meal_id, rows)
    conn.commit()
    return True


def _insert_items(conn, meal_id, rows):
    conn.executemany(
        """INSERT INTO meal_items
             (meal_id, barcode, name, grams, kcal_per_g, unit, source_type, tag_id,
              protein_per_g, fiber_per_g, calcium_per_g, iron_per_g, potassium_per_g)
           VALUES
             (:meal_id, :barcode, :name, :grams, :kcal_per_g, :unit, :source_type, :tag_id,
              :protein_per_g, :fiber_per_g, :calcium_per_g, :iron_per_g, :potassium_per_g)""",
        [{**r, "meal_id": meal_id} for r in rows],
    )


def set_planned(conn, meal_id, servings):
    """Store how many servings of this meal the household plans to make.

    Shared across everyone on the install (it's on the row). Clamped to a
    non-negative int; returns the stored value, or None if the meal is gone."""
    if get_meal(conn, meal_id) is None:
        return None
    try:
        n = max(0, int(servings))
    except (TypeError, ValueError):
        n = 0
    conn.execute("UPDATE meals SET planned_servings = ? WHERE id = ?", (n, meal_id))
    conn.commit()
    return n


def delete_meal(conn, meal_id):
    cur = conn.execute("DELETE FROM meals WHERE id = ?", (meal_id,))
    conn.commit()   # meal_items cascade via the FK
    return cur.rowcount > 0
