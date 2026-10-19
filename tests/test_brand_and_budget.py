"""Two problems found by tool-call logging in the 2026-10-15 held-out run:
 1. a brand sent as a text keyword ("acme") finds nothing, and the customer is told we do not carry Acme (23 Acme beds exist);
 2. the model writes a budget nobody gave ("for under $70") and the whole reply is blocked although the product price is right."""
import pytest

from sjbot import guardrails as g
from sjbot import router
from sjbot.tools import search_products as sp
from test_router import FakeLLM, call, say


@pytest.fixture(autouse=True)
def no_review_log(monkeypatch, tmp_path):
    monkeypatch.setattr(router, "REVIEW_LOG", tmp_path / "review.jsonl")


BRANDS = [{"brand_slug": "acme", "brand_name": "Acme"}, {"brand_slug": "coaster", "brand_name": "Coaster"},
          {"brand_slug": "signature-design-by-ashley", "brand_name": "Signature Design by Ashley\u2122"}]


def fake_query(log=None, brands=BRANDS):
    def run(sql, params=None):
        if "vw_chat_brands" in sql:
            return brands
        if log is not None:
            log.append((sql, params))
        return []
    return run


# ---------------- brand as keyword ----------------
def test_a_keyword_that_is_exactly_a_brand_becomes_a_brand_filter():
    # real call from the log: keyword "acme" in the bed category with max_price 400
    args = {"category_slug": "bedroom-furniture-beds", "max_price": 400, "keyword": "acme"}
    assert sp._brand_from_keyword(args, fake_query()) == {"category_slug": "bedroom-furniture-beds", "max_price": 400, "brand_slug": "acme"}
    assert sp._brand_from_keyword({"keyword": "Coaster", "max_price": 300}, fake_query()) == {"max_price": 300, "brand_slug": "coaster"}
    assert sp._brand_from_keyword({"keyword": "signature design by ashley"}, fake_query()) == {"brand_slug": "signature-design-by-ashley"}


def test_everything_else_is_left_alone():
    q = fake_query()
    assert sp._brand_from_keyword({"keyword": "sofa"}, q) == {"keyword": "sofa"}                 # not a brand
    assert sp._brand_from_keyword({"keyword": "acme bed"}, q) == {"keyword": "acme bed"}         # only an exact brand name counts
    assert sp._brand_from_keyword({"keyword": "acme", "brand_slug": "coaster"}, q) == {"keyword": "acme", "brand_slug": "coaster"}
    assert sp._brand_from_keyword({"category_slug": "x"}, q) == {"category_slug": "x"}

    def boom(sql, params=None):
        raise RuntimeError("db down")
    assert sp._brand_from_keyword({"keyword": "acme"}, boom) == {"keyword": "acme"}              # a failed lookup never breaks the search


def test_the_product_query_uses_the_brand_not_a_name_search():
    seen: list = []
    sp.search_products({"keyword": "acme", "category_slug": "bedroom-furniture-beds", "max_price": 400}, run_query=fake_query(seen))
    sql, params = seen[0]
    assert "acme" in params and not any(isinstance(p, str) and p.startswith("%acme") for p in params)


# ---------------- invented budget ----------------
REPLY = "We have a small dining table for under $70:\n\n**Susie** by Furniture of America \u2013 $63.80\n\nWould you like to see more options?"
RESULTS = [{"products": [{"name": "Susie", "price": 63.8}]}]
ASKED = "small cheap table for eating"


def test_the_real_blocked_reply_loses_only_the_made_up_ceiling():
    assert g.check_reply(REPLY, RESULTS, ASKED) == (False, "unverified_price")
    fixed = g.strip_invented_budget(REPLY, RESULTS, ASKED)
    assert fixed.startswith("We have a small dining table:") and "$63.80" in fixed and "$70" not in fixed
    assert g.check_reply(fixed, RESULTS, ASKED) == (True, None)


def test_a_budget_the_customer_gave_is_kept():
    assert g.strip_invented_budget("Here are sofas under $400:\n- Adlai \u2013 $272.01", [{"p": 272.01}], "sofas under $400") is None
    assert g.strip_invented_budget("Sofas $400 or less: Adlai $272.01", [{"p": 272.01}], "show me sofas under $400") is None


def test_other_made_up_phrasings_are_cut_and_real_prices_untouched():
    for phrase in ("for under $70", "under $70", "up to $70", "for less than $70"):
        out = g.strip_invented_budget(f"We have a table {phrase}: Susie $63.80", RESULTS, ASKED)
        assert out is not None and "$70" not in out and "$63.80" in out, phrase
    assert "$70" not in g.strip_invented_budget("Tables at $70 or less: Susie $63.80", RESULTS, ASKED)
    assert g.strip_invented_budget("Susie is $63.80", RESULTS, ASKED) is None


def test_a_fake_price_that_is_not_a_budget_phrase_is_still_blocked():
    reply = "Susie costs $70."                                             # a price claim, not a ceiling: no repair
    assert g.repair_unverified_prices(reply, RESULTS, ASKED) is None


def test_both_repairs_work_together():
    out = g.repair_unverified_prices("Tables for under $70:\n- Susie \u2013 $63", RESULTS, ASKED)
    assert out == "Tables:\n- Susie \u2013 $63.80"


def test_a_reply_with_one_invented_price_is_still_blocked_after_the_repairs(monkeypatch):
    monkeypatch.setattr(router, "known_brands", lambda: [])
    monkeypatch.setitem(g.TOOLS, "search_products", lambda a: {"products": [{"name": "Susie", "price": 63.8}]})
    out = router.answer(ASKED, FakeLLM(call("search_products", sort="price_asc"), say("Tables for under $70: Susie $63.80 and Bob $12")))
    assert out["reply"] == g.FALLBACK and out["blocked"] == "unverified_price"      # Bob's $12 is not real: nothing is sent


def test_router_sends_the_cleaned_reply_not_the_fallback(monkeypatch):
    monkeypatch.setattr(router, "known_brands", lambda: [])
    monkeypatch.setitem(g.TOOLS, "search_products", lambda a: {"products": [{"name": "Susie", "price": 63.8}]})
    out = router.answer(ASKED, FakeLLM(call("search_products", sort="price_asc"), say(REPLY)))
    assert out["blocked"] is None and "$70" not in out["reply"] and "$63.80" in out["reply"]


# ---------------- brand questions are grounded in what the customer said ----------------
KNOWN = [{"slug": "acme", "name": "Acme"}, {"slug": "coaster", "name": "Coaster"}, {"slug": "ashley", "name": "Ashley"},
         {"slug": "signature-design-by-ashley", "name": "Signature Design by Ashley\u2122"}]
COASTER_Q = "what do you have from Coaster under $300"


def test_a_brand_and_price_question_loses_the_category_the_model_invented():
    # the three real calls from eval run 2026-10-16
    assert g.ground_brand_search({"category_slug": "coasters", "max_price": 300}, COASTER_Q, KNOWN) == {"max_price": 300, "brand_slug": "coaster"}
    sofa = {"category_slug": "sofas-and-seating-sofas", "brand_slug": "coaster", "max_price": 300}
    assert g.ground_brand_search(sofa, COASTER_Q, KNOWN) == {"brand_slug": "coaster", "max_price": 300}
    assert g.ground_brand_search({"keyword": "coaster", "max_price": 300}, COASTER_Q, KNOWN) == {"max_price": 300, "brand_slug": "coaster"}


def test_a_product_type_the_customer_did_name_is_kept():
    bed = {"category_slug": "bedroom-furniture-beds", "max_price": 400, "keyword": "acme"}
    assert g.ground_brand_search(bed, "acme beds under $400", KNOWN) == {"category_slug": "bedroom-furniture-beds", "max_price": 400, "brand_slug": "acme"}
    out = g.ground_brand_search({"category_slug": "sofas-and-seating-sofas"}, "show me Ashley sofas", KNOWN)
    assert out == {"category_slug": "sofas-and-seating-sofas", "brand_slug": "ashley"}


def test_the_longest_brand_name_wins_and_other_words_do_not_match():
    out = g.ground_brand_search({"category_slug": "x"}, "Signature Design by Ashley sofas", KNOWN)
    assert out["brand_slug"] == "signature-design-by-ashley" and out["category_slug"] == "x"
    q = "do you sell drink coasters?"                                    # "coasters" is not the brand "Coaster"
    assert g.ground_brand_search({"category_slug": "coasters"}, q, KNOWN) == {"category_slug": "coasters"}
    assert g.ground_brand_search({"category_slug": "x"}, "show me sofas under $400", KNOWN) == {"category_slug": "x"}
    assert g.ground_brand_search({"category_slug": "x"}, COASTER_Q, []) == {"category_slug": "x"}   # no brand list: nothing changes


def test_router_searches_the_brand_not_the_invented_category(monkeypatch):
    seen = []
    monkeypatch.setattr(router, "known_brands", lambda: KNOWN)
    monkeypatch.setitem(g.TOOLS, "search_products", lambda a: (seen.append(a), {"products": [{"name": "Jacotte Bar Stool", "price": 176.0}]})[1])
    out = router.answer(COASTER_Q, FakeLLM(call("search_products", category_slug="sofas-and-seating-sofas", brand_slug="coaster", max_price=300),
                                          say("We have the Jacotte Bar Stool from Coaster for $176.")))
    assert seen == [{"brand_slug": "coaster", "max_price": 300}] and out["blocked"] is None


def test_a_brand_only_question_tells_the_model_not_to_pick_a_product_type_for_the_customer(monkeypatch):
    monkeypatch.setattr(router, "known_brands", lambda: KNOWN)
    monkeypatch.setitem(g.TOOLS, "search_products", lambda a: {"products": [{"name": "Jacotte Bar Stool", "price": 176.0}]})
    llm = FakeLLM(call("search_products", category_slug="sofas-and-seating-sofas", brand_slug="coaster", max_price=300),
                  say("We have the Jacotte Bar Stool from Coaster for $176."))
    router.answer(COASTER_Q, llm)
    shown = [m for m in llm.seen[-1] if m.get("role") == "tool"][-1]["result"]
    assert shown["customer_request"] == g.BRAND_ONLY_NOTE
    assert g.ground_brand_search_ex({"category_slug": "x"}, "acme beds under $400", KNOWN)[1] is False


def test_the_prompt_no_longer_primes_sofas_and_has_a_brand_only_rule():
    assert "(beds, sofas, desks)" not in g.SYSTEM_PROMPT
    assert "names only a brand" in g.SYSTEM_PROMPT