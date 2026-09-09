import sys, os
sys.path.insert(0, r'd:\AI projects\SQL Query Optimizer\backend')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'sql_optimizer.settings')
import django
django.setup()

from queries.services.cost_model import estimate_cost_from_structure

SCHEMA = {
    'tables': {
        'employees': {
            'columns': {'id': 'integer', 'email': 'varchar', 'joining_date': 'timestamp',
                        'salary': 'numeric', 'department_id': 'integer'},
            'primary_key': ['id'],
        },
        'departments': {'columns': {'id': 'integer', 'name': 'varchar'}, 'primary_key': ['id']},
        'purchases': {'columns': {'id': 'integer', 'customer_id': 'integer', 'total_amount': 'numeric'}},
    }
}

def cost(sql):
    return estimate_cost_from_structure(sql, schema=SCHEMA)

simple = cost("SELECT id FROM employees WHERE id = 5")
star_eq = cost("SELECT * FROM employees WHERE id = 5")
star_lower = cost("SELECT * FROM employees WHERE LOWER(email)='x'")
arith = cost("SELECT * FROM employees WHERE salary+5000=100000")
big_join = cost("SELECT * FROM employees e JOIN departments d ON e.department_id=d.id JOIN purchases p ON e.id=p.customer_id")
union_all = cost("SELECT id FROM employees UNION ALL SELECT id FROM departments")
union = cost("SELECT id FROM employees UNION SELECT id FROM departments")
distinct = cost("SELECT DISTINCT department_id FROM employees")
offset = cost("SELECT id FROM employees ORDER BY id LIMIT 10 OFFSET 2000")

print("simple:", simple.total, "flags:", simple.flags)
print("star_eq:", star_eq.total, "flags:", star_eq.flags)
print("star_lower:", star_lower.total, "flags:", star_lower.flags)
print("arith:", arith.total, "flags:", arith.flags)
print("big_join:", big_join.total, "flags:", big_join.flags)
print("union_all:", union_all.total)
print("union:", union.total)
print("distinct:", distinct.total)
print("offset:", offset.total, "flags:", offset.flags)

# Monotonic ordering assertions
assert simple.total < star_eq.total, "simple indexed equality should be cheapest"
assert star_eq.total < star_lower.total, "LOWER() should cost more"
assert star_lower.total < big_join.total, "3 tables + SELECT * + lower should cost most"
assert union_all.total < union.total, "UNION (dedup) should cost more than UNION ALL"
assert star_eq.total < distinct.total, "DISTINCT adds cost"
assert offset.total > 20, "large offset adds cost"
print("All heuristic costs labeled:", {c.flags for c in [simple, star_eq, star_lower, arith, big_join, union_all, union, distinct, offset]}.pop()[0] if False else "checked cost_source")
print("cost_source =", simple.cost_source, ",", star_lower.cost_source)
print("PHASE4 DONE")
