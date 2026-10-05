"""Look up one product as the bot would.  Usage:  python smoke_details.py SKU
With no SKU it searches for a sectional and uses the first result.
Set the SJ_DB_* variables first (see .env.example).
"""
import json
import sys
import time

from sjbot.tools.get_product_details import get_product_details
from sjbot.tools.search_products import search_products

sku = sys.argv[1] if len(sys.argv) > 1 else None
if not sku:
    found = search_products({"keyword": "sectional"})
    sku = found["products"][0]["sku"]
    print("using first search result:", sku)

t0 = time.time()
result = get_product_details({"sku": sku})
print("time: %.2fs" % (time.time() - t0))
print(json.dumps(result, indent=2, ensure_ascii=False))
