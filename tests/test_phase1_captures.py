"""Real captures from the local API: attribute-filtered search and category listing, and blogs."""
import io
import json
import urllib.parse
from pathlib import Path

import pytest

import rag.api_ingest as ai
from bot.api_client import ApiClient, ApiConfig, ApiError
from bot.tools.search_products import search_products

FIX = Path(__file__).parent / "fixtures" / "api"
QUEEN_ID = "67ace390fcb040008ecdc568b52e13f2"
BLOG_SLUG = "best-sofa-designs-for-small-living-rooms"


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


def real(requests=None, overrides=None):
    """A client whose transport serves the saved captures. The search answers differently with attribute_ids."""
    overrides = overrides or {}

    def opener(req, timeout=None):
        path = urllib.parse.urlparse(req.full_url).path
        body = json.loads(req.data) if req.data else None
        if requests is not None:
            requests.append((req.get_method(), path, body))
        if path in overrides:
            return Resp(overrides[path])
        if path == "/products/search":
            name = "product_search_attribute" if body and "attribute_ids" in body else "product_search"
            return Resp((FIX / f"{name}.json").read_bytes())
        if path.startswith("/categories/") and path.endswith("/products"):
            return Resp((FIX / "category_attribute.json").read_bytes())
        if path == "/brands":
            return Resp((FIX / "brands.json").read_bytes())
        if path == "/blogs/active":
            return Resp((FIX / "blogs_active.json").read_bytes())
        if path == f"/blogs/{BLOG_SLUG}":
            return Resp((FIX / "blog_detail.json").read_bytes())
        raise AssertionError("unexpected request " + path)

    return ApiClient(ApiConfig("http://x", "uuid", retries=0), opener=opener)


# ---------- attribute filters: what the real API answered ----------
def test_attribute_filter_narrows_the_search_and_the_facets_stay_available():
    res = real().search_products("bed", attribute_ids=[QUEEN_ID])
    assert res["total"] == 52 and res["per_page"] == 16 and len(res["products"]) == 16
    groups = {g["group"]: g["values"] for g in res["filters"]["attributes"]}
    assert groups["Bed Size"] == ["Queen", "Twin", "King", "Full"]
    assert res["filters"]["brands"] == ["Happy Home"]


def test_every_result_of_the_size_filter_is_an_option_priced_product():
    items = real().search_products("bed", attribute_ids=[QUEEN_ID])["products"]
    assert all(p["price_varies"] and p["price"] is None for p in items)


def test_the_option_shown_with_a_result_is_one_size_and_not_the_filtered_one():
    items = real().search_products("bed", attribute_ids=[QUEEN_ID])["products"]
    by_name = {p["name"]: p["shown_option"] for p in items}
    assert by_name["HH221"] == {"option": "Queen", "price": 399.99}
    assert by_name['GOLD - 9" Inner Spring'] == {"option": "Twin", "price": 199.99}   # asked for Queen, shows a Twin
    assert any(o["option"] != "Queen" for o in by_name.values())


def test_the_unfiltered_listing_also_shows_one_size_each():
    items = {p["name"]: p["shown_option"] for p in real().search_products("bed")["products"] if p["price_varies"]}
    assert items["Board"] == {"option": "Twin", "price": 79.99}
    assert items['PRESIDENT - 14" Hybrid'] == {"option": "Queen", "price": 659.99}


def test_a_product_with_a_price_has_no_shown_option():
    items = real().search_products("bed")["products"]
    assert all(p["shown_option"] is None for p in items if not p["price_varies"])


def test_category_listing_with_an_attribute_filter_real_response():
    c = real()
    res = c.category_products("bedroom-furniture-beds", attribute_ids=[QUEEN_ID])
    assert res["total"] == 29 and res["last_page"] == 2 and len(res["products"]) == 16
    assert res["products"][0]["name"] == "HH221" and res["products"][0]["shown_option"] == {"option": "Queen", "price": 399.99}


def test_search_tool_with_a_size_filter_end_to_end():
    seen = []
    out = search_products({"keyword": "bed", "attribute_group": "Bed Size", "attribute_value": "Queen"}, client=real(seen))
    assert ("POST", "/products/search", {"q": "bed"}) in seen                                   # first the options of this keyword
    assert ("POST", "/products/search", {"q": "bed", "attribute_ids": [QUEEN_ID]}) in seen      # then the filtered list
    assert out["applied_filters"]["attribute"] == {"group": "Bed Size", "value": "Queen"}
    assert out["count"] == 8 and all(p["price_varies"] and p["price"] is None for p in out["products"])
    assert out["products"][0]["shown_option"] == {"option": "Queen", "price": 399.99}
    assert "ONE option only" in out["price_note"] and "not necessarily the size the customer asked for" in out["price_note"]
    assert "do not call these all the matching products" in out["attribute_note"]


def test_a_size_that_does_not_exist_is_refused_with_the_real_values():
    out = search_products({"keyword": "bed", "attribute_group": "Bed Size", "attribute_value": "Emperor"}, client=real())
    assert out["error"] == "unknown_attribute_value" and out["values"] == ["Full", "King", "Queen", "Twin"]


# ---------- blogs ----------
def test_blog_list_real_response():
    res = real().blogs()
    assert res["total"] == 1 and res["partial"] is False
    assert res["blogs"] == [{"uuid": "35691140378774295605152830193817", "slug": BLOG_SLUG, "status": 1, "deleted_at": None,
                             "updated_at": "2026-05-12T11:52:46.000000Z", "title": "Best Sofa Designs for Small Living Rooms",
                             "category": "bedroom-furniture"}]


def test_blog_list_says_when_it_holds_fewer_posts_than_the_store_has():
    body = json.dumps({"success": True, "totalRecord": 25, "limit": 10,
                       "blogs": [{"slug": f"b{i}", "status": 1, "blog_languages": [{"title": f"B{i}"}]} for i in range(10)]}).encode()
    res = real(overrides={"/blogs/active": body}).blogs()
    assert len(res["blogs"]) == 10 and res["total"] == 25 and res["partial"] is True


def test_blog_detail_real_response():
    post = real().blog(BLOG_SLUG)
    assert (post["slug"], post["status"], post["category"]) == (BLOG_SLUG, 1, "bedroom-furniture")
    assert post["title"] == "Best Sofa Designs for Small Living Rooms" and post["language_id"] == "d7f9d24a5f714f50d26a0d095799611c"
    assert post["html"].startswith("<p")


def test_unexpected_blog_shapes_are_errors():
    with pytest.raises(ApiError):
        real(overrides={"/blogs/active": b'{"success":true,"blogs":"nope"}'}).blogs()
    with pytest.raises(ApiError):
        real(overrides={f"/blogs/{BLOG_SLUG}": b'{"success":true,"blog":{"slug":"x","blog_languages":[]}}'}).blog(BLOG_SLUG)


# ---------- blogs in the policy index ----------
def test_blog_rows_have_the_index_row_format_and_clean_text():
    skipped, warnings = [], []
    rows = ai.blog_rows(real(), skipped, warnings)
    assert rows and not skipped and not warnings
    r = rows[0]
    assert r["source_type"] == "blog" and r["source_key"] == BLOG_SLUG and r["url_path"] == f"/blogs/{BLOG_SLUG}"
    assert r["title"] == "Best Sofa Designs for Small Living Rooms" and r["updated_at"] == "2026-05-12T11:52:46.000000Z"
    text = "\n".join(x["content"] for x in rows)
    assert "<p" not in text and "data-start" not in text and len(text) > 200


def test_a_partial_blog_list_is_warned_about_not_silent():
    body = json.dumps({"success": True, "totalRecord": 25, "blogs": [
        {"slug": BLOG_SLUG, "status": 1, "deleted_at": None, "blog_languages": [{"title": "T"}]}]}).encode()
    warnings = []
    ai.blog_rows(real(overrides={"/blogs/active": body}), [], warnings)
    assert warnings and "1 of 25" in warnings[0]


def test_a_blog_that_is_not_live_is_left_out_with_the_reason():
    body = json.dumps({"success": True, "totalRecord": 1, "blogs": [
        {"slug": "old-post", "status": 0, "deleted_at": None, "blog_languages": [{"title": "Old"}]}]}).encode()
    skipped = []
    assert ai.blog_rows(real(overrides={"/blogs/active": body}), skipped, []) == [] and skipped == [("blog:old-post", "deleted or not live")]


def test_blogs_are_supported_but_not_part_of_the_default_run():
    assert "blog" in ai.SUPPORTED_TYPES and "blog" not in ai.DEFAULT_TYPES
    rows = ai.ingest(real(), ("blog",))
    assert rows and all(r["source_type"] == "blog" for r in rows)
