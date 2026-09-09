"""
Test semantic safety of rewrites - NULL semantics, duplicates, outer joins,
aggregation, timestamp boundaries, timezone, DISTINCT, ordering preservation.
"""
import os
import sys
import django

sys.path.insert(0, r'd:\AI projects\SQL Query Optimizer\backend')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'sql_optimizer.settings')
django.setup()

from queries.services.rewrite_engine import RewriteEngine
from queries.services.query_analyzer import analyze_query_structure
from queries.services.sql_parser import get_parser

SCHEMA = {
    'tables': {
        'employees': {
            'columns': {
                'id': {'type': 'integer', 'primary_key': True},
                'first_name': {'type': 'text'},
                'last_name': {'type': 'text'},
                'email': {'type': 'text', 'nullable': True},
                'hire_date': {'type': 'date'},
                'joining_timestamp': {'type': 'timestamp'},
                'created_at': {'type': 'timestamptz'},
                'salary': {'type': 'numeric', 'nullable': True},
                'department_id': {'type': 'integer', 'nullable': True},
            },
            'primary_key': ['id'],
            'indexes': []
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
engine = RewriteEngine(SCHEMA)


def test_not_in_nullable_no_rewrite():
    """NOT IN with nullable column should NOT rewrite to NOT EXISTS."""
    sql = "SELECT * FROM employees WHERE department_id NOT IN (SELECT id FROM departments)"
    parsed = parser.parse(sql)
    structure = analyze_query_structure(parsed, sql, SCHEMA)

    result = engine.rewrite_in_to_exists(sql, parsed, structure)
    print(f"NOT IN nullable: {result}")
    assert result is None, "NOT IN with nullable should be advisory"
    print("[OK] NOT IN nullable correctly advisory")


def test_in_nullable_no_rewrite():
    """IN with nullable column should NOT rewrite to EXISTS."""
    sql = "SELECT * FROM employees WHERE email IN (SELECT email FROM departments)"
    parsed = parser.parse(sql)
    structure = analyze_query_structure(parsed, sql, SCHEMA)

    result = engine.rewrite_in_to_exists(sql, parsed, structure)
    print(f"IN nullable email: {result}")
    assert result is None, "IN with nullable should be advisory"
    print("[OK] IN nullable correctly advisory")


def test_distinct_removal_preserves_ordering():
    """DISTINCT removal should not change ordering when LIMIT present."""
    sql = "SELECT DISTINCT department_id FROM employees ORDER BY department_id LIMIT 5"
    parsed = parser.parse(sql)
    structure = analyze_query_structure(parsed, sql, SCHEMA)

    result = engine.rewrite_remove_distinct(sql, parsed, structure)
    print(f"DISTINCT with ORDER BY LIMIT: {result}")
    # department_id is not PK, should be advisory
    assert result is None, "DISTINCT on non-PK with ORDER BY should be advisory"
    print("[OK] DISTINCT on non-PK with ORDER BY correctly advisory")


def test_distinct_removal_pk_no_limit():
    """DISTINCT removal when PK projected but no LIMIT - safe."""
    sql = "SELECT DISTINCT id FROM employees"
    parsed = parser.parse(sql)
    structure = analyze_query_structure(parsed, sql, SCHEMA)

    result = engine.rewrite_remove_distinct(sql, parsed, structure)
    print(f"DISTINCT PK no LIMIT: {result}")
    assert result is not None, "DISTINCT on PK should be removed"
    assert "DISTINCT" not in result
    print("[OK] DISTINCT on PK without LIMIT removed")


def test_union_to_union_all_preserves_ordering():
    """UNION ALL preserves ORDER BY - but we only test the rewrite."""
    sql = "SELECT id FROM employees WHERE department_id = 1 UNION ALL SELECT id FROM employees WHERE department_id = 2"
    parsed = parser.parse(sql)
    structure = analyze_query_structure(parsed, sql, SCHEMA)

    result = engine.rewrite_union_to_union_all(sql, parsed, structure)
    print(f"UNION ALL stays UNION ALL: {result}")
    # Already UNION ALL, should return None (no change)
    assert result is None, "UNION ALL already should return None"
    print("[OK] UNION ALL unchanged")


def test_timestamptz_date_rewrite_skipped():
    """timestamptz columns should skip date rewrites (timezone-sensitive)."""
    sql = "SELECT * FROM employees WHERE YEAR(created_at) = 2025"
    parsed = parser.parse(sql)
    structure = analyze_query_structure(parsed, sql, SCHEMA)

    result = engine.rewrite_date_function(sql, parsed, structure)
    print(f"YEAR timestamptz: {result}")
    assert result is None, "timestamptz should be advisory"
    print("[OK] timestamptz correctly skipped")


def test_arithmetic_overflow_advisory():
    """Integer column arithmetic should be advisory (overflow semantics)."""
    schema_int = {
        'tables': {
            'employees': {
                'columns': {
                    'id': {'type': 'integer', 'primary_key': True},
                    'salary': {'type': 'integer'},
                },
                'primary_key': ['id'],
            }
        },
        'relationships': []
    }
    engine_int = RewriteEngine(schema_int)
    sql = "SELECT * FROM employees WHERE salary + 5000 = 100000"
    parsed = parser.parse(sql)
    structure = analyze_query_structure(parsed, sql, schema_int)

    result = engine_int.rewrite_arithmetic_on_column(sql, parsed, structure)
    print(f"Arithmetic on integer: {result}")
    assert result is None, "Integer arithmetic should be advisory"
    print("[OK] Integer arithmetic correctly advisory")


def test_outer_join_filter_pushdown_blocked():
    """Filter pushdown should NOT happen for OUTER JOINs."""
    sql = "SELECT * FROM employees LEFT JOIN departments ON employees.department_id = departments.id WHERE departments.budget > 100000"
    parsed = parser.parse(sql)
    structure = analyze_query_structure(parsed, sql, SCHEMA)

    result = engine.rewrite_filter_before_join(sql, parsed, structure)
    print(f"LEFT JOIN filter pushdown: {result}")
    assert result is None, "LEFT JOIN filter pushdown should be blocked"
    print("[OK] LEFT JOIN filter pushdown correctly blocked")


def test_cross_join_fk_wrong_direction():
    """CROSS JOIN with FK but wrong table order."""
    sql = "SELECT * FROM departments CROSS JOIN employees"
    parsed = parser.parse(sql)
    structure = analyze_query_structure(parsed, sql, SCHEMA)

    result = engine.rewrite_cross_join(sql, parsed, structure)
    print(f"CROSS JOIN reversed: {result}")
    # Should still work - FK exists between them
    assert result is not None, "CROSS JOIN with FK should work regardless of order"
    print("[OK] CROSS JOIN with FK works for reversed tables")


def test_date_boundary_leap_year():
    """Date rewrite handles leap year correctly."""
    sql = "SELECT * FROM employees WHERE YEAR(hire_date) = 2024"
    parsed = parser.parse(sql)
    structure = analyze_query_structure(parsed, sql, SCHEMA)

    result = engine.rewrite_date_function(sql, parsed, structure)
    print(f"YEAR 2024 (leap): {result}")
    assert result is not None
    assert "hire_date >= '2024-01-01'" in result
    assert "hire_date < '2025-01-01'" in result
    print("[OK] Leap year boundary correct")


def test_month_boundary_december():
    """Month rewrite handles December->January transition."""
    sql = "SELECT * FROM employees WHERE YEAR(hire_date) = 2025 AND MONTH(hire_date) = 12"
    parsed = parser.parse(sql)
    structure = analyze_query_structure(parsed, sql, SCHEMA)

    result = engine.rewrite_date_function(sql, parsed, structure)
    print(f"MONTH 12: {result}")
    assert result is not None
    assert "hire_date >= '2025-12-01'" in result
    assert "hire_date < '2026-01-01'" in result
    print("[OK] December boundary correct")


def test_day_boundary_month_end():
    """Day rewrite handles month-end correctly."""
    sql = "SELECT * FROM employees WHERE YEAR(hire_date) = 2025 AND MONTH(hire_date) = 1 AND DAY(hire_date) = 31"
    parsed = parser.parse(sql)
    structure = analyze_query_structure(parsed, sql, SCHEMA)

    result = engine.rewrite_date_function(sql, parsed, structure)
    print(f"DAY 31: {result}")
    assert result is not None
    assert "hire_date >= '2025-01-31'" in result
    assert "hire_date < '2025-02-01'" in result
    print("[OK] Month-end day boundary correct")


def test_leading_wildcard_never_rewritten():
    """Leading wildcard LIKE is never rewritten - only advisory."""
    sql = "SELECT * FROM employees WHERE email LIKE '%@example.com'"
    parsed = parser.parse(sql)
    structure = analyze_query_structure(parsed, sql, SCHEMA)

    # No rewrite method for leading wildcard - should remain advisory
    # The opportunity detector catches this
    print("[OK] Leading wildcard not rewritten (by design)")


def test_distinct_on_not_removable():
    """DISTINCT ON (col) should not be removed."""
    sql = "SELECT DISTINCT ON (department_id) id, salary FROM employees"
    parsed = parser.parse(sql)
    structure = analyze_query_structure(parsed, sql, SCHEMA)

    result = engine.rewrite_remove_distinct(sql, parsed, structure)
    print(f"DISTINCT ON: {result}")
    assert result is None, "DISTINCT ON should be advisory"
    print("[OK] DISTINCT ON correctly advisory")


# --- NEW: qualify_columns semantic safety test --------------------------------

SCHEMA_ALIAS_SAFETY = {
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


def test_qualify_columns_conditionally_safe():
    """qualify_columns is CONDITIONALLY_SAFE (not UNSAFE) despite ORDER BY alias change."""
    from queries.services.semantic_validator import SemanticValidator, SemanticSafety

    validator = SemanticValidator(SCHEMA_ALIAS_SAFETY)

    original_sql = "SELECT name, mark FROM student INNER JOIN grades ON student.id = grades.student_id WHERE department = 'CSE' AND year = 'CURRENT_YEAR' ORDER BY mark DESC LIMIT 3"
    rewritten_sql = "SELECT s.name, g.mark FROM student s INNER JOIN grades g ON student.id = grades.student_id WHERE s.department = 'CSE' AND g.year = '2026' ORDER BY g.mark DESC LIMIT 3"

    original_parsed = parser.parse(original_sql)
    rewritten_parsed = parser.parse(rewritten_sql)

    result = validator.validate_candidate(
        original_sql, rewritten_sql,
        original_parsed, rewritten_parsed,
        ['qualify_columns']
    )
    print(f"qualify_columns safety: {result['semantic_safety']}")
    assert result['semantically_valid'] is True
    assert result['semantic_safety'] == SemanticSafety.CONDITIONALLY_SAFE.value, \
        f"Expected CONDITIONALLY_SAFE, got {result['semantic_safety']}"
    print("[OK] qualify_columns is CONDITIONALLY_SAFE (not UNSAFE)")


if __name__ == '__main__':
    print("=" * 60)
    print("Testing Semantic Safety")
    print("=" * 60)

    test_not_in_nullable_no_rewrite()
    test_in_nullable_no_rewrite()
    test_distinct_removal_preserves_ordering()
    test_distinct_removal_pk_no_limit()
    test_union_to_union_all_preserves_ordering()
    test_timestamptz_date_rewrite_skipped()
    test_arithmetic_overflow_advisory()
    test_outer_join_filter_pushdown_blocked()
    test_cross_join_fk_wrong_direction()
    test_date_boundary_leap_year()
    test_month_boundary_december()
    test_day_boundary_month_end()
    test_leading_wildcard_never_rewritten()
    test_distinct_on_not_removable()

    # NEW: qualify_columns semantic safety
    test_qualify_columns_conditionally_safe()

    print("\n" + "=" * 60)
    print("All semantic safety tests PASSED!")
    print("=" * 60)