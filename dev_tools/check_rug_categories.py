"""Shows what the category tool returns. Run from the project folder (needs .env with the database settings, uses no model quota):
    python -m dev_tools.check_rug_categories          categories matching the keyword 'rug'
    python -m dev_tools.check_rug_categories bed      categories matching another keyword
    python -m dev_tools.check_rug_categories -        the call with NO arguments (what the bot makes for "what categories do you sell?")"""
import json
import sys
from dotenv import load_dotenv
load_dotenv()

from bot.tools.list_categories import list_categories

word = sys.argv[1] if len(sys.argv) > 1 else "rug"
args = {} if word == "-" else {"keyword": word}
print(json.dumps(list_categories(args), indent=1)[:6000])