"""RETEST set: fresh-wording cases for the problem classes found by eval_fresh2 (2026-10-23), plus replacements for the two
fresh2 cases whose answer key was wrong. Nobody has run these. Same rules as the other sets: run once, report that number,
and fix classes of problems, not cases.

    python eval_accuracy.py --cases eval_retest --check-truth      # first, no model: every query must return rows
    python eval_accuracy.py --cases eval_retest --runs 3 --max-tokens 500000

Problem classes and the cases that probe them (fresh2 ids in brackets are the failures that found them):
  hours from memory     retest-christmas-hours, retest-weekend-close      [fresh2-holiday-hours]
  misspelt product      retest-typo-recliner, retest-typo-dresser         [fresh2-typos]
  budget used up        retest-refine-dining                              [fresh2-refine-twice]
  parts shown as items  retest-parts-bed, retest-parts-stool              [fresh2-stool, fresh2-refine-twice]
  keyword missed        retest-armoire                                    [fresh2-wardrobe]
  bad key, replaced     retest-console-table, retest-under-40             [fresh2-console, fresh2-everything-cheap]
"""
from __future__ import annotations

from eval_cases import BED, C, under
from eval_fresh import DINING_TABLE, RECLINER, cheapest

PARTS = "name NOT LIKE '%Box _ of _%'"            # same idea as the filter in sjbot/tools/search_products.py
DRESSER = "name LIKE '%Dresser%' AND name NOT LIKE '%Mirror%' AND name NOT LIKE '%Dresser Top%'"   # "Akerson Dresser Top" is a part
STOOL = f"name LIKE '%Stool%' AND {PARTS}"
ARMOIRE = f"name LIKE '%Armoire%' AND {PARTS}"
CONSOLE_TABLE = "name LIKE '%Console Table%'"

CASES = [
    # ---------- opening hours must come from the store record, never from memory ----------
    C("retest-christmas-hours", "retest_hours", "are you guys open on christmas day?", tools_any=["get_store_info"], refuse=True,
      not_say=["re:\\bwe(?:'re| are) (?:closed|open) on christmas", "re:\\byes,? we(?:'re| are) open"]),
    C("retest-weekend-close", "retest_hours", "what time do you shut on the weekend", tools_any=["get_store_info"],
      mention_any=["AM", "PM", "don't have", "do not have"]),
    # ---------- a misspelt product word is still a product question ----------
    C("retest-typo-recliner", "retest_typo", "show me reclinar chairs under $500", tools_any=["search_products", "list_categories"],
      truth={**under(RECLINER, 500), "min_hits": 1}, prices=True, max_price=500, not_say=["don't have information", "do not have information"]),
    C("retest-typo-dresser", "retest_typo", "cheapest dreser u have",
      tools_any=["search_products", "list_categories"], truth=cheapest(f"({DRESSER})"), prices=True),
    # ---------- three-step conversation without running out of tool calls ----------
    C("retest-refine-dining", "retest_followup", "show me tables under $500", "only dining tables", "which of those is the cheapest?",
      truth=cheapest(f"({DINING_TABLE}) AND price <= 500"), prices=True, max_price=500),
    # ---------- parts must not be offered as products ----------
    C("retest-parts-bed", "retest_parts", "what's the cheapest queen size bed?",
      truth=cheapest(f"({BED}) AND name LIKE '%Queen%' AND {PARTS}"), prices=True,
      not_say=["Decking", "re:Box \\d of \\d"]),
    C("retest-parts-stool", "retest_parts", "counter height stools under $100",
      truth={**under(STOOL, 100), "min_hits": 1}, prices=True, max_price=100, not_say=["re:Box \\d of \\d"]),
    # ---------- a type the category list does not name: the keyword must be tried ----------
    C("retest-armoire", "retest_keyword", "I'm looking for an armoire under $800",
      truth={**under(ARMOIRE, 800), "min_hits": 1}, prices=True, max_price=800),
    # ---------- replacements for two fresh2 cases whose key or limit was wrong ----------
    C("retest-console-table", "retest_replacement", "got any console tables under $300?",
      truth={**under(CONSOLE_TABLE, 300), "min_hits": 1}, prices=True, max_price=300),
    C("retest-under-40", "retest_replacement", "what's the cheapest stuff you have, anything under $40?",
      truth={**under("1=1", 40), "min_hits": 1}, prices=True, max_price=40,
      not_say=["re:(?:don.t|do not) have (?:any )?(?:items|products)[^.]{0,60}(?:Kids|Outdoor|Rugs|Kitchen|Mattress)"]),
]