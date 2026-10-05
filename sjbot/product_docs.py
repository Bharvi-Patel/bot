"""Product documents for RAG (data map 5.4): pure functions, no database access.
One document per parent product. Price is NOT in the text: it is stored as metadata only, and the bot
always gets the live price from SQL ("RAG finds it, SQL confirms it")."""
from __future__ import annotations

import hashlib
import html
import re
from typing import Any

MAX_DETAILS = 1200      # characters kept from full_description / special_features, so one product stays well under 450 tokens
MAX_FEATURES = 600
MAX_VALUES_PER_GROUP = 12


def strip_html(raw: Any) -> str:
    if not raw:
        return ""
    from bs4 import BeautifulSoup
    text = BeautifulSoup(str(raw), "html.parser").get_text(" ")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _cut(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text.rfind(". ", 0, limit)
    return text[: cut + 1] if cut > limit // 2 else text[:limit].rstrip() + "..."


def _dim(v: Any) -> str | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f"{f:g}" if f > 0 else None


def attribute_text(attrs: dict[str, list[str]]) -> str:
    parts = []
    for group, values in attrs.items():
        uniq = list(dict.fromkeys(v for v in values if v))
        if uniq:
            more = " ..." if len(uniq) > MAX_VALUES_PER_GROUP else ""
            parts.append(f"{group}: {', '.join(uniq[:MAX_VALUES_PER_GROUP])}{more}")
    return "; ".join(parts)


def compose_document(p: dict, category_titles: list[str], attrs: dict[str, list[str]]) -> str:
    """Template from 5.4. Lines with no data are left out."""
    lines = [str(p.get("name") or "").strip()]
    cats = " | ".join(dict.fromkeys(t for t in category_titles if t))
    lines.append(f"Brand: {p.get('brand_name') or 'unknown'} | Categories: {cats or 'unknown'} | SKU: {p.get('sku')}")
    style = str(p.get("lifestyle") or "").strip()
    if style and not style.isdigit():
        lines.append(f"Style: {style}")
    a = attribute_text(attrs)
    if a:
        lines.append(f"Attributes: {a}")
    dims = [(_dim(p.get("width")), "W"), (_dim(p.get("depth")), "D"), (_dim(p.get("height")), "H")]
    dims = [f"{v} {k}" for v, k in dims if v]
    if dims:
        lines.append("Dimensions (in): " + " x ".join(dims))
    short, full, feats = strip_html(p.get("short_description")), strip_html(p.get("full_description")), strip_html(p.get("special_features"))
    if short:
        lines.append(f"Summary: {short}")
    if full:
        lines.append(f"Details: {_cut(full, MAX_DETAILS)}")
    if feats:
        lines.append(f"Features: {_cut(feats, MAX_FEATURES)}")
    return "\n".join(lines)


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


# ---- category tree helpers (vw_chat_categories: parent_id points at category_id, an integer) ----
def descendants(categories: list[dict], root_slugs: list[str]) -> tuple[list[dict], list[str]]:
    """Every category under the given root slugs (roots included). Returns (categories, slugs that matched nothing)."""
    children: dict[Any, list[dict]] = {}
    for c in categories:
        children.setdefault(c["parent_id"], []).append(c)
    by_slug = {c["slug"]: c for c in categories}
    out, missing, seen = [], [], set()
    for slug in root_slugs:
        root = by_slug.get(slug)
        if not root:
            missing.append(slug)
            continue
        stack = [root]
        while stack:
            c = stack.pop()
            if c["category_id"] in seen:
                continue
            seen.add(c["category_id"])
            out.append(c)
            stack.extend(children.get(c["category_id"], []))
    return out, missing


def slugs_with_ancestors(category_uuid: str, by_uuid: dict[str, dict], by_id: dict[Any, dict]) -> list[str]:
    """A product linked to 'Sofas' is also found by filtering on its parent 'Sofas & Seating'."""
    out, c, guard = [], by_uuid.get(category_uuid), 0
    while c and guard < 20:
        out.append(c["slug"])
        c, guard = by_id.get(c["parent_id"]), guard + 1
    return out
