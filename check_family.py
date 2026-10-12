"""Shows every variant in one product family (name, price, own attributes) so we can see how size is stored.
Run from the project folder (needs .env; no model quota):
    python check_family.py "Chime 8 Inch Memory Foam Twin"
"""
import sys
from dotenv import load_dotenv
load_dotenv()

from sjbot.db import run_query

pattern = sys.argv[1] if len(sys.argv) > 1 else "Chime 8 Inch Memory Foam Twin"
hit = run_query("SELECT product_uuid, parent_uuid, name FROM vw_chat_products WHERE name LIKE %s LIMIT 1", [f"%{pattern}%"])
if not hit:
    sys.exit("no product matches that name")
fam = hit[0]["parent_uuid"] or hit[0]["product_uuid"]
rows = run_query("SELECT product_uuid, parent_uuid, sku, name, price FROM vw_chat_products "
                 "WHERE product_uuid = %s OR parent_uuid = %s ORDER BY price", [fam, fam])
print(f"family {fam}: {len(rows)} rows")
for r in rows:
    attrs = run_query("SELECT group_name, value FROM vw_chat_product_attributes WHERE product_uuid = %s", [r["product_uuid"]])
    own = run_query("SELECT c.title FROM vw_chat_product_categories pc JOIN vw_chat_categories c ON c.category_uuid = pc.category_uuid "
                    "WHERE pc.product_uuid = %s", [r["product_uuid"]])
    kind = "PARENT " if not r["parent_uuid"] else "variant"
    print(f"{kind} ${float(r['price']):>7.2f}  {r['sku']:10} {r['name'][:50]:50}  own categories: {', '.join(c['title'] for c in own) or '-'}")
    print("          attributes:", "; ".join(f"{a['group_name']}={a['value']}" for a in attrs) or "-")