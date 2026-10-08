"""Prints the question, tools used and reply for every non-passing run in an eval file.
    python show_failures.py eval_runs\\eval_20261008_161241.jsonl
Optional second argument limits it to some cases:  python show_failures.py FILE clean-sofa discount financing"""
import json, sys

path, only = sys.argv[1], set(sys.argv[2:])
for line in open(path, encoding="utf-8"):
    d = json.loads(line)
    if d["status"] == "pass" or (only and d["case"] not in only):
        continue
    print(f"=== {d['case']}  run {d['run']}  {d['status']}  tools={d.get('tools')}  blocked={d.get('blocked')}")
    print("reasons:", d.get("reasons"))
    for q, r in zip(d["questions"], d["replies"]):
        print("Q:", q)
        print("A:", r, "\n")