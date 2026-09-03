import sys
sys.path.insert(0, r'd:\AI projects\SQL Query Optimizer\backend')

from queries.services.intent import IntentService
from queries.services.optimizer import OptimizerService

intent_service = IntentService()
optimizer = OptimizerService()

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
]

for text in test_cases:
    intent = intent_service.extract_intent(text, schema)
    print(f"\n=== Input: {text} ===")
    print(f"Intent: {intent}")

    candidates = optimizer.generate_from_intent(intent)
    for i, c in enumerate(candidates):
        print(f"\nCandidate #{i+1}: {c['description']}")
        print(f"  SQL: {c['sql']}")
        print(f"  Valid: {c['validation_passed']}")
        if c['validation_errors']:
            print(f"  Errors: {c['validation_errors']}")