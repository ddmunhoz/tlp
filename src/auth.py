"""Basic single-password auth — the whole app sits behind one password.

Same shape as an older project of mine (one shared password, session flag), but the
hash lives in the settings table (so it's changeable from the Settings UI) and we
use werkzeug's salted hashing instead of a bare SHA-256. Until a password is set,
the default is "admin" — the Settings page nags you to change it.
"""
import secrets

from werkzeug.security import generate_password_hash, check_password_hash

from . import settings

DEFAULT_PASSWORD = "admin"


def get_secret_key(conn):
    """A stable, random session-signing key. Generated once and stored so cookies
    survive restarts and aren't a shared hard-coded value."""
    key = settings.get(conn, "secret_key")
    if not key:
        key = secrets.token_hex(32)
        settings.set(conn, "secret_key", key)
    return key


def is_default(conn):
    """True while no password has been set — i.e. still the default 'admin'."""
    return not settings.get(conn, "password_hash")


def check_password(conn, password):
    stored = settings.get(conn, "password_hash")
    if stored:
        return check_password_hash(stored, password or "")
    return (password or "") == DEFAULT_PASSWORD


def set_password(conn, new_password):
    settings.set(conn, "password_hash", generate_password_hash(new_password))
