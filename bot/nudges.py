"""Second chances for the model, driven by the question and the tools it used this turn.

When the model answers without the tool the question needs (cheapest from memory, categories or brands from memory, "we don't
have that" without searching the store's pages, only a phone number when the pages answered it), the router sends it back ONCE
with one of the instructions below. Also here: the fixed holiday-hours reply, the "I don't have that information" prefix, and
grounding a product search in the brand the customer actually named.
"""
from __future__ import annotations

import json
import re

from bot.reply_check import _EMAIL, _PHONE

WEAK_POLICY_MATCH = 0.65          # keep in step with search_policies.WEAK_MATCH
_DECLINE = re.compile(r"don'?t have|do not have|couldn'?t find|could not find|can'?t|cannot|unable|not sure|no information|"
                      r"not available|sorry|trouble finding|don'?t see", re.I)
_STORE_INFO_QUESTION = re.compile(r"\b(?:phone|number|e-?mail|call|contact|hours?|open|opens|close|closes|closed|shut|shuts|shutting|closing|address|location|"
                                  r"located|where are you|reach|directions|pick-?\s?up)\b", re.I)


def _policy_searches_all_weak(results: list) -> bool:
    """True if search_policies ran and none of its results had a solid match (only those results carry a 'cutoff' key)."""
    seen = [r for r in results if isinstance(r, dict) and "cutoff" in r]
    if not seen:
        return False
    for r in seen:
        sims = [c.get("similarity", 0) for c in r.get("chunks", [])] if r.get("found") else []
        if sims and max(sims) >= WEAK_POLICY_MATCH:
            return False
    return True


_MD_LINK = re.compile(r"\[([^\]]*)\]\(([^)\s]+)\)")      # markdown links only (see _is_bare_pointer)


def _is_bare_pointer(text: str) -> bool:
    """True if a reply is only a pointer to the store: once links, emails and phone numbers are removed, little is left
    (<= 28 words) and no figures other than opening hours. A short reply that states a fact (\"a restocking fee of 30% applies\") is an answer, not a pointer,
    however few words it has; the old word-count cutoff alone could not tell the two apart."""
    rest = _MD_LINK.sub(" ", text)
    rest = re.sub(r"\[(?:phone|email)\]", " ", rest)
    rest = _PHONE.sub(" ", _EMAIL.sub(" ", rest))
    rest = re.sub(r"[\d()+\s.\u2010-\u2015-]{7,}", " ", rest)                # any other phone-like digit run, e.g. 555-0100
    rest = re.sub(r"\b\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)", " ", rest, flags=re.I)   # opening hours ("9:00 AM") are not facts about the question
    return len(re.findall(r"[A-Za-z']+", rest)) <= 28 and not re.search(r"\d", rest)


def add_no_info_prefix(reply: str, results: list, question: str, tools_used: list) -> str:
    """A short reply that only points the customer to the store, after a policy search with no solid match, must first say
    we don't have the information; otherwise it reads as if the store will supply the answer ("contact us for cleaning advice").
    Does nothing for store-info questions (hours, phone, address), long answers, or replies that already decline."""
    text = reply.replace("\u2019", "'")
    if not set(tools_used) <= {"search_policies", "get_store_info"} or "search_policies" not in tools_used:
        return reply
    if _STORE_INFO_QUESTION.search(question) or _DECLINE.search(text) or not _is_bare_pointer(text):
        return reply
    if not ("@" in text or _PHONE.search(text) or "[phone]" in text or "[email]" in text):
        return reply
    if not _policy_searches_all_weak(results):
        return reply
    return "I don't have that information. " + reply


POLICY_NUDGE = ("The store text you just searched covers this question. Answer from it now, in your own words: state what it says "
                "(fees, terms, steps). Do not reply with only the phone number or email. Point to the store only for what the text does not say.")


def is_bare_contact_reply(reply: str, results: list, question: str, tools_used: list) -> bool:
    """A short reply that is only a phone/email pointer, given after a policy search that DID find a solid match, ignored the
    store text. (When the search was all weak, add_no_info_prefix handles it instead.) Never true for store-info questions."""
    text = reply.replace("\u2019", "'")
    if "search_policies" not in tools_used or not set(tools_used) <= {"search_policies", "get_store_info"}:
        return False
    if _STORE_INFO_QUESTION.search(question) or _DECLINE.search(text) or not _is_bare_pointer(text):
        return False
    if not ("@" in text or _PHONE.search(text) or "[phone]" in text or "[email]" in text):
        return False
    return any(isinstance(r, dict) and "cutoff" in r for r in results) and not _policy_searches_all_weak(results)


_CATEGORY_QUESTION = re.compile(r"\b(?:what|which)\s+(?:product\s+|furniture\s+)?(?:categories|types|kinds)\b|\bcategories\s+(?:do|does)\b|"
                                r"\bwhat\s+do\s+you\s+(?:sell|carry)\b", re.I)
_BRAND_QUESTION = re.compile(r"\b(?:what|which)\s+brands\b|\bbrands\s+do\s+you\b", re.I)
_POLICY_TOPIC = re.compile(r"\b(?:price[\s-]*match\w*|coupons?|promo(?:tion|\s+codes?)?s?|discount\s+codes?|warranty|refund\s+policy|"
                           r"financing|layaway|restocking|(?:military|veterans?|students?|seniors?|teachers?|nurses?|first[\s-]responders?)\s+discounts?|discounts?\s+for\s+(?:military|veterans?|students?|seniors?|teachers?|nurses?|first[\s-]responders?))\b", re.I)


# Questions about the store's own costs: a search of the public pages cannot help, and the model should simply decline.
_INTERNAL_QUESTION = re.compile(r"\b(?:your|our|the store'?s)\s+(?:cost|margin|markup|profit)\b|\bmargins?\b|\bwholesale\b|\bmark-?up\b|\bprofit\b", re.I)
_NO_INFO_STYLE = re.compile(r"don'?t have|do not have|no information|not sure|couldn'?t find|could not find|don'?t see|not available", re.I)
POLICY_FIRST_NUDGE = ("Before you tell the customer you do not have this information, call search_policies with their question. "
                      "If it returns a page that covers it, answer from that page in your own words. Only if it finds nothing "
                      "relevant should you say you do not have the information.")


def declined_without_policy_search(reply: str, question: str, tools_used: list) -> str | None:
    """A question the keyword list in _POLICY_TOPIC does not know ("do you take affirm or klarna?", "do you sell gift cards?")
    was answered \"I don't have that information, call the store\" without the store's own pages ever being searched. Class fix
    for that: if the reply is a no-information answer or a bare phone/email pointer, and the only tool used was get_store_info
    (or none), send the model back once to search_policies first. Product, shipping, inventory and order questions are untouched
    (they used other tools), and so are refusals (\"I can't help with that\"), which are not no-information answers."""
    text = reply.replace("\u2019", "'")
    if "search_policies" in tools_used or not set(tools_used) <= {"get_store_info"}:
        return None
    if _STORE_INFO_QUESTION.search(question) or _INTERNAL_QUESTION.search(question):
        return None
    has_contact = "@" in text or bool(_PHONE.search(text)) or "[phone]" in text or "[email]" in text
    if tools_used and has_contact:                       # only get_store_info ran, the question is not about the store's details, and
        return POLICY_FIRST_NUDGE                        # the reply sends the customer to the phone/email, whatever else it adds (hours...)
    pointer = _is_bare_pointer(text) and has_contact
    return POLICY_FIRST_NUDGE if (_NO_INFO_STYLE.search(text) or pointer) else None


# Opening hours and closures: the model used to answer "are you open on thanksgiving?" from nothing ("we are closed").
_HOURS_QUESTION = re.compile(
    r"\b(?:are\s+(?:you|u|ya)\s+(?:guys\s+)?(?:open|closed)|(?:what|when)\b[^?]{0,25}\b(?:open|close|shut)s?\b|"
    r"(?:store|showroom|business|opening|working)\s+hours?|hours?\s+(?:of\s+operation|on\s+\w+)|"
    r"open\s+(?:on|today|tomorrow|tonight|late|sundays?)|closed?\s+(?:on|today|tomorrow))\b", re.I)
HOURS_NUDGE = ("Call get_store_info now and answer about opening hours or closures only from what it returns. If it does not "
               "list the day or holiday the customer asked about, say you do not have that information and give the store's "
               "phone number. Never say the store is open or closed on a day the result does not state.")

# A misspelt product word ("cheep reclyners") was sent to search_policies, found nothing, and the reply said "I don't have information".
PRODUCT_RETRY_NUDGE = ("That may be a product request with a spelling mistake. Correct the spelling, then call search_products "
                       "(or list_categories to find the category) before telling the customer we do not have it.")
_PRODUCT_TOOLS = {"search_products", "recommend_products", "list_categories", "get_product_details"}


def declined_without_product_search(reply: str, question: str, tools_used: list, results: list) -> str | None:
    """Class fix for a no-information reply after ONLY a weak search_policies: send the model back once to try the catalog.
    Does nothing for policy topics, store-info questions, internal-cost questions, or when any product tool already ran."""
    if "search_policies" not in tools_used or not set(tools_used) <= {"search_policies", "get_store_info"}:
        return None
    if _PRODUCT_TOOLS & set(tools_used):
        return None
    if _POLICY_TOPIC.search(question) or _STORE_INFO_QUESTION.search(question) or _INTERNAL_QUESTION.search(question):
        return None
    if not _policy_searches_all_weak(results):
        return None
    text = reply.replace("\u2019", "'")
    return PRODUCT_RETRY_NUDGE if (_NO_INFO_STYLE.search(text) or _DECLINE.search(text)) else None


_HOLIDAY = re.compile(
    r"\b(?:christmas(?:\s+(?:eve|day))?|xmas|thanksgiving|new\s+year(?:'?s)?(?:\s+(?:eve|day))?|easter|"
    r"(?:4th|fourth)\s+of\s+july|july\s+(?:4|4th|fourth)|independence\s+day|memorial\s+day|labou?r\s+day|veterans?\s+day|"
    r"columbus\s+day|mlk\s+day|presidents?'?\s+day|juneteenth|black\s+friday|holidays?)\b", re.I)
_OPEN_WORD = re.compile(r"\b(?:open|opens|close|closes|closed|hours?|working|work)\b", re.I)


def holiday_hours_reply(question: str, results: list) -> str | None:
    """get_store_info holds the regular weekly hours only (its own note says \"Holidays are not in this data\"), yet the model kept
    answering \"are you open on christmas?\" with \"we are closed\". When the customer asks about a holiday and the store record
    never mentions one, the reply is built here from the record instead: say we have no holiday hours, give the regular hours and
    the store's own phone and email. Returns None when the question is not about a holiday, get_store_info did not run (the nudge
    sends the model to it first), or the record does mention holidays (then the model may answer from it)."""
    if not (_HOLIDAY.search(question) and _OPEN_WORD.search(question)):
        return None
    stores = [st for r in results if isinstance(r, dict) for st in (r.get("stores") or []) if isinstance(st, dict)]
    if not stores:
        return None
    if _HOLIDAY.search(json.dumps(stores, default=str)):
        return None
    store = stores[0]
    summary = store.get("hours_summary") or store.get("hours_text")
    parts = ["I don't have holiday hours on file."]
    if summary:
        parts.append(f"Our regular hours are {summary}.")
    contact = []
    if store.get("phone"):
        contact.append(f"call {store['phone']}")
    if store.get("email"):
        contact.append(f"email {store['email']}")
    if contact:
        parts.append("To check holiday hours, please " + " or ".join(contact) + ".")
    return " ".join(parts)


def required_tool_nudge(question: str, tools_used: list) -> str | None:
    """Questions whose answer must come from a tool, never from memory: categories, brands, store policies and offers.
    Returns the instruction to send back to the model if it tried to answer without calling that tool, else None."""
    used = set(tools_used)
    if _CATEGORY_QUESTION.search(question) and "list_categories" not in used:
        return "Call list_categories with no arguments now and list every top-level category name it returns. Do not answer from memory."
    if _BRAND_QUESTION.search(question) and "list_brands" not in used:
        return "Call list_brands now and name every brand it returns. Do not answer from memory."
    if _POLICY_TOPIC.search(question) and "search_policies" not in used:
        return "Call search_policies now and answer from what it returns. Do not answer from memory or reply with only the phone number."
    if _HOURS_QUESTION.search(question) and "get_store_info" not in used:
        return HOURS_NUDGE
    return None


_PRICE_SUPERLATIVE = re.compile(r"\b(?:cheapest|least\s+expensive|lowest[\s-]+priced?|lowest\s+price|most\s+expensive|highest[\s-]+priced?|"
                                r"highest\s+price|priciest)\b", re.I)
_NOT_ABOUT_PRODUCTS = re.compile(r"\b(?:shipping|ship|delivery|financ\w*|warranty|protection|fee|fees|payment|pay)\b", re.I)
PRODUCT_SEARCH_TOOLS = {"search_products", "recommend_products"}
CHEAPEST_NUDGE = ("Search the catalog now: call search_products with sort price_asc (cheapest) or price_desc (most expensive), "
                  "the same category_slug and any price limit, then answer from that result only. Do not answer from earlier messages.")


KEYWORD_NUDGE = ("This is a cheapest or most expensive question. Search again with the same category_slug, price limit and sort, and add "
                 "keyword set to the product type word from the conversation (singular, spelled correctly, for example bed or dresser), so "
                 "parts and accessories filed in the category are not returned as the product. Then answer from that result only.")

# Some products are filed under no category, so a category search can never return them (the real cheapest queen bed). The model is
# asked to ADD an unscoped search, not to replace the category one: with only the unscoped keyword search, a misspelt or different word
# ("reclyners", "couch" for sofas) finds nothing and the reply says so, which a first version of this nudge caused.
UNCATEGORIZED_NUDGE = ("Some products are not filed under any category, so your category search can miss the cheapest or most expensive one. "
                       "Search once more with the same sort, price limit and keyword (the product type, singular, spelled correctly) but WITHOUT "
                       "category_slug. Compare it with your category result and answer with the best real item of that type, skipping parts "
                       "and accessories. If the search without a category finds nothing, answer from your category result.")


def superlative_search_lacks_keyword(question: str, search_calls: list) -> str | None:
    """Cheapest-first lists put cheap parts and accessories on top (a Queen Decking Board Set filed under Beds, a drawer set under
    Dressers). A keyword with the type word keeps them out. The prompt asks for it; this sends the model back once when it did not."""
    if not _PRICE_SUPERLATIVE.search(question) or _NOT_ABOUT_PRODUCTS.search(question) or not search_calls:
        return None
    if all(c.get("brand_slug") for c in search_calls):
        return None
    if not any(c.get("keyword") for c in search_calls):
        return KEYWORD_NUDGE
    if all(c.get("category_slug") for c in search_calls):     # keyword given, but every search was limited to one category
        return UNCATEGORIZED_NUDGE                            # (retest-parts-bed: the real cheapest bed has no category)
    return None


def needs_fresh_price_search(question: str, tools_used: list) -> bool:
    """'Which is the cheapest?' must be answered from a search made now, never from a list shown earlier (that list is only
    the first few results, not the cheapest ones)."""
    return (bool(_PRICE_SUPERLATIVE.search(question)) and not _NOT_ABOUT_PRODUCTS.search(question)
            and not (PRODUCT_SEARCH_TOOLS & set(tools_used)))


_BRAND_ONLY_STOPWORDS = frozenset(
    "a an the any all some what which who do does you your i me my we our have has get got show see find looking look for from by in on "
    "of to and or with under below over above less more than at most least up about around budget cheap cheapest price priced cost costs "
    "products product items item stuff things thing anything furniture sell sells carry carries offer offers please can could would like "
    "want need is are there brand brands".split())


def ground_brand_search_ex(args: dict, question: str, brands: list) -> tuple[dict, bool]:
    """Make a product search say what the CUSTOMER said about brands (eval 2026-10-15/16, "what do you have from Coaster under $300").
    The model searched a drink-coasters category, or invented a "sofas" category, and the customer was told we have no Coaster sofas.
      * the question names a brand we carry (whole words, longest name wins) -> search that brand_slug, and drop a keyword that is
        only the brand name;
      * the question names no product type at all (only the brand, a price, and filler words) -> drop any category or keyword the
        model made up, so the search is brand + price.
    brands: [{"slug": ..., "name": ...}]. Returns (args, brand_only); args unchanged when no brand is named."""
    q = question.lower().replace("\u2019", "'")
    best = None
    for b in brands or []:
        name = re.sub(r"[\u2122\u00ae]", "", str(b.get("name") or "")).strip().lower()
        if name and b.get("slug") and re.search(rf"(?<![a-z0-9]){re.escape(name)}(?![a-z0-9])", q):
            if best is None or len(name) > len(best[0]):
                best = (name, b["slug"])
    if best is None:
        return args, False
    name, slug = best
    out = dict(args)
    kw = out.get("keyword")
    if isinstance(kw, str) and re.sub(r"[^a-z0-9]", "", kw.lower()) == re.sub(r"[^a-z0-9]", "", name):
        out.pop("keyword")
    out.setdefault("brand_slug", slug)
    rest = re.sub(r"\$\s?\d[\d,]*(?:\.\d+)?", " ", q.replace(name, " "))
    brand_only = not [w for w in re.findall(r"[a-z]+", rest) if w not in _BRAND_ONLY_STOPWORDS]
    if brand_only:
        out.pop("category_slug", None)
        out.pop("keyword", None)
    return out, brand_only


def ground_brand_search(args: dict, question: str, brands: list) -> dict:
    return ground_brand_search_ex(args, question, brands)[0]


BRAND_ONLY_NOTE = ("The customer named only this brand (and maybe a price), not a product type. Show the products returned, or ask what "
                   "type they want. Never say the store lacks a product type the customer did not ask for.")
