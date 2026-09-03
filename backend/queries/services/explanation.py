"""
Explanation Service - generates plain-English explanations of SQL queries from the AST.
"""
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
import logging

from ..services.sql_parser import ParsedQuery, TableReference, ColumnReference, JoinInfo, WhereCondition, ParseError
from ..services.llm_client import LLMClient, MockLLMClient

logger = logging.getLogger(__name__)


@dataclass
class ExplanationStep:
    """A single step in the query execution explanation."""
    order: int
    operation: str
    description: str
    details: Dict[str, Any]


class ExplanationService:
    """
    Generates human-readable explanations of SQL queries based on the parsed AST.
    Rule-based, no LLM required for core functionality.
    """

    def __init__(self, use_llm_polish: bool = False, llm_client=None, use_llm_intent: bool = False):
        """
        Initialize explanation service.

        Args:
            use_llm_polish: Whether to use LLM to polish the explanation
            llm_client: Optional LLM client for polishing
            use_llm_intent: Whether to use LLM to generate natural language intent explanation
        """
        self.use_llm_polish = use_llm_polish
        self.use_llm_intent = use_llm_intent
        self.llm_client = llm_client

    def explain(self, parsed: ParsedQuery) -> str:
        """
        Generate a plain-English explanation of the query.

        Args:
            parsed: ParsedQuery object from SQLParserService

        Returns:
            Human-readable explanation string
        """
        if not parsed.is_valid:
            return self._format_syntax_errors(parsed)

        steps = self._build_execution_steps(parsed)
        explanation = self._format_explanation(steps, parsed)

        if self.use_llm_polish and self.llm_client:
            explanation = self._polish_with_llm(explanation, parsed)

        return explanation

    def _build_execution_steps(self, parsed: ParsedQuery) -> List[ExplanationStep]:
        """Build ordered execution steps based on SQL logical execution order."""
        steps = []
        step_num = 0

        # 1. FROM / JOINs - data sources
        if parsed.tables:
            step_num += 1
            table_names = [t.name + (f" AS {t.alias}" if t.alias else "") for t in parsed.tables]
            if parsed.joins:
                join_descs = []
                for j in parsed.joins:
                    join_descs.append(f"{j.type} JOIN {j.table.name}" +
                                    (f" AS {j.table.alias}" if j.table.alias else "") +
                                    (f" ON {j.condition}" if j.condition else ""))
                steps.append(ExplanationStep(
                    order=step_num,
                    operation="FROM/JOIN",
                    description=f"Start with table {table_names[0]}, then {', '.join(join_descs)}",
                    details={'tables': table_names, 'joins': join_descs}
                ))
            else:
                steps.append(ExplanationStep(
                    order=step_num,
                    operation="FROM",
                    description=f"Read data from table(s): {', '.join(table_names)}",
                    details={'tables': table_names}
                ))

        # 2. WHERE - row filtering
        if parsed.where_conditions:
            step_num += 1
            cond_descs = []
            for c in parsed.where_conditions:
                table_prefix = f"{c.table}." if c.table else ""
                val_str = self._format_value(c.value)
                cond_descs.append(f"{table_prefix}{c.column} {c.operator} {val_str}")
            steps.append(ExplanationStep(
                order=step_num,
                operation="WHERE",
                description=f"Filter rows where: {' AND '.join(cond_descs)}",
                details={'conditions': [self._condition_to_dict(c) for c in parsed.where_conditions]}
            ))

        # 3. GROUP BY - grouping
        if parsed.group_by:
            step_num += 1
            steps.append(ExplanationStep(
                order=step_num,
                operation="GROUP BY",
                description=f"Group rows by: {', '.join(parsed.group_by)}",
                details={'group_by_columns': parsed.group_by}
            ))

        # 4. Aggregates - computed per group
        if parsed.aggregations:
            step_num += 1
            steps.append(ExplanationStep(
                order=step_num,
                operation="AGGREGATE",
                description=f"Compute aggregates: {', '.join(parsed.aggregations)}",
                details={'aggregations': parsed.aggregations}
            ))

        # 5. HAVING - post-aggregation filtering
        if parsed.having_conditions:
            step_num += 1
            steps.append(ExplanationStep(
                order=step_num,
                operation="HAVING",
                description=f"Filter groups where: {' AND '.join(parsed.having_conditions)}",
                details={'conditions': parsed.having_conditions}
            ))

        # 6. Window functions
        if parsed.window_functions:
            step_num += 1
            steps.append(ExplanationStep(
                order=step_num,
                operation="WINDOW FUNCTIONS",
                description=f"Apply window functions: {', '.join(parsed.window_functions)}",
                details={'functions': parsed.window_functions}
            ))

        # 7. SELECT - column projection
        step_num += 1
        col_descs = []
        for c in parsed.columns:
            if c.name == '*':
                col_descs.append("all columns (*)")
            else:
                table_prefix = f"{c.table}." if c.table else ""
                alias_suffix = f" AS {c.alias}" if c.alias else ""
                col_descs.append(f"{table_prefix}{c.name}{alias_suffix}")
        steps.append(ExplanationStep(
            order=step_num,
            operation="SELECT",
            description=f"Select columns: {', '.join(col_descs)}",
            details={'columns': col_descs}
        ))

        # 8. ORDER BY - sorting
        if parsed.order_by:
            step_num += 1
            order_descs = [f"{o['column']} {o['direction']}" for o in parsed.order_by]
            steps.append(ExplanationStep(
                order=step_num,
                operation="ORDER BY",
                description=f"Sort results by: {', '.join(order_descs)}",
                details={'order_by': parsed.order_by}
            ))

        # 9. LIMIT - row limiting
        if parsed.limit:
            step_num += 1
            steps.append(ExplanationStep(
                order=step_num,
                operation="LIMIT",
                description=f"Limit to first {parsed.limit} row(s)",
                details={'limit': parsed.limit}
            ))

        return steps

    def _format_explanation(self, steps: List[ExplanationStep], parsed: ParsedQuery) -> str:
        """Format execution steps into a readable explanation."""
        if not steps:
            return "Empty query."

        lines = []

        # Summary line
        op_type = parsed.operation_type
        if op_type == 'SELECT':
            summary = "This query retrieves data"
        elif op_type == 'WITH':
            summary = "This query uses Common Table Expressions (CTEs) to"
        else:
            summary = f"This query performs a {op_type} operation"

        if parsed.tables:
            table_names = [t.name for t in parsed.tables]
            summary += f" from {', '.join(table_names)}"
        lines.append(summary + ".")
        lines.append("")

        # Execution steps
        lines.append("Execution steps (in order):")
        lines.append("")

        for step in steps:
            lines.append(f"  {step.order}. **{step.operation}**: {step.description}")

        lines.append("")

        # Key characteristics
        characteristics = []
        if parsed.joins:
            characteristics.append(f"• Joins {len(parsed.joins)} table(s)")
        if parsed.where_conditions:
            characteristics.append(f"• Filters with {len(parsed.where_conditions)} condition(s)")
        if parsed.group_by:
            characteristics.append(f"• Groups by {len(parsed.group_by)} column(s)")
        if parsed.aggregations:
            characteristics.append(f"• Computes {len(parsed.aggregations)} aggregate(s)")
        if parsed.window_functions:
            characteristics.append(f"• Uses {len(parsed.window_functions)} window function(s)")
        if parsed.order_by:
            characteristics.append(f"• Orders by {len(parsed.order_by)} column(s)")
        if parsed.limit:
            characteristics.append(f"• Limits to {parsed.limit} row(s)")
        if parsed.cte_names:
            characteristics.append(f"• Uses {len(parsed.cte_names)} CTE(s): {', '.join(parsed.cte_names)}")
        if parsed.subqueries:
            characteristics.append(f"• Contains {len(parsed.subqueries)} subquery(s)")

        if characteristics:
            lines.append("Key characteristics:")
            lines.extend(characteristics)

        return "\n".join(lines)

    def _format_syntax_errors(self, parsed: ParsedQuery) -> str:
        """
        Format detailed syntax error information for invalid queries.

        Args:
            parsed: ParsedQuery object with errors

        Returns:
            Human-readable syntax error explanation
        """
        if not parsed.errors:
            return "Invalid query: Unknown error"

        lines = []
        lines.append("[ERROR] **Syntax Error(s) Found**")
        lines.append("")

        for i, error in enumerate(parsed.errors, 1):
            lines.append(f"**Error {i}:** {error.message}")

            if error.line is not None:
                lines.append(f"  [LOCATION] **Location:** Line {error.line}" +
                           (f", Column {error.column}" if error.column is not None else ""))

            if error.context:
                lines.append(f"  [CONTEXT] **Context:** `{error.context.strip()}`")

                # Show a pointer to the error column if available
                if error.column is not None and error.column > 0:
                    pointer = " " * (error.column - 1) + "^"
                    lines.append(f"         {pointer}")

            lines.append("")

        # Add helpful hints based on common error patterns
        hints = self._generate_error_hints(parsed.errors, parsed.raw_sql)
        if hints:
            lines.append("[SUGGESTIONS] **Suggestions:**")
            for hint in hints:
                lines.append(f"  * {hint}")
            lines.append("")

        lines.append("---")
        lines.append(f"**Query Type:** {parsed.operation_type}")
        if parsed.tables:
            lines.append(f"**Tables:** {', '.join(t.name for t in parsed.tables)}")

        return "\n".join(lines)

    def _generate_error_hints(self, errors: List[ParseError], sql: str) -> List[str]:
        """Generate helpful hints based on error patterns."""
        hints = []
        sql_upper = sql.upper()

        for error in errors:
            msg = error.message.lower()

            # Common SQL syntax error patterns and hints
            if "syntax error" in msg or "unexpected" in msg:
                if "select" in msg and "from" not in sql_upper:
                    hints.append("Missing FROM clause - every SELECT query needs a FROM table")
                elif "where" in msg and "where" not in sql_upper:
                    hints.append("WHERE clause syntax issue - check column names and operators")
                elif "group" in msg or "order" in msg:
                    hints.append("Check GROUP BY / ORDER BY syntax - column names must match SELECT list")
                else:
                    hints.append("Check for missing keywords, extra commas, or unmatched parentheses")

            elif "table" in msg and "not found" in msg:
                hints.append("Table name may be misspelled or schema-qualified name needed")

            elif "column" in msg and ("not found" in msg or "unknown" in msg):
                hints.append("Column name may be misspelled or need table qualification (table.column)")

            elif "alias" in msg:
                hints.append("Check alias syntax - use 'AS alias_name' or just 'alias_name'")

            elif "function" in msg or "aggregate" in msg:
                hints.append("Function name may be misspelled or not supported in this dialect")

            elif "join" in msg:
                hints.append("JOIN syntax issue - check ON condition and table names")

            elif "parentheses" in msg or "paren" in msg:
                hints.append("Unmatched parentheses - check all opening '(' have closing ')'")

        # General hints based on raw SQL
        if sql.strip().upper() == "SELECT *":
            hints.append("Incomplete query - SELECT * needs a FROM clause")

        if sql.count('(') != sql.count(')'):
            hints.append(f"Mismatched parentheses: {sql.count('(')} opening vs {sql.count(')')} closing")

        if sql.strip().endswith(','):
            hints.append("Trailing comma detected - remove comma after last column/table")

        # Remove duplicates while preserving order
        seen = set()
        unique_hints = []
        for hint in hints:
            if hint not in seen:
                seen.add(hint)
                unique_hints.append(hint)

        return unique_hints

    def _format_value(self, value: Any) -> str:
        """Format a value for display in explanation."""
        if value is None:
            return "NULL"
        if isinstance(value, str):
            if len(value) > 50:
                return f"'{value[:47]}...'"
            return f"'{value}'"
        if isinstance(value, (list, tuple)):
            if len(value) > 5:
                return f"[{', '.join(str(v) for v in value[:5])}, ...]"
            return f"[{', '.join(str(v) for v in value)}]"
        return str(value)

    def _condition_to_dict(self, cond: WhereCondition) -> Dict[str, Any]:
        """Convert WhereCondition to dict."""
        return {
            'column': cond.column,
            'table': cond.table,
            'operator': cond.operator,
            'value': cond.value
        }

    def _polish_with_llm(self, explanation: str, parsed: ParsedQuery) -> str:
        """Use LLM to polish the explanation (optional)."""
        if not self.llm_client:
            return explanation

        try:
            prompt = f"""Polish this SQL query explanation to be more natural and readable.
Keep all technical details accurate. Don't add or remove information.

Explanation:
{explanation}

Query type: {parsed.operation_type}
Tables: {[t.name for t in parsed.tables]}

Return only the polished explanation."""

            response = self.llm_client.complete(prompt, max_tokens=500)
            return response.strip()
        except Exception as e:
            logger.warning(f"LLM polish failed: {e}")
            return explanation

    def explain_intent(self, parsed: ParsedQuery, schema: Optional[Dict[str, Any]] = None) -> str:
        """
        Generate a natural language explanation of what the user wanted to do.
        Uses LLM to create a human-friendly description of the query's purpose.

        Args:
            parsed: ParsedQuery object from SQLParserService
            schema: Optional database schema for context

        Returns:
            Natural language explanation of the query's intent
        """
        if not parsed.is_valid:
            return "Invalid query - cannot determine intent."

        if not self.use_llm_intent or not self.llm_client:
            return self._rule_based_intent(parsed, schema)

        try:
            # Build context for LLM
            schema_summary = self._summarize_schema(schema) if schema else "No schema provided"

            prompt = f"""Generate a natural language description of what this SQL query is trying to achieve.
Write it as if explaining to a business user - focus on WHAT the user wants, not HOW the query works.

Schema:
{schema_summary}

Query:
{parsed.raw_sql}

Parsed structure:
- Operation: {parsed.operation_type}
- Tables: {[t.name for t in parsed.tables]}
- Columns: {[c.name for c in parsed.columns]}
- Joins: {[f'{j.type} JOIN {j.table.name}' for j in parsed.joins]}
- WHERE conditions: {[f'{c.column} {c.operator} {c.value}' for c in parsed.where_conditions]}
- GROUP BY: {parsed.group_by}
- Aggregations: {parsed.aggregations}
- ORDER BY: {parsed.order_by}
- LIMIT: {parsed.limit}

Return a clear, concise sentence describing the business intent.
Examples:
- "Find the top 10 highest-paid employees in the Engineering department"
- "Calculate the average salary for each department"
- "Show all customers who placed orders in the last 30 days"
- "Show me every customer, and if they have a completed order, show that too"
- "List all employees with their department names"
- "Find products where price is greater than 100"

Return ONLY the natural language description."""

            response = self.llm_client.complete(prompt, max_tokens=300, temperature=0.2)
            return response.strip()
        except Exception as e:
            logger.warning(f"LLM intent explanation failed: {e}")
            return self._rule_based_intent(parsed, schema)

    def _rule_based_intent(self, parsed: ParsedQuery, schema: Optional[Dict[str, Any]] = None) -> str:
        """Generate intent explanation using rule-based approach (fallback)."""
        if not parsed.tables:
            return "Query retrieves data from unspecified tables."

        # Detect the primary intent category
        intent = self._detect_intent_category(parsed)
        return self._format_intent_by_category(parsed, intent, schema)

    def _detect_intent_category(self, parsed: ParsedQuery) -> str:
        """Detect the high-level intent category from the query structure."""
        # TOP_N: has ORDER BY + LIMIT
        if parsed.limit and parsed.order_by:
            return 'TOP_N'

        # AGGREGATE: has aggregations with GROUP BY
        if parsed.aggregations and parsed.group_by:
            return 'AGGREGATE'

        # AGGREGATE_SINGLE: has aggregations without GROUP BY (single value)
        if parsed.aggregations:
            return 'AGGREGATE_SINGLE'

        # JOIN: has joins
        if parsed.joins:
            return 'JOIN'

        # FILTER: has WHERE conditions
        if parsed.where_conditions:
            return 'FILTER'

        # SORT: has ORDER BY but no LIMIT
        if parsed.order_by:
            return 'SORT'

        # RETRIEVE: default
        return 'RETRIEVE'

    def _format_intent_by_category(self, parsed: ParsedQuery, category: str, schema: Optional[Dict[str, Any]] = None) -> str:
        """Format a natural language intent based on the detected category."""

        if category == 'TOP_N':
            return self._format_top_n_intent(parsed, schema)
        elif category == 'AGGREGATE':
            return self._format_aggregate_intent(parsed, schema)
        elif category == 'AGGREGATE_SINGLE':
            return self._format_aggregate_single_intent(parsed, schema)
        elif category == 'JOIN':
            return self._format_join_intent(parsed, schema)
        elif category == 'FILTER':
            return self._format_filter_intent(parsed, schema)
        elif category == 'SORT':
            return self._format_sort_intent(parsed, schema)
        else:
            return self._format_retrieve_intent(parsed, schema)

    def _format_top_n_intent(self, parsed: ParsedQuery, schema: Optional[Dict[str, Any]] = None) -> str:
        """Format intent for TOP_N queries (ORDER BY + LIMIT)."""
        table = parsed.tables[0].name if parsed.tables else "records"

        # Determine direction
        direction = parsed.order_by[0].get('direction', 'DESC') if parsed.order_by else 'DESC'
        order_col = parsed.order_by[0].get('column', '') if parsed.order_by else ''

        # Find the metric column being ordered
        metric = self._extract_metric_from_order(parsed, order_col, schema)

        # Build the "top/bottom" description
        if direction == 'DESC':
            if metric:
                # Try to make it natural: "highest paid", "largest", etc.
                if any(kw in metric.lower() for kw in ['salary', 'pay', 'wage', 'compensation', 'income', 'earnings']):
                    desc = "highest-paid"
                elif any(kw in metric.lower() for kw in ['price', 'cost', 'amount', 'revenue', 'sales']):
                    desc = "highest-revenue"
                elif any(kw in metric.lower() for kw in ['date', 'time', 'created', 'updated']):
                    desc = "most recent"
                else:
                    desc = f"top {metric}"
            else:
                desc = "top"
        else:  # ASC
            if metric:
                if any(kw in metric.lower() for kw in ['salary', 'pay', 'wage', 'compensation', 'income', 'earnings']):
                    desc = "lowest-paid"
                elif any(kw in metric.lower() for kw in ['price', 'cost', 'amount']):
                    desc = "lowest-cost"
                elif any(kw in metric.lower() for kw in ['date', 'time', 'created', 'updated']):
                    desc = "oldest"
                else:
                    desc = f"bottom {metric}"
            else:
                desc = "bottom"

        # Build filter description
        filter_desc = self._format_filters_natural(parsed.where_conditions)

        # Build join description
        join_desc = self._format_joins_natural(parsed.joins)

        # Build result columns
        select_desc = self._format_select_natural(parsed.columns, parsed)

        parts = []
        if desc == "top" or desc == "bottom":
            parts.append(f"Find the {desc} {parsed.limit} {table}")
        else:
            parts.append(f"Find the {parsed.limit} {desc} {table}")

        if filter_desc:
            parts.append(filter_desc)

        if join_desc:
            parts.append(join_desc)

        if select_desc:
            parts.append(f"showing {select_desc}")

        return " ".join(parts) + "."

    def _format_aggregate_intent(self, parsed: ParsedQuery, schema: Optional[Dict[str, Any]] = None) -> str:
        """Format intent for aggregate queries with GROUP BY."""
        table = parsed.tables[0].name if parsed.tables else "records"

        # Determine aggregate type
        agg_type = "aggregate"
        for agg in parsed.aggregations:
            agg_upper = agg.upper()
            if 'AVG' in agg_upper or 'AVERAGE' in agg_upper:
                agg_type = "average"
                break
            elif 'SUM' in agg_upper:
                agg_type = "total"
                break
            elif 'COUNT' in agg_upper:
                agg_type = "count of"
                break
            elif 'MIN' in agg_upper:
                agg_type = "minimum"
                break
            elif 'MAX' in agg_upper:
                agg_type = "maximum"
                break

        # Get metric being aggregated
        metric = self._extract_metric_from_aggregation(parsed.aggregations)

        # Build group by description
        group_by_cols = parsed.group_by
        if len(group_by_cols) == 1:
            group_desc = f"each {group_by_cols[0]}"
        elif len(group_by_cols) > 1:
            group_desc = f"each {', '.join(group_by_cols[:-1])} and {group_by_cols[-1]}"
        else:
            group_desc = ""

        # Build filter description
        filter_desc = self._format_filters_natural(parsed.where_conditions)

        parts = []
        if metric:
            parts.append(f"Calculate the {agg_type} {metric} for {group_desc}")
        else:
            parts.append(f"Calculate {agg_type} for {group_desc}")

        if filter_desc:
            parts.append(filter_desc)

        return " ".join(parts) + "."

    def _format_aggregate_single_intent(self, parsed: ParsedQuery, schema: Optional[Dict[str, Any]] = None) -> str:
        """Format intent for single aggregate value (no GROUP BY)."""
        table = parsed.tables[0].name if parsed.tables else "records"

        # Determine aggregate type
        agg_type = "aggregate"
        for agg in parsed.aggregations:
            agg_upper = agg.upper()
            if 'AVG' in agg_upper or 'AVERAGE' in agg_upper:
                agg_type = "average"
                break
            elif 'SUM' in agg_upper:
                agg_type = "total"
                break
            elif 'COUNT' in agg_upper:
                agg_type = "count of"
                break
            elif 'MIN' in agg_upper:
                agg_type = "minimum"
                break
            elif 'MAX' in agg_upper:
                agg_type = "maximum"
                break

        metric = self._extract_metric_from_aggregation(parsed.aggregations)
        filter_desc = self._format_filters_natural(parsed.where_conditions)

        parts = []
        if metric:
            parts.append(f"Calculate the {agg_type} {metric}")
        else:
            parts.append(f"Calculate the {agg_type}")

        parts.append(f"from {table}")

        if filter_desc:
            parts.append(filter_desc)

        return " ".join(parts) + "."

    def _format_join_intent(self, parsed: ParsedQuery, schema: Optional[Dict[str, Any]] = None) -> str:
        """Format intent for JOIN queries."""
        if not parsed.tables:
            return "Combine data from multiple tables."

        main_table = parsed.tables[0].name
        join_descs = []
        join_conditions = []

        for j in parsed.joins:
            table_name = j.table.name
            # Remove join type prefix for natural language
            join_type = j.type.lower() if j.type else "join"
            join_descs.append(table_name)

            # Check if there's a condition in the ON clause
            if j.condition:
                join_conditions.append(f"where {j.condition}")

        filter_desc = self._format_filters_natural(parsed.where_conditions)
        select_desc = self._format_select_natural(parsed.columns, parsed)

        # Convert plural table names to singular for "every X" grammar
        def to_singular(table_name: str) -> str:
            """Convert plural table name to singular for grammar."""
            if table_name.endswith('ies'):
                return table_name[:-3] + 'y'
            elif table_name.endswith('es'):
                return table_name[:-2]
            elif table_name.endswith('s'):
                return table_name[:-1]
            return table_name

        main_table_singular = to_singular(main_table)
        join_descs_singular = [to_singular(t) for t in join_descs]

        # Build natural language based on join type
        if parsed.joins:
            join_type = parsed.joins[0].type.lower() if parsed.joins[0].type else "join"
            if join_type == 'left':
                parts = [f"Show me every {main_table_singular}, and if they have a matching {', '.join(join_descs_singular)}, show that too"]
            elif join_type == 'right':
                parts = [f"Show me every {', '.join(join_descs_singular)}, and if they have a matching {main_table_singular}, show that too"]
            elif join_type == 'inner':
                parts = [f"Show {main_table} with their {', '.join(join_descs)}"]
            else:
                parts = [f"Combine {main_table} with {', '.join(join_descs)}"]
        else:
            parts = [f"Show {main_table} with their {', '.join(join_descs)}"]

        if join_conditions:
            parts.append(", ".join(join_conditions))

        if filter_desc:
            parts.append(filter_desc.replace("Filtered by ", "where "))

        if select_desc:
            parts.append(f"showing {select_desc}")

        return " ".join(parts) + "."

    def _format_filter_intent(self, parsed: ParsedQuery, schema: Optional[Dict[str, Any]] = None) -> str:
        """Format intent for filter queries."""
        table = parsed.tables[0].name if parsed.tables else "records"
        filter_desc = self._format_filters_natural(parsed.where_conditions)
        select_desc = self._format_select_natural(parsed.columns, parsed)

        parts = [f"Find {table}"]

        if filter_desc:
            parts.append(filter_desc.replace("Filtered by ", "where "))

        if select_desc:
            parts.append(f"showing {select_desc}")

        return " ".join(parts) + "."

    def _format_sort_intent(self, parsed: ParsedQuery, schema: Optional[Dict[str, Any]] = None) -> str:
        """Format intent for sort-only queries."""
        table = parsed.tables[0].name if parsed.tables else "records"
        order_col = parsed.order_by[0].get('column', '') if parsed.order_by else ''
        direction = parsed.order_by[0].get('direction', 'ASC') if parsed.order_by else 'ASC'

        dir_word = "highest to lowest" if direction == 'DESC' else "lowest to highest"

        parts = [f"List {table} sorted by {order_col} ({dir_word})"]
        return " ".join(parts) + "."

    def _format_retrieve_intent(self, parsed: ParsedQuery, schema: Optional[Dict[str, Any]] = None) -> str:
        """Format intent for simple retrieve queries."""
        table = parsed.tables[0].name if parsed.tables else "records"
        select_desc = self._format_select_natural(parsed.columns, parsed)

        if select_desc:
            return f"Show {select_desc} from {table}."
        return f"Retrieve all data from {table}."

    def _extract_metric_from_order(self, parsed: ParsedQuery, order_col: str, schema: Optional[Dict[str, Any]]) -> Optional[str]:
        """Extract a human-readable metric name from the ORDER BY column."""
        if not order_col:
            return None

        # Clean up column name
        col = order_col.split('.')[-1]  # Remove table prefix

        # Common patterns
        if any(kw in col.lower() for kw in ['salary', 'pay', 'wage', 'compensation', 'income', 'earnings']):
            return "salary"
        elif any(kw in col.lower() for kw in ['price', 'cost', 'amount', 'revenue', 'sales']):
            return "revenue"
        elif any(kw in col.lower() for kw in ['date', 'time', 'created', 'updated']):
            return "date"

        return col.replace('_', ' ')

    def _extract_metric_from_aggregation(self, aggregations: List[str]) -> Optional[str]:
        """Extract metric name from aggregation strings."""
        for agg in aggregations:
            # Patterns: AVG(salary), SUM(total_amount), COUNT(*)
            import re
            match = re.search(r'\(([^)]+)\)', agg)
            if match:
                col = match.group(1)
                if col != '*':
                    return col.replace('_', ' ')
                else:
                    # COUNT(*) -> "records"
                    return "records"
        return None

    def _format_filters_natural(self, conditions: List[Any]) -> str:
        """Format WHERE conditions in natural language."""
        if not conditions:
            return ""

        parts = []
        for c in conditions:
            col = c.column.split('.')[-1]  # Remove table prefix
            op = c.operator
            val = self._format_value(c.value)

            # Natural language for operators
            if op in ('=', 'EQ'):
                parts.append(f"{col} is {val}")
            elif op in ('!=', 'NEQ', '<>'):
                parts.append(f"{col} is not {val}")
            elif op in ('>', 'GT'):
                parts.append(f"{col} greater than {val}")
            elif op in ('>=', 'GTE'):
                parts.append(f"{col} at least {val}")
            elif op in ('<', 'LT'):
                parts.append(f"{col} less than {val}")
            elif op in ('<=', 'LTE'):
                parts.append(f"{col} at most {val}")
            elif op in ('LIKE', 'ILIKE'):
                parts.append(f"{col} like {val}")
            elif op in ('IN',):
                parts.append(f"{col} in {val}")
            elif op in ('NOT IN',):
                parts.append(f"{col} not in {val}")
            else:
                parts.append(f"{col} {op} {val}")

        if len(parts) == 1:
            return f"where {parts[0]}"
        elif len(parts) == 2:
            return f"where {parts[0]} and {parts[1]}"
        else:
            return f"where {', '.join(parts[:-1])}, and {parts[-1]}"

    def _format_joins_natural(self, joins: List[Any]) -> str:
        """Format JOINs in natural language."""
        if not joins:
            return ""

        parts = []
        for j in joins:
            table = j.table.name
            join_type = j.type.lower() if j.type else "join"
            parts.append(f"{join_type} {table}")

        return "joined with " + ", ".join(parts)

    def _format_select_natural(self, columns: List[Any], parsed: ParsedQuery) -> str:
        """Format SELECT columns in natural language."""
        if not columns:
            return ""

        # Check for duplicate column names across tables to decide if we need table prefix
        name_to_tables = {}
        for c in columns:
            if c.name == '*':
                continue
            if c.name not in name_to_tables:
                name_to_tables[c.name] = set()
            if c.table:
                name_to_tables[c.name].add(c.table)

        select_items = []
        for c in columns:
            if c.name == '*':
                continue  # Skip * as it's implied
            name = c.name
            if c.alias:
                name = c.alias

            # Add table prefix if this column name exists in multiple tables
            if c.table and c.name in name_to_tables and len(name_to_tables[c.name]) > 1:
                name = f"{c.table}.{name}"

            select_items.append(name.replace('_', ' '))

        if not select_items:
            return ""

        if len(select_items) == 1:
            return select_items[0]
        elif len(select_items) == 2:
            return f"{select_items[0]} and {select_items[1]}"
        else:
            return f"{', '.join(select_items[:-1])}, and {select_items[-1]}"

    def _summarize_schema(self, schema: Dict[str, Any]) -> str:
        """Create a brief schema summary for LLM context."""
        lines = []
        for table_name, table_def in schema.get('tables', {}).items():
            cols = list(table_def.get('columns', {}).keys())
            lines.append(f"  {table_name}: {', '.join(cols)}")
        return "\n".join(lines)

    def explain_step_by_step(self, parsed: ParsedQuery) -> List[Dict[str, Any]]:
        """Get structured step-by-step explanation for UI rendering."""
        steps = self._build_execution_steps(parsed)
        return [
            {
                'step': s.order,
                'operation': s.operation,
                'description': s.description,
                'details': s.details
            }
            for s in steps
        ]