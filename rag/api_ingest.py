"""Per-store ingest of policy-index text from the storefront API (no SQL dump, no database).

    python rag/api_ingest.py <store uuid> [--types faq]   ->  writes chunks.jsonl for that store
    python rag/sync.py <store uuid> --types faq           ->  syncs those rows into the RAG database

Only the types whose API response shape has been seen are supported. Today that is FAQs (GET /faqs/global).
Pages (GET /page/{slug}) and blogs (GET /blogs) are NOT done: no saved response exists for them, and their field
names are not guessed. Add each one here, with a fixture and a test, once a real response is available.

Row format is the one rag/build.py writes and rag/sync.py reads, so the search tools need no change.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))      # project root, so bot is importable
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))             # so build's cleaning helpers are importable

SUPPORTED_TYPES = ("faq",)


def _row(source_type: str, key: str, title: str, heading: str, text: str, url: str) -> dict:
    content = f"{heading}\n\n{text}"
    return dict(source_type=source_type, source_key=key, title=title, heading=heading, url_path=url,
                brand_slug=None, category_slugs=None, price=None, content=content,
                content_hash=hashlib.sha256(content.encode()).hexdigest(), embedding=None, updated_at=None)


INLINE_TAGS = ["a", "b", "strong", "i", "em", "u", "span", "small", "mark", "code"]


def _unwrap_inline(html_text: str) -> str:
    """build.html_to_markdown puts every tag's text on its own line, which would cut 'we accept <b>cash</b> only'
    into three lines. Dropping the inline wrappers first keeps a sentence in one piece; block tags are untouched."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html_text or "", "html.parser")
    for tag in soup.find_all(INLINE_TAGS):
        tag.unwrap()
    return str(soup)


def faq_rows(client) -> list[dict]:
    """One chunk per FAQ: the question as heading, the answer as text. FAQs are short, so they are not split."""
    from build import html_to_markdown, strip_boilerplate
    rows = []
    for f in client.faqs():
        text = strip_boilerplate(html_to_markdown(_unwrap_inline(f["answer_html"])))
        if text:
            rows.append(_row("faq", f["slug"], f["question"], f["question"], text, "/faqs"))
    return rows


def ingest(client, types=SUPPORTED_TYPES) -> list[dict]:
    bad = [t for t in types if t not in SUPPORTED_TYPES]
    if bad:
        raise ValueError(f"not supported yet: {', '.join(bad)} (supported: {', '.join(SUPPORTED_TYPES)})")
    rows: list[dict] = []
    if "faq" in types:
        rows += faq_rows(client)
    return rows


def main(argv: list[str] | None = None) -> None:
    import argparse
    from bot.api_client import ApiConfig, ApiClient
    from bot.store_config import valid_store_id
    ap = argparse.ArgumentParser(description="Write chunks.jsonl for one store from its storefront API.")
    ap.add_argument("store_uuid")
    ap.add_argument("--types", default=",".join(SUPPORTED_TYPES), help="comma list; supported: " + ",".join(SUPPORTED_TYPES))
    args = ap.parse_args(argv)
    if not valid_store_id(args.store_uuid):
        ap.error("store_uuid has characters that are not allowed")
    types = tuple(t for t in args.types.split(",") if t)
    try:
        rows = ingest(ApiClient(ApiConfig.from_env(args.store_uuid)), types)
    except ValueError as exc:
        ap.error(str(exc))
    with open("chunks.jsonl", "w", encoding="utf8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"wrote {len(rows)} chunks for store {args.store_uuid} ({', '.join(types)})")


if __name__ == "__main__":
    main()
