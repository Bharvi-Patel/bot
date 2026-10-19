"""Shows, for the cheapest products whose name matches a pattern, the Bed Size each one records and what its PARENT is filed under.
Run from the project folder (needs .env with the database settings; no model quota):
    python check_size_data.py "Queen%Mattress"          (default)
"""
import sys
from dotenv import load_dotenv
load_dotenv()

from sjbot.db import run_query

pattern = sys.argv[1] if len(sys.argv) > 1 else "Queen%Mattress"
rows = run_query("SELECT product_uuid, parent_uuid, name, price FROM vw_chat_products "
                 "WHERE name LIKE %s ORDER BY price LIMIT 10", [f"%{pattern}%"])
for r in rows:
    uid = r["parent_uuid"] or r["product_uuid"]
    size = run_query("SELECT value FROM vw_chat_product_attributes WHERE product_uuid = %s AND group_name = 'Bed Size'", [r["product_uuid"]])
    cats = run_query("SELECT c.title FROM vw_chat_product_categories pc JOIN vw_chat_categories c ON c.category_uuid = pc.category_uuid "
                     "WHERE pc.product_uuid = %s", [uid])
    print(f"${float(r['price']):>8.2f}  {r['name'][:42]:42}  has_parent={bool(r['parent_uuid'])!s:5}  "
          f"bed_size={', '.join(s['value'] for s in size) or '(none)':10}  parent cats: {', '.join(c['title'] for c in cats) or '(none)'}")