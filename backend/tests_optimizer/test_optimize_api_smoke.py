"""
End-to-end optimize smoke test — exercises the built-in evidence-driven
pipeline via OptimizerService.optimize() AND the real DRF view path
(OptimizeQueryView + serializers + API key auth).

No live seed DB required: EXPLAIN falls back to the heuristic path, and the
schema/query-structure evidence still drives opportunities + index advice.
"""
import os
import sys
import django

sys.path.insert(0, r'd:\AI projects\SQL Query Optimizer\backend')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'sql_optimizer.settings')
django.setup()

from queries.services.optimizer import OptimizerService

SCHEMA = {
    'tables': {
        'employees': {
            'columns': {
                'id': {'type': 'integer', 'primary_key': True},
                'first_name': {'type': 'varchar'},
                'last_name': {'type': 'varchar'},
                'email': {'type': 'varchar'},
                'hire_date': {'type': 'date'},
                'joining_date': {'type': 'date'},
                'salary': {'type': 'decimal'},
                'department_id': {'type': 'integer'},
            },
            'primary_key': ['id'],
            'indexes': [
                {'name': 'idx_employees_salary', 'columns': ['salary'], 'unique': False},
            ]
        },
        'departments': {
            'columns': {
                'id': {'type': 'integer', 'primary_key': True},
                'name': {'type': 'varchar'},
                'budget': {'type': 'decimal'},
            },
            'primary_key': ['id'],
            'indexes': []
        }
    },
    'relationships': [
        {'from_table': 'employees', 'from_column': 'department_id', 'to_table': 'departments', 'to_column': 'id',
         'type': 'many_to_one'}
    ]
}

EXAMPLE_SQL = (
    "SELECT * FROM employees WHERE YEAR(hire_date) = 2025 "
    "AND department_id = 10 ORDER BY salary DESC"
)

NEW_CANDIDATE_KEYS = [
    'semantic_risk', 'plan_metrics', 'optimization_opportunities',
    'index_recommendations', 'statistics_recommendations', 'evidence_quality',
    'actual_execution_time', 'planning_time', 'execution_time',
    'row_estimation_quality', 'performance_improvement',
]
NEW_TOP_KEYS = [
    'sql_optimizations', 'index_recommendations', 'statistics_recommendations',
    'warnings', 'opportunities', 'evidence_source', 'heuristic_used',
]
OLD_CANDIDATE_KEYS = [
    'cost', 'startup_cost', 'plan_rows', 'plan_width', 'complexity_score',
    'validation_passed', 'semantic_safety', 'optimization_reasons',
    'confidence', 'performance_score',
]


def test_service_optimize_shape():
    """OptimizerService.optimize returns the full enriched response shape.

    Pin use_calcite=False: these assertions describe the builtin pipeline
    (schema validation, rewrite rules, index/opportunity evidence). When a
    Calcite server is reachable, the default path would return a Calcite
    candidate with no such evidence — environment-dependent, not a shape.
    """
    optimizer = OptimizerService(SCHEMA, use_calcite=False)
    result = optimizer.optimize(EXAMPLE_SQL)

    assert 'original_sql' in result and 'original_cost' in result
    assert 'candidates' in result and 'best_candidate' in result
    for key in NEW_TOP_KEYS:
        assert key in result, f"Missing top-level key {key}"
    assert 'original_cost' in result

    best = result['best_candidate']
    assert best is not None, "Expected a best candidate"
    for key in OLD_CANDIDATE_KEYS:
        assert key in best, f"Missing legacy candidate key {key}"
    for key in NEW_CANDIDATE_KEYS + ['confidence_level', 'confidence_score']:
        assert key in best, f"Missing new candidate key {key}"
    assert best['confidence_level'] in ('HIGH', 'MEDIUM', 'LOW')
    assert isinstance(best['confidence_score'], (int, float))

    # evidence_source + heuristic_used consistency
    assert result['evidence_source'] in ('EXPLAIN_ANALYZE', 'EXPLAIN', 'HEURISTIC')
    assert isinstance(result['heuristic_used'], bool)

    print(f"best candidate SQL: {best['sql']}")
    print(f"evidence_source={result['evidence_source']} heuristic_used={result['heuristic_used']}")
    print(f"candidates: {len(result['candidates'])}  opportunities: {len(result['opportunities'])}  "
          f"index recs: {len(result['index_recommendations'])}  sql_optimizations: {len(result['sql_optimizations'])}")
    print("[OK] Service optimize response shape")
    return result


def test_date_rewrite_candidate_generated():
    """The YEAR() predicate is rewritten to a half-open range candidate.

    Pin use_calcite=False: Calcite path emits no rewrite rules.
    """
    optimizer = OptimizerService(SCHEMA, use_calcite=False)
    result = optimizer.optimize(EXAMPLE_SQL)
    rewrite_rules = set()
    rewrites = []
    for c in result['candidates']:
        rules = c.get('rewrite_rules_applied') or []
        rewrite_rules.update(rules)
        if rules:
            rewrites.append((c['sql'], rules))
    assert any('date' in r.lower() for r in rewrite_rules), \
        f"Expected a date-function rewrite rule, got {rewrite_rules}"
    for sql, rules in rewrites:
        if any('date' in r.lower() for r in rules):
            assert "hire_date >= '2025-01-01'" in sql, f"No lower bound: {sql}"
            assert "hire_date < '2026-01-01'" in sql, f"No upper bound: {sql}"
            break
    print(f"rewrite rules detected: {sorted(rewrite_rules)}")
    print("[OK] YEAR() -> half-open range rewrite generated")


def test_index_and_opportunities_evidence():
    """Schema evidence produces index recs on department_id and FUNCTION_ON_COLUMN.

    Pin use_calcite=False: Calcite path omits index recs/opportunities.
    """
    optimizer = OptimizerService(SCHEMA, use_calcite=False)
    result = optimizer.optimize(EXAMPLE_SQL)

    idx_cols = [set(r['columns']) for r in result['index_recommendations']]
    assert any('department_id' in cols for cols in idx_cols), \
        f"Expected department_id index recommendation, got {idx_cols}"
    assert all(r['confidence'] > 0 for r in result['index_recommendations'])

    opp_types = {o['type'] for o in result['opportunities']}
    assert 'FUNCTION_ON_COLUMN' in opp_types, opp_types
    print(f"index recs: {idx_cols}  opportunity types: {sorted(opp_types)}")
    print("[OK] Index + opportunity evidence")


def test_sql_optimizations_summary():
    """sql_optimizations top-level summary is present and shaped as list of dicts.

    Pin use_calcite=False: Calcite path has no sql_optimizations list.
    """
    optimizer = OptimizerService(SCHEMA, use_calcite=False)
    result = optimizer.optimize(EXAMPLE_SQL)
    assert isinstance(result['sql_optimizations'], list)
    for opt in result['sql_optimizations']:
        assert isinstance(opt, dict)
        assert 'sql' in opt and 'description' in opt
        assert opt.get('evidence_quality') in ('EXPLAIN_ANALYZE', 'EXPLAIN', 'SCHEMA_STATS', 'HEURISTIC')
        assert opt.get('confidence_level') in ('HIGH', 'MEDIUM', 'LOW')
    print(f"sql_optimizations count: {len(result['sql_optimizations'])}")
    print("[OK] sql_optimizations summary")


def test_drf_view_path():
    """Real view: POST /api/queries/optimize/ with API key + schema string."""
    from rest_framework.test import APIClient
    from queries.services.optimizer import OptimizerService as _

    client = APIClient()
    schema_str = (
        "employees(id:integer primary_key, first_name:text, last_name:text, "
        "email:text, hire_date:date, salary:decimal, department_id:integer) | "
        "departments(id:integer primary_key, name:text, budget:decimal) | "
        "employees.department_id -> departments.id many_to_one | "
        "indexes: employees(salary unique)"
    )
    resp = client.post(
        '/api/queries/optimize/',
        {'sql': EXAMPLE_SQL, 'schema': schema_str, 'use_calcite': False},
        format='json',
        HTTP_API_KEY='dev-key-12345',
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.content[:500]}"
    data = resp.json()
    for key in NEW_TOP_KEYS + ['best_candidate', 'candidates', 'original_sql', 'original_cost']:
        assert key in data, f"Missing top-level key {key} in API response"
    assert data['best_candidate'] is not None
    assert data['best_candidate']['evidence_quality'] in (
        'EXPLAIN_ANALYZE', 'EXPLAIN', 'SCHEMA_STATS', 'HEURISTIC'
    )
    print(f"API status: {resp.status_code}; candidates={len(data['candidates'])}; "
          f"evidence={data['evidence_source']}; heuristic={data['heuristic_used']}")
    print("[OK] DRF view path")


def test_drf_enable_actual_execution_flag():
    """enable_actual_execution flag is accepted without error (default off)."""
    from rest_framework.test import APIClient

    client = APIClient()
    resp = client.post(
        '/api/queries/optimize/',
        {'sql': "SELECT id FROM employees WHERE department_id = 10",
         'use_calcite': False, 'enable_actual_execution': True},
        format='json',
        HTTP_API_KEY='dev-key-12345',
    )
    # Must still succeed (EXPLAIN ANALYZE if a seed DB is reachable, else fallback).
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.content[:500]}"
    body = resp.json()
    if body['evidence_source'] == 'EXPLAIN_ANALYZE':
        # A live DB ran ANALYZE: real execution timing must be present.
        assert body['best_candidate']['actual_execution_time'] is not None
        assert body['best_candidate']['evidence_quality'] == 'EXPLAIN_ANALYZE'
    else:
        # No DB: flag is safe, nothing executed.
        assert body['best_candidate']['actual_execution_time'] is None
    print(f"API (enable_actual_execution=True) OK: evidence={body['evidence_source']}")
    print("[OK] enable_actual_execution flag handled")


def test_default_explain_without_analyze():
    """Default (flag off): EXPLAIN runs but ANALYZE does not (no exec time)."""
    from rest_framework.test import APIClient

    client = APIClient()
    resp = client.post(
        '/api/queries/optimize/',
        {'sql': "SELECT id FROM employees WHERE department_id = 10",
         'use_calcite': False},
        format='json',
        HTTP_API_KEY='dev-key-12345',
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.content[:500]}"
    body = resp.json()
    if body['evidence_source'] == 'EXPLAIN':
        # Real EXPLAIN plan present, but no ANALYZE overlay on candidates.
        assert body['original_plan_analysis'] is not None
        assert body['best_candidate']['actual_execution_time'] is None
        assert body['heuristic_used'] is False
    else:
        # No DB in this environment; default flag must simply not error.
        assert body['best_candidate']['actual_execution_time'] is None
    print(f"Default no-ANALYZE OK: evidence={body['evidence_source']} "
          f"heuristic={body['heuristic_used']}")
    print("[OK] Default EXPLAIN without ANALYZE")


# --- NEW: qualify_columns candidate generation + promotion -------------------

SCHEMA_ALIAS_API = {
    'tables': {
        'student': {
            'columns': {
                'id': {'type': 'integer', 'primary_key': True},
                'name': {'type': 'text'},
                'department': {'type': 'text'},
            },
            'primary_key': ['id'],
            'indexes': [],
        },
        'grades': {
            'columns': {
                'id': {'type': 'integer', 'primary_key': True},
                'student_id': {'type': 'integer'},
                'mark': {'type': 'numeric'},
                'year': {'type': 'integer'},
            },
            'primary_key': ['id'],
            'indexes': [],
        },
    },
    'relationships': [
        {'from_table': 'grades', 'from_column': 'student_id', 'to_table': 'student', 'to_column': 'id', 'type': 'many_to_one'},
    ],
}


def test_qualify_columns_candidate_generated():
    """qualify_columns candidate is generated + promoted as best (no DB needed)."""
    optimizer = OptimizerService(SCHEMA_ALIAS_API, use_calcite=False)

    sql = "SELECT name, mark FROM student INNER JOIN grades ON student.id = grades.student_id WHERE department = 'CSE' AND year = 'CURRENT_YEAR' ORDER BY mark DESC LIMIT 3"
    result = optimizer.optimize(sql)

    # Check candidate with qualify_columns rule exists
    qualify_candidates = [c for c in result['candidates'] if 'qualify_columns' in (c.get('rewrite_rules_applied') or [])]
    assert qualify_candidates, f"No qualify_columns candidate; got {result['candidates']}"
    qc = qualify_candidates[0]
    assert qc['validation_passed'] is True, f"Candidate failed validation: {qc}"

    # Check best candidate is the promoted qualified version
    expected = "SELECT s.name, g.mark FROM student s INNER JOIN grades g ON student.id = grades.student_id WHERE s.department = 'CSE' AND g.year = '2026' ORDER BY g.mark DESC LIMIT 3"
    assert result['best_candidate']['sql'] == expected, f"Expected:\n{expected}\nGot:\n{result['best_candidate']['sql']}"

    # Check resolution warning present
    warnings = result['warnings']
    assert any('CURRENT_YEAR' in w for w in warnings), f"No CURRENT_YEAR warning in {warnings}"

    print(f"Best candidate: {result['best_candidate']['sql']}")
    print(f"Warnings: {warnings}")
    print("[OK] qualify_columns candidate generated and promoted")


if __name__ == '__main__':
    print("=" * 60)
    print("Testing Optimize API Smoke")
    print("=" * 60)

    test_service_optimize_shape()
    test_date_rewrite_candidate_generated()
    test_index_and_opportunities_evidence()
    test_sql_optimizations_summary()
    test_drf_view_path()
    test_drf_enable_actual_execution_flag()
    test_default_explain_without_analyze()

    # NEW: qualify_columns test
    test_qualify_columns_candidate_generated()

    print("\n" + "=" * 60)
    print("All optimize API smoke tests PASSED!")
    print("=" * 60)