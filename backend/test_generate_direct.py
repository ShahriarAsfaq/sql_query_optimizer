import os
os.environ['DJANGO_SETTINGS_MODULE'] = 'sql_optimizer.settings'
os.environ['ALLOWED_HOSTS'] = 'localhost,127.0.0.1,testserver'
import django
django.setup()

from queries.views import GenerateQueryView
from rest_framework.test import APIClient

# Test with a realistic intent
view = GenerateQueryView()

# Test 1: Simple generate without schema
print("Test 1: Generate without schema")
request_data = {
    'intent_text': 'Show me the top 10 highest paid employees',
}

client = APIClient()
client.credentials(HTTP_AUTHORIZATION='ApiKey dev-key-12345')

try:
    response = client.post('/api/queries/generate/', request_data, format='json')
    print('Status:', response.status_code)
    print('Response:', response.data)
except Exception as e:
    print('Error:', e)
    import traceback
    traceback.print_exc()