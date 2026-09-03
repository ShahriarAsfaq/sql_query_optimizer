import sys
sys.path.insert(0, r'd:\AI projects\SQL Query Optimizer\backend')
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'sql_optimizer.settings')
import django
django.setup()

from queries.services.intent import IntentService
from queries.services.optimizer import OptimizerService

SCHEMA = {
    'tables': {
        'employees': {'columns': {'id': 'integer', 'first_name': 'varchar', 'last_name': 'varchar', 'email': 'varchar', 'phone': 'varchar', 'hire_date': 'date', 'salary': 'decimal', 'department': 'varchar', 'department_id': 'integer', 'manager_id': 'integer', 'is_active': 'boolean', 'created_at': 'timestamp'}},
        'departments': {'columns': {'id': 'integer', 'name': 'varchar', 'description': 'text', 'location': 'varchar', 'budget': 'decimal', 'created_at': 'timestamp'}},
        'customers': {'columns': {'id': 'integer', 'first_name': 'varchar', 'last_name': 'varchar', 'email': 'varchar', 'phone': 'varchar', 'address': 'text', 'city': 'varchar', 'state': 'varchar', 'zip_code': 'varchar', 'country': 'varchar', 'created_at': 'timestamp', 'updated_at': 'timestamp'}},
        'purchases': {'columns': {'id': 'integer', 'customer_id': 'integer', 'product_name': 'varchar', 'category': 'varchar', 'quantity': 'integer', 'unit_price': 'decimal', 'total_amount': 'decimal', 'purchase_date': 'timestamp', 'payment_method': 'varchar', 'status': 'varchar'}},
        'products': {'columns': {'id': 'integer', 'name': 'varchar', 'category': 'varchar', 'description': 'text', 'price': 'decimal', 'cost': 'decimal', 'stock_quantity': 'integer', 'is_active': 'boolean', 'created_at': 'timestamp'}},
    }
}

intent_service = IntentService()
optimizer = OptimizerService(SCHEMA)

test_cases = [
    'Show top 5 departments by average salary',
    'Show me the top 10 highest paid employees',
    'List all customers from New York',
    'Show total sales by product category',
    'Find employees who earn more than 100000',
    'Show average purchase amount per customer',
    'Show top 3 products by total revenue',
]

for text in test_cases:
    intent = intent_service.extract_intent(text, SCHEMA)
    candidates = optimizer.generate_from_intent(intent)
    print(f'Intent: {text}')
    print(f'  Cat: {intent.get("category")}, Op: {intent.get("operation")}, Entity: {intent.get("entity")}, Metric: {intent.get("metric")}, GroupBy: {intent.get("group_by")}, Limit: {intent.get("limit")}')
    print(f'  Filters: {intent.get("filters")}')
    for i, c in enumerate(candidates[:2]):
        print(f'  #{i+1}: {c["sql"]}')
    print()