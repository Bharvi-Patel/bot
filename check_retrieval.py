"""Shows what search_policies returns for the questions the bot failed on. Run from the project folder:
    python check_retrieval.py
Needs .env with RAG_DB_URL (same as the bot). Uses no LLM, so it costs no API quota."""
from dotenv import load_dotenv
load_dotenv()

from sjbot.tools.search_policies import CUTOFF, search_policies

QUESTIONS = [
    "what's the difference between memory foam and hybrid mattresses?",
    "are there any coupon codes?",
    "do you have a price match guarantee?",
]

print(f"cosine cutoff = {CUTOFF}\n")
for q in QUESTIONS:
    r = search_policies({"question": q})
    print("Q:", q)
    if "error" in r:
        print("   ERROR:", r["error"], "\n")
        continue
    print("   found =", r["found"], "| best_similarity =", r.get("best_similarity"))
    for c in r.get("chunks", []):
        print(f"   {c['similarity']:.3f}  {c['source_key']}  >  {c['heading'][:60]}")
    print()