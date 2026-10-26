"""Shelf CRUD + the rules around naming."""
import pytest

from src import shelves


def test_create_and_list(conn):
    shelves.create_shelf(conn, "Freezer")
    shelves.create_shelf(conn, "Spice rack")
    names = [s["name"] for s in shelves.list_shelves(conn)]
    assert names == ["Freezer", "Spice rack"]


def test_get_shelf(conn):
    sid = shelves.create_shelf(conn, "Freezer")
    assert shelves.get_shelf(conn, sid)["name"] == "Freezer"
    assert shelves.get_shelf(conn, 999) is None


def test_blank_name_rejected(conn):
    with pytest.raises(shelves.ShelfError):
        shelves.create_shelf(conn, "   ")


def test_duplicate_name_rejected_case_insensitive(conn):
    shelves.create_shelf(conn, "Freezer")
    with pytest.raises(shelves.ShelfError):
        shelves.create_shelf(conn, "freezer")


def test_rename(conn):
    sid = shelves.create_shelf(conn, "Freezer")
    assert shelves.rename_shelf(conn, sid, "Chest freezer") is True
    assert shelves.get_shelf(conn, sid)["name"] == "Chest freezer"


def test_rename_missing_returns_false(conn):
    assert shelves.rename_shelf(conn, 999, "Nope") is False


def test_rename_to_existing_name_rejected(conn):
    shelves.create_shelf(conn, "Freezer")
    sid = shelves.create_shelf(conn, "Pantry")
    with pytest.raises(shelves.ShelfError):
        shelves.rename_shelf(conn, sid, "Freezer")


def test_delete(conn):
    sid = shelves.create_shelf(conn, "Freezer")
    assert shelves.delete_shelf(conn, sid) is True
    assert shelves.get_shelf(conn, sid) is None
    assert shelves.delete_shelf(conn, sid) is False
