"""Shelves — the named spots where pantry items live.

Kept separate from models.py so neither file balloons. Items point at a shelf via
pantry_items.shelf_id; deleting a shelf just orphans its items (they go Unshelved),
that's handled by the ON DELETE SET NULL on the FK.
"""
from datetime import datetime


class ShelfError(ValueError):
    """Bad shelf input — e.g. blank or duplicate name."""


def _clean_name(name):
    name = (name or "").strip()
    if not name:
        raise ShelfError("shelf name is required")
    return name


def create_shelf(conn, name, sort_order=0):
    name = _clean_name(name)
    if _name_taken(conn, name):
        raise ShelfError("a shelf with that name already exists")
    now = datetime.now().isoformat(timespec="seconds")
    cur = conn.execute(
        "INSERT INTO shelves (name, sort_order, created_at) VALUES (?, ?, ?)",
        (name, sort_order, now),
    )
    conn.commit()
    return cur.lastrowid


def get_shelf(conn, shelf_id):
    row = conn.execute("SELECT * FROM shelves WHERE id = ?", (shelf_id,)).fetchone()
    return dict(row) if row else None


def list_shelves(conn):
    """All shelves in display order (sort_order, then name)."""
    rows = conn.execute(
        "SELECT * FROM shelves ORDER BY sort_order ASC, name ASC"
    ).fetchall()
    return [dict(r) for r in rows]


def rename_shelf(conn, shelf_id, name):
    name = _clean_name(name)
    if get_shelf(conn, shelf_id) is None:
        return False
    if _name_taken(conn, name, exclude_id=shelf_id):
        raise ShelfError("a shelf with that name already exists")
    conn.execute("UPDATE shelves SET name = ? WHERE id = ?", (name, shelf_id))
    conn.commit()
    return True


def delete_shelf(conn, shelf_id):
    """Drop a shelf. Its items fall back to Unshelved via ON DELETE SET NULL."""
    cur = conn.execute("DELETE FROM shelves WHERE id = ?", (shelf_id,))
    conn.commit()
    return cur.rowcount > 0


def _name_taken(conn, name, exclude_id=None):
    # names are case-insensitively unique so "Freezer" and "freezer" don't both exist
    if exclude_id is None:
        row = conn.execute(
            "SELECT 1 FROM shelves WHERE name = ? COLLATE NOCASE", (name,)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT 1 FROM shelves WHERE name = ? COLLATE NOCASE AND id != ?",
            (name, exclude_id),
        ).fetchone()
    return row is not None
