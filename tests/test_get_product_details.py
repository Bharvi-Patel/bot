import json
import urllib.parse
from pathlib import Path

import pytest

from bot.api_client import ApiClient, ApiConfig, ApiError
from bot.text import clean_text
from bot.tools.get_product_details import _category_paths, get_product_details


# ---------- text cleaning ----------
def test_clean_text_strips_html_and_entities():
    raw = "<p>Soft &amp; <b>deep</b> seat</p><ul><li>Reversible</li><li>Pet friendly</li></ul>"
    out = clean_text(raw, 500)
    assert "<" not in out and "&amp;" not in out
    assert "Soft & deep seat" in out
    assert "- Reversible" in out and "- Pet friendly" in out


def test_clean_text_drops_scripts_and_collapses_space():
    assert clean_text("a<script>alert(1)</script>   b", 50) == "a b"


def test_clean_text_truncates_on_word_boundary():
    out = clean_text("word " * 100, 50)
    assert len(out) <= 53 and out.endswith("...")


@pytest.mark.parametrize("raw", [None, "", "   ", "<p></p>"])
def test_clean_text_empty_is_none(raw):
    assert clean_text(raw, 100) is None


# ---------- category paths ----------
TREE = [
    {"category_id": 1, "parent_id": 0, "slug": "living", "title": "Living Room"},
    {"category_id": 2, "parent_id": 1, "slug": "sofas", "title": "Sofas"},
    {"category_id": 3, "parent_id": 2, "slug": "sect", "title": "Sectionals"},
    {"category_id": 4, "parent_id": 0, "slug": "bed", "title": "Bedroom"},
]


def test_paths_keep_most_specific_and_drop_prefixes():
    paths = _category_paths(TREE, {"living", "sofas", "sect", "bed"})
    assert "Living Room > Sofas > Sectionals" in paths
    assert "Living Room > Sofas" not in paths and "Living Room" not in paths
    assert "Bedroom" in paths


def test_paths_survive_a_parent_loop():
    loop = [
        {"category_id": 1, "parent_id": 2, "slug": "a", "title": "A"},
        {"category_id": 2, "parent_id": 1, "slug": "b", "title": "B"},
    ]
    assert _category_paths(loop, {"a"})  # no infinite loop


# ---------- the tool, against real API responses ----------
FIX = Path(__file__).parent / "fixtures" / "api"
DETAIL_SLUG = "blanche-side-chair-brown-2261brn-s"


class Resp:
    status = 200

    def __init__(self, body):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def api(detail_fixture="product_detail", cdn_folder="south-jackson", tree=True, search_fixture="product_search"):
    """A client whose transport serves saved real responses by URL. Returns (client, requested_urls)."""
    urls = []

    def opener(req, timeout=None):
        urls.append(req.full_url)
        path = urllib.parse.urlparse(req.full_url).path
        if path.startswith("/product/"):
            return Resp((FIX / f"{detail_fixture}.json").read_bytes())
        if path == "/products/search":
            return Resp((FIX / f"{search_fixture}.json").read_bytes())
        if path == "/categories/tree":
            if not tree:
                raise ApiError("down")
            return Resp((FIX / "categories_tree.json").read_bytes())
        raise AssertionError("unexpected request: " + req.full_url)

    return ApiClient(ApiConfig("http://x", "uuid", cdn_folder=cdn_folder, retries=0), opener=opener), urls


def test_found_by_slug_real_response():
    client, urls = api()
    out = get_product_details({"slug": DETAIL_SLUG}, client=client)
    assert out["found"] is True
    p = out["product"]
    assert (p["sku"], p["name"], p["brand"], p["price"]) == ("2261BRN-S", "Blanche Side Chair Brown", "Crownmark", 439.99)
    assert p["summary"] == "Blanche Side Chair Brown" and p["description"].startswith("Bring the coordinated style")
    assert p["is_variant"] is False and p["pieces"] == 1
    assert p["main_image"] == "https://cdn.b3c2.com/crownmark/images/product/medium/2261BRN.webp"     # the vendor's folder, as the storefront uses
    assert out["images"] == [{"image_url": "https://cdn.b3c2.com/crownmark/images/product/large/2261BRN.webp"}]   # the big size for picture links
    assert out["categories"] == ["Dining Room > Dining Furniture > Dining Room Chairs"]
    assert urls[0] == f"http://x/product/{DETAIL_SLUG}"
    assert "base_price" not in json.dumps(out) and "200" not in json.dumps(out["product"]["price"])


def test_note_says_options_are_unknown():
    out = get_product_details({"slug": DETAIL_SLUG}, client=api()[0])
    assert "Do not say whether other colors" in out["note"]
    assert "options" not in out and "attributes" not in out


def test_all_zero_dimensions_say_so():
    p = get_product_details({"slug": DETAIL_SLUG}, client=api()[0])["product"]
    assert p["dimensions"] is None and "do not estimate" in p["dimension_note"]


def test_without_bot_cdn_folder_the_vendor_folder_still_gives_image_links():
    out = get_product_details({"slug": DETAIL_SLUG}, client=api(cdn_folder=None)[0])
    assert out["product"]["main_image"] == "https://cdn.b3c2.com/crownmark/images/product/medium/2261BRN.webp"


def test_category_tree_down_falls_back_to_titles():
    out = get_product_details({"slug": DETAIL_SLUG}, client=api(tree=False)[0])
    assert out["found"] and sorted(out["categories"]) == ["Dining Room", "Dining Room Chairs"]


def test_sku_is_resolved_through_search_then_detail():
    client, urls = api()
    sku = "I-105XLW - Twin/Full/Queen RC 70327"  # a real SKU from the saved search response
    out = get_product_details({"sku": sku.lower(), "slug": "ignored"}, client=client)
    assert out["found"] is True
    assert urls[0] == "http://x/products/search"
    assert urls[1] == "http://x/product/i-105xlw-twin-full-queen-i-105xlw-twin-full-queen-rc-70327"


def test_sku_not_in_search_results_is_not_found():
    out = get_product_details({"sku": "NOPE-123"}, client=api()[0])
    assert out["found"] is False and "search_products" in out["note"]


def test_missing_product_404_is_not_found():
    def opener(req, timeout=None):
        import io
        import urllib.error
        raise urllib.error.HTTPError(req.full_url, 404, "nf", {}, io.BytesIO(b'{"success":false,"message":"No query results","response_code":404}'))
    client = ApiClient(ApiConfig("http://x", "uuid", retries=0), opener=opener)
    out = get_product_details({"slug": "nope"}, client=client)
    assert out["found"] is False and "search_products" in out["note"]


def test_api_down_gives_safe_error():
    def opener(req, timeout=None):
        raise TimeoutError()
    client = ApiClient(ApiConfig("http://x", "uuid", retries=0), opener=opener)
    assert "Do not guess" in get_product_details({"slug": "x"}, client=client)["error"]


def test_a_non_positive_price_is_not_quoted():
    class C:
        def product_detail(self, slug):
            return {"slug": "s", "sku": "S", "name": "Mattress", "brand": None, "price": None, "short_description": None,
                    "description": None, "special_features": None, "main_image": None, "images": [], "categories": [],
                    "dimensions": {}, "pieces": None, "lifestyle": None, "is_variant": False}

        def categories(self):
            return []
    out = get_product_details({"slug": "s"}, client=C())
    assert out["product"]["price"] is None and "Do not state or guess a price" in out["note"]


@pytest.mark.parametrize("bad", [{}, {"sku": ""}, {"sku": 5}, {"sku": "x" * 500}, {"upc": "123"}])
def test_bad_input_returns_error_not_exception(bad):
    assert "error" in get_product_details(bad, client=api()[0])


def test_unsafe_slug_is_quoted_in_the_path():
    client, urls = api()
    get_product_details({"slug": "a/b?c"}, client=client)
    assert urls[0] == "http://x/product/a%2Fb%3Fc"


def test_option_priced_product_lists_each_size_with_its_price():
    out = get_product_details({"slug": "royal"}, client=api("product_detail_variable")[0])
    p = out["product"]
    assert p["price"] is None and out["price_range"] == {"min": 339.99, "max": 659.99} and out["option_count"] == 4
    assert [(o["attributes"]["Bed Size"], o["price"]) for o in out["options"]] == [("Twin", 339.99), ("Full", 439.99), ("Queen", 479.99), ("King", 659.99)]
    assert "depends on the option" in out["note"] and "Never give a price for an option that is not listed" in out["note"]
    assert "No price is available" not in out["note"]
    assert "base_price" not in json.dumps(out) and "169.0" not in json.dumps(out)


def test_sku_search_finds_the_exact_sku_even_with_other_matches_listed():
    client, urls = api(search_fixture="sku_search")
    out = get_product_details({"sku": "2261brn-s"}, client=client)
    assert out["found"] is True and urls[1] == "http://x/product/blanche-side-chair-brown-2261brn-s"


def test_without_categories_the_tree_is_not_requested_and_titles_are_used():
    client, urls = api()
    out = get_product_details({"slug": DETAIL_SLUG}, client=client, with_categories=False)
    assert out["found"] and sorted(out["categories"]) == ["Dining Room", "Dining Room Chairs"]
    assert urls == [f"http://x/product/{DETAIL_SLUG}"]        # one API call, no /categories/tree
