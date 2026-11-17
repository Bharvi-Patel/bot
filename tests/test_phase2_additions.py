import pytest

from bot.tools.check_inventory import check_inventory
from bot.tools.list_attribute_values import list_attribute_values


# ---------- list_brands ----------
# list_brands now reads the store API; its tests are in tests/test_list_brands_api.py.
# (The old database tests for product counts and 0-count brands are gone on purpose: GET /brands has no counts.)


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