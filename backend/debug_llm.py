import os
os.environ['DJANGO_SETTINGS_MODULE'] = 'sql_optimizer.settings'
os.environ['ALLOWED_HOSTS'] = 'localhost,127.0.0.1,testserver'
import django
django.setup()

from queries.services.llm_client import LLMClient
import json

llm_client = LLMClient(provider='gemini')

schema = {
    'tables': {
        'employees': {'columns': {'id': 'integer', 'name': 'text', 'salary': 'numeric', 'dept_id': 'integer'}},
        'departments': {'columns': {'id': 'integer', 'name': 'text'}}
    },
    'relationships': [
        {'from_table': 'employees', 'to_table': 'departments', 'from_column': 'dept_id', 'to_column': 'id', 'type': 'many_to_one'}
    ]
}

intent_text = 'Show me the top 5 highest paid employees with their department names'

schema_summary = ''
for table_name, table_def in schema.get('tables', {}).items():
    cols = list(table_def.get('columns', {}).keys())
    schema_summary += f'  {table_name}: {", ".join(cols)}\n'

prompt = f'''Extract structured intent from this natural language query.

Schema:
{schema_summary}

User request: "{intent_text}"

Return JSON with these fields:
- category: one of RETRIEVE, FILTER, SORT, AGGREGATE, GROUP, TOP_N, JOIN, TREND, RANKING, DUPLICATE_DETECTION, COMPARISON, UNKNOWN
- operation: SELECT, COUNT, AVG, SUM, MIN, MAX, etc.
- entity: main table name from schema
- metric: column to aggregate (if any)
- filters: list of {{"column", "operator", "value"}}
- time_range: {{"start", "end", "column"}} if date filtering
- group_by: list of columns
- order_by: list of {{"column", "direction"}} (ASC/DESC)
- limit: integer if top N
- ranking: RANK, DENSE_RANK, ROW_NUMBER if ranking
- comparison: {{"type", "entities"}} if comparison
- join_tables: list of tables to JOIN with entity
- select_columns: list of explicit columns user wants to see

Return ONLY valid JSON.'''

response = llm_client.complete(prompt, max_tokens=800, temperature=0.1)
print('Raw response:')
print(repr(response))
print()

# Test parsing
json_str = response
if '```json' in response:
    json_str = response.split('```json')[1].split('```')[0]
elif '```' in response:
    json_str = response.split('```')[1].split('```')[0]

print('Extracted JSON string:')
print(repr(json_str.strip()))
print()

try:
    intent_dict = json.loads(json_str.strip())
    print('Parsed successfully:')
    print(json.dumps(intent_dict, indent=2))
except json.JSONDecodeError as e:
    print(f'JSONDecodeError: {e}')
    print(f'Error at position {e.pos}: {json_str.strip()[max(0,e.pos-20):e.pos+20]}')