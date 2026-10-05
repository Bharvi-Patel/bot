import pytest

from sjbot.tools.search_products import (
    MAX_RESULTS, ToolInputError, build_search_query, search_products,
)


def test_no_filters_returns_capped_list_one_row_per_family():
    sql, params = build_search_query({})
    assert f"LIMIT {MAX_RESULTS}" in sql
    assert "ROW_NUMBER() OVER (PARTITION BY" in sql
    assert "WHERE rn = 1" in sql
    assert "WHERE p." not in sql          # no filters, so no filter clause
    assert params == []


def test_result_includes_matching_options_count():
    sql, _ = build_search_query({})
    assert "matching_options" in sql


def test_variants_are_searched_not_excluded():
    sql, _ = build_search_query({"color": "Black"})
    assert "parent_uuid IS NULL" not in sql


def test_include_variants_is_no_longer_a_parameter():
    with pytest.raises(ToolInputError):
        build_search_query({"include_variants": True})


def test_category_matches_product_or_its_parent():
    sql, _ = build_search_query({"category_slug": "sofas-and-seating-sofas"})
    assert "cat_products AS" in sql
    assert "p.product_uuid IN (SELECT product_uuid FROM cat_products)" in sql
    assert "p.parent_uuid IN (SELECT product_uuid FROM cat_products)" in sql


def test_search_reads_slim_view_not_the_wide_one():
    sql, _ = build_search_query({})
    assert "vw_chat_product_search" in sql
    assert "vw_chat_products" not in sql


def test_price_range_uses_placeholders_not_values():
    sql, params = build_search_query({"min_price": 300, "max_price": 1200})
    assert "p.price >= %s" in sql and "p.price <= %s" in sql
    assert "300" not in sql and "1200" not in sql
    assert params[:2] == [300.0, 1200.0]


def test_category_adds_recursive_subtree_and_comes_first_in_params():
    sql, params = build_search_query({"category_slug": "sofas-and-seating-sofas", "max_price": 1000})
    assert sql.startswith("WITH RECURSIVE sub")
    assert params[0] == "sofas-and-seating-sofas"
    assert params[1] == 1000.0


def test_keyword_words_are_and_ed_and_wildcards_escaped():
    sql, params = build_search_query({"keyword": "black 100%_sofa"})
    assert sql.count("CONCAT_WS(' ', p.name, par.name) LIKE %s") == 2
    assert "%black%" in params
    assert "%100\\%\\_sofa%" in params


def test_sql_injection_text_stays_in_params():
    evil = "x'; DROP TABLE products; --"
    sql, params = build_search_query({"brand_slug": evil})
    assert "DROP TABLE" not in sql
    assert evil in params


def test_color_filter_uses_attribute_view():
    sql, params = build_search_query({"color": "Black"})
    assert "vw_chat_product_attributes" in sql
    assert "Black" in params


def test_width_filter():
    sql, params = build_search_query({"max_width_in": 80})
    assert "p.width <= %s" in sql and 80.0 in params


@pytest.mark.parametrize("sort,fragment", [
    ("price_asc", "ORDER BY price ASC"), ("price_desc", "ORDER BY price DESC"), ("name", "ORDER BY name ASC"),
])
def test_sort_whitelist(sort, fragment):
    sql, _ = build_search_query({"sort": sort})
    assert fragment in sql


def test_unknown_sort_rejected():
    with pytest.raises(ToolInputError):
        build_search_query({"sort": "price; DROP TABLE products"})


@pytest.mark.parametrize("bad", [
    {"max_price": -5},
    {"max_price": "cheap"},
    {"min_price": 500, "max_price": 100},
    {"keyword": "x" * 500},
    {"colour": "Black"},          # misspelled key from the model
    {"max_price": True},
])
def test_bad_input_rejected(bad):
    with pytest.raises(ToolInputError):
        build_search_query(bad)


def test_tool_returns_error_dict_not_exception():
    out = search_products({"max_price": -1}, run_query=lambda *_: [])
    assert "error" in out


def test_tool_no_results_note():
    out = search_products({"max_price": 10}, run_query=lambda *_: [])
    assert out["count"] == 0 and "loosening" in out["note"]


def test_tool_full_page_note():
    rows = [{"sku": str(i)} for i in range(MAX_RESULTS)]
    out = search_products({}, run_query=lambda *_: rows)
    assert out["count"] == MAX_RESULTS and "narrowing" in out["note"]


def test_search_results_get_full_image_urls(monkeypatch):
    monkeypatch.setenv("SJ_IMAGE_BASE_URL", "https://shop.example/media")
    out = search_products({}, run_query=lambda *_: [{"sku": "A", "main_image": "a.webp"}])
    assert out["products"][0]["main_image"] == "https://shop.example/media/a.webp"
