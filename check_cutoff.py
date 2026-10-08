"""Finds a safe cosine cutoff for search_policies. Run from the project folder:  python check_cutoff.py
Needs .env with RAG_DB_URL. Uses no LLM quota. Prints the best similarity for each question with the cutoff switched off."""
from dotenv import load_dotenv
load_dotenv()

import sjbot.tools.search_policies as sp

ANSWERABLE = [
    "What is your return policy?", "Can I return a mattress?", "How long does delivery take?",
    "Do you offer financing?", "What does the warranty cover?",
    "What is the difference between memory foam and hybrid mattresses?", "Best mattress for back pain?",
    "Can I cancel my order?", "Do you ship to Canada?", "What payment methods do you accept?",
]
SHOULD_DECLINE = [
    "are there any coupon codes?", "do you have a price match guarantee?", "how do I clean a fabric sofa?",
    "do you sell gift cards?", "do you offer military discounts?",
]
sp.CUTOFF = 0.0                                   # keep everything so we can see the raw scores
def best(q):
    r = sp.search_policies({"question": q})
    return max((c["similarity"] for c in r.get("chunks", [])), default=0.0), r.get("error")

print("ANSWERABLE (want these ABOVE the cutoff)")
a = []
for q in ANSWERABLE:
    b, err = best(q); a.append(b); print(f"  {b:.3f}  {q}" + (f"   ERROR {err}" if err else ""))
print("\nSHOULD DECLINE (want these BELOW the cutoff)")
d = []
for q in SHOULD_DECLINE:
    b, err = best(q); d.append(b); print(f"  {b:.3f}  {q}" + (f"   ERROR {err}" if err else ""))
print(f"\nlowest answerable = {min(a):.3f}   highest should-decline = {max(d):.3f}   (current cutoff 0.55)")
if min(a) > max(d):
    print(f"A cutoff between {max(d):.3f} and {min(a):.3f} separates them, e.g. {(min(a) + max(d)) / 2:.2f}")
else:
    print("No single cutoff separates them; the prompt change is the safer fix.")