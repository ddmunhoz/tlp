"""Activity log — one row per change to pantry stock, so the Activity page can show
what's been going on (proposal 0014).

The model writers call record() right before their own commit, so a change and its
log line land together or not at all. Rows keep the item's name + barcode as they
were at the time, so the feed still reads once the item is renamed or gone. Details
are a small JSON blob whose shape depends on the action; describe() turns it into
the one-liner the page shows. Nothing here imports the other src modules, so models
and tags can pull it in without a cycle.
"""
import json
from datetime import date, datetime, timedelta

ACTIONS = ("added", "removed", "units", "edited", "moved", "tagged")

# how an edited field reads on the page
FIELD_LABELS = {
    "name": "name", "barcode": "barcode", "units": "units", "weight_value": "weight",
    "weight_unit": "unit", "expire_date": "expiry", "calories": "calories",
    "shelf": "shelf", "is_fluid": "drinking fluid", "exclude_stats": "excluded from stats",
}


def record(conn, action, item, details=None):
    """Write one row. `item` is anything with id/name/barcode (a Row or a dict), as
    the item is right now. Doesn't commit — the caller's commit covers it."""
    if action not in ACTIONS:
        raise ValueError("unknown activity action: %s" % action)
    conn.execute(
        "INSERT INTO activity_log (at, action, item_id, item_name, barcode, details) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (datetime.now().isoformat(timespec="seconds"), action, item["id"],
         item["name"], item["barcode"], json.dumps(details) if details else None),
    )


def item_for_key(conn, key):
    """The newest pantry row behind a product key (barcode, else name:...). Tags are
    keyed that way, so the tag writers use this to put a name on their log line.
    Falls back to the bare key when no row matches — a log line must never sink a
    save."""
    row = conn.execute(
        "SELECT id, name, barcode FROM pantry_items "
        "WHERE lower(trim(coalesce(barcode, ''))) = ? "
        "   OR (trim(coalesce(barcode, '')) = '' AND 'name:' || lower(trim(name)) = ?) "
        "ORDER BY id DESC LIMIT 1",
        (key, key),
    ).fetchone()
    if row:
        return dict(row)
    named = key.startswith("name:")
    return {"id": None, "name": key[5:] if named else key, "barcode": None if named else key}


def prune(conn, keep_days):
    """Drop rows older than keep_days (the activity_keep_days setting). Anything
    that isn't a positive number means keep everything. Returns how many went."""
    try:
        keep = int(keep_days)
    except (TypeError, ValueError):
        return 0
    if keep <= 0:
        return 0
    cutoff = (datetime.now() - timedelta(days=keep)).isoformat(timespec="seconds")
    cur = conn.execute("DELETE FROM activity_log WHERE at < ?", (cutoff,))
    conn.commit()
    return cur.rowcount


def recent(conn, action=None, before_id=None, limit=100):
    """Newest first, details decoded and described. Returns (rows, has_more); hand
    the last row's id back as before_id for the next page — keyset paging, so it
    stays cheap however big the table gets."""
    sql = "SELECT * FROM activity_log"
    where, args = [], []
    if action:
        where.append("action = ?")
        args.append(action)
    if before_id:
        where.append("id < ?")
        args.append(before_id)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit + 1)
    rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
    has_more = len(rows) > limit
    rows = rows[:limit]
    for r in rows:
        r["details"] = json.loads(r["details"]) if r["details"] else {}
        r["text"] = describe(r)
        r["time"] = r["at"][11:16]
    return rows, has_more


def group_by_day(rows, today=None):
    """[{day, label, rows}] in the order given — Today / Yesterday / Oct 20."""
    today = today or date.today()
    groups = []
    for r in rows:
        day = r["at"][:10]
        if not groups or groups[-1]["day"] != day:
            groups.append({"day": day, "label": _day_label(day, today), "rows": []})
        groups[-1]["rows"].append(r)
    return groups


def _day_label(day, today):
    d = date.fromisoformat(day)
    if d == today:
        return "Today"
    if d == today - timedelta(days=1):
        return "Yesterday"
    return d.strftime("%b %-d") if d.year == today.year else d.strftime("%b %-d, %Y")


def describe(row):
    """The one plain line the page shows next to the item name."""
    action, d = row["action"], row.get("details") or {}
    if action == "added":
        bits = [_units(d.get("units")), _exp(d), "shelf " + d["shelf"] if d.get("shelf") else None]
        if d.get("via") == "restock":
            bits.append("restock")
        return " · ".join(b for b in bits if b)
    if action == "removed":
        if d.get("reason") == "used up":
            return "used up"
        return " · ".join(b for b in (_units(d.get("units"), " left"), _exp(d)) if b)
    if action == "units":
        return "%s → %s" % (_show(d.get("old")), _show(d.get("new")))
    if action == "edited":
        parts = [
            "%s %s → %s" % (FIELD_LABELS.get(c["field"], c["field"]), _show(c["old"]), _show(c["new"]))
            for c in d.get("changes", [])
        ]
        if d.get("nutrition"):
            parts.append("nutrition refreshed")
        return ", ".join(parts)
    if action == "moved":
        return "%s → %s" % (d.get("old_shelf") or "Unshelved", d.get("new_shelf") or "Unshelved")
    if action == "tagged":
        return ", ".join(["+ " + t for t in d.get("added", [])] + ["− " + t for t in d.get("removed", [])])
    return ""


def _units(n, suffix=""):
    if n in (None, ""):
        return None
    try:
        n = int(n)
    except (TypeError, ValueError):
        return "%s units%s" % (n, suffix)
    return "%d unit%s%s" % (n, "" if n == 1 else "s", suffix)


def _exp(d):
    return "exp " + d["expire_date"] if d.get("expire_date") else None


def _show(v):
    if v is None or v == "":
        return "—"
    if isinstance(v, float):
        return "%g" % v
    return str(v)
