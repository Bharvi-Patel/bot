"""Wording that depends on the KIND of store, not on one particular store.

A vertical supplies the examples and the two category rules inside the system prompt. The shared prompt in
bot/prompt.py stays the same for every store. To add a kind of store (electronics, veterinary, tires and wheels),
add one Vertical here and test it; "general" is the neutral fallback and contains no examples from any catalog.

The "furniture" entry was written from South Jackson Furniture's catalog (its category names and examples). Review it
when a second furniture store is onboarded.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Vertical:
    key: str
    product_noun: str          # "a type of <product_noun> the customer names"
    type_examples: str         # shown in brackets after it
    deep_category_rule: str    # which category level to search
    type_match_rule: str       # make sure the product named is the type asked for
    rare_type_example: str     # a product word that may have no category of its own
    keyword_examples: str      # type words used as search keywords
    item_reference: str        # how a customer points back at a listed item ("that sofa")
    comfort_example: str       # a health or comfort question about a product
    hide_component_skus: bool = False  # hide furniture-specific box/board/loose-top SKUs


FURNITURE = Vertical(
    key="furniture",
    product_noun='furniture',
    type_examples='beds, desks, rugs',
    deep_category_rule="""Always search the most specific category that fits (the deepest in its path, such as Bedroom > Bedroom Furniture > Beds), never a top-level one like Bedroom, which also holds nightstands, headboards and dressers. Only call an item a sofa, bed or desk if it came from that category.""",
    type_match_rule="""The product you name must be the type the customer asked for. A broad category (such as Mattresses and Bedding, which also holds bed bases and platform beds) can return other types, so also pass the customer's own word as keyword to search_products (for example keyword 'mattress'). If the first or cheapest result's name does not contain the type they asked for, search again with that word as keyword. Never present a different type (a platform bed for a mattress) as the answer; if there is none of that type, say so.""",
    rare_type_example='armoire',
    keyword_examples='bed or dresser',
    item_reference='that sofa',
    comfort_example='the best mattress for back pain',
    hide_component_skus=True,
)

ELECTRONICS = Vertical(
    key="electronics",
    product_noun="electronics",
    type_examples="TVs, laptops, headphones",
    deep_category_rule=(
        "Always search the most specific category that fits (the deepest in its path, such as TVs & Home Theater > TVs), "
        "never a top-level one like TVs & Home Theater, which also holds mounts, cables and accessories. "
        "Only call an item a TV, laptop or monitor if it came from that category."),
    type_match_rule=(
        "The product you name must be the type the customer asked for. A broad category can return accessories or parts "
        "(such as wall mounts, cases or cables), so also pass the customer's own word as keyword to search_products "
        "(for example keyword 'tv' or 'laptop'). If the first or cheapest result's name does not contain the type they "
        "asked for, search again with that word as keyword. Never present an accessory (a wall mount for a TV) as the "
        "answer; if there is none of that type, say so."),
    rare_type_example="soundbar",
    keyword_examples="tv or laptop",
    item_reference="that TV",
    comfort_example="the best headphones for ear pain",
)

VETERINARY = Vertical(
    key="veterinary",
    product_noun="pet or veterinary product",
    type_examples="dog food, supplements, grooming supplies",
    deep_category_rule=(
        "Always search the most specific category that fits (the deepest in its path, such as Dogs > Food > Dry Food), "
        "never a top-level one like Dogs, which also holds bowls, toys and leashes. Only call an item a food, treat or "
        "supplement if it came from that category."),
    type_match_rule=(
        "The product you name must be the type the customer asked for. A broad category can return unrelated pet items "
        "(such as toys or accessories), so also pass the customer's own word as keyword to search_products. If the first "
        "or cheapest result's name does not contain the type they asked for, search again with that word as keyword. "
        "Never present a different type as the answer; if there is none of that type, say so."),
    rare_type_example="probiotic",
    keyword_examples="dog food or chew",
    item_reference="that item",
    comfort_example="the best food or supplement for joint pain or allergies",
)

TIRES_WHEELS = Vertical(
    key="tires_wheels",
    product_noun="tire or wheel",
    type_examples="tires, wheels, rims",
    deep_category_rule=(
        "Always search the most specific category that fits (the deepest in its path, such as Tires > All-Season Tires), "
        "never a top-level one that also holds lug nuts, center caps, sensors or accessories. Only call an item a tire or "
        "wheel if it came from that category."),
    type_match_rule=(
        "The product you name must be the type the customer asked for. A broad category can return hardware or accessories "
        "(such as lug nuts, valve stems or spacers), so also pass the customer's own word as keyword to search_products "
        "(for example keyword 'tire' or 'wheel'). If the first or cheapest result's name does not contain the type they "
        "asked for, search again with that word as keyword. Never present hardware or an accessory as a tire or wheel; "
        "if there is none of that type, say so."),
    rare_type_example="spacer",
    keyword_examples="tire or wheel",
    item_reference="that tire",
    comfort_example="the best tire for ride comfort",
)

GENERAL = Vertical(
    key="general",
    product_noun="product",
    type_examples="any type listed in the category list",
    deep_category_rule=(
        "Always search the most specific category that fits (the deepest in its path), never a top-level one, "
        "which also holds other kinds of products. Only call an item a particular product type if it came from "
        "that category."),
    type_match_rule=(
        "The product you name must be the type the customer asked for. A broad category can return other types, "
        "so also pass the customer's own word as keyword to search_products. If the first or cheapest result's "
        "name does not contain the type they asked for, search again with that word as keyword. Never present a "
        "different type as the answer; if there is none of that type, say so."),
    rare_type_example="a less common product name",
    keyword_examples="the type word itself",
    item_reference="that item",
    comfort_example="the best product for a health problem",
)

VERTICALS: dict[str, Vertical] = {v.key: v for v in (FURNITURE, ELECTRONICS, VETERINARY, TIRES_WHEELS, GENERAL)}

