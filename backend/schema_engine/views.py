"""
Views for the schema engine API.
"""
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from django.db import connections
from django.db.utils import OperationalError

from .serializers import (
    SchemaDefinitionSerializer,
    FullSchemaSerializer,
    SeedDataSummarySerializer
)
from .models import SchemaDefinition, Employee, Department, Customer, Purchase, Product


class SchemaDefinitionView(APIView):
    """
    POST /api/schema/ - Register a schema definition
    GET /api/schema/ - List schema definitions
    GET /api/schema/{id}/ - Get schema definition
    PUT /api/schema/{id}/ - Update schema definition
    DELETE /api/schema/{id}/ - Delete schema definition
    """

    def post(self, request):
        serializer = SchemaDefinitionSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def get(self, request, pk=None):
        if pk:
            try:
                schema = SchemaDefinition.objects.get(pk=pk)
                serializer = SchemaDefinitionSerializer(schema)
                return Response(serializer.data)
            except SchemaDefinition.DoesNotExist:
                return Response({'error': 'Schema not found'}, status=status.HTTP_404_NOT_FOUND)
        else:
            schemas = SchemaDefinition.objects.filter(is_active=True)
            serializer = SchemaDefinitionSerializer(schemas, many=True)
            return Response(serializer.data)


class SchemaDetailView(APIView):
    """GET /api/schema/{id}/ - Get schema definition"""

    def get(self, request, pk):
        try:
            schema = SchemaDefinition.objects.get(pk=pk)
            serializer = SchemaDefinitionSerializer(schema)
            return Response(serializer.data)
        except SchemaDefinition.DoesNotExist:
            return Response({'error': 'Schema not found'}, status=status.HTTP_404_NOT_FOUND)

    def put(self, request, pk):
        try:
            schema = SchemaDefinition.objects.get(pk=pk)
            serializer = SchemaDefinitionSerializer(schema, data=request.data, partial=True)
            if serializer.is_valid():
                serializer.save()
                return Response(serializer.data)
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        except SchemaDefinition.DoesNotExist:
            return Response({'error': 'Schema not found'}, status=status.HTTP_404_NOT_FOUND)

    def delete(self, request, pk):
        try:
            schema = SchemaDefinition.objects.get(pk=pk)
            schema.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        except SchemaDefinition.DoesNotExist:
            return Response({'error': 'Schema not found'}, status=status.HTTP_404_NOT_FOUND)


class SeedSchemaView(APIView):
    """GET /api/schema/seed/ - Get the seed database schema"""

    def get(self, request):
        # Return the predefined seed schema
        seed_schema = {
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
                    'indexes': [
                        {'columns': ['email'], 'unique': True},
                        {'columns': ['department_id']},
                        {'columns': ['manager_id']},
                        {'columns': ['hire_date']},
                        {'columns': ['salary']},
                    ]
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
                    'indexes': [
                        {'columns': ['name'], 'unique': True},
                    ]
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
                    'indexes': [
                        {'columns': ['email'], 'unique': True},
                        {'columns': ['city', 'state']},
                        {'columns': ['created_at']},
                    ]
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
                    'indexes': [
                        {'columns': ['customer_id']},
                        {'columns': ['purchase_date']},
                        {'columns': ['category']},
                        {'columns': ['status']},
                    ]
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
                    'indexes': [
                        {'columns': ['category']},
                        {'columns': ['is_active']},
                    ]
                },
            },
            'relationships': [
                {
                    'from_table': 'employees',
                    'from_column': 'department_id',
                    'to_table': 'departments',
                    'to_column': 'id',
                    'type': 'many_to_one'
                },
                {
                    'from_table': 'employees',
                    'from_column': 'manager_id',
                    'to_table': 'employees',
                    'to_column': 'id',
                    'type': 'many_to_one'
                },
                {
                    'from_table': 'purchases',
                    'from_column': 'customer_id',
                    'to_table': 'customers',
                    'to_column': 'id',
                    'type': 'many_to_one'
                },
            ]
        }

        serializer = FullSchemaSerializer(data=seed_schema)
        serializer.is_valid(raise_exception=True)
        return Response(serializer.data)


class SeedDataSummaryView(APIView):
    """GET /api/schema/seed/summary/ - Get row counts and column info for seed tables"""

    def get(self, request):
        summaries = []

        tables = [
            ('employees', Employee),
            ('departments', Department),
            ('customers', Customer),
            ('purchases', Purchase),
            ('products', Product),
        ]

        for table_name, model in tables:
            try:
                # Use seed_db connection
                count = model.objects.using('seed_db').count()

                # Get column names from model
                columns = [f.name for f in model._meta.fields]

                summaries.append({
                    'table': table_name,
                    'row_count': count,
                    'columns': columns,
                })
            except OperationalError as e:
                summaries.append({
                    'table': table_name,
                    'row_count': 0,
                    'columns': [],
                    'error': f"Database connection error: {str(e)}"
                })
            except Exception as e:
                summaries.append({
                    'table': table_name,
                    'row_count': 0,
                    'columns': [],
                    'error': str(e)
                })

        serializer = SeedDataSummarySerializer(summaries, many=True)
        return Response(serializer.data)


class ValidateSchemaView(APIView):
    """POST /api/schema/validate/ - Validate a schema definition"""

    def post(self, request):
        schema_data = request.data

        # Basic validation
        errors = []
        warnings = []

        if 'tables' not in schema_data:
            errors.append("Schema must have 'tables' key")
        else:
            tables = schema_data['tables']
            if not isinstance(tables, dict):
                errors.append("'tables' must be a dictionary")
            else:
                for table_name, table_def in tables.items():
                    if 'columns' not in table_def:
                        errors.append(f"Table '{table_name}' must have 'columns'")
                    elif not isinstance(table_def['columns'], dict):
                        errors.append(f"Table '{table_name}' columns must be a dictionary")
                    else:
                        has_pk = False
                        for col_name, col_def in table_def['columns'].items():
                            if not isinstance(col_def, dict):
                                warnings.append(f"Column '{table_name}.{col_name}' definition should be an object")
                            if col_def.get('primary_key'):
                                has_pk = True
                        if not has_pk:
                            warnings.append(f"Table '{table_name}' has no primary key defined")

        return Response({
            'valid': len(errors) == 0,
            'errors': errors,
            'warnings': warnings,
        })