from bot.product_cards import cards_for_reply, split_reply_for_cards


def test_each_product_line_becomes_its_cards_note_and_leaves_the_intro():
    results = [{"products": [
        {"slug": "kenyon-q", "name": "Kenyon 7-Inch Queen Firm Foam Mattress", "price": 459.99},
        {"slug": "sommet-f", "name": "Sommet 13-Inch Full Hybrid Mattress", "price": 759.99}]}]
    reply = ("Here are a few mattresses:\n\n"
             "**Kenyon 7-Inch Queen Firm Foam Mattress**\n$459.99 - Firm foam with a bamboo-blend cover.\n\n"
             "- **Sommet 13-Inch Full Hybrid Mattress** - $759.99 - Hybrid with a memory-foam top.\n\n"
             "Want more options?")
    display, cards = split_reply_for_cards(reply, cards_for_reply(reply, results))
    assert display == "Here are a few mattresses:\n\nWant more options?"
    assert [c["note"] for c in cards] == ["Firm foam with a bamboo-blend cover", "Hybrid with a memory-foam top"]


def test_a_bare_name_and_price_line_keeps_the_stores_own_description():
    results = [{"found": True, "products": [{"sku": "S1", "name": "Elnath", "price": 319.99, "page": "/p/elnath",
                "live": {"name": "Elnath", "summary": "Elnath",
                         "description": "A plush square cushion with a velvet cover and a hidden zip."}}]}]
    reply = "Here are some cushions:\n- **Elnath** - $319.99"
    display, cards = split_reply_for_cards(reply, cards_for_reply(reply, results))
    assert display == "Here are some cushions:"
    assert cards[0]["note"] == "A plush square cushion with a velvet cover and a hidden zip."


def test_a_reply_without_product_lines_is_left_whole():
    results = [{"products": [{"slug": "elnath", "name": "Elnath Cushion Set", "price": 319.99}]}]
    reply = "Our Elnath Cushion Set is $319.99 and comes in gray."
    display, cards = split_reply_for_cards(reply, cards_for_reply(reply, results))
    assert cards and display == ""       # the one line was the product; the widget then shows just the card



def test_a_name_gets_only_as_many_cards_as_the_reply_mentions_it():
    results = [{"products": [{"slug": "north-a", "name": "Northwich", "price": 2639.99},
                             {"slug": "north-b", "name": "Northwich", "price": 3169.99}]}]
    cards = cards_for_reply("- **Northwich** - $2,639.99", results)
    assert [c["slug"] for c in cards] == ["north-a"]


def test_cards_without_a_note_get_the_stores_description_and_a_failure_is_skipped():
    from bot.product_cards import add_store_descriptions
    cards = [{"slug": "a", "name": "Elnath", "note": None}, {"slug": "b", "name": "Alzir", "note": None},
             {"slug": "c", "name": "Kept", "note": "Already has one"}]
    def details(slug):
        if slug == "b":
            raise RuntimeError("api down")
        return {"found": True, "product": {"slug": slug, "summary": "Elnath", "description": "Plush velvet cushions, set of two."}}
    out = add_store_descriptions(cards, details)
    assert [c["note"] for c in out] == ["Plush velvet cushions, set of two.", None, "Already has one"]
