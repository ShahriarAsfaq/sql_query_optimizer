"""
Intent Service - extracts structured intent from natural language and checks intent match.
"""
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, asdict
from enum import Enum
import logging
import re

from ..services.sql_parser import ParsedQuery

logger = logging.getLogger(__name__)


class IntentCategory(Enum):
    """Categories of query intent."""
    RETRIEVE = "RETRIEVE"
    FILTER = "FILTER"
    SORT = "SORT"
    AGGREGATE = "AGGREGATE"
    GROUP = "GROUP"
    TOP_N = "TOP_N"
    JOIN = "JOIN"
    TREND = "TREND"
    RANKING = "RANKING"
    DUPLICATE_DETECTION = "DUPLICATE_DETECTION"
    COMPARISON = "COMPARISON"
    UNKNOWN = "UNKNOWN"


@dataclass
class StructuredIntent:
    """Structured representation of user intent."""
    category: IntentCategory
    operation: str  # SELECT, COUNT, AVG, etc.
    entity: str  # main table/entity
    metric: Optional[str] = None  # column to aggregate
    filters: List[Dict[str, Any]] = None  # filter conditions
    time_range: Optional[Dict[str, Any]] = None  # date filters
    group_by: List[str] = None
    order_by: List[Dict[str, str]] = None  # [{column, direction}]
    limit: Optional[int] = None
    ranking: Optional[str] = None  # RANK, DENSE_RANK, ROW_NUMBER
    comparison: Optional[Dict[str, Any]] = None  # for comparison queries
    join_tables: List[str] = None  # tables to JOIN with entity
    select_columns: List[str] = None  # explicit columns user wants to see
    raw_text: str = ""

    def __post_init__(self):
        if self.filters is None:
            self.filters = []
        if self.group_by is None:
            self.group_by = []
        if self.order_by is None:
            self.order_by = []
        if self.join_tables is None:
            self.join_tables = []
        if self.select_columns is None:
            self.select_columns = []

    def to_dict(self) -> Dict[str, Any]:
        return {
            'category': self.category.value,
            'operation': self.operation,
            'entity': self.entity,
            'metric': self.metric,
            'filters': self.filters,
            'time_range': self.time_range,
            'group_by': self.group_by,
            'order_by': self.order_by,
            'limit': self.limit,
            'ranking': self.ranking,
            'comparison': self.comparison,
            'join_tables': self.join_tables,
            'select_columns': self.select_columns,
            'raw_text': self.raw_text,
        }


class IntentService:
    """
    Service for extracting intent from natural language and checking intent match.
    """

    # Keywords for intent classification
    CATEGORY_KEYWORDS = {
        IntentCategory.TOP_N: ['top', 'highest', 'lowest', 'best', 'worst', 'most', 'least', 'first', 'last'],
        IntentCategory.AGGREGATE: ['average', 'avg', 'sum', 'total', 'count', 'minimum', 'min', 'maximum', 'max', 'mean'],
        IntentCategory.GROUP: ['group by', 'per', 'each', 'by', 'breakdown'],
        IntentCategory.FILTER: ['where', 'filter', 'having', 'only', 'exclude'],
        IntentCategory.SORT: ['sort', 'order', 'ascending', 'descending', 'asc', 'desc'],
        IntentCategory.JOIN: ['join', 'combine', 'merge', 'relate', 'connect', 'with their', 'and their', 'along with their', 'together with their'],
        IntentCategory.TREND: ['trend', 'over time', 'time series', 'daily', 'monthly', 'yearly', 'growth'],
        IntentCategory.RANKING: ['rank', 'ranking', 'percentile', 'quartile'],
        IntentCategory.DUPLICATE_DETECTION: ['duplicate', 'duplicates', 'repeated'],
        IntentCategory.COMPARISON: ['compare', 'versus', 'vs', 'difference', 'diff'],
        IntentCategory.RETRIEVE: ['show', 'get', 'list', 'find', 'select', 'display'],
    }

    def __init__(self, llm_client=None):
        """
        Initialize intent service.

        Args:
            llm_client: Optional LLM client for NL→intent extraction
        """
        self.llm_client = llm_client

    def extract_intent(self, intent_text: str, schema: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extract structured intent from natural language text.

        Args:
            intent_text: Natural language description of what user wants
            schema: Database schema for context

        Returns:
            StructuredIntent as dictionary
        """
        # Try LLM first if available
        if self.llm_client:
            try:
                return self._extract_with_llm(intent_text, schema)
            except Exception as e:
                logger.warning(f"LLM intent extraction failed: {e}, falling back to rule-based")

        # Fallback to rule-based extraction
        return self._extract_rule_based(intent_text, schema)

    def _extract_with_llm(self, intent_text: str, schema: Dict[str, Any]) -> Dict[str, Any]:
        """Extract intent using LLM."""
        schema_summary = self._summarize_schema(schema)

        prompt = f"""Extract structured intent from this natural language query.

Schema:
{schema_summary}

User request: "{intent_text}"

Return JSON with these fields:
- category: one of {', '.join([c.value for c in IntentCategory])}
- operation: SELECT, COUNT, AVG, SUM, MIN, MAX, etc.
- entity: main table name from schema
- metric: column to aggregate (if any)
- filters: list of {{column, operator, value}}
- time_range: {{start, end, column}} if date filtering
- group_by: list of columns
- order_by: list of {{column, direction}} (ASC/DESC)
- limit: integer if top N
- ranking: RANK, DENSE_RANK, ROW_NUMBER if ranking
- comparison: {{type, entities}} if comparison

Return ONLY valid JSON."""

        response = self.llm_client.complete(prompt, max_tokens=800, temperature=0.1)

        # Parse JSON from response
        import json
        try:
            # Extract JSON from response (handle markdown code blocks)
            json_str = response
            if '```json' in response:
                json_str = response.split('```json')[1].split('```')[0]
            elif '```' in response:
                json_str = response.split('```')[1].split('```')[0]

            intent_dict = json.loads(json_str.strip())
            intent_dict['raw_text'] = intent_text
            return intent_dict
        except json.JSONDecodeError:
            logger.error(f"Failed to parse LLM intent response: {response}")
            return self._extract_rule_based(intent_text, schema)

    def _extract_rule_based(self, intent_text: str, schema: Dict[str, Any]) -> Dict[str, Any]:
        """Extract intent using rule-based keyword matching."""
        text_lower = intent_text.lower()

        # Determine category
        category = IntentCategory.RETRIEVE
        max_matches = 0
        for cat, keywords in self.CATEGORY_KEYWORDS.items():
            matches = sum(1 for kw in keywords if kw in text_lower)
            if matches > max_matches:
                max_matches = matches
                category = cat

        # If tied or RETRIEVE with explicit aggregate keywords, prefer AGGREGATE
        aggregate_keywords = ['average', 'avg', 'sum', 'total', 'count', 'minimum', 'min', 'maximum', 'max', 'mean']
        has_aggregate_kw = any(kw in text_lower for kw in aggregate_keywords)
        if has_aggregate_kw and (category == IntentCategory.RETRIEVE or max_matches <= 1):
            # Check if there's a stronger AGGREGATE match
            agg_matches = sum(1 for kw in self.CATEGORY_KEYWORDS[IntentCategory.AGGREGATE] if kw in text_lower)
            if agg_matches >= max_matches:
                category = IntentCategory.AGGREGATE

        # Override: if query has "top N" or "first N" pattern, prefer TOP_N
        import re
        if re.search(r'top\s+\d+', text_lower) or re.search(r'first\s+\d+', text_lower):
            category = IntentCategory.TOP_N

        # Override: if query has JOIN patterns like "X with their Y" or "X and their Y", prefer JOIN
        if category != IntentCategory.TOP_N and category != IntentCategory.AGGREGATE:
            join_patterns = [
                r'\w+\s+with\s+their\s+\w+',
                r'\w+\s+and\s+their\s+\w+',
                r'\w+\s+along\s+with\s+their\s+\w+',
                r'\w+\s+together\s+with\s+their\s+\w+',
            ]
            for pattern in join_patterns:
                if re.search(pattern, text_lower):
                    category = IntentCategory.JOIN
                    break

        # Determine entity (table) - find schema table mentioned in text
        entity = self._find_entity(text_lower, schema)

        # Determine operation
        operation = 'SELECT'
        if category == IntentCategory.AGGREGATE:
            for kw in ['average', 'avg']:
                if kw in text_lower:
                    operation = 'AVG'
                    break
            for kw in ['sum', 'total']:
                if kw in text_lower:
                    operation = 'SUM'
                    break
            for kw in ['count']:
                if kw in text_lower:
                    operation = 'COUNT'
                    break
            for kw in ['minimum', 'min']:
                if kw in text_lower:
                    operation = 'MIN'
                    break
            for kw in ['maximum', 'max']:
                if kw in text_lower:
                    operation = 'MAX'
                    break

        # Extract metric (column to aggregate) - start with None, will be set by patterns
        metric = None
        group_by = []

        # Special handling for "average X", "sum of X", "total X", "count of X" patterns
        if not metric and category in (IntentCategory.AGGREGATE, IntentCategory.TOP_N):
            agg_metric_patterns = [
                (r'(?:average|avg|mean)\s+(?:of\s+)?(\w+)', 'AVG'),
                (r'(?:sum|total)\s+(?:of\s+)?(\w+)', 'SUM'),
                (r'(?:count)\s+(?:of\s+)?(\w+)', 'COUNT'),
                (r'(?:minimum|min)\s+(?:of\s+)?(\w+)', 'MIN'),
                (r'(?:maximum|max)\s+(?:of\s+)?(\w+)', 'MAX'),
                (r'(?:average|avg|mean)\s+(\w+)', 'AVG'),
                (r'(?:sum|total)\s+(\w+)', 'SUM'),
                (r'(?:count)\s+(\w+)', 'COUNT'),
                (r'(?:minimum|min)\s+(\w+)', 'MIN'),
                (r'(?:maximum|max)\s+(\w+)', 'MAX'),
            ]
            for pattern, op in agg_metric_patterns:
                match = re.search(pattern, text_lower)
                if match:
                    potential_metric = match.group(1)
                    # Verify this column exists in the entity, or find the table containing it
                    search_entity = entity
                    if not search_entity or (search_entity and search_entity in schema.get('tables', {})):
                        # First try the detected entity
                        if search_entity and search_entity in schema.get('tables', {}):
                            cols = list(schema['tables'][search_entity].get('columns', {}).keys())
                            for col in cols:
                                if col.lower() == potential_metric.lower():
                                    metric = col
                                    break
                        # If not found, search all tables
                        if not metric and schema.get('tables'):
                            for table_name, table_def in schema.get('tables', {}).items():
                                cols = list(table_def.get('columns', {}).keys())
                                for col in cols:
                                    if col.lower() == potential_metric.lower():
                                        metric = col
                                        search_entity = table_name
                                        break
                                if metric:
                                    break
                        if metric:
                            entity = search_entity
                            operation = op  # Set the aggregate operation
                            break

        # For TOP_N queries, also look for "by average X", "by sum X" etc. to extract metric
        if not metric and category == IntentCategory.TOP_N:
            # First try to find metric using _find_metric on the entity
            metric = self._find_metric(text_lower, schema, entity)
            by_agg_patterns = [
                (r'by\s+(?:average|avg|mean)\s+(\w+)', 'AVG'),
                (r'by\s+(?:sum|total)\s+(\w+)', 'SUM'),
                (r'by\s+(?:count)\s+(\w+)', 'COUNT'),
                (r'by\s+(?:minimum|min)\s+(\w+)', 'MIN'),
                (r'by\s+(?:maximum|max)\s+(\w+)', 'MAX'),
            ]
            for pattern, op in by_agg_patterns:
                match = re.search(pattern, text_lower)
                if match:
                    potential_metric = match.group(1)
                    # Always search for the table containing this metric column
                    search_entity = None
                    if schema.get('tables'):
                        for table_name, table_def in schema.get('tables', {}).items():
                            cols = list(table_def.get('columns', {}).keys())
                            for col in cols:
                                if col.lower() == potential_metric.lower():
                                    search_entity = table_name
                                    break
                            if search_entity:
                                break
                    if search_entity and search_entity in schema.get('tables', {}):
                        cols = list(schema['tables'][search_entity].get('columns', {}).keys())
                        for col in cols:
                            if col.lower() == potential_metric.lower():
                                metric = col
                                operation = op  # Set the aggregate operation
                                # Update entity to the table containing the metric
                                entity = search_entity
                                break
                    if metric:
                        break

        # Additional patterns for common business metrics
        # "total sales", "total revenue", "total amount" -> SUM(total_amount) or SUM(price * quantity)
        if not metric and category in (IntentCategory.AGGREGATE, IntentCategory.TOP_N):
            business_metric_patterns = [
                # Check "by X" pattern FIRST to capture group_by
                (r'total\s+(?:sales|revenue|amount)\s+by\s+([\w\s]+?)(?:\s+(?:where|with|having|order|limit|group)|$)', 'SUM'),  # "total sales by X"
                (r'total\s+(?:sales|revenue|amount)(?!\s+by)', 'SUM'),
                (r'average\s+(?:purchase|order|sale|transaction)\s+(?:amount|value)', 'AVG'),
            ]
            for pattern, op in business_metric_patterns:
                match = re.search(pattern, text_lower)
                if match:
                    # Find table with sales/revenue metrics
                    search_entity = None
                    if schema.get('tables'):
                        for table_name, table_def in schema.get('tables', {}).items():
                            cols = list(table_def.get('columns', {}).keys())
                            # Look for common sales/revenue columns
                            for col in ['total_amount', 'revenue', 'sales', 'amount', 'price', 'unit_price', 'total']:
                                if col in cols:
                                    metric = col
                                    search_entity = table_name
                                    operation = op
                                    break
                            if metric:
                                break
                    # If pattern captured a group_by (e.g., "by product category")
                    if match.groups():
                        group_val = match.group(1).strip()
                        if group_val not in group_by:
                            group_by.append(group_val)
                    if metric:
                        entity = search_entity
                        break

        
        # Extract limit (top N)
        limit = None
        top_match = re.search(r'top\s+(\d+)', text_lower)
        if top_match:
            limit = int(top_match.group(1))
        first_match = re.search(r'first\s+(\d+)', text_lower)
        if first_match:
            limit = int(first_match.group(1))

        # Extract order_by
        order_by = []
        if 'descending' in text_lower or 'desc' in text_lower or 'highest' in text_lower or 'largest' in text_lower:
            col = metric or self._find_metric(text_lower, schema, entity)
            if col:
                order_by.append({'column': col, 'direction': 'DESC'})
        elif 'ascending' in text_lower or 'asc' in text_lower or 'lowest' in text_lower or 'smallest' in text_lower:
            col = metric or self._find_metric(text_lower, schema, entity)
            if col:
                order_by.append({'column': col, 'direction': 'ASC'})

        # For TOP_N with aggregate, default to DESC order by the aggregate
        if not order_by and category == IntentCategory.TOP_N and metric and operation in ('AVG', 'SUM', 'COUNT', 'MIN', 'MAX'):
            # Check if user wants lowest/bottom
            if any(kw in text_lower for kw in ['lowest', 'smallest', 'least', 'bottom']):
                direction = 'ASC'
            else:
                direction = 'DESC'
            # The ORDER BY will use the aggregate alias (e.g., avg_salary)
            agg_alias = f"{operation.lower()}_{metric}"
            order_by.append({'column': agg_alias, 'direction': direction})

        # Extract group_by (if not already populated by business metric patterns)
        # group_by is already initialized earlier
        group_match = re.search(r'(?:group by|per|each)\s+(\w+)', text_lower)
        if group_match:
            group_by.append(group_match.group(1))
        else:
            # Also check for "by X" pattern - but be careful with "average by", "sum by" etc.
            # "top N X by Y" -> X is group_by, Y is metric
            # "average Y by X" -> X is group_by, Y is metric (different pattern)
            # Look for "top N X by" pattern first
            top_n_by_match = re.search(r'top\s+\d+\s+(\w+)\s+by\b', text_lower)
            if top_n_by_match:
                group_by_val = top_n_by_match.group(1)
                # Handle singular/plural - check if it matches a column in schema
                group_by.append(group_by_val)
            else:
                # For other cases, "by X" where X is not preceded by aggregate keyword
                # Capture multiple words after "by" (e.g., "by product category")
                by_match = re.search(r'\bby\s+([\w\s]+?)(?:\s+(?:where|with|having|order|limit|group|$))', text_lower)
                if by_match:
                    by_value = by_match.group(1).strip()
                    # Check if "by" is preceded by aggregate keyword
                    # Find position of "by" and check preceding words
                    by_pos = text_lower.find('by ')
                    preceding = text_lower[max(0, by_pos-20):by_pos].strip()
                    agg_keywords = ['average', 'avg', 'mean', 'sum', 'total', 'count', 'minimum', 'min', 'maximum', 'max']
                    preceded_by_agg = any(preceding.endswith(kw) for kw in agg_keywords)

                    if preceded_by_agg:
                        # This is "aggregate by X" pattern - X is group_by
                        group_by.append(by_value)
                    elif category == IntentCategory.TOP_N:
                        # "by" in TOP_N context usually means "group by this"
                        group_by.append(by_value)
                    else:
                        group_by.append(by_value)

        # Normalize group_by values to match schema column names (singular/plural handling)
        # This should run for ALL cases, not just the else branch
        # If entity is not explicitly mentioned, try to infer from group_by or use first table
        target_entity = entity
        if not target_entity and schema.get('tables'):
            # Try to find entity from group_by columns
            for table_name, table_def in schema.get('tables', {}).items():
                cols = set(table_def.get('columns', {}).keys())
                for gb in group_by:
                    if gb in cols or gb.rstrip('s') in cols or gb + 's' in cols:
                        target_entity = table_name
                        break
                if target_entity:
                    break
            # Fallback to first table
            if not target_entity:
                target_entity = list(schema.get('tables', {}).keys())[0]

        if target_entity and target_entity in schema.get('tables', {}):
            cols = set(schema['tables'][target_entity].get('columns', {}).keys())
            normalized_group_by = []
            for gb in group_by:
                if gb in cols:
                    normalized_group_by.append(gb)
                elif gb.rstrip('s') in cols:
                    normalized_group_by.append(gb.rstrip('s'))
                elif gb + 's' in cols:
                    normalized_group_by.append(gb + 's')
                else:
                    normalized_group_by.append(gb)
            group_by = normalized_group_by

        # For "top N X by Y" pattern where X is a table name (e.g., "departments"),
        # but the metric is in a different table (e.g., "salary" in employees),
        # we should use the column name from the entity table, not the table name
        # Check if group_by values match table names but the entity has a corresponding column
        if entity and group_by and entity in schema.get('tables', {}):
            entity_cols = set(schema['tables'][entity].get('columns', {}).keys())
            adjusted_group_by = []
            for gb in group_by:
                # If group_by is a table name (plural) and entity has singular version as column
                if gb.lower() != entity.lower() and gb.lower().rstrip('s') in entity_cols:
                    adjusted_group_by.append(gb.lower().rstrip('s'))
                elif gb.lower() + 's' == entity.lower() and gb in entity_cols:
                    adjusted_group_by.append(gb)
                else:
                    adjusted_group_by.append(gb)
            group_by = adjusted_group_by

        # Extract filters (simplified) - pass original text for case-sensitive location matching
        filters = self._extract_filters(intent_text, schema, entity)

        # Detect join tables for JOIN category
        join_tables = []
        # Also extract explicit columns the user wants to see
        select_columns = []

        if category == IntentCategory.JOIN:
            # Find all tables mentioned in the text that are not the entity
            text_lower = intent_text.lower()
            tables = list(schema.get('tables', {}).keys())

            # Build mapping for special cases
            table_aliases = {}
            for table in tables:
                table_aliases[table.lower()] = table
                table_aliases[table.lower().rstrip('s')] = table
                table_aliases[table.lower() + 's'] = table

            # Special case mappings (including self-referential like managers -> employees)
            # Only map if the target table exists in schema
            special_mappings = {
                'order': 'purchases',
                'orders': 'purchases',
                'transaction': 'purchases',
                'transactions': 'purchases',
                'sale': 'purchases',
                'sales': 'purchases',
                'manager': 'employees',
                'managers': 'employees',
            }
            # Filter out mappings where target table doesn't exist in schema
            special_mappings = {k: v for k, v in special_mappings.items() if v in schema.get('tables', {})}

            # Also check for special self-referential joins
            # e.g., "managers" when entity is "employees" -> self-join on manager_id
            for alias, table in special_mappings.items():
                if table == entity and alias in text_lower:
                    # This is a self-join case
                    join_tables.append(table + '_self')
                    break

            # First, check for exact table name matches (prefer these over special mappings)
            for table in tables:
                if entity and table.lower() != entity.lower():
                    if table.lower() in text_lower:
                        join_tables.append(table)

            # Then apply special mappings only for tables not already found
            if not join_tables:
                for alias, table in special_mappings.items():
                    if table in schema.get('tables', {}):
                        table_aliases[alias] = table

                for table in tables:
                    if entity and table.lower() != entity.lower():
                        # Check all possible aliases
                        for alias, mapped_table in table_aliases.items():
                            if mapped_table == table and alias in text_lower:
                                join_tables.append(table)
                                break

        # Extract explicit columns from "showing", "display" patterns
        # e.g., "showing id, name, amount and status" or "display name, email"
        select_patterns = [
            # Match "showing X, Y and Z" - "showing" keyword for column selection
            r'showing\s+([\w\s,]+?)(?:\s+(?:from|where|order|group|limit|join)|[.;]|$)',
            # Match "display X, Y and Z" - "display" keyword for column selection
            r'display\s+([\w\s,]+?)(?:\s+(?:from|where|order|group|limit|join|by|on)|[.;]|$)',
            # Match "with X, Y and Z" but only if "with" comes after a period/semicolon
            r'(?:[.;]\s*with)\s+([\w\s,]+?)(?:\s+(?:from|where|order|group|limit|join)|[.;]|$)',
        ]
        for pattern in select_patterns:
            match = re.search(pattern, text_lower)
            if match:
                cols_text = match.group(1)
                # Parse comma-separated and "and"-separated columns
                # First split by comma, then split each part by " and "
                parts = cols_text.split(',')
                for part in parts:
                    part = part.strip()
                    # Split by " and "
                    for col in part.split(' and '):
                        col = col.strip()
                        if col:
                            select_columns.append(col)
                break

        intent = StructuredIntent(
            category=category,
            operation=operation,
            entity=entity or (list(schema.get('tables', {}).keys())[0] if schema.get('tables') else 'unknown'),
            metric=metric,
            filters=filters,
            group_by=group_by,
            order_by=order_by,
            limit=limit,
            join_tables=join_tables,
            select_columns=select_columns,
            raw_text=intent_text,
        )

        return intent.to_dict()

    def _summarize_schema(self, schema: Dict[str, Any]) -> str:
        """Create a brief schema summary for LLM context."""
        lines = []
        for table_name, table_def in schema.get('tables', {}).items():
            cols = list(table_def.get('columns', {}).keys())
            lines.append(f"  {table_name}: {', '.join(cols)}")
        return "\n".join(lines)

    def _find_entity(self, text: str, schema: Dict[str, Any]) -> Optional[str]:
        """Find which table/entity is referenced in the text."""
        text_lower = text.lower()
        tables = list(schema.get('tables', {}).keys())

        # First check for exact table name matches
        for table in tables:
            if table.lower() in text_lower:
                return table
            # Check singular/plural variants
            if table.lower().rstrip('s') in text_lower or table.lower() + 's' in text_lower:
                return table

        # Special case: "sales", "revenue", "purchases" often refers to purchases/transactions table
        if any(kw in text_lower for kw in ['sales', 'revenue', 'purchase', 'transaction', 'order']):
            for table in ['purchases', 'orders', 'transactions', 'sales']:
                if table in schema.get('tables', {}):
                    return table

        # Check for JOIN patterns - look for multiple tables mentioned
        # e.g., "employees with their departments", "customers and their orders"
        join_indicators = ['with', 'and', 'join', 'along with', 'together with']
        mentioned_tables = []
        for table in tables:
            if table.lower() in text_lower:
                mentioned_tables.append(table)
            elif table.lower().rstrip('s') in text_lower:
                mentioned_tables.append(table)
            elif table.lower() + 's' in text_lower:
                mentioned_tables.append(table)

        # If multiple tables mentioned and there's a JOIN indicator, return the first one
        # (the intent extractor will handle the join detection separately)
        if len(mentioned_tables) > 1:
            return mentioned_tables[0]

        return None

    def _find_metric(self, text: str, schema: Dict[str, Any], entity: Optional[str]) -> Optional[str]:
        """Find which column/metric is referenced."""
        if entity and entity in schema.get('tables', {}):
            cols = list(schema['tables'][entity].get('columns', {}).keys())

            # Common synonyms for metrics
            metric_synonyms = {
                'salary': ['salary', 'pay', 'wage', 'compensation', 'income', 'earnings', 'paid'],
                'price': ['price', 'cost', 'amount', 'value'],
                'quantity': ['quantity', 'qty', 'count', 'number', 'amount'],
                'date': ['date', 'time', 'when', 'created'],
                'name': ['name', 'title'],
                'email': ['email', 'mail'],
                'id': ['id', 'identifier'],
            }

            # Check for direct column name match (whole word)
            text_lower = text.lower()
            for col in cols:
                if f" {col.lower()} " in f" {text_lower} " or text_lower.startswith(col.lower() + " ") or text_lower.endswith(" " + col.lower()):
                    return col

            # Check synonyms (whole word match)
            for col in cols:
                col_lower = col.lower()
                if col_lower in metric_synonyms:
                    for synonym in metric_synonyms[col_lower]:
                        # Whole word match for synonym
                        if f" {synonym} " in f" {text_lower} " or text_lower.startswith(synonym + " ") or text_lower.endswith(" " + synonym):
                            return col

            # Fallback: check if any column name is a whole word in text
            import re
            for col in cols:
                # Use word boundary regex for safer matching
                if re.search(r'\b' + re.escape(col.lower()) + r'\b', text_lower):
                    return col
        return None

    def _extract_filters(self, text: str, schema: Dict[str, Any], entity: Optional[str]) -> List[Dict[str, Any]]:
        """Extract filter conditions from text (simplified)."""
        filters = []
        text_lower = text.lower()

        # Look for common patterns: "where X = Y", "with X = Y", "X is Y"
        patterns = [
            r'(?:where|with|having)\s+(\w+)\s*(=|!=|>|<|>=|<=)\s*([\w\'"]+)',
            r'(\w+)\s*(=|!=|>|<|>=|<=)\s*([\w\'"]+)',
        ]

        # Get valid columns for the entity to filter out false matches
        valid_columns = set()
        if entity and entity in schema.get('tables', {}):
            valid_columns = set(schema['tables'][entity].get('columns', {}).keys())

        for pattern in patterns:
            matches = re.findall(pattern, text)
            for match in matches:
                col, op, val = match
                # Only add if column is valid for the entity
                if valid_columns and col not in valid_columns:
                    continue
                # Clean up value
                val = val.strip('\'"')
                try:
                    val = int(val)
                except ValueError:
                    try:
                        val = float(val)
                    except ValueError:
                        pass

                filters.append({
                    'column': col,
                    'operator': op,
                    'value': val
                })

        # Also handle natural language comparisons
        # "greater than", "more than", "above", "exceeds" -> >
        gt_patterns = [
            r'(\w+)\s+(?:greater than|more than|above|exceeds|over)\s+(\d+)',
            r'(?:greater than|more than|above|exceeds|over)\s+(\d+)\s+(?:for|on|in)\s+(\w+)',
        ]

        # "less than", "fewer than", "below", "under" -> <
        lt_patterns = [
            r'(\w+)\s+(?:less than|fewer than|below|under)\s+(\d+)',
            r'(?:less than|fewer than|below|under)\s+(\d+)\s+(?:for|on|in)\s+(\w+)',
        ]

        # Get valid columns for the entity to filter out false matches
        valid_columns = set()
        if entity and entity in schema.get('tables', {}):
            valid_columns = set(schema['tables'][entity].get('columns', {}).keys())

        # "at least", "minimum", "minimum of" -> >=
        gte_patterns = [
            r'(\w+)\s+(?:at least|minimum|minimum of)\s+(\d+)',
        ]

        # "at most", "maximum", "maximum of" -> <=
        lte_patterns = [
            r'(\w+)\s+(?:at most|maximum|maximum of)\s+(\d+)',
        ]

        for pattern in gt_patterns:
            matches = re.findall(pattern, text_lower)
            for match in matches:
                if len(match) == 2:
                    col, val = match
                    # Only add if column is valid for the entity
                    if col in valid_columns:
                        filters.append({
                            'column': col,
                            'operator': '>',
                            'value': int(val)
                        })

        for pattern in lt_patterns:
            matches = re.findall(pattern, text_lower)
            for match in matches:
                if len(match) == 2:
                    col, val = match
                    # Only add if column is valid for the entity
                    if col in valid_columns:
                        filters.append({
                            'column': col,
                            'operator': '<',
                            'value': int(val)
                        })

        for pattern in gte_patterns:
            matches = re.findall(pattern, text_lower)
            for match in matches:
                if len(match) == 2:
                    col, val = match
                    # Only add if column is valid for the entity
                    if col in valid_columns:
                        filters.append({
                            'column': col,
                            'operator': '>=',
                            'value': int(val)
                        })

        for pattern in lte_patterns:
            matches = re.findall(pattern, text_lower)
            for match in matches:
                if len(match) == 2:
                    col, val = match
                    # Only add if column is valid for the entity
                    if col in valid_columns:
                        filters.append({
                            'column': col,
                            'operator': '<=',
                            'value': int(val)
                        })

        # Handle "from X" or "in X" location filters (e.g., "customers from New York")
        location_patterns = [
            r'(?:from|in|located in|based in)\s+([A-Z][a-zA-Z\s]+?)(?:\s+(?:who|where|with|and)|$)',
        ]
        for pattern in location_patterns:
            matches = re.findall(pattern, text)
            for match in matches:
                location = match.strip()
                # Try to match to a location column in the entity
                if entity and entity in schema.get('tables', {}):
                    cols = schema['tables'][entity].get('columns', {})
                    for col in ['city', 'state', 'country', 'location', 'address']:
                        if col in cols:
                            filters.append({
                                'column': col,
                                'operator': '=',
                                'value': location
                            })
                            break

        # Handle "earn more than X" -> salary > X
        earn_pattern = r'earn\s+(?:more than|greater than|over|above)\s+(\d+)'
        matches = re.findall(earn_pattern, text_lower)
        for val in matches:
            filters.append({
                'column': 'salary',
                'operator': '>',
                'value': int(val)
            })

        # Handle "cost more than X" / "price greater than X" -> price/cost > X
        price_pattern = r'(?:cost|price)\s+(?:more than|greater than|over|above)\s+(\d+)'
        matches = re.findall(price_pattern, text_lower)
        for val in matches:
            if entity and entity in schema.get('tables', {}):
                cols = schema['tables'][entity].get('columns', {})
                for col in ['price', 'cost', 'unit_price', 'amount', 'total_amount']:
                    if col in cols:
                        filters.append({
                            'column': col,
                            'operator': '>',
                            'value': int(val)
                        })
                        break

        # Deduplicate filters (same column, operator, value)
        unique_filters = []
        seen = set()
        for f in filters:
            key = (f['column'], f['operator'], f['value'])
            if key not in seen:
                seen.add(key)
                unique_filters.append(f)

        return unique_filters

    def check_intent_match(self, parsed: ParsedQuery, intent_text: str, schema: Dict[str, Any]) -> Dict[str, Any]:
        """
        Check if a parsed query matches the stated intent.

        Args:
            parsed: ParsedQuery from SQL parser
            intent_text: Natural language intent
            schema: Database schema

        Returns:
            Dict with match score and details
        """
        # Extract intent from text
        structured_intent = self.extract_intent(intent_text, schema)

        # Derive implicit intent from parsed query
        implicit_intent = self._derive_implicit_intent(parsed)

        # Compare
        match_score = self._compare_intents(structured_intent, implicit_intent, parsed)

        return {
            'match_score': match_score,
            'stated_intent': structured_intent,
            'implicit_intent': implicit_intent,
            'mismatches': self._find_mismatches(structured_intent, implicit_intent, parsed),
        }

    def _derive_implicit_intent(self, parsed: ParsedQuery) -> Dict[str, Any]:
        """Derive what the query actually does from its structure."""
        intent = {
            'category': IntentCategory.RETRIEVE.value,
            'operation': 'SELECT',
            'entity': parsed.tables[0].name if parsed.tables else None,
            'metric': None,
            'filters': [],
            'group_by': parsed.group_by,
            'order_by': parsed.order_by,
            'limit': parsed.limit,
            'has_joins': len(parsed.joins) > 0,
            'has_aggregates': len(parsed.aggregations) > 0,
            'has_window_functions': len(parsed.window_functions) > 0,
        }

        # Determine category from structure
        if parsed.limit and parsed.order_by:
            intent['category'] = IntentCategory.TOP_N.value
        elif parsed.aggregations:
            intent['category'] = IntentCategory.AGGREGATE.value
        elif parsed.group_by:
            intent['category'] = IntentCategory.GROUP.value
        elif parsed.where_conditions:
            intent['category'] = IntentCategory.FILTER.value
        elif parsed.joins:
            intent['category'] = IntentCategory.JOIN.value
        elif parsed.window_functions:
            intent['category'] = IntentCategory.RANKING.value

        # Extract metric from aggregations
        if parsed.aggregations:
            for agg in parsed.aggregations:
                if 'AVG' in agg.upper() or 'AVERAGE' in agg.upper():
                    intent['operation'] = 'AVG'
                elif 'SUM' in agg.upper():
                    intent['operation'] = 'SUM'
                elif 'COUNT' in agg.upper():
                    intent['operation'] = 'COUNT'
                elif 'MIN' in agg.upper():
                    intent['operation'] = 'MIN'
                elif 'MAX' in agg.upper():
                    intent['operation'] = 'MAX'

        # Extract filters
        for cond in parsed.where_conditions:
            intent['filters'].append({
                'column': cond.column,
                'operator': cond.operator,
                'value': cond.value,
                'table': cond.table
            })

        return intent

    def _compare_intents(self, stated: Dict[str, Any], implicit: Dict[str, Any], parsed: ParsedQuery) -> float:
        """Compare stated vs implicit intent, return 0-1 score."""
        score = 1.0
        mismatches = 0
        total_checks = 0

        # Check category match
        total_checks += 1
        if stated.get('category') != implicit.get('category'):
            mismatches += 1

        # Check entity match
        total_checks += 1
        if stated.get('entity') and implicit.get('entity'):
            if stated['entity'].lower() != implicit['entity'].lower():
                mismatches += 1

        # Check operation match
        total_checks += 1
        if stated.get('operation') != implicit.get('operation'):
            mismatches += 1

        # Check metric match
        total_checks += 1
        if stated.get('metric') and implicit.get('metric'):
            if stated['metric'].lower() != implicit['metric'].lower():
                mismatches += 0.5  # Partial mismatch

        # Check limit match
        total_checks += 1
        if stated.get('limit') and implicit.get('limit'):
            if stated['limit'] != implicit['limit']:
                mismatches += 0.5

        # Check order_by direction match
        total_checks += 1
        stated_order = stated.get('order_by', [])
        implicit_order = implicit.get('order_by', [])
        if stated_order and implicit_order:
            if stated_order[0].get('direction') != implicit_order[0].get('direction'):
                mismatches += 0.5

        # Check group_by match
        total_checks += 1
        if set(stated.get('group_by', [])) != set(implicit.get('group_by', [])):
            mismatches += 0.5

        # Check filter coverage
        total_checks += 1
        stated_filters = stated.get('filters', [])
        implicit_filters = implicit.get('filters', [])
        if stated_filters:
            # Check if all stated filters appear in implicit
            for sf in stated_filters:
                found = False
                for inf in implicit_filters:
                    if (sf.get('column', '').lower() == inf.get('column', '').lower() and
                        sf.get('operator') == inf.get('operator')):
                        found = True
                        break
                if not found:
                    mismatches += 0.5
                    break

        if total_checks > 0:
            score = max(0.0, 1.0 - (mismatches / total_checks))

        return round(score, 2)

    def _find_mismatches(self, stated: Dict[str, Any], implicit: Dict[str, Any], parsed: ParsedQuery) -> List[Dict[str, Any]]:
        """Find specific mismatches between stated and implicit intent."""
        mismatches = []

        if stated.get('category') != implicit.get('category'):
            mismatches.append({
                'type': 'category_mismatch',
                'stated': stated.get('category'),
                'actual': implicit.get('category'),
                'message': f"Query appears to be {implicit.get('category').lower()}, but you asked for {stated.get('category').lower()}"
            })

        if stated.get('entity') and implicit.get('entity'):
            if stated['entity'].lower() != implicit['entity'].lower():
                mismatches.append({
                    'type': 'entity_mismatch',
                    'stated': stated['entity'],
                    'actual': implicit['entity'],
                    'message': f"Query targets '{implicit['entity']}', but you mentioned '{stated['entity']}'"
                })

        if stated.get('limit') and implicit.get('limit'):
            if stated['limit'] != implicit['limit']:
                mismatches.append({
                    'type': 'limit_mismatch',
                    'stated': stated['limit'],
                    'actual': implicit['limit'],
                    'message': f"You asked for top {stated['limit']}, but query returns {implicit['limit']} rows"
                })

        # Check for missing filters
        stated_filters = stated.get('filters', [])
        implicit_filters = implicit.get('filters', [])
        for sf in stated_filters:
            found = False
            for inf in implicit_filters:
                if (sf.get('column', '').lower() == inf.get('column', '').lower() and
                    sf.get('operator') == inf.get('operator')):
                    found = True
                    break
            if not found:
                mismatches.append({
                    'type': 'missing_filter',
                    'filter': sf,
                    'message': f"Filter on {sf.get('column')} {sf.get('operator')} {sf.get('value')} not found in query"
                })

        return mismatches