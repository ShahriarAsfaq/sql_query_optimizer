import os
os.environ['DJANGO_SETTINGS_MODULE'] = 'sql_optimizer.settings'
import django
django.setup()

from queries.views import GenerateQueryView
from queries.services.llm_client import LLMClient

# Test LLM client
llm = LLMClient(provider='gemini')
print('LLM available:', llm.is_available())
print('Provider:', llm.provider)
print('Model:', llm.model)
print('API Key present:', bool(llm.api_key))

# Test view
view = GenerateQueryView()
print('View created successfully')