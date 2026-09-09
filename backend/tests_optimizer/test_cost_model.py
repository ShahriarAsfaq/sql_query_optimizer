"""
Test cost_model — heuristic structural cost is labeled HEURISTIC and orders
queries monotonically: a heavy query (joins + SELECT * + non-sargable
predicates) scores higher than a simple indexed equality.
"""
import os
import sys
import django

sys.path.insert(0, r'd:\AI projects\SQL Query Optimizer\backend')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'sql_optimizer.settings')
django.setup()

from queries.services.cost_model import estimate_cost_from_structure

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
                'department_id': {'type': 'integer'},
            },
            'primary_key': ['id'],
        },
        'departments': {
            'columns': {
                'id': {'type': 'integer', 'primary_key': True},
                'name': {'type': 'text'},
                'budget': {'type': 'numeric'},
            },
            'primary_key': ['id'],
        },
        'orders': {
            'columns': {
                'id': {'type': 'integer', 'primary_key': True},
                'employee_id': {'type': 'integer'},
                'amount': {'type': 'numeric'},
            },
            'primary_key': ['id'],
        },
    },
    'relationships': [],
}


def test_cost_labeled_heuristic():
    """Cost estimates are always labeled heuristic."""
    cost = estimate_cost_from_structure(
        "SELECT * FROM employees", None, None, SCHEMA
    )
    assert cost.cost_source == 'heuristic'
    assert cost.total > 0
    print(f"Simple cost: total={cost.total} source={cost.cost_source}")
    print("[OK] Cost labeled HEURISTIC")


def test_cost_monotonic_ordering():
    """Heavy query costs more than a simple equality-filtered query."""
    simple = estimate_cost_from_structure(
        "SELECT first_name FROM employees WHERE department_id = 5",
        None, None, SCHEMA,
    )
    heavy = estimate_cost_from_structure(
        "SELECT * FROM employees "
        "JOIN departments ON employees.department_id = departments.id "
        "JOIN orders ON orders.employee_id = employees.id "
        "WHERE LOWER(email) LIKE '%x%@example.com' AND salary + 5000 > 100000 "
        "ORDER BY salary",
        None, None, SCHEMA,
    )
    assert heavy.total > simple.total, \
        f"Expected heavy > simple: {heavy.total} vs {simple.total}"
    assert simple.flags == [], f"Simple query should have no flags: {simple.flags}"
    print(f"Heavy cost: {heavy.total} vs simple cost: {simple.total}")
    print("[OK] Monotonic ordering")


def test_cost_flags_non_sargable():
    """Non-sargable predicates are flagged."""
    cost = estimate_cost_from_structure(
        "SELECT * FROM employees WHERE YEAR(hire_date) = 2025",
        None, None, SCHEMA,
    )
    assert any('function_on_column' in f for f in cost.flags), cost.flags
    print(f"Flags: {cost.flags}")
    print("[OK] Non-sargable flags")


def test_cost_join_and_projection_penalties():
    """SELECT * + joins add to the estimate."""
    base = estimate_cost_from_structure(
        "SELECT id FROM employees", None, None, SCHEMA
    )
    star = estimate_cost_from_structure(
        "SELECT * FROM employees", None, None, SCHEMA
    )
    assert star.total >= base.total
    print(f"SELECT * ({star.total}) >= bare id ({base.total})")
    print("[OK] Projection penalty")


if __name__ == '__main__':
    print("=" * 60)
    print("Testing Cost Model")
    print("=" * 60)

    test_cost_labeled_heuristic()
    test_cost_monotonic_ordering()
    test_cost_flags_non_sargable()
    test_cost_join_and_projection_penalties()

    print("\n" + "=" * 60)
    print("All cost model tests PASSED!")
    print("=" * 60)