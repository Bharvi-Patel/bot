"""5.7 nightly sync: read chunks.jsonl, upsert only rows whose content_hash changed, delete rows whose source is gone
or went to status 0, log added/changed/removed. Uses only the 5.3 columns. Identity of a chunk = (source_key, content_hash).
Run build.py first (it re-reads the dump export). Plug your existing embedding function into embed()."""
import json, os, psycopg                      # pip install "psycopg[binary]" pgvector
from pgvector.psycopg import register_vector

POLICY_TYPES = ("policy", "guide", "brand_info", "blog", "faq")      # never touches source_type='product'

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))      # project root, so the sjbot package is importable
from sjbot.embeddings import embed_documents as embed                        # same model + prefixes the search tool uses

def main():
    rows = [json.loads(l) for l in open("chunks.jsonl")]
    want = {(r["source_key"], r["content_hash"]): r for r in rows}
    with psycopg.connect(os.environ["RAG_DB_URL"]) as con:
        register_vector(con)
        have = {(k, h) for k, h in con.execute(
            "SELECT source_key, content_hash FROM rag_chunks WHERE source_type = ANY(%s)", (list(POLICY_TYPES),))}
        new = [want[k] for k in want if k not in have]
        gone = [k for k in have if k not in want]
        vecs = embed([r["content"] for r in new]) if new else []
        for r, v in zip(new, vecs):
            con.execute("""INSERT INTO rag_chunks (source_type, source_key, title, heading, url_path, brand_slug,
                           category_slugs, price, content, content_hash, embedding, updated_at)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (r["source_type"], r["source_key"], r["title"], r["heading"], r["url_path"], r["brand_slug"],
                         r["category_slugs"], r["price"], r["content"], r["content_hash"], v, r["updated_at"]))
        for k, h in gone:
            con.execute("DELETE FROM rag_chunks WHERE source_key=%s AND content_hash=%s", (k, h))
        print(f"added {len(new)}, removed {len(gone)}, unchanged {len(want) - len(new)}")

if __name__ == "__main__": main()