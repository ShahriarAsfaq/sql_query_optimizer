"""
Models for the schema engine - schema definitions and seed database models.
"""
from django.db import models
from django.conf import settings


class SchemaDefinition(models.Model):
    """User-defined schema definition."""
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    schema_json = models.JSONField()  # Full schema definition
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Optional: link to user if auth is added later
    # user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.name


# Seed database models - these represent the demo schema
# They use the 'seed_db' database via the router

class Employee(models.Model):
    """Employee table for seed database."""
    USE_SEED_DB = True

    id = models.AutoField(primary_key=True)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    email = models.CharField(max_length=255, unique=True)
    phone = models.CharField(max_length=20, blank=True)
    hire_date = models.DateField()
    salary = models.DecimalField(max_digits=12, decimal_places=2)
    department_id = models.IntegerField()  # FK to Department
    manager_id = models.IntegerField(null=True, blank=True)  # Self-referential FK
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'employees'
        managed = False  # Table exists in seed DB


class Department(models.Model):
    """Department table for seed database."""
    USE_SEED_DB = True

    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    location = models.CharField(max_length=100, blank=True)
    budget = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'departments'
        managed = False


class Customer(models.Model):
    """Customer table for seed database."""
    USE_SEED_DB = True

    id = models.AutoField(primary_key=True)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    email = models.CharField(max_length=255, unique=True)
    phone = models.CharField(max_length=20, blank=True)
    address = models.TextField(blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=50, blank=True)
    zip_code = models.CharField(max_length=20, blank=True)
    country = models.CharField(max_length=50, default='USA')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'customers'
        managed = False


class Purchase(models.Model):
    """Purchase table for seed database."""
    USE_SEED_DB = True

    id = models.AutoField(primary_key=True)
    customer_id = models.IntegerField()  # FK to Customer
    product_name = models.CharField(max_length=255)
    category = models.CharField(max_length=100)
    quantity = models.IntegerField()
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2)
    purchase_date = models.DateTimeField()
    payment_method = models.CharField(max_length=50)
    status = models.CharField(max_length=20, default='completed')

    class Meta:
        db_table = 'purchases'
        managed = False


class Product(models.Model):
    """Product table for seed database."""
    USE_SEED_DB = True

    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=255)
    category = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    cost = models.DecimalField(max_digits=10, decimal_places=2)
    stock_quantity = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'products'
        managed = False