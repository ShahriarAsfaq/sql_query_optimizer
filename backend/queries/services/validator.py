"""
Validation Service - validates parsed SQL against schema, safety rules, and semantic expectations.
"""
from typing import Dict, List, Any, Optional, Set
from dataclasses import dataclass, asdict
from enum import Enum
import logging

from ..services.sql_parser import ParsedQuery, TableReference, ColumnReference, WhereCondition

logger = logging.getLogger(__name__)


class ValidationSeverity(Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass
class ValidationIssue:
    """Represents a validation issue."""
    severity: ValidationSeverity
    code: str
    message: str
    location: Optional[str] = None
    suggestion: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return {
            'severity': self.severity.value if isinstance(self.severity, ValidationSeverity) else self.severity,
            'code': self.code,
            'message': self.message,
            'location': self.location,
            'suggestion': self.suggestion,
        }


@dataclass
class ValidationResult:
    """Result of validation."""
    is_valid: bool
    issues: List[ValidationIssue]
    tables_found: List[str]
    tables_missing: List[str]
    columns_found: List[str]
    columns_missing: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            'is_valid': self.is_valid,
            'issues': [i.to_dict() for i in self.issues],
            'tables_found': self.tables_found,
            'tables_missing': self.tables_missing,
            'columns_found': self.columns_found,
            'columns_missing': self.columns_missing,
        }


class ValidationService:
    """
    Validates SQL queries against schema, safety rules, and semantic expectations.
    """

    # Dangerous operations that should be rejected in read-only mode
    DANGEROUS_OPERATIONS = {
        'DROP', 'DELETE', 'UPDATE', 'TRUNCATE', 'ALTER',
        'CREATE', 'INSERT', 'REPLACE', 'MERGE', 'GRANT', 'REVOKE'
    }

    # Operations allowed in read-only mode
    READ_ONLY_OPERATIONS = {'SELECT', 'WITH', 'EXPLAIN', 'SHOW', 'DESCRIBE'}

    def __init__(self, schema: Dict[str, Any], read_only: bool = True):
        """
        Initialize validator with a schema.

        Args:
            schema: Dictionary describing tables, columns, types, and relationships
            read_only: If True, reject non-SELECT operations
        """
        self.schema = schema
        self.read_only = read_only
        self._build_schema_index()

    def _build_schema_index(self):
        """Build lookup structures from schema."""
        self.tables = {}
        self.columns_by_table = {}
        self.column_types = {}
        self.primary_keys = {}
        self.foreign_keys = []

        for table_name, table_def in self.schema.get('tables', {}).items():
            self.tables[table_name.lower()] = table_def
            cols = {}
            for col_name, col_def in table_def.get('columns', {}).items():
                cols[col_name.lower()] = col_def
                # Handle both dict format (with 'type' key) and simple string format
                if isinstance(col_def, dict):
                    col_type = col_def.get('type', 'unknown')
                else:
                    col_type = col_def
                self.column_types[f"{table_name.lower()}.{col_name.lower()}"] = col_type
            self.columns_by_table[table_name.lower()] = cols
            self.primary_keys[table_name.lower()] = table_def.get('primary_key', [])

        for rel in self.schema.get('relationships', []):
            self.foreign_keys.append(rel)

    def validate(self, parsed: ParsedQuery) -> Dict[str, Any]:
        """
        Run all validations on a parsed query.

        Args:
            parsed: ParsedQuery object from SQLParserService

        Returns:
            ValidationResult as dictionary
        """
        issues = []
        tables_found = []
        tables_missing = []
        columns_found = []
        columns_missing = []

        # 1. Syntax validation (already done in parser)
        if not parsed.is_valid:
            for error in parsed.errors:
                issues.append(ValidationIssue(
                    severity=ValidationSeverity.ERROR,
                    code='SYNTAX_ERROR',
                    message=error.message,
                    location=f"line {error.line}, col {error.column}" if error.line else None,
                    suggestion=error.context
                ))

        # 2. Safety validation
        issues.extend(self._validate_safety(parsed))

        # 3. Schema validation
        schema_issues, t_found, t_missing, c_found, c_missing = self._validate_schema(parsed)
        issues.extend(schema_issues)
        tables_found = t_found
        tables_missing = t_missing
        columns_found = c_found
        columns_missing = c_missing

        # 4. Semantic validation
        issues.extend(self._validate_semantics(parsed))

        is_valid = not any(i.severity == ValidationSeverity.ERROR for i in issues)

        return ValidationResult(
            is_valid=is_valid,
            issues=issues,
            tables_found=tables_found,
            tables_missing=tables_missing,
            columns_found=columns_found,
            columns_missing=columns_missing
        ).to_dict()

    def _validate_safety(self, parsed: ParsedQuery) -> List[ValidationIssue]:
        """Validate query safety (no dangerous operations in read-only mode)."""
        issues = []

        if self.read_only and parsed.operation_type not in self.READ_ONLY_OPERATIONS:
            issues.append(ValidationIssue(
                severity=ValidationSeverity.ERROR,
                code='READ_ONLY_VIOLATION',
                message=f"Operation '{parsed.operation_type}' not allowed in read-only mode",
                suggestion="Use SELECT/WITH queries only, or disable read-only mode"
            ))

        # Check for dangerous operations even in non-read-only mode (warn)
        if parsed.operation_type in self.DANGEROUS_OPERATIONS:
            issues.append(ValidationIssue(
                severity=ValidationSeverity.WARNING,
                code='DANGEROUS_OPERATION',
                message=f"Operation '{parsed.operation_type}' modifies data",
                suggestion="Ensure this is intentional and you have proper backups"
            ))

        # Check for SELECT * in production-like queries
        if parsed.operation_type == 'SELECT':
            has_star = any(col.name == '*' for col in parsed.columns)
            if has_star and len(parsed.tables) > 0:
                issues.append(ValidationIssue(
                    severity=ValidationSeverity.WARNING,
                    code='SELECT_STAR',
                    message="Using SELECT * - consider explicit column list",
                    suggestion="List columns explicitly for better performance and stability"
                ))

        return issues

    def _validate_schema(self, parsed: ParsedQuery) -> tuple:
        """Validate tables and columns exist in schema."""
        issues = []
        tables_found = []
        tables_missing = []
        columns_found = []
        columns_missing = []

        # Check tables
        for table_ref in parsed.tables:
            table_name = table_ref.name.lower()
            if table_name in self.tables:
                tables_found.append(table_ref.name)
            else:
                tables_missing.append(table_ref.name)
                issues.append(ValidationIssue(
                    severity=ValidationSeverity.ERROR,
                    code='TABLE_NOT_FOUND',
                    message=f"Table '{table_ref.name}' not found in schema",
                    location=table_ref.alias or table_ref.name,
                    suggestion=f"Available tables: {', '.join(self.tables.keys())}"
                ))

        # Build alias -> table resolution map from parsed tables
        alias_to_table = {}
        for tr in parsed.tables:
            if tr.alias:
                alias_to_table[tr.alias.lower()] = tr.name.lower()

        # Check columns
        for col_ref in parsed.columns:
            if col_ref.name == '*':
                continue

            # Skip validation for aliases (SELECT aliases used in ORDER BY, etc.)
            if getattr(col_ref, 'is_alias', False):
                columns_found.append(col_ref.name)
                continue

            table_name = (col_ref.table or '').lower()
            # Resolve alias to actual table name for schema validation
            if table_name in alias_to_table:
                table_name = alias_to_table[table_name]
            col_name = col_ref.name.lower()

            # If column has explicit table, validate against that table
            if table_name and table_name in self.columns_by_table:
                if col_name in self.columns_by_table[table_name]:
                    columns_found.append(f"{table_name}.{col_name}")
                else:
                    columns_missing.append(f"{table_name}.{col_name}")
                    issues.append(ValidationIssue(
                        severity=ValidationSeverity.ERROR,
                        code='COLUMN_NOT_FOUND',
                        message=f"Column '{col_ref.name}' not found in table '{table_name}'",
                        location=f"{table_name}.{col_name}",
                        suggestion=f"Available columns: {', '.join(self.columns_by_table[table_name].keys())}"
                    ))
            # If no table specified, check all tables (ambiguous column)
            elif table_name == '':
                found_in = []
                for t_name, cols in self.columns_by_table.items():
                    if col_name in cols:
                        found_in.append(t_name)
                        columns_found.append(f"{t_name}.{col_name}")

                if not found_in:
                    columns_missing.append(col_name)
                    issues.append(ValidationIssue(
                        severity=ValidationSeverity.ERROR,
                        code='COLUMN_NOT_FOUND',
                        message=f"Column '{col_ref.name}' not found in any table",
                        location=col_ref.name,
                        suggestion="Specify table name or check column spelling"
                    ))
                elif len(found_in) > 1:
                    issues.append(ValidationIssue(
                        severity=ValidationSeverity.WARNING,
                        code='AMBIGUOUS_COLUMN',
                        message=f"Column '{col_ref.name}' is ambiguous - exists in multiple tables: {', '.join(found_in)}",
                        location=col_ref.name,
                        suggestion="Qualify with table name or alias"
                    ))

        return issues, tables_found, tables_missing, columns_found, columns_missing

    def _validate_semantics(self, parsed: ParsedQuery) -> List[ValidationIssue]:
        """Validate semantic correctness (structural expectations)."""
        issues = []

        if parsed.operation_type != 'SELECT':
            return issues

        # Check GROUP BY with aggregates
        has_aggregates = len(parsed.aggregations) > 0
        has_group_by = len(parsed.group_by) > 0

        if has_aggregates and not has_group_by:
            # Check if all selected columns are aggregated
            non_agg_cols = [
                c for c in parsed.columns
                if c.name != '*' and not any(c.name in agg for agg in parsed.aggregations)
            ]
            if non_agg_cols:
                issues.append(ValidationIssue(
                    severity=ValidationSeverity.WARNING,
                    code='MISSING_GROUP_BY',
                    message="Aggregates used without GROUP BY - non-aggregated columns in SELECT",
                    suggestion="Add GROUP BY clause or wrap non-aggregated columns in aggregate functions"
                ))

        # Check HAVING without GROUP BY
        if parsed.having_conditions and not has_group_by:
            issues.append(ValidationIssue(
                severity=ValidationSeverity.WARNING,
                code='HAVING_WITHOUT_GROUP_BY',
                message="HAVING clause used without GROUP BY",
                suggestion="Add GROUP BY clause or move condition to WHERE"
            ))

        # Check for ORDER BY without LIMIT (potential performance issue)
        if parsed.order_by and parsed.limit is None:
            issues.append(ValidationIssue(
                severity=ValidationSeverity.INFO,
                code='ORDER_BY_NO_LIMIT',
                message="ORDER BY without LIMIT - may return large result set",
                suggestion="Consider adding LIMIT for pagination"
            ))

        # Check for non-sargable predicates (functions on indexed columns in WHERE)
        for cond in parsed.where_conditions:
            if self._is_non_sargable(cond):
                issues.append(ValidationIssue(
                    severity=ValidationSeverity.WARNING,
                    code='NON_SARGABLE_PREDICATE',
                    message=f"Non-sargable predicate on column '{cond.column}': {cond.operator} {cond.value}",
                    location=f"WHERE {cond.column} {cond.operator} ?",
                    suggestion="Avoid functions on columns in WHERE; consider computed columns or indexes"
                ))

        # Check JOIN without ON condition
        for join in parsed.joins:
            if join.type != 'CROSS' and not join.condition:
                issues.append(ValidationIssue(
                    severity=ValidationSeverity.WARNING,
                    code='JOIN_WITHOUT_CONDITION',
                    message=f"{join.type} JOIN on '{join.table.name}' without ON condition",
                    suggestion="Add ON clause or use CROSS JOIN explicitly"
                ))

        # Check for correlated subqueries that could be rewritten
        if parsed.subqueries:
            for sq in parsed.subqueries:
                if 'correlated' in sq.lower() or self._looks_correlated(sq):
                    issues.append(ValidationIssue(
                        severity=ValidationSeverity.INFO,
                        code='CORRELATED_SUBQUERY',
                        message="Correlated subquery detected - consider rewriting as JOIN or window function",
                        location=sq[:100],
                        suggestion="Rewrite as JOIN with window function for better performance"
                    ))

        return issues

    def _is_non_sargable(self, cond: WhereCondition) -> bool:
        """Check if a WHERE condition is non-sargable (uses function on column)."""
        # Common non-sargable patterns
        non_sargable_funcs = {'LOWER', 'UPPER', 'TRIM', 'SUBSTR', 'SUBSTRING',
                              'YEAR', 'MONTH', 'DAY', 'DATE_TRUNC', 'TO_CHAR',
                              'CAST', 'CONVERT', 'COALESCE', 'NVL', 'ISNULL'}

        # Check if value contains a function call on the column
        val_str = str(cond.value).upper()
        col_upper = cond.column.upper()

        for func in non_sargable_funcs:
            if f"{func}({col_upper}" in val_str or f"{func}( {col_upper}" in val_str:
                return True

        return False

    def _looks_correlated(self, subquery: str) -> bool:
        """Heuristic to detect correlated subqueries."""
        subquery_lower = subquery.lower()
        # Look for references to outer query tables
        correlated_indicators = ['outer', 'correlated', 'exists (select', 'in (select']
        return any(ind in subquery_lower for ind in correlated_indicators)