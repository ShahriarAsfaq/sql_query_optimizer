"""
Test rewrite engine correctness for all new rewrite rules.
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

# Test schema with date/timestamp columns and NOT NULL constraints
SCHEMA = {
    'tables': {
        'employees': {
            'columns': {
                'id': {'type': 'integer', 'primary_key': True},
                'first_name': {'type': 'text'},
                'last_name': {'type': 'text'},
                'email': {'type': 'text'},
                'hire_date': {'type': 'date'},
                'joining_timestamp': {'type': 'timestamp'},
                'created_at': {'type': 'timestamptz'},
                'salary': {'type': 'numeric'},
                'department_id': {'type': 'integer', 'not_null': True},
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


def test_rewrite_year_function():
    """Test YEAR(col)=2025 -> half-open range rewrite."""
    sql = "SELECT * FROM employees WHERE YEAR(hire_date) = 2025"
    parsed = parser.parse(sql)
    structure = analyze_query_structure(parsed, sql, SCHEMA)

    result = engine.rewrite_date_function(sql, parsed, structure)
    print(f"YEAR rewrite: {result}")
    assert result is not None, "YEAR rewrite should produce candidate"
    assert "hire_date >= '2025-01-01'" in result
    assert "hire_date < '2026-01-01'" in result
    print("[OK] YEAR function rewrite works")


def test_rewrite_month_function():
    """Test MONTH(col)=5 -> half-open range (requires year context)."""
    sql = "SELECT * FROM employees WHERE MONTH(hire_date) = 5"
    parsed = parser.parse(sql)
    structure = analyze_query_structure(parsed, sql, SCHEMA)

    result = engine.rewrite_date_function(sql, parsed, structure)
    print(f"MONTH rewrite: {result}")
    # Should be advisory (None) since year context missing
    assert result is None, "MONTH without year should be advisory only"
    print("[OK] MONTH without year is advisory")


def test_rewrite_month_with_year():
    """Test MONTH(col)=5 with year in WHERE -> half-open range."""
    sql = "SELECT * FROM employees WHERE YEAR(hire_date) = 2025 AND MONTH(hire_date) = 5"
    parsed = parser.parse(sql)
    structure = analyze_query_structure(parsed, sql, SCHEMA)

    result = engine.rewrite_date_function(sql, parsed, structure)
    print(f"MONTH with year rewrite: {result}")
    assert result is not None, "MONTH with year should produce candidate"
    assert "hire_date >= '2025-05-01'" in result
    assert "hire_date < '2025-06-01'" in result
    print("[OK] MONTH with year rewrite works")


def test_rewrite_day_function():
    """Test DAY(col)=15 -> half-open range."""
    sql = "SELECT * FROM employees WHERE YEAR(hire_date) = 2025 AND MONTH(hire_date) = 5 AND DAY(hire_date) = 15"
    parsed = parser.parse(sql)
    structure = analyze_query_structure(parsed, sql, SCHEMA)

    result = engine.rewrite_date_function(sql, parsed, structure)
    print(f"DAY rewrite: {result}")
    assert result is not None, "DAY with year/month should produce candidate"
    assert "hire_date >= '2025-05-15'" in result
    assert "hire_date < '2025-05-16'" in result
    print("[OK] DAY function rewrite works")


def test_rewrite_date_function():
    """Test DATE(timestamp_col) = '2025-01-15' -> half-open range."""
    sql = "SELECT * FROM employees WHERE DATE(joining_timestamp) = '2025-01-15'"
    parsed = parser.parse(sql)
    structure = analyze_query_structure(parsed, sql, SCHEMA)

    result = engine.rewrite_date_function(sql, parsed, structure)
    print(f"DATE rewrite: {result}")
    assert result is not None, "DATE function should produce candidate"
    assert "joining_timestamp >= '2025-01-15'" in result
    assert "joining_timestamp < '2025-01-16'" in result
    print("[OK] DATE function rewrite works")


def test_timestamptz_skipped():
    """Test timestamptz columns are skipped (timezone-sensitive)."""
    sql = "SELECT * FROM employees WHERE YEAR(created_at) = 2025"
    parsed = parser.parse(sql)
    structure = analyze_query_structure(parsed, sql, SCHEMA)

    result = engine.rewrite_date_function(sql, parsed, structure)
    print(f"TIMESTAMPTZ rewrite: {result}")
    assert result is None, "timestamptz should be skipped (advisory)"
    print("[OK] timestamptz correctly skipped")


def test_rewrite_arithmetic_addition():
    """Test col + 5000 = 100000 -> col = 95000."""
    sql = "SELECT * FROM employees WHERE salary + 5000 = 100000"
    parsed = parser.parse(sql)
    structure = analyze_query_structure(parsed, sql, SCHEMA)

    result = engine.rewrite_arithmetic_on_column(sql, parsed, structure)
    print(f"Arithmetic + rewrite: {result}")
    assert result is not None, "Addition rewrite should produce candidate"
    assert "salary = 95000" in result
    print("[OK] Arithmetic + rewrite works")


def test_rewrite_arithmetic_subtraction():
    """Test col - 3000 > 50000 -> col > 53000."""
    sql = "SELECT * FROM employees WHERE salary - 3000 > 50000"
    parsed = parser.parse(sql)
    structure = analyze_query_structure(parsed, sql, SCHEMA)

    result = engine.rewrite_arithmetic_on_column(sql, parsed, structure)
    print(f"Arithmetic - rewrite: {result}")
    assert result is not None, "Subtraction rewrite should produce candidate"
    assert "salary > 53000" in result
    print("[OK] Arithmetic - rewrite works")


def test_rewrite_arithmetic_multiplication_advisory():
    """Test multiplication/division are advisory only."""
    sql = "SELECT * FROM employees WHERE salary * 2 > 100000"
    parsed = parser.parse(sql)
    structure = analyze_query_structure(parsed, sql, SCHEMA)

    result = engine.rewrite_arithmetic_on_column(sql, parsed, structure)
    print(f"Arithmetic * rewrite: {result}")
    assert result is None, "Multiplication should be advisory only"
    print("[OK] Multiplication correctly advisory")


def test_rewrite_in_to_exists():
    """Test IN -> EXISTS with NOT NULL gate."""
    sql = "SELECT * FROM employees WHERE department_id IN (SELECT id FROM departments)"
    parsed = parser.parse(sql)
    structure = analyze_query_structure(parsed, sql, SCHEMA)

    result = engine.rewrite_in_to_exists(sql, parsed, structure)
    print(f"IN -> EXISTS rewrite: {result}")
    assert result is not None, "IN -> EXISTS should produce candidate (FK is NOT NULL)"
    assert "EXISTS" in result
    print("[OK] IN -> EXISTS rewrite works")


def test_rewrite_in_to_exists_nullable():
    """Test IN -> EXISTS skipped when column nullable."""
    schema_nullable = {
        'tables': {
            'employees': {
                'columns': {
                    'id': {'type': 'integer', 'primary_key': True},
                    'department_id': {'type': 'integer', 'nullable': True},
                },
                'primary_key': ['id'],
            },
            'departments': {
                'columns': {
                    'id': {'type': 'integer', 'primary_key': True},
                },
                'primary_key': ['id'],
            }
        },
        'relationships': []
    }
    engine_nullable = RewriteEngine(schema_nullable)
    sql = "SELECT * FROM employees WHERE department_id IN (SELECT id FROM departments)"
    parsed = parser.parse(sql)
    structure = analyze_query_structure(parsed, sql, schema_nullable)

    result = engine_nullable.rewrite_in_to_exists(sql, parsed, structure)
    print(f"IN -> EXISTS nullable: {result}")
    assert result is None, "IN -> EXISTS should be advisory when nullable"
    print("[OK] IN -> EXISTS correctly advisory for nullable")


def test_rewrite_remove_distinct_pk():
    """Test DISTINCT removal when PK projected."""
    sql = "SELECT DISTINCT id, first_name FROM employees"
    parsed = parser.parse(sql)
    structure = analyze_query_structure(parsed, sql, SCHEMA)

    result = engine.rewrite_remove_distinct(sql, parsed, structure)
    print(f"DISTINCT removal: {result}")
    assert result is not None, "DISTINCT removal should produce candidate (PK present)"
    assert "DISTINCT" not in result
    print("[OK] DISTINCT removal works for PK")


def test_rewrite_remove_distinct_not_pk():
    """Test DISTINCT removal advisory when not PK."""
    sql = "SELECT DISTINCT department_id FROM employees"
    parsed = parser.parse(sql)
    structure = analyze_query_structure(parsed, sql, SCHEMA)

    result = engine.rewrite_remove_distinct(sql, parsed, structure)
    print(f"DISTINCT removal (not PK): {result}")
    assert result is None, "DISTINCT removal should be advisory (not PK)"
    print("[OK] DISTINCT removal correctly advisory for non-PK")


def test_rewrite_union_to_union_all():
    """Test UNION -> UNION ALL when branches are provably disjoint."""
    sql = "SELECT id FROM employees WHERE department_id = 1 UNION SELECT id FROM employees WHERE department_id = 2"
    parsed = parser.parse(sql)
    structure = analyze_query_structure(parsed, sql, SCHEMA)

    result = engine.rewrite_union_to_union_all(sql, parsed, structure)
    print(f"UNION -> UNION ALL: {result}")
    assert result is not None, "UNION -> UNION ALL should produce candidate (disjoint branches)"
    assert "UNION ALL" in result
    print("[OK] UNION -> UNION ALL works for disjoint branches")


def test_rewrite_union_to_union_all_advisory():
    """Test UNION -> UNION ALL is advisory when branches NOT disjoint."""
    sql = "SELECT id FROM employees WHERE department_id = 1 UNION SELECT id FROM employees WHERE department_id = 1"
    parsed = parser.parse(sql)
    structure = analyze_query_structure(parsed, sql, SCHEMA)

    result = engine.rewrite_union_to_union_all(sql, parsed, structure)
    print(f"UNION -> UNION ALL (non-disjoint): {result}")
    assert result is None, "UNION -> UNION ALL should be advisory (non-disjoint)"
    print("[OK] UNION -> UNION ALL correctly advisory for non-disjoint")


def test_rewrite_cross_join_fk():
    """Test CROSS JOIN -> INNER JOIN when FK exists."""
    sql = "SELECT * FROM employees CROSS JOIN departments"
    parsed = parser.parse(sql)
    structure = analyze_query_structure(parsed, sql, SCHEMA)

    result = engine.rewrite_cross_join(sql, parsed, structure)
    print(f"CROSS JOIN rewrite: {result}")
    assert result is not None, "CROSS JOIN -> INNER JOIN should produce candidate (FK exists)"
    assert "JOIN" in result and "ON" in result
    # Either column order is fine
    assert ("employees.department_id = departments.id" in result or
            "departments.id = employees.department_id" in result)
    print("[OK] CROSS JOIN -> INNER JOIN works")


def test_rewrite_cross_join_no_fk():
    """Test CROSS JOIN advisory when no FK."""
    schema_no_fk = {
        'tables': {
            'employees': {
                'columns': {'id': {'type': 'integer', 'primary_key': True}},
                'primary_key': ['id'],
            },
            'departments': {
                'columns': {'id': {'type': 'integer', 'primary_key': True}},
                'primary_key': ['id'],
            }
        },
        'relationships': []
    }
    engine_no_fk = RewriteEngine(schema_no_fk)
    sql = "SELECT * FROM employees CROSS JOIN departments"
    parsed = parser.parse(sql)
    structure = analyze_query_structure(parsed, sql, schema_no_fk)

    result = engine_no_fk.rewrite_cross_join(sql, parsed, structure)
    print(f"CROSS JOIN no FK: {result}")
    assert result is None, "CROSS JOIN should be advisory without FK"
    print("[OK] CROSS JOIN correctly advisory without FK")


def test_rewrite_filter_before_join():
    """Test filter pushdown for INNER JOIN."""
    sql = "SELECT * FROM employees INNER JOIN departments ON employees.department_id = departments.id WHERE departments.budget > 100000"
    parsed = parser.parse(sql)
    structure = analyze_query_structure(parsed, sql, SCHEMA)

    result = engine.rewrite_filter_before_join(sql, parsed, structure)
    print(f"Filter pushdown: {result}")
    assert result is not None, "Filter pushdown should produce candidate"
    # Should have derived table with filter pushed down
    assert "departments.budget > 100000" in result
    print("[OK] Filter pushdown works")


if __name__ == '__main__':
    print("=" * 60)
    print("Testing Rewrite Engine Correctness")
    print("=" * 60)

    test_rewrite_year_function()
    test_rewrite_month_function()
    test_rewrite_month_with_year()
    test_rewrite_day_function()
    test_rewrite_date_function()
    test_timestamptz_skipped()
    test_rewrite_arithmetic_addition()
    test_rewrite_arithmetic_subtraction()
    test_rewrite_arithmetic_multiplication_advisory()
    test_rewrite_in_to_exists()
    test_rewrite_in_to_exists_nullable()
    test_rewrite_remove_distinct_pk()
    test_rewrite_remove_distinct_not_pk()
    test_rewrite_union_to_union_all()
    test_rewrite_union_to_union_all_advisory()
    test_rewrite_cross_join_fk()
    test_rewrite_cross_join_no_fk()
    test_rewrite_filter_before_join()

    print("\n" + "=" * 60)
    print("All rewrite correctness tests PASSED!")
    print("=" * 60)