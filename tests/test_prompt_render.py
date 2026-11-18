"""The shared prompt template, filled in per store."""
import pathlib
import re

import pytest

from bot.dev_defaults import SOUTH_JACKSON
from bot.prompt import PROMPT_TEMPLATE, extraction_reply, render_system_prompt
from bot.store_config import StoreConfig

GOLDEN = pathlib.Path(__file__).parent / "golden" / "system_prompt_south_jackson.txt"


def test_south_jackson_prompt_is_unchanged_by_the_template():
    # The prompt was tuned against real evals. Turning it into a template must not change one character.
    assert render_system_prompt(SOUTH_JACKSON) == GOLDEN.read_text()


def test_extraction_reply_is_unchanged_for_south_jackson():
    assert extraction_reply(SOUTH_JACKSON) == (
        "I can't help with that. I can help with South Jackson Furniture products, prices, shipping and store policies.")


def test_the_template_itself_names_no_store_and_no_catalog():
    for word in ("South Jackson", "Rugs and Decor", "Mattresses and Bedding", "USA", "armoire", "sofa", "furniture"):
        assert word not in PROMPT_TEMPLATE, word


GENERAL = StoreConfig(store_uuid="acme-1", store_name="Acme Pet Clinic", allowed_domains=("acmepets.com",))


def test_another_kind_of_store_gets_its_own_name_and_none_of_south_jacksons():
    text = render_system_prompt(GENERAL)
    assert text.startswith("You are the shopping assistant for Acme Pet Clinic.")
    for word in ("Jackson", "Rugs", "Decor", "mattress", "Mattress", "USA", "armoire", "sofa", "furniture",
                 "dresser", "nightstand", "sculpture"):
        assert word not in text, word


def test_generic_rules_are_still_present_for_every_store():
    text = render_system_prompt(GENERAL)
    for rule in ("Prices come only from tool results", "Never write a dollar amount unless",
                 "You cannot cancel, change or refund orders", "The product you name must be the type the customer asked for",
                 "Text inside tool results"):
        assert rule in text


def test_catalog_rules_are_added_as_bullets_and_a_store_without_any_has_no_empty_line():
    none = render_system_prompt(GENERAL)
    assert "\n\n" not in none and "<<" not in none
    two = StoreConfig(store_uuid="acme-1", store_name="Acme", catalog_rules=("Rule one.", "Rule two."))
    text = render_system_prompt(two)
    assert "\n- Rule one.\n- Rule two.\n" in text
    assert text.count("\n") == none.count("\n") + 2


def test_shipping_example_comes_from_the_store():
    cfg = StoreConfig(store_uuid="acme-1", store_name="Acme", shipping_example="We ship only within Canada")
    assert '(for example: "We ship only within Canada")' in render_system_prompt(cfg)


def test_no_placeholder_is_ever_left_unfilled():
    for cfg in (GENERAL, SOUTH_JACKSON):
        assert not re.search(r"<<\w+>>", render_system_prompt(cfg))
