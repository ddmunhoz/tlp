"""The Last Pantry — Flask entrypoint.

Kept deliberately thin: routes wire HTTP to the src/ modules and render
templates, nothing more. Business logic lives in src/.
"""
import json
import os
import threading
import unicodedata
from datetime import date
from urllib.parse import urlparse

from flask import (
    Flask, g, redirect, render_template, request, url_for, Response, flash,
    jsonify, send_from_directory, session,
)

from src import (
    activity, auth, backup, database, meals, models, offfacts, settings, shelves, stats, tags,
)


def _fold(text):
    """Casefold + strip diacritics so search is accent- and case-insensitive:
    'cafe'/'Cafe' both match 'Café', 'acucar' matches 'Açúcar'. Used by the server
    filter and (via the `fold` template filter) to build the client search blob, so
    both sides fold identically."""
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in text if not unicodedata.combining(c)).casefold()


def _download_and_attach(item_id, url, barcode):
    """Pull a product image and hang it on the item — meant to run off-request.

    The OFF image server can take many seconds, and we don't want Save to wait on
    it, so this runs in a background thread with its own db connection. If the
    fetch fails the item just stays photo-less; a missing picture isn't worth a fuss.
    """
    filename = offfacts.download_image(url, barcode, database.images_dir())
    if not filename:
        return
    conn = database.get_connection()
    try:
        conn.execute("UPDATE pantry_items SET photo_path = ? WHERE id = ?", (filename, item_id))
        conn.commit()
        backup.write_csv_mirror(conn)
    finally:
        conn.close()


def format_weight(value, unit):
    """Human-friendly weight/volume — rolls g→kg and ml→l once it's big enough.

    e.g. (400, "g") -> "400 g", (1200, "g") -> "1.2 kg".
    """
    try:
        value = float(value)
    except (TypeError, ValueError):
        return ""
    unit = (unit or "").lower()
    if unit == "g" and value >= 1000:
        value, unit = value / 1000, "kg"
    elif unit == "ml" and value >= 1000:
        value, unit = value / 1000, "l"
    num = f"{value:.2f}".rstrip("0").rstrip(".")
    return f"{num} {unit}"


def get_db():
    if "db" not in g:
        g.db = database.get_connection()
    return g.db


def create_app():
    app = Flask(__name__)

    # make sure the schema exists before we serve anything.
    database.init_db()

    # session-signing key — a stable random value kept in the settings table.
    _boot = database.get_connection()
    app.secret_key = auth.get_secret_key(_boot)
    _boot.close()

    # daily auto-backup (proposal 0001: daily, keep last `backup_keep`) — the first
    # request of each day snapshots the db. The in-memory day marker makes every
    # later request free; only the first per worker-day even looks at the disk.
    # Registered ahead of the login gate so an unauthenticated hit still counts.
    _backup_done_for = {"day": None}

    @app.before_request
    def _auto_backup():
        if request.endpoint == "static":
            return
        today = date.today().isoformat()
        if _backup_done_for["day"] == today:
            return
        try:
            backup.auto_backup_if_due(keep=_int_setting(get_db(), "backup_keep"))
            activity.prune(get_db(), _int_setting(get_db(), "activity_keep_days"))
        except Exception:
            # a failed backup must never take the site down; the Settings page's
            # backup list makes the gap visible.
            app.logger.exception("daily auto-backup failed")
        _backup_done_for["day"] = today   # one attempt per worker per day, even on failure

    @app.before_request
    def _require_login():
        # the whole app sits behind one password; only the login page + static
        # assets (needed to render it) are public.
        if request.endpoint in ("login", "static") or request.endpoint is None:
            return
        if not session.get("authenticated"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "authentication required"}), 401
            return redirect(url_for("login", next=request.path))

    # cache-bust static assets: append the file's mtime so a changed CSS/JS always
    # refetches instead of Safari serving a stale copy. Templates use asset('...').
    @app.template_global()
    def asset(filename):
        try:
            version = int(os.path.getmtime(os.path.join(app.static_folder, filename)))
        except OSError:
            version = 0
        return url_for("static", filename=filename) + f"?v={version}"

    @app.template_filter("grouped")
    def _grouped(value):
        """Big kcal figures read easier with thousands separators."""
        try:
            return "{:,}".format(int(round(float(value))))
        except (TypeError, ValueError):
            return value

    @app.template_filter("fold")
    def _fold_filter(value):
        """Accent/case-folded text for the client search index (see _fold)."""
        return _fold(value)

    @app.template_filter("fromjson")
    def _fromjson(value):
        """Parse a stored JSON blob for the templates; junk/empty -> None."""
        try:
            return json.loads(value) if value else None
        except (ValueError, TypeError):
            return None

    @app.teardown_appcontext
    def close_db(exc):
        db = g.pop("db", None)
        if db is not None:
            db.close()

    def days_left(expire_date):
        try:
            return (date.fromisoformat(expire_date) - date.today()).days
        except (TypeError, ValueError):   # None / non-string dates -> "no date"
            return None

    def _int_setting(conn, key):
        """Settings are stored as strings; pull one back as an int, defaulting
        gracefully if someone typed junk into the box."""
        try:
            return int(settings.get(conn, key))
        except (TypeError, ValueError):
            return int(settings.DEFAULTS[key])

    def _float_setting(conn, key):
        """Same, for the litres-per-day water settings."""
        try:
            return float(settings.get(conn, key))
        except (TypeError, ValueError):
            return float(settings.DEFAULTS[key])

    @app.route("/login", methods=["GET", "POST"])
    def login():
        conn = get_db()
        if request.method == "POST":
            if auth.check_password(conn, request.form.get("password")):
                session["authenticated"] = True
                session.permanent = True
                # only follow a local path back — no open redirects.
                nxt = request.form.get("next") or ""
                if not nxt.startswith("/") or nxt.startswith("//"):
                    nxt = url_for("index")
                return redirect(nxt)
            return render_template("login.html", error="Wrong password.",
                                   next=request.form.get("next")), 401
        if session.get("authenticated"):
            return redirect(url_for("index"))
        return render_template("login.html", next=request.args.get("next"))

    @app.route("/logout")
    def logout():
        session.pop("authenticated", None)
        flash("Signed out.", "ok")
        return redirect(url_for("login"))

    @app.route("/")
    def index():
        # the landing page — three doors: scan, pantry, statistics.
        return render_template("home.html")

    @app.route("/statistics")
    def statistics():
        conn = get_db()
        items = models.list_items(conn)
        for it in items:
            _decorate(it)
        people = {
            "men": _int_setting(conn, "household_men"),
            "women": _int_setting(conn, "household_women"),
            "children": _int_setting(conn, "household_children"),
        }
        per_person = {
            "man": _int_setting(conn, "cal_per_man"),
            "woman": _int_setting(conn, "cal_per_woman"),
            "child": _int_setting(conn, "cal_per_child"),
        }
        per_water = {
            "man": _float_setting(conn, "water_per_man"),
            "woman": _float_setting(conn, "water_per_woman"),
            "child": _float_setting(conn, "water_per_child"),
        }
        include_expired = settings.get(conn, "exclude_expired") != "yes"
        forecast = stats.forecast(items, people, per_person, include_expired=include_expired)
        water = stats.water_forecast(items, people, per_water, include_expired=include_expired)
        breakdown = stats.nutrient_breakdown(items, include_expired=include_expired)
        by_shelf = stats.calories_by_shelf(items, include_expired=include_expired)
        by_year = stats.food_by_year(items, include_expired=include_expired)
        nutrient_targets = {
            key: _float_setting(conn, skey)
            for key, _label, _unit, skey in stats.SUPPLY_NUTRIENTS
        }
        supply = stats.nutrient_supply(
            items, forecast["people_total"], nutrient_targets,
            include_expired=include_expired, calorie_days=forecast["days"],
        )
        load = stats.intake_load(breakdown["grams"], forecast["days"], forecast["people_total"])
        meals_days, meals_total = _meals_days_total(conn, people)
        return render_template(
            "statistics.html", forecast=forecast, water=water, people=people,
            per_person=per_person, breakdown=breakdown, by_shelf=by_shelf,
            by_year=by_year, supply=supply, load=load,
            meals_days=meals_days, meals_total=meals_total,
            meals_per_day=_meals_per_day(conn),
            bom_pending=_bom_pending(conn),
        )

    # ---- meals ----
    def _meal_person_targets(conn):
        return {
            "man": _int_setting(conn, "cal_per_man"),
            "woman": _int_setting(conn, "cal_per_woman"),
            "child": _int_setting(conn, "cal_per_child"),
        }

    # the identity a meal line shares with a pantry product — the app-wide product key.
    _meal_pantry_key = models.product_key

    def _component_key(it):
        """The stock-pool key a saved meal line draws from: a tag pool ("tag:<id>",
        every tagged product pooled) for a tag line, else the single product's key."""
        if it.get("source_type") == "tag":
            return "tag:%s" % it["tag_id"]
        return _meal_pantry_key(it["barcode"], it["name"])

    def _product_choice_map(conn):
        """Pantry products you can portion into a meal, keyed by product identity —
        each with a kcal-per-gram, a photo, and per-gram nutrient figures for the live
        nutrient bars. Only items with a calorie basis qualify; entries of the same
        product (barcode, else name) fold into one pick. Also the per-product source the
        tag averages are built from."""
        by_key = {}
        for it in models.list_items(conn):
            per_unit = stats.item_kcal(it)
            base = stats._base_grams(it["weight_value"], it["weight_unit"])
            if per_unit is None or not base:
                continue   # no calorie basis -> can't portion by weight
            key = _meal_pantry_key(it["barcode"], it["name"])
            if key in by_key:
                if not by_key[key]["photo_path"] and it["photo_path"]:
                    by_key[key]["photo_path"] = it["photo_path"]
                continue
            choice = {
                "key": key,
                "name": it["name"],
                "barcode": it["barcode"] or "",
                "kcal_per_g": round(per_unit / base, 4),
                "unit": "ml" if (it["weight_unit"] or "").lower() in ("ml", "l") else "g",
                "photo_path": it["photo_path"],
            }
            # per-gram nutrient snapshots from the stored per-100 facts (None if absent)
            per_100 = (stats._nutrition(it) or {}).get("per_100g") or {}
            for nkey, col in meals.NUTRIENT_COLUMNS.items():
                v = per_100.get(nkey)
                try:
                    choice[col] = round(float(v) / 100.0, 6)
                except (TypeError, ValueError):
                    choice[col] = None
            by_key[key] = choice
        return by_key

    def _meal_item_choices(conn):
        """Product picks for the meal builder, alphabetical (see _product_choice_map)."""
        return sorted(_product_choice_map(conn).values(), key=lambda c: c["name"].lower())

    def _meal_tag_choices(conn):
        """Tag picks for the meal builder. A tag line pools every product carrying the
        tag, so its kcal/g + nutrient-per-g are the *live average* across the tag's
        in-stock members (those with a calorie basis). `in_stock` = how many members are
        currently sourceable; 0 means a meal line on this tag would show unsourced."""
        prod = _product_choice_map(conn)
        choices = []
        for t in tags.list_tags(conn):
            avail = [prod[k] for k in tags.product_keys_for_tag(conn, t["id"]) if k in prod]
            choice = {"tag_id": t["id"], "name": t["name"],
                      "member_count": t["usage"], "in_stock": len(avail)}
            kcals = [p["kcal_per_g"] for p in avail]
            choice["kcal_per_g"] = round(sum(kcals) / len(kcals), 4) if kcals else 0.0
            for col in meals.NUTRIENT_COLUMNS.values():
                vals = [p[col] for p in avail if p.get(col) is not None]
                choice[col] = round(sum(vals) / len(vals), 6) if vals else None
            choices.append(choice)
        return choices

    def _resolve_tag_lines(conn, meal_list):
        """Refresh every tag-sourced meal line's calories/nutrients from the tag's
        current in-stock average (the stored per-g values on a tag line are only a
        fallback), then recompute each meal's totals. Also stamps `in_stock` per tag
        line so `_mark_unsourced` can flag tags with nothing on the shelf."""
        tag_nut = {c["tag_id"]: c for c in _meal_tag_choices(conn)}
        for m in meal_list:
            for it in m["items"]:
                if it.get("source_type") != "tag":
                    continue
                info = tag_nut.get(it["tag_id"])
                it["in_stock"] = info["in_stock"] if info else 0
                # only adopt the live average while members are actually in stock —
                # with none (or the tag gone) the average would be 0 and wipe the
                # meal's numbers; keep the stored snapshot instead (same contract as
                # product lines: unsourced but still showing what the meal was).
                if info and info["in_stock"]:
                    it["kcal_per_g"] = info["kcal_per_g"]
                    for col in meals.NUTRIENT_COLUMNS.values():
                        it[col] = info[col]
            m["total_kcal"] = sum(it["grams"] * it["kcal_per_g"] for it in m["items"])
            m["nutrients"] = meals._meal_nutrients(m["items"])
        return meal_list

    def _meal_nutrient_targets(conn):
        """Daily per-adult target for each meal nutrient, keyed by display key —
        the denominator behind the '% of an adult's day' bars. From Settings."""
        return {key: _float_setting(conn, skey)
                for key, _label, _unit, skey in stats.SUPPLY_NUTRIENTS}

    def _mark_unsourced(conn, saved):
        """Flag saved meals whose components can't be cooked from current stock.

        The snapshot keeps a meal's numbers valid forever, but if you can't source it
        we surface a ⚠️ with the names. A product line is unsourced when that product is
        gone; a tag line is unsourced when no product carrying the tag is in stock
        (`in_stock`, stamped by `_resolve_tag_lines`, which must run first)."""
        available = {_meal_pantry_key(it["barcode"], it["name"])
                     for it in models.list_items(conn)}
        for m in saved:
            missing = []
            for it in m["items"]:
                if it.get("source_type") == "tag":
                    if not it.get("in_stock"):
                        missing.append(it["name"])
                elif _meal_pantry_key(it["barcode"], it["name"]) not in available:
                    missing.append(it["name"])
            m["unsourced"] = missing
        return saved

    def _pantry_stock_grams(conn):
        """Grams (or ml) of each product on the shelves, keyed like a meal line.

        Sums net weight × units over every entry of a product (same barcode-else-name
        key the meals use), so shared ingredients pool together. Honours the same
        `exclude_expired` setting the statistics use. Carries a display name."""
        skip_expired = settings.get(conn, "exclude_expired") == "yes"
        stock = {}
        for it in models.list_items(conn):
            base = stats._base_grams(it["weight_value"], it["weight_unit"])
            if base is None:
                continue
            if skip_expired and days_left(it["expire_date"]) is not None \
                    and days_left(it["expire_date"]) < 0:
                continue
            try:
                units = int(it["units"] or 0)
            except (TypeError, ValueError):
                units = 0
            key = _meal_pantry_key(it["barcode"], it["name"])
            row = stock.setdefault(key, {"g": 0.0, "name": it["name"]})
            row["g"] += base * units
        return stock

    def _household_people(conn):
        """The household headcount from Settings — the denominator behind 'lasts X
        days': one serving is one person's portion, so a batch divided by heads is
        the days everyone gets a portion. Children count as a head (a portion each)."""
        return {
            "men": _int_setting(conn, "household_men"),
            "women": _int_setting(conn, "household_women"),
            "children": _int_setting(conn, "household_children"),
        }

    def _meals_per_day(conn):
        """How many meals a head eats a day (0015). Never below 1 — a 0 here would
        either blow up the division or make the pantry look infinite."""
        return max(1, _int_setting(conn, "meals_per_day"))

    def _meals_days_total(conn, people):
        """The statistics hero's meals tile: (days, servings). Servings is the sum,
        over every saved meal, of its planned servings — or, if none are planned, the
        most it could be cooked from current stock (its own ceiling, tags pooled).
        Days is that ÷ (household × meals per day), same rule the meal cards use.
        Days is None when there's no household to divide by."""
        heads = people["men"] + people["women"] + people["children"]
        stock = _pantry_stock_grams(conn)
        tag_g = {}
        for t in tags.list_tags(conn):
            tag_g["tag:%s" % t["id"]] = sum(
                stock[k]["g"] for k in tags.product_keys_for_tag(conn, t["id"]) if k in stock)

        def pool_g(key):
            if key.startswith("tag:"):
                return tag_g.get(key, 0.0)
            return stock.get(key, {}).get("g", 0.0)

        total = 0
        for m in meals.list_meals(conn):
            planned = m.get("planned_servings") or 0
            if planned > 0:
                servings = planned
            else:
                caps = [int(pool_g(_component_key(it)) // it["grams"])
                        for it in m["items"] if it["grams"] > 0]
                servings = max(0, min(caps)) if caps else 0
            total += servings
        if heads <= 0:
            return None, total
        return total / (heads * _meals_per_day(conn)), total

    def _bom_pending(conn):
        """Whether the saved plan (planned servings) needs more of any ingredient than
        is in stock — i.e. the meals page is showing a BOM. Same pooling as the planner:
        demand = Σ planned × grams per ingredient, flagged when it tops the stock pool."""
        stock = _pantry_stock_grams(conn)
        tag_g = {}
        for t in tags.list_tags(conn):
            tag_g["tag:%s" % t["id"]] = sum(
                stock[k]["g"] for k in tags.product_keys_for_tag(conn, t["id"]) if k in stock)

        def pool_g(key):
            if key.startswith("tag:"):
                return tag_g.get(key, 0.0)
            return stock.get(key, {}).get("g", 0.0)

        demand = {}
        for m in meals.list_meals(conn):
            n = m.get("planned_servings") or 0
            if n <= 0:
                continue
            for it in m["items"]:
                key = _component_key(it)
                demand[key] = demand.get(key, 0.0) + n * it["grams"]
        return any(d > pool_g(key) + 0.001 for key, d in demand.items())

    def _meal_plan_data(conn, saved):
        """The client planner's whole input: pooled stock, each meal's ingredient
        demand and its stored planned-servings, plus the household headcount. From
        those the client shows how many days a batch lasts (servings ÷ (people ×
        meals a day)) — an ingredient-and-people figure, no calorie basis.
        The allocation maths runs in JS."""
        stock = _pantry_stock_grams(conn)
        # tag pools: an aggregate "tag:<id>" whose stock is the sum of every member
        # product's grams, so a tag line draws from all of them at once. (A product
        # counted here *and* pinned directly by another meal isn't shared across the two
        # pools — same marginal caveat the planner already carries.)
        for t in tags.list_tags(conn):
            members = tags.product_keys_for_tag(conn, t["id"])
            grams = sum(stock[k]["g"] for k in members if k in stock)
            stock["tag:%s" % t["id"]] = {"g": grams, "name": "🏷 " + t["name"]}
        return {
            "stock": {k: round(v["g"], 1) for k, v in stock.items()},
            "names": {k: v["name"] for k, v in stock.items()},
            "people": _household_people(conn),
            "meals_per_day": _meals_per_day(conn),
            "meals": [
                {
                    "id": m["id"],
                    "name": m["name"],
                    "planned": m.get("planned_servings") or 0,
                    "components": [
                        {"key": _component_key(it),
                         "name": it["name"], "grams": it["grams"]}
                        for it in m["items"]
                    ],
                }
                for m in saved
            ],
        }

    def _parse_components():
        try:
            comps = json.loads(request.form.get("components") or "[]")
        except (ValueError, TypeError):
            comps = []
        return comps if isinstance(comps, list) else []

    # label/unit/column metadata for the nutrient bars, from the single stats source
    _MEAL_NUTRIENT_META = [
        {"key": key, "label": label, "unit": unit, "col": meals.NUTRIENT_COLUMNS[key]}
        for key, label, unit, _skey in stats.SUPPLY_NUTRIENTS
    ]

    @app.route("/meals")
    def meals_page():
        """The menu: saved meals + the shared pantry planner. Building lives on its
        own page (`build_meal`) so this stays focused and uncrowded."""
        conn = get_db()
        # resolve tag lines to live nutrition *before* flagging unsourced (which reads
        # the in_stock each resolve stamps on the tag lines).
        saved = _resolve_tag_lines(conn, meals.list_meals(conn))
        saved = _mark_unsourced(conn, saved)
        # header tally: meals · servings · days (servings + days update live in JS as
        # the steppers change; days = planned servings ÷ (household × meals a day))
        people = _household_people(conn)
        heads = people["men"] + people["women"] + people["children"]
        servings = sum(m.get("planned_servings") or 0 for m in saved)
        per_day = heads * _meals_per_day(conn)
        menu = {
            "meals": len(saved),
            "servings": servings,
            # round half-up so it matches the JS Math.round the live stepper uses
            "days": int(servings / per_day + 0.5) if heads else None,
        }
        return render_template(
            "meals.html", targets=_meal_person_targets(conn),
            nutrient_targets=_meal_nutrient_targets(conn),
            nutrient_meta=_MEAL_NUTRIENT_META, menu=menu,
            saved=saved, plan_data=_meal_plan_data(conn, saved),
        )

    def _render_meal_form(conn, meal):
        if meal is not None:
            _resolve_tag_lines(conn, [meal])   # show live tag nutrition while editing
        return render_template(
            "meal_form.html", choices=_meal_item_choices(conn),
            tag_choices=_meal_tag_choices(conn),
            targets=_meal_person_targets(conn),
            nutrient_targets=_meal_nutrient_targets(conn),
            nutrient_meta=_MEAL_NUTRIENT_META, meal=meal,
        )

    @app.route("/meals/new")
    def build_meal():
        return _render_meal_form(get_db(), None)

    @app.route("/meals/<int:meal_id>/edit")
    def edit_meal(meal_id):
        conn = get_db()
        meal = meals.get_meal(conn, meal_id)
        if meal is None:
            return render_template("404.html"), 404
        return _render_meal_form(conn, meal)

    @app.route("/meals/<int:meal_id>/plan", methods=["POST"])
    def plan_meal(meal_id):
        """Persist the shared planned-servings count for a meal (planner stepper)."""
        conn = get_db()
        servings = request.form.get("servings")
        if servings is None and request.is_json:
            servings = (request.get_json(silent=True) or {}).get("servings")
        stored = meals.set_planned(conn, meal_id, servings)
        if stored is None:
            return jsonify({"error": "meal not found"}), 404
        return jsonify({"ok": True, "id": meal_id, "servings": stored})

    @app.route("/meals", methods=["POST"])
    def save_meal():
        conn = get_db()
        try:
            meals.create_meal(conn, request.form.get("name"), _parse_components())
        except meals.MealError as err:
            flash(str(err), "error")
            return redirect(url_for("meals_page"))
        flash("Meal saved to your menu.", "ok")
        return redirect(url_for("meals_page"))

    @app.route("/meals/<int:meal_id>", methods=["POST"])
    def update_meal(meal_id):
        conn = get_db()
        try:
            ok = meals.update_meal(conn, meal_id, request.form.get("name"),
                                   _parse_components())
        except meals.MealError as err:
            flash(str(err), "error")
            return redirect(url_for("edit_meal", meal_id=meal_id))
        if not ok:
            return render_template("404.html"), 404
        flash("Meal updated.", "ok")
        return redirect(url_for("meals_page"))

    @app.route("/meals/<int:meal_id>/delete", methods=["POST"])
    def remove_meal(meal_id):
        conn = get_db()
        meals.delete_meal(conn, meal_id)
        flash("Meal removed from your menu.", "ok")
        return redirect(url_for("meals_page"))

    _MACRO_LABELS = {
        "fat": "Fat", "carbohydrates": "Carbs", "proteins": "Protein",
        # extra nutrients the statistics "days of nutrition" tiers drill into
        "fiber": "Fibre", "calcium": "Calcium", "iron": "Iron", "potassium": "Potassium",
    }
    _NOVA_LABELS = {1: "Unprocessed", 2: "Culinary", 3: "Processed", 4: "Ultra-processed"}

    def _expires_in_year(it, year):
        exp = it.get("expire_date")
        if not exp:
            return False
        try:
            return date.fromisoformat(str(exp)).year == year
        except (ValueError, TypeError):
            return False

    def _expiry_years(items):
        """The distinct expiry years present in the pantry, oldest first —
        drives the year filter pills."""
        years = set()
        for it in items:
            exp = it.get("expire_date")
            if not exp:
                continue
            try:
                years.add(date.fromisoformat(str(exp)).year)
            except (ValueError, TypeError):
                pass
        return sorted(years)

    def _pantry_filter(items):
        """Read the pantry's search + filter query and apply it.

        `q` is a name search; `macro`/`nova`/`grade` narrow by nutrition (the last
        two come from clicking a statistics chart). Everything combines. Returns the
        filtered items plus what's active — the search bar + banner read off this.
        """
        q = (request.args.get("q") or "").strip()
        macro = request.args.get("macro")
        if macro not in _MACRO_LABELS:
            macro = None
        try:
            nova = int(request.args.get("nova", ""))
        except (TypeError, ValueError):
            nova = None
        if nova not in _NOVA_LABELS:
            nova = None
        grade = (request.args.get("grade") or "").lower()
        if grade not in ("a", "b", "c", "d", "e"):
            grade = None
        try:
            year = int(request.args.get("year", ""))
        except (TypeError, ValueError):
            year = None
        fluid = request.args.get("fluid") in ("1", "true", "yes")
        excluded = request.args.get("excluded") in ("1", "true", "yes")
        tag = tags.normalize(request.args.get("tag")) or None

        filtered = items
        parts = []
        if q:
            # accent/case-insensitive, and matches the name OR any of the item's tags,
            # so "cafe" finds "Café" and "rice" finds an Arroz tagged rice.
            needle = _fold(q)
            filtered = [it for it in filtered
                        if needle in _fold(it["name"])
                        or any(needle in _fold(t) for t in (it.get("tags") or []))]
            parts.append('“%s”' % q)
        if macro or nova is not None or grade:
            filtered = stats.filter_by_nutrition(filtered, macro=macro, nova=nova, grade=grade)
            if macro:
                parts.append("contains " + _MACRO_LABELS[macro].lower())
            elif nova is not None:
                parts.append(_NOVA_LABELS[nova] + " · NOVA " + str(nova))
            else:
                parts.append("Nutri-Score " + grade.upper())
        if year is not None:
            filtered = [it for it in filtered if _expires_in_year(it, year)]
            parts.append("expires " + str(year))
        if fluid:
            filtered = [it for it in filtered if it.get("is_fluid")]
            parts.append("counts as drinking fluid")
        if excluded:
            filtered = [it for it in filtered if it.get("exclude_stats")]
            parts.append("excluded from stats")
        if tag:
            ftag = _fold(tag)
            filtered = [it for it in filtered
                        if ftag in [_fold(t) for t in (it.get("tags") or [])]]
            parts.append("tagged “%s”" % tag)

        return {
            "items": filtered,
            "active": bool(q or macro or nova is not None or grade
                           or year is not None or fluid or excluded or tag),
            "q": q, "macro": macro, "nova": nova, "grade": grade, "year": year,
            "fluid": fluid, "excluded": excluded, "tag": tag,
            "label": " · ".join(parts) if parts else None,
            "clear_url": url_for("pantry"),
        }

    @app.route("/pantry")
    def pantry():
        conn = get_db()
        warn_days = int(settings.get(conn, "expiry_warn_days"))
        items = models.list_items(conn)
        # tags are product-level; resolve them once for the whole list, then hang the
        # names off each item for the chips + the tag filter.
        tag_map = tags.tags_for_products(
            conn, [models.product_key(it["barcode"], it["name"]) for it in items]
        )
        for it in items:
            dl = days_left(it["expire_date"])
            it["days_left"] = dl
            it["expired"] = dl is not None and dl < 0
            it["expiring_soon"] = dl is not None and 0 <= dl <= warn_days
            it["each_weight"] = format_weight(it["weight_value"], it["weight_unit"])
            it["total_weight"] = format_weight(it["weight_value"] * it["units"], it["weight_unit"])
            it["tags"] = tag_map.get(models.product_key(it["barcode"], it["name"]), [])

        # search box + filters (name query, water/year pills, chart drill-ins, tags)
        filter_years = _expiry_years(items)
        search = _pantry_filter(items)
        items = search["items"]

        groups = models.group_by_shelf(items, shelves.list_shelves(conn))
        page_size = _int_setting(conn, "shelf_page_size")
        groups = models.paginate_groups(groups, page_size, request.args)
        # build prev/next links that only bump this shelf's page, leaving the rest.
        base_args = request.args.to_dict()
        for grp in groups:
            if grp["has_prev"]:
                grp["prev_url"] = url_for("pantry", **{**base_args, grp["param"]: grp["page"] - 1})
            if grp["has_next"]:
                grp["next_url"] = url_for("pantry", **{**base_args, grp["param"]: grp["page"] + 1})
        return render_template(
            "pantry.html", groups=groups, warn_days=warn_days,
            shelves=shelves.list_shelves(conn), search=search,
            filter_years=filter_years, filter_tags=tags.list_tags(conn),
        )

    def _prepared_form():
        """Raw form data for a save.

        The product image is NOT fetched here — that download is slow (the OFF image
        server can take many seconds) and would stall Save. We write the row first
        and pull the photo in the background afterwards (see _maybe_fetch_photo).
        """
        return request.form.to_dict()

    def _maybe_fetch_photo(item_id):
        """Kick off the product-image download for a just-saved item, if it had a
        lookup image. Runs in a background thread so Save returns immediately;
        synchronous under tests so assertions stay deterministic."""
        url = (request.form.get("image_url") or "").strip()
        if not url:
            return
        barcode = request.form.get("barcode")
        if app.testing:
            _download_and_attach(item_id, url, barcode)
        else:
            threading.Thread(
                target=_download_and_attach, args=(item_id, url, barcode), daemon=True
            ).start()

    def _render_item_form(item, action, status=200, item_tags=None):
        conn = get_db()
        return render_template(
            "item_form.html", item=item, units=database.WEIGHT_UNITS,
            shelves=shelves.list_shelves(conn), action=action,
            item_tags=item_tags or [], all_tags=tags.all_names(conn),
        ), status

    def _save_item_tags(conn, old_key=None):
        """Assign the tags typed on the item form to the just-saved product identity,
        carrying any existing tags over if an edit changed the barcode/name (which moves
        the key). Tags are product-level, so this covers every batch of the product."""
        key = models.product_key(request.form.get("barcode"), request.form.get("name"))
        if old_key and old_key != key:
            tags.move_product_tags(conn, old_key, key)
        tags.set_product_tags(conn, key, request.form.getlist("tags"))
        backup.write_csv_mirror(conn)   # keep the CSV's tags column current post-save

    @app.route("/items/new")
    def new_item():
        return _render_item_form(None, url_for("create_item"))

    @app.route("/items", methods=["POST"])
    def create_item():
        conn = get_db()
        try:
            new_id = models.create_item(conn, _prepared_form())
        except models.ValidationError as err:
            flash(str(err), "error")
            return _render_item_form(request.form, url_for("create_item"), 400,
                                     item_tags=request.form.getlist("tags"))
        _save_item_tags(conn)
        _maybe_fetch_photo(new_id)   # photo lands in the background, Save doesn't wait
        flash("Item added — scan the next one.", "ok")
        # loop straight back to a fresh add form so a scanning session keeps flowing,
        # rather than bouncing out to the pantry list after every item.
        return redirect(url_for("new_item"))

    @app.route("/items/<int:item_id>/edit")
    def edit_item(item_id):
        conn = get_db()
        item = models.get_item(conn, item_id)
        if item is None:
            return render_template("404.html"), 404
        current = tags.tags_for_product(conn, models.product_key(item["barcode"], item["name"]))
        return _render_item_form(item, url_for("update_item", item_id=item_id),
                                 item_tags=current)

    @app.route("/items/<int:item_id>", methods=["POST"])
    def update_item(item_id):
        conn = get_db()
        existing = models.get_item(conn, item_id)
        old_key = models.product_key(existing["barcode"], existing["name"]) if existing else None
        try:
            updated = models.update_item(conn, item_id, _prepared_form())
        except models.ValidationError as err:
            flash(str(err), "error")
            form = dict(request.form)
            form["id"] = item_id
            return _render_item_form(form, url_for("update_item", item_id=item_id), 400,
                                     item_tags=request.form.getlist("tags"))
        if not updated:
            return render_template("404.html"), 404
        _save_item_tags(conn, old_key)
        _maybe_fetch_photo(item_id)   # same background photo pull on edit-with-lookup
        flash("Item updated.", "ok")
        return redirect(url_for("pantry"))

    @app.route("/items/<int:item_id>/delete", methods=["POST"])
    def delete_item(item_id):
        conn = get_db()
        models.delete_item(conn, item_id)
        flash("Item removed.", "ok")
        return redirect(url_for("pantry"))

    def _bar_result(message, category="ok"):
        """A bulk-bar action's response. The fetch-driven UI (X-Requested-With: fetch)
        gets JSON and re-renders the list in place — no reload. A no-JS submit still
        gets the classic flash + redirect back to the same pantry view."""
        if request.headers.get("X-Requested-With") == "fetch":
            return jsonify({"ok": category == "ok", "message": message})
        flash(message, category)
        ref = request.referrer
        if ref and urlparse(ref).path == url_for("pantry"):
            return redirect(ref)   # keep filters/page on the no-JS fallback
        return redirect(url_for("pantry"))

    @app.route("/items/move", methods=["POST"])
    def move_items():
        conn = get_db()
        ids = request.form.getlist("item_ids")
        if not ids:
            return _bar_result("Nothing selected to move.", "error")
        try:
            moved = models.move_items(conn, ids, request.form.get("shelf_id"))
        except models.ValidationError as err:
            return _bar_result(str(err), "error")
        return _bar_result(f"Moved {moved} item{'' if moved == 1 else 's'}.")

    @app.route("/items/delete", methods=["POST"])
    def delete_items():
        conn = get_db()
        ids = request.form.getlist("item_ids")
        if not ids:
            return _bar_result("Nothing selected to delete.", "error")
        removed = models.delete_items(conn, ids)
        return _bar_result(f"Removed {removed} item{'' if removed == 1 else 's'}.")

    @app.route("/items/tag", methods=["POST"])
    def tag_items():
        """Bulk-tag the selected products (adds to their existing tags). Tags are
        product-level, so selecting several batches of one product tags it once. Tag
        names may arrive comma-separated in the move-bar field."""
        conn = get_db()
        ids = request.form.getlist("item_ids")
        if not ids:
            return _bar_result("Nothing selected to tag.", "error")
        raw = []
        for val in request.form.getlist("tags"):
            raw.extend(val.split(","))
        names = tags.clean_names(raw)
        if not names:
            return _bar_result("Type a tag to apply to the selection.", "error")
        # resolve the selected rows to their distinct product identities
        keys = set()
        for raw_id in ids:
            try:
                item = models.get_item(conn, int(raw_id))
            except (TypeError, ValueError):
                continue
            if item:
                keys.add(models.product_key(item["barcode"], item["name"]))
        if not keys:
            return _bar_result("Those items are gone — nothing to tag.", "error")
        tags.add_tags_to_keys(conn, keys, names)
        backup.write_csv_mirror(conn)   # tags feed the CSV export, keep it current
        return _bar_result("Tagged %d product%s with %s." % (
            len(keys), "" if len(keys) == 1 else "s", ", ".join(names)))

    def _decorate(it):
        """Hang the display-only fields (freshness, formatted weights) off an item
        the same way the pantry list does, so the adjust screens read consistently."""
        warn = int(settings.get(get_db(), "expiry_warn_days"))
        dl = days_left(it["expire_date"])
        it["days_left"] = dl
        it["expired"] = dl is not None and dl < 0
        it["expiring_soon"] = dl is not None and 0 <= dl <= warn
        it["each_weight"] = format_weight(it["weight_value"], it["weight_unit"])
        it["total_weight"] = format_weight(it["weight_value"] * it["units"], it["weight_unit"])
        return it

    @app.route("/api/pantry/<barcode>")
    def pantry_lookup(barcode):
        """Is this barcode already on our shelves? The scanner calls this the moment
        it reads a code, to decide between the quick +/- screen and the full form."""
        conn = get_db()
        matches = models.items_by_barcode(conn, barcode)
        if not matches:
            return jsonify({"found": False})
        return jsonify({"found": True, "url": url_for("adjust_resolve", barcode=barcode)})

    @app.route("/items/adjust")
    def adjust_resolve():
        """A scanned barcode lands here: send it to the right quick-adjust screen —
        or fall back to the add form when it's genuinely new."""
        conn = get_db()
        barcode = request.args.get("barcode", "")
        matches = models.items_by_barcode(conn, barcode)
        if not matches:
            return redirect(url_for("new_item", barcode=barcode))
        if len(matches) == 1:
            return redirect(url_for("adjust_item", item_id=matches[0]["id"]))
        return render_template(
            "adjust_pick.html", barcode=barcode,
            items=[_decorate(it) for it in matches],
        )

    @app.route("/items/<int:item_id>/adjust")
    def adjust_item(item_id):
        conn = get_db()
        item = models.get_item(conn, item_id)
        if item is None:
            return render_template("404.html"), 404
        return render_template("adjust.html", item=_decorate(item))

    @app.route("/items/<int:item_id>/units", methods=["POST"])
    def change_units(item_id):
        conn = get_db()
        # the - button sends an explicit delta; the "+" menu sends how many to add.
        if request.form.get("delta") is not None:
            try:
                amount = int(request.form.get("delta"))
            except (TypeError, ValueError):
                amount = 0
        else:
            try:
                amount = abs(int(request.form.get("units", 1)))
            except (TypeError, ValueError):
                amount = 1
        result = models.adjust_units(conn, item_id, amount)
        if result is None:
            return render_template("404.html"), 404
        if result == 0:
            flash("Used the last one — removed from the pantry.", "ok")
            return redirect(url_for("pantry"))
        return redirect(url_for("adjust_item", item_id=item_id))

    @app.route("/items/<int:item_id>/restock", methods=["POST"])
    def restock_item(item_id):
        conn = get_db()
        try:
            new_id = models.restock(
                conn, item_id, request.form.get("expire_date"), request.form.get("units")
            )
        except models.ValidationError as err:
            flash(str(err), "error")
            return redirect(url_for("adjust_item", item_id=item_id))
        flash("Added a fresh batch.", "ok")
        return redirect(url_for("adjust_item", item_id=new_id))

    @app.route("/api/products/<barcode>")
    def product_lookup(barcode):
        """Barcode -> Open Food Facts, proxied so the browser dodges CORS and we
        get to set the User-Agent OFF wants. The form's JS calls this."""
        conn = get_db()
        product = offfacts.lookup(barcode, settings.get(conn, "off_country"))
        if product is None:
            return jsonify({"found": False}), 404
        return jsonify({"found": True, "product": product})

    @app.route("/api/health/off")
    def off_health():
        """Diagnostic: can the server actually reach Open Food Facts, and if not,
        *why*? Returns the real exception + whether certifi is in play."""
        conn = get_db()
        return jsonify(offfacts.diagnose(settings.get(conn, "off_country")))

    @app.route("/media/<path:filename>")
    def media(filename):
        """Serve a downloaded product photo out of the data volume."""
        return send_from_directory(database.images_dir(), filename)

    # ---- shelves ----
    @app.route("/shelves")
    def shelves_page():
        conn = get_db()
        return render_template("shelves.html", shelves=shelves.list_shelves(conn))

    @app.route("/shelves", methods=["POST"])
    def create_shelf():
        conn = get_db()
        try:
            shelves.create_shelf(conn, request.form.get("name"))
        except shelves.ShelfError as err:
            flash(str(err), "error")
            return redirect(url_for("shelves_page"))
        flash("Shelf added.", "ok")
        return redirect(url_for("shelves_page"))

    @app.route("/shelves/<int:shelf_id>", methods=["POST"])
    def rename_shelf(shelf_id):
        conn = get_db()
        try:
            renamed = shelves.rename_shelf(conn, shelf_id, request.form.get("name"))
        except shelves.ShelfError as err:
            flash(str(err), "error")
            return redirect(url_for("shelves_page"))
        flash("Shelf renamed." if renamed else "That shelf is gone.", "ok")
        return redirect(url_for("shelves_page"))

    @app.route("/shelves/<int:shelf_id>/delete", methods=["POST"])
    def delete_shelf(shelf_id):
        conn = get_db()
        shelves.delete_shelf(conn, shelf_id)
        flash("Shelf removed. Its items are now unshelved.", "ok")
        return redirect(url_for("shelves_page"))

    # ---- tags ----
    # New tags are born on the item form (free-form), so this page is management only:
    # rename (which merges into an existing name) and delete.
    @app.route("/tags")
    def tags_page():
        conn = get_db()
        return render_template("tags.html", tags=tags.list_tags(conn))

    @app.route("/tags/<int:tag_id>", methods=["POST"])
    def rename_tag(tag_id):
        conn = get_db()
        try:
            renamed = tags.rename_tag(conn, tag_id, request.form.get("name"))
        except tags.TagError as err:
            flash(str(err), "error")
            return redirect(url_for("tags_page"))
        flash("Tag renamed." if renamed else "That tag is gone.", "ok")
        return redirect(url_for("tags_page"))

    @app.route("/tags/<int:tag_id>/delete", methods=["POST"])
    def delete_tag(tag_id):
        conn = get_db()
        tags.delete_tag(conn, tag_id)
        flash("Tag removed from every product that had it.", "ok")
        return redirect(url_for("tags_page"))

    @app.route("/activity")
    def activity_page():
        """What's been happening: every stock change, newest first (0014)."""
        conn = get_db()
        keep = _int_setting(conn, "activity_keep_days")
        activity.prune(conn, keep)
        action = request.args.get("action")
        if action not in activity.ACTIONS:
            action = None
        rows, more = activity.recent(conn, action=action,
                                     before_id=request.args.get("before", type=int))
        # which of these are still on the shelf -> the name links to the edit form
        ids = {r["item_id"] for r in rows if r["item_id"]}
        live = set()
        if ids:
            marks = ",".join("?" for _ in ids)
            live = {r["id"] for r in conn.execute(
                f"SELECT id FROM pantry_items WHERE id IN ({marks})", list(ids))}
        return render_template(
            "activity.html", days=activity.group_by_day(rows), action=action,
            actions=activity.ACTIONS, more=more, keep_days=keep, live_ids=live,
            last_id=rows[-1]["id"] if rows else None,
        )

    @app.route("/settings")
    def settings_page():
        conn = get_db()
        return render_template(
            "settings.html", settings=settings.all_settings(conn),
            backups=backup.list_backups(), default_password=auth.is_default(conn),
        )

    @app.route("/settings/password", methods=["POST"])
    def change_password():
        conn = get_db()
        current = request.form.get("current_password") or ""
        new = request.form.get("new_password") or ""
        confirm = request.form.get("confirm_password") or ""
        if not auth.check_password(conn, current):
            flash("Current password is incorrect.", "error")
        elif len(new) < 4:
            flash("New password must be at least 4 characters.", "error")
        elif new != confirm:
            flash("New passwords don't match.", "error")
        else:
            auth.set_password(conn, new)
            flash("Password changed.", "ok")
        return redirect(url_for("settings_page"))

    @app.route("/settings", methods=["POST"])
    def save_settings():
        conn = get_db()
        editable = (
            "backup_keep", "expiry_warn_days", "shelf_page_size", "activity_keep_days",
            "off_country",
            "household_men", "household_women", "household_children",
            "meals_per_day",
            "cal_per_man", "cal_per_woman", "cal_per_child",
            "water_per_man", "water_per_woman", "water_per_child",
            "protein_per_adult", "fiber_per_adult", "calcium_per_adult",
            "iron_per_adult", "potassium_per_adult",
        )
        for key in editable:
            if key in request.form:
                settings.set(conn, key, request.form[key])
        # store meals per day the way we'll read it, so the box never shows a 0 or
        # some junk while the maths quietly uses 1
        if "meals_per_day" in request.form:
            settings.set(conn, "meals_per_day", str(_meals_per_day(conn)))
        # checkbox: absent in the form means "off", so set it explicitly
        settings.set(conn, "exclude_expired",
                     "yes" if request.form.get("exclude_expired") == "yes" else "no")
        flash("Settings saved.", "ok")
        return redirect(url_for("settings_page"))

    @app.route("/export.csv")
    def export_csv():
        conn = get_db()
        csv_data = backup.export_csv_string(conn)
        return Response(
            csv_data,
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=pantry_export.csv"},
        )

    @app.route("/backup", methods=["POST"])
    def backup_now():
        conn = get_db()
        keep = int(settings.get(conn, "backup_keep"))
        path = backup.create_backup(keep=keep)
        flash("Backup created." if path else "Nothing to back up yet.", "ok")
        return redirect(url_for("settings_page"))

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
