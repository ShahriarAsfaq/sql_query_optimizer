import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'sql_optimizer.settings')
import django
django.setup()

from queries.services.llm_client import LLMClient
llm = LLMClient(provider='gemini')
print('LLM available:', llm.is_available())
print('Provider:', llm.provider)
print('Model:', llm.model)
print('API Key present:', bool(llm.api_key))
if llm.api_key:
    print('API Key starts with:', llm.api_key[:10])