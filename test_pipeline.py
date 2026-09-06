#!/usr/bin/env python
"""
Test script for the new optimization pipeline.
"""
import sys
import os

# Add backend to path
sys.path.insert(0, r'd:\AI projects\SQL Query Optimizer\backend')

# Set Django settings
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'sql_optimizer.settings')

import django
django.setup()

from queries.services.optimizer import OptimizerService, CandidateQuery
from queries.services.plan_analyzer import create_plan_analyzer, PlanAnalysis
from queries.services.semantic_validator import SemanticValidator, SemanticSafety
from queries.services.sql_parser import get_parser

# Test schema
schema = {
    'tables': {
        'employees': {
            'columns': {
                'id': 'integer',
                'name': 'text',
                'department_id': 'integer',
                'salary': 'integer',
                'hire_date': 'date'
            },
            'primary_key': ['id']
        },
        'departments': {
            'columns': {
                'id': 'integer',
                'name': 'text',
                'budget': 'integer'
            },
            'primary_key': ['id']
        }
    },
    'relationships': [
        {'from_table': 'employees', 'from_column': 'department_id', 'to_table': 'departments', 'to_column': 'id'}
    ]
}

# Test queries
test_queries = [
    # Simple query with SELECT *
    "SELECT * FROM employees WHERE salary > 50000",

    # Query with non-sargable predicate
    "SELECT name, salary FROM employees WHERE LOWER(name) = 'john'",

    # Query with IN subquery
    "SELECT name FROM employees WHERE department_id IN (SELECT id FROM departments WHERE name = 'Engineering')",

    # Join query
    "SELECT e.name, d.name FROM employees e JOIN departments d ON e.department_id = d.id WHERE e.salary > 50000",

    # Correlated subquery (if supported)
    "SELECT e.name FROM employees e WHERE e.salary > (SELECT AVG(salary) FROM employees WHERE department_id = e.department_id)",
]

def test_plan_analyzer():
    """Test the plan analyzer with mock EXPLAIN output."""
    print("=" * 60)
    print("Testing Plan Analyzer")
    print("=" * 60)

    analyzer = create_plan_analyzer()

    # Mock EXPLAIN JSON (simplified)
    mock_explain = [{
        "Plan": {
            "Node Type": "Seq Scan",
            "Startup Cost": 0.00,
            "Total Cost": 100.00,
            "Plan Rows": 1000,
            "Plan Width": 50,
            "Relation Name": "employees",
            "Alias": "e",
            "Filter": "(salary > 50000)",
            "Rows Removed by Filter": 500,
            "Actual Rows": 500,
            "Actual Total Time": 10.5,
            "Actual Startup Time": 0.1,
            "Actual Loops": 1
        }
    }]

    analysis = analyzer.analyze(mock_explain)
    print(f"Total Cost: {analysis.total_cost}")
    print(f"Seq Scans: {analysis.seq_scans}")
    print(f"Tables Scanned: {analysis.tables_scanned}")
    print(f"Scan Efficiency: {analysis.index_scan_tables} index / {analysis.seq_scan_tables} seq")
    print()

def test_semantic_validator():
    """Test the semantic validator."""
    print("=" * 60)
    print("Testing Semantic Validator")
    print("=" * 60)

    validator = SemanticValidator(schema)
    parser = get_parser()

    original = "SELECT name FROM employees WHERE department_id IN (SELECT id FROM departments WHERE name = 'Engineering')"
    rewritten = "SELECT e.name FROM employees e JOIN departments d ON e.department_id = d.id WHERE d.name = 'Engineering'"

    orig_parsed = parser.parse(original)
    rew_parsed = parser.parse(rewritten)

    result = validator.validate_candidate(
        original, rewritten,
        orig_parsed, rew_parsed,
        ['rewrite_in_subquery']
    )

    print(f"Semantically Valid: {result['semantically_valid']}")
    print(f"Safety: {result['semantic_safety']}")
    print(f"Details: {result['safety_details']}")
    print()

def test_optimizer_pipeline():
    """Test the full optimizer pipeline."""
    print("=" * 60)
    print("Testing Full Optimizer Pipeline")
    print("=" * 60)

    optimizer = OptimizerService(schema=schema)

    for sql in test_queries:
        print(f"\n--- Testing: {sql} ---")
        try:
            result = optimizer.optimize(sql)
            print(f"Original Cost: {result.get('original_cost')}")
            print(f"Best Candidate: {result.get('best_candidate', {}).get('description', 'None')}")
            print(f"Best Cost: {result.get('best_candidate', {}).get('cost')}")
            print(f"Best Performance Score: {result.get('best_candidate', {}).get('performance_score')}")
            print(f"Best Confidence: {result.get('best_candidate', {}).get('confidence')}")
            print(f"Best Semantic Safety: {result.get('best_candidate', {}).get('semantic_safety')}")
            print(f"Number of Candidates: {len(result.get('candidates', []))}")

            # Print candidate details
            for i, c in enumerate(result.get('candidates', [])[:3]):
                print(f"  Candidate {i+1}: {c.get('description')}")
                print(f"    Cost: {c.get('cost')}")
                print(f"    Perf Score: {c.get('performance_score')}")
                print(f"    Safety: {c.get('semantic_safety')}")
                print(f"    Reasons: {c.get('optimization_reasons', [])[:2]}")
                print(f"    Rules: {c.get('rewrite_rules_applied', [])}")

        except Exception as e:
            print(f"  ERROR: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    test_plan_analyzer()
    test_semantic_validator()
    test_optimizer_pipeline()
    print("\n" + "=" * 60)
    print("All tests completed!")
    print("=" * 60)