"""list_categories: browse the category tree or find categories by name. Reads the store API (GET /categories/tree).

The whole tree is tiny (161 categories in the saved real response), so we read it once and shape it in Python.
Returned slugs are what search_products takes as category_slug.
"""
from __future__ import annotations

import re
from typing import Any

from bot.api_client import ApiClient, ApiError, get_client

MAX_MATCHES = 10
ALLOWED_KEYS = {"parent_slug", "keyword"}


def _text(args: dict, key: str) -> str | None:
    value = args.get(key)
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} must be text")
    value = value.strip()
    if len(value) > 100:
        raise ValueError(f"{key} is too long")
    return value or None


def _is_root(row: dict) -> bool:
    return not row["parent_id"]


def _path(by_id: dict[int, dict], row: dict) -> str:
    titles, cur, guard = [], row, 0
    while cur is not None and guard < 8:
        titles.append(cur["title"])
        cur = by_id.get(cur["parent_id"]) if cur["parent_id"] else None
        guard += 1
    return " > ".join(reversed(titles))


def rows_from_tree(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten the API's nested tree into rows of category_id, parent_id, slug, title. A node's parent is
    taken from where it sits in the tree, not from a field, and roots get parent_id 0."""
    rows: list[dict[str, Any]] = []

    def walk(node: dict[str, Any], parent_id: Any) -> None:
        cid = node.get("category_id")
        rows.append({"category_id": cid, "parent_id": parent_id, "slug": node.get("slug"), "title": node.get("name")})
        for child in node.get("children") or []:
            walk(child, cid)

    for n in nodes:
        walk(n, 0)
    return rows


def list_categories(args: dict[str, Any], client: ApiClient | None = None) -> dict[str, Any]:
    try:
        unknown = set(args) - ALLOWED_KEYS
        if unknown:
            raise ValueError(f"unknown parameter(s): {', '.join(sorted(unknown))}")
        parent_slug, keyword = _text(args, "parent_slug"), _text(args, "keyword")
        if parent_slug and keyword:
            raise ValueError("use either parent_slug or keyword, not both")
    except ValueError as exc:
        return {"error": str(exc)}

    try:
        rows = rows_from_tree((client or get_client()).categories())
    except ApiError:
        return {"error": "The category list is not available right now. Do not guess category names or slugs."}
    by_id = {r["category_id"]: r for r in rows}
    children: dict[int, list[dict]] = {}
    for r in rows:
        if r["parent_id"]:
            children.setdefault(r["parent_id"], []).append(r)

    def brief(r: dict) -> dict:
        return {
            "slug": r["slug"],
            "title": r["title"],
            "has_subcategories": r["category_id"] in children,
        }

    if keyword:
        from bot.tools.search_products import _stem          # 'beds' and 'bed' are the same word here
        needle = keyword.lower()
        word = _stem(needle)
        hits = [r for r in rows if needle in (r["title"] or "").lower() or word in (r["title"] or "").lower()]

        def rank(r: dict) -> tuple:
            """Categories whose title has the keyword as a whole word (Beds, Bed Pillows) come before ones that merely
            contain it (Bedroom, Bedding), then shallow before deep. Otherwise 'Beds' sits below the 10-match cut."""
            words = [_stem(w) for w in re.findall(r"[a-z0-9]+", (r["title"] or "").lower())]
            return (0 if word in words else 1, _path(by_id, r).count(">"), r["title"] or "")

        hits.sort(key=rank)
        result = {
            "matches": [dict(brief(r), path=_path(by_id, r)) for r in hits[:MAX_MATCHES]],
            "note": None if hits else "No category title contains that word. Try a shorter or different word.",
        }
        if len(hits) > MAX_MATCHES:
            result["total_matches"] = len(hits)
            result["note"] = f"Showing the best {MAX_MATCHES} of {len(hits)} matches. Use a more specific word to narrow it."
        return result

    if parent_slug:
        parent = next((r for r in rows if r["slug"] == parent_slug), None)
        if parent is None:
            return {"found": False, "note": "No category has that slug. Call list_categories with no arguments to see the top level."}
        kids = sorted(children.get(parent["category_id"], []), key=lambda r: r["title"])
        return {
            "parent": dict(brief(parent), path=_path(by_id, parent)),
            "categories": [brief(r) for r in kids],
        }

    roots = sorted((r for r in rows if _is_root(r)), key=lambda r: r["title"])
    return {"categories": [brief(r) for r in roots],
            "note": "Tell the customer these category names (all of them, briefly, in one sentence or a short list). "
                    "Do not describe them in general terms and do not answer with a question instead."}