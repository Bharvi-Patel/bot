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
    assert foa == {"uuid": "24535729948324335664029000859186", "slug": "foa", "name": "Furniture Of America",
                   "vendor": "FOA", "status": 1}


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
    for unseen in ("attributes", "colors", "sizes", "variations", "promotions", "base_price"):
        assert unseen not in d


def test_image_url_is_none_without_cdn_folder():
    client, _ = make(fixture("product_detail"))
    d = client.product_detail("blanche-side-chair-brown-2261brn-s")
    assert d["images"] == []  # placeholder not replaced, so no broken link


# ---------- products whose price depends on an option (real mattress responses) ----------

def test_variable_product_detail_exposes_per_size_prices_and_no_base_price():
    client, _ = make(fixture("product_detail_variable"), cdn_folder="south-jackson")
    d = client.product_detail("royal-ultra-plush-12-hybrid-happyhomep2529parent")
    assert d["price"] is None and d["price_varies"] is True  # the product's own sale_price is -0.01
    assert [(o["attributes"], o["price"]) for o in d["options"]] == [
        ({"Bed Size": "Twin"}, 339.99), ({"Bed Size": "Full"}, 439.99), ({"Bed Size": "Queen"}, 479.99), ({"Bed Size": "King"}, 659.99)]
    assert set(d["options"][0]) == {"sku", "slug", "price", "attributes"}
    assert "base_price" not in json.dumps(d) and "169.00" not in json.dumps(d)


def test_plain_product_has_no_options_and_does_not_vary():
    client, _ = make(fixture("product_detail"))
    d = client.product_detail("blanche-side-chair-brown-2261brn-s")
    assert d["options"] == [] and d["price_varies"] is False


def test_search_listing_marks_option_priced_products():
    client, _ = make(fixture("product_search"))
    items = client.search_products("bed")["products"]
    varies = [p for p in items if p["price_varies"]]
    assert len(varies) == 7 and all(p["price"] is None for p in varies)  # all seven -0.01 products are option-priced
    assert not any(p["price_varies"] for p in items if p["price"] is not None)


def test_searching_for_a_sku_finds_that_product():
    client, _ = make(fixture("sku_search"))
    res = client.search_products("2261BRN-S")
    assert [p["sku"] for p in res["products"]] == ["2261BRN-S", "2261BRN-5P-Set"] and res["total"] == 2


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


def test_base_url_must_be_http_or_https():
    import pytest
    for bad in ("file:///etc/passwd", "127.0.0.1:8001", "ftp://x"):
        with pytest.raises(ValueError):
            ApiConfig(base_url=bad, store_uuid="u")
    ApiConfig(base_url="https://api.example.com", store_uuid="u")


# ---------- listing filters (formats confirmed against the real API, 16-17 Nov) ----------

def test_brand_uuid_in_brand_list_is_the_one_the_search_facets_use():
    """The brand_ids filter takes the same uuid the search response lists under filter.brands."""
    brands = make(fixture("brands"))[0].brands()
    facets = make(fixture("product_search"))[0].search_products("bed")["filters"]["brands"]
    foa_uuid = next(b["uuid"] for b in brands if b["slug"] == "foa")
    assert foa_uuid == "24535729948324335664029000859186" and "Furniture Of America" in facets


def test_search_sends_brand_and_price_in_the_confirmed_format():
    client, t = make(fixture("product_search"))
    client.search_products("bed", brand_ids=["24535729948324335664029000859186"], price_from=100, price_to=900.5, per_page=40)
    assert json.loads(t.requests[0].data) == {
        "q": "bed", "brand_ids": ["24535729948324335664029000859186"],
        "price_range": {"from": 100, "to": 900.5}, "per_page": 40}   # {"min","max"} is ignored by the API


def test_search_without_filters_sends_only_q():
    client, t = make(fixture("product_search"))
    client.search_products("bed")
    assert json.loads(t.requests[0].data) == {"q": "bed"}


@pytest.mark.parametrize("text", ["", " ", "a", " b "])
def test_search_text_under_two_characters_is_refused_without_a_request(text):
    client, t = make()
    with pytest.raises(ApiError) as err:
        client.search_products(text)
    assert err.value.status == 422 and t.requests == []   # the real API answers 422 "at least 2 characters"


def test_search_trims_the_text_before_sending():
    client, t = make(fixture("product_search"))
    client.search_products("  bed ")
    assert json.loads(t.requests[0].data) == {"q": "bed"}


def test_unfiltered_category_request_carries_the_paging_workaround():
    """Without any filter the API serves every page from one cached page 1; a price_range from 0 avoids that
    and returned the same totals on two real categories."""
    client, t = make(fixture("category_products"))
    client.category_products("dining-room", page=2)
    assert t.requests[0].full_url.endswith("/categories/dining-room/products?page=2")
    assert json.loads(t.requests[0].data) == {"price_range": {"from": 0}}


def test_category_request_with_a_price_limit_does_not_add_the_workaround():
    client, t = make(fixture("category_products"))
    client.category_products("dining-room", price_to=300)
    assert json.loads(t.requests[0].data) == {"price_range": {"to": 300}}


def test_category_request_with_brand_only_still_gets_the_workaround():
    client, t = make(fixture("category_products"))
    client.category_products("dining-room", brand_ids=["24535729948324335664029000859186"])
    assert json.loads(t.requests[0].data) == {"brand_ids": ["24535729948324335664029000859186"], "price_range": {"from": 0}}


@pytest.mark.parametrize("kwargs", [
    {"brand_ids": ["bad id"]}, {"brand_ids": "not-a-list"}, {"brand_ids": [5]},
    {"price_from": -1}, {"price_to": "10"}, {"price_from": True}, {"price_to": float("nan")},
    {"per_page": 0}, {"per_page": 41}, {"per_page": "16"},
])
def test_bad_filter_values_are_refused_before_any_request(kwargs):
    client, t = make()
    with pytest.raises(ValueError):
        client.search_products("bed", **kwargs)
    with pytest.raises(ValueError):
        client.category_products("dining-room", **kwargs)
    assert t.requests == []


# ---------- attribute filter and facets ----------

def test_search_sends_one_attribute_id():
    client, t = make(fixture("product_search"))
    client.search_products("bed", attribute_ids=["67ace390fcb040008ecdc568b52e13f2"])
    assert json.loads(t.requests[0].data) == {"q": "bed", "attribute_ids": ["67ace390fcb040008ecdc568b52e13f2"]}


def test_category_request_sends_attribute_ids_with_the_paging_workaround():
    client, t = make(fixture("category_products"))
    client.category_products("bedroom-furniture-beds", attribute_ids=["67ace390fcb040008ecdc568b52e13f2"])
    assert json.loads(t.requests[0].data) == {"attribute_ids": ["67ace390fcb040008ecdc568b52e13f2"], "price_range": {"from": 0}}


@pytest.mark.parametrize("bad", ["not-a-list", [5], ["bad id"], [None]])
def test_bad_attribute_ids_are_refused_before_any_request(bad):
    client, t = make()
    with pytest.raises(ValueError):
        client.search_products("bed", attribute_ids=bad)
    assert t.requests == []


def test_attribute_options_carry_the_ids_the_filter_takes():
    client, t = make(fixture("product_search"))
    options = client.attribute_options("bed")
    assert json.loads(t.requests[0].data) == {"q": "bed"}
    group = next(g for g in options if g["group"] == "Bed Size")
    assert {"id": "67ace390fcb040008ecdc568b52e13f2", "name": "Queen"} in group["values"]   # the id the real API filtered Queen by


# ---------- faqs ----------

def test_faqs_are_read_from_the_global_faq_endpoint_in_the_first_language():
    client, t = make(fixture("faqs"))
    faqs = client.faqs()
    assert t.requests[0].full_url == "http://127.0.0.1:8001/faqs/global"
    assert [f["slug"] for f in faqs] == ["what-payment-methods-do-you-accept", "is-online-payment-secure"]
    assert faqs[0]["question"] == "What payment methods do you accept?"
    assert faqs[0]["answer_html"].startswith("<p>We accept credit/debit cards")
    assert "product_ids" not in faqs[0] and "product_faqs" not in faqs[0]      # store-wide text, no product links


def test_faqs_skip_rows_without_a_question_or_answer_and_reject_a_wrong_shape():
    body = b'{"faqs": [{"slug": "a", "faq_languages": [{"question": "Q?", "answer": ""}]}, {"slug": "b", "faq_languages": []}]}'
    client, _ = make(body)
    assert client.faqs() == []
    client, _ = make(b'{"success": true, "data": []}')
    with pytest.raises(ApiError):
        client.faqs()


def test_page_is_read_from_the_page_endpoint_in_the_first_language():
    client, t = make(fixture("page_mattress_buying_guide"))
    page = client.page("mattress-buying-guide")
    assert t.requests[0].full_url == "http://127.0.0.1:8001/page/mattress-buying-guide"
    assert page["slug"] == "mattress-buying-guide" and page["title"] == "Mattress Buying Guide"
    assert page["html"].startswith("<!-- B3 MASTER FILE") and "Memory Foam Mattresses" in page["html"]
    assert page["status"] == 1 and page["deleted"] is False and page["advanced"] is False
    assert page["updated_at"] == "2026-06-07T16:31:33.000000Z"
    assert "global_options" not in page and "page_languages" not in page      # only the fields the index needs


def test_page_accepts_a_wrapped_answer_quotes_the_slug_and_rejects_a_wrong_shape():
    body = b'{"success": true, "data": {"slug": "a", "status": 1, "page_languages": [{"title": "T", "long_description": "<p>x</p>"}]}}'
    client, t = make(body)
    assert client.page("a b/c")["html"] == "<p>x</p>"
    assert t.requests[0].full_url.endswith("/page/a%20b%2Fc")
    client, _ = make(b'{"slug": "a"}')
    with pytest.raises(ApiError):
        client.page("a")


def test_page_marks_deleted_and_page_builder_pages_without_guessing_their_content():
    body = (b'{"slug": "p", "status": 1, "deleted_at": "2026-01-01", "page_languages": '
            b'[{"title": "P", "is_advanced": 1, "long_description": null, "builder_data": "{...}"}]}')
    client, _ = make(body)
    page = client.page("p")
    assert page["deleted"] is True and page["advanced"] is True and page["html"] == ""


# ---------- the logged-in customer's orders ----------
def test_customer_orders_send_the_customers_own_token_and_keep_only_the_fields_the_bot_may_state():
    client, t = make(fixture("orders_index_from_controller_code"))
    orders = client.customer_orders("tok.en.value", limit=10)
    req = t.requests[0]
    assert req.full_url == "http://127.0.0.1:8001/orders?page=1&limit=10"
    assert req.get_header("Authorization") == "Bearer tok.en.value" and req.get_header("Store-uuid") == UUID
    assert [o["order_number"] for o in orders] == ["102500001", "102500002", "102400007"]
    first = orders[0]
    assert set(first) == {"order_number", "order_status", "created_at", "grand_total", "items"}
    assert first["order_status"] == "Processing" and first["grand_total"] == 879.98
    assert first["items"] == [{"name": "Blanche Side Chair Brown", "sku": "2261BRN-S"},
                              {"name": "Blanche Brown Dining Group Set", "sku": "2261BRN-5P-Set"}]
    blob = json.dumps(orders)
    for private in ("customer@example.com", "Customer", "(000) 000-0000", "txn_SECRET_1", "card", "cust-9", "uuid"):
        assert private not in blob
    assert orders[2]["order_status"] is None and orders[2]["items"] == []


def test_the_token_is_not_kept_on_the_client_and_other_calls_never_carry_it():
    client, t = make(fixture("orders_index_from_controller_code"), fixture("brands"))
    client.customer_orders("tok.en.value")
    client.brands()
    assert t.requests[0].get_header("Authorization") == "Bearer tok.en.value"
    assert t.requests[1].get_header("Authorization") is None
    assert "tok.en.value" not in repr(vars(client)) and "tok.en.value" not in repr(client.config)


def test_customer_orders_need_a_token_and_reject_a_wrong_shape():
    client, t = make(b'{"orders": []}')
    for bad in ("", None):
        with pytest.raises(ApiError) as exc:
            client.customer_orders(bad)
        assert exc.value.status == 401
    assert t.requests == []                                                       # nothing is sent without a login
    client, _ = make(b'{"status": true, "orders": "no"}')
    with pytest.raises(ApiError):
        client.customer_orders("tok.en.value")


def test_an_unauthenticated_answer_from_the_api_is_a_401_error():
    err = urllib.error.HTTPError("http://x", 401, "Unauthorized", {}, io.BytesIO(b'{"message":"Unauthenticated"}'))
    client, _ = make(err)
    with pytest.raises(ApiError) as exc:
        client.customer_orders("tok.en.value")
    assert exc.value.status == 401 and "tok.en.value" not in str(exc.value)
