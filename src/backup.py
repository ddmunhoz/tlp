"""Data-safety bits: DB backups + the always-current CSV mirror.

Two independent guarantees from the requirements:
  1. we keep timestamped copies of the db so nothing is ever really lost — taken
     automatically once a day (first request of the day, see auto_backup_if_due)
     and on demand via Settings' "Backup now", pruned to the `backup_keep` newest, and
  2. a plain CSV of the whole pantry sits on disk at all times, rewritten after
     every change, so even if the app dies the data is still readable.

This module talks to sqlite directly (via database.get_connection) and doesn't
import models, to keep the import graph a straight line.
"""
import csv
import io
import os
import re
import shutil
import threading
from datetime import datetime

from . import database, tags

DEFAULT_KEEP = 7  # daily backups -> a rolling week


def csv_path():
    return os.path.join(database.data_dir(), "pantry_export.csv")


def backups_dir():
    d = os.path.join(database.data_dir(), "backups")
    os.makedirs(d, exist_ok=True)
    return d


# CSV carries every db column plus a resolved shelf *name* and the product's tags (a
# semicolon-joined list) so the export stays human-readable and complete on its own.
CSV_COLUMNS = database.ITEM_COLUMNS + ["shelf", "tags"]


def _rows(conn):
    cols = ", ".join("i." + c for c in database.ITEM_COLUMNS)
    cur = conn.execute(
        f"SELECT {cols}, s.name AS shelf "
        "FROM pantry_items i LEFT JOIN shelves s ON s.id = i.shelf_id "
        "ORDER BY i.id"
    )
    return cur.fetchall()


def export_csv_string(conn):
    """Full CSV of the pantry as a string. Used for on-demand download."""
    rows = _rows(conn)
    # tags live on the product identity, so resolve them once per key for the whole export
    tag_map = tags.tags_for_products(
        conn, [database.product_key(r["barcode"], r["name"]) for r in rows]
    )
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(CSV_COLUMNS)
    for row in rows:
        key = database.product_key(row["barcode"], row["name"])
        values = [row[c] for c in database.ITEM_COLUMNS] + [
            row["shelf"], ";".join(tag_map.get(key, [])),
        ]
        writer.writerow(values)
    return buf.getvalue()


def write_csv_mirror(conn):
    """Rewrite the on-disk CSV mirror. Called after every write.

    Written to a temp file then renamed so a crash mid-write can't leave a
    half-baked CSV behind.
    """
    target = csv_path()
    # unique temp per writer — the background photo-fetch thread can rewrite the
    # mirror at the same time as a request, and a shared temp name would collide.
    tmp = "%s.%d-%d.tmp" % (target, os.getpid(), threading.get_ident())
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        fh.write(export_csv_string(conn))
    os.replace(tmp, target)   # atomic swap-in
    return target


def create_backup(keep=DEFAULT_KEEP):
    """Copy the live db file into backups/ with a timestamp, then prune old ones.

    Returns the backup path, or None if there's no db yet (nothing to back up).
    """
    src = database.db_path()
    if not os.path.exists(src):
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(backups_dir(), f"pantry-{stamp}.db")
    shutil.copy2(src, dest)
    prune_backups(keep)
    return dest


_BACKUP_NAME = re.compile(r"pantry-(\d{8})-\d{6}\.db$")


def _backup_date(path):
    """The YYYYMMDD part of a backup's filename, or None for a name that doesn't
    fit the pattern. Filenames are the source of truth for when a backup was taken —
    copy2 preserves the *db's* mtime, so file times say nothing about backup time."""
    m = _BACKUP_NAME.match(os.path.basename(path))
    return m.group(1) if m else None


def auto_backup_if_due(keep=DEFAULT_KEEP, now=None):
    """Take the day's backup unless one stamped today already exists.

    The daily cadence from proposal 0001 ("daily, keep last N"), driven by requests:
    the app calls this and the first hit of a new day snapshots the db. Returns the
    new backup's path, or None when today is already covered (or there's no db yet).
    """
    today = (now or datetime.now()).strftime("%Y%m%d")
    for path in list_backups():
        if _backup_date(path) == today:
            return None
    return create_backup(keep=keep)


def list_backups():
    """Backup files, newest first."""
    files = [
        os.path.join(backups_dir(), f)
        for f in os.listdir(backups_dir())
        if f.startswith("pantry-") and f.endswith(".db")
    ]
    return sorted(files, reverse=True)


def prune_backups(keep=DEFAULT_KEEP):
    """Drop the oldest backups past the keep count."""
    for old in list_backups()[keep:]:
        os.remove(old)
