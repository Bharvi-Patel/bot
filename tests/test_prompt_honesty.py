"""The prompt must tell the model (a) to answer from returned policy chunks and (b) not to claim what the store does or does not offer without a source."""
import sjbot.guardrails as g


def test_prompt_requires_answering_from_returned_chunks():
    assert "check whether the chunks directly address what the customer asked" in g.SYSTEM_PROMPT
    assert "do not reply with just a phone or email when the chunks answer the question" in g.SYSTEM_PROMPT
    assert "treat it as not found" in g.SYSTEM_PROMPT
    assert "start your reply by saying you don't have that information" in g.SYSTEM_PROMPT
    assert "Never reply with contact details alone" in g.SYSTEM_PROMPT


def test_prompt_forbids_unsourced_offer_claims():
    assert "Never say the store does or does not offer" in g.SYSTEM_PROMPT
    assert "say you don't have that information" in g.SYSTEM_PROMPT


def test_prompt_uses_category_search_followups_and_health_searches():
    assert "first call list_categories with the singular keyword" in g.SYSTEM_PROMPT
    assert "Only call an item a sofa, bed or desk if it came from that category" in g.SYSTEM_PROMPT
    assert "call the search tool again and answer from its result" in g.SYSTEM_PROMPT
    assert "do not refuse to search" in g.SYSTEM_PROMPT


def test_price_from_the_bots_own_earlier_reply_is_allowed_but_new_prices_are_not():
    assert g.check_reply("The cheapest is $798.", [], "which is cheapest?", "", None, earlier_replies="Sofa set - $798")[0]
    ok, why = g.check_reply("Try sofas up to $150.", [], "show sofas under $100", "", None, earlier_replies="Sofa set - $798")
    assert not ok and why == "unverified_price"


def test_prompt_forbids_made_up_budget_numbers():
    assert "Never write a dollar amount unless the customer said it or a tool result shows it" in g.SYSTEM_PROMPT