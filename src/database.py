"""SQLite connection + schema setup.

Everything DB-ish funnels through here so the rest of the app doesn't have to
care where the file lives or how the tables look. Paths come from DATA_DIR so
Docker can point them at a mounted volume.
"""
import os
import sqlite3

# every column on pantry_items, in a fixed order. shared with the CSV mirror so
# the export always lines up with the table. keep this in sync with the schema.
ITEM_COLUMNS = [
    "id",
    "barcode",
    "name",
    "units",
    "weight_value",
    "weight_unit",
    "is_fluid",
    "exclude_stats",
    "calories",
    "expire_date",
    "photo_path",
    "nutrition",
    "shelf_id",
    "created_at",
    "updated_at",
]

# units we accept for weight. value + unit instead of free text so we can do
# real stats later without parsing "500g" strings.
WEIGHT_UNITS = ("g", "kg", "ml", "l")


def product_key(barcode, name):
    """The identity a product is known by across the app — its barcode if it has one,
    else a name-based fallback. Every batch/restock of the same product shares this, so
    it's what tags attach to, what the meal planner pools stock by, and what a meal line
    matches against. Lower-cased so trivial case differences don't split a product.

    Lives here (the lowest layer) so tags/backup/models can all key off one definition
    without import cycles. Both parts are stripped: the item form may carry padding the
    DB row doesn't (validate_item strips names on save), and an unstripped fallback
    would silently key tags to a product no pantry row matches."""
    return (barcode or "").strip().lower() or ("name:" + (name or "").strip().lower())


def data_dir():
    """Where the db, backups and csv mirror live. Overridable for Docker/tests."""
    return os.environ.get("DATA_DIR", os.path.join(os.getcwd(), "data"))


def db_path():
    return os.path.join(data_dir(), "pantry.db")


def images_dir():
    """Where downloaded product photos live — inside the data volume so Docker
    keeps them alongside the db + CSV. Created lazily on first use."""
    d = os.path.join(data_dir(), "images")
    os.makedirs(d, exist_ok=True)
    return d


def get_connection():
    """Open a connection with sane defaults (row access by name, FKs on)."""
    os.makedirs(data_dir(), exist_ok=True)
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


_SCHEMA = """
CREATE TABLE IF NOT EXISTS shelves (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL UNIQUE,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS pantry_items (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    barcode          TEXT,
    name             TEXT    NOT NULL,
    units            INTEGER NOT NULL DEFAULT 1,
    weight_value     REAL    NOT NULL,
    weight_unit      TEXT    NOT NULL,
    is_fluid         INTEGER NOT NULL DEFAULT 0,
    exclude_stats    INTEGER NOT NULL DEFAULT 0,
    calories         REAL,
    expire_date      TEXT    NOT NULL,
    photo_path       TEXT,
    nutrition        TEXT,
    shelf_id         INTEGER REFERENCES shelves(id) ON DELETE SET NULL,
    created_at       TEXT    NOT NULL,
    updated_at       TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS activity_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    at         TEXT    NOT NULL,
    action     TEXT    NOT NULL,
    item_id    INTEGER,
    item_name  TEXT    NOT NULL,
    barcode    TEXT,
    details    TEXT
);

CREATE TABLE IF NOT EXISTS meals (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL,
    created_at TEXT    NOT NULL,
    updated_at TEXT    NOT NULL,
    -- how many servings of this meal the household plans to make. Shared/persisted
    -- so the pantry planner is the same for everyone on this install.
    planned_servings INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS meal_items (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    meal_id    INTEGER NOT NULL REFERENCES meals(id) ON DELETE CASCADE,
    barcode    TEXT,
    name       TEXT    NOT NULL,
    grams      REAL    NOT NULL,
    kcal_per_g REAL    NOT NULL,
    unit       TEXT    NOT NULL DEFAULT 'g',
    -- per-gram nutrient snapshots (native units: g for protein/fibre, mg for the
    -- minerals), so a saved meal keeps its nutrition even if the product leaves the
    -- pantry. NULL = wasn't captured (older meal, or the product lacked the fact).
    protein_per_g   REAL,
    fiber_per_g     REAL,
    calcium_per_g   REAL,
    iron_per_g      REAL,
    potassium_per_g REAL
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- free-form labels grouping interchangeable products (e.g. every kind of "rice").
CREATE TABLE IF NOT EXISTS tags (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE COLLATE NOCASE,
    created_at TEXT NOT NULL
);

-- a tag applied to a product *identity* (barcode-else-name key), not a single batch,
-- so every entry and future restock of that product inherits it. See models.product_key.
CREATE TABLE IF NOT EXISTS product_tags (
    tag_id      INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    product_key TEXT    NOT NULL,
    PRIMARY KEY (tag_id, product_key)
);
"""


def init_db(conn=None):
    """Create tables if they aren't there yet, then patch in any newer columns.
    Safe to call on every boot; never touches existing rows."""
    close_after = False
    if conn is None:
        conn = get_connection()
        close_after = True
    conn.executescript(_SCHEMA)
    _ensure_columns(conn)
    conn.commit()
    if close_after:
        conn.close()


# columns added after the original schema shipped. SQLite can't do "ADD COLUMN IF
# NOT EXISTS", so we check what's there and ALTER in whatever's missing — this keeps
# existing pantry databases (with real data) working across upgrades.
_ADDED_COLUMNS = [
    ("calories", "REAL"),
    ("is_fluid", "INTEGER NOT NULL DEFAULT 0"),
    ("exclude_stats", "INTEGER NOT NULL DEFAULT 0"),
]

# same idea for meal_items: per-gram nutrient snapshots patched into older meal DBs,
# plus the tag-source columns (a line can draw from a tag instead of one product).
_ADDED_MEAL_ITEM_COLUMNS = [
    ("protein_per_g", "REAL"),
    ("fiber_per_g", "REAL"),
    ("calcium_per_g", "REAL"),
    ("iron_per_g", "REAL"),
    ("potassium_per_g", "REAL"),
    ("source_type", "TEXT NOT NULL DEFAULT 'product'"),
    ("tag_id", "INTEGER"),
]

# and the shared planner count on meals.
_ADDED_MEAL_COLUMNS = [
    ("planned_servings", "INTEGER NOT NULL DEFAULT 0"),
]


def _ensure_columns(conn):
    have = {row["name"] for row in conn.execute("PRAGMA table_info(pantry_items)")}
    for name, decl in _ADDED_COLUMNS:
        if name not in have:
            conn.execute(f"ALTER TABLE pantry_items ADD COLUMN {name} {decl}")

    have_mi = {row["name"] for row in conn.execute("PRAGMA table_info(meal_items)")}
    for name, decl in _ADDED_MEAL_ITEM_COLUMNS:
        if name not in have_mi:
            conn.execute(f"ALTER TABLE meal_items ADD COLUMN {name} {decl}")

    have_m = {row["name"] for row in conn.execute("PRAGMA table_info(meals)")}
    for name, decl in _ADDED_MEAL_COLUMNS:
        if name not in have_m:
            conn.execute(f"ALTER TABLE meals ADD COLUMN {name} {decl}")
