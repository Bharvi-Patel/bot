"""Create the rag_chunks table from schema.sql.  Usage (from the rag folder):  python apply_schema.py
Needs RAG_DB_URL set first, e.g.  set RAG_DB_URL=postgresql://user:pass@host/dbname?sslmode=require"""
import os, sys, psycopg
from dotenv import load_dotenv; load_dotenv("../.env")

url = os.environ.get("RAG_DB_URL")
if not url:
    sys.exit("RAG_DB_URL is not set. In cmd:  set RAG_DB_URL=postgresql://user:pass@host/dbname?sslmode=require")
with psycopg.connect(url, autocommit=True) as con:
    con.execute(open("schema.sql", encoding="utf8").read())
print("rag_chunks table created")
