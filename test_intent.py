import sys
sys.path.insert(0, r'd:\AI projects\SQL Query Optimizer\backend')

from queries.services.intent import IntentService

intent_service = IntentService()
schema = {
    'tables': {
        'employees': {'columns': {'id': 'integer', 'name': 'text', 'department': 'text', 'salary': 'integer'}},
        'departments': {'columns': {'id': 'integer', 'name': 'text'}}
    }
}

# Test cases
test_cases = [
    "Show top 5 departments by average salary",
    "Show me the top 5 highest paid employees",
    "List all employees with salary greater than 50000",
    "Show average salary by department",
    "Find customers who made purchases in the last 30 days",
]

for text in test_cases:
    intent = intent_service.extract_intent(text, schema)
    print(f"\nInput: {text}")
    print(f"Category: {intent.get('category')}")
    print(f"Operation: {intent.get('operation')}")
    print(f"Entity: {intent.get('entity')}")
    print(f"Metric: {intent.get('metric')}")
    print(f"Group by: {intent.get('group_by')}")
    print(f"Order by: {intent.get('order_by')}")
    print(f"Limit: {intent.get('limit')}")
    print(f"Filters: {intent.get('filters')}")