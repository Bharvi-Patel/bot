"""Regression tests for the problems found in the first manual chat session (Oct 2)."""
import pytest

from bot import guardrails as g
from bot import router
from bot.llm_base import LLMTurn, ToolCall
from bot.tools.get_shipping_options import get_shipping_options
from bot.tools.search_products import search_products

# the two enabled rows in the real dev database
SHIP = [
    {"zone_name": "USA", "method_name": "Local Shipping", "flat_price": "299", "note": None},
    {"zone_name": "In Store Pick Up", "method_name": "In-Store Pickup", "flat_price": None, "note": None},
]


def ship(args):
    return get_shipping_options(args, run_query=lambda *_: [dict(r) for r in SHIP])


# ---------- "Is in-store pickup available?" said no because 'pickup' did not match the zone 'In Store Pick Up' ----------
@pytest.mark.parametrize("word", ["pickup", "pick up", "In-Store Pickup", "store pickup", "PICKUP"])
def test_pickup_keyword_finds_the_pickup_zone(word):
    out = ship({"zone_keyword": word})
    assert out["found"] is True and [z["zone"] for z in out["zones"]] == ["In Store Pick Up"]
    assert out["zones"][0]["methods"][0]["method"] == "In-Store Pickup"


def test_usa_keyword_still_returns_only_the_usa_zone_and_unknown_words_find_nothing():
    assert [z["zone"] for z in ship({"zone_keyword": "usa"})["zones"]] == ["USA"]
    out = ship({"zone_keyword": "canada"})
    assert out["found"] is False and "no parameters" in out["note"]


def test_symbol_only_keyword_means_no_filter():
    assert len(ship({"zone_keyword": " - "})["zones"]) == 2


# ---------- phone numbers inside policy pages are not the store's phone numbers ----------
PAGE = {"results": [{"title": "Financing", "text": "Visit our Brooklyn showroom or call (718) 384-8413 or mail old@other-store.com"}]}
STORE = '{"phone": "(731) 423-6565", "email": "shop@store.com"}'


def test_number_from_a_policy_page_is_blocked_when_the_page_is_untrusted():
    reply = "Please call (718) 384-8413."
    assert g.check_reply(reply, [PAGE], "", STORE) == (True, None)                         # old behaviour: any tool result counts
    assert g.check_reply(reply, [PAGE], "", STORE, contact_results=[]) == (False, "unknown_phone")
    assert g.check_reply("Write to old@other-store.com", [PAGE], "", STORE, contact_results=[])[1] == "unknown_email"


def test_store_record_numbers_are_still_fine_and_repair_swaps_in_the_real_ones():
    assert g.check_reply("Call (731) 423-6565.", [PAGE], "", STORE, contact_results=[]) == (True, None)
    fixed = g.repair_contacts("Call (718) 384-8413 or old@other-store.com", [], STORE)
    assert "(731) 423-6565" in fixed and "shop@store.com" in fixed and "718" not in fixed


class FakeLLM:
    def __init__(self, *turns):
        self.turns = list(turns)

    def generate(self, messages):
        return self.turns.pop(0)


def test_router_repairs_a_reply_that_copied_a_phone_from_a_policy_page(monkeypatch, tmp_path):
    monkeypatch.setattr(router, "REVIEW_LOG", tmp_path / "review.jsonl")
    monkeypatch.setattr(g, "_STORE_CONTACT_TEXT", STORE)
    monkeypatch.setitem(g.TOOLS, "search_policies", lambda a: PAGE)
    llm = FakeLLM(LLMTurn(tool_calls=[ToolCall("search_policies", {"question": "financing"})]),
                  LLMTurn(text="You can apply in store or call (718) 384-8413."))
    out = router.answer("do you offer financing?", llm)
    assert out["blocked"] is None and "(731) 423-6565" in out["reply"] and "718" not in out["reply"]
    assert "repaired_unknown_phone" in (tmp_path / "review.jsonl").read_text()


def test_phone_from_another_tool_is_still_allowed(monkeypatch):
    monkeypatch.setattr(g, "_STORE_CONTACT_TEXT", STORE)
    monkeypatch.setitem(g.TOOLS, "get_store_info", lambda a, ctx=None: {"phone": "(731) 423-6565"})
    out = router.answer("phone?", FakeLLM(LLMTurn(tool_calls=[ToolCall("get_store_info", {})]), LLMTurn(text="Call (731) 423-6565.")))
    assert out["blocked"] is None


def test_prompt_has_the_pickup_rule():
    assert "In-store pickup" in g.SYSTEM_PROMPT and "only if the result does not list it" in g.SYSTEM_PROMPT


# ---------- "yeah show me other options": a good answer was blocked because it said "under $1,000" ----------
SOFA_ROWS = [{"sku": "S1", "slug": "clearbrooke-sofa-s1", "name": "Clearbrooke Sofa", "price": 455.83, "brand_name": "Acme",
              "width": None, "height": None, "depth": None, "main_image": None, "matching_options": 0}]
OPTIONS_REPLY = "Here are sofas under $1,000: Clearbrooke Sofa, $455.83.\n/product/clearbrooke-sofa-s1"


def test_search_result_lists_the_price_limits_it_used():
    from tests.api_fakes import CatalogClient, item
    client = CatalogClient([item("S1", "Clearbrooke Sofa", 455.83, "Acme", "clearbrooke-sofa-s1")], brands=[{"slug": "acme", "name": "Acme"}])
    out = search_products({"keyword": "sofa", "max_price": 1000}, client=client)
    assert out["applied_filters"] == {"max_price": 1000}
    assert "applied_filters" not in search_products({"keyword": "sofa"}, client=client)
    assert g.check_reply(OPTIONS_REPLY, [out], "yeah show me other options", "{}") == (True, None)


def test_a_limit_nobody_gave_or_applied_is_still_blocked():
    out = {"count": 1, "products": [dict(SOFA_ROWS[0])]}
    assert g.check_reply(OPTIONS_REPLY, [out], "yeah show me other options", "{}") == (False, "unverified_price")


def test_figures_the_customer_typed_in_earlier_turns_may_be_repeated(monkeypatch, tmp_path):
    monkeypatch.setattr(router, "REVIEW_LOG", tmp_path / "review.jsonl")
    monkeypatch.setattr(g, "_STORE_CONTACT_TEXT", "{}")
    out = {"count": 1, "products": [dict(SOFA_ROWS[0])]}
    monkeypatch.setitem(g.TOOLS, "search_products", lambda a, ctx=None: out)
    history = [{"role": "user", "text": "I have about $1,000 to spend"}, {"role": "assistant", "text": "Great, what room is it for?"}]
    llm = FakeLLM(LLMTurn(tool_calls=[ToolCall("search_products", {"keyword": "sofa"})]), LLMTurn(text=OPTIONS_REPLY))
    assert router.answer("living room", llm, history)["blocked"] is None


def test_prompt_handles_other_options_and_why_questions():
    assert "applied_filters" in g.SYSTEM_PROMPT and "say what you widened" in g.SYSTEM_PROMPT
    assert "why you could not answer" in g.SYSTEM_PROMPT


# ---------- "show beds under $500" found nothing: the keyword was the plural 'beds' and names say 'Bed' ----------
def test_prompt_says_to_try_the_category_before_saying_none():
    assert "singular keyword" in g.SYSTEM_PROMPT and "before saying we have none" in g.SYSTEM_PROMPT


# ---------- "show beds under $500": list_categories('bed') returned Bedroom, Mattresses and Bedding, ... and cut off 'Beds' ----------
from bot.tools.list_categories import list_categories
from tests.api_fakes import TreeClient

_T = [  # id, parent, slug, title: the bed-related rows of the real category tree
    (77, 0, "bedroom", "Bedroom"), (50, 0, "mattresses-and-bedding", "Mattresses and Bedding"), (91, 0, "kids-and-nursery", "Kids and Nursery"),
    (71, 0, "pets", "Pets"), (6, 77, "bedroom-bedroom-furniture", "Bedroom Furniture"), (185, 77, "bedroom-sets", "Bedroom Sets"),
    (79, 50, "mattresses-and-bedding-bedding-accessories", "Bedding Accessories"), (192, 50, "mattresses-and-bedding-sets", "Bedding Sets"),
    (67, 91, "kids-furniture", "Kids Furniture"), (63, 91, "nursery", "Nursery"), (69, 91, "kids-bedding", "Kids Bedding"),
    (44, 6, "bedroom-furniture-beds", "Beds"), (118, 6, "bedroom-furniture-bedroom-benches", "Bedroom Benches"),
    (75, 6, "bedroom-furniture-bedroom-chairs", "Bedroom Chairs"), (8, 79, "bedding-accessories-bed-pillows", "Bed Pillows"),
    (30, 67, "kids-furniture-beds", "Beds"), (66, 67, "kids-furniture-bunk-beds", "Bunk Beds"), (60, 63, "nursery-beds", "Beds"),
    (92, 71, "pets-beds", "Beds"),
]
TREE = [{"category_id": i, "parent_id": p, "slug": s, "title": t} for i, p, s, t in _T]


def cats(args):
    return list_categories(args, client=TreeClient(TREE))


@pytest.mark.parametrize("word", ["bed", "beds", "Beds"])
def test_the_beds_categories_are_not_cut_off_by_longer_matches(word):
    out = cats({"keyword": word})
    slugs = [m["slug"] for m in out["matches"]]
    assert "bedroom-furniture-beds" in slugs
    paths = [m["path"] for m in out["matches"]]
    assert "Bedroom > Bedroom Furniture > Beds" in paths              # the path tells furniture beds from pet beds
    assert out["matches"][0]["title"] in {"Bed Pillows", "Beds", "Bunk Beds"}   # whole-word matches come first
    assert out["total_matches"] == len([r for r in TREE if "bed" in r["title"].lower()]) and "Showing the best 10" in out["note"]


def test_a_small_match_list_has_no_total_or_note():
    out = cats({"keyword": "pet"})
    assert [m["slug"] for m in out["matches"]] == ["pets"] and "total_matches" not in out and out["note"] is None


def test_prompt_prefers_the_most_specific_category():
    assert "most specific category" in g.SYSTEM_PROMPT and "never a top-level one" in g.SYSTEM_PROMPT


def test_prompt_keeps_greetings_short_and_contacts_on_request():
    assert "Greetings and thanks" in g.SYSTEM_PROMPT and "from your own knowledge" in g.SYSTEM_PROMPT



# ---------- "sofas under $250": the model searched category_slug 'sofas', which does not exist, and got "no products" ----------
def sofa_catalog(known=(), close_rows=()):
    from tests.api_fakes import CatalogClient, TreeClient
    return CatalogClient(by_category={k: [] for k in known}, tree=TreeClient(close_rows).categories())


def test_an_invented_category_slug_is_an_error_with_suggestions_not_an_empty_result():
    close = [{"category_id": 1, "parent_id": 0, "slug": "living-room-sofas", "title": "Sofas"}]
    out = search_products({"category_slug": "sofas", "max_price": 400}, client=sofa_catalog({"living-room-sofas"}, close))
    assert out["error"] == "unknown_category" and "'sofas'" in out["note"] and "list_categories" in out["note"]
    assert out["did_you_mean"] == [{"slug": "living-room-sofas", "title": "Sofas"}] and "products" not in out


def test_a_real_category_with_no_matches_is_still_a_plain_empty_result():
    out = search_products({"category_slug": "living-room-sofas", "max_price": 50}, client=sofa_catalog({"living-room-sofas"}))
    assert out["count"] == 0 and "error" not in out and out["note"].startswith("No products matched")


def test_results_are_never_second_guessed_when_products_were_found():
    from tests.api_fakes import CatalogClient, item
    c = CatalogClient(by_category={"whatever": [item("S1", "Clearbrooke Sofa", 455.83)]})
    out = search_products({"category_slug": "whatever", "max_price": 500}, client=c)
    assert out["count"] == 1 and not any(call[0] == "categories" for call in c.calls)


def test_prompt_forbids_guessing_slugs():
    assert "never guess or shorten a slug" in g.SYSTEM_PROMPT

# ---------- "cheapest?" after a capped, name-sorted list; "other options" after an empty result ----------
def test_truncated_store_order_list_warns_not_to_call_anything_cheapest():
    from tests.api_fakes import CatalogClient, item
    c = CatalogClient([item(f"S{i}", f"Sofa {i}", 455.83) for i in range(9)], brands=[])
    out = search_products({"keyword": "sofa", "max_price": 1000}, client=c)
    assert out["sorted_by"].startswith("store order") and "NOT sorted by price" in out["note"] and "sort=price_asc" in out["note"]
    cheap = search_products({"keyword": "sofa", "sort": "price_asc"}, client=c)
    assert "first one is the cheapest" in cheap["note"]


def test_empty_result_under_a_limit_returns_the_real_nearest_products():
    from tests.api_fakes import CatalogClient, item
    c = CatalogClient([item("S1", "Clearbrooke Sofa", 455.83, "Acme", "clearbrooke-sofa-s1")], brands=[])
    out = search_products({"keyword": "sofa", "max_price": 100}, client=c)
    assert out["count"] == 0 and out["cheapest_over_limit"][0]["price"] == 455.83
    assert "Never invent" in out["note"]
    assert g.check_reply("Nothing under $100, but the Clearbrooke Sofa is $455.83.", [out], "show me sofas under $100", "{}") == (True, None)
