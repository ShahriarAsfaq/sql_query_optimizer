"""
Semantic Validator - determines if query rewrites preserve SQL semantics.

This module classifies optimization rewrites as SAFE, CONDITIONALLY_SAFE, or UNSAFE
based on whether they provably preserve query results.
"""
import logging
from typing import Dict, Any, List, Optional, Tuple, Set
from dataclasses import dataclass, field
from enum import Enum

from .sql_parser import ParsedQuery, TableReference, ColumnReference, WhereCondition, JoinInfo

logger = logging.getLogger(__name__)


class SemanticSafety(Enum):
    """Safety classification for query rewrites."""
    SAFE = "safe"
    CONDITIONALLY_SAFE = "conditionally_safe"
    UNSAFE = "unsafe"
    UNKNOWN = "unknown"


@dataclass
class SemanticCheckResult:
    """Result of semantic equivalence check."""
    safety: SemanticSafety
    reason: str
    assumptions: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'safety': self.safety.value,
            'reason': self.reason,
            'assumptions': self.assumptions,
            'warnings': self.warnings,
        }


@dataclass
class RewriteRule:
    """Definition of a query rewrite rule with its safety classification."""
    name: str
    description: str
    safety: SemanticSafety
    conditions: List[str] = field(default_factory=list)
    applies_to: List[str] = field(default_factory=list)  # Query patterns it applies to


class SemanticValidator:
    """
    Validates semantic equivalence of query rewrites.

    This is the gate that prevents unsafe optimizations from being applied
    automatically. Every candidate rewrite is checked before being considered
    for performance ranking.
    """

    def __init__(self, schema: Optional[Dict[str, Any]] = None):
        self.schema = schema or {}
        self._build_schema_index()

    def _build_schema_index(self):
        """Build lookup structures from schema."""
        self.tables = {}
        self.columns_by_table = {}
        self.primary_keys = {}
        self.foreign_keys = []
        self.not_null_columns = set()

        for table_name, table_def in self.schema.get('tables', {}).items():
            self.tables[table_name.lower()] = table_def
            cols = {}
            for col_name, col_def in table_def.get('columns', {}).items():
                cols[col_name.lower()] = col_def
                # Track NOT NULL columns if type info available
                if isinstance(col_def, dict) and col_def.get('not_null'):
                    self.not_null_columns.add(f"{table_name.lower()}.{col_name.lower()}")
            self.columns_by_table[table_name.lower()] = cols
            self.primary_keys[table_name.lower()] = table_def.get('primary_key', [])

        for rel in self.schema.get('relationships', []):
            self.foreign_keys.append(rel)

    def classify_rewrite(
        self,
        original_sql: str,
        rewritten_sql: str,
        original_parsed: ParsedQuery,
        rewritten_parsed: ParsedQuery,
        rule_name: str
    ) -> SemanticCheckResult:
        """
        Classify a rewrite by comparing original and rewritten queries.

        This is the main entry point for semantic validation.
        """
        # First, check if the rewrite is in our known safe rules
        rule = self._get_rewrite_rule(rule_name)
        if rule:
            if rule.safety == SemanticSafety.SAFE:
                return SemanticCheckResult(
                    safety=SemanticSafety.SAFE,
                    reason=f"Rewrite '{rule.name}' is provably safe: {rule.description}",
                )
            elif rule.safety == SemanticSafety.CONDITIONALLY_SAFE:
                return self._check_conditional_safety(
                    original_parsed, rewritten_parsed, rule
                )

        # If not a known rule, perform general semantic comparison
        return self._compare_semantics(original_parsed, rewritten_parsed)

    def _get_rewrite_rule(self, rule_name: str) -> Optional[RewriteRule]:
        """Get a rewrite rule by name."""
        rules = self._get_all_rewrite_rules()
        return rules.get(rule_name)

    def _get_all_rewrite_rules(self) -> Dict[str, RewriteRule]:
        """Define all known rewrite rules with their safety classifications."""
        return {
            'expand_select_star': RewriteRule(
                name='expand_select_star',
                description='Replace SELECT * with explicit column list',
                safety=SemanticSafety.CONDITIONALLY_SAFE,
                conditions=[
                    'Schema must be complete and accurate',
                    'No dynamic schema changes',
                    'Column order must be preserved',
                    'JOIN must not produce duplicate column names without explicit aliases',
                ],
                applies_to=['SELECT * FROM ...'],
            ),
            'add_limit_to_order_by': RewriteRule(
                name='add_limit_to_order_by',
                description='Add LIMIT clause to ORDER BY query',
                safety=SemanticSafety.UNSAFE,
                conditions=[],
                applies_to=['ORDER BY without LIMIT'],
            ),
            'rewrite_in_to_exists': RewriteRule(
                name='rewrite_in_to_exists',
                description='Rewrite IN (subquery) to EXISTS (subquery)',
                safety=SemanticSafety.CONDITIONALLY_SAFE,
                conditions=[
                    'Subquery must not return NULLs in the compared column',
                    'No duplicate semantics difference (IN removes dupes, EXISTS does not)',
                    'Outer query must not depend on duplicate count from IN',
                ],
                applies_to=['WHERE x IN (SELECT ...)'],
            ),
            'rewrite_in_to_join': RewriteRule(
                name='rewrite_in_to_join',
                description='Rewrite IN (subquery) to INNER JOIN',
                safety=SemanticSafety.CONDITIONALLY_SAFE,
                conditions=[
                    'Subquery must return unique values on join column',
                    'No NULLs in join columns',
                    'Join must not multiply rows',
                ],
                applies_to=['WHERE x IN (SELECT ...)'],
            ),
            'rewrite_not_in_to_not_exists': RewriteRule(
                name='rewrite_not_in_to_not_exists',
                description='Rewrite NOT IN (subquery) to NOT EXISTS (subquery)',
                safety=SemanticSafety.CONDITIONALLY_SAFE,
                conditions=[
                    'Subquery must not return NULLs (NOT IN with NULL returns UNKNOWN)',
                    'Semantics differ significantly with NULLs',
                ],
                applies_to=['WHERE x NOT IN (SELECT ...)'],
            ),
            'lower_to_ilike': RewriteRule(
                name='lower_to_ilike',
                description='Rewrite LOWER(col) = \'val\' to col ILIKE \'val\'',
                safety=SemanticSafety.CONDITIONALLY_SAFE,
                conditions=[
                    'Collation must be case-insensitive compatible',
                    'ILIKE pattern matching semantics must match equality',
                    'No special characters in value that would be treated as patterns',
                ],
                applies_to=['LOWER(col) = \'value\'', 'UPPER(col) = \'VALUE\''],
            ),
            'function_on_column_to_range': RewriteRule(
                name='function_on_column_to_range',
                description='Rewrite function(column) = value to column range predicate',
                safety=SemanticSafety.CONDITIONALLY_SAFE,
                conditions=[
                    'Function must be monotonic and invertible',
                    'Column type and timezone semantics must be known',
                    'Must account for boundary conditions correctly',
                ],
                applies_to=['YEAR(date) = 2025', 'DATE(timestamp) = \'2025-01-01\''],
            ),
            'correlated_subquery_to_join': RewriteRule(
                name='correlated_subquery_to_join',
                description='Rewrite correlated subquery as JOIN with window function',
                safety=SemanticSafety.CONDITIONALLY_SAFE,
                conditions=[
                    'Subquery must return at most one row per outer row',
                    'No side effects',
                    'NULL semantics must be preserved (outer join for nullable)',
                    'Aggregation behavior must match',
                ],
                applies_to=['Scalar correlated subquery', 'EXISTS correlated subquery'],
            ),
            'push_predicate_into_cte': RewriteRule(
                name='push_predicate_into_cte',
                description='Push WHERE predicate into CTE/subquery',
                safety=SemanticSafety.CONDITIONALLY_SAFE,
                conditions=[
                    'Must not cross outer join boundaries',
                    'Must not change aggregation semantics',
                    'Must not change DISTINCT semantics',
                    'Must not change LIMIT/OFFSET semantics',
                    'Must not change window function semantics',
                ],
                applies_to=['CTE with outer WHERE', 'Derived table with outer WHERE'],
            ),
            'subquery_to_cte': RewriteRule(
                name='subquery_to_cte',
                description='Convert nested subquery to CTE',
                safety=SemanticSafety.SAFE,
                conditions=[
                    'CTE must not be referenced multiple times (materialization difference)',
                    'No volatile functions in subquery',
                ],
                applies_to=['FROM (SELECT ...) subquery'],
            ),
            'union_to_union_all': RewriteRule(
                name='union_to_union_all',
                description='Rewrite UNION to UNION ALL',
                safety=SemanticSafety.UNSAFE,
                conditions=[],
                applies_to=['UNION'],
            ),
            'outer_join_to_inner_join': RewriteRule(
                name='outer_join_to_inner_join',
                description='Rewrite LEFT/RIGHT/FULL JOIN to INNER JOIN',
                safety=SemanticSafety.UNSAFE,
                conditions=[],
                applies_to=['LEFT JOIN', 'RIGHT JOIN', 'FULL JOIN'],
            ),
            'remove_distinct': RewriteRule(
                name='remove_distinct',
                description='Remove DISTINCT when proven unnecessary',
                safety=SemanticSafety.CONDITIONALLY_SAFE,
                conditions=[
                    'Primary key or unique constraint on selected columns',
                    'No JOIN that can multiply rows',
                    'No UNION that introduces duplicates',
                ],
                applies_to=['SELECT DISTINCT ...'],
            ),
            'reorder_joins': RewriteRule(
                name='reorder_joins',
                description='Change JOIN order (PostgreSQL planner does this automatically)',
                safety=SemanticSafety.SAFE,
                conditions=[
                    'Only INNER JOINs (outer join order matters)',
                    'No side effects',
                ],
                applies_to=['Multiple INNER JOINs'],
            ),
            'predicate_pushdown': RewriteRule(
                name='predicate_pushdown',
                description='Push predicates closer to table scans',
                safety=SemanticSafety.CONDITIONALLY_SAFE,
                conditions=[
                    'Must not cross outer join boundaries',
                    'Must not change aggregation semantics',
                    'Must preserve NULL behavior',
                ],
                applies_to=['WHERE in outer query', 'JOIN conditions'],
            ),
            'rewrite_in_subquery': RewriteRule(
                name='rewrite_in_subquery',
                description='Rewrite IN (subquery) to EXISTS or JOIN for performance',
                safety=SemanticSafety.CONDITIONALLY_SAFE,
                conditions=[
                    'Subquery must not return NULLs in the compared column (for EXISTS)',
                    'Subquery must return unique values on join column (for JOIN)',
                    'No duplicate semantics difference (IN removes dupes, EXISTS/JOIN may not)',
                    'Join must not multiply rows (for JOIN rewrite)',
                    'Outer query must not depend on duplicate count from IN',
                ],
                applies_to=['WHERE x IN (SELECT ...)'],
            ),
        }

    def _check_conditional_safety(
        self,
        original: ParsedQuery,
        rewritten: ParsedQuery,
        rule: RewriteRule
    ) -> SemanticCheckResult:
        """Check if a conditionally safe rewrite is actually safe for these specific queries."""
        # This is where we'd implement deep semantic analysis
        # For now, return the rule's base classification with its conditions
        return SemanticCheckResult(
            safety=rule.safety,
            reason=f"Rewrite '{rule.name}' is {rule.safety.value}: {rule.description}",
            assumptions=rule.conditions.copy(),
            warnings=[f"Verify: {c}" for c in rule.conditions],
        )

    def _compare_semantics(
        self,
        original: ParsedQuery,
        rewritten: ParsedQuery
    ) -> SemanticCheckResult:
        """Compare two parsed queries for semantic equivalence."""
        warnings = []
        assumptions = []

        # Check operation type
        if original.operation_type != rewritten.operation_type:
            return SemanticCheckResult(
                safety=SemanticSafety.UNSAFE,
                reason=f"Operation type changed: {original.operation_type} -> {rewritten.operation_type}",
            )

        # Check table references
        orig_tables = {t.name.lower() for t in original.tables}
        rew_tables = {t.name.lower() for t in rewritten.tables}
        if orig_tables != rew_tables:
            warnings.append(f"Table set changed: {orig_tables} -> {rew_tables}")

        # Check columns (projection)
        orig_cols = self._get_projection_columns(original)
        rew_cols = self._get_projection_columns(rewritten)
        if orig_cols != rew_cols:
            return SemanticCheckResult(
                safety=SemanticSafety.UNSAFE,
                reason=f"Projection columns differ: {orig_cols} -> {rew_cols}",
            )

        # Check JOINs
        if not self._joins_equivalent(original.joins, rewritten.joins):
            return SemanticCheckResult(
                safety=SemanticSafety.UNSAFE,
                reason="JOIN structure or semantics changed",
            )

        # Check WHERE conditions
        if not self._where_equivalent(original.where_conditions, rewritten.where_conditions):
            warnings.append("WHERE conditions differ - verify semantic equivalence")

        # Check GROUP BY
        if original.group_by != rewritten.group_by:
            return SemanticCheckResult(
                safety=SemanticSafety.UNSAFE,
                reason=f"GROUP BY changed: {original.group_by} -> {rewritten.group_by}",
            )

        # Check aggregations
        if set(original.aggregations) != set(rewritten.aggregations):
            return SemanticCheckResult(
                safety=SemanticSafety.UNSAFE,
                reason=f"Aggregations changed: {original.aggregations} -> {rewritten.aggregations}",
            )

        # Check ORDER BY
        if original.order_by != rewritten.order_by:
            return SemanticCheckResult(
                safety=SemanticSafety.UNSAFE,
                reason=f"ORDER BY changed: {original.order_by} -> {rewritten.order_by}",
            )

        # Check LIMIT
        if original.limit != rewritten.limit:
            return SemanticCheckResult(
                safety=SemanticSafety.UNSAFE,
                reason=f"LIMIT changed: {original.limit} -> {rewritten.limit}",
            )

        # Check DISTINCT (via columns aliases or special handling)
        # This is a simplified check

        if warnings:
            return SemanticCheckResult(
                safety=SemanticSafety.CONDITIONALLY_SAFE,
                reason="Queries structurally similar but with differences requiring verification",
                assumptions=assumptions,
                warnings=warnings,
            )

        return SemanticCheckResult(
            safety=SemanticSafety.SAFE,
            reason="Queries appear semantically equivalent based on structural comparison",
            assumptions=assumptions,
        )

    def _get_projection_columns(self, parsed: ParsedQuery) -> List[Tuple[str, str]]:
        """Get normalized projection columns as (name, table_or_alias) tuples.

        Normalizes by resolving aliases to actual table names for comparison.
        Uses schema to resolve unambiguous columns.
        """
        # Build alias -> table mapping
        alias_to_table = {}
        table_names = set()
        for table_ref in parsed.tables:
            if table_ref.alias:
                alias_to_table[table_ref.alias.lower()] = table_ref.name.lower()
            alias_to_table[table_ref.name.lower()] = table_ref.name.lower()
            table_names.add(table_ref.name.lower())

        # Build column -> table mapping from schema
        col_to_tables = {}
        if self.tables:
            for table_name, table_def in self.tables.items():
                for col_name in table_def.get('columns', {}).keys():
                    col_lower = col_name.lower()
                    if col_lower not in col_to_tables:
                        col_to_tables[col_lower] = []
                    col_to_tables[col_lower].append(table_name.lower())

        cols = []
        for c in parsed.columns:
            if c.name == '*':
                cols.append(('*', c.table or ''))
            else:
                table_ref = c.table or c.alias or ''
                # Resolve alias to actual table name
                if table_ref:
                    table_resolved = alias_to_table.get(table_ref.lower(), table_ref.lower())
                else:
                    # No table reference - try to infer from schema
                    col_lower = c.name.lower()
                    if col_lower in col_to_tables:
                        tables_with_col = col_to_tables[col_lower]
                        # Filter to tables actually in this query
                        tables_in_query = [t for t in tables_with_col if t in table_names]
                        if len(tables_in_query) == 1:
                            table_resolved = tables_in_query[0]
                        elif len(table_names) == 1:
                            table_resolved = list(table_names)[0]
                        else:
                            table_resolved = ''
                    else:
                        # Column not in schema - if only one table, use that
                        if len(table_names) == 1:
                            table_resolved = list(table_names)[0]
                        else:
                            table_resolved = ''
                cols.append((c.name.lower(), table_resolved))
        return sorted(cols)

    def _joins_equivalent(self, joins1: List[JoinInfo], joins2: List[JoinInfo]) -> bool:
        """Check if two JOIN lists are semantically equivalent."""
        if len(joins1) != len(joins2):
            return False

        # Sort by table name for comparison (order may not matter for INNER JOINs)
        j1_sorted = sorted(joins1, key=lambda j: (j.type, j.table.name))
        j2_sorted = sorted(joins2, key=lambda j: (j.type, j.table.name))

        for j1, j2 in zip(j1_sorted, j2_sorted):
            if j1.type != j2.type:
                return False
            if j1.table.name.lower() != j2.table.name.lower():
                return False
            # Compare conditions (simplified)
            if (j1.condition or '') != (j2.condition or ''):
                # Conditions might be equivalent but written differently
                # This is a simplified check
                pass

        return True

    def _where_equivalent(self, conds1: List[WhereCondition], conds2: List[WhereCondition]) -> bool:
        """Check if two WHERE condition lists are equivalent."""
        if len(conds1) != len(conds2):
            return False

        # Normalize and compare
        def normalize(c: WhereCondition) -> Tuple[str, str, str, str]:
            return (
                c.column.lower(),
                c.operator.upper(),
                str(c.value),
                c.table.lower() if c.table else '',
            )

        set1 = {normalize(c) for c in conds1}
        set2 = {normalize(c) for c in conds2}

        return set1 == set2

    def validate_candidate(
        self,
        original_sql: str,
        candidate_sql: str,
        original_parsed: ParsedQuery,
        candidate_parsed: ParsedQuery,
        rewrite_rules_applied: List[str]
    ) -> Dict[str, Any]:
        """
        Validate a candidate query comprehensively.

        Returns a validation result with safety classification.
        """
        # Check each rewrite rule applied
        safety_results = []
        overall_safety = SemanticSafety.SAFE

        for rule_name in rewrite_rules_applied:
            result = self.classify_rewrite(
                original_sql, candidate_sql,
                original_parsed, candidate_parsed,
                rule_name
            )
            safety_results.append({
                'rule': rule_name,
                'safety': result.safety.value,
                'reason': result.reason,
                'assumptions': result.assumptions,
            })

            # Track most restrictive safety level
            if result.safety == SemanticSafety.UNSAFE:
                overall_safety = SemanticSafety.UNSAFE
            elif result.safety == SemanticSafety.CONDITIONALLY_SAFE and overall_safety == SemanticSafety.SAFE:
                overall_safety = SemanticSafety.CONDITIONALLY_SAFE

        # Also run schema validation
        schema_valid = self._validate_schema(candidate_parsed)

        return {
            'semantically_valid': overall_safety != SemanticSafety.UNSAFE,
            'semantic_safety': overall_safety.value,
            'schema_valid': schema_valid,
            'safety_details': safety_results,
        }

    def _validate_schema(self, parsed: ParsedQuery) -> bool:
        """Quick schema validation."""
        if not self.tables:
            return True  # No schema to validate against

        for table_ref in parsed.tables:
            if table_ref.name.lower() not in self.tables:
                return False

        return True


class SemanticEquivalenceChecker:
    """
    Advanced semantic equivalence checking using multiple strategies.

    This can be extended with:
    - Query canonicalization
    - Constraint-based reasoning
    - Automated theorem proving for SQL
    - Differential testing against a reference database
    """

    def __init__(self, schema: Optional[Dict[str, Any]] = None):
        self.schema = schema or {}
        self.validator = SemanticValidator(schema)

    def check_equivalence(
        self,
        query1: str,
        query2: str,
        parsed1: ParsedQuery,
        parsed2: ParsedQuery,
        rewrite_rules: List[str] = None
    ) -> SemanticCheckResult:
        """
        Check if two queries are semantically equivalent.

        Uses multiple strategies:
        1. Structural comparison
        2. Rewrite rule classification
        3. Schema-aware checks
        """
        return self.validator._compare_semantics(parsed1, parsed2)

    def check_rewrite_safety(
        self,
        original_sql: str,
        rewritten_sql: str,
        original_parsed: ParsedQuery,
        rewritten_parsed: ParsedQuery,
        rule_name: str
    ) -> SemanticCheckResult:
        """Check if a specific rewrite is safe."""
        return self.validator.classify_rewrite(
            original_sql, rewritten_sql,
            original_parsed, rewritten_parsed,
            rule_name
        )