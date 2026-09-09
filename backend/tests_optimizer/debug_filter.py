import os
import sys
import django
sys.path.insert(0, r'd:\AI projects\SQL Query Optimizer\backend')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'sql_optimizer.settings')
django.setup()

from queries.services.query_analyzer import analyze_query_structure
from queries.services.sql_parser import get_parser
from queries.services.rewrite_engine import RewriteEngine
import sqlglot
from sqlglot import exp

SCHEMA = {
    'tables': {
        'employees': {
            'columns': {
                'id': {'type': 'integer', 'primary_key': True},
                'first_name': {'type': 'text'},
                'last_name': {'type': 'text'},
                'email': {'type': 'text'},
                'hire_date': {'type': 'date'},
                'joining_timestamp': {'type': 'timestamp'},
                'created_at': {'type': 'timestamptz'},
                'salary': {'type': 'numeric'},
                'department_id': {'type': 'integer', 'not_null': True},
            },
            'primary_key': ['id'],
            'indexes': []
        },
        'departments': {
            'columns': {
                'id': {'type': 'integer', 'primary_key': True},
                'name': {'type': 'text'},
                'budget': {'type': 'numeric'},
            },
            'primary_key': ['id'],
            'indexes': []
        }
    },
    'relationships': [
        {'from_table': 'employees', 'from_column': 'department_id', 'to_table': 'departments', 'to_column': 'id', 'type': 'many_to_one'}
    ]
}

parser = get_parser()
engine = RewriteEngine(SCHEMA)
sql = "SELECT * FROM employees INNER JOIN departments ON employees.department_id = departments.id WHERE departments.budget > 100000"
parsed = parser.parse(sql)
structure = analyze_query_structure(parsed, sql, SCHEMA)

# Debug the function step by step
print('Step 1: Find inner join')
inner_join = None
for j in structure.joins:
    if j.type.upper().strip() == 'INNER':
        inner_join = j
        break
print(f'  inner_join: table={inner_join.table if inner_join else None}')

joined = inner_join.table.lower()
print(f'  joined: {joined}')

print('Step 2: Find candidate pred')
candidate_pred = None
for pred in list(structure.where.equalities) + list(structure.where.ranges):
    table = (pred.table or '').lower()
    if table == joined:
        candidate_pred = pred
        break
print(f'  candidate_pred: {candidate_pred}')
if candidate_pred:
    print(f'    column={candidate_pred.column}, value={candidate_pred.value}, operator={candidate_pred.operator}')

print('Step 3: Parse SQL')
ast = sqlglot.parse_one(sql, dialect='postgres')
where = ast.find(exp.Where)
print(f'  where: {where}')
print(f'  where.this: {where.this if where else None}')

print('Step 4: Build filter')
value_str = str(candidate_pred.value)
print(f'  value_str: {value_str}')
if ':' in value_str:
    value_str = value_str.split(':', 1)[1]
if value_str.startswith("'") and value_str.endswith("'"):
    value_str = value_str[1:-1]
print(f'  cleaned value_str: {value_str}')

col_expr = exp.column(candidate_pred.column, table=candidate_pred.table or joined)
print(f'  col_expr: {col_expr}')

if candidate_pred.operator in ('EQ', '='):
    filt = exp.EQ(this=col_expr, expression=exp.Literal.string(value_str))
else:
    filt = None
print(f'  filt: {filt}')

print('Step 5: Find target join')
target_join = None
for j in ast.find_all(exp.Join):
    jt = j.this if isinstance(j.this, exp.Table) else None
    if jt and jt.name.lower() == joined:
        target_join = j
        break
print(f'  target_join: {target_join}')
if target_join:
    print(f'    on: {target_join.args.get("on")}')

print('Step 6: Append to ON')
if target_join and target_join.args.get('on'):
    on_expr = target_join.args['on']
    if isinstance(on_expr, exp.Paren):
        on_expr = on_expr.this
    target_join.set('on', exp.paren(exp.and_(on_expr, filt)))
    print(f'  new on: {target_join.args.get("on")}')