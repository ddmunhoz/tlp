"""App settings — a plain key/value store in the db.

Requirement is that *all* config is editable through the Settings UI, so nothing
lives in env/config files that the user can't reach. Defaults below fill in the
gaps until the user overrides them.
"""
from . import backup

DEFAULTS = {
    # how many daily backups to keep around before pruning the oldest.
    "backup_keep": str(backup.DEFAULT_KEEP),
    # days-before-expiry that counts as "expiring soon". used by the UI now,
    # and by the Signal alert feature once that proposal lands.
    "expiry_warn_days": "7",
    # how many items a shelf shows before it paginates. 0 = show everything.
    "shelf_page_size": "25",
    # how far back the Activity page keeps its log; older rows get pruned.
    "activity_keep_days": "365",
    # which Open Food Facts subdomain to query — steers localisation (e.g. "ca" for
    # bilingual Canadian labels). "world" is the global aggregate.
    "off_country": "world",
    # who's eating out of the pantry — drives the "how long will it last" maths.
    "household_men": "1",
    "household_women": "1",
    "household_children": "0",
    # how many meals each person eats a day. turns "planned servings" into real
    # days: days = servings / (people * meals_per_day). 1 keeps the old maths.
    "meals_per_day": "1",
    # daily kcal we budget per head. rough adult/child baselines; tune to taste.
    "cal_per_man": "2500",
    "cal_per_woman": "2000",
    "cal_per_child": "1500",
    # daily drinking fluids per head, in litres (total-fluids guideline).
    "water_per_man": "3.7",
    "water_per_woman": "2.7",
    "water_per_child": "1.5",
    # daily nutrient targets per adult — reference intakes used by the
    # "days of nutrition" tower (total in the pantry ÷ headcount × target).
    # macros in grams, minerals in milligrams, matching the stored per-100 units.
    "protein_per_adult": "50",       # g/day
    "fiber_per_adult": "30",         # g/day
    "calcium_per_adult": "1000",     # mg/day
    "iron_per_adult": "14",          # mg/day
    "potassium_per_adult": "3500",   # mg/day
    # whether the statistics leave expired items out. off by default — expired
    # stock still counts unless you say otherwise.
    "exclude_expired": "no",
}


def get(conn, key, default=None):
    row = conn.execute(
        "SELECT value FROM settings WHERE key = ?", (key,)
    ).fetchone()
    if row is not None:
        return row["value"]
    if default is not None:
        return default
    return DEFAULTS.get(key)


def set(conn, key, value):
    conn.execute(
        """
        INSERT INTO settings (key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, str(value)),
    )
    conn.commit()


def all_settings(conn):
    """Everything the Settings page needs: stored values over the defaults."""
    merged = dict(DEFAULTS)
    for row in conn.execute("SELECT key, value FROM settings").fetchall():
        merged[row["key"]] = row["value"]
    return merged
