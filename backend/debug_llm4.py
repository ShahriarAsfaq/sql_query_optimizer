import os
os.environ['DJANGO_SETTINGS_MODULE'] = 'sql_optimizer.settings'
os.environ['ALLOWED_HOSTS'] = 'localhost,127.0.0.1,testserver'
import django
django.setup()

from queries.services.llm_client import LLMClient

llm_client = LLMClient(provider='gemini')

# Test with a slightly longer prompt
prompt = """Extract intent from NL query. Return ONLY valid JSON.

Schema:
  employees: id, name, salary, dept_id
  departments: id, name

Request: "Show me the top 5 highest paid employees with their department names"

Fields: category (RETRIEVE/FILTER/SORT/AGGREGATE/GROUP/TOP_N/JOIN/TREND/RANKING/DUPLICATE_DETECTION/COMPARISON/UNKNOWN), operation (SELECT/COUNT/AVG/SUM/MIN/MAX), entity (main table), metric (column to aggregate), filters (list of {"column","operator","value"}), time_range, group_by, order_by (list of {"column","direction"}), limit, ranking, comparison, join_tables, select_columns.

JSON:"""

try:
    response = llm_client.complete(prompt, max_tokens=2000, temperature=0.1)
    print('Response length:', len(response))
    print('Response:', repr(response))
except Exception as e:
    print('Error:', e)