"""Accuracy evaluations for the bot. Developer-only: nothing in bot/ or rag/ may import this package.

Run from the repository root as modules, for example:
    python -m evals.eval_accuracy --check-truth
    python -m evals.eval_accuracy --cases eval_fresh --runs 3
    python -m evals.show_failures eval_runs/eval_20261015_162151.jsonl

eval_cases, eval_fresh, eval_fresh2, eval_heldout and eval_retest hold the case sets (CASES); eval_accuracy runs them and
writes eval_runs/. eval_phase2..6 are the older per-phase checks.
"""
