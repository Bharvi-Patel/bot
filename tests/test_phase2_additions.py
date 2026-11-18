import pytest

from bot.tools.check_inventory import check_inventory


# ---------- list_brands ----------
# list_brands now reads the store API; its tests are in tests/test_list_brands_api.py.
# (The old database tests for product counts and 0-count brands are gone on purpose: GET /brands has no counts.)


# ---------- list_attribute_values ----------
# list_attribute_values now reads the store API; its tests are in tests/test_list_attribute_values_api.py.


# ---------- check_inventory ----------
def test_availability_is_always_unknown():
    out = check_inventory({"sku": "ABC"})
    assert out["availability"] == "unknown" and "contact the store" in out["message"]
    assert "Never say an item is in stock" in out["message"]


@pytest.mark.parametrize("bad", [{}, {"sku": ""}, {"sku": 5}, {"sku": "x" * 500}, {"sku": "A", "qty": 1}])
def test_availability_bad_input(bad):
    assert "error" in check_inventory(bad)