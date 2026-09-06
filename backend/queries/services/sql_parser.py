"""
SQL Parser Service - wraps sqlglot to parse SQL into structured JSON representation.
"""
import sqlglot
from sqlglot import exp
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, asdict
import logging

logger = logging.getLogger(__name__)


@dataclass
class ParseError:
    """Represents a SQL parse error with location info."""
    message: str
    line: Optional[int] = None
    column: Optional[int] = None
    context: Optional[str] = None


@dataclass
class TableReference:
    """Represents a table reference in the query."""
    name: str
    alias: Optional[str] = None
    schema: Optional[str] = None


@dataclass
class ColumnReference:
    """Represents a column reference in the query."""
    name: str
    table: Optional[str] = None
    alias: Optional[str] = None
    is_alias: bool = False


@dataclass
class JoinInfo:
    """Represents a JOIN clause."""
    type: str  # INNER, LEFT, RIGHT, FULL, CROSS
    table: TableReference
    condition: Optional[str] = None


@dataclass
class WhereCondition:
    """Represents a WHERE condition."""
    column: str
    operator: str
    value: Any
    table: Optional[str] = None


@dataclass
class ParsedQuery:
    """Structured representation of a parsed SQL query."""
    operation_type: str  # SELECT, INSERT, UPDATE, DELETE, etc.
    tables: List[TableReference]
    columns: List[ColumnReference]
    joins: List[JoinInfo]
    where_conditions: List[WhereCondition]
    group_by: List[str]
    having_conditions: List[str]
    order_by: List[Dict[str, Any]]
    limit: Optional[int]
    window_functions: List[str]
    aggregations: List[str]
    subqueries: List[str]
    cte_names: List[str]
    is_valid: bool
    errors: List[ParseError]
    raw_sql: str
    dialect: str = "postgres"


class SQLParserService:
    """
    Service for parsing SQL queries using sqlglot.
    Provides structured JSON representation and parse error details.
    """

    # Map sqlglot expression types to readable operation types
    OPERATION_TYPE_MAP = {
        exp.Select: "SELECT",
        exp.Insert: "INSERT",
        exp.Update: "UPDATE",
        exp.Delete: "DELETE",
        exp.Create: "CREATE",
        exp.Drop: "DROP",
        exp.Alter: "ALTER",
        exp.TruncateTable: "TRUNCATE",
        exp.Merge: "MERGE",
        exp.With: "WITH",
    }

    def __init__(self, dialect: str = "postgres"):
        self.dialect = dialect

    def parse(self, sql: str) -> ParsedQuery:
        """
        Parse a SQL string into a structured ParsedQuery object.

        Args:
            sql: Raw SQL query string

        Returns:
            ParsedQuery object with structured representation and any errors
        """
        errors = []
        parsed_ast = None

        try:
            # Parse the SQL with sqlglot
            parsed_ast = sqlglot.parse_one(sql, dialect=self.dialect)
        except sqlglot.errors.ParseError as e:
            # Extract detailed error info from sqlglot's errors attribute
            if hasattr(e, 'errors') and e.errors:
                for err_detail in e.errors:
                    errors.append(ParseError(
                        message=err_detail.get('description', str(e)),
                        line=err_detail.get('line'),
                        column=err_detail.get('col'),
                        context=err_detail.get('start_context', '') + err_detail.get('highlight', '') + err_detail.get('end_context', '')
                    ))
            else:
                errors.append(ParseError(
                    message=str(e),
                    line=getattr(e, 'line', None),
                    column=getattr(e, 'col', None),
                    context=self._get_error_context(sql, getattr(e, 'line', None))
                ))
            return self._empty_result(sql, errors)
        except sqlglot.errors.TokenError as e:
            # Handle tokenization errors (e.g., unclosed quotes)
            # TokenError has start and end positions
            context_str = None
            if hasattr(e, 'start') and e.start is not None:
                # Get context around the error
                start = max(0, e.start - 30)
                end = min(len(sql), e.start + 30)
                context_str = sql[start:end]

            # Provide a more user-friendly error message
            msg = str(e)
            # Check for unclosed string literals (common pattern: truncated string in error)
            if "Error tokenizing" in msg and ('"' in msg or "'" in msg):
                msg = "Unclosed string literal (missing closing quote)"

            errors.append(ParseError(
                message=msg,
                line=None,
                column=None,
                context=context_str
            ))
            return self._empty_result(sql, errors)

        if parsed_ast is None:
            errors.append(ParseError(message="Failed to parse SQL: empty result"))
            return self._empty_result(sql, errors)

        # Extract structured information
        try:
            operation_type = self._get_operation_type(parsed_ast)
            tables = self._extract_tables(parsed_ast)
            columns = self._extract_columns(parsed_ast)
            joins = self._extract_joins(parsed_ast)
            where_conditions = self._extract_where_conditions(parsed_ast)
            group_by = self._extract_group_by(parsed_ast)
            having_conditions = self._extract_having(parsed_ast)
            order_by = self._extract_order_by(parsed_ast)
            limit = self._extract_limit(parsed_ast)
            window_functions = self._extract_window_functions(parsed_ast)
            aggregations = self._extract_aggregations(parsed_ast)
            subqueries = self._extract_subqueries(parsed_ast)
            cte_names = self._extract_ctes(parsed_ast)

            return ParsedQuery(
                operation_type=operation_type,
                tables=tables,
                columns=columns,
                joins=joins,
                where_conditions=where_conditions,
                group_by=group_by,
                having_conditions=having_conditions,
                order_by=order_by,
                limit=limit,
                window_functions=window_functions,
                aggregations=aggregations,
                subqueries=subqueries,
                cte_names=cte_names,
                is_valid=len(errors) == 0,
                errors=errors,
                raw_sql=sql,
                dialect=self.dialect
            )
        except Exception as e:
            logger.exception("Error extracting query structure")
            errors.append(ParseError(message=f"Error analyzing query structure: {str(e)}"))
            return self._empty_result(sql, errors)

    def _empty_result(self, sql: str, errors: List[ParseError]) -> ParsedQuery:
        """Return an empty ParsedQuery with errors."""
        return ParsedQuery(
            operation_type="UNKNOWN",
            tables=[],
            columns=[],
            joins=[],
            where_conditions=[],
            group_by=[],
            having_conditions=[],
            order_by=[],
            limit=None,
            window_functions=[],
            aggregations=[],
            subqueries=[],
            cte_names=[],
            is_valid=False,
            errors=errors,
            raw_sql=sql,
            dialect=self.dialect
        )

    def _get_operation_type(self, ast: exp.Expression) -> str:
        """Determine the operation type from the AST root."""
        for expr_type, op_name in self.OPERATION_TYPE_MAP.items():
            if isinstance(ast, expr_type):
                return op_name
        return ast.__class__.__name__.upper()

    def _extract_tables(self, ast: exp.Expression) -> List[TableReference]:
        """Extract all table references from the query."""
        tables = []

        for table in ast.find_all(exp.Table):
            # Skip tables in subqueries (they'll be found recursively)
            if table.parent and isinstance(table.parent, (exp.Subquery, exp.CTE)):
                continue

            tables.append(TableReference(
                name=table.name,
                alias=table.alias or None,
                schema=table.args.get('db') or None
            ))

        return tables

    def _extract_columns(self, ast: exp.Expression) -> List[ColumnReference]:
        """Extract columns from the SELECT list only (not WHERE, JOIN, ORDER BY, etc.)."""
        columns = []

        select_expr = ast.find(exp.Select)
        if not select_expr:
            return columns

        # First, extract aliases from SELECT expressions
        select_aliases = set()
        for expr in select_expr.expressions:
            if isinstance(expr, exp.Alias):
                select_aliases.add(expr.alias)

        # Handle SELECT *
        for star in select_expr.find_all(exp.Star):
            columns.append(ColumnReference(
                name='*',
                table=None,
                alias=None,
                is_alias=False
            ))

        # Extract columns only from the SELECT expressions
        seen = set()  # Track seen (name, table, alias) to deduplicate
        for expr in select_expr.expressions:
            # For each expression in SELECT, find column references within it
            for column in expr.find_all(exp.Column):
                # Skip if this column is part of a table reference
                if isinstance(column.parent, exp.Table):
                    continue

                table_name = None
                if column.table:
                    table_name = column.table

                # Check if this column name is actually an alias from SELECT
                is_alias = column.name in select_aliases
                alias = column.alias or None

                # Deduplicate
                key = (column.name, table_name, alias)
                if key in seen:
                    continue
                seen.add(key)

                columns.append(ColumnReference(
                    name=column.name,
                    table=table_name,
                    alias=alias,
                    is_alias=is_alias
                ))

        return columns

    def _extract_joins(self, ast: exp.Expression) -> List[JoinInfo]:
        """Extract JOIN information from the query."""
        joins = []

        for join in ast.find_all(exp.Join):
            # sqlglot uses 'side' for LEFT/RIGHT/FULL and 'kind' for INNER/CROSS/SEMI/ANTI
            join_side = join.side.upper() if join.side else ""
            join_kind = join.kind.upper() if join.kind else ""

            # Determine join type: LEFT, RIGHT, FULL, INNER, CROSS, SEMI, ANTI
            if join_side:
                join_type = join_side  # LEFT, RIGHT, FULL
            elif join_kind:
                join_type = join_kind  # INNER, CROSS, SEMI, ANTI
            else:
                join_type = "INNER"  # Default

            table_ref = TableReference(
                name=join.this.name if isinstance(join.this, exp.Table) else str(join.this),
                alias=join.this.alias if isinstance(join.this, exp.Table) else None
            )

            condition = None
            if join.args.get('on'):
                condition = join.args['on'].sql(dialect=self.dialect)

            joins.append(JoinInfo(
                type=join_type,
                table=table_ref,
                condition=condition
            ))

        return joins

    def _extract_where_conditions(self, ast: exp.Expression) -> List[WhereCondition]:
        """Extract WHERE conditions from the query."""
        conditions = []

        where = ast.find(exp.Where)
        if where:
            conditions.extend(self._parse_conditions(where.this))

        return conditions

    def _parse_conditions(self, expr: exp.Expression, table: Optional[str] = None) -> List[WhereCondition]:
        """Recursively parse boolean conditions."""
        conditions = []

        if isinstance(expr, exp.And):
            conditions.extend(self._parse_conditions(expr.left, table))
            conditions.extend(self._parse_conditions(expr.right, table))
        elif isinstance(expr, exp.Or):
            # For OR conditions, we still parse both sides
            conditions.extend(self._parse_conditions(expr.left, table))
            conditions.extend(self._parse_conditions(expr.right, table))
        elif isinstance(expr, exp.In):
            # IN clause: column IN (subquery) or column IN (values)
            col_name = None
            value = None
            operator = "IN"

            if isinstance(expr.this, exp.Column):
                col_name = expr.this.name
                table = expr.this.table or table
                # Value is the subquery or list
                if expr.args.get('query'):
                    value = expr.args['query'].sql(dialect=self.dialect)
                elif expr.expressions:
                    # Value list: IN (1, 2, 3)
                    value = [self._extract_value(v) for v in expr.expressions]
                else:
                    value = self._extract_value(expr.expression)
            elif isinstance(expr.expression, exp.Column):
                col_name = expr.expression.name
                table = expr.expression.table or table
                value = self._extract_value(expr.this)
                operator = "IN"  # Same operator

            if col_name:
                conditions.append(WhereCondition(
                    column=col_name,
                    operator=operator,
                    value=value,
                    table=table
                ))
        elif isinstance(expr, (exp.EQ, exp.NEQ, exp.LT, exp.LTE, exp.GT, exp.GTE,
                              exp.Like, exp.ILike, exp.Is, exp.Between)):
            # Binary comparison
            left = expr.left
            right = expr.right

            col_name = None
            value = None
            operator = expr.__class__.__name__.upper()

            if isinstance(left, exp.Column):
                col_name = left.name
                table = left.table or table
                value = self._extract_value(right)
            elif isinstance(right, exp.Column):
                col_name = right.name
                table = right.table or table
                value = self._extract_value(left)
                # Reverse operator for right-side column
                operator = self._reverse_operator(operator)

            if col_name:
                conditions.append(WhereCondition(
                    column=col_name,
                    operator=operator,
                    value=value,
                    table=table
                ))
        elif isinstance(expr, exp.Not):
            # Handle NOT conditions (NOT IN, IS NOT NULL, etc.)
            inner = expr.this
            # Check if the inner expression is a comparison we can handle
            if isinstance(inner, (exp.In, exp.Is)):
                inner_conditions = self._parse_conditions(inner, table)
                for c in inner_conditions:
                    c.operator = f"NOT {c.operator}"
                    conditions.append(c)
            else:
                # General NOT case
                inner_conditions = self._parse_conditions(inner, table)
                for c in inner_conditions:
                    c.operator = f"NOT {c.operator}"
                    conditions.append(c)

        return conditions

    def _extract_value(self, expr: exp.Expression) -> Any:
        """Extract a Python value from a sqlglot expression."""
        if isinstance(expr, exp.Literal):
            return expr.this
        elif isinstance(expr, exp.Column):
            return f"COL:{expr.name}"
        elif isinstance(expr, exp.Paren):
            return self._extract_value(expr.this)
        else:
            return expr.sql(dialect=self.dialect)

    def _reverse_operator(self, operator: str) -> str:
        """Reverse a comparison operator."""
        reversals = {
            'LT': 'GT', 'LTE': 'GTE', 'GT': 'LT', 'GTE': 'LTE',
            'EQ': 'EQ', 'NEQ': 'NEQ',
        }
        return reversals.get(operator, operator)

    def _extract_group_by(self, ast: exp.Expression) -> List[str]:
        """Extract GROUP BY columns."""
        group_by = []
        group = ast.find(exp.Group)
        if group:
            for expr in group.expressions:
                group_by.append(expr.sql(dialect=self.dialect))
        return group_by

    def _extract_having(self, ast: exp.Expression) -> List[str]:
        """Extract HAVING conditions."""
        having = []
        having_clause = ast.find(exp.Having)
        if having_clause:
            having.append(having_clause.this.sql(dialect=self.dialect))
        return having

    def _extract_order_by(self, ast: exp.Expression) -> List[Dict[str, Any]]:
        """Extract ORDER BY clauses."""
        order_by = []
        order = ast.find(exp.Order)
        if order:
            for expr in order.expressions:
                desc = False
                if isinstance(expr, exp.Ordered):
                    desc = expr.args.get('desc', False)
                    expr = expr.this

                order_by.append({
                    'column': expr.sql(dialect=self.dialect),
                    'direction': 'DESC' if desc else 'ASC'
                })
        return order_by

    def _extract_limit(self, ast: exp.Expression) -> Optional[int]:
        """Extract LIMIT value."""
        limit_expr = ast.find(exp.Limit)
        if limit_expr:
            try:
                return int(limit_expr.expression.this)
            except (ValueError, AttributeError):
                pass
        return None

    def _extract_window_functions(self, ast: exp.Expression) -> List[str]:
        """Extract window function calls."""
        windows = []
        for window in ast.find_all(exp.Window):
            windows.append(window.sql(dialect=self.dialect))
        return windows

    def _extract_aggregations(self, ast: exp.Expression) -> List[str]:
        """Extract aggregate function calls."""
        aggregations = []
        agg_funcs = (exp.Count, exp.Sum, exp.Avg, exp.Min, exp.Max,
                     exp.CountIf, exp.Stddev, exp.Variance)

        for agg in ast.find_all(agg_funcs):
            aggregations.append(agg.sql(dialect=self.dialect))

        return list(set(aggregations))  # Deduplicate

    def _extract_subqueries(self, ast: exp.Expression) -> List[str]:
        """Extract subqueries."""
        subqueries = []
        for subq in ast.find_all(exp.Subquery):
            subqueries.append(subq.sql(dialect=self.dialect))
        return subqueries

    def _extract_ctes(self, ast: exp.Expression) -> List[str]:
        """Extract CTE names."""
        ctes = []
        with_expr = ast.find(exp.With)
        if with_expr:
            for cte in with_expr.expressions:
                if isinstance(cte, exp.CTE):
                    ctes.append(cte.alias)
        return ctes

    def _get_error_context(self, sql: str, line: Optional[int]) -> Optional[str]:
        """Get context around the error line."""
        if line is None:
            return None
        lines = sql.split('\n')
        idx = line - 1
        if 0 <= idx < len(lines):
            return lines[idx]
        return None

    def _extract_join_columns(self, condition: str) -> List[Tuple[str, str, str, str]]:
        """
        Extract column pairs from JOIN condition.
        Returns list of (left_table, left_col, right_table, right_col).
        """
        import re
        results = []
        # Pattern: table1.col1 = table2.col2 (or with aliases)
        pattern = r'(\w+)\.(\w+)\s*=\s*(\w+)\.(\w+)'
        for match in re.finditer(pattern, condition):
            left_table, left_col, right_table, right_col = match.groups()
            results.append((left_table, left_col, right_table, right_col))
        return results

    def build_virtual_schema(self, parsed: ParsedQuery, use_llm: bool = False, llm_client=None) -> Dict[str, Any]:
        """
        Build a virtual schema from a parsed SQL query.

        Extracts tables, columns, and join relationships from the query structure.

        Args:
            parsed: ParsedQuery object from sqlglot parsing
            use_llm: Whether to use LLM to enhance schema (add types, PK/FK)
            llm_client: LLMClient instance for enhancement

        Returns:
            Schema dict with tables, columns, and relationships
        """
        tables = {}
        relationships = []

        # Track all tables and their columns
        table_columns = {}  # table_name -> set of column names
        table_aliases = {}  # alias -> table_name

        # First pass: collect tables from FROM/JOIN
        for table_ref in parsed.tables:
            table_name = table_ref.name.lower()
            if table_ref.alias:
                table_aliases[table_ref.alias.lower()] = table_name
            if table_name not in table_columns:
                table_columns[table_name] = set()

        # Second pass: collect columns from SELECT
        for col in parsed.columns:
            if col.name == '*' or col.is_alias:
                continue
            table_name = (col.table or '').lower()
            # Resolve alias to table name
            if table_name in table_aliases:
                table_name = table_aliases[table_name]
            if table_name and table_name in table_columns:
                table_columns[table_name].add(col.name)
            elif table_name == '' and table_columns:
                # Ambiguous column - add to all tables
                for t in table_columns:
                    table_columns[t].add(col.name)

        # Third pass: collect columns from WHERE conditions
        for cond in parsed.where_conditions:
            table_name = (cond.table or '').lower()
            if table_name in table_aliases:
                table_name = table_aliases[table_name]
            if table_name and table_name in table_columns:
                table_columns[table_name].add(cond.column)
            elif table_name == '' and table_columns:
                for t in table_columns:
                    table_columns[t].add(cond.column)

        # Fourth pass: collect columns from JOIN conditions
        for join in parsed.joins:
            if join.condition:
                join_pairs = self._extract_join_columns(join.condition)
                for left_table, left_col, right_table, right_col in join_pairs:
                    left_table = left_table.lower()
                    right_table = right_table.lower()
                    # Resolve aliases
                    if left_table in table_aliases:
                        left_table = table_aliases[left_table]
                    if right_table in table_aliases:
                        right_table = table_aliases[right_table]
                    if left_table in table_columns:
                        table_columns[left_table].add(left_col)
                    if right_table in table_columns:
                        table_columns[right_table].add(right_col)

                    # Build relationship
                    relationships.append({
                        "from_table": left_table,
                        "from_column": left_col,
                        "to_table": right_table,
                        "to_column": right_col,
                        "type": "one_to_many"  # Default, could be refined
                    })

        # Also check GROUP BY and ORDER BY for columns
        for gb in parsed.group_by:
            # gb is a string like "table.column" or just "column"
            if '.' in gb:
                table_name, col_name = gb.split('.', 1)
                table_name = table_name.lower()
                if table_name in table_aliases:
                    table_name = table_aliases[table_name]
                if table_name in table_columns:
                    table_columns[table_name].add(col_name)
            elif table_columns:
                for t in table_columns:
                    table_columns[t].add(gb)

        for ob in parsed.order_by:
            col_expr = ob.get('column', '')
            if '.' in col_expr:
                table_name, col_name = col_expr.split('.', 1)
                table_name = table_name.lower()
                if table_name in table_aliases:
                    table_name = table_aliases[table_name]
                if table_name in table_columns:
                    table_columns[table_name].add(col_name)
            elif table_columns:
                for t in table_columns:
                    table_columns[t].add(col_expr)

        # Build final tables dict with "unknown" types
        for table_name, columns in table_columns.items():
            tables[table_name] = {
                "columns": {col: "unknown" for col in columns}
            }

        # If LLM enhancement is requested and client available
        if use_llm and llm_client and llm_client.is_available():
            enhanced = self._enhance_schema_with_llm(tables, relationships, llm_client)
            return enhanced

        return {"tables": tables, "relationships": relationships}

    def _enhance_schema_with_llm(self, tables: Dict, relationships: List, llm_client) -> Dict[str, Any]:
        """
        Use LLM to enhance the inferred schema with:
        - Better data types (int, text, timestamp, numeric, boolean)
        - Primary key detection
        - Foreign key relationship refinement
        """
        # Build schema summary for LLM
        schema_summary = ""
        for table_name, table_def in tables.items():
            cols = list(table_def.get('columns', {}).keys())
            schema_summary += f"  {table_name}: {', '.join(cols)}\n"

        rel_summary = ""
        for rel in relationships:
            rel_summary += f"  {rel['from_table']}.{rel['from_column']} -> {rel['to_table']}.{rel['to_column']}\n"

        prompt = f"""Given this inferred schema from SQL query analysis, enhance it with proper data types, primary keys, and refined relationships.

Current tables:
{schema_summary}

Current relationships:
{rel_summary if rel_summary else "  (none detected)"}

Return ONLY valid JSON with this structure:
{{
  "tables": {{
    "table_name": {{
      "columns": {{
        "column_name": "type"  // one of: integer, text, timestamp, numeric, boolean, date, uuid
      }},
      "primary_key": ["column_name"]
    }}
  }},
  "relationships": [
    {{"from_table": "", "from_column": "", "to_table": "", "to_column": "", "type": "one_to_many|many_to_one|one_to_one|self_referential"}}
  ]
}}

Guidelines:
- id, *_id columns -> integer (likely PK/FK)
- name, email, city, status, category -> text
- created_at, updated_at, hire_date -> timestamp
- salary, amount, price, budget, total_amount -> numeric
- is_active, is_deleted -> boolean
- Tables usually have 'id' as primary key
- Relationships: if from_table.column matches to_table.id -> many_to_one from from_table to to_table"""

        try:
            response = llm_client.complete(prompt, max_tokens=1500, temperature=0.1)
            import json
            # Handle markdown code blocks
            json_str = response
            if '```json' in response:
                json_str = response.split('```json')[1].split('```')[0]
            elif '```' in response:
                json_str = response.split('```')[1].split('```')[0]

            enhanced = json.loads(json_str.strip())

            # Merge with existing - keep LLM enhancements but don't lose tables
            if 'tables' in enhanced:
                for table_name, table_def in enhanced['tables'].items():
                    if table_name in tables:
                        # Merge columns - LLM types override "unknown"
                        existing_cols = tables[table_name]['columns']
                        for col_name, col_type in table_def.get('columns', {}).items():
                            existing_cols[col_name] = col_type
                        if 'primary_key' in table_def:
                            tables[table_name]['primary_key'] = table_def['primary_key']
                    else:
                        tables[table_name] = table_def

            if 'relationships' in enhanced:
                relationships = enhanced['relationships']

        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"LLM schema enhancement failed: {e}")

        return {"tables": tables, "relationships": relationships}

    def format_for_json(self, parsed: ParsedQuery) -> Dict[str, Any]:
        """Convert ParsedQuery to JSON-serializable dict."""
        return {
            'operation_type': parsed.operation_type,
            'tables': [asdict(t) for t in parsed.tables],
            'columns': [asdict(c) for c in parsed.columns],
            'joins': [asdict(j) for j in parsed.joins],
            'where_conditions': [asdict(w) for w in parsed.where_conditions],
            'group_by': parsed.group_by,
            'having_conditions': parsed.having_conditions,
            'order_by': parsed.order_by,
            'limit': parsed.limit,
            'window_functions': parsed.window_functions,
            'aggregations': parsed.aggregations,
            'subqueries': parsed.subqueries,
            'cte_names': parsed.cte_names,
            'is_valid': parsed.is_valid,
            'errors': [asdict(e) for e in parsed.errors],
            'raw_sql': parsed.raw_sql,
            'dialect': parsed.dialect,
        }


# Singleton instance
_parser_instance = None


def get_parser(dialect: str = "postgres") -> SQLParserService:
    """Get or create the parser singleton."""
    global _parser_instance
    if _parser_instance is None or _parser_instance.dialect != dialect:
        _parser_instance = SQLParserService(dialect=dialect)
    return _parser_instance