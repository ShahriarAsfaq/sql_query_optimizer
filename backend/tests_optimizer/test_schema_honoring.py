"""
Schema-honoring tests.

Verifies the directive: "in all features, if user provides the schema, then
follow that structure." Covers:

1. ``parse_schema_string`` preserves column types, primary keys, not-null flags,
   index uniqueness, and relationship types (instead of flattening everything to
   ``text`` / ``unique=False`` / ``one_to_many``).
2. The opportunity detector honors schema-declared indexes/PK (skips
   MISSING_INDEX when already covered) and gates IN_TO_EXISTS on NOT NULL.
3. The statistics analyzer restricts analysis to schema-declared tables.
4. The intent schema summary includes column types and constraints.
"""
import os
import sys
import django

sys.path.insert(0, r'd:\AI projects\SQL Query Optimizer\backend')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'sql_optimizer.settings')
django.setup()

from queries.views import parse_schema_string
from queries.services.opportunity_detector import detect_optimization_opportunities
from queries.services.query_analyzer import analyze_query_structure
from queries.services.statistics_analyzer import _tables_from_query
from queries.services.sql_parser import get_parser
from queries.services.intent import IntentService


def test_parse_schema_string_preserves_structure():
    schema_str = (
        "employees(id:integer primary_key, name:text not_null, salary:decimal, "
        "email:text unique) | "
        "departments(id:integer, name:text) | "
        "employees.department_id -> departments.id many_to_one | "
        "indexes: employees(salary unique)"
    )
    schema = parse_schema_string(schema_str)
    tables = schema['tables']
    assert 'employees' in tables and 'departments' in tables

    emp_cols = tables['employees']['columns']
    assert emp_cols['id'] == {'type': 'integer', 'primary_key': True}, emp_cols
    assert emp_cols['name'] == {'type': 'text', 'not_null': True}, emp_cols
    assert emp_cols['salary'] == 'decimal', emp_cols  # bare string when no flags
    assert emp_cols['email'] == {'type': 'text', 'unique': True}, emp_cols

    assert tables['employees']['primary_key'] == ['id']
    assert tables['employees']['indexes'] == [{'columns': ['salary'], 'unique': True}]
    assert schema['relationships'] == [{
        'from_table': 'employees', 'from_column': 'department_id',
        'to_table': 'departments', 'to_column': 'id', 'type': 'many_to_one',
    }]
    print("[OK] parse_schema_string preserves types/PK/index/relationship")


def test_parse_schema_string_backward_compat():
    # bare column lists -> all "text" (unchanged behavior)
    schema = parse_schema_string("employees(id, name, salary)")
    cols = schema['tables']['employees']['columns']
    assert cols == {'id': 'text', 'name': 'text', 'salary': 'text'}, cols
    print("[OK] parse_schema_string backward compatible (bare columns -> text)")


def test_opportunity_detector_skips_covered_index():
    schema = {
        'tables': {
            'employees': {
                'columns': {
                    'id': {'type': 'integer', 'primary_key': True},
                    'department_id': {'type': 'integer'},
                    'name': {'type': 'text'},
                },
                'primary_key': ['id'],
                'indexes': [{'columns': ['department_id'], 'unique': False}],
            }
        },
        'relationships': [],
    }
    parser = get_parser()
    sql = "SELECT name FROM employees WHERE department_id = 10"
    query = analyze_query_structure(parser.parse(sql), sql, schema)
    opps = detect_optimization_opportunities(query, None, schema)
    types = [o.type for o in opps]
    assert 'MISSING_INDEX' not in types, \
        f"Expected no MISSING_INDEX (covered by schema index), got {types}"
    print("[OK] opportunity detector skips MISSING_INDEX when schema index exists")


def test_opportunity_detector_emits_when_not_covered():
    schema = {
        'tables': {
            'employees': {
                'columns': {
                    'id': {'type': 'integer', 'primary_key': True},
                    'name': {'type': 'text'},
                    'salary': {'type': 'decimal'},
                },
                'primary_key': ['id'],
            }
        },
        'relationships': [],
    }
    parser = get_parser()
    # Unqualified column: the detector resolves 'salary' -> employees from schema.
    sql = "SELECT name FROM employees WHERE salary > 10000"
    query = analyze_query_structure(parser.parse(sql), sql, schema)
    opps = detect_optimization_opportunities(query, None, schema)
    types = [o.type for o in opps]
    assert 'MISSING_INDEX' in types, \
        f"Expected MISSING_INDEX for unindexed salary, got {types}"
    print("[OK] opportunity detector emits MISSING_INDEX when not covered")


def test_opportunity_detector_in_to_exists_null_gate():
    schema = {
        'tables': {
            'employees': {
                'columns': {
                    'id': {'type': 'integer', 'primary_key': True},
                    'department_id': {'type': 'integer', 'not_null': True},
                },
                'primary_key': ['id'],
            },
            'departments': {
                'columns': {'id': {'type': 'integer', 'primary_key': True}},
                'primary_key': ['id'],
            },
        },
        'relationships': [],
    }
    parser = get_parser()
    sql = "SELECT id FROM employees WHERE department_id IN (SELECT id FROM departments)"
    query = analyze_query_structure(parser.parse(sql), sql, schema)
    opps = [o for o in detect_optimization_opportunities(query, None, schema)
            if o.type == 'IN_TO_EXISTS']
    assert opps, "Expected an IN_TO_EXISTS opportunity"
    assert opps[0].confidence == 0.7, \
        f"Expected 0.7 confidence for NOT NULL column, got {opps[0].confidence}"
    print("[OK] IN_TO_EXISTS boosted when column is NOT NULL per schema")


def test_statistics_analyzer_restricts_to_schema():
    sql = "SELECT * FROM employees e JOIN departments d ON e.department_id = d.id"
    schema = {'tables': {'employees': {}}, 'relationships': []}
    tables = _tables_from_query(sql, None, schema)
    assert tables == ['employees'], f"Expected only employees (declared), got {tables}"

    tables2 = _tables_from_query(sql, None, None)
    assert set(tables2) == {'employees', 'departments'}, \
        f"Expected both tables without schema, got {tables2}"
    print("[OK] statistics analyzer restricts to schema-declared tables")


def test_intent_summarize_schema_includes_types():
    service = IntentService.__new__(IntentService)  # pure method; skip __init__
    schema = {
        'tables': {
            'employees': {
                'columns': {
                    'id': {'type': 'integer', 'primary_key': True},
                    'name': {'type': 'text', 'not_null': True},
                    'salary': 'decimal',
                }
            }
        },
        'relationships': [],
    }
    summary = service._summarize_schema(schema)
    assert 'id:integer' in summary and 'primary_key' in summary, summary
    assert 'name:text' in summary and 'not_null' in summary, summary
    assert 'salary:decimal' in summary, summary
    print("[OK] intent schema summary includes types/constraints")


if __name__ == '__main__':
    print("=" * 60)
    print("Testing Schema Honoring")
    print("=" * 60)

    test_parse_schema_string_preserves_structure()
    test_parse_schema_string_backward_compat()
    test_opportunity_detector_skips_covered_index()
    test_opportunity_detector_emits_when_not_covered()
    test_opportunity_detector_in_to_exists_null_gate()
    test_statistics_analyzer_restricts_to_schema()
    test_intent_summarize_schema_includes_types()

    print("\n" + "=" * 60)
    print("All schema honoring tests PASSED!")
    print("=" * 60)
