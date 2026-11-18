"""Capture real storefront API responses for store PAGES and BLOGS, so rag/api_ingest.py can index them without guessing
field names. READ-ONLY, same setup as the earlier capture scripts. In PowerShell, in the bot repo:

    $env:BOT_API_BASE_URL = "http://127.0.0.1:8001/"
    $env:BOT_STORE_UUID = "<your store uuid>"
    python capture_pages.py

It writes ./captured5/ (one file per call, plus summary.txt). Send me the folder or summary.txt.
  1. GET /stores                  the store row; its page list is where the page slugs come from
  2. GET /page/{slug}             for each page slug found there (at most 25)
  3. GET /blogs                   and the first blog's own page, if the list gives a slug
  4. GET /faqs/global             again, for comparison with the saved fixture
"""
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

BASE = os.environ["BOT_API_BASE_URL"].rstrip("/")
UUID = os.environ["BOT_STORE_UUID"]
OUT = Path("captured5")
OUT.mkdir(exist_ok=True)
summary = []


def call(label, path):
    req = urllib.request.Request(BASE + path, headers={"Store-UUID": UUID, "Accept": "application/json"}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            status, raw = r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        status, raw = None, f"{type(e).__name__}: {e}"
    (OUT / f"{label}.json").write_text(json.dumps({"request": {"method": "GET", "path": path}, "status": status,
                                                   "response": raw}, indent=1), encoding="utf-8")
    line = f"{label:34} GET {path:36} status={status} bytes={len(raw)}"
    summary.append(line)
    print(line)
    try:
        return json.loads(raw)
    except Exception:
        return None


def find_slugs(node, under_pages=False, found=None, trail=""):
    """Every {"slug": ...} that sits inside a list or object whose key mentions 'page' (or 'blog' for blogs)."""
    found = [] if found is None else found
    if isinstance(node, dict):
        if under_pages and isinstance(node.get("slug"), str):
            found.append((trail, node["slug"]))
        for k, v in node.items():
            find_slugs(v, under_pages or "page" in k.lower(), found, f"{trail}/{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            find_slugs(v, under_pages, found, f"{trail}[{i}]")
    return found


stores = call("00_stores", "/stores")
slugs = []
for where, slug in find_slugs(stores):
    if slug not in slugs:
        slugs.append(slug)
summary.append(f"page slugs found in /stores ({len(slugs)}): {slugs[:25]}")
print(summary[-1])
for i, slug in enumerate(slugs[:25]):
    call(f"10_page_{i:02d}_{slug[:40]}", f"/page/{slug}")

blogs = call("20_blogs", "/blogs")
blog_slugs = []
for _, slug in find_slugs(blogs, under_pages=True):
    if slug not in blog_slugs:
        blog_slugs.append(slug)
summary.append(f"blog slugs found in /blogs ({len(blog_slugs)}): {blog_slugs[:5]}")
print(summary[-1])
if blog_slugs:
    call("21_blog_first", f"/blogs/{blog_slugs[0]}")

call("30_faqs_global", "/faqs/global")

(OUT / "summary.txt").write_text("\n".join(summary), encoding="utf-8")
print("\nSaved to ./captured5/ ; send the folder or summary.txt")
