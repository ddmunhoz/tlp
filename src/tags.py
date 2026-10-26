"""Tags — free-form labels that group interchangeable products.

A tag lives on a product *identity* (models.product_key: barcode-else-name), not a
single pantry row, so tagging "Arroz Basmati" once applies to every batch and future
restock of it. The pantry filters by tag, and a meal line can draw from a tag instead
of one product — pooling the stock of everything that carries it.

Kept separate from models.py so neither file balloons. product_tags links cascade-
delete with their tag; a tag with no links left is pruned so the vocabulary stays tidy.
"""
from datetime import datetime

from . import activity
from .database import product_key  # re-exported for callers that only import tags


class TagError(ValueError):
    """Bad tag input — e.g. a blank name."""


def normalize(name):
    """Canonical form of a tag: trimmed and lower-cased. Returns "" for junk, which
    callers treat as 'skip this one' rather than storing an empty tag."""
    return (name or "").strip().lower()


def clean_names(names):
    """Normalise a list of raw tag names into a deduped, order-preserving clean list."""
    seen = []
    for raw in names or []:
        n = normalize(raw)
        if n and n not in seen:
            seen.append(n)
    return seen


# ---- tag vocabulary ----

def get_or_create(conn, name):
    """The id for a tag name, creating it if it's new. Raises on a blank name."""
    n = normalize(name)
    if not n:
        raise TagError("tag name is required")
    row = conn.execute("SELECT id FROM tags WHERE name = ? COLLATE NOCASE", (n,)).fetchone()
    if row:
        return row["id"]
    now = datetime.now().isoformat(timespec="seconds")
    cur = conn.execute("INSERT INTO tags (name, created_at) VALUES (?, ?)", (n, now))
    return cur.lastrowid


def list_tags(conn):
    """Every tag with how many products carry it, most-used then alphabetical."""
    rows = conn.execute(
        """
        SELECT t.id, t.name, COUNT(pt.product_key) AS usage
        FROM tags t
        LEFT JOIN product_tags pt ON pt.tag_id = t.id
        GROUP BY t.id
        ORDER BY usage DESC, t.name ASC
        """
    ).fetchall()
    return [dict(r) for r in rows]


def all_names(conn):
    """Just the tag names, alphabetical — feeds the item-form autocomplete."""
    return [r["name"] for r in conn.execute("SELECT name FROM tags ORDER BY name ASC")]


def rename_tag(conn, tag_id, name):
    """Rename a tag. If the new name already exists, fold this tag's products into it
    (a merge) and drop the now-empty tag. Returns True if anything changed."""
    n = normalize(name)
    if not n:
        raise TagError("tag name is required")
    if conn.execute("SELECT 1 FROM tags WHERE id = ?", (tag_id,)).fetchone() is None:
        return False
    existing = conn.execute(
        "SELECT id FROM tags WHERE name = ? COLLATE NOCASE AND id != ?", (n, tag_id)
    ).fetchone()
    if existing:
        # merge: repoint this tag's links onto the existing one, ignoring dupes, then bin it
        conn.execute(
            "UPDATE OR IGNORE product_tags SET tag_id = ? WHERE tag_id = ?",
            (existing["id"], tag_id),
        )
        conn.execute("DELETE FROM product_tags WHERE tag_id = ?", (tag_id,))
        # meal lines drawing from this tag follow the merge too — otherwise they'd
        # dangle on a dead id and read as permanently unsourced.
        conn.execute(
            "UPDATE meal_items SET tag_id = ?, name = ? WHERE tag_id = ?",
            (existing["id"], n, tag_id),
        )
        conn.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
    else:
        conn.execute("UPDATE tags SET name = ? WHERE id = ?", (n, tag_id))
        # keep meal lines' display name in step with the tag they draw from
        conn.execute("UPDATE meal_items SET name = ? WHERE tag_id = ?", (n, tag_id))
    conn.commit()
    return True


def delete_tag(conn, tag_id):
    """Drop a tag; its product links cascade away via the FK."""
    cur = conn.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
    conn.commit()
    return cur.rowcount > 0


# ---- product <-> tag links ----

def tags_for_product(conn, key):
    """Tag names on one product identity, alphabetical."""
    rows = conn.execute(
        """
        SELECT t.name FROM tags t
        JOIN product_tags pt ON pt.tag_id = t.id
        WHERE pt.product_key = ?
        ORDER BY t.name ASC
        """,
        (key,),
    ).fetchall()
    return [r["name"] for r in rows]


def tags_for_products(conn, keys):
    """Map of product_key -> [tag names] for a batch of keys, in one query. Keys not
    tagged simply don't appear. Used to decorate the pantry list without N queries."""
    keys = list({k for k in keys if k})
    if not keys:
        return {}
    placeholders = ",".join("?" for _ in keys)
    rows = conn.execute(
        f"""
        SELECT pt.product_key AS k, t.name AS name
        FROM product_tags pt
        JOIN tags t ON t.id = pt.tag_id
        WHERE pt.product_key IN ({placeholders})
        ORDER BY t.name ASC
        """,
        keys,
    ).fetchall()
    out = {}
    for r in rows:
        out.setdefault(r["k"], []).append(r["name"])
    return out


def set_product_tags(conn, key, names):
    """Replace a product's tags with exactly `names` (created as needed). Prunes any
    tag left with no products so stale labels don't linger in the autocomplete."""
    wanted = clean_names(names)
    wanted_ids = {get_or_create(conn, n) for n in wanted}

    current = _names_for_key(conn, key)   # {tag_id: name}
    for tag_id in set(current) - wanted_ids:
        conn.execute(
            "DELETE FROM product_tags WHERE product_key = ? AND tag_id = ?", (key, tag_id)
        )
    for tag_id in wanted_ids - set(current):
        conn.execute(
            "INSERT OR IGNORE INTO product_tags (tag_id, product_key) VALUES (?, ?)",
            (tag_id, key),
        )
    # one log line per product, and only when something actually changed
    added = sorted(set(wanted) - set(current.values()))
    removed = sorted(set(current.values()) - set(wanted))
    if added or removed:
        activity.record(conn, "tagged", activity.item_for_key(conn, key),
                        {"added": added, "removed": removed})
    _prune_orphans(conn)
    conn.commit()


def _names_for_key(conn, key):
    """{tag_id: name} for everything a product currently carries."""
    return {
        r["tag_id"]: r["name"] for r in conn.execute(
            "SELECT pt.tag_id, t.name FROM product_tags pt JOIN tags t ON t.id = pt.tag_id "
            "WHERE pt.product_key = ?", (key,)
        )
    }


def add_tags_to_keys(conn, keys, names):
    """Add `names` to every product key in `keys`, keeping their existing tags (unlike
    set_product_tags, which replaces). For the pantry's bulk "tag selected" action.
    Returns how many distinct tags were applied."""
    clean = clean_names(names)
    keys = {k for k in keys if k}
    if not keys or not clean:
        return 0   # nothing to link — don't mint orphan tags nothing will carry
    tag_ids = [get_or_create(conn, n) for n in clean]
    for key in keys:
        have = set(_names_for_key(conn, key).values())
        for tag_id in tag_ids:
            conn.execute(
                "INSERT OR IGNORE INTO product_tags (tag_id, product_key) VALUES (?, ?)",
                (tag_id, key),
            )
        new = sorted(set(clean) - have)
        if new:
            activity.record(conn, "tagged", activity.item_for_key(conn, key),
                            {"added": new, "removed": []})
    conn.commit()
    return len(tag_ids)


def move_product_tags(conn, old_key, new_key):
    """Carry a product's tags over when its identity changes (barcode/name edit). No-op
    when the key didn't actually change or the product had no tags."""
    if old_key == new_key:
        return
    conn.execute(
        "UPDATE OR IGNORE product_tags SET product_key = ? WHERE product_key = ?",
        (new_key, old_key),
    )
    conn.execute("DELETE FROM product_tags WHERE product_key = ?", (old_key,))
    conn.commit()


def product_keys_for_tag(conn, tag_id):
    """Set of product keys carrying a tag — the members the planner pools stock over."""
    return {
        r["product_key"] for r in conn.execute(
            "SELECT product_key FROM product_tags WHERE tag_id = ?", (tag_id,)
        )
    }


def _prune_orphans(conn):
    """Drop tags nothing points at any more — but keep any a meal line still targets,
    even with no products currently carrying it (the meal shows it as unsourced, and we
    still need its name). meal_items.tag_id has no FK, so this guard is on us."""
    conn.execute(
        """
        DELETE FROM tags
        WHERE id NOT IN (SELECT DISTINCT tag_id FROM product_tags)
          AND id NOT IN (SELECT DISTINCT tag_id FROM meal_items WHERE tag_id IS NOT NULL)
        """
    )
