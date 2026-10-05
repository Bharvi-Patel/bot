"""Run the three small tools against your dev database and print what the bot would receive.
Set the SJ_DB_* variables first (see .env.example), then:  python smoke_all.py
"""
import json
import time

from sjbot.tools.get_shipping_options import get_shipping_options
from sjbot.tools.get_store_info import get_store_info
from sjbot.tools.list_categories import list_categories

CASES = [
    ("list_categories (top level)", list_categories, {}),
    ("list_categories (keyword: sectional)", list_categories, {"keyword": "sectional"}),
    ("get_store_info", get_store_info, {}),
    ("get_shipping_options", get_shipping_options, {}),
]

for title, fn, args in CASES:
    t0 = time.time()
    result = fn(args)
    print("\n==", title, "(%.2fs)" % (time.time() - t0))
    print(json.dumps(result, indent=2, ensure_ascii=False)[:2500])
