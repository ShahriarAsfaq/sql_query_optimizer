import os
os.environ['DJANGO_SETTINGS_MODULE'] = 'sql_optimizer.settings'
os.environ['ALLOWED_HOSTS'] = 'localhost,127.0.0.1,testserver'
import django
django.setup()

from queries.services.llm_client import LLMClient

llm_client = LLMClient(provider='gemini')

# Test with a very simple prompt
prompt = 'Return only JSON: {"category": "TOP_N", "operation": "SELECT", "entity": "employees", "metric": "salary", "limit": 5, "join_tables": ["departments"], "select_columns": ["employees.name", "employees.salary", "departments.name"]}'

try:
    response = llm_client.complete(prompt, max_tokens=2000, temperature=0.1)
    print('Response length:', len(response))
    print('Response:', repr(response[:500]))
except Exception as e:
    print('Error:', e)