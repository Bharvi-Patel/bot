"""Phase 3 (Policy RAG), built strictly from southjackson_chatbot_data_map.md sections 5.1-5.7.
Reads raw.json (pages/faqs/blogs pulled from the dump by extract.py). Writes:
  chunks.jsonl   -> rows to index now (live sources)
  pending.jsonl  -> same schema, status-0 sources; merge in only after the owner confirms (5.6)
"""
import json, re, html, hashlib
from bs4 import BeautifulSoup

# ---------- 5.2 cleaning (function copied from the map) ----------
def html_to_markdown(raw: str) -> str:
    soup = BeautifulSoup(raw or "", "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "form", "iframe"]):
        tag.decompose()
    for h in soup.find_all(["h1", "h2", "h3", "h4"]):
        level = int(h.name[1])
        h.replace_with("\n\n" + "#" * level + " " + h.get_text(" ", strip=True) + "\n")
    for li in soup.find_all("li"):
        li.replace_with("\n- " + li.get_text(" ", strip=True))
    for tr in soup.find_all("tr"):
        tr.replace_with("\n" + " | ".join(c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])))
    text = html.unescape(soup.get_text("\n"))
    text = re.sub(r"[ \t\xa0]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()

def strip_boilerplate(t):
    """5.2 'read the cleaned text for boilerplate': page nav, 'Section 01' labels, emoji-only lines, CTA buttons,
    and the contact footer that repeats on every page. Also re-joins words the HTML split across lines."""
    t = re.sub(r"## (Jump to a section|Quick Navigation)\n(?:\s*- [^\n]*\n)+", "", t)
    t = re.sub(r"^(Quick Navigation|On This Page)\n(?:\s*- [^\n]*\n)+", "", t, flags=re.M)
    t = re.sub(r"^Section \d+\n", "", t, flags=re.M)
    t = re.sub(r"^[\U0001F300-\U0001FAFF\u2600-\u27BF]\ufe0f?\n", "", t, flags=re.M)
    t = re.sub(r"^- [→✓✗] ", "- ", t, flags=re.M)
    t = re.sub(r"^(Contact Us|Shop Mattresses|View Financing|Shop Now|Shop All|Apply Now|shop now|shop all) ?$", "", t, flags=re.M)
    t = re.sub(r"\n(?:Call )?\(731\) 423-6565\n\nsouthjacksonfurniture@gmail\.com\s*$", "", t)
    t = re.sub(r"(?<![\n#\-])\n(?=[a-z\(\.,;:\)\$\d'\"“”—–-])(?!- )", " ", t)
    t = re.sub(r"\n(?=[\.,;:\)])", "", t)
    t = re.sub(r"\n- \n", "\n", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()

# ---------- 5.3 chunking: split on headings, 250-450 tokens, ~40 token overlap, breadcrumb prefix ----------
TOK = 4                       # chars per token (rough)
MAX_C, MIN_C, OVERLAP_C = 450 * TOK, 250 * TOK, 40 * TOK

def sections(text):
    out, h2, h3, buf = [], "", "", []
    def flush():
        body = "\n".join(buf).strip()
        if body: out.append((h2, h3, body))
        buf.clear()
    for line in text.split("\n"):
        m = re.match(r"(#{1,4}) (.+)", line)
        if not m: buf.append(line); continue
        flush()
        n = len(m.group(1))
        if n == 2: h2, h3 = m.group(2), ""
        elif n >= 3: h3 = m.group(2)
    flush(); return out

def split_long(body):
    paras = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    chunks, cur = [], ""
    for p in paras:
        while len(p) > MAX_C:                              # one huge paragraph: cut at a sentence/space
            cut = p.rfind(". ", 0, MAX_C); cut = cut + 1 if cut > MAX_C // 2 else MAX_C
            head, p = p[:cut], p[cut:].lstrip()
            if cur: chunks.append(cur); cur = ""
            chunks.append(head)
        if cur and len(cur) + len(p) + 2 > MAX_C:
            chunks.append(cur)
            tail = cur[-OVERLAP_C:]; tail = tail.split(" ", 1)[-1]
            cur = tail + "\n\n" + p
        else:
            cur = (cur + "\n\n" + p) if cur else p
    if cur: chunks.append(cur)
    return chunks

def chunk_text(title, text):
    """Pack consecutive heading sections up to MAX_C so chunks land in the 250-450 token band; a section bigger than
    MAX_C is split by paragraph with overlap. Breadcrumb = page > first heading in the chunk."""
    units = []                                              # (breadcrumb, heading_line_for_body, body)
    for h2, h3, body in sections(text):
        crumb = " > ".join(x for x in (title, h2, h3) if x)
        for piece in (split_long(body) if len(body) > MAX_C else [body]):
            units.append((crumb, (h3 or h2), piece))
    chunks, cur_crumb, cur = [], None, ""
    for crumb, head, body in units:
        if cur and len(cur) + len(body) + len(head) + 4 > MAX_C and len(cur) >= MIN_C // 2:
            chunks.append((cur_crumb, cur)); cur_crumb, cur = None, ""
        if not cur:
            cur_crumb, cur = crumb, body
        else:
            cur += "\n\n" + head + "\n" + body
    if cur: chunks.append((cur_crumb, cur))
    # a runt tail (< 80 chars) is folded into the previous chunk
    if len(chunks) > 1 and len(chunks[-1][1]) < 80:
        c, t = chunks.pop(); chunks[-1] = (chunks[-1][0], chunks[-1][1] + "\n\n" + t)
    return chunks

# ---------- 5.1 corpus manifest ----------
PAGES = {   # slug -> source_type (policy | guide | brand_info per 5.3)
    "returns": "policy", "shipping-delivery": "policy", "warranty-information": "policy",
    "terms-and-conditions": "policy", "privacy-policy": "policy", "financing": "policy",
    "financing-by-lendpro": "policy",
    "care-and-cleaning": "guide", "mattress-buying-guide": "guide", "about-us": "guide", "contact-us": "guide",
    "nectar": "brand_info", "dreamcloud": "brand_info", "sierra-sleep": "brand_info", "ashley-sleep": "brand_info",
}
SKIP = {"home-page", "audience-iq", "promotions", "sitemap", "test", "dj", "efw", "ten"}
URL = {"page": lambda s: f"/{s}", "blog": lambda s: f"/blog/{s}", "faq": lambda s: "/faqs"}   # ASSUMED routes

def row(source_type, key, title, crumb, text, url, updated):
    content = f"{crumb}\n\n{text}"
    return dict(source_type=source_type, source_key=key, title=title, heading=crumb, url_path=url,
                brand_slug=None, category_slugs=None, price=None, content=content,
                content_hash=hashlib.sha256(content.encode()).hexdigest(), embedding=None, updated_at=updated)

def builder_json_text(raw):
    """ashley-sleep keeps ~8k chars of page-builder JSON in `description`; pull the HTML out of its text elements."""
    try:
        data = json.loads(raw)
        if isinstance(data, str): data = json.loads(data)      # the column holds JSON that was JSON-encoded twice
    except Exception: return ""
    found = []
    def walk(x):
        if isinstance(x, dict):
            if x.get("type") == "text" and isinstance(x.get("content"), str): found.append(x["content"])
            for v in x.values(): walk(v)
        elif isinstance(x, list):
            for v in x: walk(v)
    walk(data)
    return "\n\n".join(html_to_markdown(f) for f in found)

def main():
    d = json.load(open("raw.json"))
    en = next(l["uuid"] for l in d["languages"] if l["code"] == "en")
    live, pending, report = [], [], []

    pl = {p["page_id"]: p for p in d["page_languages"] if p["language_id"] == en}
    for p in d["pages"]:
        slug = p["slug"]
        if slug in SKIP or slug not in PAGES or p["deleted_at"]: continue
        l = pl[p["uuid"]]; title = l["title"]
        text = strip_boilerplate(html_to_markdown(l["long_description"]))
        if slug == "ashley-sleep":                          # 5.1: "+8,099 in `description`"
            extra = strip_boilerplate(builder_json_text(l["description"] or ""))
            seen = set(text.split("\n"))
            extra = "\n".join(x for x in extra.split("\n") if x not in seen).strip()
            report.append(f"ashley-sleep: description column added {len(extra)} chars not already in long_description")
            if extra: text += "\n\n## More about Ashley Sleep\n" + extra
        status = int(p["status"])
        for crumb, body in chunk_text(title, text):
            (live if status == 1 else pending).append(row(PAGES[slug], slug, title, crumb, body, URL["page"](slug), p["updated_at"]))

    bl = {b["blog_id"]: b for b in d["blog_languages"] if b["language_id"] == en}
    for b in d["blogs"]:
        if b["deleted_at"]: continue
        l = bl[b["uuid"]]; text = strip_boilerplate(html_to_markdown(l["description"]))
        for crumb, body in chunk_text(l["title"], text):
            (live if int(b["status"]) == 1 else pending).append(row("blog", b["slug"], l["title"], crumb, body, URL["blog"](b["slug"]), b["updated_at"]))

    fl = {f["faq_id"]: f for f in d["faq_languages"] if f["language_id"] == en}
    for f in d["faqs"]:           # 5.1: product_faqs links are orphaned -> treat all FAQs as global; category_faqs add nothing new
        if f["deleted_at"] or int(f["status"]) != 1: continue
        l = fl[f["uuid"]]
        live.append(row("faq", f["slug"], l["question"], l["question"], html_to_markdown(l["answer"]), URL["faq"](f["slug"]), f["updated_at"]))

    for name, rows in (("chunks.jsonl", live), ("pending.jsonl", pending)):
        with open(name, "w") as fh:
            for r in rows: fh.write(json.dumps(r) + "\n")
    from collections import defaultdict
    agg = defaultdict(list)
    for tag, rows in (("LIVE", live), ("PENDING", pending)):
        for r in rows: agg[(tag, r["source_type"], r["source_key"])].append(len(r["content"]) // TOK)
    for (tag, st, k), L in sorted(agg.items()):
        print(f"{tag:8}{st:11}{k:46.46} chunks={len(L):3} tokens min/avg/max={min(L)}/{sum(L)//len(L)}/{max(L)}")
    print("live", len(live), "pending", len(pending)); print(*report, sep="\n")

if __name__ == "__main__": main()
