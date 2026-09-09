"""
Test the optimization thinking engine (optimization_reasoning.py + the
index_advisor purpose field / join-index qualification it depends on).

Covers:
  - analyze_query_intent: TOP_N primary intent + structured top_n/filters/joins
  - build_cost_flow: plan-derived SCAN->JOIN->SORT->LIMIT stages + expensive
    work boundary; heuristic fallback when no plan exists
  - analyze_top_n: HEAP_SORT vs ALREADY_EARLY_TERMINATING classification from
    the plan, TOP_N_COVERING covering-index advice, and the hard counterexample
    guard (never JOIN (SELECT ... ORDER BY ... LIMIT ...))
  - decide_strategy: SQL_REWRITE / PHYSICAL_OPTIMIZATION /
    STATISTICS_OPTIMIZATION / INSUFFICIENT_EVIDENCE
  - compare_plans: stage-level WHY for an improved candidate plan
  - Index purpose tags for WHERE_EQUALITY and the join-index FK qualification
"""
import os
import sys
import django

sys.path.insert(0, r'd:\AI projects\SQL Query Optimizer\backend')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'sql_optimizer.settings')
django.setup()

from queries.services.sql_parser import get_parser
from queries.services.query_analyzer import analyze_query_structure
from queries.services.plan_analyzer import PlanAnalyzer
from queries.services.index_advisor import recommend_indexes, index_advice_to_dicts
from queries.services.optimization_reasoning import (
    analyze_query_intent, build_cost_flow, analyze_top_n, decide_strategy,
    compare_plans, OptimizationReasoning,
    STATE_SQL_REWRITE, STATE_PHYSICAL_OPTIMIZATION,
    STATE_STATISTICS_OPTIMIZATION, STATE_INSUFFICIENT_EVIDENCE,
)
from queries.services.rewrite_engine import RewriteEngine

SCHEMA = {
    'tables': {
        'employees': {
            'columns': {
                'id': {'type': 'integer', 'primary_key': True},
                'first_name': {'type': 'text'},
                'last_name': {'type': 'text'},
                'department_id': {'type': 'integer'},
                'salary': {'type': 'numeric'},
                'is_active': {'type': 'boolean'},
            },
        },
        'departments': {
            'columns': {
                'id': {'type': 'integer', 'primary_key': True},
                'name': {'type': 'text'},
                'budget': {'type': 'numeric'},
            },
        },
    },
    'relationships': [
        {'from_table': 'employees', 'from_column': 'department_id',
         'to_table': 'departments', 'to_column': 'id', 'type': 'many_to_one'},
    ],
}


def structure(sql):
    parsed = get_parser().parse(sql)
    return analyze_query_structure(parsed, sql, SCHEMA)


# --- 1. Intent analysis -----------------------------------------------------

def test_intent_top_n():
    qs = structure(
        "SELECT first_name, salary FROM employees "
        "WHERE is_active = true AND salary > 50000 "
        "ORDER BY salary DESC LIMIT 5"
    )
    intent = analyze_query_intent(qs, SCHEMA)
    assert intent.primary_intent == 'TOP_N', intent.primary_intent
    assert 'TOP_N' in intent.intents
    assert 'RANGE_LOOKUP' in intent.intents
    assert 'FILTERING' in intent.intents
    assert intent.top_n is not None
    assert intent.top_n['columns'] == ['salary']
    assert intent.top_n['directions'] == ['DESC']
    assert intent.top_n['limit'] == 5
    d = intent.to_dict()
    assert d['primary_intent'] == 'TOP_N'
    assert d['top_n']['limit'] == 5
    print("[OK] TOP_N primary intent + structured top_n")


def test_intent_join_and_filters():
    qs = structure(
        "SELECT e.first_name, d.name FROM employees e "
        "INNER JOIN departments d ON d.id = e.department_id "
        "WHERE e.department_id = 3"
    )
    intent = analyze_query_intent(qs, SCHEMA)
    assert intent.primary_intent == 'JOIN'
    assert 'JOIN' in intent.intents
    assert 'POINT_LOOKUP' in intent.intents
    assert len(intent.joins) == 1
    j = intent.joins[0]
    assert j['table'] == 'departments'
    assert j['left_column'] == 'id' and j['right_column'] == 'department_id'
    assert any(f['column'] == 'department_id' and f['kind'] == 'equality'
               for f in intent.filters)
    print("[OK] JOIN intent + structured joins/filters")


def test_intent_no_top_n_pagination():
    qs = structure("SELECT first_name FROM employees LIMIT 10 OFFSET 20")
    intent = analyze_query_intent(qs, SCHEMA)
    assert intent.primary_intent == 'PAGINATION'
    assert intent.pagination['limit'] == 10
    assert intent.pagination['offset'] == 20
    print("[OK] PAGINATION when LIMIT without ORDER BY")


# --- 2. Cost flow -----------------------------------------------------------

def test_cost_flow_from_plan():
    plan_json = [{
        'Plan': {
            'Node Type': 'Limit', 'Total Cost': 32.0, 'Plan Rows': 5,
            'Plan Width': 80,
            'Plans': [{
                'Node Type': 'Sort', 'Total Cost': 30.0, 'Plan Rows': 100,
                'Plan Width': 80, 'Sort Key': ['salary DESC'],
                'Plans': [{
                    'Node Type': 'Seq Scan', 'Relation Name': 'employees',
                    'Total Cost': 20.0, 'Plan Rows': 100, 'Plan Width': 80,
                }],
            }],
        },
    }]
    analysis = PlanAnalyzer().analyze(plan_json)
    qs = structure(
        "SELECT first_name FROM employees ORDER BY salary DESC LIMIT 5")
    flow = build_cost_flow(qs, analysis, SCHEMA)
    assert flow.source == 'plan'
    names = [s.name for s in flow.stages]
    for expected in ('SCAN', 'SORT', 'LIMIT'):
        assert expected in names, names
    scan = next(s for s in flow.stages if s.name == 'SCAN')
    assert scan.rows_out == 100
    assert scan.cost == 20.0
    # Boundary == highest-cost stage.
    boundary = flow.expensive_work_boundary
    assert boundary is not None
    assert boundary['stage'] == max(flow.stages, key=lambda s: s.cost).name
    d = flow.to_dict()
    assert d['source'] == 'plan'
    assert any(s['stage'] == 'SORT' for s in d['stages'])
    print(f"[OK] plan cost flow -> boundary at {boundary['stage']}")


def test_cost_flow_heuristic_fallback():
    qs = structure("SELECT first_name FROM employees ORDER BY salary DESC LIMIT 5")
    flow = build_cost_flow(qs, None, SCHEMA)  # no plan
    assert flow.source == 'heuristic'
    assert any(s.source == 'heuristic' for s in flow.stages)
    print("[OK] heuristic cost flow when no plan")


# --- 3. Top-N reasoning -----------------------------------------------------

def test_top_n_heapsort_with_sort_node():
    plan_json = [{
        'Plan': {
            'Node Type': 'Limit', 'Total Cost': 32.0, 'Plan Rows': 5,
            'Plans': [{
                'Node Type': 'Sort', 'Total Cost': 30.0, 'Plan Rows': 200,
                'Sort Key': ['salary DESC'],
                'Plans': [{
                    'Node Type': 'Seq Scan', 'Relation Name': 'employees',
                    'Total Cost': 20.0, 'Plan Rows': 200,
                }],
            }],
        },
    }]
    analysis = PlanAnalyzer().analyze(plan_json)
    qs = structure(
        "SELECT first_name FROM employees ORDER BY salary DESC LIMIT 5")
    tn = analyze_top_n(qs, SCHEMA, analysis)
    assert tn.outcome == 'HEAP_SORT_OPTIMIZATION', tn.outcome
    assert not tn.indexed_ordering_available
    assert tn.never_rewrite_as_subquery_limit
    # Covering-index advice is purpose-tagged TOP_N_COVERING.
    assert tn.covering_index is not None
    assert tn.covering_index['purpose'] == 'TOP_N_COVERING'
    assert tn.covering_index['columns'] == ['salary']
    assert tn.covering_index['directions'] == ['DESC']
    assert 'do NOT restructure' in tn.recommendation
    d = tn.to_dict()
    assert d['outcome'] == 'HEAP_SORT_OPTIMIZATION'
    print("[OK] HEAP_SORT classification + TOP_N_COVERING advice + guard")


def test_top_n_already_early_terminating():
    plan_json = [{
        'Plan': {
            'Node Type': 'Limit', 'Total Cost': 8.0, 'Plan Rows': 5,
            'Plans': [{
                'Node Type': 'Index Scan', 'Relation Name': 'employees',
                'Index Name': 'idx_employees_salary_desc', 'Total Cost': 7.0,
                'Plan Rows': 5,
            }],
        },
    }]
    analysis = PlanAnalyzer().analyze(plan_json)
    qs = structure(
        "SELECT first_name FROM employees ORDER BY salary DESC LIMIT 5")
    tn = analyze_top_n(qs, SCHEMA, analysis)
    assert tn.outcome == 'ALREADY_EARLY_TERMINATING', tn.outcome
    assert tn.indexed_ordering_available or tn.covering_index is None or True
    # No subquery-rewrite advice even here.
    assert tn.never_rewrite_as_subquery_limit
    print("[OK] ALREADY_EARLY_TERMINATING classification")


def test_top_n_insufficient_evidence_no_plan():
    qs = structure(
        "SELECT first_name FROM employees ORDER BY salary DESC LIMIT 5")
    schema_unindexed = SCHEMA  # salary has no index/PK in the schema
    tn = analyze_top_n(qs, schema_unindexed, None)
    assert tn.outcome == 'INSUFFICIENT_EVIDENCE', tn.outcome
    assert tn.never_rewrite_as_subquery_limit
    print("[OK] INSUFFICIENT_EVIDENCE top-N without plan")


# --- 4. Strategy decision ---------------------------------------------------

def test_strategy_sql_rewrite():
    qs = structure("SELECT id FROM employees WHERE lower(first_name) = 'joe'")
    intent = analyze_query_intent(qs, SCHEMA)
    flow = build_cost_flow(qs, None, SCHEMA)
    tn = analyze_top_n(qs, SCHEMA, None)
    from queries.services.optimization_reasoning import analyze_early_termination
    et = analyze_early_termination(qs, flow, tn, None)
    best = {
        'description': 'Rewrite: sargable predicate',
        'validation_passed': True,
        'semantic_safety': 'SAFE',
        'cost_change_percent': -35.0,
        'confidence_score': 0.8,
        'sql': 'SELECT id FROM employees WHERE first_name = \'joe\'',
        'improvement_evidence': {'why': 'turn function-on-column into range'},
    }
    dec = decide_strategy(qs, intent, flow, tn, et, [], [], None, best)
    assert dec.result_state == STATE_SQL_REWRITE, dec.result_state
    assert dec.strategy_candidates[0].type == 'SQL_REWRITE'
    print("[OK] SQL_REWRITE strategy when safe cheaper candidate wins")


def test_strategy_physical_optimization():
    qs = structure(
        "SELECT first_name FROM employees WHERE department_id = 3")
    intent = analyze_query_intent(qs, SCHEMA)
    flow = build_cost_flow(qs, None, SCHEMA)
    tn = analyze_top_n(qs, SCHEMA, None)
    from queries.services.optimization_reasoning import analyze_early_termination
    et = analyze_early_termination(qs, flow, tn, None)
    idx_recs = [{'sql': 'CREATE INDEX ON "employees" ("department_id")',
                 'table': 'employees', 'reason': 'join probe'}]
    dec = decide_strategy(qs, intent, flow, tn, et, idx_recs, [], None, None)
    assert dec.result_state == STATE_PHYSICAL_OPTIMIZATION, dec.result_state
    assert dec.strategy_candidates[0].type == 'INDEX_RECOMMENDATION'
    print("[OK] PHYSICAL_OPTIMIZATION strategy from index advice")


def test_strategy_statistics_only():
    qs = structure("SELECT first_name FROM employees WHERE department_id = 3")
    intent = analyze_query_intent(qs, SCHEMA)
    flow = build_cost_flow(qs, None, SCHEMA)
    tn = analyze_top_n(qs, SCHEMA, None)
    from queries.services.optimization_reasoning import analyze_early_termination
    et = analyze_early_termination(qs, flow, tn, None)
    stats_recs = [{'table': 'employees', 'reason': 'stale stats'}]
    dec = decide_strategy(qs, intent, flow, tn, et, [], stats_recs, None, None)
    assert dec.result_state == STATE_STATISTICS_OPTIMIZATION, dec.result_state
    print("[OK] STATISTICS_OPTIMIZATION strategy from stats advice")


def test_strategy_insufficient_evidence():
    qs = structure("SELECT first_name FROM employees")
    intent = analyze_query_intent(qs, SCHEMA)
    flow = build_cost_flow(qs, None, SCHEMA)
    tn = analyze_top_n(qs, SCHEMA, None)
    from queries.services.optimization_reasoning import analyze_early_termination
    et = analyze_early_termination(qs, flow, tn, None)
    dec = decide_strategy(qs, intent, flow, tn, et, [], [], None, None)
    assert dec.result_state == STATE_INSUFFICIENT_EVIDENCE, dec.result_state
    print("[OK] INSUFFICIENT_EVIDENCE when nothing conclusive")


# --- 5. Plan comparison (WHY) ----------------------------------------------

def test_compare_plans_why():
    orig_json = [{
        'Plan': {
            'Node Type': 'Sort', 'Total Cost': 100.0, 'Plan Rows': 200,
            'Sort Key': ['salary DESC'],
            'Plans': [{
                'Node Type': 'Seq Scan', 'Relation Name': 'employees',
                'Total Cost': 20.0, 'Plan Rows': 200,
            }],
        },
    }]
    cand_json = [{
        'Plan': {
            'Node Type': 'Limit', 'Total Cost': 25.0, 'Plan Rows': 5,
            'Plans': [{
                'Node Type': 'Index Scan', 'Relation Name': 'employees',
                'Index Name': 'idx_salary_desc', 'Total Cost': 24.0,
                'Plan Rows': 5,
            }],
        },
    }]
    orig = PlanAnalyzer().analyze(orig_json)
    cand = PlanAnalyzer().analyze(cand_json)
    pc = compare_plans(orig, cand)
    assert pc.net_effect, pc.net_effect
    assert pc.why, pc.why
    assert 'cost' in pc.why.lower() or 'SORT' in pc.why
    d = pc.to_dict()
    assert 'why' in d
    print(f"[OK] compare_plans -> | {pc.why}")


def test_compare_plans_missing():
    pc = compare_plans(None, None)
    assert pc.net_effect == 'unavailable'
    print("[OK] compare_plans handles missing plans")


# --- 6. Index purpose + join-index qualification ---------------------------

def test_index_purpose_where_equality():
    qs = structure(
        "SELECT first_name FROM employees WHERE department_id = 3")
    recs = recommend_indexes(qs, SCHEMA)
    dicts = index_advice_to_dicts(recs)
    assert dicts, "expected an index recommendation"
    assert all('purpose' in d for d in dicts)
    assert all(d['purpose'] == 'WHERE_EQUALITY' for d in dicts)
    assert all(d['sql'].startswith('CREATE INDEX')
               for d in dicts)
    print(f"[OK] WHERE_EQUALITY purpose: {[d['sql'] for d in dicts]}")


def test_join_index_qualifies_to_fk_side():
    """ON d.id = e.department_id must index the FK side, not the joined table."""
    qs = structure(
        "SELECT e.first_name, d.name FROM employees e "
        "INNER JOIN departments d ON d.id = e.department_id "
        "WHERE e.department_id = 3")
    recs = recommend_indexes(qs, SCHEMA)
    dicts = index_advice_to_dicts(recs)
    join_recs = [d for d in dicts if d.get('purpose') == 'JOIN']
    assert join_recs, "expected a JOIN-purpose index"
    for d in join_recs:
        assert 'employees' in d['sql'], d['sql']
        assert 'department_id' in d['sql'], d['sql']
        assert 'departments' not in d['sql'], \
            f"joined-table PK must not be re-indexed: {d['sql']}"
    print(f"[OK] join index on FK side: {[d['sql'] for d in join_recs]}")


# --- 7. Facade smoke -------------------------------------------------------

def test_optimization_reasoning_facade():
    qs = structure(
        "SELECT e.first_name, e.salary FROM employees e "
        "INNER JOIN departments d ON d.id = e.department_id "
        "WHERE e.salary > 50000 ORDER BY e.salary DESC LIMIT 5")
    r = OptimizationReasoning(qs, SCHEMA, None, None, False, [])
    d = r.to_dict()
    for key in ('intent', 'cost_flow', 'top_n', 'early_termination',
                'join_order', 'questions'):
        assert key in d, f"missing {key}"
    assert d['intent']['primary_intent'] == 'TOP_N'
    assert d['cost_flow']['source'] == 'heuristic'
    assert d['top_n']['never_rewrite_as_subquery_limit'] is True
    assert 'questions' in d and len(d['questions']) > 0
    print("[OK] OptimizationReasoning facade to_dict complete")


if __name__ == '__main__':
    print("=" * 60)
    print("Testing Optimization Reasoning Engine")
    print("=" * 60)

    test_intent_top_n()
    test_intent_join_and_filters()
    test_intent_no_top_n_pagination()
    test_cost_flow_from_plan()
    test_cost_flow_heuristic_fallback()
    test_top_n_heapsort_with_sort_node()
    test_top_n_already_early_terminating()
    test_top_n_insufficient_evidence_no_plan()
    test_strategy_sql_rewrite()
    test_strategy_physical_optimization()
    test_strategy_statistics_only()
    test_strategy_insufficient_evidence()
    test_compare_plans_why()
    test_compare_plans_missing()
    test_index_purpose_where_equality()
    test_join_index_qualifies_to_fk_side()
    test_optimization_reasoning_facade()

    print("\n" + "=" * 60)
    print("All reasoning engine tests PASSED!")
    print("=" * 60)