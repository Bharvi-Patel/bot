"""Prints the cheapest products whose name matches a word, with the categories each one is filed under.
Run from the project folder (needs .env with the database settings; no model quota):
    python check_cheapest.py "Dining%Table"          (default)
    python check_cheapest.py "Desk"
"""
import sys
from dotenv import load_dotenv
load_dotenv()

from sjbot.db import run_query

pattern = sys.argv[1] if len(sys.argv) > 1 else "Dining%Table"
rows = run_query("SELECT product_uuid, parent_uuid, name, price FROM vw_chat_products "
                 "WHERE name LIKE %s AND name NOT LIKE '%%Chair%%' ORDER BY price LIMIT 8", [f"%{pattern}%"])
for r in rows:
    uid = r["parent_uuid"] or r["product_uuid"]
    cats = run_query("SELECT c.title FROM vw_chat_product_categories pc JOIN vw_chat_categories c ON c.category_uuid = pc.category_uuid "
                     "WHERE pc.product_uuid = %s", [uid])
    print(f"${float(r['price']):>8.2f}  {r['name'][:55]:55}  categories: {', '.join(c['title'] for c in cats) or '(none)'}")