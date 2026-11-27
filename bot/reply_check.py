"""The last gate before a reply is sent, and the repairs that save a good answer with one bad detail.

check_reply: every price, email, phone number, link and quote in a reply must come from tool results (or the customer's own
words), and nothing internal (tool names, database names, the system prompt) may appear. It returns the reason when it fails.
The repairs (repair_unverified_prices, repair_contacts, strip_unknown_links, strip_tool_name_links) fix the detail that failed,
and the router checks the fixed reply again.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from bot.dev_defaults import SOUTH_JACKSON

log = logging.getLogger(__name__)

_PRICE = re.compile(r"\$\s?(\d[\d,]*(?:\.\d{1,2})?)")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(?<!\d)\(?\d{3}\)?[\s.\u2010-\u2015-]*\d{3}[\s.\u2010-\u2015-]*\d{4}(?!\d)")
_BLOCKED = re.compile(r"base_price|vw_chat|information_schema|chatbot_ro|markup|rag_chunks|\b\w+_(?:db|dev)\b", re.I)   # internal names; the last one is any "<store>_db" / "<store>_dev" database name


_LEAK_WORDS: re.Pattern | None = None
_PROMPT_SHINGLES: set | None = None


def _leak_words() -> re.Pattern:
    """Tool names and snake_case parameter names (check_inventory, category_slug, ...): never shown to customers."""
    global _LEAK_WORDS
    if _LEAK_WORDS is None:
        from bot.guardrails import TOOLS                         # imported late: guardrails imports this module
        from bot.tool_schemas import TOOL_DECLARATIONS          # imported late: tool_schemas needs the tool modules
        words = set(TOOLS)
        for d in TOOL_DECLARATIONS:
            words.add(d["name"])
            words |= {k for k in (d.get("parameters") or {}).get("properties", {}) if "_" in k}
        _LEAK_WORDS = re.compile(r"\b(?:" + "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True)) + r")\b", re.I)
    return _LEAK_WORDS


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def _copies_system_prompt(reply: str, n: int = 8) -> bool:
    """True if the reply repeats n words in a row from the system prompt."""
    global _PROMPT_SHINGLES
    if _PROMPT_SHINGLES is None:
        from bot.guardrails import SYSTEM_PROMPT                 # imported late: guardrails imports this module
        w = _words(SYSTEM_PROMPT)
        _PROMPT_SHINGLES = {tuple(w[i:i + n]) for i in range(len(w) - n + 1)}
    w = _words(reply)
    return any(tuple(w[i:i + n]) in _PROMPT_SHINGLES for i in range(len(w) - n + 1))


def _emails(text: str) -> set[str]:
    """Emails in text, lowercased, without the sentence punctuation the regex swallows ('...@gmail.com.')."""
    return {e.rstrip(".,;:!?)-").lower() for e in _EMAIL.findall(text)}


def _phones(text: str) -> set[str]:
    """10-digit phone numbers as bare digits, whatever dashes or spaces (including U+2011) were used."""
    return {re.sub(r"\D", "", p) for p in _PHONE.findall(text)}


def _to_float(text: str) -> float:
    return round(float(text.replace(",", "")), 2)


def _numbers(obj: Any, out: set) -> None:
    if isinstance(obj, dict):
        for v in obj.values():
            _numbers(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _numbers(v, out)
    elif isinstance(obj, bool):
        return
    elif isinstance(obj, (int, float)):
        out.add(round(float(obj), 2))
    elif isinstance(obj, str) and re.fullmatch(r"\d[\d,]*(\.\d+)?", obj):
        out.add(_to_float(obj))


def _priced_items(obj: Any, pairs: list, every: set) -> None:
    """Collect the prices in tool results: every value under a key containing 'price', and (name, price) pairs when the
    same record has a name or title, so a price in a reply can be tied to the product on its line."""
    if isinstance(obj, dict):
        name = next((obj[k] for k in ("name", "title") if isinstance(obj.get(k), str)), None)
        for k, v in obj.items():
            if "price" in str(k).lower() and not isinstance(v, bool):
                if isinstance(v, (int, float)) or (isinstance(v, str) and re.fullmatch(r"\d[\d,]*(\.\d+)?", v)):
                    val = round(float(v) if not isinstance(v, str) else _to_float(v), 2)
                    every.add(val)
                    if name:
                        pairs.append((_norm_words(name), val))
            _priced_items(v, pairs, every)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _priced_items(v, pairs, every)


def repair_rounded_prices(reply: str, tool_results: list, user_message: str = "", earlier_replies: str = "") -> str | None:
    """The model often writes a whole-dollar price for a price with cents ($185 for 185.14, $197 for 196.82). That is the right
    answer with a loose number, so swap it for the exact price instead of blocking the reply. A whole-dollar price is fixed only
    when it is the floor or the rounding of exactly one real price, matched to the product named on its own line when there is
    one (and then even when that whole number also happens to be another product's exact price). A price with cents that is wrong, or one that fits no real price or several, is not rounding: returns None and the
    reply stays blocked. Also returns None when nothing needed fixing."""
    allowed: set = set()
    _numbers(tool_results, allowed)
    allowed |= {_to_float(m) for m in _PRICE.findall(user_message)} | {_to_float(m) for m in _PRICE.findall(earlier_replies)}
    pairs: list = []
    every: set = set()
    _priced_items(tool_results, pairs, every)
    state = {"changed": False, "ok": True}

    def fits(v: float, p: float) -> bool:
        return p in (float(int(v)), float(int(v + 0.5)))

    def fix(m: re.Match) -> str:
        p = _to_float(m.group(1))
        start = m.string.rfind("\n", 0, m.start()) + 1
        end = m.string.find("\n", m.end())
        line = _norm_words(m.string[start:len(m.string) if end == -1 else end])
        named = {nm for nm, _ in pairs if nm and nm in line}
        named = {nm for nm in named if not any(nm != o and nm in o for o in named)}   # 'Dresser' inside 'Willowton Bed, Dresser and Mirror' is not a second product on the line: keep the longest names only
        on_line = {v for nm, v in pairs if nm in named}          # real prices of the product(s) named on this line
        if p in on_line:
            return m.group(0)                                    # already this product's exact price
        whole = p == int(p)
        near = {v for v in on_line if fits(v, p)} if whole else set()
        found = near if len(near) == 1 else None
        if found is None:
            if p in allowed:
                return m.group(0)                                # equals some real price and no single product to pin it to: as the guard always allowed
            if not whole or on_line:
                state["ok"] = False                              # cents but not a real price, or this product's price does not fit: invented, not rounding
                return m.group(0)
            found = {v for v in every if fits(v, p)}             # no product named on the line: only when exactly one real price fits
            if len(found) != 1:
                state["ok"] = False
                return m.group(0)
        v = next(iter(found))
        state["changed"] = True
        return "$" + (f"{v:,.0f}" if v == int(v) else f"{v:,.2f}")

    out = _PRICE.sub(fix, reply)
    return out if state["ok"] and state["changed"] else None


_MD_LINK_TARGET = re.compile(r"\s*\[[^\]]*\]\(\s*([A-Za-z_][\w]*)\s*\)")


def strip_tool_name_links(reply: str) -> str | None:
    """The model sometimes writes a markdown link whose target is a tool name ([Get store contact details](get_store_info)).
    The answer around it is fine and the link goes nowhere, so cut the link out instead of blocking the whole reply. Only links
    whose target is an internal tool or parameter name are cut; a tool name anywhere else in the text still blocks. Returns
    None if there was no such link."""
    leak = _leak_words()
    found = []

    def cut(m: re.Match) -> str:
        if leak.fullmatch(m.group(1)):
            found.append(m.group(1))
            return ""
        return m.group(0)

    out = _MD_LINK_TARGET.sub(cut, reply)
    if not found:
        return None
    out = re.sub(r"([.!?])\.(?=\s|$)", r"\1", out)                 # \"call us. [link].\" must not end up as \"call us..\"
    return out.strip()


_BUDGET_PHRASE = re.compile(
    r"\s*(?:\bfor\s+)?\b(?:under|below|less\s+than|up\s+to|within|at\s+most|no\s+more\s+than)\s+\$\s?(\d[\d,]*(?:\.\d{1,2})?)"
    r"|\s*\$\s?(\d[\d,]*(?:\.\d{1,2})?)\s+or\s+(?:less|under|below)\b", re.I)


def strip_invented_budget(reply: str, tool_results: list, user_message: str = "", earlier_replies: str = "") -> str | None:
    """The model sometimes writes a budget nobody gave ("a small dining table for under $70" when the customer said "small cheap
    table"; the product itself really costs $63.80). The product and its price are right and the made-up ceiling is not, so cut
    the ceiling phrase instead of blocking the whole reply. Only budget phrases (under / below / up to / $N or less) with a number
    that is not in the tool results, the customer's words or earlier replies are cut. Returns None if nothing was cut."""
    allowed: set = set()
    _numbers(tool_results, allowed)
    allowed |= {_to_float(m) for m in _PRICE.findall(user_message)} | {_to_float(m) for m in _PRICE.findall(earlier_replies)}
    cut_any = []

    def cut(m: re.Match) -> str:
        v = _to_float(m.group(1) or m.group(2))
        if v in allowed:
            return m.group(0)
        cut_any.append(v)
        return ""

    out = _BUDGET_PHRASE.sub(cut, reply)
    return out if cut_any else None


def repair_unverified_prices(reply: str, tool_results: list, user_message: str = "", earlier_replies: str = "") -> str | None:
    """Both repairs for an unverified_price block: cut an invented budget phrase, then fix whole-dollar rounding. None if neither
    changed anything or the result still has a price that is not real (the router checks again and blocks)."""
    step = strip_invented_budget(reply, tool_results, user_message, earlier_replies)
    base = step or reply
    rounded = repair_rounded_prices(base, tool_results, user_message, earlier_replies)
    return rounded or step



def repair_contacts(reply: str, tool_results: list, trusted_text: str) -> str | None:
    """Swap an invented email or phone number for the store's real one, so a good answer is not thrown away because
    the model made up its contact line. Returns the fixed reply, or None if the store's own contact is unknown."""
    store_email = next(iter(sorted(_emails(trusted_text))), None)
    phone_match = _PHONE.search(trusted_text)
    dumped = json.dumps(tool_results, default=str, ensure_ascii=False) + " " + trusted_text
    known_emails, known_phones = _emails(dumped), _phones(dumped)

    def fix_email(m: re.Match) -> str:
        raw = m.group()
        core = raw.rstrip(".,;:!?)-")
        return raw if core.lower() in known_emails else (store_email or core) + raw[len(core):]

    def fix_phone(m: re.Match) -> str:
        return m.group() if re.sub(r"\D", "", m.group()) in known_phones else (phone_match.group() if phone_match else m.group())

    fixed = _PHONE.sub(fix_phone, _EMAIL.sub(fix_email, reply))
    if (_emails(fixed) - known_emails) or (_phones(fixed) - known_phones):
        return None                                               # nothing real to swap in
    return fixed


# ---- links and quotes must come from tool results -------------------------------------------------------------
_URL = re.compile(r"https?://[^\s)\]>\"'<]+", re.I)
_MD_LINK = re.compile(r"\[([^\]]*)\]\(([^)\s]+)\)")
_REL_PATH = re.compile(r"(?<![\w:/.\-])(/[a-z0-9][\w\-./%]*)", re.I)
_QUOTE = re.compile(r"(?<!\w)[\u201c\"]([^\u201c\u201d\"\n]{40,}?)[\u201d\"](?!\w)")
_TRIM = ".,;:!?)"


def _strings(obj):
    """Every text value inside the tool results."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _strings(v)


def _link_known(target: str, dumped: str, user_message: str, domains: tuple[str, ...] | None = None) -> bool:
    low = target.lower()
    if low in dumped or low in user_message.lower():
        return True
    host = re.match(r"https?://([^/:]+)", low)
    own = SOUTH_JACKSON.allowed_domains if domains is None else domains      # the chat's store, never another store
    if host and not any(host.group(1).removeprefix("www.") == d or host.group(1).endswith("." + d) for d in own):
        return False                                              # some other site that no tool returned
    path = re.split(r"[?#]", re.sub(r"^https?://[^/]+", "", low))[0].rstrip("/")
    if not path or path in dumped:
        return True
    seg = path.rsplit("/", 1)[-1]                                 # /product/<slug>: the slug itself came from a tool
    return bool(re.search(r'"' + re.escape(seg) + r'"(?!\s*:)', dumped))


def unknown_links(reply: str, tool_results: list, user_message: str = "", domains: tuple[str, ...] | None = None) -> list[str]:
    dumped = json.dumps(tool_results, default=str, ensure_ascii=False).lower()
    no_md = _MD_LINK.sub(" ", reply)
    targets = [m.group(2) for m in _MD_LINK.finditer(reply)] + _URL.findall(no_md) + _REL_PATH.findall(_URL.sub(" ", no_md))
    return [t for t in (t.rstrip(_TRIM) for t in targets) if not _link_known(t, dumped, user_message, domains)]


def strip_unknown_links(reply: str, tool_results: list, user_message: str = "", domains: tuple[str, ...] | None = None) -> str:
    """Keep the answer, drop links no tool returned (the model built them from memory and they 404)."""
    dumped = json.dumps(tool_results, default=str, ensure_ascii=False).lower()

    def keep(t: str) -> bool:
        return _link_known(t.rstrip(_TRIM), dumped, user_message, domains)
    fixed = _MD_LINK.sub(lambda m: m.group(0) if keep(m.group(2)) else m.group(1), reply)
    fixed = _URL.sub(lambda m: m.group(0) if keep(m.group(0)) else "", fixed)
    fixed = _REL_PATH.sub(lambda m: m.group(0) if keep(m.group(1)) else "", fixed)
    return re.sub(r"[ \t]{2,}", " ", fixed)


def _norm_words(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9']+", text.lower().replace("\u2019", "'")))


def unverified_quotes(reply: str, tool_results: list) -> list[str]:
    """Quoted passages (6+ words) that do not appear word for word in any tool result."""
    hays = [_norm_words(t) for t in _strings(tool_results)]
    return [q for q in _QUOTE.findall(reply)
            if len(_norm_words(q).split()) >= 6 and not any(_norm_words(q) in h for h in hays)]


# Where a reply may repeat a number the customer typed: as their budget or price range, never as a product's price.
_N = r"\$\s?(\d[\d,]*(?:\.\d{1,2})?)"
_BUDGET_CONTEXT = re.compile(
    r"\b(?:under|below|less\s+than|up\s+to|within|at\s+most|no\s+more\s+than|not\s+over|over|above|more\s+than|at\s+least|"
    r"budget\s+of|limit\s+of|max(?:imum)?\s+of|between|from)\s+" + _N + r"(?:\s*(?:and|to|-|–|—)\s*" + _N + r")?"
    r"|" + _N + r"\s+(?:or\s+(?:less|under|below|lower|more|over|above)|budget|range|limit|max(?:imum)?)\b"
    r"|\byour\s+" + _N, re.I)


def _outside_budget_phrases(reply: str) -> str:
    """The reply with its budget phrases ("under $500", "between $300 and $600", "your $800 budget") taken out."""
    return _BUDGET_CONTEXT.sub(" ", reply)


def check_reply(reply: str, tool_results: list, user_message: str = "", trusted_text: str = "",
                contact_results: list | None = None, earlier_replies: str = "",
                domains: tuple[str, ...] | None = None) -> tuple[bool, str | None]:
    """Last gate before a reply is sent. Returns (ok, reason). On not-ok, send FALLBACK and log the reason.
    trusted_text: the store's own contact details (store_contact_text()); emails and phones in it may be repeated.
    contact_results: the tool results whose emails and phones may be repeated (default: all of tool_results)."""
    allowed: set = set()
    _numbers(tool_results, allowed)
    allowed |= {_to_float(m) for m in _PRICE.findall(earlier_replies)}   # the bot's own earlier replies passed this check (mw keeps them server-side)
    # A number the customer typed may be echoed back only as their budget ("under $1,000"), never as a price: otherwise
    # "I saw the Kenyon for $1, right?" could get "Yes, the Kenyon is $1" past this check.
    typed = {_to_float(m) for m in _PRICE.findall(user_message)}
    in_budget = {_to_float(m) for m in _PRICE.findall(reply)} - {_to_float(m) for m in _PRICE.findall(_outside_budget_phrases(reply))}
    bad_prices = {_to_float(m) for m in _PRICE.findall(reply)} - allowed - (typed & in_budget)
    bad_prices |= {_to_float(m) for m in _PRICE.findall(_outside_budget_phrases(reply))} - allowed
    if bad_prices:
        log.warning("reply blocked, price not in tool results: %s", sorted(bad_prices))
        return False, "unverified_price"
    if _BLOCKED.search(reply) or _leak_words().search(reply) or _copies_system_prompt(reply):
        return False, "internal_leak"
    contact_src = tool_results if contact_results is None else contact_results
    dumped = json.dumps(contact_src, default=str, ensure_ascii=False) + " " + trusted_text
    bad_email = _emails(reply) - _emails(dumped) - _emails(user_message)     # the customer may see their own typed email
    if bad_email:
        log.warning("reply blocked, email not in tool results: %s", sorted(bad_email))
        return False, "unknown_email"
    bad_phone = _phones(reply) - _phones(dumped)
    if bad_phone:
        log.warning("reply blocked, phone not in tool results: %s", sorted(bad_phone))
        return False, "unknown_phone"
    bad_links = unknown_links(reply, tool_results, user_message, domains)
    if bad_links:
        log.warning("reply blocked, link not in tool results: %s", bad_links)
        return False, "unknown_link"
    fake = unverified_quotes(reply, tool_results)
    if fake:
        log.warning("reply blocked, quote not found in tool results: %s", [q[:80] for q in fake])
        return False, "unverified_quote"
    return True, None
