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

text = "Show top 5 departments by average salary"
text_lower = text.lower()

print("=== Debugging ===")
print(f"Text: {text}")

# Check category
from queries.services.intent import IntentCategory
category = IntentCategory.TOP_N
print(f"Category: {category}")

# Check by_agg_patterns
by_agg_patterns = [
    (r'by\s+(?:average|avg|mean)\s+(\w+)', 'AVG'),
    (r'by\s+(?:sum|total)\s+(\w+)', 'SUM'),
    (r'by\s+(?:count)\s+(\w+)', 'COUNT'),
    (r'by\s+(?:minimum|min)\s+(\w+)', 'MIN'),
    (r'by\s+(?:maximum|max)\s+(\w+)', 'MAX'),
]

import re
for pattern, op in by_agg_patterns:
    match = re.search(pattern, text_lower)
    if match:
        potential_metric = match.group(1)
        print(f"Pattern matched: {pattern}, op: {op}, potential_metric: {potential_metric}")

        # Search for entity
        search_entity = None
        if schema.get('tables'):
            for table_name, table_def in schema.get('tables', {}).items():
                cols = list(table_def.get('columns', {}).keys())
                for col in cols:
                    if col.lower() == potential_metric.lower():
                        search_entity = table_name
                        break
                if search_entity:
                    break
        print(f"  search_entity: {search_entity}")

        if search_entity and search_entity in schema.get('tables', {}):
            cols = list(schema['tables'][search_entity].get('columns', {}).keys())
            for col in cols:
                if col.lower() == potential_metric.lower():
                    print(f"  Found metric: {col}")
                    print(f"  Would set operation={op}, entity={search_entity}")

# Also check agg_metric_patterns
agg_metric_patterns = [
    r'(?:average|avg|mean|sum|total|count|minimum|min|maximum|max)\s+(?:of\s+)?(\w+)',
    r'(?:average|avg|mean|sum|total|count|minimum|min|maximum|max)\s+(\w+)',
]

print("\n--- agg_metric_patterns ---")
for pattern in agg_metric_patterns:
    match = re.search(pattern, text_lower)
    if match:
        potential_metric = match.group(1)
        print(f"Pattern matched: {pattern}, potential_metric: {potential_metric}")