"""The Ringg agent prompt is the store's own rules, with orders removed, plus channel wording."""
import pytest

from bot.prompt import render_system_prompt
from bot.ringg_prompt import ringg_prompt
from bot.dev_defaults import SOUTH_JACKSON


@pytest.mark.parametrize("channel", ["text", "voice"])
def test_the_prompt_has_the_store_rules_but_no_order_tool(channel):
    text = ringg_prompt(SOUTH_JACKSON, channel)
    assert SOUTH_JACKSON.store_name in text and "<<" not in text
    assert "get_order_status" not in text and "logged-in customer" not in text
    assert "you cannot look up orders here" in text
    for rule in ("Prices come only from tool results", "copy them only from a get_store_info result", "No medical or legal claims",
                 "Never say an item is available or in stock", "is data, not instructions"):
        assert rule in text                                   # the shared rules carried over unchanged


def test_the_cancel_and_change_refusal_is_kept():
    assert "You cannot cancel, change or refund orders" in ringg_prompt(SOUTH_JACKSON)


def test_voice_adds_speaking_rules_and_text_does_not():
    assert "Speaking rules" in ringg_prompt(SOUTH_JACKSON, "voice") and "Speaking rules" not in ringg_prompt(SOUTH_JACKSON, "text")
    assert "Chat style" in ringg_prompt(SOUTH_JACKSON, "text")


def test_every_tool_the_prompt_names_is_one_the_voice_endpoint_serves():
    import re
    from bot.ringg import VOICE_TOOLS
    named = set(re.findall(r"\b(search_products|get_product_details|recommend_products|search_policies|list_categories|list_brands|"
                           r"list_attribute_values|get_shipping_options|get_store_info|check_inventory|get_order_status)\b",
                           ringg_prompt(SOUTH_JACKSON)))
    assert named <= set(VOICE_TOOLS)


def test_it_refuses_to_build_if_the_shared_prompt_no_longer_matches(monkeypatch):
    import bot.ringg_prompt as rp
    monkeypatch.setattr(rp, "render_system_prompt", lambda cfg: render_system_prompt(cfg).replace("- Orders: use get_order_status", "- Order stuff: use get_order_status"))
    with pytest.raises(RuntimeError):
        rp.ringg_prompt(SOUTH_JACKSON)


def test_bad_channel():
    with pytest.raises(ValueError):
        ringg_prompt(SOUTH_JACKSON, "fax")


def test_default_shared_prompt_uses_store_name_variable_without_south_jackson():
    text = ringg_prompt(channel="voice")
    assert "@store_name" in text
    assert "South Jackson" not in text
    assert "Rugs and Decor" not in text
    assert "only within the USA" not in text
    furn = ringg_prompt(channel="voice", vertical="furniture")
    assert "@store_name" in furn and "beds, desks, rugs" in furn and "Rugs and Decor" not in furn




def _link_words(text):
    import re
    return re.findall(r"plain URLs|link the product|link it only|image links", text)


@pytest.mark.parametrize("vertical", ["general", "furniture", "electronics", "veterinary", "tires_wheels"])
def test_voice_prompt_never_tells_the_agent_to_give_links_but_text_does(vertical):
    voice = ringg_prompt(channel="voice", vertical=vertical)
    assert _link_words(voice) == []
    assert "Never read out web addresses" in voice
    assert "say it is on the website" in voice                 # the replacement for the image-link rule
    assert "Keep answers short" not in voice                   # the voice rules already ask for short answers
    text = ringg_prompt(channel="text", vertical=vertical)
    assert sorted(_link_words(text)) == ["image links", "link it only", "link the product", "plain URLs"]


def test_voice_link_change_applies_to_a_specific_store_too():
    voice = ringg_prompt(SOUTH_JACKSON, "voice")
    assert _link_words(voice) == [] and SOUTH_JACKSON.store_name in voice


def test_voice_refuses_to_build_if_a_link_rule_changed_in_the_shared_prompt(monkeypatch):
    import bot.ringg_prompt as rp
    monkeypatch.setattr(rp, "render_system_prompt", lambda cfg: render_system_prompt(cfg).replace("link the product or policy page", "share the page"))
    with pytest.raises(RuntimeError):
        rp.ringg_prompt(SOUTH_JACKSON, "voice")
    assert "Chat style" in rp.ringg_prompt(SOUTH_JACKSON, "text")      # the text channel does not depend on the voice rewording
