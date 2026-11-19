"""Per-store ingest of policy-index text from the storefront API (no SQL dump, no database).

    python rag/api_ingest.py <store uuid> [--types faq]   ->  writes chunks.jsonl for that store
    python rag/sync.py <store uuid> --types faq           ->  syncs those rows into the RAG database

Only the types whose API response shape has been seen are supported. Today that is FAQs (GET /faqs/global) and
pages (GET /page/{slug}, the slugs coming from GET /stores), and blogs (GET /blogs/active for the list, GET /blogs/{slug}
for the text). Blogs are opt-in (--types blog): they are not part of the default run. The API lists 10 blogs at a time and
how to ask for the next page has not been seen, so a store with more than 10 gets a warning that the list is partial.
Blog rows point at /blogs/{slug}, the API's own route name; the storefront's real blog URL has not been checked.
Add each new type here, with a fixture and a test, once a real response is available.

Pages become rows of type policy or guide (what sync.py --types takes after a page run). A page is indexed only if it
is live (status 1, not deleted), has text, and its slug is recognised as a policy or guide page or named with
--page-type slug=type. Every page left out is printed with the reason, and a page that still carries another store's
demo data (the B3 template comment) is flagged, so nothing is dropped silently.

Row format is the one rag/sync.py reads, so the search tools need no change.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))      # project root, so bot is importable
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))             # so text_utils is importable

SUPPORTED_TYPES = ("faq", "page", "blog")
DEFAULT_TYPES = ("faq", "page")                 # what a run without --types does; blogs are opt-in
PAGE_TYPES = ("policy", "guide", "brand_info")
# Slug words that mark a page as store policy or help text. Anything else (home page, brand and landing pages) is left out
# unless the person running the ingest names it with --page-type.
POLICY_WORDS = ("return", "refund", "shipping", "delivery", "warranty", "terms", "privacy", "financ", "policy", "cancel", "payment")
GUIDE_WORDS = ("care", "clean", "guide", "about", "contact")
NEVER_INDEXED = {"home-page", "promotions", "sitemap", "test"}


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
    from text_utils import html_to_markdown, strip_boilerplate
    rows = []
    for f in client.faqs():
        text = strip_boilerplate(html_to_markdown(_unwrap_inline(f["answer_html"])))
        if text:
            rows.append(_row("faq", f["slug"], f["question"], f["question"], text, "/faqs"))
    return rows


def classify_page(slug: str, overrides: dict[str, str] | None = None) -> str | None:
    """policy | guide | brand_info, or None when the page is not recognised (and so not indexed)."""
    if overrides and slug in overrides:
        return overrides[slug]
    s = slug.lower()
    if s in NEVER_INDEXED:
        return None
    if any(w in s for w in POLICY_WORDS):
        return "policy"
    if any(w in s for w in GUIDE_WORDS):
        return "guide"
    return None


def _digits(text: str | None) -> str:
    return re.sub(r"\D", "", text or "")


def demo_leftovers(raw_html: str, clean_text: str, store: dict) -> list[str]:
    """B3 page templates carry a comment such as 'Swap demo data ("Name", (555) 123-4567, example.com, City)'. If a phone
    or domain from that list is still in the page text and is not this store's own, the page was never adapted to the store."""
    low = clean_text.lower()
    line = next((l for l in raw_html.splitlines() if "swap demo data" in l.lower()), "")
    if "(" not in line or ")" not in line:
        return []
    listed = line[line.index("(", line.lower().index("swap demo data")) + 1: line.rindex(")")]
    own_phone = _digits(store.get("phone"))
    own = re.sub(r"[^a-z0-9]", "", ((store.get("name") or "") + (store.get("email") or "")).lower())
    left = []
    for tok in (t.strip().strip('"') for t in listed.split(", ")):
        if re.fullmatch(r"\(?\d{3}\)?[ -]\d{3}-\d{4}", tok):
            if own_phone and _digits(tok) != own_phone and _digits(tok) in _digits(clean_text):
                left.append(tok)
        elif re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+", tok.lower()):
            stem = tok.lower().split(".")[0]
            if own and stem not in own and tok.lower() in low:
                left.append(tok)
    return left


def page_rows(client, overrides: dict[str, str] | None = None, skipped: list | None = None,
              warnings: list | None = None) -> list[dict]:
    """Chunks for the store's live policy and help pages. Pages left out go into `skipped` as (slug, reason); pages that
    still hold another store's demo data go into `warnings`."""
    from text_utils import chunk_text, html_to_markdown, strip_boilerplate
    skipped = skipped if skipped is not None else []
    warnings = warnings if warnings is not None else []
    store = client.store_info()
    rows: list[dict] = []
    for entry in client.store_pages():
        slug = entry.get("slug")
        if not slug:
            continue
        kind = classify_page(slug, overrides)
        if kind is None:
            skipped.append((slug, "slug not recognised as a policy or guide page (use --page-type to include it)"))
            continue
        if entry.get("status") != 1:
            skipped.append((slug, "not live (status is not 1)"))
            continue
        page = client.page(slug)
        if page["deleted"] or page["status"] != 1:
            skipped.append((slug, "deleted or not live"))
            continue
        if not page["html"].strip():
            skipped.append((slug, "page-builder page with no text in long_description (no sample response yet)"
                            if page["advanced"] else "no text"))
            continue
        text = strip_boilerplate(html_to_markdown(_unwrap_inline(page["html"])))
        if not text.strip():
            skipped.append((slug, "no text after cleaning"))
            continue
        leftovers = demo_leftovers(page["html"], text, store)
        if leftovers:
            warnings.append(f"{slug}: still has demo data from another store ({', '.join(leftovers)})")
        title = page["title"] or entry.get("title") or slug
        for crumb, body in chunk_text(title, text):
            rows.append(_row(kind, slug, title, crumb, body, f"/{slug}") | {"updated_at": page["updated_at"]})
    return rows


def blog_rows(client, skipped: list | None = None, warnings: list | None = None) -> list[dict]:
    """Chunks for the store's live blog posts: the title as heading, the post text split like a page."""
    from text_utils import chunk_text, html_to_markdown, strip_boilerplate
    skipped = skipped if skipped is not None else []
    warnings = warnings if warnings is not None else []
    listing = client.blogs()
    if listing["partial"]:
        warnings.append(f"blogs: only {len(listing['blogs'])} of {listing['total']} posts were read (the API lists 10 at a time "
                        "and how to read the next page is not known yet)")
    rows: list[dict] = []
    for entry in listing["blogs"]:
        slug = entry["slug"]
        if entry.get("status") != 1 or entry.get("deleted_at"):
            skipped.append((f"blog:{slug}", "deleted or not live"))
            continue
        post = client.blog(slug)
        if post["status"] != 1:
            skipped.append((f"blog:{slug}", "not live"))
            continue
        text = strip_boilerplate(html_to_markdown(_unwrap_inline(post["html"])))
        if not text.strip():
            skipped.append((f"blog:{slug}", "no text"))
            continue
        title = post["title"] or entry.get("title") or slug
        for crumb, body in chunk_text(title, text):
            rows.append(_row("blog", slug, title, crumb, body, f"/blogs/{slug}") | {"updated_at": entry.get("updated_at")})
    return rows


def ingest(client, types=DEFAULT_TYPES, overrides: dict[str, str] | None = None, skipped: list | None = None,
           warnings: list | None = None) -> list[dict]:
    bad = [t for t in types if t not in SUPPORTED_TYPES]
    if bad:
        raise ValueError(f"not supported yet: {', '.join(bad)} (supported: {', '.join(SUPPORTED_TYPES)})")
    rows: list[dict] = []
    if "faq" in types:
        rows += faq_rows(client)
    if "page" in types:
        rows += page_rows(client, overrides, skipped, warnings)
    if "blog" in types:
        rows += blog_rows(client, skipped, warnings)
    return rows


def main(argv: list[str] | None = None) -> None:
    import argparse
    from bot.api_client import ApiConfig, ApiClient
    from bot.store_config import valid_store_id
    ap = argparse.ArgumentParser(description="Write chunks.jsonl for one store from its storefront API.")
    ap.add_argument("store_uuid")
    ap.add_argument("--types", default=",".join(DEFAULT_TYPES), help="comma list; supported: " + ",".join(SUPPORTED_TYPES) + " (default: " + ",".join(DEFAULT_TYPES) + ")")
    ap.add_argument("--page-type", action="append", default=[], metavar="SLUG=TYPE",
                    help="index this page as policy, guide or brand_info (repeat for several pages)")
    args = ap.parse_args(argv)
    overrides = {}
    for item in args.page_type:
        slug, _, kind = item.partition("=")
        if not slug or kind not in PAGE_TYPES:
            ap.error(f"--page-type must look like slug=type with type one of {', '.join(PAGE_TYPES)}: {item!r}")
        overrides[slug] = kind
    if not valid_store_id(args.store_uuid):
        ap.error("store_uuid has characters that are not allowed")
    types = tuple(t for t in args.types.split(",") if t)
    skipped: list = []
    warnings: list = []
    try:
        rows = ingest(ApiClient(ApiConfig.from_env(args.store_uuid)), types, overrides, skipped, warnings)
    except ValueError as exc:
        ap.error(str(exc))
    with open("chunks.jsonl", "w", encoding="utf8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"wrote {len(rows)} chunks for store {args.store_uuid} ({', '.join(types)})")
    for slug, why in skipped:
        print(f"  left out: {slug}: {why}")
    for w in warnings:
        print(f"  WARNING: {w}")
    if "page" in types:
        print("To load page rows: python rag/sync.py <store uuid> --types policy,guide,brand_info")
    if "blog" in types:
        print("To load blog rows: python rag/sync.py <store uuid> --types blog")


if __name__ == "__main__":
    main()
