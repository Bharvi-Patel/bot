"""list_categories: browse the category tree or find categories by name. Reads vw_chat_categories only.

The whole tree is tiny (about 200 rows), so we read it once and shape it in Python.
Returned slugs are what search_products takes as category_slug.
"""
from __future__ import annotations

import re
from typing import Any, Callable

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


def list_categories(args: dict[str, Any], run_query: Callable | None = None) -> dict[str, Any]:
    if run_query is None:
        from sjbot.db import run_query as default_run_query
        run_query = default_run_query
    try:
        unknown = set(args) - ALLOWED_KEYS
        if unknown:
            raise ValueError(f"unknown parameter(s): {', '.join(sorted(unknown))}")
        parent_slug, keyword = _text(args, "parent_slug"), _text(args, "keyword")
        if parent_slug and keyword:
            raise ValueError("use either parent_slug or keyword, not both")
    except ValueError as exc:
        return {"error": str(exc)}

    rows = run_query("SELECT category_id, parent_id, slug, title FROM vw_chat_categories")
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
        from sjbot.tools.search_products import _stem          # 'beds' and 'bed' are the same word here
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