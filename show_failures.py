"""Print every failing case from an eval .jsonl: the question, tools used, why it failed, the reply the safety check threw away
(if any) and the replies. The first failing run of each case is shown in full, later ones shortened.

    python show_failures.py eval_runs\\eval_20261015_162151.jsonl
"""
import json
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    rows = [json.loads(line) for line in open(sys.argv[1], encoding="utf-8") if line.strip()]
    bad: dict[str, list[dict]] = {}
    for r in rows:
        if r["status"] != "pass":
            bad.setdefault(r["case"], []).append(r)
    if not bad:
        print("no failing runs")
        return 0
    for case, runs in bad.items():
        total = sum(1 for r in rows if r["case"] == case)
        print(f"=== {case}: failed {len(runs)} of {total} runs")
        print("   asked:", " | ".join(runs[0]["questions"]))
        for i, r in enumerate(runs):
            full = i == 0
            cut = (lambda t: t) if full else (lambda t: t[:350] + (" ..." if len(t) > 350 else ""))
            print(f"  -- run {r['run']}  tools: {', '.join(r['tools']) or 'none'}")
            for c in r.get("tool_calls") or []:
                print(f"     call: {c['tool']}({c['args'][:300]})")
            for reason in r["reasons"]:
                print("     why:", reason)
            for t in r.get("blocked_replies") or []:
                print("     BLOCKED REPLY:", cut(t).replace("\n", "\n        "))
            if full or not r.get("blocked_replies"):
                print("     reply:", cut(r["replies"][-1]).replace("\n", "\n        "))
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())