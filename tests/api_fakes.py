"""Test helpers for tools that read the store API."""
from __future__ import annotations


class TreeClient:
    """A fake API client whose categories() returns a nested tree built from flat rows
    (category_id, parent_id, slug, title), the way GET /categories/tree nests them."""

    def __init__(self, rows):
        self.rows = [dict(r) for r in rows]

    def categories(self):
        kids: dict = {}
        for r in self.rows:
            kids.setdefault(r["parent_id"] or 0, []).append(r)

        def node(r):
            return {"category_id": r["category_id"], "slug": r["slug"], "name": r["title"],
                    "children": [node(c) for c in kids.get(r["category_id"], [])]}

        return [node(r) for r in kids.get(0, [])]
