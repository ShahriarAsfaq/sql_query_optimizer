"""
Test runner for the evidence-driven optimizer suite.

Runs each standalone test module in its own namespace (no name collisions),
reports per-suite pass/fail, and exits non-zero if any suite fails.

Usage:
    python tests_optimizer/run_all.py
"""
import os
import sys
import traceback

sys.path.insert(0, r'd:\AI projects\SQL Query Optimizer\backend')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'sql_optimizer.settings')

HERE = os.path.dirname(os.path.abspath(__file__))

SUITES = [
    'test_rewrite_correctness.py',
    'test_semantic_safety.py',
    'test_index_detection.py',
    'test_plan_analysis.py',
    'test_ranking.py',
    'test_opportunities_and_advisor.py',
    'test_cost_model.py',
    'test_optimize_api_smoke.py',
    'test_schema_honoring.py',
    'test_reasoning_engine.py',
]


def main():
    failed = []
    for name in SUITES:
        path = os.path.join(HERE, name)
        print(f"\n{'#' * 70}\n# {name}\n{'#' * 70}")
        try:
            spec = __import__('runpy').run_path(path, run_name='__main__')
            print(f"\n>>> SUITE PASSED: {name}")
        except SystemExit as exc:  # a suite may sys.exit(nonzero)
            code = exc.code
            failed.append((name, 'SystemExit(%r)' % (code,)))
            print(f"\n>>> SUITE FAILED (exit {code}): {name}")
        except Exception as exc:
            failed.append((name, repr(exc)))
            print(f"\n>>> SUITE FAILED: {name}")
            traceback.print_exc()

    print("\n" + "=" * 70)
    if failed:
        print(f"RESULT: {len(failed)}/{len(SUITES)} suite(s) FAILED")
        for name, err in failed:
            print(f"  - {name}: {err}")
        print("=" * 70)
        sys.exit(1)
    print(f"RESULT: ALL {len(SUITES)} SUITES PASSED")
    print("=" * 70)


if __name__ == '__main__':
    main()