"""
Optimization Opportunity Detector.

Given a deep ``QueryStructure`` (and an optional ``PlanAnalysis``), produce a
list of structured optimization opportunities. Opportunities are evidence-backed:
each carries a type, severity, the table/columns involved, human-readable evidence,
and a confidence score. Not every opportunity becomes a rewrite — opportunities
feed recommendations, explanations, and index/statistics advice.

Every opportunity type is listed in ``OPPORTUNITY_TYPES``.
"""
import logging
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field

from .query_analyzer import (
    QueryStructure, get_table_primary_key, get_table_indexes,
    get_column_type, column_is_not_null,
)
from .plan_analyzer import PlanAnalysis

logger = logging.getLogger(__name__)


def _column_covered_by_index(schema: Dict[str, Any], table, column) -> bool:
    """Return True if an existing index or the primary key already covers the column.

    When the schema is absent or declares nothing for this column, returns False
    so a MISSING_INDEX recommendation is still surfaced (conservative).
    """
    if not schema:
        return False
    tbl = str(table).split('.')[-1].strip().lower()
    col = str(column).split('.')[-1].strip().lower()
    pk = get_table_primary_key(schema, tbl)
    if any(c == col for c in pk):
        return True
    for idx in get_table_indexes(schema, tbl):
        if any(c == col for c in idx.get('columns', [])):
            return True
    return False


def _resolve_table_for_column(schema: Dict[str, Any], table, column) -> Optional[str]:
    """Resolve the schema table for a predicate column.

    Returns *table* when already known; otherwise, when the schema declares the
    (unqualified) column in exactly one table, returns that table. Returns None
    when ambiguous or the schema is absent — never guesses.
    """
    if table:
        return table
    col = str(column).split('.')[-1].strip().lower()
    if not schema or not schema.get('tables'):
        return None
    candidates = []
    for tbl, tdef in schema.get('tables', {}).items():
        cols = tdef.get('columns', {}) if isinstance(tdef, dict) else {}
        if any(str(c).lower() == col for c in cols):
            candidates.append(tbl)
    return candidates[0] if len(candidates) == 1 else None


@dataclass
class OptimizationOpportunity:
    """A single, evidence-backed optimization opportunity."""
    type: str
    severity: str = 'LOW'  # LOW | MEDIUM | HIGH
    table: Optional[str] = None
    columns: List[str] = field(default_factory=list)
    evidence: str = ''
    confidence: float = 0.5

    def to_dict(self) -> Dict[str, Any]:
        return {
            'type': self.type,
            'severity': self.severity,
            'table': self.table,
            'columns': self.columns,
            'evidence': self.evidence,
            'confidence': self.confidence,
        }


# The full set of opportunity types this detector can emit.
OPPORTUNITY_TYPES = {
    'MISSING_INDEX', 'NON_SARGABLE_PREDICATE', 'FUNCTION_ON_COLUMN',
    'CALCULATION_ON_COLUMN', 'LEADING_WILDCARD', 'SELECT_STAR',
    'EXCESSIVE_COLUMNS', 'FILTER_AFTER_LARGE_SCAN', 'IN_TO_EXISTS',
    'CORRELATED_SUBQUERY', 'SUBQUERY_TO_JOIN', 'CROSS_JOIN',
    'MISSING_JOIN_CONDITION', 'JOIN_BEFORE_FILTER', 'UNNECESSARY_DISTINCT',
    'EXPENSIVE_DISTINCT', 'UNNECESSARY_ORDER_BY', 'EXPENSIVE_SORT',
    'GROUP_BY_AFTER_LARGE_INPUT', 'UNION_TO_UNION_ALL', 'LARGE_OFFSET',
    'STALE_STATISTICS', 'POOR_ROW_ESTIMATION', 'SEQUENTIAL_SCAN',
    'INEFFICIENT_JOIN_STRATEGY', 'REDUNDANT_CONDITION',
}


def detect_optimization_opportunities(
    query: QueryStructure,
    plan: Optional[PlanAnalysis] = None,
    schema: Optional[Dict[str, Any]] = None,
) -> List[OptimizationOpportunity]:
    """
    Detect optimization opportunities from query structure and (optionally) plan.

    Args:
        query: Deep query structure from ``query_analyzer.analyze_query_structure``.
        plan: Optional ``PlanAnalysis`` from an EXPLAIN plan.
        schema: Optional schema dict used to gate some opportunities.

    Returns:
        List of ``OptimizationOpportunity`` objects (sorted by severity desc).
    """
    opps: List[OptimizationOpportunity] = []
    schema = schema or {}

    # --- SELECT / projection issues ---
    if query.select.has_star:
        opps.append(OptimizationOpportunity(
            type='SELECT_STAR', severity='MEDIUM',
            columns=['*'],
            evidence="SELECT * projects all columns; only needed columns should be projected.",
            confidence=0.9,
        ))

    if query.select.column_count > 10 and not query.select.has_star:
        opps.append(OptimizationOpportunity(
            type='EXCESSIVE_COLUMNS', severity='LOW',
            columns=[c for c in query.select.expression_columns][:10],
            evidence=f"SELECT projects {query.select.column_count} columns; consider trimming to only required columns.",
            confidence=0.5,
        ))

    # --- WHERE / sargability issues ---
    for rec in query.where.function_on_column:
        opps.append(OptimizationOpportunity(
            type='FUNCTION_ON_COLUMN', severity='MEDIUM' if rec['func'] in ('YEAR', 'MONTH', 'DAY', 'DATE', 'DATE_TRUNC', 'LOWER', 'UPPER') else 'LOW',
            table=rec['table'], columns=[rec['column']],
            evidence=f"Function {rec['func']}({rec['column']}) in predicate prevents index usage; rewrite as a range predicate.",
            confidence=0.8,
        ))

    for rec in query.where.arithmetic_on_column:
        opps.append(OptimizationOpportunity(
            type='CALCULATION_ON_COLUMN', severity='MEDIUM',
            table=rec['table'], columns=[rec['column']],
            evidence=f"Arithmetic on column ({rec['column']} {rec['op']} {rec['value']}) prevents index usage; move the constant to the other side.",
            confidence=0.8,
        ))

    for cond in query.where.leading_wildcards:
        opps.append(OptimizationOpportunity(
            type='LEADING_WILDCARD', severity='MEDIUM',
            table=cond.table, columns=[cond.column],
            evidence=f"LIKE pattern '{cond.value}' has a leading wildcard, preventing index range scan on {cond.column}.",
            confidence=0.9,
        ))

    # --- IN -> EXISTS / subquery opportunities ---
    for cond in query.where.in_conditions:
        if cond.is_subquery:
            # Honor the schema: IN -> EXISTS is only provably safe when the
            # outer column is NOT NULL (different NULL semantics). Resolve the
            # outer table from the schema when the column is unqualified.
            outer_table = _resolve_table_for_column(schema, cond.table, cond.column)
            not_null = bool(outer_table) and column_is_not_null(schema, outer_table, cond.column)
            if not_null:
                evidence = (f"IN (subquery) on {cond.column}; EXISTS is safe here "
                            f"(column is NOT NULL per schema).")
                confidence = 0.7
            else:
                evidence = (f"IN (subquery) on {cond.column}; EXISTS may be more "
                            f"efficient (safe only when NULLs are absent).")
                confidence = 0.5
            opps.append(OptimizationOpportunity(
                type='IN_TO_EXISTS', severity='MEDIUM',
                table=cond.table, columns=[cond.column],
                evidence=evidence,
                confidence=confidence,
            ))

    if query.subqueries:
        opps.append(OptimizationOpportunity(
            type='SUBQUERY_TO_JOIN', severity='LOW',
            evidence=f"{len(query.subqueries)} subquery(ies) present; consider rewriting as JOIN if semantics permit.",
            confidence=0.4,
        ))
        if any('correlated' in s.lower() or 'outer' in s.lower() for s in query.subqueries):
            opps.append(OptimizationOpportunity(
                type='CORRELATED_SUBQUERY', severity='MEDIUM',
                evidence="Correlated subquery detected; consider rewriting as JOIN/window function.",
                confidence=0.5,
            ))

    # --- JOIN issues ---
    for join in query.joins:
        if join.type == 'CROSS':
            opps.append(OptimizationOpportunity(
                type='CROSS_JOIN', severity='HIGH',
                table=join.table,
                evidence="CROSS JOIN without condition produces a cartesian product; add a join condition.",
                confidence=0.9,
            ))
        elif join.missing_condition:
            opps.append(OptimizationOpportunity(
                type='MISSING_JOIN_CONDITION', severity='HIGH',
                table=join.table,
                evidence=f"{join.type} JOIN on {join.table} has no ON condition.",
                confidence=0.9,
            ))
        elif join.expression_on_join_col and join.left_column and join.right_column:
            opps.append(OptimizationOpportunity(
                type='JOIN_BEFORE_FILTER', severity='LOW',
                table=join.table,
                columns=[join.left_column, join.right_column],
                evidence="Join column has an expression, preventing join index usage.",
                confidence=0.6,
            ))
        elif join.left_column and join.right_column:
            # Missing index on join column — skip when schema already covers it.
            if not _column_covered_by_index(schema, join.table, join.right_column):
                opps.append(OptimizationOpportunity(
                    type='MISSING_INDEX', severity='MEDIUM',
                    table=join.table, columns=[join.right_column],
                    evidence=f"JOIN on {join.left_column} = {join.right_column} benefits from an index on {join.table}.{join.right_column}.",
                    confidence=0.5,
                ))

    # WHERE filter after JOIN (filter on joined table, not just base)
    if query.joins and query.where.equalities:
        opps.append(OptimizationOpportunity(
            type='JOIN_BEFORE_FILTER', severity='LOW',
            evidence="Filters present alongside JOINs; consider pushing selective filters before joining.",
            confidence=0.4,
        ))

    # --- DISTINCT ---
    if query.distinct.distinct:
        if query.distinct.redundant_candidate:
            opps.append(OptimizationOpportunity(
                type='UNNECESSARY_DISTINCT', severity='MEDIUM',
                columns=query.distinct.distinct_on or [],
                evidence="DISTINCT appears redundant: selected columns already include the primary key and no row-multiplying join is present.",
                confidence=0.7,
            ))
        else:
            opps.append(OptimizationOpportunity(
                type='EXPENSIVE_DISTINCT', severity='LOW',
                evidence="DISTINCT requires a sort or hash; consider whether duplicates can exist.",
                confidence=0.4,
            ))

    # --- ORDER BY ---
    if query.order_by.columns and not query.limit_offset.limit:
        opps.append(OptimizationOpportunity(
            type='UNNECESSARY_ORDER_BY', severity='LOW',
            columns=query.order_by.columns,
            evidence="ORDER BY without LIMIT may sort the full result set unnecessarily.",
            confidence=0.6,
        ))

    # --- GROUP BY ---
    if query.group_by.columns:
        opps.append(OptimizationOpportunity(
            type='GROUP_BY_AFTER_LARGE_INPUT', severity='LOW',
            columns=query.group_by.columns,
            evidence="GROUP BY present; ensure the input is filtered/aggregated early.",
            confidence=0.4,
        ))

    # --- SET operations ---
    if query.set_ops.kind == 'UNION':
        opps.append(OptimizationOpportunity(
            type='UNION_TO_UNION_ALL', severity='LOW',
            evidence="UNION de-duplicates; UNION ALL is faster when branches are provably disjoint.",
            confidence=0.4,
        ))

    # --- LIMIT / OFFSET ---
    if query.limit_offset.large_offset:
        opps.append(OptimizationOpportunity(
            type='LARGE_OFFSET', severity='MEDIUM',
            evidence=f"OFFSET {query.limit_offset.offset} scans and discards many rows; consider keyset pagination.",
            confidence=0.8,
        ))

    # --- Statistics (stale/estimation) ---
    if query.where.equalities or query.where.ranges:
        for pred in list(query.where.equalities) + list(query.where.ranges):
            if pred.column:
                # Resolve the table from the schema when the column is
                # unqualified, so schema-declared columns are honored.
                eff_table = pred.table or _resolve_table_for_column(
                    schema, pred.table, pred.column)
                if not eff_table:
                    continue
                # Only flag a MISSING_INDEX when the column is not already
                # covered by an index/PK declared in the schema.
                if not _column_covered_by_index(schema, eff_table, pred.column):
                    opps.append(OptimizationOpportunity(
                        type='MISSING_INDEX', severity='LOW',
                        table=pred.table or eff_table, columns=[pred.column],
                        evidence=f"Filter on {eff_table}.{pred.column} may benefit from an index.",
                        confidence=0.4,
                    ))
                    break

    # --- Plan-based opportunities ---
    if plan is not None:
        _detect_plan_opportunities(query, plan, opps)

    # Sort: HIGH first, then MEDIUM, then LOW; stable within severity.
    sev_order = {'HIGH': 0, 'MEDIUM': 1, 'LOW': 2}
    opps.sort(key=lambda o: (sev_order.get(o.severity, 3), o.type))
    return opps


def _detect_plan_opportunities(
    query: QueryStructure,
    plan: PlanAnalysis,
    opps: List[OptimizationOpportunity],
):
    """Add plan-derived opportunities (sequential scans, sorts, joins, stats)."""
    if plan.seq_scans > 0 and plan.index_scans == 0:
        opps.append(OptimizationOpportunity(
            type='SEQUENTIAL_SCAN', severity='MEDIUM',
            table=', '.join(plan.seq_scan_tables[:3]) or None,
            evidence=f"Plan uses {plan.seq_scans} sequential scan(s) with no index scans; consider indexes or smaller scans.",
            confidence=0.6,
        ))

    if plan.sorts > 0:
        opps.append(OptimizationOpportunity(
            type='EXPENSIVE_SORT', severity='MEDIUM',
            evidence=f"Plan performs {plan.sorts} sort(s); consider an index that provides the ordering.",
            confidence=0.6,
        ))

    if plan.nested_loops > 0 and (plan.hash_joins + plan.merge_joins) > 0:
        opps.append(OptimizationOpportunity(
            type='INEFFICIENT_JOIN_STRATEGY', severity='LOW',
            evidence=f"Mixed join strategies ({plan.nested_loops} nested loop, {plan.hash_joins} hash, {plan.merge_joins} merge).",
            confidence=0.4,
        ))

    if plan.estimation_error_nodes > 0 and plan.avg_estimation_error > 2.0:
        opps.append(OptimizationOpportunity(
            type='POOR_ROW_ESTIMATION', severity='MEDIUM',
            evidence=f"Average row-estimation error is {plan.avg_estimation_error:.1f}x; statistics may be stale.",
            confidence=0.6,
        ))

    if plan.total_cost > 0 and plan.other_cost / plan.total_cost > 0.5:
        opps.append(OptimizationOpportunity(
            type='REDUNDANT_CONDITION', severity='LOW',
            evidence="Large share of plan cost is in generic nodes; verify predicate selectivity.",
            confidence=0.3,
        ))


def opportunity_list_to_dicts(opps: List[OptimizationOpportunity]) -> List[Dict[str, Any]]:
    """Serialize a list of opportunities (objects or already-serialized dicts)."""
    out = []
    for o in opps:
        if isinstance(o, dict):
            out.append(o)
        else:
            out.append(o.to_dict())
    return out
