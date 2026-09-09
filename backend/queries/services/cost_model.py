"""
Labeled Heuristic Cost Model.

Computes a structural cost estimate for queries when no EXPLAIN plan is
available. The result is ALWAYS labeled `cost_source="heuristic"` and is never
presented as a real PostgreSQL cost. It is deterministic and intended only for
relative ranking within the same environment.

Supersedes ``OptimizerService._estimate_cost_from_structure``; that method
becomes a thin delegate so existing callers/tests keep working.
"""
import logging
import re
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field

from .sql_parser import get_parser, ParsedQuery
from .query_analyzer import (
    analyze_query_structure,
    QueryStructure,
    get_column_type,
)

logger = logging.getLogger(__name__)

# Selectivity assumptions (rows returned as a fraction of the table).
SEL_EQUALITY = 0.01
SEL_RANGE = 0.10
SEL_LIKE = 0.05
SEL_OR = 0.20


@dataclass
class HeuristicCost:
    """A labeled heuristic cost estimate."""
    total: float = 0.0
    startup: float = 0.0
    breakdown: Dict[str, float] = field(default_factory=dict)
    flags: List[str] = field(default_factory=list)
    cost_source: str = "heuristic"

    def to_dict(self) -> Dict[str, Any]:
        return {
            'total': self.total,
            'startup': self.startup,
            'breakdown': self.breakdown,
            'flags': self.flags,
            'cost_source': self.cost_source,
        }


class HeuristicCostModel:
    """Structural heuristic cost estimation."""

    def __init__(self, schema: Optional[Dict[str, Any]] = None):
        self.schema = schema or {}
        self._parser = get_parser()

    def estimate(
        self,
        sql: str,
        parsed: Optional[ParsedQuery] = None,
        structure: Optional[QueryStructure] = None,
    ) -> HeuristicCost:
        """
        Estimate cost from structure. ``parsed``/``structure`` are optional;
        they are computed on demand if absent.
        """
        try:
            if parsed is None:
                parsed = self._parser.parse(sql)
            q = structure or analyze_query_structure(parsed, sql, self.schema)

            if not parsed.is_valid:
                return HeuristicCost(
                    total=0.0, startup=0.0, breakdown={},
                    flags=['invalid_query'], cost_source='heuristic',
                )

            return self._estimate_from_structure(sql, parsed, q)
        except Exception:
            logger.warning('Heuristic cost estimation failed; returning baseline', exc_info=True)
            return HeuristicCost(
                total=1.0, startup=0.0, breakdown={'base': 1.0},
                flags=['estimation_error'], cost_source='heuristic',
            )

    def _estimate_from_structure(self, sql: str, parsed: ParsedQuery,
                                 q: QueryStructure) -> HeuristicCost:
        breakdown: Dict[str, float] = {}
        flags: List[str] = []
        cost = 1.0  # base

        # --- Table scans ---
        n_tables = len(q.tables)
        scan_cost = n_tables * 10.0
        # Selectivity adjustment: a selective equality on the driving table
        # reduces the base scan estimate. Non-sargable predicates raise it.
        selective = False
        if q.where.equalities:
            selective = True
        if q.where.ranges:
            scan_cost *= 0.7  # range filter still reduces output modestly
        cost += scan_cost

        # --- Predicates ---
        pred_cost = 0.0
        for _ in q.where.equalities:
            pred_cost += 2.0  # indexable equality
        for _ in q.where.ranges:
            pred_cost += 3.0
        for _ in q.where.like_conditions:
            pred_cost += 4.0
        for cond in q.where.leading_wildcards:
            pred_cost += 8.0
            flags.append(f'leading_wildcard_like:{cond.column}')
        for rec in q.where.function_on_column:
            pred_cost += 6.0  # non-sargable
            flags.append(f'function_on_column:{rec["func"]}({rec["column"]})')
        for rec in q.where.arithmetic_on_column:
            pred_cost += 6.0
            flags.append(f'arithmetic_on_column:{rec["column"]}{rec["op"]}{rec["value"]}')
        cost += pred_cost

        # --- OR groups (bitmap OR / non-indexable) ---
        if len(q.where.or_groups) > 1:
            cost += 5.0

        # --- IN / EXISTS / subqueries ---
        in_subqueries = sum(1 for c in q.where.in_conditions if c.is_subquery)
        if in_subqueries:
            cost += in_subqueries * 20.0
        if q.where.exists_conditions:
            cost += len(q.where.exists_conditions) * 15.0
        for sub_q in q.subqueries:
            cost += 10.0  # uncorrelated subquery
            if 'correlated' in sub_q.lower() or 'outer' in sub_q.lower():
                cost += 40.0
                flags.append('correlated_subquery')

        # --- Joins ---
        join_cost = 0.0
        for j in q.joins:
            if j.type == 'CROSS':
                join_cost += 40.0
                flags.append('cross_join')
            elif j.type in ('LEFT', 'RIGHT', 'FULL'):
                join_cost += 18.0
            elif any(op in (j.condition or '') for op in ('LOWER(', 'UPPER(', 'YEAR(')):
                join_cost += 20.0
            else:
                join_cost += 10.0  # default join (treated as nested loop)
        cost += join_cost

        # --- DISTINCT / GROUP BY / ORDER BY ---
        if q.distinct.distinct:
            if q.distinct.distinct_on and not q.distinct.redundant_candidate:
                cost += 8.0
                flags.append('distinct_on')
            elif q.distinct.redundant_candidate:
                cost += 4.0  # likely removable
            else:
                cost += 10.0  # sort/hash dedup
        if q.group_by.columns:
            cost += 5.0 + 1.5 * len(q.group_by.columns)
        if q.order_by.columns:
            cost += 5.0 + 1.0 * len(q.order_by.columns)

        # --- SET operations ---
        if q.set_ops.kind:
            if q.set_ops.kind == 'UNION':
                cost += 15.0 * max(q.set_ops.branch_count - 1, 1)  # dedup
            else:
                cost += 10.0 * max(q.set_ops.branch_count - 1, 1)

        # --- CTEs / nested ---
        if q.cte_names:
            cost += 5.0 * len(q.cte_names)

        # --- SELECT projection ---
        if q.select.has_star:
            cost += 5.0
            flags.append('select_star')
        elif q.select.column_count > 5:
            cost += 0.5 * (q.select.column_count - 5)

        # --- Window functions ---
        if q.select.window_functions:
            cost += 10.0 * len(q.select.window_functions)

        # --- LIMIT / OFFSET ---
        if q.limit_offset.limit is not None and q.limit_offset.limit > 0:
            if q.limit_offset.limit < 100:
                cost *= 0.5
            else:
                cost *= 0.9
        if q.limit_offset.offset:
            cost += q.limit_offset.offset * 0.05
            if q.limit_offset.large_offset:
                flags.append('large_offset')

        breakdown = {
            'scan': round(scan_cost, 2),
            'predicates': round(pred_cost, 2),
            'joins': round(join_cost, 2),
            'base': 1.0,
        }

        return HeuristicCost(
            total=round(cost, 2),
            startup=round(1.0, 2),
            breakdown=breakdown,
            flags=flags,
            cost_source='heuristic',
        )


def estimate_cost_from_structure(
    sql: str,
    parsed: Optional[ParsedQuery] = None,
    structure: Optional[QueryStructure] = None,
    schema: Optional[Dict[str, Any]] = None,
) -> HeuristicCost:
    """Module-level convenience: single use, no model state needed."""
    return HeuristicCostModel(schema=schema).estimate(sql, parsed=parsed, structure=structure)