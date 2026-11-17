"""API client tests, run against real API responses saved in tests/fixtures/api/ (secrets redacted)."""
import io
import json
import urllib.error
from pathlib import Path

import pytest

from bot.api_client import ApiClient, ApiConfig, ApiError, strip_html, to_number, to_price

FIX = Path(__file__).parent / "fixtures" / "api"
UUID = "56695974416925887669406417382718"


def fixture(name: str) -> bytes:
    return (FIX / f"{name}.json").read_bytes()


class FakeResponse:
    def __init__(self, body: bytes, status: int = 200):
        self._body, self.status = body, status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeTransport:
    """Plays back queued responses (bytes, or an exception to raise) and records requests."""

    def __init__(self, *queue):
        self.queue = list(queue)
        self.requests = []

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return FakeResponse(item)


def http_error(code: int, body: bytes = b"{}"):
    return urllib.error.HTTPError("http://x", code, "err", {}, io.BytesIO(body))


def make(*queue, **cfg):
    transport = FakeTransport(*queue)
    config = ApiConfig(base_url="http://127.0.0.1:8001", store_uuid=UUID, **cfg)
    return ApiClient(config, opener=transport, sleep=lambda s: None), transport


# ---------- transport ----------

def test_sends_store_uuid_header_and_no_key_by_default():
    client, t = make(fixture("categories_tree"))
    client.categories()
    req = t.requests[0]
    assert req.get_header("Store-uuid") == UUID
    assert req.full_url == "http://127.0.0.1:8001/categories/tree"
    assert len(req.headers) == 2  # Store-UUID and Accept only


def test_key_sent_only_when_value_and_header_name_both_set():
    client, t = make(fixture("categories_tree"), api_key="k", api_key_header=None)
    client.categories()
    assert "K" not in t.requests[0].headers
    client, t = make(fixture("categories_tree"), api_key="secret-key", api_key_header="X-Bot-Key")
    client.categories()
    assert t.requests[0].get_header("X-bot-key") == "secret-key"


def test_retries_on_server_error_then_succeeds():
    client, t = make(http_error(503), fixture("categories_tree"))
    assert client.categories()
    assert len(t.requests) == 2


def test_retries_on_timeout_and_gives_up():
    client, t = make(TimeoutError(), TimeoutError(), TimeoutError(), retries=2)
    with pytest.raises(ApiError) as e:
        client.categories()
    assert e.value.retryable and len(t.requests) == 3


def test_client_errors_are_not_retried():
    client, t = make(http_error(404, b'{"success":false,"message":"No query results","response_code":404}'))
    with pytest.raises(ApiError) as e:
        client.product_detail("nope")
    assert e.value.status == 404 and "No query results" in str(e.value) and len(t.requests) == 1


def test_real_401_body_from_search_endpoint_is_an_api_error():
    body = b'{"message":"Authorization token is missing or invalid.","success":false,"code":401}'
    client, t = make(http_error(401, body))
    with pytest.raises(ApiError) as e:
        client.product_detail("x")
    assert e.value.status == 401 and not e.value.retryable


def test_success_false_on_http_200_is_an_error():
    body = b'{"success":false,"message":"Missing required header: Store-UUID","response_code":404,"data":[]}'
    client, _ = make(body)
    with pytest.raises(ApiError) as e:
        client.product_detail("x")
    assert e.value.status == 404


def test_non_json_is_an_error():
    client, _ = make(b"<html>oops</html>")
    with pytest.raises(ApiError):
        client.categories()


# ---------- stores ----------

def test_store_info_returns_only_allowed_fields():
    client, _ = make(fixture("stores"))
    info = client.store_info()
    assert set(info) == {"name", "phone", "email", "address", "address2", "city", "state", "zipcode", "timezone", "opening_hours"}
    assert info["phone"] == "(731) 423-6565"
    assert info["timezone"] == "America/New_York"
    assert info["opening_hours"]["sunday"] == {"open": "11:00", "close": "18:00", "is_open": True}
    assert len(info["opening_hours"]) == 7
    assert "secret" not in json.dumps(info).lower()


def test_store_pages_lists_slugs_and_titles():
    client, _ = make(fixture("stores"))
    pages = client.store_pages()
    assert {"slug": "contact-us", "title": "Contact Us", "status": 1} in pages
    assert len(pages) == 18
    shipping = next(p for p in pages if p["slug"] == "shipping-delivery")
    assert shipping["status"] == 0  # real data: kept and flagged, not silently dropped


# ---------- brands ----------

def test_brands_flattened_from_vendors():
    client, t = make(fixture("brands"))
    brands = client.brands()
    assert t.requests[0].full_url == "http://127.0.0.1:8001/brands"
    assert [b["slug"] for b in brands] == ["coaster", "foa", "foa-exclusive", "acme", "crownmark", "emerald-home", "mda-rugs", "happy-home"]
    foa = next(b for b in brands if b["slug"] == "foa")
    assert foa == {"slug": "foa", "name": "Furniture Of America", "vendor": "FOA", "status": 1}


def test_brands_duplicates_are_removed():
    body = json.dumps({"success": True, "data": {"vendors": [
        {"name": "V1", "brands": [{"slug": "a", "status": 1, "brand_languages": [{"name": "A"}]}]},
        {"name": "V2", "brands": [{"slug": "a", "status": 1, "brand_languages": [{"name": "A"}]}]}]}}).encode()
    client, _ = make(body)
    assert [b["slug"] for b in client.brands()] == ["a"]


# ---------- categories ----------

def test_category_tree_bare_list_is_handled():
    client, _ = make(fixture("categories_tree"))
    tree = client.categories()
    assert len(tree) == 8
    dining = next(c for c in tree if c["slug"] == "dining-room")
    assert dining["name"] == "Dining Room"
    assert any(ch["slug"] == "dining-room-dining-furniture" for ch in dining["children"])
    assert set(dining) == {"category_id", "slug", "name", "children"}


def test_category_products_listing():
    client, t = make(fixture("category_products"), cdn_folder="south-jackson")
    res = client.category_products("dining-room-dining-furniture", page=2)
    req = t.requests[0]
    assert req.get_method() == "POST"
    assert req.full_url.endswith("/categories/dining-room-dining-furniture/products?page=2")
    assert (res["per_page"], res["total"]) == (16, 1283)
    assert len(res["products"]) == 16
    first = res["products"][0]
    assert first["slug"] == "blanche-side-chair-brown-2261brn-s"
    assert first["price"] == 439.99 and isinstance(first["price"], float)
    assert first["brand"] == "Crownmark"
    assert first["image_url"] == "https://cdn.b3c2.com/south-jackson/images/p/2261BRN.webp" or first["image_url"].startswith(
        "https://cdn.b3c2.com/south-jackson/")
    assert "base_price" not in first
    assert res["breadcrumbs"][0]["slug"] == "dining-room"


# ---------- search ----------

def test_search_products_real_response():
    client, t = make(fixture("product_search"), cdn_folder="south-jackson")
    res = client.search_products("bed")
    req = t.requests[0]
    assert req.get_method() == "POST" and req.full_url == "http://127.0.0.1:8001/products/search"
    assert json.loads(req.data) == {"q": "bed"}
    assert (res["page"], res["per_page"], res["total"], res["last_page"]) == (1, 16, 4976, 311)
    assert len(res["products"]) == 16
    first = res["products"][0]
    assert first["name"] == "I-105XLW - Twin/Full/Queen" and first["price"] == 59.99 and first["brand"] == "Happy Home"
    assert res["filters"]["price_range"] == {"min": 39.99, "max": 10339.99}
    assert len(res["filters"]["categories"]) == 82
    assert res["filters"]["attributes"][0]["group"] == "Bed Size"
    assert "Queen" in res["filters"]["attributes"][0]["values"]


def test_search_never_returns_the_minus_one_cent_price():
    client, _ = make(fixture("product_search"))
    prices = [p["price"] for p in client.search_products("bed")["products"]]
    assert prices.count(None) == 7  # the seven -0.01 products in the real response
    assert all(p is None or p > 0 for p in prices)


def test_search_page_two_uses_page_param():
    client, t = make(fixture("product_search"))
    client.search_products("bed", page=2)
    assert t.requests[0].full_url.endswith("/products/search?page=2")
    assert json.loads(t.requests[0].data) == {"q": "bed"}


# ---------- product detail ----------

def test_product_detail_normalized():
    client, _ = make(fixture("product_detail"), cdn_folder="south-jackson")
    d = client.product_detail("blanche-side-chair-brown-2261brn-s")
    assert d["name"] == "Blanche Side Chair Brown"
    assert d["price"] == 439.99
    assert d["brand"] == "Crownmark"
    assert "base_price" not in d and 200.0 not in d.values()
    assert d["description"].startswith("Bring the coordinated style")  # raw text; the tool cleans HTML
    assert d["main_image"].endswith("/2261BRN.webp") and d["is_variant"] is False and d["pieces"] == 1
    assert d["dimensions"] == {"height": 0.0, "width": 0.0, "length": None, "depth": 0.0, "weight": None}
    assert [c["slug"] for c in d["categories"]] == ["dining-furniture-dining-room-chairs", "dining-room"]  # de-duplicated
    assert d["images"] and d["images"][0].endswith("/2261BRN.webp")
    assert d["attributes"] == [] and d["colors"] == [] and d["sizes"] == [] and d["variations"] == []


def test_image_url_is_none_without_cdn_folder():
    client, _ = make(fixture("product_detail"))
    d = client.product_detail("blanche-side-chair-brown-2261brn-s")
    assert d["images"] == []  # placeholder not replaced, so no broken link


# ---------- helpers ----------

def test_to_number():
    assert to_number("439.99") == 439.99
    assert to_number(439.99) == 439.99
    assert to_number(None) is None and to_number("") is None and to_number("abc") is None


def test_to_price_rejects_non_positive():
    assert to_price("439.99") == 439.99
    assert to_price(-0.01) is None and to_price(0) is None and to_price("0.00") is None and to_price(None) is None


def test_strip_html():
    assert strip_html("<p>We accept&nbsp;cards.<br></p>") == "We accept cards."
    assert strip_html(None) == ""
