"""search_products through the store API: fake catalog for the logic, saved real responses for the shapes."""
import io
import json
import urllib.error
import urllib.parse
from pathlib import Path

import pytest

from bot.api_client import ApiClient, ApiConfig, ApiError
from bot.tools import search_products as sp
from bot.tools.search_products import MAX_RESULTS, MAX_PAGES, ToolInputError, _prepare, _stem, search_products
from tests.api_fakes import CatalogClient, TreeClient, item

FIX = Path(__file__).parent / "fixtures" / "api"
BRANDS = [{"slug": "acme", "name": "Acme", "vendor": "V", "status": 1},
          {"slug": "coaster", "name": "Coaster", "vendor": "V", "status": 1},
          {"slug": "signature-design-by-ashley", "name": "Signature Design by Ashley\u2122", "vendor": "V", "status": 1}]


def beds(n=5, brand="Acme"):
    return [item(f"B{i}", f"Bed {i}", 100.0 + i * 10, brand) for i in range(n)]


# ---------- keyword and category sources ----------
def test_keyword_search_calls_the_search_endpoint_and_maps_fields():
    c = CatalogClient(beds(3), brands=BRANDS)
    out = search_products({"keyword": "bed"}, client=c)
    assert ("search", "bed", 1) in c.calls
    assert out["count"] == 3
    assert set(out["products"][0]) == {"sku", "slug", "name", "price", "brand_name", "main_image"}
    assert out["sorted_by"].startswith("store order")


def test_category_search_calls_the_category_endpoint_and_skips_the_brand_lookup():
    c = CatalogClient(by_category={"beds": beds(3)})
    out = search_products({"category_slug": "beds"}, client=c)
    assert c.calls == [("category", "beds", 1)] and out["count"] == 3


def test_keyword_inside_a_category_is_applied_here_on_the_names_with_the_singular_form():
    c = CatalogClient(by_category={"all": [item("A", "Oak Bed", 5), item("B", "Oak Nightstand", 6), item("C", "Pine Beds Set", 7)]}, brands=BRANDS)
    out = search_products({"category_slug": "all", "keyword": "beds"}, client=c)
    assert [p["sku"] for p in out["products"]] == ["A", "C"]


def test_no_keyword_category_or_brand_is_an_error():
    assert "error" in search_products({"min_price": 10}, client=CatalogClient())


def test_result_is_capped_and_says_so():
    c = CatalogClient(beds(12), brands=BRANDS)
    out = search_products({"keyword": "bed"}, client=c)
    assert out["count"] == MAX_RESULTS and "NOT sorted by price" in out["note"] and "all of them" in out["note"]


# ---------- price filters and sort ----------
def test_price_limits_filter_and_are_reported():
    c = CatalogClient(beds(6), brands=BRANDS)  # prices 100..150
    out = search_products({"keyword": "bed", "min_price": 110, "max_price": 140}, client=c)
    assert [p["price"] for p in out["products"]] == [110.0, 120.0, 130.0, 140.0]
    assert out["applied_filters"] == {"min_price": 110, "max_price": 140}


def test_price_sort_orders_the_whole_match_list_and_drops_unpriced_items():
    prods = [item("A", "Bed A", 300.0), item("B", "Bed B", 100.0), item("C", "Bed C", None), item("D", "Bed D", 200.0)]
    c = CatalogClient(prods, brands=BRANDS)
    asc = search_products({"keyword": "bed", "sort": "price_asc"}, client=c)
    assert [p["sku"] for p in asc["products"]] == ["B", "D", "A"]
    desc = search_products({"keyword": "bed", "sort": "price_desc"}, client=c)
    assert [p["sku"] for p in desc["products"]] == ["A", "D", "B"]


def test_price_sort_on_too_many_matches_refuses_instead_of_guessing():
    c = CatalogClient([item(f"S{i}", f"Sofa {i}", 500 + i) for i in range(16 * MAX_PAGES + 1)], brands=BRANDS)
    out = search_products({"keyword": "sofa", "sort": "price_asc"}, client=c)
    assert out["error"] == "too_many_matches" and "Do not name a cheapest" in out["note"] and "products" not in out
    assert len([x for x in c.calls if x[0] == "search"]) == MAX_PAGES


def test_unpriced_items_are_listed_with_a_note_when_no_price_rule_applies():
    c = CatalogClient([item("A", "Bed A", None), item("B", "Bed B", 50.0)], brands=BRANDS)
    out = search_products({"keyword": "bed"}, client=c)
    assert out["count"] == 2 and "no price available" in out["price_note"]
    assert search_products({"keyword": "bed", "max_price": 60}, client=c)["count"] == 1  # unpriced never passes a price limit


def test_cheapest_without_a_keyword_carries_the_type_check_note():
    c = CatalogClient(by_category={"dressers": [item("A", "Drawers (2Pcs), Black", 79.2)]})
    assert "type_check" in search_products({"category_slug": "dressers", "sort": "price_asc"}, client=c)
    assert "type_check" not in search_products({"category_slug": "dressers", "sort": "price_asc", "keyword": "dresser"}, client=c)
    assert "type_check" not in search_products({"category_slug": "dressers"}, client=c)


# ---------- products whose price depends on an option ----------
def varies_item(sku, name, brand="Acme"):
    return {**item(sku, name, None, brand), "price_varies": True}


def test_option_priced_products_are_flagged_and_never_given_a_price():
    out = search_products({"keyword": "mattress"}, client=CatalogClient([varies_item("M1", "Mattress One")], brands=BRANDS))
    assert out["products"][0]["price"] is None and out["products"][0]["price_varies"] is True
    assert "get_product_details" in out["price_note"] and "depends on its option" in out["price_note"]
    assert "price_varies_skipped" not in out  # no price rule was used


def test_a_price_limit_says_how_many_option_priced_products_it_could_not_check():
    c = CatalogClient([varies_item("M1", "Mattress One"), varies_item("M2", "Mattress Two"), item("M3", "Mattress Three", 150.0)], brands=BRANDS)
    out = search_products({"keyword": "mattress", "max_price": 200}, client=c)
    assert [p["sku"] for p in out["products"]] == ["M3"]
    assert out["price_varies_skipped"] == 2 and "could not be checked" in out["price_varies_note"]


def test_price_sort_also_reports_the_option_priced_products_it_left_out():
    c = CatalogClient([varies_item("M1", "Mattress One"), item("M2", "Mattress Two", 150.0), item("M3", "Mattress Three", 120.0)], brands=BRANDS)
    out = search_products({"keyword": "mattress", "sort": "price_asc"}, client=c)
    assert [p["sku"] for p in out["products"]] == ["M3", "M2"] and out["price_varies_skipped"] == 1


# ---------- brands ----------
def test_brand_slug_filters_by_the_brand_name_and_searches_for_it():
    prods = [item("A", "Bed A", 100.0, "Acme"), item("B", "Bed B", 90.0, "Coaster")]
    c = CatalogClient(prods, by_category={"beds": prods}, brands=BRANDS)
    out = search_products({"category_slug": "beds", "brand_slug": "coaster"}, client=c)
    assert [p["sku"] for p in out["products"]] == ["B"]
    c2 = CatalogClient(prods, brands=BRANDS)
    out2 = search_products({"brand_slug": "acme"}, client=c2)
    assert ("search", "Acme", 1) in c2.calls and [p["sku"] for p in out2["products"]] == ["A"]


def test_the_signature_brand_matches_despite_the_trademark_sign():
    prods = [item("A", "Chair", 10.0, "Signature Design by Ashley\u2122")]
    c = CatalogClient(prods, by_category={"chairs": prods}, brands=BRANDS)
    assert search_products({"category_slug": "chairs", "brand_slug": "signature-design-by-ashley"}, client=c)["count"] == 1


def test_unknown_brand_slug_is_an_error_with_a_hint():
    out = search_products({"keyword": "bed", "brand_slug": "nope"}, client=CatalogClient(beds(2), brands=BRANDS))
    assert out["error"] == "unknown_brand" and "list_brands" in out["note"]


def test_a_keyword_that_is_exactly_a_brand_becomes_a_brand_filter():
    # real call from the log: keyword "acme" in the bed category with max_price 400
    args = {"category_slug": "bedroom-furniture-beds", "max_price": 400, "keyword": "acme"}
    assert sp._brand_from_keyword(args, BRANDS) == {"category_slug": "bedroom-furniture-beds", "max_price": 400, "brand_slug": "acme"}
    assert sp._brand_from_keyword({"keyword": "Coaster", "max_price": 300}, BRANDS) == {"max_price": 300, "brand_slug": "coaster"}
    assert sp._brand_from_keyword({"keyword": "signature design by ashley"}, BRANDS) == {"brand_slug": "signature-design-by-ashley"}


def test_everything_else_is_left_alone_by_the_brand_from_keyword_step():
    assert sp._brand_from_keyword({"keyword": "sofa"}, BRANDS) == {"keyword": "sofa"}
    assert sp._brand_from_keyword({"keyword": "acme bed"}, BRANDS) == {"keyword": "acme bed"}
    assert sp._brand_from_keyword({"keyword": "acme", "brand_slug": "coaster"}, BRANDS) == {"keyword": "acme", "brand_slug": "coaster"}
    assert sp._brand_from_keyword({"category_slug": "x"}, BRANDS) == {"category_slug": "x"}


def test_the_brand_keyword_search_uses_the_brand_not_a_name_search():
    prods = [item("A", "Oak Bed", 100.0, "Acme"), item("B", "Acme Style Bed", 90.0, "Coaster")]
    c = CatalogClient(prods, by_category={"beds": prods}, brands=BRANDS)
    out = search_products({"keyword": "acme", "category_slug": "beds"}, client=c)
    assert [p["sku"] for p in out["products"]] == ["A"]


# ---------- filters the API cannot do yet ----------
@pytest.mark.parametrize("args,word", [({"color": "Black"}, "color"), ({"material": "Oak"}, "material"),
                                       ({"max_width_in": 80}, "size or width"),
                                       ({"attribute_group": "Bed Size", "attribute_value": "Queen"}, "attribute")])
def test_unsupported_filters_are_refused_with_guidance(args, word):
    out = search_products({"keyword": "bed", **args}, client=CatalogClient(beds(2), brands=BRANDS))
    assert out["error"] == "unsupported_filter" and word in out["note"] and "Search again without it" in out["note"]


# ---------- unknown category ----------
def test_unknown_category_is_reported_with_close_matches():
    tree = TreeClient([{"category_id": 1, "parent_id": 0, "slug": "bedroom", "title": "Bedroom"},
                       {"category_id": 2, "parent_id": 1, "slug": "bedroom-furniture-beds", "title": "Beds"}]).categories()
    c = CatalogClient(by_category={}, tree=tree)
    out = search_products({"category_slug": "beds"}, client=c)
    assert out["error"] == "unknown_category" and "list_categories" in out["note"]
    assert {"slug": "bedroom-furniture-beds", "title": "Beds"} in out["did_you_mean"]


# ---------- parts hidden ----------
def _parts():
    return [item("1", "Queen Decking Board Set", 77.0), item("2", "Akerson Dresser Top Grey", 66.0), item("3", "Bar Table Top", 77.0),
            item("4", "Base, Box 2 of 2", 50.0), item("5", "Matteo Top Dresser", 88.0), item("6", "Kimonte Dining Table", 77.28),
            item("7", "Glass Top Coffee Table", 120.0)]


def test_parts_are_hidden_but_real_items_stay():
    c = CatalogClient(by_category={"x": _parts()})
    names = [p["name"] for p in search_products({"category_slug": "x"}, client=c)["products"]]
    for hidden in ("Queen Decking Board Set", "Akerson Dresser Top Grey", "Bar Table Top", "Base, Box 2 of 2"):
        assert hidden not in names, hidden
    for shown in ("Matteo Top Dresser", "Kimonte Dining Table", "Glass Top Coffee Table"):
        assert shown in names, shown


def test_a_search_for_the_parts_category_still_finds_them():
    c = CatalogClient(by_category={"component-skus": _parts()})
    assert search_products({"category_slug": "component-skus"}, client=c)["count"] == 7


def test_parts_filter_can_be_switched_off(monkeypatch):
    monkeypatch.setattr(sp, "HIDE_COMPONENT_SKUS", False)
    c = CatalogClient(by_category={"x": _parts()})
    assert search_products({"category_slug": "x"}, client=c)["count"] == 7


# ---------- empty results ----------
def test_empty_search_plain_note():
    out = search_products({"keyword": "zzz"}, client=CatalogClient(beds(2), brands=BRANDS))
    assert out["count"] == 0 and out["note"].startswith("No products matched")
    assert "list_categories" in out["note"] and "category_slug" in out["note"]


def test_empty_category_search_tells_the_model_to_try_a_keyword():
    out = search_products({"category_slug": "beds"}, client=CatalogClient(by_category={"beds": []}))
    assert "keyword" in out["note"] and "list_categories" not in out["note"]


def test_nothing_under_the_limit_lists_the_cheapest_above_it():
    prods = [item("A", "Bed A", 500.0), item("B", "Bed B", 300.0), item("C", "Bed C", 400.0), item("D", "Bed D", 900.0)]
    out = search_products({"keyword": "bed", "max_price": 200}, client=CatalogClient(prods, brands=BRANDS))
    assert out["count"] == 0
    assert [p["price"] for p in out["cheapest_over_limit"]] == [300.0, 400.0, 500.0]
    assert "SAME reply" in out["note"]


def test_a_partial_scan_never_claims_the_store_has_none():
    prods = [item(f"B{i}", f"Bed {i}", 100.0, "Acme") for i in range(16 * MAX_PAGES + 10)]
    out = search_products({"keyword": "bed", "brand_slug": "coaster"}, client=CatalogClient(prods, brands=BRANDS))
    assert out["count"] == 0 and "Do NOT say the store has none" in out["note"]
    assert out["searched"] == {"checked": 16 * MAX_PAGES, "of": 16 * MAX_PAGES + 10}


def test_a_partial_scan_with_hits_says_not_all_matches_were_checked():
    prods = [item(f"B{i}", f"Bed {i}", 100.0, "Coaster" if i == 3 else "Acme") for i in range(16 * MAX_PAGES + 10)]
    out = search_products({"keyword": "bed", "brand_slug": "coaster"}, client=CatalogClient(prods, brands=BRANDS))
    assert out["count"] == 1 and "Only the first" in out["partial_note"]


def test_a_full_first_page_of_matches_stops_early():
    c = CatalogClient([item(f"B{i}", f"Bed {i}", 10.0) for i in range(100)], brands=BRANDS)
    search_products({"keyword": "bed", "max_price": 50}, client=c)
    assert len([x for x in c.calls if x[0] == "search"]) == 1


# ---------- validation ----------
@pytest.mark.parametrize("bad", [
    {"keyword": 5}, {"keyword": "x" * 500}, {"sort": "random"}, {"min_price": -1}, {"max_price": "cheap"},
    {"min_price": 500, "max_price": 100}, {"include_variants": True}, {"attribute_group": "Bed Size"},
    {"attribute_group": "Not A Group", "attribute_value": "x"}, {"category_slug": ["a"]},
])
def test_bad_input_returns_an_error_dict_not_an_exception(bad):
    out = search_products(bad, client=CatalogClient(beds(1), brands=BRANDS))
    assert "error" in out


def test_include_variants_is_not_a_parameter():
    with pytest.raises(ToolInputError):
        _prepare({"include_variants": True})


def test_every_known_attribute_group_is_accepted_by_validation():
    for g in sp.ATTRIBUTE_GROUPS:
        _prepare({"attribute_group": g, "attribute_value": "x"})


@pytest.mark.parametrize("word,stem", [("beds", "bed"), ("Sofas", "sofa"), ("loveseats", "loveseat"), ("mattresses", "mattress"),
                                       ("nightstands", "nightstand"), ("accessories", "accessory"), ("glass", "glass"),
                                       ("bed", "bed"), ("bus", "bus"), ("rugs", "rug"), ("tv", "tv")])
def test_plural_keywords_are_reduced_to_the_singular(word, stem):
    assert _stem(word) == stem


# ---------- failures ----------
def test_api_down_gives_a_safe_error():
    class Down(CatalogClient):
        def search_products(self, query, page=1):
            raise ApiError("down", retryable=True)
    out = search_products({"keyword": "bed"}, client=Down(brands=BRANDS))
    assert "Do not guess" in out["error"]


def test_missing_store_setting_gives_a_safe_error(monkeypatch):
    monkeypatch.delenv("BOT_STORE_UUID", raising=False)
    assert "Do not guess" in search_products({"keyword": "bed"})["error"]


# ---------- against saved real responses ----------
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


def real_client(urls=None):
    def opener(req, timeout=None):
        path = urllib.parse.urlparse(req.full_url).path
        if urls is not None:
            urls.append((req.get_method(), req.full_url))
        if path == "/products/search":
            return Resp((FIX / "product_search.json").read_bytes())
        if path == "/brands":
            return Resp((FIX / "brands.json").read_bytes())
        if path == "/categories/dining-room-dining-furniture/products":
            return Resp((FIX / "category_products.json").read_bytes())
        if path == "/categories/nope/products":
            raise urllib.error.HTTPError(req.full_url, 404, "nf", {}, io.BytesIO(b'{"success":false,"message":"No query results","response_code":404}'))
        if path == "/categories/tree":
            return Resp((FIX / "categories_tree.json").read_bytes())
        raise AssertionError("unexpected request " + req.full_url)
    return ApiClient(ApiConfig("http://x", "uuid", cdn_folder="south-jackson", retries=0), opener=opener)


def test_real_search_response_for_bed():
    urls = []
    out = search_products({"keyword": "bed"}, client=real_client(urls))
    assert urls[0][1] == "http://x/brands" and urls[1] == ("POST", "http://x/products/search")
    assert out["count"] == 8 and out["products"][0]["name"] == "I-105XLW - Twin/Full/Queen"
    assert out["products"][0]["price"] == 59.99 and out["products"][0]["brand_name"] == "Happy Home"
    assert out["products"][0]["main_image"].startswith("https://cdn.b3c2.com/south-jackson/")
    assert "base_price" not in json.dumps(out)


def test_real_search_never_shows_a_minus_one_cent_price():
    out = search_products({"keyword": "bed", "max_price": 100000}, client=real_client())
    assert all(p["price"] is not None and p["price"] > 0 for p in out["products"])


def test_real_category_listing_with_a_price_limit():
    out = search_products({"category_slug": "dining-room-dining-furniture", "max_price": 450}, client=real_client())
    assert out["count"] >= 1 and all(p["price"] <= 450 for p in out["products"])
    assert out["applied_filters"] == {"max_price": 450}


def test_real_unknown_category_404():
    out = search_products({"category_slug": "nope"}, client=real_client())
    assert out["error"] == "unknown_category"


# ---------- filters handed to the API ----------
BRANDS_WITH_IDS = [{"uuid": "u-acme", "slug": "acme", "name": "Acme", "vendor": "V", "status": 1},
                   {"uuid": None, "slug": "coaster", "name": "Coaster", "vendor": "V", "status": 1}]


def test_brand_and_price_limits_are_sent_to_the_api_on_a_search():
    c = CatalogClient(beds(3), brands=BRANDS_WITH_IDS)
    search_products({"keyword": "bed", "brand_slug": "acme", "min_price": 50, "max_price": 500}, client=c)
    assert c.filters == [{"brand_ids": ["u-acme"], "price_from": 50.0, "price_to": 500.0}]


def test_brand_and_price_limits_are_sent_to_the_api_on_a_category_listing():
    c = CatalogClient(by_category={"beds": beds(3)}, brands=BRANDS_WITH_IDS)
    search_products({"category_slug": "beds", "brand_slug": "acme", "max_price": 500}, client=c)
    assert c.filters == [{"brand_ids": ["u-acme"], "price_to": 500.0}]


def test_no_filters_are_sent_when_none_were_asked_for():
    c = CatalogClient(beds(3), brands=BRANDS_WITH_IDS)
    search_products({"keyword": "bed"}, client=c)
    assert c.filters == [{}]


def test_brand_without_an_id_is_filtered_by_the_tool_only():
    c = CatalogClient([item("C1", "Coaster Bed", 100, "Coaster"), item("A1", "Acme Bed", 100, "Acme")], brands=BRANDS_WITH_IDS)
    res = search_products({"keyword": "bed", "brand_slug": "coaster"}, client=c)
    assert c.filters == [{}] and [p["sku"] for p in res["products"]] == ["C1"]


def test_price_limits_are_still_checked_on_what_the_api_returns():
    c = CatalogClient([item("A", "Bed A", 40), item("B", "Bed B", 400), item("C", "Bed C", 900)], brands=BRANDS_WITH_IDS)
    res = search_products({"keyword": "bed", "min_price": 50, "max_price": 500}, client=c)
    assert [p["sku"] for p in res["products"]] == ["B"]


def test_a_one_letter_keyword_is_refused_without_searching():
    c = CatalogClient(beds(3), brands=BRANDS_WITH_IDS)
    res = search_products({"keyword": "a"}, client=c)
    assert res["error"] == "keyword_too_short"
    assert not [call for call in c.calls if call[0] in ("search", "category")]


def test_a_one_letter_keyword_is_fine_with_a_category():
    c = CatalogClient(by_category={"beds": beds(3)})
    assert "error" not in search_products({"keyword": "a", "category_slug": "beds"}, client=c)
