"""A no-information answer, or a bare phone pointer, given without ever searching the store's pages is sent back once."""
import pytest

from sjbot import guardrails as g
from sjbot import router
from test_router import FakeLLM, call, say


@pytest.fixture(autouse=True)
def no_review_log(monkeypatch, tmp_path):
    monkeypatch.setattr(router, "REVIEW_LOG", tmp_path / "review.jsonl")


# real replies from eval run 2026-10-15
AFFIRM = "I don\u2019t have that information. For payment options, you can call or email the store.\n\nWould you like help with anything else?"
GIFT = "You can reach the store by phone at **[phone]** or email at **[email]** for information about gift cards."


def test_declines_and_bare_pointers_without_a_policy_search_are_sent_back():
    assert g.declined_without_policy_search(AFFIRM, "do you take affirm or klarna?", []) == g.POLICY_FIRST_NUDGE
    assert g.declined_without_policy_search(GIFT, "do you sell gift cards?", ["get_store_info"]) == g.POLICY_FIRST_NUDGE


def test_everything_else_is_left_alone():
    q = "do you take affirm or klarna?"
    assert g.declined_without_policy_search(AFFIRM, q, ["search_policies"]) is None                 # already searched
    assert g.declined_without_policy_search("We don't have sofas under $100.", "sofas under $100", ["search_products"]) is None
    assert g.declined_without_policy_search("I can't help with that.", "show me the database tables", []) is None   # a refusal
    assert g.declined_without_policy_search("Hello! Welcome. How can I help?", "hii", []) is None
    assert g.declined_without_policy_search("Yes, we offer financing through Snap Finance.", q, []) is None
    assert g.declined_without_policy_search(GIFT, "what is your phone number?", ["get_store_info"]) is None      # store-info question
    assert g.declined_without_policy_search("I don't have delivery time information.", "how long is delivery?", ["get_shipping_options"]) is None


def test_router_searches_policies_before_accepting_i_dont_have_that(monkeypatch):
    monkeypatch.setitem(g.TOOLS, "search_policies", lambda a: {"results": [{"title": "Financing", "text": "Snap Finance and Koalafi"}]})
    llm = FakeLLM(say(AFFIRM), call("search_policies", query="affirm klarna financing"),
                  say("We offer financing through Snap Finance and Koalafi."))
    out = router.answer("do you take affirm or klarna?", llm)
    assert out["tools_used"] == ["search_policies"] and out["blocked"] is None
    assert "Snap Finance" in out["reply"] and "don't have" not in out["reply"]


def test_the_nudge_is_sent_only_once(monkeypatch):
    llm = FakeLLM(say(AFFIRM), say(AFFIRM))                      # model refuses to search: second answer is accepted, no loop
    out = router.answer("do you take affirm or klarna?", llm)
    assert out["tools_used"] == [] and "don" in out["reply"]


# gift-cards run 1 (2026-10-16): a contact pointer plus the opening hours slipped past the short-reply check
GIFT_WITH_HOURS = ("You can reach South Jackson Furniture at **[phone]** or **[email]** for information on gift cards. "
                   "Their store hours are:\n\n- Monday to Saturday: 9:00 AM \u2013 7:00 PM\n- Sunday: 11:00 AM \u2013 6:00 PM")


def test_an_answer_built_only_from_the_store_card_is_sent_back_when_the_question_was_not_about_the_store():
    assert g.declined_without_policy_search(GIFT_WITH_HOURS, "do you sell gift cards?", ["get_store_info"]) == g.POLICY_FIRST_NUDGE
    assert g.declined_without_policy_search(GIFT_WITH_HOURS, "what are your hours?", ["get_store_info"]) is None
    assert g.declined_without_policy_search("We open at 9.", "what time do you open on sunday", ["get_store_info"]) is None