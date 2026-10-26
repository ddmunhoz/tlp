"""Shared test fixtures.

Every test gets its own throwaway DATA_DIR so the real ./data is never touched
and tests can't step on each other.
"""
import os
import sys

import pytest

# make the project root importable (src/, app.py)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def conn(data_dir):
    from src import database

    database.init_db()
    connection = database.get_connection()
    yield connection
    connection.close()


@pytest.fixture
def client(data_dir):
    from app import create_app

    app = create_app()
    app.config.update(TESTING=True)
    with app.test_client() as c:
        # the app sits behind a password; pre-authenticate so the feature tests
        # exercise the routes, not the login wall (see test_auth for the wall).
        with c.session_transaction() as sess:
            sess["authenticated"] = True
        yield c


@pytest.fixture
def anon(data_dir):
    """A client that is NOT logged in — for the auth tests."""
    from app import create_app

    app = create_app()
    app.config.update(TESTING=True)
    with app.test_client() as c:
        yield c


# a valid item payload the tests can tweak per case
def sample_item(**overrides):
    base = {
        "name": "Canned beans",
        "units": 3,
        "weight_value": 400,
        "weight_unit": "g",
        "calories": 350,
        "expire_date": "2027-01-01",
    }
    base.update(overrides)
    return base
