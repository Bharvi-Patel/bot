"""Phase 3 check: the 10 policy (RAG) questions from Appendix D, plus refusal checks, run against the real vector search.

  python eval_phase3.py                      retrieval check: which pages would the bot cite, and how similar are they
  python eval_phase3.py --answer             also have Claude write the answer from the retrieved text only (needs ANTHROPIC_API_KEY)
Set RAG_DB_URL first. The first run loads the embedding model (a few seconds).

Statuses:  PASS    an expected page was retrieved (or, for refusal checks, nothing was retrieved)
           REVIEW  the expected page is not in the index yet (status-0 page waiting for the owner), or a refusal check
                   found text: look at the similarities and tune SJ_RAG_CUTOFF
           FAIL    pages are indexed but the expected one was not retrieved
           ERROR   the search itself failed (see the log line above it)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime

from sjbot.tools.search_policies import CUTOFF, _default_run_query, search_policies

PASS, REVIEW, FAIL, ERROR = "PASS", "REVIEW", "FAIL", "ERROR"

# (Appendix D number, question, pages that may be cited, or None when the right behaviour is a refusal)
QUESTIONS = [
    (1, "What is your return policy?", {"returns", "terms-and-conditions"}),
    (2, "Can I return a mattress?", {"returns", "mattress-buying-guide", "terms-and-conditions"}),
    (3, "How long does delivery take?", {"shipping-delivery"}),
    (4, "Do you offer financing?", {"financing", "financing-by-lendpro"}),
    (5, "What does the warranty cover?", {"warranty-information"}),
    (6, "How do I clean a fabric sofa?", {"care-and-cleaning"}),
    (10, "What is the difference between memory foam and hybrid mattresses?", {"mattress-buying-guide"}),
    (21, "Can I return the Loreo Sofa and what does shipping cost?", {"returns", "terms-and-conditions"}),
    (22, "Best mattress for back pain?", {"mattress-buying-guide"}),
    (26, "Can I cancel my order?", {"returns", "terms-and-conditions"}),
    (27, "Do you have any coupon codes?", None),
    ("x", "Who won the cricket world cup?", None),
]

RULES = (
    "You are the South Jackson Furniture assistant. Answer ONLY from the store text provided. "
    "Quote policy text faithfully and name the page it came from (for example 'According to our Returns page ...'). "
    "If the text does not cover the question, say you don't have that information and point the customer to the store phone. "
    "No medical claims: talk about firmness and support features that appear in the text. "
    "No prices, stock or delivery dates from this text. Keep it short."
)


def indexed_pages() -> set[str]:
    return {r["source_key"] for r in _default_run_query("SELECT DISTINCT source_key FROM rag_chunks", [])}


def write_answer(question: str, out: dict, model: str) -> str:
    import anthropic
    phone = "the store"
    try:
        from sjbot.tools.get_store_info import get_store_info
        phone = get_store_info({})["stores"][0]["phone"] or phone
    except Exception:
        pass
    if out.get("found"):
        ctx = "\n\n".join(f"[{c['title']}, page {c['page']}]\n{c['content']}" for c in out["chunks"])
        user = f"Customer question: {question}\n\nStore text:\n{ctx}\n\nStore phone: {phone}"
    else:
        user = f"Customer question: {question}\n\nNo store text covers this question.\n\nStore phone: {phone}"
    msg = anthropic.Anthropic().messages.create(model=model, max_tokens=500, system=RULES,
                                                messages=[{"role": "user", "content": user}])
    return msg.content[0].text.strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--answer", action="store_true", help="also write the answer with Claude (needs: pip install anthropic, ANTHROPIC_API_KEY)")
    ap.add_argument("--model", default="claude-sonnet-5-5")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)

    have = indexed_pages()
    print(f"cutoff {CUTOFF} | pages in the index: {', '.join(sorted(have))}\n")
    results = []
    for num, question, expected in QUESTIONS:
        print(f"Q{num} running: {question} ...", flush=True)
        out = search_policies({"question": question})
        if "error" in out:
            status, evidence = ERROR, out["error"]
        elif expected is None:
            if not out["found"]:
                status, evidence = PASS, f"refused: best similarity {out['best_similarity']} is under the {CUTOFF} cutoff"
            else:
                top = [(c["source_key"], c["similarity"]) for c in out["chunks"]]
                status, evidence = REVIEW, f"found text for a question the store text should not cover: {top}. Raise SJ_RAG_CUTOFF?"
        else:
            cited = [(c["source_key"], c["similarity"]) for c in out.get("chunks", [])]
            hit = expected & {k for k, _ in cited}
            if hit:
                status, evidence = PASS, f"cites {sorted(hit)}; all retrieved: {cited}"
            elif not (expected & have):
                status, evidence = REVIEW, f"expected page(s) {sorted(expected)} are not indexed yet (waiting for owner confirmation). Retrieved: {cited or 'nothing'}"
            else:
                status, evidence = FAIL, f"expected {sorted(expected)} but retrieved {cited or 'nothing (below cutoff, best ' + str(out.get('best_similarity')) + ')'}"
        row = {"q": num, "question": question, "status": status, "evidence": evidence}
        if args.answer and status != ERROR:
            row["answer"] = write_answer(question, out, args.model)
        results.append(row)
        print(f"Q{num:<2} {status:<6} {question}\n      {evidence}" + (f"\n      ANSWER: {row['answer']}" if "answer" in row else "") + "\n", flush=True)

    counts = {s: sum(r["status"] == s for r in results) for s in (PASS, REVIEW, FAIL, ERROR)}
    print("Summary:", ", ".join(f"{v} {k}" for k, v in counts.items()), f"(of {len(results)})")
    with open("eval_phase3_results.json", "w", encoding="utf-8") as fh:
        json.dump({"run_at": datetime.now().isoformat(timespec="seconds"), "cutoff": CUTOFF, "results": results}, fh, indent=2, ensure_ascii=False)
    print("Saved eval_phase3_results.json")
    return 0 if not (counts[FAIL] or counts[ERROR]) else 1


if __name__ == "__main__":
    sys.exit(main())
