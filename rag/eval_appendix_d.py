"""Appendix D questions that route through policy/guide RAG. Retrieval-only check (BM25 stand-in for the keyword half
of 5.5; the vector half needs your embedding model). Shows the pages the bot would cite.
  python3 eval_appendix_d.py                              # live chunks only (what 5.6 allows today)
  python3 eval_appendix_d.py returns shipping-delivery    # pretend those status-0 pages were confirmed"""
import json, math, re, sys
from collections import Counter
confirmed = set(sys.argv[1:])
rows = [json.loads(l) for l in open("chunks.jsonl")] + [r for r in map(json.loads, open("pending.jsonl")) if r["source_key"] in confirmed]
STOP = set("a an the is are do you i my me can of to for in on and or what how does it your we our be if with at there any".split())
tok = lambda s: [w for w in re.findall(r"[a-z0-9']+", s.lower()) if w not in STOP]
stem = lambda w: re.sub(r"(ing|ed|es|s)$", "", w) if len(w) > 4 else w
docs = [Counter(stem(w) for w in tok(r["content"])) for r in rows]
df = Counter(w for d in docs for w in d); N = len(docs); avg = sum(sum(d.values()) for d in docs) / N
POLICY_ROUTE = {"policy", "guide", "faq"}     # policy/guide questions filter on source_type (5.3 index), brand_info/blog excluded
def search(q, k=3):
    qs = [stem(w) for w in tok(q)]; res = []
    for r, d in zip(rows, docs):
        if r["source_type"] not in POLICY_ROUTE: continue
        L = sum(d.values()); s = sum(math.log(1 + (N - df[w] + .5) / (df[w] + .5)) * d[w] * 2.2 / (d[w] + 1.2 * (.25 + .75 * L / avg)) for w in qs if w in d)
        res.append((s, r))
    return sorted(res, key=lambda x: -x[0])[:k]
# (App. D #, question, pages that should be cited | None = no source covers it, bot must decline)
QS = [
 (1, "What is your return policy?", {"returns"}),
 (2, "Can I return a mattress?", {"returns", "mattress-buying-guide"}),
 (3, "How long does delivery take?", {"shipping-delivery"}),
 (4, "Do you offer financing?", {"financing", "financing-by-lendpro"}),
 (5, "What does the warranty cover?", {"warranty-information"}),
 (6, "How do I clean a fabric sofa?", {"care-and-cleaning"}),
 (10, "What is the difference between memory foam and hybrid mattresses?", {"mattress-buying-guide"}),
 (21, "Can I return the Loreo Sofa and what does shipping cost?", {"returns", "shipping-delivery"}),
 (22, "Best mattress for back pain?", {"mattress-buying-guide"}),
 (26, "Can I cancel my order?", {"returns", "terms-and-conditions"}),
 (27, "Do you have any coupon codes?", None),
 (28, "Do you ship to Canada?", {"shipping-delivery"}),
]
ok = 0
for n, q, want in QS:
    res = search(q); pages = [r["source_key"] for s, r in res]
    cite = sorted(set(pages))
    if want is None: tag = "DECLINE?"      # nothing in the corpus covers it; score cutoff must be tuned on embeddings
    elif want & set(pages): tag = "PASS"; ok += 1
    else: tag = "NOT-INDEXED" if not (want & {r["source_key"] for r in rows}) else "MISS"
    print(f"#{n:<2} {tag:12}{q}\n      want={sorted(want) if want else 'none'}  top3={[(round(s,1), r['source_key'], r['heading'].split(' > ',1)[-1][:32]) for s, r in res]}")
print(f"\nretrieval pass: {ok}/{sum(1 for _,_,w in QS if w)} answerable questions | confirmed pages: {sorted(confirmed) or 'none'}")
