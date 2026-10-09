"""Shows what the category tool returns. Run from the project folder (needs .env with the database settings, uses no model quota):
    python check_rug_categories.py          categories matching the keyword 'rug'
    python check_rug_categories.py bed      categories matching another keyword
    python check_rug_categories.py -        the call with NO arguments (what the bot makes for "what categories do you sell?")"""
import json
import sys
from dotenv import load_dotenv
load_dotenv()

from sjbot.tools.list_categories import list_categories

word = sys.argv[1] if len(sys.argv) > 1 else "rug"
args = {} if word == "-" else {"keyword": word}
print(json.dumps(list_categories(args), indent=1)[:6000])