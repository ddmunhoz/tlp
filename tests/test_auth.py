"""Single-password auth: the login wall, sign-in/out, and password change."""
from src import auth


# --- the auth helpers ---

def test_default_password_until_one_is_set(conn):
    assert auth.is_default(conn) is True
    assert auth.check_password(conn, "admin") is True
    assert auth.check_password(conn, "nope") is False


def test_set_password_replaces_the_default(conn):
    auth.set_password(conn, "hunter2")
    assert auth.is_default(conn) is False
    assert auth.check_password(conn, "hunter2") is True
    assert auth.check_password(conn, "admin") is False


def test_secret_key_is_stable_and_random(conn):
    k1 = auth.get_secret_key(conn)
    assert k1 and len(k1) >= 32
    assert auth.get_secret_key(conn) == k1        # stored, not regenerated


# --- the login wall (anon = not signed in) ---

def test_anon_is_redirected_to_login(anon):
    resp = anon.get("/pantry")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_anon_api_gets_401_not_a_redirect(anon):
    resp = anon.get("/api/pantry/123")
    assert resp.status_code == 401


def test_login_page_and_static_are_public(anon):
    assert anon.get("/login").status_code == 200


def test_login_with_default_password_grants_access(anon):
    resp = anon.post("/login", data={"password": "admin"}, follow_redirects=False)
    assert resp.status_code == 302
    # now the app opens up
    assert anon.get("/pantry").status_code == 200


def test_login_with_wrong_password_is_rejected(anon):
    resp = anon.post("/login", data={"password": "wrong"})
    assert resp.status_code == 401
    assert b"Wrong password" in resp.data
    assert anon.get("/pantry").status_code == 302   # still shut out


def test_login_next_only_follows_local_paths(anon):
    # an absolute URL must not be honoured (open-redirect guard)
    resp = anon.post("/login", data={"password": "admin", "next": "http://evil.test/"})
    assert resp.status_code == 302
    assert "evil.test" not in resp.headers["Location"]


def test_logout_clears_the_session(anon):
    anon.post("/login", data={"password": "admin"})
    assert anon.get("/pantry").status_code == 200
    anon.get("/logout")
    assert anon.get("/pantry").status_code == 302


# --- changing the password from settings ---

def test_change_password_happy_path(client):
    resp = client.post("/settings/password", data={
        "current_password": "admin", "new_password": "freshpass",
        "confirm_password": "freshpass",
    }, follow_redirects=True)
    assert b"Password changed" in resp.data


def test_change_password_wrong_current(client):
    resp = client.post("/settings/password", data={
        "current_password": "nope", "new_password": "freshpass",
        "confirm_password": "freshpass",
    }, follow_redirects=True)
    assert b"Current password is incorrect" in resp.data


def test_change_password_mismatch(client):
    resp = client.post("/settings/password", data={
        "current_password": "admin", "new_password": "freshpass",
        "confirm_password": "different",
    }, follow_redirects=True)
    assert b"don&#39;t match" in resp.data


def test_change_password_too_short(client):
    resp = client.post("/settings/password", data={
        "current_password": "admin", "new_password": "ab", "confirm_password": "ab",
    }, follow_redirects=True)
    assert b"at least 4" in resp.data
