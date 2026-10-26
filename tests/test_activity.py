"""Activity log (0014): every stock change writes a row, and the page shows them."""
from datetime import datetime, timedelta

from src import activity, models, shelves, tags
from tests.conftest import sample_item


def _rows(conn, action=None):
    rows, _ = activity.recent(conn, action=action)
    return rows


def test_create_and_restock_log_added(conn):
    iid = models.create_item(conn, sample_item(units=2))
    models.restock(conn, iid, "2028-01-01", 4)
    rows = _rows(conn, "added")
    assert [r["item_name"] for r in rows] == ["Canned beans", "Canned beans"]
    assert rows[1]["text"] == "2 units · exp 2027-01-01"
    assert rows[0]["text"] == "4 units · exp 2028-01-01 · restock"


def test_adjust_logs_units_and_used_up(conn):
    iid = models.create_item(conn, sample_item(units=2))
    models.adjust_units(conn, iid, -1)
    assert _rows(conn, "units")[0]["text"] == "2 → 1"
    models.adjust_units(conn, iid, -1)
    gone = _rows(conn, "removed")[0]
    assert gone["text"] == "used up" and gone["item_name"] == "Canned beans"


def test_edit_logs_only_what_changed(conn):
    iid = models.create_item(conn, sample_item())
    models.update_item(conn, iid, sample_item())          # same data: nothing to say
    assert _rows(conn, "edited") == []
    models.update_item(conn, iid, sample_item(units=5, expire_date="2027-06-01"))
    row = _rows(conn, "edited")[0]
    assert row["text"] == "units 3 → 5, expiry 2027-01-01 → 2027-06-01"


def test_move_and_bulk_delete(conn):
    a = models.create_item(conn, sample_item(name="A"))
    b = models.create_item(conn, sample_item(name="B"))
    shelves.create_shelf(conn, "Top")
    sid = shelves.list_shelves(conn)[0]["id"]
    models.move_items(conn, [a, b], sid)
    moved = _rows(conn, "moved")
    assert len(moved) == 2 and moved[0]["text"] == "Unshelved → Top"
    models.move_items(conn, [a], sid)                     # already there: silent
    assert len(_rows(conn, "moved")) == 2
    models.delete_items(conn, [a, b])
    removed = _rows(conn, "removed")
    assert sorted(r["item_name"] for r in removed) == ["A", "B"]
    assert removed[0]["text"] == "3 units left · exp 2027-01-01"


def test_tags_log_adds_and_removes_once_per_product(conn):
    models.create_item(conn, sample_item(name="Rice", barcode="111"))
    tags.set_product_tags(conn, "111", ["rice", "grain"])
    tags.set_product_tags(conn, "111", ["rice", "grain"])   # same again: silent
    tags.set_product_tags(conn, "111", ["rice"])
    rows = _rows(conn, "tagged")
    assert len(rows) == 2
    assert rows[1]["text"] == "+ grain, + rice" and rows[1]["item_name"] == "Rice"
    assert rows[0]["text"] == "− grain"
    tags.add_tags_to_keys(conn, {"111"}, ["rice", "bulk"])
    assert _rows(conn, "tagged")[0]["text"] == "+ bulk"


def test_prune_drops_old_rows(conn):
    iid = models.create_item(conn, sample_item())
    old = (datetime.now() - timedelta(days=100)).isoformat(timespec="seconds")
    conn.execute("UPDATE activity_log SET at = ?", (old,))
    conn.commit()
    models.adjust_units(conn, iid, 1)
    assert activity.prune(conn, 90) == 1
    assert len(_rows(conn)) == 1
    assert activity.prune(conn, 0) == 0                    # 0 / junk = keep everything
    assert activity.prune(conn, "lots") == 0


def test_day_labels(conn):
    today = datetime(2026, 10, 22).date()
    rows = [{"at": "2026-10-22T10:00:00"}, {"at": "2026-10-21T10:00:00"},
            {"at": "2026-10-02T10:00:00"}, {"at": "2025-12-31T10:00:00"}]
    assert [g["label"] for g in activity.group_by_day(rows, today)] == \
        ["Today", "Yesterday", "Oct 2", "Dec 31, 2025"]


def test_activity_page_lists_filters_and_marks_gone_items(client):
    client.post("/items", data=sample_item(name="Rice", barcode="1", tags=["rice"]))
    client.post("/items/1/delete")
    page = client.get("/activity").data.decode()
    assert "Rice" in page and "act-pill--added" in page and "act-pill--removed" in page
    assert 'href="/items/1/edit"' not in page              # gone: plain text, no link
    assert 'aria-label="Activity"' in page                 # the nav icon
    only = client.get("/activity?action=tagged").data.decode()
    assert "+ rice" in only and "act-pill--added" not in only
    assert client.get("/activity?action=bogus").status_code == 200


def test_activity_page_links_live_items_and_pages_by_id(client):
    for i in range(3):
        client.post("/items", data=sample_item(name="Item %d" % i, barcode=str(i)))
    page = client.get("/activity").data.decode()
    assert 'href="/items/1/edit"' in page and "Older" not in page
    page2 = client.get("/activity?before=2").data.decode()
    assert "Item 0" in page2 and "Item 2" not in page2


def test_activity_keep_days_is_a_setting(client):
    client.post("/settings", data={"activity_keep_days": "30"})
    page = client.get("/settings").data.decode()
    assert 'name="activity_keep_days"' in page and 'value="30"' in page
    assert "last 30 days" in client.get("/activity").data.decode()
