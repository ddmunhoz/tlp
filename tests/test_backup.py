"""Data-safety guarantees: the CSV mirror stays current and backups rotate."""
import csv
import os

from src import backup, models, shelves
from tests.conftest import sample_item


def _read_mirror():
    with open(backup.csv_path(), newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def test_csv_mirror_written_after_create(conn):
    models.create_item(conn, sample_item(name="Rice"))
    rows = _read_mirror()
    assert len(rows) == 1
    assert rows[0]["name"] == "Rice"


def test_csv_mirror_reflects_updates_and_deletes(conn):
    item_id = models.create_item(conn, sample_item(name="Rice"))
    models.create_item(conn, sample_item(name="Pasta"))

    models.update_item(conn, item_id, sample_item(name="Brown rice"))
    names = {r["name"] for r in _read_mirror()}
    assert names == {"Brown rice", "Pasta"}

    models.delete_item(conn, item_id)
    names = {r["name"] for r in _read_mirror()}
    assert names == {"Pasta"}


def test_csv_export_string_has_header(conn):
    models.create_item(conn, sample_item())
    text = backup.export_csv_string(conn)
    first_line = text.splitlines()[0]
    assert first_line.startswith("id,barcode,name")


def test_csv_mirror_includes_shelf_name(conn):
    sid = shelves.create_shelf(conn, "Freezer")
    models.create_item(conn, sample_item(name="Peas", shelf_id=sid))
    rows = _read_mirror()
    assert rows[0]["shelf"] == "Freezer"


def test_create_backup_makes_a_file(conn):
    models.create_item(conn, sample_item())
    path = backup.create_backup()
    assert path is not None
    assert os.path.exists(path)
    assert len(backup.list_backups()) == 1


def test_backup_pruning_keeps_only_n(conn):
    models.create_item(conn, sample_item())
    # drop a bunch of fake old backups in, then prune to 3
    for stamp in range(10):
        with open(os.path.join(backup.backups_dir(), f"pantry-2020010{stamp}-000000.db"), "w") as fh:
            fh.write("x")
    backup.prune_backups(keep=3)
    assert len(backup.list_backups()) == 3


def test_backup_none_when_no_db(data_dir):
    # no db file created yet -> nothing to back up
    assert backup.create_backup() is None


# --- the daily auto-backup ---

def test_auto_backup_takes_first_of_the_day(conn):
    models.create_item(conn, sample_item())
    path = backup.auto_backup_if_due()
    assert path is not None and os.path.exists(path)
    assert len(backup.list_backups()) == 1


def test_auto_backup_skips_when_today_is_covered(conn):
    models.create_item(conn, sample_item())
    backup.create_backup()   # a manual backup counts as today's
    assert backup.auto_backup_if_due() is None
    assert len(backup.list_backups()) == 1


def test_auto_backup_due_when_only_old_backups_exist(conn):
    models.create_item(conn, sample_item())
    with open(os.path.join(backup.backups_dir(), "pantry-20200101-000000.db"), "w") as fh:
        fh.write("x")
    assert backup.auto_backup_if_due() is not None
    assert len(backup.list_backups()) == 2


def test_auto_backup_ignores_foreign_filenames(conn):
    # junk in backups/ (e.g. a stray json) must not read as "today's backup done"
    models.create_item(conn, sample_item())
    assert backup._backup_date("serving.json") is None
    assert backup.auto_backup_if_due() is not None


def test_request_triggers_the_daily_backup(client):
    client.get("/")
    assert len(backup.list_backups()) == 1
    client.get("/")   # same day -> no second backup
    assert len(backup.list_backups()) == 1
