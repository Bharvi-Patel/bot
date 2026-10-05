"""Phase 4: load products.jsonl into rag_chunks (Postgres). Same rules as 5.7:
  - embeds only products whose content_hash changed (a changed product's old row is replaced)
  - unchanged products: only the metadata (price, brand, categories, title, url) is refreshed, no re-embedding
  - removes product rows that are gone, but ONLY inside the categories you built (products_scope.json), never other categories
Needs RAG_DB_URL. The first run loads the embedding model.  Usage:  python sync_products.py"""
import json, os, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import psycopg
from sjbot.embeddings import embed_documents


def main():
    rows = [json.loads(l) for l in open("products.jsonl", encoding="utf8")]
    roots = json.load(open("products_scope.json"))["roots"]
    want = {r["source_key"]: r for r in rows}
    with psycopg.connect(os.environ["RAG_DB_URL"]) as con:
        have = {k: h for k, h in con.execute("SELECT source_key, content_hash FROM rag_chunks WHERE source_type = 'product'")}
        new = [r for k, r in want.items() if have.get(k) != r["content_hash"]]
        same = [r for k, r in want.items() if have.get(k) == r["content_hash"]]
        for i in range(0, len(new), 32):
            part = new[i:i + 32]
            for r, v in zip(part, embed_documents([r["content"] for r in part])):
                con.execute("DELETE FROM rag_chunks WHERE source_type='product' AND source_key=%s", (r["source_key"],))
                con.execute("""INSERT INTO rag_chunks (source_type, source_key, title, heading, url_path, brand_slug,
                               category_slugs, price, content, content_hash, embedding)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                            (r["source_type"], r["source_key"], r["title"], r["heading"], r["url_path"], r["brand_slug"],
                             r["category_slugs"], r["price"], r["content"], r["content_hash"], v))
            con.commit(); print(f"  embedded {min(i + 32, len(new))}/{len(new)}", flush=True)
        for r in same:
            con.execute("""UPDATE rag_chunks SET price=%s, brand_slug=%s, category_slugs=%s, title=%s, url_path=%s
                           WHERE source_type='product' AND source_key=%s""",
                        (r["price"], r["brand_slug"], r["category_slugs"], r["title"], r["url_path"], r["source_key"]))
        gone = [k for k in have if k not in want]
        if gone:
            gone = [k for (k,) in con.execute("SELECT source_key FROM rag_chunks WHERE source_type='product' AND source_key = ANY(%s) AND category_slugs && %s", (gone, roots))]
        for k in gone:
            con.execute("DELETE FROM rag_chunks WHERE source_type='product' AND source_key=%s", (k,))
        print(f"added/changed {len(new)}, metadata refreshed {len(same)}, removed {len(gone)}")


if __name__ == "__main__":
    main()
