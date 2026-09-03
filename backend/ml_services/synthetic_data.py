"""
Synthetic dataset generation for training ML models.
"""
import random
import json
from typing import List, Dict, Any, Tuple
from dataclasses import dataclass, asdict
import os


# Schema definition for generating queries
SCHEMA = {
    'tables': {
        'employees': {
            'columns': ['id', 'first_name', 'last_name', 'email', 'phone', 'hire_date', 'salary', 'department_id', 'manager_id', 'is_active', 'created_at'],
            'numeric_columns': ['id', 'salary', 'department_id', 'manager_id'],
            'string_columns': ['first_name', 'last_name', 'email', 'phone'],
            'date_columns': ['hire_date', 'created_at'],
        },
        'departments': {
            'columns': ['id', 'name', 'description', 'location', 'budget', 'created_at'],
            'numeric_columns': ['id', 'budget'],
            'string_columns': ['name', 'description', 'location'],
            'date_columns': ['created_at'],
        },
        'customers': {
            'columns': ['id', 'first_name', 'last_name', 'email', 'phone', 'address', 'city', 'state', 'zip_code', 'country', 'created_at', 'updated_at'],
            'numeric_columns': ['id'],
            'string_columns': ['first_name', 'last_name', 'email', 'phone', 'address', 'city', 'state', 'zip_code', 'country'],
            'date_columns': ['created_at', 'updated_at'],
        },
        'purchases': {
            'columns': ['id', 'customer_id', 'product_name', 'category', 'quantity', 'unit_price', 'total_amount', 'purchase_date', 'payment_method', 'status'],
            'numeric_columns': ['id', 'customer_id', 'quantity', 'unit_price', 'total_amount'],
            'string_columns': ['product_name', 'category', 'payment_method', 'status'],
            'date_columns': ['purchase_date'],
        },
        'products': {
            'columns': ['id', 'name', 'category', 'description', 'price', 'cost', 'stock_quantity', 'is_active', 'created_at'],
            'numeric_columns': ['id', 'price', 'cost', 'stock_quantity'],
            'string_columns': ['name', 'category', 'description'],
            'date_columns': ['created_at'],
        },
    },
    'relationships': [
        {'from_table': 'employees', 'from_column': 'department_id', 'to_table': 'departments', 'to_column': 'id', 'type': 'many_to_one'},
        {'from_table': 'employees', 'from_column': 'manager_id', 'to_table': 'employees', 'to_column': 'id', 'type': 'many_to_one'},
        {'from_table': 'purchases', 'from_column': 'customer_id', 'to_table': 'customers', 'to_column': 'id', 'type': 'many_to_one'},
    ],
}


# Intent categories
INTENT_CATEGORIES = [
    'RETRIEVE', 'FILTER', 'SORT', 'AGGREGATE', 'GROUP',
    'TOP_N', 'JOIN', 'TREND', 'RANKING', 'DUPLICATE_DETECTION', 'COMPARISON'
]

# Natural language templates for each intent category
NL_TEMPLATES = {
    'RETRIEVE': [
        "Show me all {table}",
        "List all {table}",
        "Get all {table}",
        "Display {table}",
        "Find all {table}",
    ],
    'FILTER': [
        "Show {table} where {col} = '{val}'",
        "Find {table} with {col} = '{val}'",
        "List {table} having {col} = '{val}'",
        "Get {table} from {val}",
        "Show {table} in {val}",
    ],
    'SORT': [
        "Sort {table} by {col} ascending",
        "Order {table} by {col} descending",
        "Show {table} sorted by {col} ASC",
        "List {table} ordered by {col} DESC",
    ],
    'AGGREGATE': [
        "What is the average {col} of {table}",
        "Calculate total {col} for {table}",
        "Sum of {col} in {table}",
        "Average {col} per {table}",
        "Count of {table}",
    ],
    'GROUP': [
        "Group {table} by {col}",
        "Breakdown {table} by {col}",
        "Show {table} per {col}",
        "Aggregate {table} by {col}",
    ],
    'TOP_N': [
        "Show top {n} {table} by {col}",
        "Find highest {n} {table} by {col}",
        "List {n} best {table} by {col}",
        "Get top {n} {table} with highest {col}",
    ],
    'JOIN': [
        "Show {table1} with their {table2}",
        "List {table1} and their {table2}",
        "Find {table1} along with {table2}",
        "Get {table1} together with {table2}",
    ],
    'TREND': [
        "Show trend of {col} in {table} over time",
        "How has {col} changed in {table} over time",
        "Time series of {col} for {table}",
    ],
    'RANKING': [
        "Rank {table} by {col}",
        "Show ranking of {table} by {col}",
        "Percentile of {table} by {col}",
    ],
    'DUPLICATE_DETECTION': [
        "Find duplicates in {table}",
        "Show duplicate {table}",
        "List repeated {table}",
    ],
    'COMPARISON': [
        "Compare {table1} vs {table2}",
        "Difference between {table1} and {table2}",
        "Compare {col} of {table1} versus {table2}",
    ],
}

# Value pools for templates
TABLES = ['employees', 'departments', 'customers', 'purchases', 'products']

COLUMN_MAP = {
    'employees': ['salary', 'department_id', 'hire_date', 'first_name', 'last_name', 'email', 'manager_id'],
    'departments': ['budget', 'name', 'location'],
    'customers': ['city', 'state', 'country', 'first_name', 'last_name', 'email'],
    'purchases': ['total_amount', 'category', 'quantity', 'unit_price', 'product_name', 'purchase_date'],
    'products': ['price', 'cost', 'category', 'stock_quantity', 'name'],
}

JOIN_PAIRS = [
    ('employees', 'departments'),
    ('employees', 'employees'),  # self-join for managers
    ('customers', 'purchases'),
]

CITIES = ['New York', 'Los Angeles', 'Chicago', 'Houston', 'Phoenix', 'Philadelphia', 'San Antonio', 'San Diego']
STATES = ['NY', 'CA', 'IL', 'TX', 'AZ', 'PA', 'TX', 'CA']
CATEGORIES = ['Electronics', 'Furniture', 'Clothing', 'Books', 'Sports', 'Toys']


def generate_retrieve_samples(n: int = 100) -> List[Dict[str, Any]]:
    """Generate RETRIEVE intent samples."""
    samples = []
    for _ in range(n):
        table = random.choice(TABLES)
        template = random.choice(NL_TEMPLATES['RETRIEVE'])
        nl = template.format(table=table)
        samples.append({
            'natural_language': nl,
            'intent_category': 'RETRIEVE',
            'features': extract_features(nl, table),
        })
    return samples


def generate_filter_samples(n: int = 100) -> List[Dict[str, Any]]:
    """Generate FILTER intent samples."""
    samples = []
    for _ in range(n):
        table = random.choice(TABLES)
        cols = COLUMN_MAP[table]
        col = random.choice(cols)
        val = random.choice(CITIES + STATES + CATEGORIES + ['100000', '50000', '200000'])
        template = random.choice(NL_TEMPLATES['FILTER'])
        nl = template.format(table=table, col=col, val=val)
        samples.append({
            'natural_language': nl,
            'intent_category': 'FILTER',
            'features': extract_features(nl, table),
        })
    return samples


def generate_sort_samples(n: int = 100) -> List[Dict[str, Any]]:
    """Generate SORT intent samples."""
    samples = []
    for _ in range(n):
        table = random.choice(TABLES)
        cols = [c for c in COLUMN_MAP[table] if c in ['salary', 'budget', 'total_amount', 'price', 'hire_date', 'purchase_date']]
        if not cols:
            cols = COLUMN_MAP[table]  # fallback to any column
        col = random.choice(cols)
        direction = random.choice(['ascending', 'descending', 'ASC', 'DESC'])
        template = random.choice(NL_TEMPLATES['SORT'])
        nl = template.format(table=table, col=col, direction=direction)
        samples.append({
            'natural_language': nl,
            'intent_category': 'SORT',
            'features': extract_features(nl, table),
        })
    return samples


def generate_aggregate_samples(n: int = 100) -> List[Dict[str, Any]]:
    """Generate AGGREGATE intent samples."""
    samples = []
    for _ in range(n):
        table = random.choice(TABLES)
        cols = [c for c in COLUMN_MAP[table] if c in ['salary', 'budget', 'total_amount', 'price', 'quantity', 'cost']]
        if not cols:
            cols = COLUMN_MAP[table]  # fallback to any column
        col = random.choice(cols)
        agg_func = random.choice(['average', 'total', 'sum', 'count', 'maximum', 'minimum'])
        template = random.choice(NL_TEMPLATES['AGGREGATE'])
        nl = template.format(table=table, col=col)
        # Replace aggregate keyword in template
        nl = nl.replace('average', agg_func).replace('total', agg_func).replace('sum', agg_func)
        samples.append({
            'natural_language': nl,
            'intent_category': 'AGGREGATE',
            'features': extract_features(nl, table),
        })
    return samples


def generate_group_samples(n: int = 100) -> List[Dict[str, Any]]:
    """Generate GROUP intent samples."""
    samples = []
    for _ in range(n):
        table = random.choice(TABLES)
        cols = [c for c in COLUMN_MAP[table] if c in ['department_id', 'city', 'state', 'category', 'country', 'location']]
        if not cols:
            cols = COLUMN_MAP[table]  # fallback to any column
        col = random.choice(cols)
        template = random.choice(NL_TEMPLATES['GROUP'])
        nl = template.format(table=table, col=col)
        samples.append({
            'natural_language': nl,
            'intent_category': 'GROUP',
            'features': extract_features(nl, table),
        })
    return samples


def generate_top_n_samples(n: int = 100) -> List[Dict[str, Any]]:
    """Generate TOP_N intent samples."""
    samples = []
    for _ in range(n):
        table = random.choice(TABLES)
        numeric_cols = [c for c in COLUMN_MAP[table] if c in ['salary', 'budget', 'total_amount', 'price', 'quantity', 'cost']]
        if not numeric_cols:
            numeric_cols = COLUMN_MAP[table]  # fallback to any column
        col = random.choice(numeric_cols)
        limit = random.choice([3, 5, 10, 20, 50])
        template = random.choice(NL_TEMPLATES['TOP_N'])
        nl = template.format(n=limit, table=table, col=col)
        samples.append({
            'natural_language': nl,
            'intent_category': 'TOP_N',
            'features': extract_features(nl, table),
        })
    return samples


def generate_join_samples(n: int = 100) -> List[Dict[str, Any]]:
    """Generate JOIN intent samples."""
    samples = []
    for _ in range(n):
        table1, table2 = random.choice(JOIN_PAIRS)
        template = random.choice(NL_TEMPLATES['JOIN'])
        nl = template.format(table1=table1, table2=table2)
        samples.append({
            'natural_language': nl,
            'intent_category': 'JOIN',
            'features': extract_features(nl, table1),
        })
    return samples


def generate_trend_samples(n: int = 100) -> List[Dict[str, Any]]:
    """Generate TREND intent samples."""
    samples = []
    for _ in range(n):
        table = random.choice(TABLES)
        cols = [c for c in COLUMN_MAP[table] if c in ['salary', 'budget', 'total_amount', 'price', 'hire_date', 'purchase_date']]
        if not cols:
            cols = COLUMN_MAP[table]  # fallback to any column
        col = random.choice(cols)
        template = random.choice(NL_TEMPLATES['TREND'])
        nl = template.format(table=table, col=col)
        samples.append({
            'natural_language': nl,
            'intent_category': 'TREND',
            'features': extract_features(nl, table),
        })
    return samples


def generate_ranking_samples(n: int = 100) -> List[Dict[str, Any]]:
    """Generate RANKING intent samples."""
    samples = []
    for _ in range(n):
        table = random.choice(TABLES)
        cols = [c for c in COLUMN_MAP[table] if c in ['salary', 'budget', 'total_amount', 'price']]
        if not cols:
            cols = COLUMN_MAP[table]  # fallback to any column
        col = random.choice(cols)
        template = random.choice(NL_TEMPLATES['RANKING'])
        nl = template.format(table=table, col=col)
        samples.append({
            'natural_language': nl,
            'intent_category': 'RANKING',
            'features': extract_features(nl, table),
        })
    return samples


def generate_duplicate_samples(n: int = 100) -> List[Dict[str, Any]]:
    """Generate DUPLICATE_DETECTION intent samples."""
    samples = []
    for _ in range(n):
        table = random.choice(TABLES)
        template = random.choice(NL_TEMPLATES['DUPLICATE_DETECTION'])
        nl = template.format(table=table)
        samples.append({
            'natural_language': nl,
            'intent_category': 'DUPLICATE_DETECTION',
            'features': extract_features(nl, table),
        })
    return samples


def generate_comparison_samples(n: int = 100) -> List[Dict[str, Any]]:
    """Generate COMPARISON intent samples."""
    samples = []
    for _ in range(n):
        table1, table2 = random.choice([(t1, t2) for t1 in TABLES for t2 in TABLES if t1 != t2])
        template = random.choice(NL_TEMPLATES['COMPARISON'])
        col = random.choice(['salary', 'budget', 'total_amount', 'price'])
        nl = template.format(table1=table1, table2=table2, col=col)
        samples.append({
            'natural_language': nl,
            'intent_category': 'COMPARISON',
            'features': extract_features(nl, table1),
        })
    return samples


def extract_features(text: str, table: str) -> Dict[str, Any]:
    """Extract features from natural language for ML training."""
    text_lower = text.lower()

    # Keyword features
    features = {
        'has_top': 1 if 'top' in text_lower else 0,
        'has_highest': 1 if 'highest' in text_lower else 0,
        'has_lowest': 1 if 'lowest' in text_lower else 0,
        'has_average': 1 if any(kw in text_lower for kw in ['average', 'avg', 'mean']) else 0,
        'has_sum': 1 if any(kw in text_lower for kw in ['sum', 'total']) else 0,
        'has_count': 1 if 'count' in text_lower else 0,
        'has_group': 1 if any(kw in text_lower for kw in ['group by', 'per', 'by ', 'breakdown']) else 0,
        'has_where': 1 if any(kw in text_lower for kw in ['where', 'filter', 'having', 'only']) else 0,
        'has_order': 1 if any(kw in text_lower for kw in ['sort', 'order', 'ascending', 'descending', 'asc', 'desc']) else 0,
        'has_join': 1 if any(kw in text_lower for kw in ['join', 'with their', 'and their', 'along with', 'together with']) else 0,
        'has_trend': 1 if any(kw in text_lower for kw in ['trend', 'over time', 'time series']) else 0,
        'has_rank': 1 if any(kw in text_lower for kw in ['rank', 'ranking', 'percentile']) else 0,
        'has_duplicate': 1 if any(kw in text_lower for kw in ['duplicate', 'repeated']) else 0,
        'has_compare': 1 if any(kw in text_lower for kw in ['compare', 'versus', 'vs', 'difference']) else 0,
        'has_limit': 1 if any(c.isdigit() for c in text_lower) else 0,
        'table_mentioned': table,
        'text_length': len(text),
        'word_count': len(text.split()),
    }

    # Extract numeric values (potential limits)
    import re
    numbers = re.findall(r'\b\d+\b', text)
    features['numbers'] = [int(n) for n in numbers]
    features['max_number'] = max(features['numbers']) if features['numbers'] else 0

    return features


def generate_all_samples(samples_per_category: int = 100) -> List[Dict[str, Any]]:
    """Generate complete synthetic dataset."""
    all_samples = []

    all_samples.extend(generate_retrieve_samples(samples_per_category))
    all_samples.extend(generate_filter_samples(samples_per_category))
    all_samples.extend(generate_sort_samples(samples_per_category))
    all_samples.extend(generate_aggregate_samples(samples_per_category))
    all_samples.extend(generate_group_samples(samples_per_category))
    all_samples.extend(generate_top_n_samples(samples_per_category))
    all_samples.extend(generate_join_samples(samples_per_category))
    all_samples.extend(generate_trend_samples(samples_per_category))
    all_samples.extend(generate_ranking_samples(samples_per_category))
    all_samples.extend(generate_duplicate_samples(samples_per_category))
    all_samples.extend(generate_comparison_samples(samples_per_category))

    return all_samples


def save_dataset(samples: List[Dict[str, Any]], filepath: str):
    """Save dataset to JSON file."""
    with open(filepath, 'w') as f:
        json.dump(samples, f, indent=2)


def load_dataset(filepath: str) -> List[Dict[str, Any]]:
    """Load dataset from JSON file."""
    with open(filepath, 'r') as f:
        return json.load(f)


if __name__ == '__main__':
    print("Generating synthetic dataset...")
    samples = generate_all_samples(200)  # 200 per category = 2200 total
    print(f"Generated {len(samples)} samples")

    output_path = os.path.join(os.path.dirname(__file__), 'synthetic_dataset.json')
    save_dataset(samples, output_path)
    print(f"Saved to {output_path}")

    # Print category distribution
    from collections import Counter
    dist = Counter(s['intent_category'] for s in samples)
    for cat, count in dist.items():
        print(f"  {cat}: {count}")