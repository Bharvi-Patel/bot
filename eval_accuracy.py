"""Measure how accurate the bot is, per kind of question, with the model's run-to-run variation included.

  python eval_accuracy.py --check-truth                     # no model: run every ground-truth query, find broken cases
  python eval_accuracy.py --runs 1 --limit 10               # quick smoke run
  python eval_accuracy.py --runs 3 --order 102500001 --email order2@example.test
  python eval_accuracy.py --only product_search,beds-500    # case ids or types
  python eval_accuracy.py --resume eval_runs/eval_20261007_101500.jsonl     # continue after a model rate limit

What it does: asks every question in eval_cases.py in a fresh conversation, N times. The expected answer comes from the
database (ground-truth SQL) or from words the reply must / must not contain. A case counts as "stable pass" only if it
passes EVERY run; "flaky" if it passes some; "fail" if it passes none. Provider errors and broken cases are reported
separately and are not counted against the bot. The score is read from the router's reply and tool log, so it also shows
false "nothing found" answers, invented facts, leaks and fallback replies.

Cost: each question needs several model calls (measured: about 10,000 tokens). 58 cases x 3 runs is far over a free daily
limit, so start with --runs 1 --limit 15. Saved replies have emails, phones and long numbers masked; the file still
holds customer-style text, so do not commit eval_runs/.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import math
import re
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

NONE_FOUND = re.compile(
    r"couldn.?t find|could not find|did not find|didn.?t find|not able to find|unable to find|don.?t see any|"
    r"\bno (?:\w+ ){0,2}(?:products|results|sofas|beds|desks|rugs|items|matches|options)|none (?:found|available)|"
    r"\bno .{0,40}(?:found|available|match)", re.I)
REFUSAL = re.compile(r"can.?t|cannot|unable|not able|sorry|don.?t have|do not have|no information|not sure|not available|contact the store|please call", re.I)
MONEY = re.compile(r"\$\s?(\d[\d,]*(?:\.\d{1,2})?)")
FALLBACK_TEXT = "can't answer that reliably"
RUNS_DIR = Path("eval_runs")
_TR = str.maketrans({"\u2011": "-", "\u2010": "-", "\u2013": "-", "\u2014": "-", "\u2019": "'", "\u2018": "'", "\u201c": '"',
                     "\u201d": '"', "\u00ae": "", "\u2122": "", "\u00a0": " ", "*": "", "`": "",
                     "\u00d7": "x", "\u2032": "'", "\u2033": '"'})                    # 2' x 3' is often written 2' \u00d7 3' (or with prime marks)


# ---------------------------------------------------------------- text helpers
def norm(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text).translate(_TR)).strip().lower()


def forms(value: Any) -> set[str]:
    """The ways a database value may appear in a reply."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        x = float(value)
        out = {f"{x:.2f}", f"{x:,.2f}"}
        if x == int(x):
            out |= {str(int(x)), f"{int(x):,}"}
        return out
    return {norm(value)} - {""}


def amounts(text: str) -> list[float]:
    return [float(m.group(1).replace(",", "")) for m in MONEY.finditer(text)]


def price_near(text: str, names: set[str], price: float, others: set[str] = frozenset()) -> bool:
    """True if the price appears right after the product name, before the next different product name is mentioned.
    (A window around the name would accept a reply that swaps the prices of two products listed together.)"""
    for name in names:
        for m in re.finditer(re.escape(name), text):
            segment = text[m.end(): m.end() + 120]
            cuts = [segment.find(o) for o in others if o not in names and segment.find(o) >= 0]
            if cuts:
                segment = segment[:min(cuts)]
            if any(abs(a - price) < 0.005 for a in amounts(segment)):
                return True
    return False


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% interval for a pass rate, so 'about 90%' is not mistaken for '91% vs 93%'."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, centre - half), min(1.0, centre + half))


def fill(text: Any, subs: dict[str, str]) -> Any:
    if isinstance(text, str):
        for k, v in subs.items():
            text = text.replace("{" + k + "}", v)
        return text
    if isinstance(text, list):
        return [fill(t, subs) for t in text]
    return text


# ---------------------------------------------------------------- scoring one run
def result(status: str, reasons: list[str], flags: set[str]) -> dict:
    return {"status": status, "reasons": reasons, "flags": sorted(flags)}


def score_run(case: dict, outs: list[dict], rows: list[dict] | None, tool_errors: list[str] | tuple = ()) -> dict:
    """status: pass | fail | infra | bad_case. flags: false_none, invented, leak, fallback, infra."""
    if any(o.get("blocked") == "llm_error" for o in outs):
        return result("infra", ["the model provider failed (rate limit or outage), so this run says nothing about the bot"], {"infra"})
    reasons: list[str] = []
    flags: set[str] = set()
    reply = outs[-1]["reply"]
    text = norm(reply)
    tools = {t for o in outs for t in o.get("tools_used", [])}
    ok_blocked = set(case.get("blocked_ok", []))
    if tool_errors:
        reasons.append(f"a tool crashed: {', '.join(sorted(set(tool_errors)))} (so the reply did not come from your data)")
        flags.add("tool_failed")

    blocked_reasons = [o["blocked"] for o in outs if o.get("blocked") and o["blocked"] not in ok_blocked]
    for b in blocked_reasons:
        reasons.append(f"reply was blocked by the safety check: {b}")
        flags.add("fallback")
        if b.startswith(("unknown_", "unverified_")):
            flags.add("invented")
        if b == "internal_leak":
            flags.add("leak")
    if not blocked_reasons and FALLBACK_TEXT in text:
        reasons.append("gave the generic fallback reply")
        flags.add("fallback")

    truth = case.get("truth")
    if truth:
        rows = rows or []
        if truth.get("expect_empty"):
            if rows:
                return result("bad_case", [f"the case expects no database results but the database has {len(rows)}; fix the case or the data"], set())
            if "$" in text and not NONE_FOUND.search(text):
                reasons.append("listed priced products although the database has none")
        else:
            if not rows:
                return result("bad_case", ["the ground-truth query returned no rows; fix the case or the data"], set())
            col, pcol = truth["col"], truth.get("price_col")
            mentioned = [r for r in rows if any(f in text for f in forms(r[col]))]
            need = max(1, math.ceil(truth["min_share"] * len(rows))) if "min_share" in truth else int(truth.get("min_hits", 1))
            need = min(need, len(rows))
            if len(mentioned) < need:
                reasons.append(f"reply mentions {len(mentioned)} of the {len(rows)} database results (needs {need})")
                if NONE_FOUND.search(text):
                    flags.add("false_none")
                    reasons.append("says nothing was found although the database has matching products")
            if case.get("prices") and pcol:
                every_name = {f for r in rows for f in forms(r[col])}
                for r in mentioned:
                    if not price_near(text, forms(r[col]), float(r[pcol]), every_name):
                        reasons.append(f"price for {r[col]} is missing or wrong (database: ${float(r[pcol]):.2f})")

    if case.get("mention_any") and not any(norm(w) in text for w in case["mention_any"]):
        reasons.append(f"mentions none of: {case['mention_any']}")
    for item in case.get("mention_all", []):
        options = item if isinstance(item, list) else [item]
        if not any(norm(w) in text for w in options):
            reasons.append(f"does not mention: {' / '.join(options)}")
    for w in case.get("not_say", []):
        hit = re.search(w[3:], text, re.I) if w.startswith("re:") else (norm(w) in text)
        if hit:
            reasons.append(f"says something it must not: {w}")
    if case.get("tools_any") and not (tools & set(case["tools_any"])):
        reasons.append(f"none of the expected tools ran: {case['tools_any']} (ran: {sorted(tools) or 'none'})")
    if case.get("tools_none") and (tools & set(case["tools_none"])):
        reasons.append(f"used a tool it should not have: {sorted(tools & set(case['tools_none']))}")
    if case.get("max_price") is not None and any(a > case["max_price"] + 0.005 for a in amounts(text)):
        reasons.append(f"quotes a price above ${case['max_price']:g}")
    if case.get("refuse") and not REFUSAL.search(text):
        reasons.append("should have declined or said it does not have this information")
    if case.get("max_chars") and len(reply) > case["max_chars"]:
        reasons.append(f"reply is {len(reply)} characters (limit {case['max_chars']})")
    return result("fail" if reasons else "pass", reasons, flags)


# ---------------------------------------------------------------- running
@contextlib.contextmanager
def watch_tool_errors():
    """Collect the names of tools that crashed (the dispatcher turns a crash into {"error": "tool_failed"}) while the block runs."""
    from sjbot import router
    failed: list[str] = []
    real = router.run_tool

    def spy(name, args, ctx=None):
        result = real(name, args, ctx)
        if isinstance(result, dict) and result.get("error") in ("tool_failed", "unknown_tool"):
            failed.append(name)
        return result

    router.run_tool = spy
    try:
        yield failed
    finally:
        router.run_tool = real


def run_case(case: dict, run_idx: int, answer_fn: Callable, llm: Any, subs: dict[str, str], rows: list[dict] | None,
             sleep: float = 0.0) -> dict:
    from sjbot.context import ChatContext
    from sjbot.tracing import mask
    ctx = ChatContext(client_id=f"eval-{case['id']}-{run_idx}")      # a fresh visitor, so the order rate limit never interferes
    questions = [fill(q, subs) for q in case["turns"]]
    history: list[dict] = []
    outs: list[dict] = []
    t0, tok0 = time.monotonic(), getattr(llm, "tokens", 0) or 0
    failed: list[str] = []
    try:
        with watch_tool_errors() as failed:
            for q in questions:
                out = answer_fn(q, llm, history, ctx=ctx)
                outs.append(out)
                history += [{"role": "user", "text": q}, {"role": "assistant", "text": out["reply"]}]
                if sleep:
                    time.sleep(sleep)
        scored = score_run(case, outs, rows, failed)
    except Exception as exc:                                         # a crash is the bot's fault, unless it was the provider
        provider = any(w in type(exc).__name__.lower() for w in ("ratelimit", "timeout", "connection", "apistatus"))
        scored = result("infra" if provider else "fail", [f"raised {type(exc).__name__}: {exc}"], {"infra"} if provider else {"fallback"})
    return {
        "case": case["id"], "type": case["type"], "run": run_idx, **scored,
        "questions": [mask(q) for q in questions], "replies": [mask(o["reply"]) for o in outs],
        "tools": sorted({t for o in outs for t in o.get("tools_used", [])}),
        "blocked": [o.get("blocked") for o in outs], "turns": len(questions),
        "seconds": round(time.monotonic() - t0, 2), "tokens": max(0, (getattr(llm, "tokens", 0) or 0) - tok0),
        "known_issue": case.get("known_issue"), "model": getattr(llm, "model", None), "tool_errors": sorted(set(failed)),
    }


def fetch_truth(case: dict, subs: dict[str, str], run_query: Callable) -> tuple[list[dict] | None, str | None]:
    truth = case.get("truth")
    if not truth:
        return None, None
    try:
        return run_query(truth["sql"], fill(truth.get("params", []), subs)), None
    except Exception as exc:
        return None, f"ground-truth query failed: {type(exc).__name__}: {exc}"


# ---------------------------------------------------------------- summary and report
def load_records(path: Path) -> dict[tuple[str, int], dict]:
    recs: dict[tuple[str, int], dict] = {}
    if path.exists():
        for line in path.read_text(encoding="utf8").splitlines():
            if line.strip():
                r = json.loads(line)
                recs[(r["case"], r["run"])] = r                      # the last record for a (case, run) wins
    return recs


def case_status(recs: list[dict]) -> str:
    counted = [r["status"] for r in recs if r["status"] in ("pass", "fail")]
    if any(r["status"] == "bad_case" for r in recs):
        return "bad_case"
    if not counted:
        return "infra"
    if all(s == "pass" for s in counted):
        return "stable_pass"
    return "fail" if all(s == "fail" for s in counted) else "flaky"


def summarize(records: list[dict]) -> dict:
    by_case: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        by_case[r["case"]].append(r)
    statuses = {cid: case_status(rs) for cid, rs in by_case.items()}
    types = {cid: rs[0]["type"] for cid, rs in by_case.items()}
    per_type: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for cid, st in statuses.items():
        per_type[types[cid]][st] += 1
    counted = [r for r in records if r["status"] in ("pass", "fail")]
    n_runs = len(counted)
    flag = lambda f: sum(1 for r in counted if f in r["flags"])
    scored_cases = [s for s in statuses.values() if s in ("stable_pass", "flaky", "fail")]
    return {
        "statuses": statuses, "types": types, "per_type": {t: dict(v) for t, v in per_type.items()},
        "cases_scored": len(scored_cases), "stable_pass": sum(1 for s in scored_cases if s == "stable_pass"),
        "flaky": sum(1 for s in scored_cases if s == "flaky"), "fail": sum(1 for s in scored_cases if s == "fail"),
        "bad_cases": [c for c, s in statuses.items() if s == "bad_case"], "infra_cases": [c for c, s in statuses.items() if s == "infra"],
        "runs_scored": n_runs, "runs_passed": sum(1 for r in counted if r["status"] == "pass"),
        "false_none_runs": flag("false_none"), "invented_runs": flag("invented"), "leak_runs": flag("leak"), "tool_failed_runs": flag("tool_failed"), "fallback_runs": flag("fallback"),
        "infra_runs": sum(1 for r in records if r["status"] == "infra"),
        "avg_seconds_per_question": round(sum(r["seconds"] for r in counted) / max(1, sum(r["turns"] for r in counted)), 1),
        "avg_tokens_per_question": round(sum(r["tokens"] for r in counted) / max(1, sum(r["turns"] for r in counted))),
    }


def pct(k: int, n: int) -> str:
    return f"{100 * k / n:.0f}%" if n else "n/a"


def build_report(records: list[dict], model: str, skipped: list[str], when: str) -> str:
    s = summarize(records)
    lo, hi = wilson(s["stable_pass"], s["cases_scored"])
    used: dict[str, int] = defaultdict(int)
    for r in records:
        if r.get("model"):
            used[r["model"]] += 1
    shown = ", ".join(f"`{m}` ({n} runs)" for m, n in sorted(used.items())) or f"`{model}`"
    out = [f"# Bot accuracy report\n", f"Model: {shown}   Date: {when}   Runs scored: {s['runs_scored']}   Cases scored: {s['cases_scored']}\n"]
    if len(used) > 1:
        out.append("**More than one model answered in this run** (the daily limit forced a switch), so read the scores as a mix, not as one model.\n")
    out.append("## Headline\n")
    out.append(f"- **Stable pass** (passes every run): **{s['stable_pass']} of {s['cases_scored']} = {pct(s['stable_pass'], s['cases_scored'])}** "
               f"(95% range {100 * lo:.0f}% to {100 * hi:.0f}%)")
    out.append(f"- Flaky (passes some runs): {s['flaky']}   Fail (passes none): {s['fail']}")
    out.append(f"- Single-run pass rate: {pct(s['runs_passed'], s['runs_scored'])}")
    out.append(f"- **False \"nothing found\" answers: {s['false_none_runs']}**   Invented facts blocked: {s['invented_runs']}   Leaks: {s['leak_runs']}   **Tool crashes: {s['tool_failed_runs']}**   Fallback replies: {s['fallback_runs']}")
    out.append(f"- Average per question: {s['avg_seconds_per_question']} s, {s['avg_tokens_per_question']} tokens")
    out.append(f"- Not counted: {s['infra_runs']} provider-error runs, {len(s['bad_cases'])} broken cases, {len(skipped)} skipped cases\n")
    out.append("## By type\n\n| Type | Cases | Stable pass | Flaky | Fail |\n|---|---|---|---|---|")
    for t, d in sorted(s["per_type"].items()):
        n = d.get("stable_pass", 0) + d.get("flaky", 0) + d.get("fail", 0)
        out.append(f"| {t} | {n} | {d.get('stable_pass', 0)} ({pct(d.get('stable_pass', 0), n)}) | {d.get('flaky', 0)} | {d.get('fail', 0)} |")
    by_case: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        by_case[r["case"]].append(r)
    for title, wanted in (("Failing cases", "fail"), ("Flaky cases", "flaky")):
        ids = [c for c, st in s["statuses"].items() if st == wanted]
        if ids:
            out.append(f"\n## {title}\n")
            for cid in sorted(ids):
                bad = next(r for r in by_case[cid] if r["status"] == "fail")
                passed = sum(1 for r in by_case[cid] if r["status"] == "pass")
                known = f"  _(known: {bad['known_issue']})_" if bad.get("known_issue") else ""
                out.append(f"**{cid}** ({bad['type']}, passed {passed} of {sum(1 for r in by_case[cid] if r['status'] in ('pass', 'fail'))} runs){known}")
                out.append(f"- asked: {bad['questions'][-1]}")
                out.append(f"- tools: {', '.join(bad['tools']) or 'none'}")
                out.extend(f"- problem: {x}" for x in bad["reasons"])
                out.append(f"- reply: {bad['replies'][-1][:300].replace(chr(10), ' ')}\n")
    if s["bad_cases"]:
        out.append("\n## Broken cases (fix the case or the data; not counted)\n")
        for cid in sorted(s["bad_cases"]):
            r = next(r for r in by_case[cid] if r["status"] == "bad_case")
            out.append(f"- **{cid}**: {r['reasons'][0]}")
    if s["infra_cases"]:
        out.append(f"\n## Not scored: provider errors\n\n{', '.join(sorted(s['infra_cases']))}. Re-run with --resume.")
    if skipped:
        out.append(f"\n## Skipped (missing --order / --email)\n\n{', '.join(skipped)}")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- command line
def select(cases: list[dict], only: str | None, limit: int | None) -> list[dict]:
    if only:
        wanted = {w.strip() for w in only.split(",") if w.strip()}
        cases = [c for c in cases if c["id"] in wanted or c["type"] in wanted]
    return cases[:limit] if limit else cases


def preflight(cases: list[dict]) -> str | None:
    """Check the parts the questions depend on BEFORE any model tokens are spent. Returns a problem description or None."""
    if not any(c["type"] in ("policy", "combined") or "search_policies" in c.get("tools_any", []) for c in cases):
        return None
    try:
        from sjbot.tools.search_policies import search_policies
        result = search_policies({"question": "return policy"})
    except Exception as exc:
        hint = ""
        if "get_extended_attention_mask" in str(exc):
            hint = ("\nThe embedding model's downloaded code needs an older `transformers` than the one installed. "
                    "Try:  python -m pip install \"transformers<5\"   (then check:  python -m pip show transformers sentence-transformers)")
        return f"the knowledge search (search_policies) is broken: {type(exc).__name__}: {exc}{hint}"
    if not isinstance(result, dict) or result.get("error"):
        return f"the knowledge search (search_policies) returned an error: {str(result)[:200]}"
    return None


def main(argv: list[str] | None = None, llm: Any = None, answer_fn: Callable | None = None, run_query: Callable | None = None,
         cases: list[dict] | None = None, preflight_fn: Callable | None = None) -> int:
    ap = argparse.ArgumentParser(description="Measure bot accuracy.")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--only")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--order")
    ap.add_argument("--email")
    ap.add_argument("--sleep", type=float, default=0.0, help="seconds to wait between questions (helps with provider rate limits)")
    ap.add_argument("--resume")
    ap.add_argument("--max-tokens", type=int, help="stop after about this many tokens, so a daily limit is not used up (continue with --resume)")
    ap.add_argument("--skip-preflight", action="store_true", help="run even if the knowledge search is broken")
    ap.add_argument("--check-truth", action="store_true", help="run the ground-truth queries only; no model needed")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args(argv)

    if cases is None:
        from eval_cases import CASES as cases
    cases = select(list(cases), args.only, args.limit)
    if args.list:
        for c in cases:
            print(f"{c['type']:15} {c['id']:22} {c['turns'][-1][:70]}")
        return 0
    subs = {k: v for k, v in (("ORDER", args.order), ("EMAIL", args.email)) if v}
    runnable = [c for c in cases if all(n in subs for n in c.get("needs", []))]
    skipped = [c["id"] for c in cases if c not in runnable]
    if run_query is None:
        from sjbot.db import run_query
    truth = {c["id"]: fetch_truth(c, subs, run_query) for c in runnable}

    if args.check_truth:
        broken = n_capped = 0
        for c in runnable:
            rows, err = truth[c["id"]]
            if not c.get("truth"):
                continue
            t = c["truth"]
            bad = err or (not rows and not t.get("expect_empty")) or (rows and t.get("expect_empty"))
            broken += bool(bad)
            m = re.search(r"LIMIT\s+(\d+)\s*$", t["sql"].strip(), re.I)
            capped = bool(m and int(m.group(1)) > 1 and len(rows or []) >= int(m.group(1)))     # the answer key was cut off by its own LIMIT
            n_capped += capped and not bad
            sample = ", ".join(str(r.get(t["col"])) for r in (rows or [])[:3])
            print(f"{'BROKEN' if bad else ('CAPPED' if capped else 'ok    ')} {c['id']:20} {len(rows or []):4} rows  {err or sample}")
        print(f"\n{broken} broken ground-truth cases." + (f" {n_capped} CAPPED: the query hit its LIMIT, so valid answers beyond it would be marked wrong; raise the LIMIT." if n_capped else "")
              + (f" Skipped (need --order/--email): {', '.join(skipped)}" if skipped else ""))
        return 1 if broken else 0

    if not args.skip_preflight:
        problem = (preflight_fn or preflight)(runnable)
        if problem:
            print(f"\nNot starting: {problem}\nFix that first (or use --skip-preflight to run anyway); no model tokens were used.")
            return 2
    if llm is None:
        from sjbot.llm import get_llm
        llm = get_llm()
    if answer_fn is None:
        from sjbot.router import answer as answer_fn
    when = datetime.now().strftime("%Y-%m-%d %H:%M")
    RUNS_DIR.mkdir(exist_ok=True)
    path = Path(args.resume) if args.resume else RUNS_DIR / f"eval_{datetime.now():%Y%m%d_%H%M%S}.jsonl"
    done = {k for k, r in load_records(path).items() if r["status"] != "infra"}
    questions = sum(len(c["turns"]) for c in runnable)
    print(f"Model: {getattr(llm, 'model', 'unknown')}  ({type(llm).__name__})")
    print(f"{len(runnable)} cases x {args.runs} runs = {len(runnable) * args.runs} conversations, about {questions * args.runs * 10000:,} tokens (measured: roughly 10,000 per question)."
          f" Saving to {path}" + (f"  (skipping {len(skipped)} cases that need --order/--email)" if skipped else ""))
    infra_streak, stop = 0, False
    start_tokens = getattr(llm, "tokens", 0) or 0
    with path.open("a", encoding="utf8") as f:
        for run_idx in range(1, args.runs + 1):
            for c in runnable:
                if (c["id"], run_idx) in done:
                    continue
                rows, err = truth[c["id"]]
                if err:
                    rec = {"case": c["id"], "type": c["type"], "run": run_idx, **result("bad_case", [err], set()), "questions": c["turns"],
                           "replies": [], "tools": [], "blocked": [], "turns": 0, "seconds": 0, "tokens": 0, "known_issue": c.get("known_issue")}
                else:
                    rec = run_case(c, run_idx, answer_fn, llm, subs, rows, args.sleep)
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                f.flush()
                print(f"  run {run_idx} {c['id']:22} {rec['status'].upper():8} {'; '.join(rec['reasons'])[:90]}")
                infra_streak = infra_streak + 1 if rec["status"] == "infra" else 0
                if infra_streak >= 3:
                    print(f"\nStopping: 3 provider errors in a row (rate limit?). Continue later with:\n  python eval_accuracy.py --resume {path}")
                    stop = True
                elif args.max_tokens and (getattr(llm, "tokens", 0) or 0) - start_tokens >= args.max_tokens:
                    print(f"\nStopping: reached the --max-tokens budget ({args.max_tokens:,}). Continue later with:\n  python eval_accuracy.py --resume {path}")
                    stop = True
                if stop:
                    break
            if stop:
                break
    records = list(load_records(path).values())
    report = build_report(records, getattr(llm, "model", "unknown"), skipped, when)
    report_path = path.with_suffix(".md")
    report_path.write_text(report, encoding="utf8")
    print("\n" + report.split("## By type")[0])
    print(f"Full report: {report_path}")
    s = summarize(records)
    return 1 if (s["fail"] or s["flaky"] or s["invented_runs"] or s["leak_runs"] or s["tool_failed_runs"]) else 0


if __name__ == "__main__":
    sys.exit(main())