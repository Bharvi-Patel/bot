from bot.tools.search_products import _is_a


def p(name, tags):
    return {"name": name, "tags": tags}


def test_a_word_only_in_cushioned_does_not_make_a_nightstand_a_cushion():
    assert _is_a(p("Alzir", "alzir nightstand, bedroom nightstand, cushioned nightstand"), ["cushion"]) is False
    assert _is_a(p("Elnath", "beige fabric bench, cozy seating, dining bench"), ["cushions"]) is False


def test_the_real_type_matches_by_name_tag_plural_or_two_word_spelling():
    assert _is_a(p("Velvet Throw Cushion Set", "decor"), ["cushion"]) is True
    assert _is_a(p("Amalia Night Stand White", "Bedroom, Nightstands"), ["nightstand"]) is True
    assert _is_a(p("B3000 - Marble Top Nightstand", "bed, bedroom furniture"), ["nightstands"]) is True
    assert _is_a(p("Brena", "brena throw pillows, accent decor"), ["pillow"]) is True       # the catalog's "<name> <type>" tag
    assert _is_a(p("Alzir", "alzir nightstand, cushioned nightstand"), ["nightstand"]) is True


def test_every_word_must_match_and_untagged_products_cannot_be_judged():
    assert _is_a(p("Oak Dining Table", "dining room, tables"), ["dining", "table"]) is True
    assert _is_a(p("Oak Coffee Table", "living room, tables"), ["dining", "table"]) is False
    assert _is_a(p("Daisy", ""), ["mattress"]) is None


def test_loose_feature_and_category_tags_do_not_count():
    assert _is_a(p("Northwich", "5 piece dining set, foam cushions"), ["cushion"]) is False
    assert _is_a(p("Orla", "orla dining chair, black foam cushion chair"), ["cushion"]) is False
    assert _is_a(p("Luxo Vanity Base Top White", "Rugs and Decor, vanity"), ["rug"]) is False
    assert _is_a(p("Bird Of Paradise", "euro pillow top, mattress"), ["pillow"]) is False
