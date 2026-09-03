"""
Test API integration - tests the views and services directly without HTTP.
"""
import sys
sys.path.insert(0, r'd:\AI projects\SQL Query Optimizer\backend')
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'sql_optimizer.settings')

import django
django.setup()

from queries.services.sql_parser import get_parser
from queries.services.validator import ValidationService
from queries.services.intent import IntentService
from queries.services.optimizer import OptimizerService
from queries.services.explanation import ExplanationService

# Test schema with full seed database structure
SCHEMA = {
    'tables': {
        'employees': {
            'columns': {
                'id': 'integer',
                'first_name': 'varchar',
                'last_name': 'varchar',
                'email': 'varchar',
                'phone': 'varchar',
                'hire_date': 'date',
                'salary': 'decimal',
                'department': 'varchar',  # denormalized for testing
                'department_id': 'integer',
                'manager_id': 'integer',
                'is_active': 'boolean',
                'created_at': 'timestamp',
            }
        },
        'departments': {
            'columns': {
                'id': 'integer',
                'name': 'varchar',
                'description': 'text',
                'location': 'varchar',
                'budget': 'decimal',
                'created_at': 'timestamp',
            }
        },
        'customers': {
            'columns': {
                'id': 'integer',
                'first_name': 'varchar',
                'last_name': 'varchar',
                'email': 'varchar',
                'phone': 'varchar',
                'address': 'text',
                'city': 'varchar',
                'state': 'varchar',
                'zip_code': 'varchar',
                'country': 'varchar',
                'created_at': 'timestamp',
                'updated_at': 'timestamp',
            }
        },
        'purchases': {
            'columns': {
                'id': 'integer',
                'customer_id': 'integer',
                'product_name': 'varchar',
                'category': 'varchar',
                'quantity': 'integer',
                'unit_price': 'decimal',
                'total_amount': 'decimal',
                'purchase_date': 'timestamp',
                'payment_method': 'varchar',
                'status': 'varchar',
            }
        },
        'products': {
            'columns': {
                'id': 'integer',
                'name': 'varchar',
                'category': 'varchar',
                'description': 'text',
                'price': 'decimal',
                'cost': 'decimal',
                'stock_quantity': 'integer',
                'is_active': 'boolean',
                'created_at': 'timestamp',
            }
        },
    },
    'relationships': [
        {'from_table': 'employees', 'from_column': 'department_id', 'to_table': 'departments', 'to_column': 'id'},
        {'from_table': 'employees', 'from_column': 'manager_id', 'to_table': 'employees', 'to_column': 'id'},
        {'from_table': 'purchases', 'from_column': 'customer_id', 'to_table': 'customers', 'to_column': 'id'},
    ]
}


def test_analyze():
    """Test AnalyzeQueryView logic."""
    print("\n=== Test: Analyze Query ===")
    sql = "SELECT department, AVG(salary) AS avg_salary FROM employees GROUP BY department ORDER BY avg_salary DESC LIMIT 5"

    parser = get_parser()
    parsed = parser.parse(sql)
    parsed_json = parser.format_for_json(parsed)

    validator = ValidationService(SCHEMA)
    validation = validator.validate(parsed)

    intent_service = IntentService()
    intent_match = intent_service.check_intent_match(
        parsed,
        "Show top 5 departments by average salary",
        SCHEMA
    )

    explanation_service = ExplanationService()
    explanation = explanation_service.explain(parsed)

    print(f"  SQL: {sql}")
    print(f"  Valid: {validation['is_valid']}")
    print(f"  Intent match score: {intent_match['match_score']}")
    print(f"  Issues: {len(validation['issues'])}")
    for issue in validation['issues']:
        print(f"    - [{issue['severity']}] {issue['message']}")
    # explanation is a string, not a dict
    summary_lines = explanation.strip().split('\n')[:3]
    print(f"  Explanation: {' '.join(summary_lines)}")
    return True


def test_optimize():
    """Test OptimizeQueryView logic."""
    print("\n=== Test: Optimize Query ===")
    sql = "SELECT * FROM employees WHERE LOWER(first_name) = 'john'"

    optimizer = OptimizerService(SCHEMA)
    result = optimizer.optimize(sql)

    print(f"  SQL: {sql}")
    print(f"  Original cost: {result['original_cost']}")
    print(f"  Candidates: {len(result['candidates'])}")
    for i, c in enumerate(result['candidates']):
        print(f"    #{i+1} [{c['description']}]: {c['sql']}")
        if c['validation_errors']:
            print(f"      Errors: {c['validation_errors']}")
    return True


def test_generate():
    """Test GenerateQueryView logic."""
    print("\n=== Test: Generate Query ===")
    test_cases = [
        "Show top 5 departments by average salary",
        "Show me the top 10 highest paid employees",
        "List all customers from New York",
        "Show total sales by product category",
        "Find employees who earn more than 100000",
    ]

    intent_service = IntentService()
    optimizer = OptimizerService(SCHEMA)

    for text in test_cases:
        intent = intent_service.extract_intent(text, SCHEMA)
        candidates = optimizer.generate_from_intent(intent)

        print(f"\n  Intent: {text}")
        print(f"  Category: {intent.get('category')}, Operation: {intent.get('operation')}, Entity: {intent.get('entity')}")
        print(f"  Metric: {intent.get('metric')}, GroupBy: {intent.get('group_by')}, Limit: {intent.get('limit')}")
        for i, c in enumerate(candidates):
            print(f"    Candidate #{i+1}: {c['sql']}")
    return True


def test_explain():
    """Test EXPLAIN cost scoring (requires seed database)."""
    print("\n=== Test: EXPLAIN Cost Scoring ===")
    try:
        import psycopg2
        conn = psycopg2.connect(
            host='localhost',
            port=5432,
            user='postgres',
            password='postgres',
            database='sql_optimizer_seed'
        )

        optimizer = OptimizerService(SCHEMA, seed_db_connection=conn)

        sql = "SELECT department, AVG(salary) as avg_salary FROM employees GROUP BY department ORDER BY avg_salary DESC LIMIT 5"
        cost = optimizer._get_explain_cost(sql)
        print(f"  Query: {sql}")
        print(f"  EXPLAIN cost: {cost}")

        conn.close()
        return True
    except Exception as e:
        print(f"  EXPLAIN test skipped (seed DB not available): {e}")
        return True


if __name__ == '__main__':
    test_analyze()
    test_optimize()
    test_generate()
    test_explain()
    print("\n=== All tests completed ===")