"""5.7 nightly sync: read chunks.jsonl, upsert only rows whose content_hash changed, delete rows whose source is gone
or went to status 0, log added/changed/removed. Uses only the 5.3 columns. Identity of a chunk = (source_key, content_hash).
Run build.py first (it re-reads the dump export). Plug your existing embedding function into embed()."""
import json, os, psycopg                      # pip install "psycopg[binary]" pgvector
try:
    from pgvector.psycopg import register_vector
except ImportError:                           # only needed when actually writing to the database
    register_vector = None

POLICY_TYPES = ("policy", "guide", "brand_info", "blog", "faq")      # never touches source_type='product'

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))      # project root, so the bot package is importable
from bot.embeddings import embed_documents as embed                        # same model + prefixes the search tool uses

def plan_changes(rows, have):
    """Which chunks to add and which keys to remove. rows: the wanted chunks; have: {(source_key, content_hash)} already stored."""
    want = {(r["source_key"], r["content_hash"]): r for r in rows}
    return [want[k] for k in want if k not in have], [k for k in have if k not in want], want


def main():
    import sys
    from bot.store_config import valid_store_id
    if len(sys.argv) != 2 or not valid_store_id(sys.argv[1]):
        sys.exit("usage: python rag/sync.py <store uuid>   (syncs chunks.jsonl into that store's rows only)")
    store = sys.argv[1]
    rows = [json.loads(l) for l in open("chunks.jsonl")]
    with psycopg.connect(os.environ["RAG_DB_URL"]) as con:
        register_vector(con)
        have = {(k, h) for k, h in con.execute(
            "SELECT source_key, content_hash FROM rag_chunks WHERE store_uuid = %s AND source_type = ANY(%s)",
            (store, list(POLICY_TYPES)))}
        new, gone, want = plan_changes(rows, have)
        vecs = embed([r["content"] for r in new]) if new else []
        for r, v in zip(new, vecs):
            con.execute("""INSERT INTO rag_chunks (store_uuid, source_type, source_key, title, heading, url_path, brand_slug,
                           category_slugs, price, content, content_hash, embedding, updated_at)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (store, r["source_type"], r["source_key"], r["title"], r["heading"], r["url_path"], r["brand_slug"],
                         r["category_slugs"], r["price"], r["content"], r["content_hash"], v, r["updated_at"]))
        for k, h in gone:
            con.execute("DELETE FROM rag_chunks WHERE store_uuid=%s AND source_key=%s AND content_hash=%s", (store, k, h))
        print(f"added {len(new)}, removed {len(gone)}, unchanged {len(want) - len(new)}")

if __name__ == "__main__": main()