"""search_policies: finds store policy / guide / FAQ text for the bot to answer from (data map 5.5, 5.6).

Hybrid search over the pgvector table rag_chunks:
  1. vector search with cosine similarity on the embedded question
  2. Postgres full-text search on the question's words
  3. the two rankings are merged (reciprocal rank fusion)
  4. anything below the cosine cutoff is dropped; if nothing is left, found=False and the bot must not answer from RAG

Reads only rag_chunks, in the separate Postgres (RAG_DB_URL). It never touches the shop's MySQL database.
Never returns prices, stock or delivery dates from this text: those come from SQL tools.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Callable

log = logging.getLogger(__name__)

DEFAULT_TYPES = ["policy", "guide", "faq"]
ALLOWED_TYPES = {"policy", "guide", "faq", "brand_info", "blog"}
CUTOFF = float(os.environ.get("SJ_RAG_CUTOFF", "0.55"))    # tune on the evaluation set (Appendix D)
FETCH = 8          # candidates pulled from each search
TOP_K = 6          # chunks handed to the model (map: 6 to 8)
RRF_K = 60
MAX_QUESTION_CHARS = 500

STOP = set(
    "a an the is are was were do does did you your i my me we our can could would should will of to for in on and or "
    "what how when where why who which that this it be if with at about there any have has get tell please".split()
)

COLS = "id, source_type, source_key, title, heading, url_path, content, 1 - (embedding <=> %s::vector) AS similarity"
VECTOR_SQL = (
    f"SELECT {COLS} FROM rag_chunks "
    "WHERE source_type = ANY(%s) AND embedding IS NOT NULL "
    "ORDER BY embedding <=> %s::vector LIMIT %s"
)
KEYWORD_SQL = (
    f"SELECT {COLS} FROM rag_chunks "
    "WHERE source_type = ANY(%s) AND embedding IS NOT NULL "
    "AND to_tsvector('english', content) @@ websearch_to_tsquery('english', %s) "
    "ORDER BY ts_rank_cd(to_tsvector('english', content), websearch_to_tsquery('english', %s)) DESC LIMIT %s"
)

NOTE_FOUND = (
    "Answer ONLY from these chunks. Quote policy text faithfully and name the page it came from "
    "(for example 'According to our Returns page ...'). Do not give prices, stock or delivery dates from this text: "
    "use the SQL tools for those. No medical claims. If the chunks do not fully answer the question, say what they do "
    "say and point the customer to the store phone or email (get_store_info)."
)
NOTE_NOT_FOUND = (
    "Nothing in the store's policy text covers this. Do NOT answer from general knowledge or guess. "
    "Say you don't have that information and point the customer to the store phone or email (get_store_info)."
)


def _default_run_query(sql: str, params: list) -> list[dict]:
    import psycopg
    from psycopg.rows import dict_row
    url = os.environ.get("RAG_DB_URL")
    if not url:
        raise RuntimeError("RAG_DB_URL is not set")
    with psycopg.connect(url, row_factory=dict_row) as con:
        return con.execute(sql, params).fetchall()


def _vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


def keyword_query(question: str) -> str:
    """'What is your return policy?' -> 'return or policy' (websearch syntax; OR so long questions still match)."""
    words = [w for w in re.findall(r"[a-z0-9]+", question.lower()) if w not in STOP and len(w) > 1]
    return " or ".join(dict.fromkeys(words))


def fuse(vector_rows: list[dict], keyword_rows: list[dict], cutoff: float, top_k: int) -> list[dict]:
    """Reciprocal rank fusion of the two result lists, then drop everything under the cosine cutoff."""
    score: dict[Any, float] = {}
    rows: dict[Any, dict] = {}
    for lst in (vector_rows, keyword_rows):
        for rank, r in enumerate(lst):
            score[r["id"]] = score.get(r["id"], 0.0) + 1.0 / (RRF_K + rank + 1)
            rows[r["id"]] = r
    kept = [r for r in rows.values() if float(r["similarity"]) >= cutoff]
    kept.sort(key=lambda r: (-score[r["id"]], -float(r["similarity"])))
    return kept[:top_k]


def search_policies(args: dict[str, Any], run_query: Callable | None = None, embed_query: Callable | None = None) -> dict[str, Any]:
    allowed_args = {"question", "source_types"}
    if not isinstance(args, dict) or set(args) - allowed_args:
        return {"error": "allowed parameters: question (required), source_types (optional)"}
    question = args.get("question")
    if not isinstance(question, str) or not question.strip():
        return {"error": "question is required"}
    question = question.strip()[:MAX_QUESTION_CHARS]
    types = args.get("source_types") or DEFAULT_TYPES
    if not isinstance(types, list) or not types or any(t not in ALLOWED_TYPES for t in types):
        return {"error": f"source_types must be a list from {sorted(ALLOWED_TYPES)}"}

    try:
        if embed_query is None:
            from sjbot.embeddings import embed_query as default_embed
            embed_query = default_embed
        run_query = run_query or _default_run_query
        vec = _vector_literal(embed_query(question))
        vector_rows = run_query(VECTOR_SQL, [vec, types, vec, FETCH])
        kw = keyword_query(question)
        keyword_rows = run_query(KEYWORD_SQL, [vec, types, kw, kw, FETCH]) if kw else []
    except Exception:
        log.exception("search_policies failed")
        return {"error": "policy search is unavailable right now",
                "note": "Do not guess. Point the customer to the store phone or email (get_store_info)."}

    best = max((float(r["similarity"]) for r in vector_rows), default=None)
    kept = fuse(vector_rows, keyword_rows, CUTOFF, TOP_K)
    if not kept:
        return {"found": False, "cutoff": CUTOFF, "best_similarity": round(best, 3) if best is not None else None,
                "note": NOTE_NOT_FOUND}
    return {
        "found": True,
        "cutoff": CUTOFF,
        "chunks": [{
            "title": r["title"], "heading": r["heading"], "page": r["url_path"],
            "source_type": r["source_type"], "source_key": r["source_key"],
            "similarity": round(float(r["similarity"]), 3), "content": r["content"],
        } for r in kept],
        "note": NOTE_FOUND,
    }
