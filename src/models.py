"""Pantry item data access + validation.

Thin layer over sqlite. Every mutation refreshes the CSV mirror so the on-disk
export is never stale (that's a hard requirement, not a nice-to-have).
"""
import json
import os
from datetime import datetime, date

from . import activity, backup, database, shelves


# the canonical product identity lives in database.py (lowest layer); re-exported here
# so the long-standing models.product_key(...) call sites keep working.
from .database import product_key  # noqa: E402,F401


class ValidationError(ValueError):
    """Raised when incoming item data doesn't pass muster."""


# ---- activity log helpers (0014) ----------------------------------------------
# the fields an edit gets diffed on for the log. photo + nutrition are blobs, so
# those are only flagged, never spelled out.
_LOGGED_FIELDS = (
    "name", "barcode", "units", "weight_value", "weight_unit", "expire_date",
    "calories", "shelf_id", "is_fluid", "exclude_stats",
)


def _shelf_name(conn, shelf_id):
    if shelf_id is None:
        return "Unshelved"
    row = shelves.get_shelf(conn, shelf_id)
    return row["name"] if row else "Unshelved"


def _same(a, b):
    """Loose equality for the diff: 3 == 3.0 == "3", strings compared stripped."""
    if a is None or b is None:
        return a is None and b is None
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return str(a).strip() == str(b).strip()


def _edit_changes(conn, old, new):
    """What an edit actually changed, as the log stores it — or None if nothing did,
    so a Save with no changes writes no row."""
    changes = []
    for field in _LOGGED_FIELDS:
        a, b = old[field], new.get(field)
        if _same(a, b):
            continue
        if field == "shelf_id":
            changes.append({"field": "shelf", "old": _shelf_name(conn, a), "new": _shelf_name(conn, b)})
        elif field in ("is_fluid", "exclude_stats"):
            changes.append({"field": field, "old": "on" if a else "off", "new": "on" if b else "off"})
        else:
            changes.append({"field": field, "old": a, "new": b})
    details = {"changes": changes}
    if (old["nutrition"] or None) != (new.get("nutrition") or None):
        details["nutrition"] = True
    return details if changes or details.get("nutrition") else None


def _removed_details(item, reason):
    d = {"units": item["units"], "expire_date": item["expire_date"]}
    if reason:
        d["reason"] = reason
    return d


def _parse_iso_date(value, field):
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        raise ValidationError(f"{field} must be a valid date (YYYY-MM-DD)")


def validate_item(data, conn=None):
    """Check + normalise item fields. Returns a clean dict, or raises.

    Mandatory: name, weight_value, weight_unit, expire_date. units defaults to 1.
    Optional: shelf_id, barcode, photo_path, nutrition, calories.
    A shelf_id, when given, must point at a real shelf (pass conn to check).
    """
    clean = {}

    name = (data.get("name") or "").strip()
    if not name:
        raise ValidationError("name is required")
    clean["name"] = name

    # units — default to 1 when blank, but reject nonsense/zero/negatives.
    raw_units = data.get("units")
    if raw_units in (None, ""):
        clean["units"] = 1
    else:
        try:
            units = int(raw_units)
        except (ValueError, TypeError):
            raise ValidationError("units must be a whole number")
        if units < 1:
            raise ValidationError("units must be at least 1")
        clean["units"] = units

    try:
        weight_value = float(data.get("weight_value"))
    except (ValueError, TypeError):
        raise ValidationError("weight_value must be a number")
    if weight_value <= 0:
        raise ValidationError("weight_value must be greater than 0")
    clean["weight_value"] = weight_value

    weight_unit = (data.get("weight_unit") or "").strip().lower()
    if weight_unit not in database.WEIGHT_UNITS:
        allowed = "/".join(database.WEIGHT_UNITS)
        raise ValidationError(f"weight_unit must be one of {allowed}")
    clean["weight_unit"] = weight_unit

    # does this item count toward the drinking-water supply? (checkbox on the form)
    clean["is_fluid"] = 1 if str(data.get("is_fluid", "")).strip().lower() in (
        "1", "true", "yes", "on") else 0

    # leave this item out of the statistics? (still lives in the pantry as normal)
    clean["exclude_stats"] = 1 if str(data.get("exclude_stats", "")).strip().lower() in (
        "1", "true", "yes", "on") else 0

    # calories (kcal for this item's net weight) — required, since the whole
    # "how long will the pantry last" / meals maths leans on it. 0 is allowed
    # (e.g. water), but it can't be left blank.
    raw_cal = data.get("calories")
    if raw_cal in (None, ""):
        raise ValidationError("calories is required")
    try:
        calories = float(raw_cal)
    except (ValueError, TypeError):
        raise ValidationError("calories must be a number")
    if calories < 0:
        raise ValidationError("calories can't be negative")
    clean["calories"] = calories

    expire_raw = (data.get("expire_date") or "").strip()
    if not expire_raw:
        raise ValidationError("expire_date is required")
    _parse_iso_date(expire_raw, "expire_date")
    clean["expire_date"] = expire_raw

    # shelf is optional — blank / 0 / missing means Unshelved.
    raw_shelf = data.get("shelf_id")
    if raw_shelf in (None, "", "0", 0):
        clean["shelf_id"] = None
    else:
        try:
            shelf_id = int(raw_shelf)
        except (ValueError, TypeError):
            raise ValidationError("shelf is invalid")
        if conn is not None and shelves.get_shelf(conn, shelf_id) is None:
            raise ValidationError("that shelf doesn't exist")
        clean["shelf_id"] = shelf_id

    # barcode + photo path are free-text passthroughs (blank -> null).
    for optional in ("barcode", "photo_path"):
        val = data.get(optional)
        clean[optional] = val.strip() if isinstance(val, str) and val.strip() else None

    # nutrition is a JSON blob from the Open Food Facts lookup. store it verbatim,
    # but don't let malformed junk into the column.
    nutrition = data.get("nutrition")
    if isinstance(nutrition, str) and nutrition.strip():
        try:
            json.loads(nutrition)
        except ValueError:
            raise ValidationError("nutrition must be valid JSON")
        clean["nutrition"] = nutrition.strip()
    else:
        clean["nutrition"] = None

    return clean


def create_item(conn, data, via=None):
    """Insert a validated item. `via` is a word for the activity log when the add
    came from somewhere other than the form ("restock")."""
    clean = validate_item(data, conn)
    now = datetime.now().isoformat(timespec="seconds")
    cur = conn.execute(
        """
        INSERT INTO pantry_items
            (barcode, name, units, weight_value, weight_unit, is_fluid, exclude_stats,
             calories, expire_date, photo_path, nutrition, shelf_id, created_at, updated_at)
        VALUES (:barcode, :name, :units, :weight_value, :weight_unit, :is_fluid, :exclude_stats,
                :calories, :expire_date, :photo_path, :nutrition, :shelf_id, :created_at, :updated_at)
        """,
        {**clean, "created_at": now, "updated_at": now},
    )
    new_id = cur.lastrowid
    added = {"units": clean["units"], "expire_date": clean["expire_date"]}
    if clean["shelf_id"] is not None:
        added["shelf"] = _shelf_name(conn, clean["shelf_id"])
    if via:
        added["via"] = via
    activity.record(conn, "added",
                    {"id": new_id, "name": clean["name"], "barcode": clean["barcode"]}, added)
    conn.commit()
    backup.write_csv_mirror(conn)
    return new_id


def get_item(conn, item_id):
    row = conn.execute(
        "SELECT * FROM pantry_items WHERE id = ?", (item_id,)
    ).fetchone()
    return dict(row) if row else None


def list_items(conn):
    """All items, soonest-to-expire first — that's what the owner cares about.

    Each row carries its shelf name (via `shelf`) so callers don't re-query.
    """
    rows = conn.execute(
        """
        SELECT i.*, s.name AS shelf
        FROM pantry_items i
        LEFT JOIN shelves s ON s.id = i.shelf_id
        ORDER BY i.expire_date ASC, i.name ASC
        """
    ).fetchall()
    return [dict(r) for r in rows]


def group_by_shelf(items, shelf_list):
    """Bucket already-sorted items under their shelf, in shelf display order.

    Empty shelves are skipped (no point showing a heading with nothing under it).
    Unshelved items collect into a trailing group.
    """
    buckets = {s["id"]: {"id": s["id"], "name": s["name"], "items": []} for s in shelf_list}
    unshelved = {"id": None, "name": "Unshelved", "items": []}
    for it in items:
        bucket = buckets.get(it.get("shelf_id"))
        (bucket or unshelved)["items"].append(it)
    groups = [b for b in buckets.values() if b["items"]]
    if unshelved["items"]:
        groups.append(unshelved)
    return groups


def move_items(conn, item_ids, shelf_id):
    """Reassign a batch of items to a shelf (or Unshelved when shelf_id is None).

    Returns how many rows actually moved. Ids that don't exist are just skipped.
    The target shelf, when given, has to be real — otherwise it's a ValidationError,
    same rule the item form already enforces.
    """
    # normalise the target: blank / 0 / None all mean Unshelved.
    if shelf_id in (None, "", "0", 0):
        shelf_id = None
    else:
        try:
            shelf_id = int(shelf_id)
        except (ValueError, TypeError):
            raise ValidationError("shelf is invalid")
        if shelves.get_shelf(conn, shelf_id) is None:
            raise ValidationError("that shelf doesn't exist")

    # keep only the ids that parse as ints — a stray value shouldn't sink the move.
    ids = []
    for raw in item_ids or []:
        try:
            ids.append(int(raw))
        except (ValueError, TypeError):
            continue
    if not ids:
        return 0

    now = datetime.now().isoformat(timespec="seconds")
    placeholders = ",".join("?" for _ in ids)
    # snapshot before the update: the log only speaks for rows that actually changed shelf
    before = conn.execute(
        f"SELECT id, name, barcode, shelf_id FROM pantry_items WHERE id IN ({placeholders})", ids
    ).fetchall()
    cur = conn.execute(
        f"UPDATE pantry_items SET shelf_id = ?, updated_at = ? WHERE id IN ({placeholders})",
        [shelf_id, now, *ids],
    )
    target = _shelf_name(conn, shelf_id)
    for row in before:
        if row["shelf_id"] != shelf_id:
            activity.record(conn, "moved", row,
                            {"old_shelf": _shelf_name(conn, row["shelf_id"]), "new_shelf": target})
    conn.commit()
    backup.write_csv_mirror(conn)
    return cur.rowcount


def paginate_groups(groups, page_size, args):
    """Slice each shelf's items to one page and hang pager metadata off the group.

    Each shelf pages on its own so flipping one doesn't disturb the others; the page
    number rides in the query param `s<id>_page` (unshelved uses `s0_page`). A
    page_size <= 0 means "show everything" — no slicing, no pager.

    `args` is the request's query args (anything with .get). Mutates+returns groups.
    """
    for g in groups:
        total = len(g["items"])
        param = "s%d_page" % (g["id"] or 0)
        if page_size and page_size > 0:
            pages = max(1, -(-total // page_size))  # ceil division
        else:
            pages = 1
        page = _clamp_page(args.get(param), pages)
        if page_size and page_size > 0:
            start = (page - 1) * page_size
            g["items"] = g["items"][start:start + page_size]
        g["page"] = page
        g["pages"] = pages
        g["total"] = total
        g["param"] = param
        g["has_prev"] = page > 1
        g["has_next"] = page < pages
    return groups


def _clamp_page(raw, pages):
    try:
        page = int(raw)
    except (ValueError, TypeError):
        return 1
    return min(max(page, 1), pages)


def update_item(conn, item_id, data):
    existing = get_item(conn, item_id)
    if existing is None:
        return False
    clean = validate_item(data, conn)
    changes = _edit_changes(conn, existing, clean)
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        """
        UPDATE pantry_items SET
            barcode = :barcode, name = :name, units = :units,
            weight_value = :weight_value, weight_unit = :weight_unit, is_fluid = :is_fluid,
            exclude_stats = :exclude_stats, calories = :calories, expire_date = :expire_date,
            photo_path = :photo_path, nutrition = :nutrition, shelf_id = :shelf_id,
            updated_at = :updated_at
        WHERE id = :id
        """,
        {**clean, "updated_at": now, "id": item_id},
    )
    if changes:
        activity.record(conn, "edited",
                        {"id": item_id, "name": clean["name"], "barcode": clean["barcode"]}, changes)
    conn.commit()
    # if the photo was swapped out, clean up the one it left behind
    old_photo = existing["photo_path"]
    if old_photo and old_photo != clean.get("photo_path"):
        _prune_image(conn, old_photo)
    backup.write_csv_mirror(conn)
    return True


def delete_item(conn, item_id, reason=None):
    """Drop one row. `reason` is a word for the activity log ("used up" when the
    counter hit zero) — plain deletes leave it out."""
    item = get_item(conn, item_id)   # grab the photo + the log line before the row's gone
    cur = conn.execute("DELETE FROM pantry_items WHERE id = ?", (item_id,))
    if item:
        activity.record(conn, "removed", item, _removed_details(item, reason))
    conn.commit()
    if item and item["photo_path"]:
        _prune_image(conn, item["photo_path"])
    backup.write_csv_mirror(conn)
    return cur.rowcount > 0


def delete_items(conn, item_ids):
    """Delete a batch of items in one go. Returns how many rows actually went.

    Ids that don't parse or don't exist are just skipped. Photos are pruned once
    all rows are gone, and the CSV mirror is written a single time at the end.
    """
    ids = []
    for raw in item_ids or []:
        try:
            ids.append(int(raw))
        except (ValueError, TypeError):
            continue
    if not ids:
        return 0
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT id, name, barcode, units, expire_date, photo_path FROM pantry_items "
        f"WHERE id IN ({placeholders})", ids
    ).fetchall()
    photos = [r["photo_path"] for r in rows if r["photo_path"]]
    cur = conn.execute(f"DELETE FROM pantry_items WHERE id IN ({placeholders})", ids)
    for r in rows:
        activity.record(conn, "removed", r, _removed_details(r, None))
    conn.commit()
    for path in set(photos):
        _prune_image(conn, path)   # only unlinks once nothing references it
    backup.write_csv_mirror(conn)
    return cur.rowcount


def _prune_image(conn, photo_path):
    """Delete a downloaded product image once nothing references it any more.

    Images are named by barcode, so several entries (e.g. the same product under
    different expiry dates) share one file — we only unlink when the last referrer
    is gone. basename keeps this from ever reaching outside the images dir.
    """
    still_used = conn.execute(
        "SELECT 1 FROM pantry_items WHERE photo_path = ? LIMIT 1", (photo_path,)
    ).fetchone()
    if still_used:
        return
    name = os.path.basename(photo_path)
    if not name:
        return
    try:
        os.remove(os.path.join(database.images_dir(), name))
    except OSError:
        pass   # already gone, or was never written — nothing to clean up


def items_by_barcode(conn, barcode):
    """Every pantry entry carrying this exact barcode, soonest-expiry first.

    A blank barcode matches nothing — plenty of hand-entered items have none, and
    we don't want them all colliding under "".
    """
    barcode = (barcode or "").strip()
    if not barcode:
        return []
    rows = conn.execute(
        """
        SELECT i.*, s.name AS shelf
        FROM pantry_items i
        LEFT JOIN shelves s ON s.id = i.shelf_id
        WHERE i.barcode = ?
        ORDER BY i.expire_date ASC, i.name ASC
        """,
        (barcode,),
    ).fetchall()
    return [dict(r) for r in rows]


def adjust_units(conn, item_id, delta):
    """Nudge an entry's unit count by delta (usually +1 / -1).

    Hitting zero means it's all gone, so the entry is removed. Returns the new
    count (0 when it was removed), or None if there's no such item.
    """
    item = get_item(conn, item_id)
    if item is None:
        return None
    try:
        new_units = int(item["units"]) + int(delta)
    except (ValueError, TypeError):
        raise ValidationError("delta must be a whole number")
    if new_units <= 0:
        delete_item(conn, item_id, reason="used up")   # commits + refreshes the CSV itself
        return 0
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        "UPDATE pantry_items SET units = ?, updated_at = ? WHERE id = ?",
        (new_units, now, item_id),
    )
    activity.record(conn, "units", item, {"old": int(item["units"]), "new": new_units})
    conn.commit()
    backup.write_csv_mirror(conn)
    return new_units


def restock(conn, item_id, expire_date, units):
    """Add a fresh batch of an item that's already on the shelf, under its own
    expiry date. Clones the product bits (name, barcode, weight, facts, shelf)
    into a brand-new entry so each expiry stays tracked on its own. Returns the
    new id.
    """
    src = get_item(conn, item_id)
    if src is None:
        raise ValidationError("that item doesn't exist")
    data = {
        "name": src["name"],
        "units": units if units not in (None, "") else 1,
        "weight_value": src["weight_value"],
        "weight_unit": src["weight_unit"],
        "is_fluid": src["is_fluid"],
        "exclude_stats": src["exclude_stats"],
        "calories": src["calories"],
        "expire_date": expire_date,
        "shelf_id": src["shelf_id"],
        "barcode": src["barcode"],
        "photo_path": src["photo_path"],
        "nutrition": src["nutrition"],
    }
    return create_item(conn, data, via="restock")
