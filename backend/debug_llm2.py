import os
os.environ['DJANGO_SETTINGS_MODULE'] = 'sql_optimizer.settings'
os.environ['ALLOWED_HOSTS'] = 'localhost,127.0.0.1,testserver'
import django
django.setup()

from queries.services.llm_client import LLMClient

llm_client = LLMClient(provider='gemini')

# Test with a simpler prompt
prompt = 'Return JSON: {"category": "TOP_N", "entity": "employees", "metric": "salary", "limit": 5}'

try:
    response = llm_client.complete(prompt, max_tokens=500, temperature=0.1)
    print('Response:', response)
except Exception as e:
    print('Error:', e)