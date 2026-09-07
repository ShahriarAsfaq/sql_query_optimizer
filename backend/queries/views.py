"""
Views for the queries API.
"""
import logging
from typing import List, Dict, Any
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.decorators import api_view
from django.utils import timezone
from django.db import connections

logger = logging.getLogger(__name__)

import re


def parse_schema_string(schema_str: str) -> Dict[str, Any]:
    """
    Parse schema string format into dict format.

    Input formats supported:
    - "tables: employees(id, name, salary), departments(id, name)"
    - "table: users(id, email, password)"
    - "employees(id, name, salary), departments(id, name)"
    - "employees(id, name, salary)"
    - "tables: customers(id, name), orders(id, customer_id); relationships: customers.id -> orders.customer_id"

    Output: {"tables": {"employees": {"columns": {"id": "integer", "name": "text", "salary": "numeric"}}, ...}, "relationships": [...]}
    """
    result = {"tables": {}, "relationships": []}

    # Try to extract tables part with "tables:" or "table:" prefix
    tables_match = re.search(r'tables?:\s*(.+?)(?:\s*;|$)', schema_str, re.IGNORECASE)
    if tables_match:
        tables_part = tables_match.group(1)
    else:
        # Assume the whole string is the tables part
        tables_part = schema_str

    # Parse each table: table_name(col1, col2, ...)
    table_pattern = r'(\w+)\s*\(([^)]+)\)'
    for match in re.finditer(table_pattern, tables_part):
        table_name = match.group(1)
        cols_str = match.group(2)
        columns = {}
        for col in cols_str.split(','):
            col = col.strip()
            if col:
                # Default to text type if no type specified
                columns[col] = "text"
        result["tables"][table_name] = {"columns": columns}

    # Try to extract relationships part
    rel_match = re.search(r'relationships?:\s*(.+)$', schema_str, re.IGNORECASE)
    if rel_match:
        rel_part = rel_match.group(1)
        # Parse relationships: from_table.from_col -> to_table.to_col
        rel_pattern = r'(\w+)\.(\w+)\s*(?:->|->>)\s*(\w+)\.(\w+)'
        for match in re.finditer(rel_pattern, rel_part):
            from_table = match.group(1)
            from_col = match.group(2)
            to_table = match.group(3)
            to_col = match.group(4)
            result["relationships"].append({
                "from_table": from_table,
                "from_column": from_col,
                "to_table": to_table,
                "to_column": to_col,
                "type": "one_to_many"
            })

    return result


from .serializers import (
    AnalyzeRequestSerializer, AnalyzeResponseSerializer,
    OptimizeRequestSerializer, OptimizeResponseSerializer,
    GenerateRequestSerializer, GenerateResponseSerializer,
    GenerateSQLRequestSerializer, GenerateSQLResponseSerializer,
)
from .services.sql_parser import get_parser, SQLParserService
from .services.validator import ValidationService
from .services.explanation import ExplanationService
from .services.intent import IntentService
from .services.optimizer import OptimizerService
from .services.sql_parser import ParsedQuery
from .services.llm_client import LLMClient
from .services.gemini_pipeline import create_pipeline, NL2SQLPipeline
from .authentication import APIKeyAuthentication


class AnalyzeQueryView(APIView):
    """
    POST /api/queries/analyze/
    Analyze a SQL query: parse, validate, check intent match, explain.
    """
    authentication_classes = [APIKeyAuthentication]
    permission_classes = [AllowAny]  # Allow both authenticated and unauthenticated

    def post(self, request):
        serializer = AnalyzeRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        sql = serializer.validated_data['sql']
        schema = serializer.validated_data.get('schema')
        intent_text = serializer.validated_data.get('intent_text')
        explain = serializer.validated_data.get('explain', False)

        # Parse the SQL
        parser = get_parser()
        parsed = parser.parse(sql)
        parsed_json = parser.format_for_json(parsed)

        # Handle schema: string format -> dict, or infer virtual schema if not provided
        if isinstance(schema, str):
            schema = parse_schema_string(schema)
        elif schema is None:
            # Infer virtual schema from the parsed SQL
            schema = parser.build_virtual_schema(parsed)

        # Validate against schema (provided or inferred)
        validator = ValidationService(schema)
        validation_result = validator.validate(parsed)

        # Check intent match if intent_text provided
        intent_match = None
        if intent_text:
            # Use LLMClient with gemini provider to leverage the GOOGLE_API_KEY from .env
            llm_client = LLMClient(provider='gemini')
            intent_service = IntentService(llm_client=llm_client)
            intent_match = intent_service.check_intent_match(parsed, intent_text, schema)

        # Generate explanation - use LLM for syntax error enhancement
        # Force Gemini provider to avoid system ANTHROPIC_AUTH_TOKEN
        llm_client = LLMClient(provider='gemini')
        explanation_service = ExplanationService(
            use_llm_intent=explain,
            llm_client=llm_client
        )
        explanation = explanation_service.explain(parsed)

        # Generate natural language intent explanation if EXPLAIN plan is requested
        explain_plan = None
        if explain:
            try:
                nl_explanation = explanation_service.explain_intent(parsed, schema)

                # Keep only the first line (the natural explanation in one line)
                if nl_explanation:
                    explain_plan = nl_explanation.strip().split('\n')[0]

            except Exception as e:
                logger.warning(f"NL explanation generation failed: {e}")

        # Compute complexity score
        optimizer = OptimizerService(schema)
        complexity_score = optimizer._compute_complexity_score(parsed, sql)

        response_data = {
            'parsed_query': parsed_json,
            'validation': validation_result,
            'intent_match': intent_match,
            'explanation': explanation,
            'complexity_score': complexity_score,
            'explain_plan': explain_plan,
        }

        response_serializer = AnalyzeResponseSerializer(data=response_data)
        response_serializer.is_valid(raise_exception=True)
        return Response(response_serializer.data, status=status.HTTP_200_OK)


class OptimizeQueryView(APIView):
    """
    POST /api/queries/optimize/
    Generate optimized candidates for a SQL query.
    """
    authentication_classes = [APIKeyAuthentication]
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = OptimizeRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        sql = serializer.validated_data['sql']
        schema = serializer.validated_data.get('schema')
        use_calcite = serializer.validated_data.get('use_calcite', True)

        # Handle schema: string format -> dict, or infer virtual schema if not provided
        if isinstance(schema, str):
            schema = parse_schema_string(schema)
        elif schema is None:
            # Infer virtual schema from the parsed SQL
            parser = get_parser()
            parsed = parser.parse(sql)
            schema = parser.build_virtual_schema(parsed)

        # Get seed database connection for EXPLAIN
        seed_db = connections['seed_db']
        optimizer = OptimizerService(schema, seed_db_connection=seed_db, use_calcite=use_calcite)
        result = optimizer.optimize(sql)

        response_serializer = OptimizeResponseSerializer(data=result)
        response_serializer.is_valid(raise_exception=True)
        return Response(response_serializer.data, status=status.HTTP_200_OK)


class GenerateQueryView(APIView):
    """
    POST /api/queries/generate/
    Generate candidate SQL queries from natural language intent.
    """
    authentication_classes = [APIKeyAuthentication]
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = GenerateRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        intent_text = serializer.validated_data['intent_text']
        schema = serializer.validated_data.get('schema')
        answers = serializer.validated_data.get('answers', {})

        # Handle schema as string format (e.g., "tables: employees(id, name, salary)")
        if isinstance(schema, str):
            schema = self._parse_schema_string(schema)
        elif schema is None:
            # Use a minimal default schema based on common tables if none provided
            schema = self._get_default_schema()

        # Use LLMClient with gemini provider to leverage the GOOGLE_API_KEY from .env
        llm_client = LLMClient(provider='gemini')
        intent_service = IntentService(llm_client=llm_client)
        structured_intent = intent_service.extract_intent(intent_text, schema)

        # Apply answers to the structured intent
        if answers:
            structured_intent = self._apply_answers(structured_intent, answers)

        # Check if we need to ask clarifying questions
        missing_values = self._check_missing_values(structured_intent, schema, intent_text)

        if missing_values:
            # Return clarifying questions to the client
            return Response({
                'status': 'needs_clarification',
                'questions': missing_values,
                'partial_intent': structured_intent,
            }, status=status.HTTP_200_OK)

        optimizer = OptimizerService(schema)
        candidates = optimizer.generate_from_intent(structured_intent)

        # Use the best candidate (first one) as the primary result
        best_candidate = candidates[0] if candidates else None
        sql = best_candidate.get('sql', '') if best_candidate else ''

        # Handle category - could be list from LLM, pick first priority
        category = structured_intent.get('category', '')
        if isinstance(category, list):
            priority = ['TOP_N', 'JOIN', 'AGGREGATE', 'FILTER', 'GROUP', 'SORT', 'TREND', 'RANKING', 'DUPLICATE_DETECTION', 'COMPARISON', 'RETRIEVE', 'UNKNOWN']
            for cat in priority:
                if cat in category:
                    category = cat
                    break
            else:
                category = category[0] if category else 'RETRIEVE'
        explanation = f"Generated query for: {category.lower()} operation"

        # Convert confidence string to float
        conf_str = best_candidate.get('confidence', 'MEDIUM') if best_candidate else 'MEDIUM'
        confidence_map = {'HIGH': 0.9, 'MEDIUM': 0.7, 'LOW': 0.4}
        confidence = confidence_map.get(conf_str, 0.7)
        warnings = best_candidate.get('validation_errors', []) if best_candidate else []

        response_data = {
            'status': 'success',
            'sql': sql,
            'intent': structured_intent,
            'candidates': candidates,
            'explanation': explanation,
            'confidence': confidence,
            'warnings': warnings,
        }

        response_serializer = GenerateResponseSerializer(data=response_data)
        response_serializer.is_valid(raise_exception=True)
        return Response(response_serializer.data, status=status.HTTP_200_OK)

    def _apply_answers(self, intent: Dict[str, Any], answers: Dict[str, Any]) -> Dict[str, Any]:
        """Apply user answers to the structured intent."""
        import copy
        intent = copy.deepcopy(intent)

        for field, value in answers.items():
            if not value:
                continue

            # Handle nested fields like "filters[0].value"
            if '[' in field and ']' in field:
                # Parse field like "filters[0].value"
                parts = field.replace(']', '').split('[')
                parent = parts[0]
                idx = int(parts[1])
                child = parts[2].lstrip('.') if len(parts) > 2 else None

                if parent not in intent:
                    intent[parent] = []
                # Ensure list is long enough
                while len(intent[parent]) <= idx:
                    intent[parent].append({})
                if child:
                    intent[parent][idx][child] = value
                else:
                    intent[parent][idx] = value
            else:
                # Special handling for list fields - if value is a string, convert to list
                if field in ('group_by', 'join_tables', 'order_by') and isinstance(value, str):
                    intent[field] = [value]
                # Special handling for limit - convert string to integer
                elif field == 'limit' and isinstance(value, str):
                    try:
                        intent[field] = int(value)
                    except ValueError:
                        intent[field] = value
                else:
                    intent[field] = value

        return intent

    def _check_missing_values(self, intent: Dict[str, Any], schema: Dict[str, Any], intent_text: str) -> List[Dict[str, Any]]:
        """
        Check if the structured intent is missing required values and generate clarifying questions.

        Args:
            intent: Extracted structured intent
            schema: Database schema
            intent_text: Original natural language input

        Returns:
            List of questions to ask the user, empty if no missing values
        """
        questions = []

        # Check if entity (table) is missing or not in schema
        entity = intent.get('entity')
        if not entity or entity not in schema.get('tables', {}):
            # Try to find a close match
            available_tables = list(schema.get('tables', {}).keys())
            questions.append({
                'field': 'entity',
                'type': 'select',
                'question': f'Which table do you want to query?',
                'options': available_tables,
                'current_value': entity or '',
            })
            return questions  # Can't proceed without entity

        # Check if operation needs metric but it's missing
        operation = intent.get('operation')
        if operation in ('COUNT', 'SUM', 'AVG', 'MIN', 'MAX') and not intent.get('metric'):
            entity_cols = list(schema['tables'][entity].get('columns', {}).keys())
            questions.append({
                'field': 'metric',
                'type': 'select',
                'question': f'Which column should we {operation.lower()}?',
                'options': entity_cols,
                'current_value': '',
            })

        # Check for aggregate without group_by
        if intent.get('category') == 'AGGREGATE' and intent.get('metric') and not intent.get('group_by'):
            # See if there's a column that looks like a grouping dimension
            entity_cols = list(schema['tables'][entity].get('columns', {}).keys())
            groupable_cols = [c for c in entity_cols if c not in (intent.get('metric'), 'id')]
            if groupable_cols:
                questions.append({
                    'field': 'group_by',
                    'type': 'select',
                    'question': f'How do you want to group the {operation.lower()} of {intent.get("metric")}?',
                    'options': ['No grouping'] + groupable_cols,
                    'current_value': '',
                    'optional': True,
                })

        # Check for JOIN intent but no join condition
        if intent.get('category') == 'JOIN' and not intent.get('join_tables'):
            # Look for related tables in schema
            related = self._find_related_tables(schema, entity)
            if related:
                questions.append({
                    'field': 'join_tables',
                    'type': 'select',
                    'question': f'Which table do you want to join with {entity}?',
                    'options': related,
                    'current_value': '',
                })

        # Check for filters with missing values
        for i, f in enumerate(intent.get('filters', [])):
            if f.get('value') is None or f.get('value') == '':
                col = f.get('column')
                questions.append({
                    'field': f'filters[{i}].value',
                    'type': 'text',
                    'question': f'What value should {col} be compared to?',
                    'current_value': '',
                })

        # Check for TOP_N without limit
        if intent.get('category') == 'TOP_N' and not intent.get('limit'):
            questions.append({
                'field': 'limit',
                'type': 'text',
                'question': 'How many results do you want (top N)?',
                'current_value': '',
                'validation': 'number',
            })

        # Check for ORDER BY without direction
        for i, ob in enumerate(intent.get('order_by', [])):
            if not ob.get('direction'):
                questions.append({
                    'field': f'order_by[{i}].direction',
                    'type': 'select',
                    'question': f'Sort {ob.get("column")} in which order?',
                    'options': ['ASC', 'DESC'],
                    'current_value': 'ASC',
                })

        return questions

    def _find_related_tables(self, schema: Dict[str, Any], entity: str) -> List[str]:
        """Find tables related to the entity via foreign key relationships."""
        related = []
        for rel in schema.get('relationships', []):
            if rel.get('from_table') == entity:
                related.append(rel.get('to_table'))
            elif rel.get('to_table') == entity:
                related.append(rel.get('from_table'))
        return related

    def _format_pg_explain(self, pg_explain) -> str:
        """Format PostgreSQL EXPLAIN output as readable text."""
        if not pg_explain:
            return ""

        lines = []
        for item in pg_explain:
            if isinstance(item, dict) and 'Plan' in item:
                plan = item['Plan']
                lines.append(self._format_plan_node(plan, 0))
        return "\n".join(lines)

    def _format_plan_node(self, plan: dict, indent: int) -> str:
        """Recursively format a plan node."""
        indent_str = "  " * indent
        node_type = plan.get('Node Type', 'Unknown')
        relation = plan.get('Relation Name', '')
        alias = plan.get('Alias', '')
        startup = plan.get('Startup Cost', 0)
        total = plan.get('Total Cost', 0)
        rows = plan.get('Plan Rows', 0)
        width = plan.get('Plan Width', 0)

        line = f"{indent_str}{node_type}"
        if relation:
            line += f" on {relation}"
        if alias and alias != relation:
            line += f" (alias: {alias})"
        line += f"  (cost={startup:.2f}..{total:.2f} rows={rows} width={width})"

        children = plan.get('Plans', [])
        for child in children:
            line += "\n" + self._format_plan_node(child, indent + 1)

        return line

    def _parse_schema_string(self, schema_str: str) -> Dict[str, Any]:
        """
        Parse schema string format into dict format.

        Input formats supported:
        - "tables: employees(id, name, salary), departments(id, name)"
        - "table: users(id, email, password)"
        - "employees(id, name, salary), departments(id, name)"
        - "employees(id, name, salary)"
        - "tables: customers(id, name), orders(id, customer_id); relationships: customers.id -> orders.customer_id"

        Output: {"tables": {"employees": {"columns": {"id": "integer", "name": "text", "salary": "numeric"}}, ...}, "relationships": [...]}
        """
        import re
        result = {"tables": {}, "relationships": []}

        # Try to extract tables part with "tables:" or "table:" prefix
        tables_match = re.search(r'tables?:\s*(.+?)(?:\s*;|$)', schema_str, re.IGNORECASE)
        if tables_match:
            tables_part = tables_match.group(1)
        else:
            # Assume the whole string is the tables part
            tables_part = schema_str

        # Parse each table: table_name(col1, col2, ...)
        table_pattern = r'(\w+)\s*\(([^)]+)\)'
        for match in re.finditer(table_pattern, tables_part):
            table_name = match.group(1)
            cols_str = match.group(2)
            columns = {}
            for col in cols_str.split(','):
                col = col.strip()
                if col:
                    # Default to text type if no type specified
                    columns[col] = "text"
            result["tables"][table_name] = {"columns": columns}

        # Try to extract relationships part
        rel_match = re.search(r'relationships?:\s*(.+)$', schema_str, re.IGNORECASE)
        if rel_match:
            rel_part = rel_match.group(1)
            # Parse relationships: from_table.from_col -> to_table.to_col
            rel_pattern = r'(\w+)\.(\w+)\s*(?:->|->>)\s*(\w+)\.(\w+)'
            for match in re.finditer(rel_pattern, rel_part):
                from_table = match.group(1)
                from_col = match.group(2)
                to_table = match.group(3)
                to_col = match.group(4)
                result["relationships"].append({
                    "from_table": from_table,
                    "from_column": from_col,
                    "to_table": to_table,
                    "to_column": to_col,
                    "type": "one_to_many"
                })

        return result

    def _get_default_schema(self) -> Dict[str, Any]:
        """Provide a default schema for common tables when none is given."""
        return {
            "tables": {
                "customers": {
                    "columns": {
                        "id": "integer",
                        "name": "text",
                        "email": "text",
                        "city": "text",
                        "created_at": "timestamp"
                    }
                },
                "orders": {
                    "columns": {
                        "id": "integer",
                        "customer_id": "integer",
                        "amount": "numeric",
                        "status": "text",
                        "created_at": "timestamp"
                    }
                },
                "employees": {
                    "columns": {
                        "id": "integer",
                        "name": "text",
                        "salary": "numeric",
                        "department": "text",
                        "dept_id": "integer",
                        "manager_id": "integer",
                        "created_at": "timestamp"
                    }
                },
                "departments": {
                    "columns": {
                        "id": "integer",
                        "name": "text",
                        "budget": "numeric"
                    }
                },
                "products": {
                    "columns": {
                        "id": "integer",
                        "name": "text",
                        "price": "numeric",
                        "category": "text"
                    }
                },
                "purchases": {
                    "columns": {
                        "id": "integer",
                        "customer_id": "integer",
                        "product_id": "integer",
                        "quantity": "integer",
                        "total_amount": "numeric",
                        "created_at": "timestamp"
                    }
                }
            },
            "relationships": [
                {"from_table": "customers", "to_table": "orders", "from_column": "id", "to_column": "customer_id", "type": "one_to_many"},
                {"from_table": "employees", "to_table": "departments", "from_column": "dept_id", "to_column": "id", "type": "many_to_one"},
                {"from_table": "employees", "to_table": "employees", "from_column": "manager_id", "to_column": "id", "type": "self_referential"},
                {"from_table": "purchases", "to_table": "customers", "from_column": "customer_id", "to_column": "id", "type": "many_to_one"},
                {"from_table": "purchases", "to_table": "products", "from_column": "product_id", "to_column": "id", "type": "many_to_one"}
            ]
        }


class GenerateSQLView(APIView):
    """
    POST /api/queries/generate-sql/
    Generate SQL from natural language using the new modular pipeline.
    Supports schema inference, clarifying questions, and confidence scoring.
    """
    authentication_classes = [APIKeyAuthentication]
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = GenerateSQLRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        query = serializer.validated_data['query']
        dialect = serializer.validated_data.get('dialect', 'postgresql')
        schema = serializer.validated_data.get('schema')

        # Handle schema string format (e.g., "tables: employees(id, name, salary)")
        if isinstance(schema, str):
            schema = parse_schema_string(schema)

        # Create pipeline with LLM client (using Gemini from .env)
        llm_client = LLMClient(provider='gemini')
        pipeline = create_pipeline(llm_client=llm_client)

        # Process through pipeline
        result = pipeline.process(query, dialect=dialect, provided_schema=schema)

        # Convert response to serializer format
        response_data = result.to_dict()

        response_serializer = GenerateSQLResponseSerializer(data=response_data)
        response_serializer.is_valid(raise_exception=True)
        return Response(response_serializer.data, status=status.HTTP_200_OK)


@api_view(['GET'])
def health_check(request):
    """Health check endpoint."""
    return Response({
        'status': 'healthy',
        'timestamp': timezone.now().isoformat(),
    }, status=status.HTTP_200_OK)