"""Sweeps the WHOLE catalog through the rounded-price repair. Needs .env with the database settings; uses NO model quota.

For every product it simulates the failure we fixed: the model lists search results with each price rounded to whole dollars
($185 for 185.14) and guardrails.repair_rounded_prices has to put the exact price back on the right product's line.
Results are served in pages of 8, as search_products does, in two orders: by name (a page of neighbours) and by price
(a page of near-identical prices, the hardest case for matching a price to its product).

    python check_price_sweep.py                  all products, both orders, both rounding styles
    python check_price_sweep.py --limit 2000     first 2000 products only (quick run)
    python check_price_sweep.py --show 25        print up to 25 examples of each problem (default 10)

Reads the numbers like this:
    repaired right   the reply came back with every exact price on the right product: the customer sees the true price
    left rounded     the reply passed with a price off by under $1, because the rounded number equals another real price
                     in the results and no single product on the line could be pinned to it
    blocked          repair gave up (ambiguous or no match), so the bot would send the fallback message; safe, but a lost answer
    WRONG            repair put a price on the wrong product. This must be 0: if not, send me the examples.
"""
import argparse
import math
from collections import Counter

from sjbot import guardrails as g

PAGE = 8


def load_rows() -> list[dict]:
    from dotenv import load_dotenv
    load_dotenv()
    from sjbot.db import run_query
    rows = run_query("SELECT name, price FROM vw_chat_products WHERE price IS NOT NULL AND price > 0 ORDER BY name, price", [])
    return [{"name": str(r["name"]), "price": round(float(r["price"]), 2)} for r in rows if r.get("name")]


def money(v: float) -> str:
    return f"${v:,.0f}" if v == int(v) else f"${v:,.2f}"


def whole(v: float, style: str) -> float:
    return float(math.floor(v)) if style == "floor" else float(math.floor(v + 0.5))


def sweep(rows: list[dict], order: str, style: str, show: int) -> dict:
    rows = sorted(rows, key=(lambda r: (r["price"], r["name"])) if order == "price" else (lambda r: (r["name"], r["price"])))
    stats: Counter = Counter()
    examples = {"blocked": [], "left rounded": [], "WRONG": []}
    for i in range(0, len(rows), PAGE):
        page = rows[i:i + PAGE]
        cents = [r for r in page if r["price"] != int(r["price"])]
        if not cents:
            continue                                            # every price on the page is already whole: nothing to round
        result = {"count": len(page), "products": page}
        lines_in, lines_want = [], []
        for r in page:
            shown = whole(r["price"], style) if r in cents else r["price"]
            lines_in.append(f"- {r['name']} \u2013 {money(shown)}")
            lines_want.append(f"- {r['name']} \u2013 {money(r['price'])}")
        reply, want = "\n".join(lines_in), "\n".join(lines_want)
        stats["pages"] += 1
        stats["rounded prices"] += len(cents)
        fixed = g.repair_rounded_prices(reply, [result])
        if fixed is None:
            stats["blocked"] += 1
            if len(examples["blocked"]) < show:
                examples["blocked"].append(reply)
            continue
        got = fixed.splitlines()
        marks = ["right" if a == w else "left" if a == o else "WRONG" for a, w, o in zip(got, lines_want, lines_in)]
        if "WRONG" in marks:
            stats["WRONG"] += 1
            if len(examples["WRONG"]) < show:
                examples["WRONG"].append(f"{reply}\n   -> became:\n{fixed}")
        elif "left" in marks:
            stats["left rounded"] += 1                          # a rounded price survived: it equals another product's exact price
            if len(examples["left rounded"]) < show:
                examples["left rounded"].append(f"{reply}\n   -> became:\n{fixed}")
        else:
            stats["repaired right"] += 1
    return {"stats": stats, "examples": examples}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--show", type=int, default=10)
    args = ap.parse_args()
    rows = load_rows()
    if args.limit:
        rows = rows[:args.limit]
    with_cents = sum(1 for r in rows if r["price"] != int(r["price"]))
    print(f"{len(rows)} products, {with_cents} with cents in the price ({len(rows) - with_cents} whole-dollar)\n")
    worst_wrong = 0
    for order in ("name", "price"):
        for style in ("round", "floor"):
            out = sweep(rows, order, style, args.show)
            s = out["stats"]
            pages = s["pages"] or 1
            print(f"pages by {order}, model {'rounds' if style == 'round' else 'truncates'}: {s['pages']} pages | "
                  f"repaired right {s['repaired right']} ({100 * s['repaired right'] / pages:.1f}%) | "
                  f"left rounded {s['left rounded']} ({100 * s['left rounded'] / pages:.1f}%) | "
                  f"blocked {s['blocked']} ({100 * s['blocked'] / pages:.1f}%) | WRONG {s['WRONG']}")
            worst_wrong = max(worst_wrong, s["WRONG"])
            for kind, items in out["examples"].items():
                for ex in items:
                    print(f"\n  [{kind}]\n" + "\n".join("    " + ln for ln in ex.splitlines()))
            print()
    print("RESULT:", "OK, no wrong-product repairs" if worst_wrong == 0 else "PROBLEM: wrong-product repairs found, see [WRONG] above")


if __name__ == "__main__":
    main()