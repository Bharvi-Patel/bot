import json

import pytest

import eval_accuracy as ev
import eval_cases


def out(reply, tools=(), blocked=None):
    return {"reply": reply, "tools_used": list(tools), "blocked": blocked}


ROWS = [{"name": "Greenbriar Sofa", "price": 215.0}, {"name": "Darcy Sofa", "price": 249.7}, {"name": "Mahoney Sofa", "price": 249.7}]
TRUTH = {"sql": "x", "col": "name", "price_col": "price", "min_hits": 2}
CASE = {"id": "c", "type": "product_search", "turns": ["sofas under $250"], "truth": TRUTH, "prices": True, "max_price": 250}


def score(case, reply, rows=ROWS, **kw):
    return ev.score_run(case, [out(reply, **kw)], rows)


# ---------------------------------------------------------------- helpers
def test_wilson_interval_is_wide_for_small_samples_and_tight_for_large_ones():
    lo, hi = ev.wilson(9, 10)
    assert 0.55 < lo < 0.65 and 0.95 < hi < 1.0
    lo, hi = ev.wilson(900, 1000)
    assert 0.87 < lo < 0.89 and 0.91 < hi < 0.92
    assert ev.wilson(0, 0) == (0.0, 0.0) and ev.wilson(0, 10)[0] == 0.0 and ev.wilson(10, 10)[1] == 1.0


def test_norm_and_forms_ignore_markdown_dashes_quotes_and_marks():
    assert ev.norm("**Loreo Sofa** \u2013 Ashley\u00ae  can\u2019t") == "loreo sofa - ashley can't"
    assert {"215.00", "215", "1,000.00"} <= ev.forms(215.0) | ev.forms(1000.0)
    assert "249.70" in ev.forms(249.7) and "249" not in ev.forms(249.7)
    assert ev.forms("Greenbriar Sofa") == {"greenbriar sofa"}


# ---------------------------------------------------------------- scoring a run
GOOD = "Here are sofas: **Greenbriar Sofa** - $215.00, Darcy Sofa - $249.70, Mahoney Sofa - $249.70."


def test_a_correct_listing_passes():
    assert score(CASE, GOOD)["status"] == "pass"


def test_false_none_is_flagged_when_the_database_has_results():
    r = score(CASE, "I couldn't find any sofas under $250.")
    assert r["status"] == "fail" and "false_none" in r["flags"]
    assert any("although the database has matching products" in x for x in r["reasons"])


def test_a_wrong_or_swapped_price_fails_even_though_every_number_exists_somewhere():
    swapped = "Greenbriar Sofa - $249.70, Darcy Sofa - $215.00, Mahoney Sofa - $249.70."
    r = score(CASE, swapped)
    assert r["status"] == "fail" and any("Greenbriar Sofa is missing or wrong" in x for x in r["reasons"])


def test_prices_in_a_table_or_list_are_matched_to_the_product_on_the_same_row():
    table = "| Greenbriar Sofa | Ashley | $215.00 |\n| Darcy Sofa | Ashley | $249.70 |\n| Mahoney Sofa | Ashley | $249.70 |"
    assert score(CASE, table)["status"] == "pass"
    assert score(CASE, "Darcy Sofa $249.70, Mahoney Sofa $249.70, Greenbriar Sofa $215.00")["status"] == "pass"
    assert score(CASE, "Greenbriar Sofa $249.70, Darcy Sofa $249.70, Mahoney Sofa $249.70")["status"] == "fail"


def test_a_repeated_product_name_with_the_same_price_is_fine():
    case = {**CASE, "truth": {**TRUTH, "min_hits": 1}, "max_price": None}
    rows = [{"name": "Loreo Sofa", "price": 257.46}] * 3
    assert ev.score_run(case, [out("Loreo Sofa $257.46, Loreo Sofa $257.46, Loreo Sofa $257.46")], rows)["status"] == "pass"


def test_prices_above_the_limit_fail():
    assert any("above $250" in x for x in score(CASE, GOOD + " Also the Big Sofa at $900.")["reasons"])


def test_too_few_listed_products_fails_without_claiming_false_none():
    r = score(CASE, "The Greenbriar Sofa is $215.00.")
    assert r["status"] == "fail" and "false_none" not in r["flags"]


def test_empty_truth_cases():
    case = {"id": "n", "type": "t", "turns": ["q"], "truth": {"sql": "x", "col": "name", "expect_empty": True}}
    assert ev.score_run(case, [out("No sofas are available under $100.")], [])["status"] == "pass"
    assert ev.score_run(case, [out("Try the Greenbriar Sofa at $215.00.")], [])["status"] == "fail"
    assert ev.score_run(case, [out("No sofas under $100.")], ROWS)["status"] == "bad_case"       # the data changed, not the bot
    assert ev.score_run(CASE, [out(GOOD)], [])["status"] == "bad_case"


def test_share_based_threshold():
    case = {"id": "b", "type": "t", "turns": ["q"], "truth": {"sql": "x", "col": "name", "min_share": 0.8}}
    rows = [{"name": n} for n in ("Acme", "Coaster", "Nectar", "Dreamcloud", "Beautyrest")]
    assert ev.score_run(case, [out("We carry Acme, Coaster, Nectar and Dreamcloud.")], rows)["status"] == "pass"
    assert ev.score_run(case, [out("We carry Acme and Coaster.")], rows)["status"] == "fail"


def test_word_checks():
    case = {"id": "w", "type": "t", "turns": ["q"], "mention_any": ["pickup", "pick up"], "mention_all": ["299", ["final", "return"]],
            "not_say": ["do not offer", "re:\\d+% off"], "max_chars": 120}
    assert ev.score_run(case, [out("Pick up is free and shipping is $299. Returns are final.")], None)["status"] == "pass"
    bad = ev.score_run(case, [out("We do not offer this. 20% off! " + "x" * 120)], None)
    assert bad["status"] == "fail" and len(bad["reasons"]) == 6


def test_tool_checks_and_refusal():
    case = {"id": "t", "type": "t", "turns": ["q"], "tools_any": ["get_order_status"], "tools_none": ["search_products"], "refuse": True}
    assert ev.score_run(case, [out("Sorry, I can't do that.", tools=["get_order_status"])], None)["status"] == "pass"
    r = ev.score_run(case, [out("Here you go: $5.", tools=["search_products"])], None)
    assert len(r["reasons"]) == 3


def test_blocked_replies_and_fallbacks_are_counted_as_failures_with_flags():
    r = ev.score_run(CASE, [out("Sorry, I can't answer that reliably.", blocked="unverified_price")], ROWS)
    assert r["status"] == "fail" and {"invented", "fallback"} <= set(r["flags"])
    assert "leak" in ev.score_run(CASE, [out("x", blocked="internal_leak")], ROWS)["flags"]
    plain = ev.score_run({"id": "g", "type": "t", "turns": ["q"]}, [out("Sorry, I can't answer that reliably.")], None)
    assert plain["status"] == "fail" and plain["flags"] == ["fallback"]


def test_an_expected_block_is_not_a_failure():
    case = {"id": "x", "type": "t", "turns": ["q"], "refuse": True, "blocked_ok": ["extraction_attempt"]}
    assert ev.score_run(case, [out("I can't help with that.", blocked="extraction_attempt")], None)["status"] == "pass"


def test_provider_errors_are_not_counted_against_the_bot():
    r = ev.score_run(CASE, [out("Sorry, I can't answer that reliably.", blocked="llm_error")], ROWS)
    assert r["status"] == "infra" and r["flags"] == ["infra"]


# ---------------------------------------------------------------- per-case and overall summary
def rec(case, run, status, typ="t", flags=(), reasons=()):
    return {"case": case, "type": typ, "run": run, "status": status, "flags": list(flags), "reasons": list(reasons), "seconds": 2.0,
            "turns": 1, "tokens": 1000, "questions": ["q"], "replies": ["r"], "tools": [], "blocked": [None], "known_issue": None}


def test_case_status_is_stable_flaky_fail_or_not_scored():
    assert ev.case_status([rec("a", 1, "pass"), rec("a", 2, "pass")]) == "stable_pass"
    assert ev.case_status([rec("a", 1, "pass"), rec("a", 2, "fail")]) == "flaky"
    assert ev.case_status([rec("a", 1, "fail"), rec("a", 2, "fail")]) == "fail"
    assert ev.case_status([rec("a", 1, "infra")]) == "infra"
    assert ev.case_status([rec("a", 1, "pass"), rec("a", 2, "infra")]) == "stable_pass"       # provider errors do not spoil a case
    assert ev.case_status([rec("a", 1, "bad_case")]) == "bad_case"


def test_summary_counts_and_report_text():
    records = [rec("a", 1, "pass", "x"), rec("a", 2, "pass", "x"), rec("b", 1, "pass", "x"), rec("b", 2, "fail", "x", ["false_none"], ["said none"]),
               rec("c", 1, "fail", "y", ["invented", "fallback"], ["bad"]), rec("c", 2, "fail", "y"), rec("d", 1, "infra", "y"), rec("e", 1, "bad_case", "y", [], ["no rows"])]
    s = ev.summarize(records)
    assert (s["stable_pass"], s["flaky"], s["fail"], s["cases_scored"]) == (1, 1, 1, 3)
    assert s["false_none_runs"] == 1 and s["invented_runs"] == 1 and s["infra_runs"] == 1 and s["bad_cases"] == ["e"]
    assert s["avg_tokens_per_question"] == 1000 and s["per_type"]["x"] == {"stable_pass": 1, "flaky": 1}
    report = ev.build_report(records, "m", ["order-ok"], "2026-10-07")
    for text in ("1 of 3 = 33%", "False \"nothing found\" answers: 1", "Failing cases", "Flaky cases", "Broken cases", "order-ok", "said none"):
        assert text in report


# ---------------------------------------------------------------- running cases
class FakeLLM:
    model, tokens = "fake", 0


def test_run_case_keeps_the_conversation_uses_a_fresh_visitor_and_masks_personal_data():
    seen = []

    def answer(q, llm, history, ctx=None):
        seen.append((q, list(history), ctx.client_id))
        llm.tokens += 500
        return out(f"Order for jo@example.com is marked Paid. Call (731) 423-6565. Number 102500001.")

    case = {"id": "oc", "type": "order", "turns": ["where is order {ORDER}? email {EMAIL}", "thanks"], "needs": ["ORDER", "EMAIL"], "mention_any": ["marked"]}
    r = ev.run_case(case, 2, answer, FakeLLM(), {"ORDER": "102500001", "EMAIL": "jo@example.com"}, None)
    assert seen[0][0] == "where is order 102500001? email jo@example.com" and seen[0][1] == []
    assert seen[1][1] == [{"role": "user", "text": seen[0][0]}, {"role": "assistant", "text": r["replies"][0].replace("[email]", "jo@example.com").replace("[phone]", "(731) 423-6565").replace("[number]", "102500001")}] or len(seen[1][1]) == 2
    assert seen[0][2] == "eval-oc-2" and r["status"] == "pass" and r["tokens"] == 1000 and r["turns"] == 2
    saved = json.dumps(r)
    assert "jo@example.com" not in saved and "423-6565" not in saved and "102500001" not in saved and "[email]" in saved


def test_run_case_separates_provider_crashes_from_bot_crashes():
    class RateLimitError(Exception):
        pass

    def provider(*a, **k):
        raise RateLimitError("429")

    def bug(*a, **k):
        raise KeyError("x")

    case = {"id": "c", "type": "t", "turns": ["q"]}
    assert ev.run_case(case, 1, provider, FakeLLM(), {}, None)["status"] == "infra"
    assert ev.run_case(case, 1, bug, FakeLLM(), {}, None)["status"] == "fail"


# ---------------------------------------------------------------- the command line
CASES = [
    {"id": "sofa", "type": "product_search", "turns": ["sofas under $250"], "truth": TRUTH, "prices": True},
    {"id": "hi", "type": "followup", "turns": ["hii"], "not_say": ["@"]},
    {"id": "ord", "type": "order", "turns": ["order {ORDER}"], "needs": ["ORDER"]},
]


def fake_query(sql, params=()):
    return [dict(r) for r in ROWS]


@pytest.fixture
def tmp_runs(monkeypatch, tmp_path):
    monkeypatch.setattr(ev, "RUNS_DIR", tmp_path / "eval_runs")
    return tmp_path / "eval_runs"


def test_main_runs_scores_writes_files_and_skips_cases_that_need_an_order(tmp_runs, capsys):
    calls = []

    def answer(q, llm, history, ctx=None):
        calls.append(q)
        return out(GOOD if "sofas" in q else "Hello! What are you looking for?")

    code = ev.main(["--runs", "2"], llm=FakeLLM(), answer_fn=answer, run_query=fake_query, cases=CASES)
    assert code == 0 and len(calls) == 4                                   # 2 runnable cases x 2 runs; the order case was skipped
    files = sorted(p.name for p in tmp_runs.iterdir())
    assert len(files) == 2 and files[0].endswith(".jsonl") and files[1].endswith(".md")
    report = (tmp_runs / files[1]).read_text(encoding="utf8")
    assert "2 of 2 = 100%" in report and "ord" in report                  # skipped list
    assert "Stable pass" in capsys.readouterr().out


def test_main_exit_code_is_1_when_something_fails(tmp_runs):
    code = ev.main(["--runs", "1"], llm=FakeLLM(), answer_fn=lambda q, llm, h, ctx=None: out("I couldn't find any sofas."),
                   run_query=fake_query, cases=CASES[:1])
    assert code == 1


def test_main_stops_after_three_provider_errors_and_resume_finishes_the_job(tmp_runs, capsys):
    cases = [{"id": f"c{i}", "type": "t", "turns": ["q"]} for i in range(6)]
    state = {"down": True, "asked": []}

    def answer(q, llm, history, ctx=None):
        state["asked"].append(ctx.client_id)
        return out("Sorry, I can't answer that reliably.", blocked="llm_error") if state["down"] else out("Fine.")

    ev.main(["--runs", "1"], llm=FakeLLM(), answer_fn=answer, run_query=fake_query, cases=cases)
    assert len(state["asked"]) == 3 and "--resume" in capsys.readouterr().out          # stopped early instead of burning every case
    path = next(tmp_runs.glob("*.jsonl"))
    state["down"], state["asked"] = False, []
    ev.main(["--runs", "1", "--resume", str(path)], llm=FakeLLM(), answer_fn=answer, run_query=fake_query, cases=cases)
    assert len(state["asked"]) == 6                                                    # the three failed ones are redone, nothing else is skipped
    state["asked"] = []
    ev.main(["--runs", "1", "--resume", str(path)], llm=FakeLLM(), answer_fn=answer, run_query=fake_query, cases=cases)
    assert state["asked"] == []                                                        # everything is done, so nothing is asked again


def test_check_truth_reports_broken_cases_without_a_model(tmp_runs, capsys):
    cases = [CASES[0], {**CASES[0], "id": "empty"}]
    queries = iter([ROWS, []])
    code = ev.main(["--check-truth"], run_query=lambda sql, params=(): next(queries), cases=cases)
    out_text = capsys.readouterr().out
    assert code == 1 and "ok     sofa" in out_text and "BROKEN empty" in out_text and not tmp_runs.exists()


def test_a_failing_ground_truth_query_becomes_a_broken_case_not_a_crash(tmp_runs):
    def boom(sql, params=()):
        raise RuntimeError("db down")
    ev.main(["--runs", "1"], llm=FakeLLM(), answer_fn=lambda *a, **k: out("x"), run_query=boom, cases=CASES[:1])
    report = next(tmp_runs.glob("*.md")).read_text(encoding="utf8")
    assert "Broken cases" in report and "db down" in report


def test_select_filters_by_id_type_and_limit():
    assert [c["id"] for c in ev.select(CASES, "sofa,followup", None)] == ["sofa", "hi"]
    assert [c["id"] for c in ev.select(CASES, None, 2)] == ["sofa", "hi"]


# ---------------------------------------------------------------- the real question set is well formed
def test_the_question_set_is_well_formed():
    ids = [c["id"] for c in eval_cases.CASES]
    assert len(ids) == len(set(ids)) >= 50
    for c in eval_cases.CASES:
        assert c["turns"] and all(isinstance(t, str) and t for t in c["turns"]) and c["type"]
        used = {p for t in c["turns"] for p in ("ORDER", "EMAIL") if "{" + p + "}" in t}
        assert used <= set(c.get("needs", [])), c["id"]
        truth = c.get("truth")
        if truth:
            assert truth["sql"].strip().upper().startswith("SELECT") and "vw_chat_" in truth["sql"] and "col" in truth
            assert truth["sql"].count("%s") == len(truth.get("params", []))
            assert not (c.get("prices") and "price_col" not in truth)
        assert len({k for k in c} - {"id", "type", "turns", "truth", "prices", "mention_any", "mention_all", "not_say", "tools_any", "tools_none",
                                      "max_price", "refuse", "max_chars", "blocked_ok", "known_issue", "needs"}) == 0, c["id"]
        for w in c.get("not_say", []):
            if w.startswith("re:"):
                __import__("re").compile(w[3:])