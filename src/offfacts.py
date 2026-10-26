"""Open Food Facts lookups — turn a barcode into something the form can use.

OFF is crowd-sourced, so anything here is a *best effort*: fields go missing, the
network flakes, quantities are written a hundred ways. Every function fails soft —
a miss just means "type it in yourself", never a crash. All the actual network
lives in the two `_fetch_*` helpers so tests can monkeypatch them and never touch
the real API.

Licence note: OFF data is ODbL — attribution shown on the form where it surfaces.
"""
import json
import os
import re
import ssl
import urllib.parse
import urllib.request

# macOS' framework Python (and slim containers) often can't verify TLS with the
# stock urllib because they don't ship / point at a CA store — curl works, urllib
# doesn't. Verifying against certifi's bundle sidesteps that. Guarded so a missing
# certifi doesn't take the whole app down; we just fall back to the default context.
try:
    import certifi
    _CAFILE = certifi.where()
    _SSL_CONTEXT = ssl.create_default_context(cafile=_CAFILE)
except Exception:
    _CAFILE = None
    _SSL_CONTEXT = ssl.create_default_context()

# OFF asks for a descriptive UA or they throttle you. Keep it honest.
USER_AGENT = "TheLastPantry/0.1 (self-hosted pantry manager)"

# only pull the fields we actually use — keeps the payload small and OFF happy.
_FIELDS = ",".join([
    "code", "product_name", "brands",
    "quantity", "product_quantity", "product_quantity_unit",
    "image_front_url", "nutriments", "nutriscore_grade", "nova_group",
])
# the product record is shared across every OFF subdomain — the country only steers
# language/localisation (e.g. bilingual FR/EN names for Canada). Configurable via the
# Settings UI; defaults to the global aggregate.
_HOST = "https://{country}.openfoodfacts.org"
_PATH = "/api/v2/product/{code}.json?fields=" + _FIELDS
DEFAULT_COUNTRY = "world"


def _api_url(code, country):
    country = (country or DEFAULT_COUNTRY).strip().lower() or DEFAULT_COUNTRY
    return _HOST.format(country=country) + _PATH.format(code=urllib.parse.quote(code))


_TIMEOUT = 6  # seconds — a phone add shouldn't hang on a slow lookup

# the nutriments worth keeping, label-order. OFF suffixes each base with _100g or
# _serving; we pull both. `factor` scales OFF's grams to the unit we display in —
# macros stay in g, micros (sodium/cholesterol/minerals) go to mg like a real label.
# The UI keeps its own label+unit list in sync with these keys.
_NUTRIMENTS = [
    # our key,        OFF base,        factor
    ("energy_kcal",   "energy-kcal",   1),
    ("fat",           "fat",           1),
    ("saturated_fat", "saturated-fat", 1),
    ("trans_fat",     "trans-fat",     1),
    ("cholesterol",   "cholesterol",   1000),
    ("sodium",        "sodium",        1000),
    ("carbohydrates", "carbohydrates", 1),
    ("fiber",         "fiber",         1),
    ("sugars",        "sugars",        1),
    ("added_sugars",  "added-sugars",  1),
    ("proteins",      "proteins",      1),
    ("salt",          "salt",          1),
    ("calcium",       "calcium",       1000),
    ("iron",          "iron",          1000),
    ("potassium",     "potassium",     1000),
]

# map OFF's loose unit spellings onto the four units we store.
_UNIT_ALIASES = {
    "g": "g", "gr": "g", "grams": "g", "gram": "g",
    "kg": "kg", "kgs": "kg",
    "ml": "ml", "milliliter": "ml", "millilitre": "ml",
    "l": "l", "lt": "l", "liter": "l", "litre": "l", "cl": "cl",
}


def lookup(barcode, country=DEFAULT_COUNTRY):
    """Fetch + normalise a product, or None if it's not there / network's down.

    Tries a couple of barcode spellings: North American UPC-A scans come back as 12
    digits, but OFF files those products as 13-digit EAN-13 with a leading zero (and
    vice-versa), so a naive single query misses them. `country` picks the OFF
    subdomain for localisation (see _HOST).
    """
    barcode = (barcode or "").strip()
    if not barcode:
        return None
    for code in _candidate_codes(barcode):
        raw = _fetch_json(_api_url(code, country))
        if raw and raw.get("status") == 1:
            return _normalise(raw.get("product") or {}, code)
    return None


def _candidate_codes(code):
    """The barcode as scanned, plus sensible UPC-A <-> EAN-13 variants to retry."""
    cands = [code]
    if code.isdigit():
        if len(code) == 12:               # UPC-A -> EAN-13 (pad a leading zero)
            cands.append("0" + code)
        elif len(code) == 13 and code[0] == "0":  # EAN-13 zero-padded -> UPC-A
            cands.append(code[1:])
    seen, out = set(), []
    for c in cands:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def check_reachable(country=DEFAULT_COUNTRY):
    """Cheap probe: can *this server* reach OFF at all? Used by the health route to
    tell 'product genuinely missing' apart from 'the box has no internet'. Nutella's
    barcode is about as guaranteed-present as it gets."""
    raw = _fetch_json(_api_url("3017620422003", country))
    return bool(raw and raw.get("status") == 1)


def diagnose(country=DEFAULT_COUNTRY):
    """Verbose reachability probe — surfaces the *actual* failure instead of hiding
    it behind a bare bool, so we can see a cert error vs DNS vs timeout at a glance.
    """
    url = _api_url("3017620422003", country)
    info = {
        "reachable": False,
        "url": url,
        "using_certifi": _CAFILE is not None,
        "cafile": _CAFILE,
        "error": None,
    }
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT, context=_SSL_CONTEXT) as resp:
            info["http_status"] = getattr(resp, "status", None)
            info["reachable"] = info["http_status"] == 200
    except Exception as e:
        info["error"] = repr(e)
    return info


def _normalise(product, barcode):
    name = (product.get("product_name") or "").strip()
    brands = (product.get("brands") or "").strip()
    value, unit = _net_weight(product)

    nutriments = product.get("nutriments") or {}
    # Nutrition is standardised PER 100 g/ml (the _100g fields) — serving sizes vary
    # wildly and are ignored. Calories stored on the item scale that per-100 energy to
    # the whole package (energy_100 × net weight / 100) for the pantry-duration maths.
    per_100g = _facts_for(nutriments, "_100g")
    calories = _package_calories(nutriments.get("energy-kcal_100g"), value, unit)

    # OFF also returns "unknown" / "not-applicable" here — only a–e are real grades,
    # anything else means "no score" so we don't render a broken badge.
    grade = (product.get("nutriscore_grade") or "").strip().lower()
    if grade not in ("a", "b", "c", "d", "e"):
        grade = None

    nutrition = {
        "source": "openfoodfacts",
        "barcode": barcode,
        "brands": brands or None,
        "nutriscore": grade,
        "nova_group": product.get("nova_group"),
        "per_100g": per_100g,
    }
    return {
        "barcode": barcode,
        "name": name,
        "brands": brands,
        "weight_value": value,
        "weight_unit": unit,
        "calories": calories,
        "image_url": (product.get("image_front_url") or "").strip() or None,
        "nutriscore": grade,
        "nutrition": nutrition,
    }


def _net_weight(product):
    """The package's net weight as (value, unit). Prefer OFF's numeric
    product_quantity/product_quantity_unit; fall back to parsing the free-text
    `quantity` string ('425 g') when those aren't set."""
    pq = product.get("product_quantity")
    if pq not in (None, ""):
        try:
            v = float(pq)
        except (TypeError, ValueError):
            v = None
        if v is not None:
            unit = _UNIT_ALIASES.get((product.get("product_quantity_unit") or "g").strip().lower())
            if unit == "cl":
                v, unit = v * 10, "ml"
            if unit in ("g", "kg", "ml", "l"):
                return v, unit
    return parse_quantity(product.get("quantity"))


def _base_grams(weight_value, weight_unit):
    """Net weight expressed in the per-100 basis unit (g or ml)."""
    if weight_value is None:
        return None
    return weight_value * 1000 if weight_unit in ("kg", "l") else weight_value


def _package_calories(energy_100, weight_value, weight_unit):
    """kcal for the whole package = per-100 energy × net weight / 100."""
    base = _base_grams(weight_value, weight_unit)
    if energy_100 in (None, "") or base is None:
        return None
    try:
        e = float(energy_100)
    except (TypeError, ValueError):
        return None
    return round(e * base / 100)


def _facts_for(nutriments, suffix):
    """Pull our subset of nutriments for one basis ('_100g' or '_serving'), scaled to
    the display unit and tidied."""
    out = {}
    for our_key, base, factor in _NUTRIMENTS:
        v = nutriments.get(base + suffix)
        if v in (None, ""):
            continue
        try:
            num = float(v) * factor
        except (TypeError, ValueError):
            continue
        out[our_key] = _tidy(num, energy=(our_key == "energy_kcal"))
    return out


def _tidy(value, energy=False):
    """Round OFF's long floats to something a human wants to read: energy to a whole
    kcal, everything else to one decimal, and drop a trailing .0 so 13.0 shows as 13."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return value
    v = round(v) if energy else round(v, 1)
    return int(v) if v == int(v) else v


_QTY_RE = re.compile(r"([0-9]+(?:[.,][0-9]+)?)\s*([a-zA-Z]+)")


def parse_quantity(text):
    """Pull a (value, unit) out of OFF's free-text quantity like '400 g'.

    Returns (float, one-of-our-units) or (None, None). We scan for *all* number+unit
    pairs and keep the last one with a unit we recognise — that handles messy real
    entries like '6 x 30 g' (-> 30 g) and ignores leading counts. cl folds into ml.
    """
    if not text:
        return None, None
    result = (None, None)
    for m in _QTY_RE.finditer(str(text)):
        unit = _UNIT_ALIASES.get(m.group(2).lower())
        if unit is None:
            continue
        try:
            value = float(m.group(1).replace(",", "."))
        except ValueError:
            continue
        if unit == "cl":  # 1 cl = 10 ml — normalise into ml
            value, unit = value * 10, "ml"
        result = (value, unit)
    return result


def download_image(url, barcode, dest_dir):
    """Save a product image next to the db, return the stored filename or None.

    Named off the barcode so re-scanning the same product overwrites rather than
    piling up. Any failure is swallowed — a missing photo is not worth a 500.
    """
    if not url:
        return None
    try:
        ext = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower()
        if ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
            ext = ".jpg"
        safe = re.sub(r"[^A-Za-z0-9_-]", "", barcode or "product") or "product"
        filename = safe + ext
        os.makedirs(dest_dir, exist_ok=True)
        data = _fetch_bytes(url)
        if not data:
            return None
        with open(os.path.join(dest_dir, filename), "wb") as fh:
            fh.write(data)
        return filename
    except OSError:
        return None


# --- the only two spots that touch the network (monkeypatched in tests) ---

def _fetch_json(url):
    body = _fetch_bytes(url)
    if not body:
        return None
    try:
        return json.loads(body)
    except (ValueError, TypeError):
        return None


def _fetch_bytes(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT, context=_SSL_CONTEXT) as resp:
            return resp.read()
    except Exception:
        # network down, timeout, 404, DNS, cert failure — caller treats None as "no data"
        return None
