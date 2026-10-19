"""Rounded prices ($185 for 185.14) are repaired to the exact price; invented prices are still blocked."""
from sjbot import guardrails as g
from sjbot import router
from test_router import FakeLLM, call, say
import pytest


@pytest.fixture(autouse=True)
def no_review_log(monkeypatch, tmp_path):                  # the router writes to logs/review.jsonl; keep test rows out of it
    monkeypatch.setattr(router, "REVIEW_LOG", tmp_path / "review.jsonl")

PRODUCTS = {"products": [
    {"name": "Adelaide Chest Drift Wood", "price": 286.0},
    {"name": "Aleaport Chest of Drawers", "price": 185.14},
    {"name": "Aleaport Dresser", "price": 196.82},
    {"name": "Alisdair Chest of Drawers", "price": 138.0},
    {"name": "Alisdair Dresser", "price": 170.0},
    {"name": "Altyra Chest of Drawers", "price": 205.0},
]}

# the reply that fresh-dresser got blocked on (eval run 2026-10-15)
BLOCKED = ("Here are some dressers under $400:\n\n- Adelaide Chest Drift Wood \u2013 $286\n- Aleaport Chest of Drawers \u2013 $185\n"
           "- Aleaport Dresser \u2013 $197\n- Alisdair Chest of Drawers \u2013 $138\n- Alisdair Dresser \u2013 $170\n"
           "- Altyra Chest of Drawers \u2013 $205\n\nWould you like to see more options?")


def test_the_real_blocked_reply_is_blocked_by_check_reply_then_repaired():
    assert g.check_reply(BLOCKED, [PRODUCTS], "looking for a dresser, nothing over $400 pls") == (False, "unverified_price")
    fixed = g.repair_rounded_prices(BLOCKED, [PRODUCTS], "looking for a dresser, nothing over $400 pls")
    assert "$185.14" in fixed and "$196.82" in fixed and "$185\n" not in fixed and "$197\n" not in fixed
    assert "$286" in fixed and "$286.00" not in fixed                       # exact whole prices stay as they were
    assert g.check_reply(fixed, [PRODUCTS], "looking for a dresser, nothing over $400 pls") == (True, None)


def test_a_price_is_matched_to_the_product_on_its_line_when_two_real_prices_round_alike():
    res = [{"products": [{"name": "Ona Dresser", "price": 184.6}, {"name": "Pia Dresser", "price": 185.14}]}]
    assert g.repair_rounded_prices("- Pia Dresser: $185", res) == "- Pia Dresser: $185.14"
    assert g.repair_rounded_prices("- Ona Dresser: $185", res) == "- Ona Dresser: $184.60"
    assert g.repair_rounded_prices("It is $185", res) is None                # no product on the line, two real prices fit: do not guess


def test_invented_or_wrong_prices_are_not_repaired():
    assert g.repair_rounded_prices("Aleaport Dresser is $250", [PRODUCTS]) is None          # fits no real price
    assert g.repair_rounded_prices("Aleaport Dresser is $196.99", [PRODUCTS]) is None       # cents given, not a real price
    assert g.repair_rounded_prices("Aleaport Dresser is $196.82", [PRODUCTS]) is None       # already exact: nothing to fix
    assert g.repair_rounded_prices("Dresser $120", [{"weight": 120.0, "price": 400.0}]) is None   # 120 is a weight, not a price


def test_router_repairs_a_rounded_price_instead_of_sending_the_fallback(monkeypatch):
    monkeypatch.setitem(g.TOOLS, "get_product_details", lambda a: {"name": "Loreo Sofa", "sale_price": 898.6})
    out = router.answer("price of the Loreo sofa?", FakeLLM(call("get_product_details", sku="X"), say("The Loreo Sofa is $899.")))
    assert out["blocked"] is None and out["reply"] == "The Loreo Sofa is $898.60."


def test_a_rounded_price_that_equals_another_products_exact_price_is_still_repaired():
    res = [{"products": [{"name": "Ona Dresser", "price": 1033.8}, {"name": "Pia Dresser", "price": 1034.0}]}]
    assert g.repair_rounded_prices("- Ona Dresser: $1,034\n- Pia Dresser: $1,034", res) == "- Ona Dresser: $1,033.80\n- Pia Dresser: $1,034"
    assert g.repair_rounded_prices("- Pia Dresser: $1,034", res) is None                      # already exact: nothing to fix


def test_a_price_that_does_not_fit_the_product_on_its_line_is_not_borrowed_from_another_product():
    res = [{"products": [{"name": "Ona Dresser", "price": 500.4}, {"name": "Pia Dresser", "price": 800.7}]}]
    assert g.repair_rounded_prices("- Ona Dresser: $801", res) is None                        # $801 fits Pia's price, not Ona's

def test_a_short_product_name_inside_a_longer_one_does_not_borrow_the_short_products_price():
    res = [{"products": [{"name": "Dresser", "price": 457.6}, {"name": "Willowton Queen Sleigh Bed, Dresser and Mirror", "price": 458.2}]}]
    assert g.repair_rounded_prices("- Willowton Queen Sleigh Bed, Dresser and Mirror \u2013 $458", res) == \
        "- Willowton Queen Sleigh Bed, Dresser and Mirror \u2013 $458.20"
    assert g.repair_rounded_prices("- Dresser \u2013 $458", res) == "- Dresser \u2013 $457.60"


def test_products_with_the_same_name_and_alike_rounded_prices_stay_blocked():
    res = [{"products": [{"name": "Modmax 5-Piece Sectional", "price": 971.6}, {"name": "Modmax 5-Piece Sectional", "price": 972.4}]}]
    assert g.repair_rounded_prices("- Modmax 5-Piece Sectional \u2013 $972", res) is None   # two real prices fit one name: do not guess

def test_a_short_product_name_inside_a_longer_one_does_not_borrow_the_short_products_price():
    res = [{"products": [{"name": "Dresser", "price": 457.6}, {"name": "Willowton Queen Sleigh Bed, Dresser and Mirror", "price": 458.2}]}]
    assert g.repair_rounded_prices("- Willowton Queen Sleigh Bed, Dresser and Mirror \u2013 $458", res) == \
        "- Willowton Queen Sleigh Bed, Dresser and Mirror \u2013 $458.20"
    assert g.repair_rounded_prices("- Dresser \u2013 $458", res) == "- Dresser \u2013 $457.60"


def test_products_with_the_same_name_and_alike_rounded_prices_stay_blocked():
    res = [{"products": [{"name": "Modmax 5-Piece Sectional", "price": 971.6}, {"name": "Modmax 5-Piece Sectional", "price": 972.4}]}]
    assert g.repair_rounded_prices("- Modmax 5-Piece Sectional \u2013 $972", res) is None   # two real prices fit one name: do not guess