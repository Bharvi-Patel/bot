from bot.product_cards import cards_for_reply

SEARCH = {"count": 3, "products": [
    {"sku": "M1", "slug": "shapesense-ortho-queen", "name": "Wakefit ShapeSense Orthopedic Memory Foam Mattress", "price": 7688.0,
     "brand_name": "Wakefit", "main_image": "https://cdn.example.com/m1.jpg"},
    {"sku": "M2", "slug": "duroflex-livein", "name": "Duroflex LiveIn Duropedic Mattress", "price": None, "price_varies": True,
     "brand_name": "Duroflex", "main_image": None},
    {"sku": "M3", "slug": "plain-foam", "name": "Plain Foam Mattress", "price": 300.0, "brand_name": None, "main_image": None},
]}


def test_only_products_the_reply_names_in_reply_order():
    reply = "Try the Duroflex LiveIn Duropedic Mattress, or the Wakefit ShapeSense Orthopedic Memory Foam Mattress at $7,688."
    cards = cards_for_reply(reply, [SEARCH])
    assert [c["slug"] for c in cards] == ["duroflex-livein", "shapesense-ortho-queen"]
    wake = cards[1]
    assert wake == {"sku": "M1", "slug": "shapesense-ortho-queen", "name": "Wakefit ShapeSense Orthopedic Memory Foam Mattress",
                    "brand": "Wakefit", "url": "/p/shapesense-ortho-queen", "image": "https://cdn.example.com/m1.jpg",
                    "price": 7688.0, "price_from": None, "note": None}
    assert cards[0]["price"] is None and cards[0]["price_from"] is None        # option-priced: no single price on the card


def test_shortened_name_and_link_match():
    assert [c["slug"] for c in cards_for_reply("The Wakefit ShapeSense Orthopedic is firm.", [SEARCH])] == ["shapesense-ortho-queen"]
    assert [c["slug"] for c in cards_for_reply("See [this one](/p/plain-foam).", [SEARCH])] == ["plain-foam"]


def test_ambiguous_short_name_gets_no_card():
    two = {"products": [{"slug": "a", "name": "Ashley Queen Panel Bed Brown", "price": 1.0},
                        {"slug": "b", "name": "Ashley Queen Panel Bed White", "price": 2.0}]}
    assert cards_for_reply("The Ashley Queen Panel Bed is popular.", [two]) == []


def test_details_fill_price_from_and_image():
    details = {"found": True, "product": {"sku": "M2", "slug": "duroflex-livein", "name": "Duroflex LiveIn Duropedic Mattress",
                                          "brand": "Duroflex", "price": None, "main_image": "https://cdn.example.com/m2.jpg"},
               "price_range": {"min": 499.0, "max": 899.0}}
    card = cards_for_reply("The Duroflex LiveIn Duropedic Mattress starts at $499.", [SEARCH, details])[0]
    assert card["price_from"] == 499.0 and card["price"] is None and card["image"] == "https://cdn.example.com/m2.jpg"


def test_recommend_products_rows():
    rec = {"found": True, "products": [{"sku": "R1", "name": "Cloud Sofa", "price": 999.0, "page": "/p/cloud-sofa",
                                        "live": {"slug": "cloud-sofa", "main_image": "https://cdn.example.com/s.jpg", "brand": "Acme"}}]}
    card = cards_for_reply("I'd suggest the Cloud Sofa.", [rec])[0]
    assert card["url"] == "/p/cloud-sofa" and card["image"] == "https://cdn.example.com/s.jpg" and card["brand"] == "Acme"


def test_nothing_named_or_bad_slug():
    assert cards_for_reply("We have mattresses.", [SEARCH]) == []
    assert cards_for_reply("Evil Thing", [{"products": [{"slug": "../x", "name": "Evil Thing"}]}]) == []



def _daisy(slug, price, image=None):
    return {"slug": slug, "name": "Daisy", "price": price, "sku": slug.upper(), "brand_name": "Furniture Of America", "main_image": image}


def test_same_named_products_follow_the_order_the_reply_lists_their_prices():
    from bot.product_cards import cards_for_reply
    results = [{"products": [_daisy("daisy-q", 479.99), _daisy("daisy-t", 279.99), _daisy("daisy-f", 369.99)]}]
    reply = "1. **Daisy** - $279.99\n2. **Daisy** - $369.99\n3. **Daisy** - $479.99"
    assert [c["slug"] for c in cards_for_reply(reply, results)] == ["daisy-t", "daisy-f", "daisy-q"]


def test_the_text_drops_image_urls_empty_links_and_carded_page_links():
    from bot.product_cards import cards_for_reply, tidy_reply_for_cards
    results = [{"products": [_daisy("daisy-t", 279.99, "https://cdn.x/foa/images/product/a.webp"), _daisy("daisy-f", 369.99)]}]
    reply = ("Here are two:\n1. **Daisy** - $279.99 - <https://cdn.x/foa/images/product/a.webp>\n"
             "2. [Daisy](/p/daisy-f) - $369.99 - <>\nSee our [return policy](/blogs/returns).")
    out = tidy_reply_for_cards(reply, cards_for_reply(reply, results))
    assert out == "Here are two:\n1. **Daisy** - $279.99\n2. Daisy - $369.99\nSee our [return policy](/blogs/returns)."


def test_without_cards_the_reply_is_left_alone():
    from bot.product_cards import tidy_reply_for_cards
    assert tidy_reply_for_cards("x <https://cdn.x/a.webp>", []) == "x <https://cdn.x/a.webp>"
