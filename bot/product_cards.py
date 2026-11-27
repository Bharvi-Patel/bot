"""Product cards for the chat widget: the products a reply talks about, as data the storefront can draw (image, name,
price, link), so the visitor gets clickable cards and not only text.

Every field comes from a tool result of the same turn (search_products, recommend_products, get_product_details), never
from the model's text, so a card cannot show a made-up price, picture or link. The model's reply only decides WHICH
products get a card and in what order: a product gets one when the reply names it (its full name, or the first words of
its name when no other product starts the same way) or links its page.

Card shape (all keys always present; null when the store has no value):
    {"sku": str|None, "slug": str, "name": str, "brand": str|None, "url": "/p/<slug>", "image": str|None,
     "price": float|None, "price_from": float|None}
price is the one price of the product. price_from is set instead when the price depends on an option (size...) and
get_product_details listed the options: the lowest option price, to show as "from $X". Both null: show no price.
url is a path on the store's own site (SJ_PRODUCT_URL, default /p/{slug}); the widget runs on that site, so it works as is.
"""
from __future__ import annotations

import os
import re
from typing import Any

MAX_CARDS = 6
MIN_PREFIX_WORDS = 3                                   # "Wakefit ShapeSense Orthopedic" is enough to know which product
_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,200}$")
_PAGE = re.compile(r"^/(?:p|product)/([A-Za-z0-9][A-Za-z0-9_-]{0,200})$")


def product_url(slug: str) -> str:
    return os.environ.get("SJ_PRODUCT_URL", "/p/{slug}").replace("{slug}", slug)


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower().replace("’", "'"))


def _price(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0 else None


def _candidates(results: list) -> dict[str, dict]:
    """slug -> card, from every product the tools returned this turn. A later, fuller result (details) fills gaps."""
    found: dict[str, dict] = {}

    def add(slug: Any, name: Any, *, sku=None, brand=None, image=None, price=None, price_from=None, blurb=None) -> None:
        if not isinstance(slug, str) or not _SLUG.match(slug) or not isinstance(name, str) or not name.strip():
            return
        card = found.setdefault(slug, {"sku": None, "slug": slug, "name": name.strip(), "brand": None,
                                       "url": product_url(slug), "image": None, "price": None, "price_from": None,
                                       "note": None, "_blurb": None})
        for key, value in (("sku", sku), ("brand", brand), ("image", image)):
            if card[key] is None and isinstance(value, str) and value.strip():
                card[key] = value.strip()
        if card["_blurb"] is None:
            card["_blurb"] = _blurb(blurb, card["name"])
        if card["price"] is None and card["price_from"] is None:
            card["price"], card["price_from"] = _price(price), _price(price_from)

    for r in results:
        if not isinstance(r, dict):
            continue
        for p in [*(r.get("products") or []), *(r.get("cheapest_over_limit") or [])]:
            if not isinstance(p, dict):
                continue
            live = p.get("live") if isinstance(p.get("live"), dict) else {}           # recommend_products
            slug = p.get("slug") or live.get("slug")
            if not slug and isinstance(p.get("page"), str) and (m := _PAGE.match(p["page"])):
                slug = m.group(1)
            rng = p.get("price_range") if isinstance(p.get("price_range"), dict) else {}
            add(slug, p.get("name") or live.get("name"), sku=p.get("sku"), brand=p.get("brand_name") or live.get("brand"),
                image=p.get("main_image") or live.get("main_image"),
                price=None if p.get("price_varies") else p.get("price"), price_from=rng.get("min"),
                blurb=[live.get("summary"), live.get("description")])
        prod = r.get("product") if r.get("found") and isinstance(r.get("product"), dict) else None   # get_product_details
        if prod:
            rng = r.get("price_range") if isinstance(r.get("price_range"), dict) else {}
            add(prod.get("slug"), prod.get("name"), sku=prod.get("sku"), brand=prod.get("brand"),
                image=prod.get("main_image"), price=None if rng else prod.get("price"), price_from=rng.get("min"),
                blurb=[prod.get("summary"), prod.get("description")])
    return found


BLURB_CHARS = 140


def _blurb(texts: Any, name: str) -> str | None:
    """A short description from the store's own text (summary, else description), when the reply wrote none. A summary
    that only repeats the name ("Daisy") is skipped. Cut at a word, never mid-word."""
    for t in texts if isinstance(texts, list) else [texts]:
        if not isinstance(t, str):
            continue
        t = re.sub(r"\s+", " ", t).strip()
        if not t or _words(t) == _words(name):
            continue
        if len(t) > BLURB_CHARS:
            t = t[:BLURB_CHARS].rsplit(" ", 1)[0].rstrip(" ,;:-") + "..."
        return t
    return None


def _count(words: list[str], reply_words: list[str]) -> int:
    """How many times these words appear in a row in the reply (not overlapping)."""
    n, i, found = len(words), 0, 0
    while n and i <= len(reply_words) - n:
        if reply_words[i:i + n] == words:
            found += 1
            i += n
        else:
            i += 1
    return found


def _position(words: list[str], reply_words: list[str]) -> int | None:
    n = len(words)
    for i in range(len(reply_words) - n + 1):
        if reply_words[i:i + n] == words:
            return i
    return None


def cards_for_reply(reply: str, results: list, max_cards: int = MAX_CARDS) -> list[dict]:
    """The cards for the products this reply names, in the order the reply names them."""
    if not reply or not results:
        return []
    candidates = _candidates(results)
    if not candidates:
        return []
    reply_words = _words(reply)
    low_reply = reply.lower()
    hits: list[tuple[int, str, tuple[str, ...] | None]] = []        # (position, slug, prefix it was matched by)
    for slug, card in candidates.items():
        name = _words(card["name"])
        if not name:
            continue
        pos = _position(name, reply_words)
        if pos is not None:
            hits.append((pos, slug, None))
            continue
        link = re.search(r"/(?:p|product)/" + re.escape(slug.lower()) + r"(?![a-z0-9_-])", low_reply)
        if link:
            hits.append((len(_words(reply[:link.start()])), slug, None))
            continue
        for k in range(len(name) - 1, MIN_PREFIX_WORDS - 1, -1):          # the reply shortened the name
            pos = _position(name[:k], reply_words)
            if pos is not None:
                hits.append((pos, slug, tuple(name[:k])))
                break
    # A shortened name that fits several products ("Ashley Queen Panel") cannot say which one is meant: no card.
    names = [tuple(_words(c["name"])) for c in candidates.values()]
    hits = [h for h in hits if h[2] is None or sum(n[:len(h[2])] == h[2] for n in names) == 1]
    # Several products with the same name ("Daisy" in five sizes) all match at the same words: order those by where the
    # reply gives their price, so the cards follow the reply's list.
    def price_pos(slug: str) -> int:
        c = candidates[slug]
        value = c["price"] or c["price_from"]
        if not value:
            return len(reply)
        for text in (f"{value:,.2f}", f"{value:.2f}"):
            i = reply.find(text)
            if i >= 0:
                return i
        return len(reply)
    hits.sort(key=lambda h: (h[0], price_pos(h[1])))
    # The store can have several products with one name ("Northwich" twice). A name gets at most as many cards as the
    # reply mentions it, and the ones whose price the reply gives come first (the sort above).
    mentions: dict[tuple[str, ...], int] = {}
    kept_hits = []
    for h in hits:
        key = tuple(_words(candidates[h[1]]["name"]))
        if key not in mentions:
            mentions[key] = max(1, _count(list(h[2] or key), reply_words))
        if mentions[key] > 0:
            mentions[key] -= 1
            kept_hits.append(h)
    hits = kept_hits
    cards = [dict(candidates[slug]) for _, slug, _ in hits[:max_cards]]
    for c in cards:
        c["note"] = c.pop("_blurb", None)            # replaced by the reply's own words in split_reply_for_cards
    return cards


def add_store_descriptions(cards: list[dict], details: Any, timeout: float = 4.0) -> list[dict]:
    """A search listing has no description, so a card from search_products has no note. For those cards, read the
    product's details (all at once, in threads) and use the store's own summary or description as the note.
    details(slug) -> get_product_details result. A slow or failed read just leaves that card without a note."""
    missing = [c for c in cards if not c.get("note") and c.get("slug")]
    if not missing:
        return cards
    from concurrent.futures import ThreadPoolExecutor, wait
    pool = ThreadPoolExecutor(max_workers=min(6, len(missing)))
    try:
        futures = {pool.submit(details, c["slug"]): c for c in missing}
        done, _ = wait(futures, timeout=timeout)
        for f in done:
            try:
                r = f.result()
            except Exception:
                continue
            prod = r.get("product") if isinstance(r, dict) and r.get("found") and isinstance(r.get("product"), dict) else None
            card = futures[f]
            if prod and prod.get("slug") == card["slug"]:
                card["note"] = _blurb([prod.get("summary"), prod.get("description")], card["name"])
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return cards


_ITEM_START = re.compile(r"^\s*(?:[-*•]|\d{1,2}[.)])\s+")      # "- ", "* ", "• ", "1. ", "2) "
_PRICE_TEXT = re.compile(r"(?:from\s+)?\$\s?\d[\d,]*(?:\.\d{2})?", re.I)
_EDGE = " \t-–—:|,.()"


def _line_names(line: str, card: dict) -> bool:
    name = _words(card["name"])
    return bool(name) and _position(name, _words(line)) is not None


def _note_from(lines: list[str], card: dict) -> str:
    """The reply's words about one product, without the list marker, the bold marks, the name and the price."""
    text = " ".join(l.strip() for l in lines)
    text = _ITEM_START.sub("", text).replace("**", "").replace("__", "")
    name_pat = r"[\W_]*".join(re.escape(w) for w in re.findall(r"[A-Za-z0-9]+", card["name"]))
    if name_pat:
        text = re.sub(name_pat, " ", text, count=1, flags=re.I)
    text = _PRICE_TEXT.sub(" ", text)
    text = re.sub(r"\s{2,}", " ", text).strip(_EDGE)
    return text[:1].upper() + text[1:] if text else ""


def split_reply_for_cards(reply: str, cards: list[dict]) -> tuple[str, list[dict]]:
    """For a reply that lists products: (the text without the product lines, the cards each with its note).
    A product's lines are the line that names it plus the lines under it, up to a blank line or the next product.
    The reply's own words about the product become its note; otherwise the store's own description stays. Nothing is
    invented: the note is the model's text (which the reply checks already passed) or the store's description."""
    if not cards or not reply:
        return reply, cards
    lines = reply.split("\n")
    owner: list[int | None] = [None] * len(lines)
    taken: set[int] = set()
    current = None
    for i, line in enumerate(lines):
        if not line.strip():
            current = None
            continue
        hit = next((k for k, c in enumerate(cards) if k not in taken and _line_names(line, c)), None)
        if hit is not None:
            taken.add(hit)
            current = hit
            owner[i] = hit
        elif current is not None and not _ITEM_START.match(line):
            owner[i] = current                       # "$459.99 - Firm foam..." on the line under the name
        else:
            current = None
    if not taken:
        return reply, cards
    out_cards = []
    for k, c in enumerate(cards):
        c = dict(c)
        own = [lines[i] for i in range(len(lines)) if owner[i] == k]
        note = _note_from(own, c) if own else ""
        if len(_words(note)) >= 3:
            c["note"] = note
        out_cards.append(c)
    kept = [l for i, l in enumerate(lines) if owner[i] is None]
    display = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
    return display, out_cards


_IMAGE_URL = r"https?://[^\s<>()\]]+\.(?:webp|jpe?g|png|gif)(?:\?[^\s<>()\]]*)?"


def tidy_reply_for_cards(reply: str, cards: list[dict]) -> str:
    """The products now come with cards (picture and link), so the text does not need the bare image URLs, the empty
    "<>" links or the product page links the model sometimes writes anyway. Link text is kept; only the URL goes."""
    if not cards or not reply:
        return reply
    text = reply
    text = re.sub(r"!\[[^\]]*\]\(" + _IMAGE_URL + r"\)", "", text)                       # ![alt](image)
    text = re.sub(r"\[([^\]]+)\]\((?:" + _IMAGE_URL + r")\)", r"\1", text)                 # [text](image)
    for c in cards:
        page = re.escape(c["url"])
        text = re.sub(r"\[([^\]]+)\]\((?:https?://[^\s/()]+)?" + page + r"/?\)", r"\1", text)   # [name](/p/slug)
        text = re.sub(r"<?(?:https?://[^\s/<>]+)?" + page + r"/?>?(?![\w/-])", "", text)            # bare /p/slug
    text = re.sub(r"<?" + _IMAGE_URL + r">?", "", text)                                           # bare image URL
    text = re.sub(r"<\s*>", "", text)                                                             # empty <>
    text = re.sub(r"[ \t]*[–—|-][ \t]*(?=\n|$)", "", text)                                       # a dash left at a line's end
    text = re.sub(r"[ \t]+(?=\n|$)", "", text)
    return text.strip() or reply
