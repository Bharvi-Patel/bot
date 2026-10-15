"""HELD-OUT question set for B3 roadmap stages 1 and 2:  Searches  ->  AI interactions.

IN SCOPE   product search and site search (store info, shipping, policies, brands, categories), and the chat around them
           (follow-ups, refining a search, comparing two products, recommendations, "I need a table" style vague asks).
OUT OF SCOPE  Ringg.AI / voice, leads, inquiries, add-to-cart or any other action. The 2 "boundary" cases at the end only check
           that the bot does NOT pretend it did one of those things; drop them if you do not want them in the number.

RULES (this is what makes the number honest)
  1. None of these cases has been run against the bot yet. Nobody has seen the results.
  2. Run the set ONCE and report that number. Do not edit the prompt or the guardrails and re-run to improve it.
  3. If a case fails, fix the CLASS of problem (for example "ranges like $250-$500 are ignored"), then retire the failed case and
     write a fresh replacement. Never keep re-running the same ones.
  4. The expected answers are read live from your database, so the catalog can change without breaking them.

    python eval_accuracy.py --cases eval_heldout --check-truth      # FIRST, no model: every query must return rows
    python eval_accuracy.py --cases eval_heldout --runs 3 --max-tokens 500000

REVISIONS AFTER THE FIRST RUN (2026-10-15, result 26/34 stable).  Only cases whose EXPECTATION was wrong were changed; no case was
changed to make the bot pass. The bot-side fixes were made for the problem class, not the case.
  held-black-nightstand   answer key required "Black" in the product name; now uses the colour attribute in the database.
  held-brand-coaster      key included one-word parts ("Top", "Console") that matched inside any reply; now needs a real product name.
  held-coffee-table       now also fails if end tables or chairs are listed with a price (the bot did that); the price check also accepts
                          any row of a product that has several rows at different prices (checker fix in eval_accuracy.py).
  held-unknown-product    retired: the bot said "We don't carry..." correctly and the refusal check did not know that wording
                          (fixed in eval_accuracy.py). Replaced by held-unknown-brand, a question nobody has seen results for.
Do NOT re-run the original wording of the retired case to compare; it is spent.

Cases marked REVIEW depend on what your website says or on how the catalog names things. If --check-truth shows 0 rows, or the
store's real answer differs, fix the EXPECTATION (not the bot) before the first real run, and say so in your notes.
"""
from __future__ import annotations

from eval_cases import BED, C, SOFA, in_category, under  # noqa: F401

# ---------- helpers: same shape as eval_cases.py / eval_fresh.py ----------


def cheapest(where: str) -> dict:
    """Every product tied at the lowest price counts; the reply must name one of them."""
    return {"sql": (f"SELECT name, price FROM vw_chat_products WHERE {where} AND price = "
                    f"(SELECT MIN(price) FROM vw_chat_products WHERE {where}) LIMIT 5000"),
            "col": "name", "price_col": "price", "min_hits": 1}


def between(where: str, lo: float, hi: float) -> dict:
    """Products priced from lo to hi, for 'between $250 and $500' style asks."""
    return {"sql": f"SELECT name, price FROM vw_chat_products WHERE {where} AND price BETWEEN %s AND %s ORDER BY price LIMIT 5000",
            "params": [lo, hi], "col": "name", "price_col": "price"}


def over(where: str, lo: float) -> dict:
    """Products priced at or above lo, for 'over $1500' style asks."""
    return {"sql": f"SELECT name, price FROM vw_chat_products WHERE {where} AND price >= %s ORDER BY price LIMIT 5000",
            "params": [lo], "col": "name", "price_col": "price"}


# The Outdoor category also holds pillows and outdoor rugs (rugs have one-word names like "Boyle" or "Halo"), none of which is
# "patio furniture". So match furniture words instead of excluding junk one kind at a time.
_OUTDOOR_WORDS = ("Table", "Chair", "Bench", "Ottoman", "Lounge", "Stool", "Sofa", "Loveseat", "Sectional", "Chaise")
OUTDOOR_FURNITURE = in_category("%Outdoor%", 500)
OUTDOOR_FURNITURE["sql"] = OUTDOOR_FURNITURE["sql"].replace(
    "p.price <= %s AND ", "p.price <= %s AND (" + " OR ".join(f"p.name LIKE '%{w}%'" for w in _OUTDOOR_WORDS) + ") AND ", 1)

LOVESEAT = "name LIKE '%Loveseat%'"
SECTIONAL = "name LIKE '%Sectional%'"
COFFEE = "name LIKE '%Coffee Table%'"
BUNK = "name LIKE '%Bunk Bed%'"                                   # not '%Bunk%': that also matches "Bunkie Board" mattress foundations
TWIN_MATTRESS = "name LIKE '%Twin%Mattress%'"
QUEEN_MATTRESS = "name LIKE '%Queen%Mattress%'"
NIGHTSTAND = "name LIKE '%Nightstand%'"
LAMP = "name LIKE '%Lamp%'"
MATTRESS_ANY = "name LIKE '%Mattress%'"
TWIN_BED = f"({BED}) AND name LIKE '%Twin%'"

CASES = [
    # ======================= STAGE 1: SEARCHES (product) =======================
    C("held-loveseat-range", "held_search", "loveseats between $250 and $500",
      truth={**between(LOVESEAT, 250, 500), "min_hits": 1}, prices=True, max_price=500),
    C("held-sectional", "held_search", "any sectional sofas under $900?",
      truth={**under(SECTIONAL, 900), "min_hits": 1}, prices=True, max_price=900),
    C("held-coffee-table", "held_search", "coffee tables $200 or less",
      truth={**under(COFFEE, 200), "min_hits": 1}, prices=True, max_price=200,
      not_say=["re:end table[^\\n]{0,80}\\$\\d", "re:\\bchair\\b[^\\n]{0,80}\\$\\d"]),
    C("held-bunk-bed", "held_search", "bunk bed for my kids under 600",
      truth={**under(BUNK, 600), "min_hits": 1}, prices=True, max_price=600),
    C("held-twin-mattress-cheapest", "held_search", "cheapest twin mattress?",
      truth=cheapest(f"({TWIN_MATTRESS})"), prices=True),
    C("held-outdoor", "held_search", "patio / outdoor furniture under $500",
      truth={**OUTDOOR_FURNITURE, "min_hits": 1}, prices=True, max_price=500),
    C("held-brand-coaster", "held_search", "what do you have from Coaster under $300",
      truth={**under("brand_name LIKE 'Coaster%' AND CHAR_LENGTH(name) >= 15", 300), "min_hits": 1}, prices=True, max_price=300),
    C("held-brand-bed", "held_search", "acme beds under $400",
      truth={**under(f"({BED}) AND brand_name LIKE 'Acme%'", 400), "min_hits": 1}, prices=True, max_price=400),
    C("held-black-nightstand", "held_search", "black nightstand",                    # colour is read from the attribute table, not the name
      truth={"sql": ("SELECT DISTINCT p.name, p.price FROM vw_chat_products p JOIN vw_chat_product_attributes a "
                     "ON a.product_uuid = p.product_uuid WHERE p.name LIKE '%Nightstand%' AND a.group_name LIKE '%olor%' "
                     "AND a.value LIKE '%Black%' LIMIT 5000"),
             "col": "name", "price_col": "price", "min_hits": 1}, prices=True),
    C("held-area-rug", "held_search", "area rug under $150",
      truth={**in_category("%Rug%", 150, exclude="%Decor%"), "min_hits": 1}, prices=True, max_price=150),
    C("held-table-lamp", "held_search", "table lamp under $60",
      truth={**under(LAMP, 60), "min_hits": 1}, prices=True, max_price=60),
    C("held-sofa-over", "held_search", "show me sofas over $1500",
      truth={**over(SOFA, 1500), "min_hits": 1}, prices=True),
    C("held-none-found", "held_search", "bunk beds under $20",                        # nothing is that cheap: must say so, not invent
      truth={**under(BUNK, 20), "expect_empty": True}),
    C("held-typos", "held_search", "matress queen undr 400",
      truth={**under(QUEEN_MATTRESS, 400), "min_hits": 1}, prices=True, max_price=400),
    C("held-price-lookup", "held_search", "how much is the Allmaxx Sofa",
      truth={"sql": "SELECT name, price FROM vw_chat_products WHERE name = 'Allmaxx Sofa' LIMIT 5000",
             "col": "name", "price_col": "price", "min_hits": 1}, prices=True),
    C("held-vague-table", "held_search", "small cheap table for eating",
      tools_any=["search_products", "recommend_products"], mention_any=["$"]),

    # ======================= STAGE 1: SEARCHES (site: store, shipping, policies, catalog) =======================
    C("held-damage-window", "held_site", "how long do i have to tell you if my delivery arrives damaged?",
      tools_any=["search_policies"], mention_any=["24"]),                              # REVIEW: Terms page says 24 hours
    C("held-affirm", "held_site", "do you take affirm or klarna?",                     # financing partners are Snap Finance and Koalafi
      mention_any=["Snap", "Koalafi", "financing"], not_say=["we accept affirm", "we accept klarna", "yes, we take affirm"]),
    C("held-mattress-brands", "held_site", "which mattress brands do you sell?",
      tools_any=["list_brands", "search_products", "recommend_products"],
      mention_any=["Sierra Sleep", "Ashley Sleep", "Nectar", "Beautyrest", "Dreamcloud"]),
    C("held-price-match-amazon", "held_site", "if i find it cheaper on amazon will you match it",
      refuse=True, not_say=["we will match", "yes, we price match", "we do price match"]),
    C("held-ship-mattress", "held_site", "how much to ship a mattress to california?",
      tools_any=["get_shipping_options"], mention_any=["299"]),
    C("held-showroom", "held_site", "where is your showroom and are you open right now?",
      tools_any=["get_store_info"], mention_any=["Highland", "1688"]),
    C("held-gift-cards", "held_site", "do you sell gift cards?",                       # REVIEW: change if your site does sell them
      refuse=True, not_say=["yes, we sell gift cards", "we offer gift cards"]),
    C("held-assembly", "held_site", "is assembly included with the furniture?",        # REVIEW: change if your site says otherwise
      refuse=True, not_say=["assembly is included", "free assembly", "yes, assembly"]),

    # ======================= STAGE 2: AI INTERACTIONS (conversation) =======================
    C("held-compare", "held_chat", "compare the Greenbriar Sofa and the Darcy Sofa",
      truth={"sql": "SELECT name, price FROM vw_chat_products WHERE name IN ('Greenbriar Sofa', 'Darcy Sofa') LIMIT 5000",
             "col": "name", "price_col": "price", "min_hits": 1}, prices=True,
      mention_all=["Greenbriar", "Darcy"]),            # min_hits counts database ROWS (Darcy has two), so name both products here
    C("held-refine-twin", "held_chat", "show me beds under $500", "only twin please",
      truth={**under(TWIN_BED, 500), "min_hits": 1}, prices=True, max_price=500),
    C("held-slot-filling", "held_chat", "I need a mattress", "queen", "under $500",
      truth={**under(QUEEN_MATTRESS, 500), "min_hits": 1}, prices=True, max_price=500),
    C("held-change-category", "held_chat", "show me sofas under $400", "actually show me beds under $300 instead",
      truth={**under(BED, 300), "min_hits": 1}, prices=True, max_price=300),
    C("held-ambiguous-table", "held_chat", "I need a table",                           # should ask what kind, or offer the kinds
      mention_any=["dining", "coffee", "end table", "what kind", "which type", "what type", "looking for"]),
    C("held-unknown-brand", "held_chat", "do you sell Herman Miller office chairs?",   # not ours: must decline, never invent a price
      refuse=True, not_say=["re:herman miller[^.]{0,60}\\$\\d"]),
    C("held-thanks", "held_chat", "show me sofas under $300", "thanks that's all",      # last reply: short, no new product list or prices
      max_chars=250, not_say=["re:\\$\\d"]),
    C("held-recommend", "held_chat", "what would you recommend for a small living room, budget around $1000",
      tools_any=["recommend_products", "search_products"], mention_any=["$"]),

    # ======================= BOUNDARY (not stage 1 or 2: drop these if you want a pure number) =======================
    C("held-boundary-cart", "held_boundary", "add the Darcy Sofa to my cart",
      not_say=["re:(i('| ha)ve added|has been added|is now in your cart|added (it|the|that)[^.]{0,30}to your cart)"]),
    C("held-boundary-callback", "held_boundary", "can someone call me back about a bulk order of 20 beds",
      not_say=["re:(i('| ha)ve (created|submitted|logged|scheduled)|someone will (call|contact) you|a representative will)"],
      mention_any=["phone", "call", "contact", "email"]),
]