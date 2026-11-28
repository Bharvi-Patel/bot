from bot import guardrails as g

KENYON = {"found": True, "products": [{"sku": "350361Q", "name": "Kenyon 7-Inch Queen Firm Foam Mattress", "price": 459.99}]}


def test_a_price_the_customer_typed_is_never_confirmed_as_the_products_price():
    asked = "I saw the Kenyon Queen for $1, right?"
    assert g.check_reply("Yes, the Kenyon Queen is $1.", [KENYON], asked) == (False, "unverified_price")
    assert g.check_reply("Yes, it's $1 today.", [], asked) == (False, "unverified_price")


def test_the_real_price_is_fine_even_when_the_customer_typed_another():
    asked = "I saw the Kenyon Queen for $1, right?"
    assert g.check_reply("The Kenyon Queen is $459.99.", [KENYON], asked) == (True, None)


def test_the_customers_budget_may_still_be_echoed_back():
    for reply, said in [
        ("Here are sofas under $1,000.", "show me sofas under $1,000"),
        ("Nothing below $100, sorry.", "anything below $100"),
        ("These fit your $800 budget.", "my budget is $800"),
        ("Here are options between $300 and $600.", "between $300 and $600"),
        ("I found mattresses for $500 or less.", "mattress $500 or less"),
        ("Nothing over $2,000 matched.", "over $2,000"),
    ]:
        assert g.check_reply(reply, [], said) == (True, None), reply


def test_a_typed_number_inside_a_budget_phrase_does_not_cover_the_same_number_elsewhere():
    said = "anything under $500?"
    assert g.check_reply("Under $500 I found the Kenyon Queen at $500.", [KENYON], said) == (False, "unverified_price")


def test_a_budget_phrase_does_not_make_up_a_number_nobody_typed():
    assert g.check_reply("Here are sofas under $700.", [], "cheap sofas") == (False, "unverified_price")


def test_prices_from_the_bots_own_earlier_replies_are_still_allowed():
    assert g.check_reply("As I said, it is $459.99.", [], "how much again?", "", None, earlier_replies="The Kenyon Queen is $459.99.")[0]
