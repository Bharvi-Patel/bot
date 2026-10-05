"""Regression tests for the problems found in the first manual chat session (Oct 2)."""
import pytest

from sjbot import guardrails as g
from sjbot import router
from sjbot.llm_base import LLMTurn, ToolCall
from sjbot.tools.get_shipping_options import get_shipping_options
from sjbot.tools.search_products import search_products

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


# ---------- "Will a sofa fit on an 80 inch wall?" answered "no sofas in the catalog" ----------
def test_empty_width_search_does_not_claim_the_catalog_has_none():
    def db(sql, params=()):
        return [{"n": 40}] if "COUNT(DISTINCT" in sql else []
    out = search_products({"keyword": "sofa", "max_width_in": 80}, run_query=db)
    assert out["count"] == 0 and out["without_size_data"] == 40
    assert "Do NOT say the catalog has no such products" in out["note"] and "size_note" in out


def test_empty_search_without_width_keeps_the_plain_note():
    out = search_products({"keyword": "zzz"}, run_query=lambda *_: [])
    assert out["note"].startswith("No products matched") and "Do NOT" not in out["note"]


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
    monkeypatch.setitem(g.TOOLS, "get_store_info", lambda a: {"phone": "(731) 423-6565"})
    out = router.answer("phone?", FakeLLM(LLMTurn(tool_calls=[ToolCall("get_store_info", {})]), LLMTurn(text="Call (731) 423-6565.")))
    assert out["blocked"] is None


def test_prompt_has_the_pickup_rule():
    assert "In-store pickup" in g.SYSTEM_PROMPT and "only if the result does not list it" in g.SYSTEM_PROMPT