#!/usr/bin/env python
"""
Seed database setup script.
Creates the seed database tables and inserts sample data.
Run this after setting up PostgreSQL and configuring the seed_db connection.
"""
import os
import sys
import django

# Setup Django
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'sql_optimizer.settings')
django.setup()

from django.db import connections
from django.db.utils import OperationalError


def create_seed_database():
    """Create the seed database if it doesn't exist."""
    # Connect to default postgres database to create seed_db
    import psycopg2
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    try:
        conn = psycopg2.connect(
            host=os.getenv('SEED_DB_HOST', 'localhost'),
            port=os.getenv('SEED_DB_PORT', '5432'),
            user=os.getenv('SEED_DB_USER', 'postgres'),
            password=os.getenv('SEED_DB_PASSWORD', 'postgres'),
            database='postgres'
        )
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        cursor = conn.cursor()

        db_name = os.getenv('SEED_DB_NAME', 'sql_optimizer_seed')
        cursor.execute(f"SELECT 1 FROM pg_database WHERE datname = '{db_name}'")
        exists = cursor.fetchone()

        if not exists:
            cursor.execute(f"CREATE DATABASE {db_name}")
            print(f"Created database: {db_name}")
        else:
            print(f"Database {db_name} already exists")

        cursor.close()
        conn.close()
    except Exception as e:
        print(f"Error creating database: {e}")
        return False

    return True


def create_tables():
    """Create tables in the seed database."""
    try:
        conn = connections['seed_db']
        cursor = conn.cursor()

        # Create tables
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS departments (
                id SERIAL PRIMARY KEY,
                name VARCHAR(100) UNIQUE NOT NULL,
                description TEXT,
                location VARCHAR(100),
                budget DECIMAL(14,2) DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS employees (
                id SERIAL PRIMARY KEY,
                first_name VARCHAR(100) NOT NULL,
                last_name VARCHAR(100) NOT NULL,
                email VARCHAR(255) UNIQUE NOT NULL,
                phone VARCHAR(20),
                hire_date DATE NOT NULL,
                salary DECIMAL(12,2) NOT NULL,
                department_id INTEGER REFERENCES departments(id),
                manager_id INTEGER REFERENCES employees(id),
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS customers (
                id SERIAL PRIMARY KEY,
                first_name VARCHAR(100) NOT NULL,
                last_name VARCHAR(100) NOT NULL,
                email VARCHAR(255) UNIQUE NOT NULL,
                phone VARCHAR(20),
                address TEXT,
                city VARCHAR(100),
                state VARCHAR(50),
                zip_code VARCHAR(20),
                country VARCHAR(50) DEFAULT 'USA',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS purchases (
                id SERIAL PRIMARY KEY,
                customer_id INTEGER REFERENCES customers(id),
                product_name VARCHAR(255) NOT NULL,
                category VARCHAR(100) NOT NULL,
                quantity INTEGER NOT NULL,
                unit_price DECIMAL(10,2) NOT NULL,
                total_amount DECIMAL(12,2) NOT NULL,
                purchase_date TIMESTAMP NOT NULL,
                payment_method VARCHAR(50),
                status VARCHAR(20) DEFAULT 'completed'
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id SERIAL PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                category VARCHAR(100) NOT NULL,
                description TEXT,
                price DECIMAL(10,2) NOT NULL,
                cost DECIMAL(10,2) NOT NULL,
                stock_quantity INTEGER DEFAULT 0,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.commit()
        print("Tables created successfully")
        return True
    except Exception as e:
        print(f"Error creating tables: {e}")
        return False
    finally:
        if 'conn' in locals():
            conn.close()


def insert_sample_data():
    """Insert sample data into seed database."""
    try:
        conn = connections['seed_db']
        cursor = conn.cursor()

        # Clear existing data
        cursor.execute("TRUNCATE TABLE purchases, employees, departments, customers, products RESTART IDENTITY CASCADE")

        # Insert departments
        departments = [
            (1, 'Engineering', 'Software development team', 'San Francisco', 500000.00),
            (2, 'Sales', 'Sales and business development', 'New York', 300000.00),
            (3, 'Marketing', 'Marketing and brand', 'Los Angeles', 200000.00),
            (4, 'HR', 'Human resources', 'Chicago', 150000.00),
            (5, 'Finance', 'Finance and accounting', 'Boston', 250000.00),
        ]
        for dept in departments:
            cursor.execute("""
                INSERT INTO departments (id, name, description, location, budget)
                VALUES (%s, %s, %s, %s, %s)
            """, dept)

        # Insert employees
        employees = [
            (1, 'John', 'Smith', 'john.smith@company.com', '555-0101', '2020-01-15', 120000.00, 1, None),
            (2, 'Jane', 'Doe', 'jane.doe@company.com', '555-0102', '2019-03-22', 135000.00, 1, 1),
            (3, 'Bob', 'Johnson', 'bob.johnson@company.com', '555-0103', '2021-06-10', 95000.00, 1, 1),
            (4, 'Alice', 'Williams', 'alice.williams@company.com', '555-0104', '2018-11-05', 140000.00, 2, None),
            (5, 'Charlie', 'Brown', 'charlie.brown@company.com', '555-0105', '2022-02-18', 85000.00, 2, 4),
            (6, 'Diana', 'Prince', 'diana.prince@company.com', '555-0106', '2020-08-30', 110000.00, 3, None),
            (7, 'Edward', 'Norton', 'edward.norton@company.com', '555-0107', '2021-12-01', 90000.00, 3, 6),
            (8, 'Fiona', 'Gallagher', 'fiona.gallagher@company.com', '555-0108', '2019-07-14', 75000.00, 4, None),
            (9, 'George', 'Lucas', 'george.lucas@company.com', '555-0109', '2022-04-20', 125000.00, 5, None),
            (10, 'Hannah', 'Montana', 'hannah.montana@company.com', '555-0110', '2023-01-10', 80000.00, 5, 9),
        ]
        for emp in employees:
            cursor.execute("""
                INSERT INTO employees (id, first_name, last_name, email, phone, hire_date, salary, department_id, manager_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, emp)

        # Insert customers
        customers = [
            (1, 'Michael', 'Scott', 'michael@dundermifflin.com', '555-1001', '123 Paper St', 'Scranton', 'PA', '18501', 'USA'),
            (2, 'Jim', 'Halpert', 'jim@dundermifflin.com', '555-1002', '456 Office Ave', 'Scranton', 'PA', '18502', 'USA'),
            (3, 'Pam', 'Beesly', 'pam@dundermifflin.com', '555-1003', '789 Desk Rd', 'Philadelphia', 'PA', '19101', 'USA'),
            (4, 'Dwight', 'Schrute', 'dwight@dundermifflin.com', '555-1004', '321 Beet Farm', 'Schrute', 'PA', '18503', 'USA'),
            (5, 'Angela', 'Martin', 'angela@dundermifflin.com', '555-1005', '555 Cat Lane', 'Scranton', 'PA', '18504', 'USA'),
        ]
        for cust in customers:
            cursor.execute("""
                INSERT INTO customers (id, first_name, last_name, email, phone, address, city, state, zip_code, country)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, cust)

        # Insert products
        products = [
            (1, 'Laptop Pro 15', 'Electronics', 'High-performance laptop', 1299.99, 800.00, 50),
            (2, 'Wireless Mouse', 'Electronics', 'Ergonomic wireless mouse', 29.99, 10.00, 200),
            (3, 'Mechanical Keyboard', 'Electronics', 'RGB mechanical keyboard', 149.99, 60.00, 75),
            (4, 'Monitor 27"', 'Electronics', '4K UHD monitor', 399.99, 220.00, 30),
            (5, 'Desk Chair', 'Furniture', 'Ergonomic office chair', 299.99, 150.00, 40),
        ]
        for prod in products:
            cursor.execute("""
                INSERT INTO products (id, name, category, description, price, cost, stock_quantity)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, prod)

        # Insert purchases
        purchases = [
            (1, 1, 'Laptop Pro 15', 'Electronics', 2, 1299.99, 2599.98, '2023-01-15 10:30:00', 'credit_card', 'completed'),
            (2, 2, 'Wireless Mouse', 'Electronics', 5, 29.99, 149.95, '2023-01-20 14:15:00', 'credit_card', 'completed'),
            (3, 3, 'Mechanical Keyboard', 'Electronics', 1, 149.99, 149.99, '2023-02-01 09:00:00', 'paypal', 'completed'),
            (4, 1, 'Monitor 27"', 'Electronics', 1, 399.99, 399.99, '2023-02-10 16:45:00', 'credit_card', 'completed'),
            (5, 4, 'Desk Chair', 'Furniture', 3, 299.99, 899.97, '2023-02-15 11:20:00', 'bank_transfer', 'completed'),
            (6, 5, 'Laptop Pro 15', 'Electronics', 1, 1299.99, 1299.99, '2023-03-01 13:30:00', 'credit_card', 'completed'),
            (7, 2, 'Monitor 27"', 'Electronics', 2, 399.99, 799.98, '2023-03-10 10:00:00', 'paypal', 'completed'),
            (8, 3, 'Wireless Mouse', 'Electronics', 10, 29.99, 299.90, '2023-03-20 15:45:00', 'credit_card', 'completed'),
        ]
        for pur in purchases:
            cursor.execute("""
                INSERT INTO purchases (id, customer_id, product_name, category, quantity, unit_price, total_amount, purchase_date, payment_method, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, pur)

        conn.commit()
        print("Sample data inserted successfully")
        return True
    except Exception as e:
        print(f"Error inserting data: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        if 'conn' in locals():
            conn.close()


def test_explain():
    """Test EXPLAIN on a sample query."""
    try:
        conn = connections['seed_db']
        cursor = conn.cursor()

        # Query with join to get department name
        query = """
            SELECT d.name as department, AVG(e.salary) as avg_salary
            FROM employees e
            JOIN departments d ON e.department_id = d.id
            GROUP BY d.name
            ORDER BY avg_salary DESC
            LIMIT 5
        """
        cursor.execute(f"EXPLAIN (FORMAT JSON) {query}")
        result = cursor.fetchone()
        if result and result[0]:
            plan = result[0][0]['Plan']
            print(f"Query: {query.strip()}")
            print(f"Estimated cost: {plan.get('Total Cost', 'N/A')}")
            print(f"Plan: {plan}")

        # Test another query
        query2 = "SELECT * FROM employees WHERE salary > 100000"
        cursor.execute(f"EXPLAIN (FORMAT JSON) {query2}")
        result = cursor.fetchone()
        if result and result[0]:
            plan = result[0][0]['Plan']
            print(f"\nQuery: {query2}")
            print(f"Estimated cost: {plan.get('Total Cost', 'N/A')}")

        return True
    except Exception as e:
        print(f"Error testing EXPLAIN: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        if 'conn' in locals():
            conn.close()


if __name__ == '__main__':
    print("Setting up seed database...")
    if create_seed_database():
        if create_tables():
            if insert_sample_data():
                print("\nSeed database setup complete!")
                print("Testing EXPLAIN...")
                test_explain()
            else:
                print("Failed to insert sample data")
        else:
            print("Failed to create tables")
    else:
        print("Failed to create database")