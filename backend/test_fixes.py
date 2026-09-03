import os
os.environ['DJANGO_SETTINGS_MODULE'] = 'sql_optimizer.settings'
import django
django.setup()

from queries.views import GenerateQueryView
from queries.services.intent import IntentService
from queries.services.optimizer import OptimizerService
from queries.services.sql_parser import get_parser
from queries.services.explanation import ExplanationService

view = GenerateQueryView()

print("=" * 60)
print("TEST 1: No schema provided (should use default)")
print("=" * 60)
intent_text = 'Show me all customers name'
schema = None
schema = view._get_default_schema()
print('Default schema tables:', list(schema.get('tables', {}).keys()))

intent_service = IntentService()
structured_intent = intent_service.extract_intent(intent_text, schema)
print('Intent:', structured_intent)

optimizer = OptimizerService(schema)
candidates = optimizer.generate_from_intent(structured_intent)
for c in candidates:
    print('SQL:', c.get('sql'))
print()

print("=" * 60)
print("TEST 2: String schema provided")
print("=" * 60)
intent_text2 = 'Show me all employees name'
schema_str = 'tables: employees(id, name, salary)'
parsed_schema = view._parse_schema_string(schema_str)
print('Parsed schema:', parsed_schema)

intent_service2 = IntentService()
structured_intent2 = intent_service2.extract_intent(intent_text2, parsed_schema)
print('Intent:', structured_intent2)

optimizer2 = OptimizerService(parsed_schema)
candidates2 = optimizer2.generate_from_intent(structured_intent2)
for c in candidates2:
    print('SQL:', c.get('sql'))
print()

print("=" * 60)
print("TEST 3: JOIN query with select columns (the original issue)")
print("=" * 60)
intent_text3 = 'Show customers with their orders. showing id, name, amount and status.'
schema3 = {
    'tables': {
        'customers': {'columns': {'id': 'int', 'name': 'text'}},
        'orders': {'columns': {'id': 'int', 'customer_id': 'int', 'amount': 'numeric', 'status': 'text'}}
    },
    'relationships': [{'from_table': 'customers', 'to_table': 'orders', 'from_column': 'id', 'to_column': 'customer_id', 'type': 'one_to_many'}]
}

intent_service3 = IntentService()
structured_intent3 = intent_service3.extract_intent(intent_text3, schema3)
print('Intent:', structured_intent3)

optimizer3 = OptimizerService(schema3)
candidates3 = optimizer3.generate_from_intent(structured_intent3)
for c in candidates3:
    print('SQL:', c.get('sql'))
print()

print("=" * 60)
print("TEST 4: Explain plan for LEFT JOIN with completed filter")
print("=" * 60)
sql = "SELECT c.id, c.name, o.id AS order_id, o.amount FROM customers c LEFT JOIN orders o ON o.customer_id = c.id AND o.status = 'completed' ORDER BY c.name;"
parser = get_parser()
parsed = parser.parse(sql)

explanation_service = ExplanationService(use_llm_intent=False)
explanation = explanation_service.explain_intent(parsed, None)
print('Explanation:')
print(explanation)
print()

print("=" * 60)
print("TEST 5: Explain plan for simple SELECT *")
print("=" * 60)
sql2 = 'SELECT * FROM employees;'
parsed2 = parser.parse(sql2)
explanation2 = explanation_service.explain_intent(parsed2, None)
print('Explanation:')
print(explanation2)
print()

print("=" * 60)
print("TEST 6: Explain plan for filter query")
print("=" * 60)
sql3 = 'SELECT * FROM employees WHERE salary > 50000;'
parsed3 = parser.parse(sql3)
explanation3 = explanation_service.explain_intent(parsed3, None)
print('Explanation:')
print(explanation3)