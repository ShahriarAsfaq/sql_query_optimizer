import sys
sys.path.insert(0, r'd:\AI projects\SQL Query Optimizer\backend')
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'sql_optimizer.settings')
import django
django.setup()

from queries.services.intent import IntentService
from queries.services.optimizer import OptimizerService
from queries.services.llm_client import MockLLMClient

# Use the same schema as frontend
SCHEMA = {
    'tables': {
        'employees': {
            'columns': {
                'id': {'type': 'integer', 'primary_key': True},
                'first_name': {'type': 'varchar', 'max_length': 100},
                'last_name': {'type': 'varchar', 'max_length': 100},
                'email': {'type': 'varchar', 'max_length': 255, 'unique': True},
                'phone': {'type': 'varchar', 'max_length': 20},
                'hire_date': {'type': 'date'},
                'salary': {'type': 'decimal', 'precision': 12, 'scale': 2},
                'department_id': {'type': 'integer', 'foreign_key': 'departments.id'},
                'manager_id': {'type': 'integer', 'foreign_key': 'employees.id', 'nullable': True},
                'is_active': {'type': 'boolean', 'default': True},
                'created_at': {'type': 'timestamp'},
            },
            'primary_key': ['id'],
        },
        'departments': {
            'columns': {
                'id': {'type': 'integer', 'primary_key': True},
                'name': {'type': 'varchar', 'max_length': 100, 'unique': True},
                'description': {'type': 'text'},
                'location': {'type': 'varchar', 'max_length': 100},
                'budget': {'type': 'decimal', 'precision': 14, 'scale': 2},
                'created_at': {'type': 'timestamp'},
            },
            'primary_key': ['id'],
        },
        'customers': {
            'columns': {
                'id': {'type': 'integer', 'primary_key': True},
                'first_name': {'type': 'varchar', 'max_length': 100},
                'last_name': {'type': 'varchar', 'max_length': 100},
                'email': {'type': 'varchar', 'max_length': 255, 'unique': True},
                'phone': {'type': 'varchar', 'max_length': 20},
                'address': {'type': 'text'},
                'city': {'type': 'varchar', 'max_length': 100},
                'state': {'type': 'varchar', 'max_length': 50},
                'zip_code': {'type': 'varchar', 'max_length': 20},
                'country': {'type': 'varchar', 'max_length': 50},
                'created_at': {'type': 'timestamp'},
                'updated_at': {'type': 'timestamp'},
            },
            'primary_key': ['id'],
        },
        'purchases': {
            'columns': {
                'id': {'type': 'integer', 'primary_key': True},
                'customer_id': {'type': 'integer', 'foreign_key': 'customers.id'},
                'product_name': {'type': 'varchar', 'max_length': 255},
                'category': {'type': 'varchar', 'max_length': 100},
                'quantity': {'type': 'integer'},
                'unit_price': {'type': 'decimal', 'precision': 10, 'scale': 2},
                'total_amount': {'type': 'decimal', 'precision': 12, 'scale': 2},
                'purchase_date': {'type': 'timestamp'},
                'payment_method': {'type': 'varchar', 'max_length': 50},
                'status': {'type': 'varchar', 'max_length': 20},
            },
            'primary_key': ['id'],
        },
        'products': {
            'columns': {
                'id': {'type': 'integer', 'primary_key': True},
                'name': {'type': 'varchar', 'max_length': 255},
                'category': {'type': 'varchar', 'max_length': 100},
                'description': {'type': 'text'},
                'price': {'type': 'decimal', 'precision': 10, 'scale': 2},
                'cost': {'type': 'decimal', 'precision': 10, 'scale': 2},
                'stock_quantity': {'type': 'integer'},
                'is_active': {'type': 'boolean', 'default': True},
                'created_at': {'type': 'timestamp'},
            },
            'primary_key': ['id'],
        },
    },
    'relationships': [
        {'from_table': 'employees', 'from_column': 'department_id', 'to_table': 'departments', 'to_column': 'id', 'type': 'many_to_one'},
        {'from_table': 'employees', 'from_column': 'manager_id', 'to_table': 'employees', 'to_column': 'id', 'type': 'many_to_one'},
        {'from_table': 'purchases', 'from_column': 'customer_id', 'to_table': 'customers', 'to_column': 'id', 'type': 'many_to_one'},
    ],
}

# Convert schema format for optimizer
opt_schema = {
    'tables': {},
    'relationships': SCHEMA['relationships']
}
for table_name, table_def in SCHEMA['tables'].items():
    cols = {}
    for col_name, col_info in table_def['columns'].items():
        cols[col_name] = col_info['type']
    opt_schema['tables'][table_name] = {'columns': cols}

# Test with MockLLMClient
mock_llm = MockLLMClient()
intent_service = IntentService(llm_client=mock_llm)
optimizer = OptimizerService(opt_schema)

test_cases = [
    'Show employees with their departments',
    'Find employees with their managers',
    'Show customers with their orders',
    'List customers and their purchases',
    'Show me the top 5 highest paid employees',
    'Show top 5 departments by average salary',
    'List all customers from New York',
]

for text in test_cases:
    intent = intent_service.extract_intent(text, opt_schema)
    candidates = optimizer.generate_from_intent(intent)
    print(f'Intent: {text}')
    print(f'  Cat: {intent.get("category")}, Op: {intent.get("operation")}, Entity: {intent.get("entity")}, Join tables: {intent.get("join_tables", "N/A")}')
    for i, c in enumerate(candidates[:2]):
        print(f'  #{i+1}: {c["sql"]}')
    print()