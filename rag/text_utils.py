"""Text helpers shared by the page and FAQ ingest (rag/api_ingest.py): turn page HTML into clean markdown, drop boilerplate
and split it into search chunks. Moved here from the old dump-based builder (rag/build.py), which has been removed."""
import re
import html
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
    t = re.sub(r"\n(?:Call )?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\n\n[\w.+-]+@[\w-]+\.[\w.-]+\s*$", "", t)
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
