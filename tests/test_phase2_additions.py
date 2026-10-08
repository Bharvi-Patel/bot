import pytest

from sjbot.tools.check_inventory import check_inventory
from sjbot.tools.list_attribute_values import list_attribute_values
from sjbot.tools.list_brands import list_brands
from sjbot.tools.search_products import (
    ATTRIBUTE_GROUPS, ToolInputError, build_missing_size_query, build_search_query, search_products,
)


# ---------- search_products: attribute filter ----------
def test_attribute_filter_uses_group_and_value_params():
    sql, params = build_search_query({"attribute_group": "bed size", "attribute_value": "Queen"})
    assert "vw_chat_product_attributes" in sql
    assert params[:2] == ["Bed Size", "Queen"]          # group is normalised to the catalog spelling


@pytest.mark.parametrize("bad", [
    {"attribute_group": "Bed Size"},                       # value missing
    {"attribute_value": "Queen"},                          # group missing
    {"attribute_group": "Not A Group", "attribute_value": "x"},
    {"attribute_group": "Color'; DROP TABLE x;--", "attribute_value": "x"},
])
def test_attribute_filter_rejects_bad_input(bad):
    with pytest.raises(ToolInputError):
        build_search_query(bad)


def test_every_known_group_is_accepted():
    for g in ATTRIBUTE_GROUPS:
        build_search_query({"attribute_group": g, "attribute_value": "x"})


# ---------- search_products: width handling ----------
def test_width_filter_ignores_zero_widths():
    sql, params = build_search_query({"max_width_in": 80})
    assert "p.width > 0 AND p.width <= %s" in sql and 80.0 in params


def test_missing_size_query_only_when_width_filter_used():
    assert build_missing_size_query({"max_price": 500}) is None
    sql, params = build_missing_size_query({"max_width_in": 80, "max_price": 1000, "category_slug": "sofas"})
    assert "p.width IS NULL OR p.width = 0" in sql
    assert "p.width <= %s" not in sql
    assert params == ["sofas", 1000.0]


def test_search_reports_products_without_size_data():
    calls = []

    def run(sql, params=()):
        calls.append(sql)
        return [{"n": 7}] if "COUNT(DISTINCT" in sql else [{"sku": "A", "main_image": None, "width": 70}]

    out = search_products({"max_width_in": 80}, run_query=run)
    assert out["without_size_data"] == 7 and "no width on file" in out["size_note"]
    assert len(calls) == 2


def test_search_without_width_filter_makes_one_query_and_no_size_fields():
    calls = []
    out = search_products({"max_price": 500}, run_query=lambda s, p=(): calls.append(s) or [])
    # an empty result under a price limit also runs one "nearest products" lookup, but never the missing-size count
    assert len(calls) == 2 and not any("COUNT(DISTINCT" in c for c in calls) and "without_size_data" not in out


def test_zero_missing_means_no_size_note():
    run = lambda s, p=(): [{"n": 0}] if "COUNT(DISTINCT" in s else []
    out = search_products({"max_width_in": 80}, run_query=run)
    assert out["without_size_data"] == 0 and "size_note" not in out


# ---------- list_brands ----------
def test_brands_deduped_and_counted():
    rows = [
        {"brand_slug": "ashley", "brand_name": "Ashley", "active_products": 7381},
        {"brand_slug": "ashley", "brand_name": "Ashley", "active_products": 7381},  # duplicate
        {"brand_slug": "nectar", "brand_name": "Nectar", "active_products": 0},
    ]
    out = list_brands({}, run_query=lambda *_: rows)
    assert out["count"] == 2
    assert [b["products_available"] for b in out["brands"]] == [7381, 0]
    assert "0" in out["note"]


def test_brands_empty_and_args():
    assert list_brands({}, run_query=lambda *_: [])["found"] is False
    assert "error" in list_brands({"x": 1}, run_query=lambda *_: [])


# ---------- list_attribute_values ----------
def test_attribute_values_normalises_group_and_binds_it():
    seen = {}

    def run(sql, params=()):
        seen["params"] = list(params)
        return [{"value": "Queen", "products": 120}, {"value": "King", "products": 90}]

    out = list_attribute_values({"group": "bed size"}, run_query=run)
    assert seen["params"][0] == "Bed Size"
    assert out["values"][0] == {"value": "Queen", "products": 120}


@pytest.mark.parametrize("bad", [{}, {"group": ""}, {"group": 5}, {"group": "Nope"}, {"group": "Color", "x": 1}])
def test_attribute_values_bad_input(bad):
    assert "error" in list_attribute_values(bad, run_query=lambda *_: [])


# ---------- check_inventory ----------
def test_availability_is_always_unknown():
    out = check_inventory({"sku": "ABC"})
    assert out["availability"] == "unknown" and "contact the store" in out["message"]
    assert "Never say an item is in stock" in out["message"]


@pytest.mark.parametrize("bad", [{}, {"sku": ""}, {"sku": 5}, {"sku": "x" * 500}, {"sku": "A", "qty": 1}])
def test_availability_bad_input(bad):
    assert "error" in check_inventory(bad)