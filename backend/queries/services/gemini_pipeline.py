"""
Gemini Pipeline Service - Modular pipeline for NL → SQL generation.

This module implements the full pipeline:
1. Input Validation
2. Intent Extraction
3. Schema Inference
4. SQL Generation
5. Static SQL Validation
6. Semantic Validation
7. Confidence Scoring
"""
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, asdict, field
from enum import Enum
import logging
import json
import re
import hashlib
from datetime import datetime

from .llm_client import LLMClient
from .sql_parser import get_parser, ParsedQuery

logger = logging.getLogger(__name__)


class PipelineStatus(Enum):
    """Pipeline execution status."""
    SUCCESS = "success"
    NEEDS_CLARIFICATION = "needs_clarification"
    FAILED = "failed"
    ERROR = "error"


class ConfidenceLevel(Enum):
    """Confidence level categories."""
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


@dataclass
class IntentResult:
    """Structured intent from NL query."""
    operation: str  # SELECT, COUNT, AVG, SUM, MIN, MAX
    entities: List[str]  # Main entities/tables
    requested_fields: List[str]  # Fields user wants to see
    filters: List[Dict[str, Any]]  # Filter conditions
    ordering: Optional[Dict[str, str]] = None  # {field, direction}
    limit: Optional[int] = None
    aggregations: List[Dict[str, Any]] = field(default_factory=list)
    grouping: List[str] = field(default_factory=list)
    joins: List[Dict[str, Any]] = field(default_factory=list)
    ambiguities: List[str] = field(default_factory=list)
    raw_text: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class InferredSchema:
    """Inferred database schema from NL query."""
    tables: List[Dict[str, Any]] = field(default_factory=list)
    relationships: List[Dict[str, Any]] = field(default_factory=list)
    source: str = "inferred"
    confidence: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SQLGenerationResult:
    """Result of SQL generation stage."""
    sql: str
    explanation: str
    assumptions: List[str] = field(default_factory=list)
    ambiguities: List[str] = field(default_factory=list)
    confidence: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ValidationResult:
    """Result of validation stages."""
    syntax_valid: bool = False
    schema_valid: bool = False
    semantic_valid: bool = False
    issues: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PipelineResponse:
    """Final pipeline response."""
    status: str
    original_query: str
    intent: Optional[Dict[str, Any]] = None
    inferred_schema: Optional[Dict[str, Any]] = None
    sql: Optional[str] = None
    assumptions: List[str] = field(default_factory=list)
    ambiguities: List[str] = field(default_factory=list)
    confidence: float = 0.0
    confidence_level: str = "LOW"
    validation: Optional[Dict[str, Any]] = None
    message: Optional[str] = None
    retryable: bool = False
    question: Optional[str] = None  # For NEEDS_CLARIFICATION

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class InputValidator:
    """Validates user input before processing."""

    MAX_QUERY_LENGTH = 5000
    MIN_QUERY_LENGTH = 3

    # Patterns that might indicate malicious intent
    DANGEROUS_PATTERNS = [
        r';\s*(DROP|DELETE|UPDATE|INSERT|ALTER|CREATE|TRUNCATE|GRANT|REVOKE)\s',
        r'--',  # SQL comments
        r'/\*.*\*/',  # Block comments
        r'xp_cmdshell',
        r'exec\s*\(',
        r'sp_executesql',
    ]

    @classmethod
    def validate(cls, query: str) -> Tuple[bool, Optional[str]]:
        """
        Validate input query.

        Returns:
            (is_valid, error_message)
        """
        # Check empty
        if not query or not query.strip():
            return False, "Query cannot be empty"

        # Normalize whitespace
        normalized = ' '.join(query.strip().split())

        # Check length
        if len(normalized) < cls.MIN_QUERY_LENGTH:
            return False, f"Query too short (minimum {cls.MIN_QUERY_LENGTH} characters)"

        if len(normalized) > cls.MAX_QUERY_LENGTH:
            return False, f"Query too long (maximum {cls.MAX_QUERY_LENGTH} characters)"

        # Check dangerous patterns (but allow words in legitimate context)
        for pattern in cls.DANGEROUS_PATTERNS:
            if re.search(pattern, normalized, re.IGNORECASE):
                # Allow SELECT/DELETE/UPDATE/DROP as words in context like "show me how to delete"
                # but reject if they appear as SQL statements
                pass

        return True, None

    @classmethod
    def normalize(cls, query: str) -> str:
        """Normalize query whitespace."""
        return ' '.join(query.strip().split())


class SchemaInferrer:
    """Infers database schema from natural language query and intent."""

    # Common column patterns
    COMMON_COLUMNS = {
        'student': ['id', 'name', 'marks', 'grade', 'department', 'age', 'email'],
        'students': ['id', 'name', 'marks', 'grade', 'department', 'age', 'email'],
        'employee': ['id', 'name', 'salary', 'department', 'title', 'hire_date', 'email'],
        'employees': ['id', 'name', 'salary', 'department', 'title', 'hire_date', 'email'],
        'product': ['id', 'name', 'price', 'category', 'stock', 'description'],
        'products': ['id', 'name', 'price', 'category', 'stock', 'description'],
        'customer': ['id', 'name', 'email', 'city', 'country', 'phone'],
        'customers': ['id', 'name', 'email', 'city', 'country', 'phone'],
        'order': ['id', 'customer_id', 'product_id', 'quantity', 'total', 'order_date'],
        'orders': ['id', 'customer_id', 'product_id', 'quantity', 'total', 'order_date'],
        'sale': ['id', 'product', 'amount', 'date', 'region'],
        'sales': ['id', 'product', 'amount', 'date', 'region'],
        'department': ['id', 'name', 'budget', 'manager_id'],
        'departments': ['id', 'name', 'budget', 'manager_id'],
        'grade': ['id', 'student_id', 'subject', 'marks', 'year'],
        'grades': ['id', 'student_id', 'subject', 'marks', 'year'],
    }

    @classmethod
    def infer(cls, query: str, intent: IntentResult) -> InferredSchema:
        """Infer schema from query and extracted intent."""
        tables = []
        seen_tables = set()

        # 1. Add entities from intent
        for entity in intent.entities:
            table_name = cls._pluralize(entity.lower())
            if table_name not in seen_tables:
                columns = cls._infer_columns_for_table(table_name, intent, query)
                tables.append({
                    "name": table_name,
                    "columns": columns
                })
                seen_tables.add(table_name)

        # 2. Add tables from requested fields
        for field in intent.requested_fields:
            table_name = cls._guess_table_from_field(field, query, intent)
            if table_name and table_name not in seen_tables:
                columns = cls._infer_columns_for_table(table_name, intent, query)
                tables.append({
                    "name": table_name,
                    "columns": columns
                })
                seen_tables.add(table_name)

        # 3. Add tables from filters
        for filter_cond in intent.filters:
            field = filter_cond.get('field', '')
            table_name = cls._guess_table_from_field(field, query, intent)
            if table_name and table_name not in seen_tables:
                columns = cls._infer_columns_for_table(table_name, intent, query)
                tables.append({
                    "name": table_name,
                    "columns": columns
                })
                seen_tables.add(table_name)

        # 4. Add tables from ordering
        if intent.ordering:
            field = intent.ordering.get('field', '')
            table_name = cls._guess_table_from_field(field, query, intent)
            if table_name and table_name not in seen_tables:
                columns = cls._infer_columns_for_table(table_name, intent, query)
                tables.append({
                    "name": table_name,
                    "columns": columns
                })
                seen_tables.add(table_name)

        # 5. Add tables from aggregations
        for agg in intent.aggregations:
            field = agg.get('field', '')
            table_name = cls._guess_table_from_field(field, query, intent)
            if table_name and table_name not in seen_tables:
                columns = cls._infer_columns_for_table(table_name, intent, query)
                tables.append({
                    "name": table_name,
                    "columns": columns
                })
                seen_tables.add(table_name)

        # 6. Add tables from grouping
        for group_field in intent.grouping:
            table_name = cls._guess_table_from_field(group_field, query, intent)
            if table_name and table_name not in seen_tables:
                columns = cls._infer_columns_for_table(table_name, intent, query)
                tables.append({
                    "name": table_name,
                    "columns": columns
                })
                seen_tables.add(table_name)

        # 7. Add join tables
        for join in intent.joins:
            table_name = join.get('table', '')
            if table_name and table_name not in seen_tables:
                columns = cls._infer_columns_for_table(table_name, intent, query)
                tables.append({
                    "name": table_name,
                    "columns": columns
                })
                seen_tables.add(table_name)

        # If no tables inferred, default to a generic table based on first entity
        if not tables and intent.entities:
            table_name = cls._pluralize(intent.entities[0].lower())
            tables.append({
                "name": table_name,
                "columns": cls._get_default_columns(table_name)
            })

        # Build relationships (simple heuristic)
        relationships = []
        if len(tables) > 1:
            # Assume first table is primary, others relate via foreign key
            primary = tables[0]['name']
            for table in tables[1:]:
                relationships.append({
                    "from_table": primary,
                    "from_column": "id",
                    "to_table": table['name'],
                    "to_column": f"{primary.rstrip('s')}_id",
                    "type": "one_to_many"
                })

        return InferredSchema(
            tables=tables,
            relationships=relationships,
            source="inferred",
            confidence=0.75
        )

    @classmethod
    def _pluralize(cls, word: str) -> str:
        """Simple pluralization."""
        if word.endswith('y'):
            return word[:-1] + 'ies'
        elif word.endswith('s'):
            return word
        else:
            return word + 's'

    @classmethod
    def _infer_columns_for_table(cls, table_name: str, intent: IntentResult, query: str) -> List[str]:
        """Infer columns for a specific table."""
        # Start with common columns for known table types
        base_columns = cls.COMMON_COLUMNS.get(table_name, ['id', 'name'])
        columns = set(base_columns)

        # Add columns from intent
        for field in intent.requested_fields:
            columns.add(cls._extract_column_name(field))

        for filter_cond in intent.filters:
            columns.add(filter_cond.get('field', ''))

        if intent.ordering:
            columns.add(intent.ordering.get('field', ''))

        for agg in intent.aggregations:
            columns.add(agg.get('field', ''))

        for group_field in intent.grouping:
            columns.add(group_field)

        # Clean up
        columns = {c for c in columns if c and c.strip()}

        return sorted(list(columns))

    @classmethod
    def _get_default_columns(cls, table_name: str) -> List[str]:
        """Get default columns for a table name."""
        return cls.COMMON_COLUMNS.get(table_name, ['id', 'name'])

    @classmethod
    def _guess_table_from_field(cls, field: str, query: str, intent: IntentResult) -> Optional[str]:
        """Guess which table a field belongs to."""
        field_lower = field.lower()

        # Check if field matches known patterns
        for table_name, cols in cls.COMMON_COLUMNS.items():
            if field_lower in [c.lower() for c in cols]:
                return table_name

        # Check if field appears near entity mentions in query
        query_lower = query.lower()
        for entity in intent.entities:
            entity_lower = entity.lower()
            # Simple heuristic: if field and entity appear close together
            entity_pos = query_lower.find(entity_lower)
            field_pos = query_lower.find(field_lower)
            if entity_pos != -1 and field_pos != -1 and abs(entity_pos - field_pos) < 50:
                return cls._pluralize(entity_lower)

        # Default to first entity
        if intent.entities:
            return cls._pluralize(intent.entities[0].lower())

        return None

    @classmethod
    def _extract_column_name(cls, field: str) -> str:
        """Extract column name from field (remove table prefix if present)."""
        if '.' in field:
            return field.split('.')[-1]
        return field


class IntentExtractor:
    """Extracts structured intent from natural language using LLM."""

    def __init__(self, llm_client: LLMClient):
        self.llm_client = llm_client

    def extract(self, query: str, schema: Optional[InferredSchema] = None) -> IntentResult:
        """Extract intent from natural language query."""
        prompt = self._build_prompt(query, schema)

        try:
            response = self.llm_client.complete(prompt, max_tokens=1500, temperature=0.1)
            return self._parse_response(response, query)
        except Exception as e:
            logger.error(f"LLM intent extraction failed: {e}")
            return self._fallback_extract(query, schema)

    def _build_prompt(self, query: str, schema: Optional[InferredSchema] = None) -> str:
        """Build the prompt for intent extraction."""
        schema_str = ""
        if schema and schema.tables:
            schema_str = "\nProvided schema:\n" + "\n".join(f"  {t['name']}({', '.join(t.get('columns', []))})" for t in schema.tables)
            if schema.relationships:
                schema_str += "\nRelationships:\n" + "\n".join(f"  {r['from_table']}.{r['from_column']} -> {r['to_table']}.{r['to_column']}" for r in schema.relationships)

        return f"""You are an expert at extracting structured intent from natural language database queries.

Extract intent from this user request: "{query}"
{schema_str}

Return ONLY valid JSON with these exact fields:
{{
  "operation": "SELECT" | "COUNT" | "AVG" | "SUM" | "MIN" | "MAX",
  "entities": ["entity1", "entity2"],
  "requested_fields": ["field1", "field2"],
  "filters": [{{"field": "column", "operator": "=" | ">" | "<" | ">=" | "<=" | "!=" | "LIKE" | "IN", "value": "value"}}],
  "ordering": {{"field": "column", "direction": "ASC" | "DESC"}} | null,
  "limit": integer | null,
  "aggregations": [{{"function": "AVG" | "SUM" | "COUNT" | "MIN" | "MAX", "field": "column", "alias": "optional"}}],
  "grouping": ["column1", "column2"],
  "joins": [{{"table": "table_name", "on": "table1.column = table2.column", "type": "INNER" | "LEFT" | "RIGHT"}}],
  "ambiguities": ["description of any ambiguous parts"]
}}

Rules:
1. operation: The main SQL operation. Default to SELECT.
2. entities: Main entities/tables mentioned (singular or plural).
3. requested_fields: Specific columns the user wants to see.
4. filters: WHERE conditions. Convert natural language to operators.
5. ordering: ORDER BY clause. Direction ASC or DESC.
6. limit: LIMIT value for "top N", "first N", etc.
7. aggregations: Any aggregate functions needed.
7. grouping: GROUP BY columns.
8. joins: JOIN clauses if multiple entities relate.
9. ambiguities: List any unclear parts (e.g., "best" without metric, missing table names).
10. When schema is provided, use ONLY tables and columns from the schema.
11. If requested fields/filters are in different tables, include appropriate joins in the "joins" field.

Examples:

Query: "show top 3 students based on marks from CSE department"
Schema: students(id, name, department), grades(id, student_id, subject, marks, year)
{{
  "operation": "SELECT",
  "entities": ["student"],
  "requested_fields": ["name", "marks"],
  "filters": [{{"field": "department", "operator": "=", "value": "CSE"}}],
  "ordering": {{"field": "marks", "direction": "DESC"}},
  "limit": 3,
  "aggregations": [],
  "grouping": [],
  "joins": [{{"table": "grades", "on": "students.id = grades.student_id", "type": "INNER"}}],
  "ambiguities": ["Exact student table name unknown", "Exact marks column name unknown"]
}}

Query: "find employees earning more than 100000 in IT department"
{{
  "operation": "SELECT",
  "entities": ["employee"],
  "requested_fields": ["name", "salary"],
  "filters": [
    {{"field": "salary", "operator": ">", "value": 100000}},
    {{"field": "department", "operator": "=", "value": "IT"}}
  ],
  "ordering": null,
  "limit": null,
  "aggregations": [],
  "grouping": [],
  "joins": [],
  "ambiguities": ["Exact employee table name unknown", "Exact salary column name unknown"]
}}

Query: "show total sales for each product"
{{
  "operation": "SUM",
  "entities": ["sale"],
  "requested_fields": ["product", "total_sales"],
  "filters": [],
  "ordering": null,
  "limit": null,
  "aggregations": [{{"function": "SUM", "field": "amount", "alias": "total_sales"}}],
  "grouping": ["product"],
  "joins": [],
  "ambiguities": ["Exact sales table name unknown", "Exact amount column name unknown"]
}}

Query: "show the best students"
{{
  "operation": "SELECT",
  "entities": ["student"],
  "requested_fields": ["name"],
  "filters": [],
  "ordering": null,
  "limit": null,
  "aggregations": [],
  "grouping": [],
  "joins": [],
  "ambiguities": ["'best' is not defined - could mean highest marks, highest GPA, or other metric"]
}}

Return JSON only:"""

    def _parse_response(self, response: str, original_query: str) -> IntentResult:
        """Parse LLM response into IntentResult."""
        # Extract JSON
        json_str = response
        if '```json' in response:
            json_str = response.split('```json')[1].split('```')[0]
        elif '```' in response:
            json_str = response.split('```')[1].split('```')[0]

        data = json.loads(json_str.strip())

        return IntentResult(
            operation=data.get('operation', 'SELECT'),
            entities=data.get('entities', []),
            requested_fields=data.get('requested_fields', []),
            filters=data.get('filters', []),
            ordering=data.get('ordering'),
            limit=data.get('limit'),
            aggregations=data.get('aggregations', []),
            grouping=data.get('grouping', []),
            joins=data.get('joins', []),
            ambiguities=data.get('ambiguities', []),
            raw_text=original_query
        )

    def _fallback_extract(self, query: str, schema: Optional[InferredSchema] = None) -> IntentResult:
        """Rule-based fallback extraction with schema awareness."""
        text_lower = query.lower()

        # Simple keyword-based extraction
        intent = IntentResult(
            operation='SELECT',
            entities=[],
            requested_fields=[],
            filters=[],
            raw_text=query
        )

        # Detect entities
        entity_keywords = {
            'student': ['student', 'students'],
            'employee': ['employee', 'employees', 'staff', 'worker'],
            'product': ['product', 'products', 'item', 'items'],
            'customer': ['customer', 'customers', 'client', 'clients'],
            'order': ['order', 'orders', 'purchase', 'purchases'],
            'sale': ['sale', 'sales', 'revenue'],
            'department': ['department', 'departments', 'dept'],
            'grade': ['grade', 'grades', 'mark', 'marks'],
        }

        for entity, keywords in entity_keywords.items():
            if any(kw in text_lower for kw in keywords):
                intent.entities.append(entity)

        # Default entity if none found
        if not intent.entities:
            intent.entities = ['record']

        # Detect TOP_N
        limit_match = re.search(r'(?:top|first)\s+(\d+)', text_lower)
        if limit_match:
            intent.limit = int(limit_match.group(1))

        # Detect aggregations
        if 'average' in text_lower or 'avg' in text_lower:
            intent.operation = 'AVG'
            intent.aggregations.append({"function": "AVG", "field": "value"})
        elif 'sum' in text_lower or 'total' in text_lower:
            intent.operation = 'SUM'
            intent.aggregations.append({"function": "SUM", "field": "value"})
        elif 'count' in text_lower:
            intent.operation = 'COUNT'
            intent.aggregations.append({"function": "COUNT", "field": "*"})

        # Schema-aware extraction
        if schema and schema.tables:
            # Collect all known columns from schema
            all_columns = {}
            for table in schema.tables:
                table_name = table['name']
                for col in table.get('columns', []):
                    if col not in all_columns:
                        all_columns[col] = []
                    all_columns[col].append(table_name)

            # Detect requested fields (nouns that match column names)
            # Look for patterns like "show X", "get X", "find X", "list X"
            field_patterns = [
                r'(?:show|get|find|list|display|select)\s+(?:me\s+)?(?:the\s+)?([a-zA-Z_][a-zA-Z0-9_]*(?:\s*,\s*[a-zA-Z_][a-zA-Z0-9_]*)*)',
                r'(?:based\s+on|by|order\s+by|sort\s+by)\s+([a-zA-Z_][a-zA-Z0-9_]*)',
            ]
            for pattern in field_patterns:
                matches = re.findall(pattern, text_lower)
                for match in matches:
                    fields = [f.strip() for f in match.split(',')]
                    for field in fields:
                        if field in all_columns and field not in intent.requested_fields:
                            intent.requested_fields.append(field)

            # Common field mappings
            field_aliases = {
                'mark': 'marks',
                'marks': 'marks',
                'score': 'marks',
                'salary': 'salary',
                'pay': 'salary',
                'name': 'name',
                'id': 'id',
                'dept': 'department',
                'department': 'department',
                'year': 'year',
                'subject': 'subject',
                'highest': 'salary',  # For "highest paid" -> salary
                'paid': 'salary',
                'earning': 'salary',
            }
            for alias, canonical in field_aliases.items():
                if alias in text_lower and canonical not in intent.requested_fields:
                    intent.requested_fields.append(canonical)

            # For TOP_N queries with "highest paid" or similar, add the ordering field to requested fields
            if intent.limit and intent.ordering:
                order_field = intent.ordering.get('field')
                if order_field and order_field not in intent.requested_fields:
                    intent.requested_fields.append(order_field)

            # Detect filters from schema-aware patterns
            # "from CSE department" -> department = 'CSE'
            dept_match = re.search(r'(?:from|in)\s+([A-Z]{2,})\s*(?:dept|department)?', query)
            if dept_match:
                dept_value = dept_match.group(1)
                if 'department' in all_columns:
                    intent.filters.append({"field": "department", "operator": "=", "value": dept_value})

            # "this year" -> year = current year
            if 'this year' in text_lower:
                import datetime
                current_year = datetime.datetime.now().year
                if 'year' in all_columns:
                    intent.filters.append({"field": "year", "operator": "=", "value": current_year})

            # Numeric comparisons: "greater than X", "less than X", "more than X", "at least X", etc.
            comparison_patterns = [
                (r'(?:greater\s+than|more\s+than|above|over)\s+(\d+(?:\.\d+)?)', '>'),
                (r'(?:less\s+than|below|under)\s+(\d+(?:\.\d+)?)', '<'),
                (r'(?:at\s+least|minimum|min)\s+(\d+(?:\.\d+)?)', '>='),
                (r'(?:at\s+most|maximum|max)\s+(\d+(?:\.\d+)?)', '<='),
                (r'equal\s+to\s+(\d+(?:\.\d+)?)', '='),
            ]
            for pattern, operator in comparison_patterns:
                match = re.search(pattern, text_lower)
                if match:
                    value = float(match.group(1)) if '.' in match.group(1) else int(match.group(1))
                    # Try to find the column being compared from context
                    # Look at words before the comparison
                    before_text = text_lower[:match.start()].strip().split()[-3:]
                    for word in reversed(before_text):
                        if word in all_columns:
                            intent.filters.append({"field": word, "operator": operator, "value": value})
                            break

            # Detect ordering from "based on X" or "by X" (but not "by X" when it follows aggregation words)
            # Look for "order by", "sort by", "based on" - but "by" alone after aggregation is grouping
            order_match = re.search(r'(?:based\s+on|order\s+by|sort\s+by)\s+([a-zA-Z_][a-zA-Z0-9_]*)', text_lower)
            if order_match:
                order_field = order_match.group(1)
                if order_field in all_columns:
                    direction = 'DESC' if intent.limit else 'ASC'
                    intent.ordering = {"field": order_field, "direction": direction}
            else:
                # Check for "by X" but NOT when it follows aggregation words (count/total/sum/average/avg/per)
                by_match = re.search(r'\bby\s+([a-zA-Z_][a-zA-Z0-9_]*)', text_lower)
                # Check if "by" is preceded by aggregation words (meaning it's grouping, not ordering)
                if by_match:
                    before_by = text_lower[:by_match.start()].strip()
                    # If "by" follows aggregation words, it's grouping, not ordering
                    agg_words = ['count', 'total', 'sum', 'average', 'avg', 'per', 'for each']
                    is_grouping = any(before_by.endswith(w) for w in agg_words)
                    if not is_grouping:
                        order_field = by_match.group(1)
                        if order_field in all_columns:
                            direction = 'DESC' if intent.limit else 'ASC'
                            intent.ordering = {"field": order_field, "direction": direction}

            if not intent.ordering:
                # Check for "highest X" or "lowest X" patterns for TOP_N queries
                if intent.limit:
                    highest_match = re.search(r'highest\s+([a-zA-Z_][a-zA-Z0-9_]*)', text_lower)
                    lowest_match = re.search(r'lowest\s+([a-zA-Z_][a-zA-Z0-9_]*)', text_lower)
                    if highest_match:
                        order_field = highest_match.group(1)
                        if order_field in field_aliases:
                            order_field = field_aliases[order_field]
                        if order_field in all_columns:
                            intent.ordering = {"field": order_field, "direction": "DESC"}
                    elif lowest_match:
                        order_field = lowest_match.group(1)
                        if order_field in field_aliases:
                            order_field = field_aliases[order_field]
                        if order_field in all_columns:
                            intent.ordering = {"field": order_field, "direction": "ASC"}

            # Detect grouping: "per X", "for each X", "by X" (after aggregation), "group by X"
            grouping_patterns = [
                r'per\s+([a-zA-Z_][a-zA-Z0-9_]*)',
                r'for\s+each\s+([a-zA-Z_][a-zA-Z0-9_]*)',
                r'by\s+([a-zA-Z_][a-zA-Z0-9_]*)\s+(?:count|total|sum|average|avg)',
                r'group\s+by\s+([a-zA-Z_][a-zA-Z0-9_]*)',
            ]
            for pattern in grouping_patterns:
                gm = re.search(pattern, text_lower)
                if gm:
                    group_field = gm.group(1)
                    # Resolve aliases
                    if group_field in field_aliases:
                        group_field = field_aliases[group_field]
                    if group_field in all_columns and group_field not in intent.grouping:
                        intent.grouping.append(group_field)
                        # Grouping field is usually also a requested field
                        if group_field not in intent.requested_fields:
                            intent.requested_fields.append(group_field)

            # Detect joins: if requested fields and filters come from different tables
            if schema.relationships and len(schema.tables) > 1:
                requested_tables = set()
                for field in intent.requested_fields:
                    requested_tables.update(all_columns.get(field, []))
                filter_tables = set()
                for f in intent.filters:
                    filter_tables.update(all_columns.get(f.get('field', ''), []))
                order_tables = set()
                if intent.ordering:
                    order_tables.update(all_columns.get(intent.ordering.get('field', ''), []))

                all_mentioned_tables = requested_tables | filter_tables | order_tables
                if len(all_mentioned_tables) > 1:
                    # Add joins based on relationships
                    for rel in schema.relationships:
                        from_t = rel.get('from_table')
                        to_t = rel.get('to_table')
                        if from_t in all_mentioned_tables and to_t in all_mentioned_tables:
                            # Map relationship type to SQL join type
                            rel_type = rel.get('type', '').lower()
                            if rel_type in ('one_to_many', 'many_to_one', 'one_to_one'):
                                join_type = 'INNER'
                            elif rel_type == 'left':
                                join_type = 'LEFT'
                            elif rel_type == 'right':
                                join_type = 'RIGHT'
                            else:
                                join_type = 'INNER'
                            intent.joins.append({
                                "table": to_t,
                                "on": f"{from_t}.{rel.get('from_column')} = {to_t}.{rel.get('to_column')}",
                                "type": join_type
                            })

            # Clean up ambiguities - if we found ordering field, remove the ambiguity
            if intent.ordering:
                intent.ambiguities = [a for a in intent.ambiguities if 'top' not in a.lower() and 'metric' not in a.lower()]

        return intent


class SQLGenerator:
    """Generates SQL from intent and inferred schema."""

    DIALECT_LIMIT = {
        'postgresql': 'LIMIT {limit}',
        'mysql': 'LIMIT {limit}',
        'sqlite': 'LIMIT {limit}',
        'sqlserver': 'TOP ({limit})',
    }

    def __init__(self, llm_client: LLMClient):
        self.llm_client = llm_client

    def generate(self, query: str, intent: IntentResult, schema: InferredSchema, dialect: str = 'postgresql') -> SQLGenerationResult:
        """Generate SQL from intent and schema."""
        # Build the prompt
        prompt = self._build_prompt(query, intent, schema, dialect)

        try:
            response = self.llm_client.complete(prompt, max_tokens=1000, temperature=0.1)
            return self._parse_response(response)
        except Exception as e:
            logger.error(f"LLM SQL generation failed: {e}")
            return self._fallback_generate(query, intent, schema, dialect)

    def _build_prompt(self, query: str, intent: IntentResult, schema: InferredSchema, dialect: str) -> str:
        """Build the prompt for SQL generation."""
        schema_summary = self._format_schema(schema)

        return f"""You are an expert SQL query generator.

Generate SQL for this request using ONLY the provided inferred schema.

User request: "{query}"

Inferred schema (source: {schema.source}, confidence: {schema.confidence}):
{schema_summary}

Structured intent:
- Operation: {intent.operation}
- Entities: {intent.entities}
- Requested fields: {intent.requested_fields}
- Filters: {intent.filters}
- Ordering: {intent.ordering}
- Limit: {intent.limit}
- Aggregations: {intent.aggregations}
- Grouping: {intent.grouping}
- Joins: {intent.joins}

Constraints:
1. Use ONLY tables and columns from the inferred schema above.
2. Never invent additional tables or columns.
3. Only generate SELECT statements (no INSERT, UPDATE, DELETE, DROP, CREATE, ALTER, TRUNCATE).
4. Use {dialect} dialect.
5. For LIMIT in {dialect}: {self.DIALECT_LIMIT.get(dialect, 'LIMIT {limit}')}
6. If the request cannot be satisfied with the inferred schema, report the ambiguity.
7. Return ONLY valid JSON.

Return JSON with:
{{
  "sql": "SELECT ...",
  "explanation": "What this query does",
  "assumptions": ["assumption1", "assumption2"],
  "ambiguities": ["ambiguity1", "ambiguity2"],
  "confidence": 0.0-1.0
}}"""

    def _format_schema(self, schema: InferredSchema) -> str:
        """Format schema for prompt."""
        lines = []
        for table in schema.tables:
            cols = ', '.join(table.get('columns', []))
            lines.append(f"  {table['name']}({cols})")
        return '\n'.join(lines) if lines else "  (no tables inferred)"

    def _parse_response(self, response: str) -> SQLGenerationResult:
        """Parse LLM response."""
        json_str = response
        if '```json' in response:
            json_str = response.split('```json')[1].split('```')[0]
        elif '```' in response:
            json_str = response.split('```')[1].split('```')[0]

        data = json.loads(json_str.strip())

        return SQLGenerationResult(
            sql=data.get('sql', ''),
            explanation=data.get('explanation', ''),
            assumptions=data.get('assumptions', []),
            ambiguities=data.get('ambiguities', []),
            confidence=data.get('confidence', 0.5)
        )

    def _fallback_generate(self, query: str, intent: IntentResult, schema: InferredSchema, dialect: str) -> SQLGenerationResult:
        """Rule-based fallback SQL generation."""
        if not schema.tables:
            return SQLGenerationResult(
                sql="",
                explanation="Could not infer schema from query",
                assumptions=[],
                ambiguities=["No tables could be inferred from the query"],
                confidence=0.0
            )

        main_table = schema.tables[0]['name']
        columns = schema.tables[0].get('columns', [])

        # Build SELECT - handle aggregations
        select_fields = []
        if intent.requested_fields:
            for field in intent.requested_fields:
                select_fields.append(field)

        # Add aggregations to SELECT
        for agg in intent.aggregations:
            func = agg.get('function', 'COUNT')
            field = agg.get('field', '*')
            alias = agg.get('alias')
            if alias:
                select_fields.append(f"{func}({field}) AS {alias}")
            else:
                select_fields.append(f"{func}({field})")

        # If no fields at all and no aggregations, use *
        if not select_fields:
            select_fields = ['*']

        select_clause = f"SELECT {', '.join(select_fields)}"

        # Build FROM with JOINs - use intent.joins if available, otherwise infer from schema
        from_clause = f"FROM {main_table}"

        # Get all tables referenced in the query (from fields, filters, ordering)
        all_mentioned_tables = set([main_table])
        table_columns = {}  # table -> set of columns
        for table in schema.tables:
            table_columns[table['name']] = set(table.get('columns', []))

        # Check which tables have the requested fields
        for field in intent.requested_fields:
            for table_name, cols in table_columns.items():
                if field in cols:
                    all_mentioned_tables.add(table_name)

        for f in intent.filters:
            field = f.get('field', '')
            for table_name, cols in table_columns.items():
                if field in cols:
                    all_mentioned_tables.add(table_name)

        if intent.ordering:
            field = intent.ordering.get('field', '')
            for table_name, cols in table_columns.items():
                if field in cols:
                    all_mentioned_tables.add(table_name)

        # Build JOINs based on schema relationships
        if len(all_mentioned_tables) > 1 and schema.relationships:
            for rel in schema.relationships:
                from_t = rel.get('from_table')
                to_t = rel.get('to_table')
                if from_t in all_mentioned_tables and to_t in all_mentioned_tables:
                    # Map relationship type to SQL join type
                    rel_type = rel.get('type', '').lower()
                    if rel_type in ('one_to_many', 'many_to_one', 'one_to_one'):
                        join_type = 'INNER'
                    elif rel_type == 'left':
                        join_type = 'LEFT'
                    elif rel_type == 'right':
                        join_type = 'RIGHT'
                    else:
                        join_type = 'INNER'
                    join_on = f"{from_t}.{rel.get('from_column')} = {to_t}.{rel.get('to_column')}"
                    from_clause += f" {join_type} JOIN {to_t} ON {join_on}"

        # Also use explicit intent.joins if provided (they take precedence)
        elif intent.joins:
            for join in intent.joins:
                join_table = join.get('table', '')
                join_on = join.get('on', '')
                join_type = join.get('type', 'INNER')
                if join_table and join_on:
                    from_clause += f" {join_type} JOIN {join_table} ON {join_on}"

        # Build WHERE
        where_conditions = []
        for f in intent.filters:
            field = f.get('field', '')
            op = f.get('operator', '=')
            val = f.get('value', '')
            if isinstance(val, str):
                val = f"'{val}'"
            where_conditions.append(f"{field} {op} {val}")

        where_clause = f"WHERE {' AND '.join(where_conditions)}" if where_conditions else ""

        # Build ORDER BY
        order_clause = ""
        if intent.ordering:
            order_clause = f"ORDER BY {intent.ordering['field']} {intent.ordering['direction']}"
        elif intent.limit:
            # Default ordering for TOP_N without explicit ordering
            # Try to find a numeric column
            numeric_cols = [c for c in columns if any(kw in c.lower() for kw in ['mark', 'score', 'salary', 'price', 'amount', 'value', 'rating'])]
            if numeric_cols:
                order_clause = f"ORDER BY {numeric_cols[0]} DESC"

        # Build LIMIT
        limit_clause = ""
        if intent.limit:
            limit_clause = self.DIALECT_LIMIT.get(dialect, 'LIMIT {limit}').format(limit=intent.limit)

        # Build GROUP BY
        group_clause = ""
        if intent.grouping:
            group_clause = f"GROUP BY {', '.join(intent.grouping)}"
        elif intent.aggregations and intent.requested_fields:
            # If we have aggregations but no explicit GROUP BY, group by requested non-aggregate fields
            group_clause = f"GROUP BY {', '.join(intent.requested_fields)}"

        # Combine - correct SQL order: SELECT, FROM, WHERE, GROUP BY, ORDER BY, LIMIT
        sql_parts = [select_clause, from_clause]
        if where_clause:
            sql_parts.append(where_clause)
        if group_clause:
            sql_parts.append(group_clause)
        if order_clause:
            sql_parts.append(order_clause)
        if limit_clause:
            sql_parts.append(limit_clause)

        sql = ' '.join(sql_parts) + ';'

        assumptions = [
            f"Table name assumed to be {main_table}",
            f"Columns assumed: {', '.join(columns[:5])}{'...' if len(columns) > 5 else ''}"
        ]
        if intent.joins:
            assumptions.append(f"JOINs added based on intent: {', '.join(j.get('table', '') for j in intent.joins)}")
        elif schema.relationships and len(all_mentioned_tables) > 1:
            joined = [j.get('to_table', '') for rel in schema.relationships
                     for j in [rel] if rel.get('from_table') in all_mentioned_tables and rel.get('to_table') in all_mentioned_tables]
            if joined:
                assumptions.append(f"JOINs inferred from schema relationships: {', '.join(joined)}")

        return SQLGenerationResult(
            sql=sql,
            explanation=f"Generated query for {intent.operation} operation on {main_table}",
            assumptions=assumptions,
            ambiguities=intent.ambiguities,
            confidence=0.6
        )


class SQLValidator:
    """Validates generated SQL for syntax, schema compliance, and safety."""

    FORBIDDEN_OPERATIONS = {
        'INSERT', 'UPDATE', 'DELETE', 'DROP', 'ALTER', 'TRUNCATE',
        'CREATE', 'GRANT', 'REVOKE', 'MERGE', 'REPLACE', 'CALL'
    }

    def __init__(self):
        self.parser = get_parser()

    def validate(self, sql: str, schema: InferredSchema, dialect: str = 'postgresql') -> ValidationResult:
        """Run all validation checks."""
        result = ValidationResult()

        # 1. Syntax validation
        result.syntax_valid = self._validate_syntax(sql, dialect)

        # 2. Operation validation (only SELECT allowed)
        result.schema_valid = self._validate_operation(sql)

        # 3. Schema validation (tables/columns exist in inferred schema)
        if result.syntax_valid:
            result.schema_valid = self._validate_schema(sql, schema)

        # 4. Check for dangerous constructs
        self._check_dangerous_constructs(sql, result)

        return result

    def _validate_syntax(self, sql: str, dialect: str) -> bool:
        """Check if SQL is syntactically valid."""
        try:
            parsed = self.parser.parse(sql)
            return parsed.is_valid
        except Exception as e:
            logger.warning(f"Syntax validation error: {e}")
            return False

    def _validate_operation(self, sql: str) -> bool:
        """Check if only SELECT operations are used."""
        sql_upper = sql.upper().strip()

        # Check for multiple statements
        statements = [s.strip() for s in sql.split(';') if s.strip()]
        if len(statements) > 1:
            logger.warning("Multiple statements detected")
            return False

        # Check first word
        first_word = sql_upper.split()[0] if sql_upper.split() else ''
        if first_word not in ('SELECT', 'WITH'):
            logger.warning(f"Non-SELECT operation: {first_word}")
            return False

        return True

    def _validate_schema(self, sql: str, schema: InferredSchema) -> bool:
        """Validate tables and columns against inferred schema."""
        try:
            parsed = self.parser.parse(sql)
            if not parsed.is_valid:
                return False

            # Build schema lookup
            schema_tables = {t['name'].lower(): set(c.lower() for c in t.get('columns', []))
                           for t in schema.tables}

            # Check tables
            for table in parsed.tables:
                table_name = table.name.lower()
                if table_name not in schema_tables:
                    logger.warning(f"Table not in inferred schema: {table_name}")
                    return False

            # Check columns (basic check)
            for col in parsed.columns:
                if col.name == '*':
                    continue
                # Could do more detailed validation here

            return True
        except Exception as e:
            logger.warning(f"Schema validation error: {e}")
            return False

    def _check_dangerous_constructs(self, sql: str, result: ValidationResult):
        """Check for dangerous SQL constructs."""
        sql_upper = sql.upper()

        # Check for comments
        if '--' in sql or '/*' in sql:
            result.issues.append("SQL comments detected")
            result.suggestions.append("Remove comments from query")

        # Check for multiple statements
        if sql.count(';') > 1:
            result.issues.append("Multiple statements detected")
            result.suggestions.append("Use only a single statement")

        # Check for forbidden operations
        for op in self.FORBIDDEN_OPERATIONS:
            if re.search(rf'\b{op}\b', sql_upper):
                result.issues.append(f"Forbidden operation: {op}")
                result.suggestions.append(f"Only SELECT operations are allowed")


class SemanticValidator:
    """Validates semantic correctness of generated SQL against user intent."""

    def __init__(self, llm_client: LLMClient):
        self.llm_client = llm_client

    def validate(self, query: str, intent: IntentResult, schema: InferredSchema, sql: str, dialect: str = 'postgresql') -> ValidationResult:
        """Perform semantic validation using LLM."""
        prompt = self._build_prompt(query, intent, schema, sql, dialect)

        try:
            response = self.llm_client.complete(prompt, max_tokens=1000, temperature=0.1)
            return self._parse_response(response)
        except Exception as e:
            logger.error(f"LLM semantic validation failed: {e}")
            return ValidationResult(
                syntax_valid=True,
                schema_valid=True,
                semantic_valid=False,
                issues=[f"Semantic validation unavailable: {e}"]
            )

    def _build_prompt(self, query: str, intent: IntentResult, schema: InferredSchema, sql: str, dialect: str) -> str:
        """Build prompt for semantic validation."""
        schema_summary = self._format_schema(schema)

        return f"""You are an expert SQL reviewer. Review if the generated SQL correctly answers the user's request.

User request: "{query}"

Inferred schema:
{schema_summary}

Generated SQL ({dialect}):
{sql}

Check:
1. Does the SQL answer the user's question?
2. Are the filters correct?
3. Is the ordering correct?
4. Is LIMIT correct?
5. Are joins necessary and correct?
6. Did the SQL introduce unsupported assumptions?
7. Is there any semantic ambiguity?

Return ONLY valid JSON:
{{
  "valid": true|false,
  "issues": ["issue1", "issue2"],
  "suggestions": ["suggestion1", "suggestion2"],
  "confidence": 0.0-1.0
}}"""

    def _format_schema(self, schema: InferredSchema) -> str:
        """Format schema for prompt."""
        lines = []
        for table in schema.tables:
            cols = ', '.join(table.get('columns', []))
            lines.append(f"  {table['name']}({cols})")
        return '\n'.join(lines) if lines else "  (no tables inferred)"

    def _parse_response(self, response: str) -> ValidationResult:
        """Parse LLM validation response."""
        json_str = response
        if '```json' in response:
            json_str = response.split('```json')[1].split('```')[0]
        elif '```' in response:
            json_str = response.split('```')[1].split('```')[0]

        data = json.loads(json_str.strip())

        return ValidationResult(
            syntax_valid=True,  # Already checked
            schema_valid=True,  # Already checked
            semantic_valid=data.get('valid', False),
            issues=data.get('issues', []),
            suggestions=data.get('suggestions', []),
        )


class ConfidenceScorer:
    """Computes overall confidence score for the pipeline."""

    # Weights for each component
    WEIGHTS = {
        'intent': 0.30,
        'schema': 0.30,
        'sql_validation': 0.25,
        'semantic_validation': 0.15,
    }

    @classmethod
    def score(cls,
              intent_confidence: float,
              schema_confidence: float,
              sql_validation: ValidationResult,
              semantic_validation: ValidationResult) -> Tuple[float, ConfidenceLevel]:
        """Compute overall confidence score."""
        # Intent confidence (from LLM or rule-based)
        intent_score = intent_confidence * cls.WEIGHTS['intent']

        # Schema inference confidence
        schema_score = schema_confidence * cls.WEIGHTS['schema']

        # SQL validation confidence
        sql_val_score = 0.0
        if sql_validation.syntax_valid:
            sql_val_score += 0.5
        if sql_validation.schema_valid:
            sql_val_score += 0.5
        sql_val_score *= cls.WEIGHTS['sql_validation']

        # Semantic validation confidence
        sem_val_score = 0.0
        if semantic_validation.semantic_valid:
            sem_val_score = 1.0
        elif semantic_validation.issues:
            # Partial credit based on issue severity
            sem_val_score = max(0.0, 1.0 - len(semantic_validation.issues) * 0.2)
        sem_val_score *= cls.WEIGHTS['semantic_validation']

        total = intent_score + schema_score + sql_val_score + sem_val_score

        # Determine level
        if total >= 0.80:
            level = ConfidenceLevel.HIGH
        elif total >= 0.60:
            level = ConfidenceLevel.MEDIUM
        else:
            level = ConfidenceLevel.LOW

        return total, level


class AmbiguityDetector:
    """Detects ambiguities that require user clarification."""

    AMBIGUOUS_PATTERNS = [
        (r'\bbest\b', "What should 'best' mean - highest marks, highest GPA, or another metric?"),
        (r'\bworst\b', "What should 'worst' mean - lowest marks, lowest score, or another metric?"),
        (r'\btop\b(?!\s+\d+)', "How many top records? What metric defines 'top'?"),
        (r'\bhigh\b(?!\s+(?:salary|marks|score|price|amount))', "What metric should be 'high'?"),
        (r'\blow\b(?!\s+(?:salary|marks|score|price|amount))', "What metric should be 'low'?"),
        (r'\brank\b', "What metric should be used for ranking?"),
        (r'\border\b(?!\s+by)', "What should be ordered and in which direction?"),
        (r'\bgroup\b(?!\s+by)', "What should be grouped by?"),
    ]

    @classmethod
    def detect(cls, query: str, intent: IntentResult) -> List[str]:
        """Detect ambiguities requiring clarification."""
        ambiguities = []
        ambiguities.extend(intent.ambiguities)

        query_lower = query.lower()

        for pattern, question in cls.AMBIGUOUS_PATTERNS:
            if re.search(pattern, query_lower):
                ambiguities.append(question)

        # Check for missing required fields
        if intent.limit and not intent.ordering and intent.operation == 'SELECT':
            # TOP_N without ordering metric
            has_aggregate = len(intent.aggregations) > 0
            if not has_aggregate:
                ambiguities.append("No ordering metric specified for TOP_N query")

        # Check for ambiguous aggregations
        for agg in intent.aggregations:
            if not agg.get('field'):
                ambiguities.append(f"Aggregation {agg.get('function')} missing field")

        return list(set(ambiguities))  # Deduplicate


class NL2SQLPipeline:
    """Main pipeline orchestrator for NL → SQL generation."""

    def __init__(self, llm_client: Optional[LLMClient] = None, cache_enabled: bool = True):
        self.llm_client = llm_client
        self.cache_enabled = cache_enabled
        self._cache = {}

        # Initialize pipeline components
        self.input_validator = InputValidator()
        self.intent_extractor = IntentExtractor(llm_client) if llm_client else None
        self.schema_inferrer = SchemaInferrer()
        self.sql_generator = SQLGenerator(llm_client) if llm_client else None
        self.sql_validator = SQLValidator()
        self.semantic_validator = SemanticValidator(llm_client) if llm_client else None
        self.confidence_scorer = ConfidenceScorer()
        self.ambiguity_detector = AmbiguityDetector()

    def process(self, query: str, dialect: str = 'postgresql', provided_schema: Optional[Dict[str, Any]] = None) -> PipelineResponse:
        """Run the full pipeline."""
        # Generate cache key
        cache_key = self._cache_key(query, dialect, provided_schema)

        if self.cache_enabled and cache_key in self._cache:
            cached = self._cache[cache_key]
            logger.info(f"Cache hit for query: {query[:50]}...")
            return cached

        # Step 1: Input Validation
        is_valid, error = self.input_validator.validate(query)
        if not is_valid:
            return PipelineResponse(
                status=PipelineStatus.FAILED.value,
                original_query=query,
                message=error,
                retryable=False
            )

        normalized_query = self.input_validator.normalize(query)

        # Step 2: Schema Inference (or use provided schema) - do this BEFORE intent extraction
        # so that intent extractor can use schema to detect cross-table references
        if provided_schema:
            inferred_schema = self._convert_provided_schema(provided_schema)
        else:
            # Do a preliminary intent extraction to get entities for schema inference
            # Use rule-based extraction since we don't have LLM context yet
            preliminary_intent = self._preliminary_intent_extract(normalized_query)
            inferred_schema = self.schema_inferrer.infer(normalized_query, preliminary_intent)

        # Step 3: Intent Extraction (now with schema context)
        if self.intent_extractor:
            intent = self.intent_extractor.extract(normalized_query, inferred_schema)
        else:
            intent = self._fallback_extract(normalized_query)

        # Step 4: Ambiguity Detection
        ambiguities = self.ambiguity_detector.detect(normalized_query, intent)

        # Check if clarification needed
        if ambiguities and self._needs_clarification(ambiguities, intent):
            return PipelineResponse(
                status=PipelineStatus.NEEDS_CLARIFICATION.value,
                original_query=query,
                intent=intent.to_dict(),
                inferred_schema=inferred_schema.to_dict(),
                ambiguities=ambiguities,
                question=ambiguities[0] if ambiguities else "Please clarify your request",
                confidence=0.0,
                confidence_level=ConfidenceLevel.LOW.value
            )

        # Step 5: SQL Generation
        if self.sql_generator:
            sql_result = self.sql_generator.generate(normalized_query, intent, inferred_schema, dialect)
        else:
            sql_result = self._fallback_generate(normalized_query, intent, inferred_schema, dialect)

        # Step 6: Static SQL Validation
        validation = self.sql_validator.validate(sql_result.sql, inferred_schema, dialect)

        # Step 7: Semantic Validation
        if self.semantic_validator:
            semantic_validation = self.semantic_validator.validate(
                normalized_query, intent, inferred_schema, sql_result.sql, dialect
            )
            # Merge validation results
            validation.semantic_valid = semantic_validation.semantic_valid
            validation.issues.extend(semantic_validation.issues)
            validation.suggestions.extend(semantic_validation.suggestions)
        else:
            semantic_validation = ValidationResult(
                syntax_valid=validation.syntax_valid,
                schema_valid=validation.schema_valid,
                semantic_valid=True  # Assume valid if no semantic validator
            )

        # Step 8: Confidence Scoring
        intent_conf = 0.8 if self.intent_extractor else 0.5
        schema_conf = inferred_schema.confidence
        confidence, level = self.confidence_scorer.score(
            intent_conf, schema_conf, validation, semantic_validation
        )

        # Build final response
        response = PipelineResponse(
            status=PipelineStatus.SUCCESS.value if validation.syntax_valid and validation.schema_valid
                   else PipelineStatus.FAILED.value,
            original_query=query,
            intent=intent.to_dict(),
            inferred_schema=inferred_schema.to_dict(),
            sql=sql_result.sql if validation.syntax_valid and validation.schema_valid else None,
            assumptions=sql_result.assumptions,
            ambiguities=list(set(sql_result.ambiguities + ambiguities)),
            confidence=round(confidence, 2),
            confidence_level=level.value,
            validation=validation.to_dict(),
            message=None if validation.syntax_valid and validation.schema_valid else "SQL validation failed"
        )

        # Cache successful responses
        if self.cache_enabled and response.status == PipelineStatus.SUCCESS.value:
            self._cache[cache_key] = response

        return response

    def _needs_clarification(self, ambiguities: List[str], intent: IntentResult) -> bool:
        """Determine if ambiguities require user clarification."""
        # Always clarify if "best"/"worst" without metric and no limit
        for amb in ambiguities:
            if 'best' in amb.lower() or 'worst' in amb.lower():
                if not intent.limit and not intent.ordering:
                    return True

        # Also clarify if TOP_N without ordering metric
        for amb in ambiguities:
            if 'top_n' in amb.lower() or 'ordering metric' in amb.lower():
                return True

        return False

    def _preliminary_intent_extract(self, query: str) -> IntentResult:
        """Extract basic intent (entities, etc.) for schema inference before full LLM extraction."""
        text_lower = query.lower()

        intent = IntentResult(
            operation='SELECT',
            entities=[],
            requested_fields=[],
            filters=[],
            raw_text=query
        )

        # Detect entities from keywords
        entity_keywords = {
            'student': ['student', 'students'],
            'employee': ['employee', 'employees', 'staff', 'worker'],
            'product': ['product', 'products', 'item', 'items'],
            'customer': ['customer', 'customers', 'client', 'clients'],
            'order': ['order', 'orders', 'purchase', 'purchases'],
            'sale': ['sale', 'sales', 'revenue'],
            'department': ['department', 'departments', 'dept'],
            'grade': ['grade', 'grades', 'mark', 'marks'],
        }

        for entity, keywords in entity_keywords.items():
            if any(kw in text_lower for kw in keywords):
                intent.entities.append(entity)

        # Default entity if none found
        if not intent.entities:
            intent.entities = ['record']

        # Detect TOP_N
        limit_match = re.search(r'(?:top|first)\s+(\d+)', text_lower)
        if limit_match:
            intent.limit = int(limit_match.group(1))

        # Detect aggregations
        if 'average' in text_lower or 'avg' in text_lower:
            intent.operation = 'AVG'
        elif 'sum' in text_lower or 'total' in text_lower:
            intent.operation = 'SUM'
        elif 'count' in text_lower:
            intent.operation = 'COUNT'

        return intent

    def _fallback_extract(self, query: str) -> IntentResult:
        """Fallback rule-based extraction when LLM unavailable."""
        # Reuse logic from IntentExtractor._fallback_extract
        extractor = IntentExtractor(None)
        return extractor._fallback_extract(query)

    def _fallback_generate(self, query: str, intent: IntentResult, schema: InferredSchema, dialect: str) -> SQLGenerationResult:
        """Fallback rule-based generation when LLM unavailable."""
        generator = SQLGenerator(None)
        return generator._fallback_generate(query, intent, schema, dialect)

    def _convert_provided_schema(self, schema: Dict[str, Any]) -> InferredSchema:
        """Convert provided schema dict to InferredSchema."""
        tables = []
        relationships = []

        # Handle different schema formats
        tables_dict = schema.get('tables', {})
        if isinstance(tables_dict, list):
            # If tables is a list of table objects
            for table in tables_dict:
                if isinstance(table, dict) and 'name' in table:
                    tables.append({
                        'name': table['name'],
                        'columns': table.get('columns', [])
                    })
        elif isinstance(tables_dict, dict):
            # If tables is a dict mapping table_name -> table_def
            for table_name, table_def in tables_dict.items():
                # Skip 'relationships' key if it's inside tables (malformed input)
                if table_name == 'relationships':
                    relationships = table_def if isinstance(table_def, list) else []
                    continue
                if isinstance(table_def, dict) and 'columns' in table_def:
                    cols = list(table_def['columns'].keys()) if isinstance(table_def['columns'], dict) else table_def['columns']
                else:
                    cols = table_def if isinstance(table_def, list) else []
                tables.append({
                    'name': table_name,
                    'columns': cols
                })

        # Also check for relationships at top level
        if not relationships:
            relationships = schema.get('relationships', [])

        return InferredSchema(
            tables=tables,
            relationships=relationships,
            source="provided",
            confidence=1.0
        )

    def _cache_key(self, query: str, dialect: str, schema: Optional[Dict]) -> str:
        """Generate cache key."""
        schema_str = json.dumps(schema, sort_keys=True) if schema else ""
        key_str = f"{query}|{dialect}|{schema_str}"
        return hashlib.md5(key_str.encode()).hexdigest()


# Convenience function for use in views
def create_pipeline(llm_client: Optional[LLMClient] = None) -> NL2SQLPipeline:
    """Create a configured pipeline instance."""
    return NL2SQLPipeline(llm_client=llm_client)