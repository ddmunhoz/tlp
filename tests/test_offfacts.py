"""Open Food Facts client — normalising, quantity parsing, image download.

The network is always mocked: we monkeypatch the two _fetch_* helpers so nothing
here ever reaches the real API.
"""
import os

from src import offfacts

SAMPLE = {
    "status": 1,
    "product": {
        "code": "737628064502",
        "product_name": "Thai peanut noodle kit",
        "brands": "Simply Asia",
        "quantity": "155 g",
        "image_front_url": "https://images.openfoodfacts.org/x/front.jpg",
        "nutriments": {
            "energy-kcal_100g": 400,
            "fat_100g": 10.5,
            "proteins_100g": 12,
            "salt_100g": 1.2,
            "sodium_100g": "",   # blank should be dropped, not stored
        },
        "nutriscore_grade": "D",
        "nova_group": 4,
        "serving_size": "50 g",
    },
}


def test_lookup_normalises_a_product(monkeypatch):
    monkeypatch.setattr(offfacts, "_fetch_json", lambda url: SAMPLE)
    p = offfacts.lookup("737628064502")
    assert p["name"] == "Thai peanut noodle kit"
    assert p["weight_value"] == 155.0
    assert p["weight_unit"] == "g"
    assert p["image_url"].endswith("front.jpg")
    assert p["nutriscore"] == "d"
    facts = p["nutrition"]["per_100g"]
    assert facts["energy_kcal"] == 400
    assert facts["fat"] == 10.5
    assert "sodium" not in facts   # blank value was skipped
    assert p["nutrition"]["nova_group"] == 4


def test_lookup_rounds_ugly_floats(monkeypatch):
    payload = {"status": 1, "product": {
        "product_name": "Chili", "nutriscore_grade": "c",
        "nutriments": {
            "energy-kcal_100g": 101.176470588235,
            "fat_100g": 3.76470588235294,
            "proteins_100g": 6.0,          # whole number -> no trailing .0
            "salt_100g": 0.752941176470588,
        },
    }}
    monkeypatch.setattr(offfacts, "_fetch_json", lambda url: payload)
    f = offfacts.lookup("1")["nutrition"]["per_100g"]
    assert f["energy_kcal"] == 101      # energy rounds to a whole kcal
    assert f["fat"] == 3.8
    assert f["proteins"] == 6           # int, not 6.0
    assert f["salt"] == 0.8


def test_lookup_standardises_per_100g_scales_calories(monkeypatch):
    # a 425 g can whose serving (250 g) differs from the pack: facts stay per 100 g
    # (standard), while the stored calories scale to the whole package.
    payload = {"status": 1, "product": {
        "product_name": "Triple Bean Chili",
        "product_quantity": "425", "product_quantity_unit": "g",
        "serving_size": "1 portion (250 g)",
        "nutriments": {"energy-kcal_100g": 80, "fat_100g": 2},
    }}
    monkeypatch.setattr(offfacts, "_fetch_json", lambda url: payload)
    p = offfacts.lookup("1")
    assert p["weight_value"] == 425 and p["weight_unit"] == "g"   # from product_quantity
    assert p["nutrition"]["per_100g"]["energy_kcal"] == 80        # facts stay per 100 g
    assert p["nutrition"]["per_100g"]["fat"] == 2
    assert "per_item" not in p["nutrition"]
    assert p["calories"] == 340                                  # 80 * 425 / 100


def test_lookup_converts_micros_to_mg(monkeypatch):
    payload = {"status": 1, "product": {"product_name": "X", "nutriments": {
        "sodium_100g": 1.3, "cholesterol_100g": 0.02, "potassium_100g": 0.35,
        "trans-fat_100g": 0.5, "added-sugars_100g": 4,
    }}}
    monkeypatch.setattr(offfacts, "_fetch_json", lambda url: payload)
    f = offfacts.lookup("1")["nutrition"]["per_100g"]
    assert f["sodium"] == 1300         # g -> mg
    assert f["cholesterol"] == 20
    assert f["potassium"] == 350
    assert f["trans_fat"] == 0.5       # stays in g
    assert f["added_sugars"] == 4


def test_lookup_drops_non_grade_nutriscore(monkeypatch):
    payload = {"status": 1, "product": {
        "product_name": "Noodles", "nutriscore_grade": "unknown", "nutriments": {},
    }}
    monkeypatch.setattr(offfacts, "_fetch_json", lambda url: payload)
    p = offfacts.lookup("1")
    assert p["nutriscore"] is None
    assert p["nutrition"]["nutriscore"] is None


def test_lookup_computes_total_calories(monkeypatch):
    payload = {"status": 1, "product": {
        "product_name": "Chili", "quantity": "425 g",
        "nutriments": {"energy-kcal_100g": 101.18},
    }}
    monkeypatch.setattr(offfacts, "_fetch_json", lambda url: payload)
    assert offfacts.lookup("1")["calories"] == 430   # 101.18 * 4.25


def test_package_calories_by_unit():
    assert offfacts._package_calories(100, 425, "g") == 425
    assert offfacts._package_calories(50, 1, "kg") == 500     # 1 kg = 1000 g
    assert offfacts._package_calories(40, 500, "ml") == 200
    assert offfacts._package_calories(None, 100, "g") is None
    assert offfacts._package_calories(100, None, "g") is None


def test_net_weight_prefers_product_quantity():
    prod = {"product_quantity": "425", "product_quantity_unit": "g", "quantity": "nonsense"}
    assert offfacts._net_weight(prod) == (425.0, "g")
    # falls back to the free-text quantity string when product_quantity is absent
    assert offfacts._net_weight({"quantity": "500 ml"}) == (500.0, "ml")


def test_lookup_not_found_returns_none(monkeypatch):
    monkeypatch.setattr(offfacts, "_fetch_json", lambda url: {"status": 0})
    assert offfacts.lookup("000") is None


def test_lookup_pads_upc_a_to_ean13(monkeypatch):
    # OFF stores the 12-digit UPC-A under a 13-digit, zero-padded EAN-13. The bare
    # 12-digit query should miss and the padded retry should hit.
    def fake(url):
        return SAMPLE if "0737628064502" in url else {"status": 0}
    monkeypatch.setattr(offfacts, "_fetch_json", fake)
    p = offfacts.lookup("737628064502")   # 12 digits as scanned
    assert p is not None
    assert p["name"] == "Thai peanut noodle kit"


def test_candidate_codes_variants():
    assert offfacts._candidate_codes("737628064502") == ["737628064502", "0737628064502"]
    assert offfacts._candidate_codes("0737628064502") == ["0737628064502", "737628064502"]
    assert offfacts._candidate_codes("abc") == ["abc"]   # non-numeric, left alone


def test_check_reachable(monkeypatch):
    monkeypatch.setattr(offfacts, "_fetch_json", lambda url: {"status": 1})
    assert offfacts.check_reachable() is True
    monkeypatch.setattr(offfacts, "_fetch_json", lambda url: None)
    assert offfacts.check_reachable() is False


def test_lookup_network_error_returns_none(monkeypatch):
    monkeypatch.setattr(offfacts, "_fetch_json", lambda url: None)
    assert offfacts.lookup("123") is None


def test_lookup_blank_barcode_short_circuits(monkeypatch):
    # should never even try to fetch
    monkeypatch.setattr(offfacts, "_fetch_json", lambda url: (_ for _ in ()).throw(AssertionError))
    assert offfacts.lookup("  ") is None


def test_parse_quantity_variants():
    assert offfacts.parse_quantity("400 g") == (400.0, "g")
    assert offfacts.parse_quantity("1 l") == (1.0, "l")
    assert offfacts.parse_quantity("500ml") == (500.0, "ml")
    assert offfacts.parse_quantity("33 cl") == (330.0, "ml")   # cl folds into ml
    assert offfacts.parse_quantity("1,5 kg") == (1.5, "kg")    # comma decimal
    assert offfacts.parse_quantity("a dozen") == (None, None)
    assert offfacts.parse_quantity("") == (None, None)
    assert offfacts.parse_quantity("12 oz") == (None, None)    # unit we don't store
    assert offfacts.parse_quantity("6 x 30 g") == (30.0, "g")  # take the per-item size
    assert offfacts.parse_quantity("540 mL") == (540.0, "ml")  # capitalised unit


def test_download_image_writes_file(tmp_path, monkeypatch):
    monkeypatch.setattr(offfacts, "_fetch_bytes", lambda url: b"\x89PNGdata")
    name = offfacts.download_image("http://x/pic.png", "737", str(tmp_path))
    assert name == "737.png"
    assert (tmp_path / "737.png").read_bytes() == b"\x89PNGdata"


def test_download_image_no_url_is_none(tmp_path):
    assert offfacts.download_image("", "737", str(tmp_path)) is None


def test_download_image_fetch_failure_is_none(tmp_path, monkeypatch):
    monkeypatch.setattr(offfacts, "_fetch_bytes", lambda url: None)
    assert offfacts.download_image("http://x/pic.png", "737", str(tmp_path)) is None
