"""Print the rows behind one case's expected answer, so you can see whether the answer key really matches the question.

    python peek_truth.py eval_heldout held-outdoor            # first 60 distinct names with price range
    python peek_truth.py eval_heldout held-outdoor 150        # first 150
"""
import importlib
import sys

from eval_accuracy import fetch_truth
from sjbot.db import run_query


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    cases = importlib.import_module(sys.argv[1]).CASES
    limit = int(sys.argv[3]) if len(sys.argv) > 3 else 60
    case = next((c for c in cases if c["id"] == sys.argv[2]), None)
    if not case or not case.get("truth"):
        print(f"no case with a truth query called {sys.argv[2]!r}")
        return 2
    rows, err = fetch_truth(case, {}, run_query)
    if err:
        print("query failed:", err)
        return 1
    col, pcol = case["truth"]["col"], case["truth"].get("price_col")
    seen: dict[str, list[float]] = {}
    for r in rows:
        seen.setdefault(str(r[col]), []).append(float(r[pcol]) if pcol and r.get(pcol) is not None else 0.0)
    print(f"{len(rows)} rows, {len(seen)} distinct names. First {min(limit, len(seen))}:")
    for name, prices in list(seen.items())[:limit]:
        print(f"  {name[:80]:80}  ${min(prices):.2f}" + (f"-${max(prices):.2f}" if max(prices) != min(prices) else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())