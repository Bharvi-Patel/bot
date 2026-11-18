"""Read-only report on the RAG index (table rag_chunks). Needs RAG_DB_URL.

Save this file in the repo folder (next to the .env file) and run:   python inspect_index.py
RAG_DB_URL is read from that .env file, the same way the rest of the bot reads it. If it is not in .env yet, add a line
    RAG_DB_URL=postgresql://user:password@host/dbname?sslmode=require
(your Neon connection string). Never paste that string into a chat.

Writes index_report.txt next to where you run it. It only runs SELECT statements (the connection is read-only) and never
prints the database URL. Upload index_report.txt.
"""
import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row

load_dotenv(Path(__file__).resolve().parent / ".env")      # the repo's .env, like bot/__init__.py does
URL = os.environ.get("RAG_DB_URL")
if not URL:
    sys.exit("RAG_DB_URL is not set: add it to the .env file in the repo folder (see the top of this file)")

out = []


def line(text=""):
    out.append(str(text))


def cell(v, width=160):
    s = "" if v is None else str(v).replace("\r", " ").replace("\n", " | ")
    return s if len(s) <= width else s[:width] + "..."


def show(con, title, sql, width=160, limit=None):
    line()
    line("=== " + title)
    try:
        rows = con.execute(sql).fetchall()
    except Exception as exc:  # keep going: one failing query must not hide the rest
        con.rollback()
        line("  QUERY FAILED: " + cell(exc, 300))
        return
    if not rows:
        line("  (no rows)")
        return
    line("  " + " | ".join(rows[0].keys()))
    for r in (rows if limit is None else rows[:limit]):
        line("  " + " | ".join(cell(v, width) for v in r.values()))
    if limit is not None and len(rows) > limit:
        line(f"  ... {len(rows) - limit} more rows not shown")


with psycopg.connect(URL, row_factory=dict_row) as con:
    con.read_only = True

    show(con, "1. rows per store and type (no_embedding should be 0)",
         "SELECT store_uuid, source_type, count(*) AS rows, count(*) FILTER (WHERE embedding IS NULL) AS no_embedding, "
         "round(avg(length(content))) AS avg_chars FROM rag_chunks GROUP BY 1, 2 ORDER BY 1, 2")

    show(con, "2. embedding size",
         "SELECT vector_dims(embedding) AS dims FROM rag_chunks WHERE embedding IS NOT NULL LIMIT 1")

    show(con, "3. product rows: quality checks, per store",
         "SELECT store_uuid, count(*) AS products, "
         "count(*) FILTER (WHERE price IS NULL OR price <= 0) AS no_price, min(price) AS min_price, max(price) AS max_price, "
         "count(*) FILTER (WHERE content !~ 'SKU:') AS no_sku_line, "
         "count(*) FILTER (WHERE url_path IS NULL OR url_path NOT LIKE '/product/%') AS odd_url_path, "
         "count(*) FILTER (WHERE brand_slug IS NULL) AS no_brand, "
         "count(*) FILTER (WHERE category_slugs IS NULL OR cardinality(category_slugs) = 0) AS no_categories, "
         "max(updated_at) AS newest "
         "FROM rag_chunks WHERE source_type = 'product' GROUP BY 1")

    show(con, "4. category slugs used by product rows (slug | how many products)",
         "SELECT store_uuid, s AS category_slug, count(*) AS products FROM rag_chunks, unnest(category_slugs) AS s "
         "WHERE source_type = 'product' GROUP BY 1, 2 ORDER BY 1, 3 DESC, 2", limit=600)

    show(con, "5. brand slugs used by product rows",
         "SELECT store_uuid, brand_slug, count(*) AS products FROM rag_chunks WHERE source_type = 'product' "
         "GROUP BY 1, 2 ORDER BY 1, 3 DESC")

    show(con, "6. how many products have 'mattress' in the title (the API's mattresses have no single price)",
         "SELECT store_uuid, count(*) AS mattress_titles FROM rag_chunks "
         "WHERE source_type = 'product' AND title ILIKE '%mattress%' GROUP BY 1")

    show(con, "7. sample of 6 products (title | price | page | start of text)",
         "SELECT title, price, url_path, left(content, 140) AS start_of_text FROM rag_chunks "
         "WHERE source_type = 'product' ORDER BY id LIMIT 6", width=200)

    show(con, "8. one full product document", 
         "SELECT title, url_path, brand_slug, category_slugs, price, content FROM rag_chunks "
         "WHERE source_type = 'product' ORDER BY id LIMIT 1", width=1800)

    show(con, "9. policy, guide, FAQ and brand pages (chunks per source)",
         "SELECT store_uuid, source_type, source_key, count(*) AS chunks, max(updated_at) AS newest FROM rag_chunks "
         "WHERE source_type <> 'product' GROUP BY 1, 2, 3 ORDER BY 1, 2, 3")

    show(con, "10. FAQ rows (question | start of answer)",
         "SELECT store_uuid, title, left(content, 200) AS start_of_answer FROM rag_chunks WHERE source_type = 'faq' ORDER BY 1, 2",
         width=260)

    show(con, "11. leftovers that should not be in page text",
         "SELECT source_type, source_key, "
         "count(*) FILTER (WHERE content ~* '<(style|script|div)') AS has_html, "
         "count(*) FILTER (WHERE content ~* 'swap demo data|B3 MASTER') AS template_note "
         "FROM rag_chunks WHERE source_type <> 'product' GROUP BY 1, 2 HAVING "
         "count(*) FILTER (WHERE content ~* '<(style|script|div)') > 0 OR count(*) FILTER (WHERE content ~* 'swap demo data|B3 MASTER') > 0")

with open("index_report.txt", "w", encoding="utf-8") as fh:
    fh.write("\n".join(out) + "\n")
print("Wrote index_report.txt (" + str(len(out)) + " lines). Upload that file.")