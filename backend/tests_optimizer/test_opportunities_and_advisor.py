"""
Test opportunity detector + index advisor — opportunity types per query shape,
plan-derived opportunities, and index recommendation gating.
"""
import os
import sys
import django

sys.path.insert(0, r'd:\AI projects\SQL Query Optimizer\backend')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'sql_optimizer.settings')
django.setup()

from queries.services.opportunity_detector import (
    detect_optimization_opportunities,
    OPPORTUNITY_TYPES,
    OptimizationOpportunity,
)
from queries.services.query_analyzer import analyze_query_structure
from queries.services.sql_parser import get_parser
from queries.services.index_advisor import recommend_indexes, IndexRecommendation
from queries.services.plan_analyzer import PlanAnalysis, PlanNodeMetrics

SCHEMA = {
    'tables': {
        'employees': {
            'columns': {
                'id': {'type': 'integer', 'primary_key': True},
                'first_name': {'type': 'text'},
                'last_name': {'type': 'text'},
                'email': {'type': 'text'},
                'hire_date': {'type': 'date'},
                'salary': {'type': 'numeric'},
                'department_id': {'type': 'integer', 'not_null': True},
            },
            'primary_key': ['id'],
            'indexes': [
                {'name': 'idx_department_id', 'columns': ['department_id'], 'unique': False},
            ]
        },
        'departments': {
            'columns': {
                'id': {'type': 'integer', 'primary_key': True},
                'name': {'type': 'text'},
                'budget': {'type': 'numeric'},
            },
            'primary_key': ['id'],
            'indexes': []
        }
    },
    'relationships': [
        {'from_table': 'employees', 'from_column': 'department_id', 'to_table': 'departments', 'to_column': 'id', 'type': 'many_to_one'}
    ]
}

parser = get_parser()


def detect(sql, schema=SCHEMA, plan=None):
    parsed = parser.parse(sql)
    structure = analyze_query_structure(parsed, sql, schema)
    return detect_optimization_opportunities(structure, plan, schema)


def types_of(opps):
    return {o.type for o in opps}


def test_select_star_opportunity():
    """SELECT * emits SELECT_STAR."""
    opps = detect("SELECT * FROM employees")
    assert 'SELECT_STAR' in types_of(opps)
    print("[OK] SELECT_STAR opportunity")


def test_function_on_column_opportunity():
    """YEAR(col) predicate emits FUNCTION_ON_COLUMN."""
    opps = detect("SELECT * FROM employees WHERE YEAR(hire_date) = 2025")
    assert 'FUNCTION_ON_COLUMN' in types_of(opps)
    fn = [o for o in opps if o.type == 'FUNCTION_ON_COLUMN'][0]
    assert fn.columns == ['hire_date']
    assert fn.severity == 'MEDIUM'
    print("[OK] FUNCTION_ON_COLUMN opportunity")


def test_calculation_on_column_opportunity():
    """Arithmetic on column emits CALCULATION_ON_COLUMN."""
    opps = detect("SELECT * FROM employees WHERE salary + 5000 = 100000")
    assert 'CALCULATION_ON_COLUMN' in types_of(opps)
    print("[OK] CALCULATION_ON_COLUMN opportunity")


def test_leading_wildcard_opportunity():
    """Leading-wildcard LIKE emits LEADING_WILDCARD (never rewritten)."""
    opps = detect("SELECT * FROM employees WHERE email LIKE '%@example.com'")
    assert 'LEADING_WILDCARD' in types_of(opps)
    print("[OK] LEADING_WILDCARD opportunity")


def test_in_to_exists_opportunity():
    """IN (subquery) emits IN_TO_EXISTS."""
    opps = detect(
        "SELECT * FROM employees WHERE department_id IN (SELECT id FROM departments)"
    )
    assert 'IN_TO_EXISTS' in types_of(opps)
    assert 'SUBQUERY_TO_JOIN' in types_of(opps)
    print("[OK] IN_TO_EXISTS + SUBQUERY_TO_JOIN opportunity")


def test_cross_join_opportunity():
    """CROSS JOIN emits CROSS_JOIN (cartesian concern)."""
    opps = detect("SELECT * FROM employees CROSS JOIN departments")
    assert 'CROSS_JOIN' in types_of(opps)
    cross = [o for o in opps if o.type == 'CROSS_JOIN'][0]
    assert cross.severity == 'HIGH'
    print("[OK] CROSS_JOIN opportunity")


def test_distinct_opportunities():
    """DISTINCT on PK emits UNNECESSARY_DISTINCT; on non-PK EXPENSIVE_DISTINCT."""
    opps_pk = detect("SELECT DISTINCT id FROM employees")
    assert 'UNNECESSARY_DISTINCT' in types_of(opps_pk), types_of(opps_pk)

    opps_nonpk = detect("SELECT DISTINCT department_id FROM employees")
    assert 'EXPENSIVE_DISTINCT' in types_of(opps_nonpk)
    print("[OK] DISTINCT opportunities")


def test_union_to_union_all_opportunity():
    """UNION (dedup) emits UNION_TO_UNION_ALL advisory."""
    opps = detect(
        "SELECT id FROM employees WHERE department_id = 1 "
        "UNION SELECT id FROM employees WHERE department_id = 2"
    )
    assert 'UNION_TO_UNION_ALL' in types_of(opps)
    print("[OK] UNION_TO_UNION_ALL opportunity")


def test_large_offset_opportunity():
    """Large OFFSET emits LARGE_OFFSET."""
    opps = detect("SELECT * FROM employees LIMIT 10 OFFSET 5000")
    assert 'LARGE_OFFSET' in types_of(opps)
    print("[OK] LARGE_OFFSET opportunity")


def test_plan_based_opportunities():
    """Plan-derived SEQUENTIAL_SCAN / POOR_ROW_ESTIMATION opportunities."""
    # Sequential scan plan
    opps_slow = detect(
        "SELECT * FROM employees WHERE email LIKE '%@%'",
        plan=seq_scan_plan(),
    )
    assert 'SEQUENTIAL_SCAN' in types_of(opps_slow)
    print("[OK] SEQUENTIAL_SCAN from plan")

    # Poor row estimation plan
    opps_stats = detect(
        "SELECT * FROM employees WHERE salary > 100",
        plan=poor_estimation_plan(),
    )
    assert 'POOR_ROW_ESTIMATION' in types_of(opps_stats)
    print("[OK] POOR_ROW_ESTIMATION from plan")


def test_opportunity_shape_and_registry():
    """Every returned opportunity has the documented keys; all types registered."""
    opps = detect("SELECT * FROM employees")
    for o in opps:
        assert isinstance(o, OptimizationOpportunity)
        d = o.to_dict()
        for key in ('type', 'severity', 'table', 'columns', 'evidence', 'confidence'):
            assert key in d
        assert d['type'] in OPPORTUNITY_TYPES
    print(f"Opportunity shape OK; {len(OPPORTUNITY_TYPES)} registered types")
    print("[OK] Opportunity shape and registry")


def test_missing_index_opportunity():
    """WHERE equality emits MISSING_INDEX (informational; advisor gates it)."""
    opps = detect("SELECT * FROM employees WHERE employees.first_name = 'John'")
    assert 'MISSING_INDEX' in types_of(opps), types_of(opps)
    print("[OK] MISSING_INDEX opportunity")


def test_index_advisor_gates_existing():
    """Advisor does not duplicate an existing index on department_id."""
    sql = "SELECT * FROM employees WHERE department_id = 5"
    parsed = parser.parse(sql)
    structure = analyze_query_structure(parsed, sql, SCHEMA)
    recs = recommend_indexes(structure, SCHEMA, seq_scan_plan())
    assert all('department_id' not in r.columns for r in recs), \
        "Should not recommend an index that already exists"
    print("[OK] Index advisor skips existing index")


def test_index_advisor_recommends_missing():
    """Advisor recommends an index on an unindexed equality column."""
    sql = "SELECT * FROM employees WHERE first_name = 'John'"
    parsed = parser.parse(sql)
    structure = analyze_query_structure(parsed, sql, SCHEMA)
    recs = recommend_indexes(structure, SCHEMA, seq_scan_plan())
    assert any('first_name' in r.columns for r in recs), "Should recommend first_name index"
    assert all(isinstance(r, IndexRecommendation) for r in recs)
    assert all(r.type == 'INDEX_RECOMMENDATION' for r in recs)
    # Serialization shape
    d = recs[0].to_dict()
    for key in ('type', 'sql', 'table', 'columns', 'reason', 'confidence', 'evidence'):
        assert key in d
    print("[OK] Index advisor recommends missing index")


def seq_scan_plan():
    root = PlanNodeMetrics(node_type='Seq Scan', relation_name='employees', total_cost=100.0)
    plan = PlanAnalysis(root=root)
    plan.seq_scans = 1
    plan.index_scans = 0
    plan.seq_scan_tables = ['employees']
    return plan


def poor_estimation_plan():
    root = PlanNodeMetrics(node_type='Seq Scan', total_cost=50.0)
    plan = PlanAnalysis(root=root)
    plan.total_cost = 50.0
    plan.estimation_error_nodes = 2
    plan.avg_estimation_error = 8.0
    return plan


if __name__ == '__main__':
    print("=" * 60)
    print("Testing Opportunities & Index Advisor")
    print("=" * 60)

    test_select_star_opportunity()
    test_function_on_column_opportunity()
    test_calculation_on_column_opportunity()
    test_leading_wildcard_opportunity()
    test_in_to_exists_opportunity()
    test_cross_join_opportunity()
    test_distinct_opportunities()
    test_union_to_union_all_opportunity()
    test_large_offset_opportunity()
    test_plan_based_opportunities()
    test_opportunity_shape_and_registry()
    test_missing_index_opportunity()
    test_index_advisor_gates_existing()
    test_index_advisor_recommends_missing()

    print("\n" + "=" * 60)
    print("All opportunities & advisor tests PASSED!")
    print("=" * 60)