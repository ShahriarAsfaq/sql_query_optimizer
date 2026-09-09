"""
Query Optimization Thinking Engine.

Reorients the optimizer from rewrite-centric ("which SQL rewrite rules can I
apply?") to intent / cost-flow driven reasoning:

    "What is the query trying to accomplish, where is the work happening, where
    are rows generated/discarded, and what logically-equivalent strategy can
    produce the required result with less work?"

Everything in this module is ADDITIVE and evidence-driven: it reads the parsed
``QueryStructure``, the baseline ``PlanAnalysis`` (when available), and the
schema, and produces structured reasoning (intents, a cost-flow model, the
expensive-work boundary, Top-N reasoning, early-termination analysis, index
reasoning with a PURPOSE, a strategy decision, and a plan comparison that
answers WHY). It never mutates candidates or ranking, and it never fabricates
rewards: when evidence is absent, results report INSUFFICIENT_EVIDENCE rather
than guess.

Hard counterexample respected throughout: never propose
``JOIN (SELECT ... ORDER BY ... LIMIT n)`` as a "top-N rewrite" — that changes
result semantics (globally top-N vs top-N among qualifying rows). Top-N is
solved by *supporting* the existing ORDER BY + LIMIT (index ordering /
removing the sort), never by restructuring the query.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .query_analyzer import (
    QueryStructure,
    get_table_primary_key,
    get_table_indexes,
)
from .plan_analyzer import PlanAnalysis, PlanNodeMetrics

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Intents the engine can classify (generic, not hard-coded per query).
INTENT_TOP_N = 'TOP_N'
INTENT_FILTERING = 'FILTERING'
INTENT_JOIN = 'JOIN'
INTENT_AGGREGATION = 'AGGREGATION'
INTENT_SORTING = 'SORTING'
INTENT_GROUPING = 'GROUPING'
INTENT_DEDUPLICATION = 'DEDUPLICATION'
INTENT_PAGINATION = 'PAGINATION'
INTENT_EXISTENCE_CHECK = 'EXISTENCE_CHECK'
INTENT_SUBQUERY = 'SUBQUERY'
INTENT_SET_OPERATION = 'SET_OPERATION'
INTENT_RANGE_LOOKUP = 'RANGE_LOOKUP'
INTENT_POINT_LOOKUP = 'POINT_LOOKUP'
INTENT_FULL_SCAN = 'FULL_SCAN'

# Result states of the final decision tree.
# "No rewrite" is deliberately distinct from "no optimization": a query with no
# SQL rewrite may still benefit from a physical (index) or statistics change.
STATE_SQL_REWRITE = 'SQL_REWRITE'
STATE_PHYSICAL_OPTIMIZATION = 'NO_SQL_REWRITE_PHYSICAL_OPTIMIZATION'
STATE_STATISTICS_OPTIMIZATION = 'NO_SQL_REWRITE_STATISTICS_OPTIMIZATION'
STATE_NO_CHANGE = 'NO_CHANGE_REQUIRED'
STATE_INSUFFICIENT_EVIDENCE = 'INSUFFICIENT_EVIDENCE'

# Strategy candidate types.
STRATEGY_SQL_REWRITE = 'SQL_REWRITE'
STRATEGY_INDEX = 'INDEX_RECOMMENDATION'
STRATEGY_STATISTICS = 'STATISTICS_RECOMMENDATION'
STRATEGY_NO_CHANGE = 'NO_SQL_CHANGE'

# Index recommendation purposes.
PURPOSE_WHERE_EQUALITY = 'WHERE_EQUALITY'
PURPOSE_RANGE_LOOKUP = 'RANGE_LOOKUP'
PURPOSE_JOIN = 'JOIN'
PURPOSE_ORDER_BY = 'ORDER_BY'
PURPOSE_GROUP_BY = 'GROUP_BY'
PURPOSE_TOP_N_COVERING = 'TOP_N_COVERING'
PURPOSE_COMPOSITE = 'COMPOSITE'

# Cost-flow pipeline stages (scan -> filter -> join -> agg -> sort -> distinct
# -> limit -> result).
STAGES = ('SCAN', 'FILTER', 'JOIN', 'AGG', 'SORT', 'DISTINCT', 'LIMIT', 'RESULT')
STAGE_ORDER = {name: i for i, name in enumerate(STAGES)}

# Approximate selectivity of a single equality / range predicate. These mirror
# the (currently dead) constants in cost_model.py — reused here so the heuristic
# cost flow stays consistent with the existing heuristic cost model.
SEL_EQUALITY = 0.01
SEL_RANGE = 0.10


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class OptimizationIntent:
    """Detected query intent(s) plus structured top_n/filters/joins info."""
    intents: List[str] = field(default_factory=list)
    primary_intent: str = ''
    top_n: Optional[Dict[str, Any]] = None
    filters: List[Dict[str, Any]] = field(default_factory=list)
    joins: List[Dict[str, Any]] = field(default_factory=list)
    groupings: List[str] = field(default_factory=list)
    aggregates: List[str] = field(default_factory=list)
    pagination: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            'primary_intent': self.primary_intent,
            'intents': self.intents,
            'top_n': self.top_n,
            'filters': self.filters,
            'joins': self.joins,
            'groupings': self.groupings,
            'aggregates': self.aggregates,
            'pagination': self.pagination,
        }


@dataclass
class CostFlowStage:
    """One stage of the row-flow pipeline."""
    name: str
    rows_in: float
    rows_out: float
    cost: float
    detail: str = ''
    source: str = 'plan'  # plan | heuristic

    def to_dict(self) -> Dict[str, Any]:
        return {
            'stage': self.name,
            'rows_in': round(self.rows_in, 2),
            'rows_out': round(self.rows_out, 2),
            'cost': round(self.cost, 2),
            'detail': self.detail,
            'source': self.source,
        }


@dataclass
class CostFlow:
    """The row-flow pipeline with the identified expensive-work boundary."""
    stages: List[CostFlowStage] = field(default_factory=list)
    expensive_work_boundary: Optional[Dict[str, Any]] = None
    total_cost: float = 0.0
    source: str = 'plan'  # plan | heuristic

    def to_dict(self) -> Dict[str, Any]:
        return {
            'stages': [s.to_dict() for s in self.stages],
            'expensive_work_boundary': self.expensive_work_boundary,
            'total_cost': round(self.total_cost, 2),
            'source': self.source,
        }


@dataclass
class TopNReasoning:
    """Answers the dedicated Top-N reasoning questions."""
    outcome: str = ''            # HEAP_SORT_OPTIMIZATION | EARLY_TERMINATION_AVAILABLE |
                                 # ALREADY_EARLY_TERMINATING | INSUFFICIENT_EVIDENCE | NOT_TOP_N
    ordering_columns: List[str] = field(default_factory=list)
    directions: List[str] = field(default_factory=list)
    limit: Optional[int] = None
    indexed_ordering_available: bool = False
    early_termination_possible: bool = False
    recommendation: str = ''
    # Purpose-tagged index advice surfaced for this top-N (already covered
    # elsewhere, but kept here to answer the composite-index question).
    covering_index: Optional[Dict[str, Any]] = None
    # The spec's hard counterexample guard — never restructure as a subquery.
    never_rewrite_as_subquery_limit: bool = True
    questions: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'outcome': self.outcome,
            'ordering_columns': self.ordering_columns,
            'directions': self.directions,
            'limit': self.limit,
            'indexed_ordering_available': self.indexed_ordering_available,
            'early_termination_possible': self.early_termination_possible,
            'recommendation': self.recommendation,
            'covering_index': self.covering_index,
            'never_rewrite_as_subquery_limit': self.never_rewrite_as_subquery_limit,
            'questions': self.questions,
        }


@dataclass
class EarlyTermination:
    """Answers 'can rows be eliminated earlier / expensive work avoided / stop early'."""
    eliminate_rows_earlier: Optional[bool] = None
    avoid_expensive_work: Optional[bool] = None
    stop_early: Optional[bool] = None
    evidence: str = ''

    def to_dict(self) -> Dict[str, Any]:
        return {
            'eliminate_rows_earlier': self.eliminate_rows_earlier,
            'avoid_expensive_work': self.avoid_expensive_work,
            'stop_early': self.stop_early,
            'evidence': self.evidence,
        }


@dataclass
class JoinOrderReasoning:
    """Join-order reasoning — evidence-driven, no hard-coded strategy wins."""
    recommendation: str = ''
    evidence: str = ''
    join_sequence: List[str] = field(default_factory=list)
    strategy_concerns: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'recommendation': self.recommendation,
            'evidence': self.evidence,
            'join_sequence': self.join_sequence,
            'strategy_concerns': self.strategy_concerns,
        }


@dataclass
class StrategyCandidate:
    """One strategy the decision tree chose."""
    type: str
    action: str
    rationale: str
    evidence: str = ''
    confidence: float = 0.5

    def to_dict(self) -> Dict[str, Any]:
        return {
            'type': self.type,
            'action': self.action,
            'rationale': self.rationale,
            'evidence': self.evidence,
            'confidence': round(self.confidence, 2),
        }


@dataclass
class StrategyDecision:
    """Final decision-tree output."""
    result_state: str = STATE_INSUFFICIENT_EVIDENCE
    strategy_candidates: List[StrategyCandidate] = field(default_factory=list)
    decision_path: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'result_state': self.result_state,
            'strategy_candidates': [s.to_dict() for s in self.strategy_candidates],
            'decision_path': self.decision_path,
        }


@dataclass
class PlanComparison:
    """Why one plan is better than another (stage-level evidence)."""
    improvements: List[str] = field(default_factory=list)
    regressions: List[str] = field(default_factory=list)
    net_effect: str = ''
    why: str = ''

    def to_dict(self) -> Dict[str, Any]:
        return {
            'improvements': self.improvements,
            'regressions': self.regressions,
            'net_effect': self.net_effect,
            'why': self.why,
        }


# ---------------------------------------------------------------------------
# 1. Intent analysis
# ---------------------------------------------------------------------------

def analyze_query_intent(
    query: QueryStructure,
    schema: Optional[Dict[str, Any]] = None,
    baseline_plan: Optional[PlanAnalysis] = None,
) -> OptimizationIntent:
    """
    Classify what the query is trying to accomplish.

    Generic: driven entirely by ``QueryStructure`` fields, never by hard-coded
    SQL text. Produces a list of intents, a primary intent, and structured
    ``top_n`` / ``filters`` / ``joins`` info.
    """
    intents: List[str] = []
    schema = schema or {}

    top_n: Optional[Dict[str, Any]] = None
    if query.order_by.columns and query.limit_offset.limit:
        top_n = {
            'columns': list(query.order_by.columns),
            'directions': list(query.order_by.directions or ['ASC'] * len(query.order_by.columns)),
            'limit': query.limit_offset.limit,
            'offset': query.limit_offset.offset or 0,
            'has_expression': query.order_by.has_expression,
        }

    # Priority-ordered detection.
    if top_n:
        intents.append(INTENT_TOP_N)
    elif query.limit_offset.limit is not None:
        # Pagination is LIMIT/OFFSET without ORDER BY.
        intents.append(INTENT_PAGINATION)
        if query.limit_offset.large_offset:
            intents.append(INTENT_PAGINATION)  # else branch handles large offset below

    if query.group_by.columns:
        intents.append(INTENT_GROUPING)
    if query.group_by.aggregation_type not in ('none', '') or query.select.aggregates:
        intents.append(INTENT_AGGREGATION)
    if query.order_by.columns and not top_n:
        intents.append(INTENT_SORTING)

    if query.distinct.distinct or query.set_ops.kind in ('UNION', 'INTERSECT', 'EXCEPT'):
        intents.append(INTENT_DEDUPLICATION)
    if query.set_ops.kind:
        intents.append(INTENT_SET_OPERATION)

    if query.where.exists_conditions or any(c.is_subquery for c in query.where.in_conditions):
        intents.append(INTENT_EXISTENCE_CHECK)
    if query.subqueries:
        intents.append(INTENT_SUBQUERY)

    if query.joins:
        intents.append(INTENT_JOIN)

    if query.where.equalities or (query.where.in_conditions and not any(
            c.is_subquery for c in query.where.in_conditions)):
        intents.append(INTENT_POINT_LOOKUP)
    if query.where.ranges:
        intents.append(INTENT_RANGE_LOOKUP)

    if query.where.equalities or query.where.ranges or query.where.in_conditions:
        intents.append(INTENT_FILTERING)

    # FULL_SCAN: no selective predicate on any base table.
    has_selective_predicate = bool(
        query.where.equalities or query.where.ranges or query.where.in_conditions
        or query.where.like_conditions or query.where.is_null_conditions
        or query.where.is_not_null_conditions
    )
    if not has_selective_predicate and (not query.joins or query.operation_type == 'SELECT'):
        # A join without filters is still a scan of all inputs; but a join is
        # functionally driven by its join keys, so only call it FULL_SCAN when
        # it is a plain scan (no join, no predicate).
        if not query.joins and not top_n:
            intents.append(INTENT_FULL_SCAN)

    # De-duplicate, preserving order.
    seen = set()
    unique = []
    for i in intents:
        if i not in seen:
            seen.add(i)
            unique.append(i)
    intents = unique

    # PRIMARY_INTENT priority: the "shape" of the query that dominates reasoning.
    priority = [
        INTENT_TOP_N, INTENT_AGGREGATION, INTENT_SET_OPERATION, INTENT_DEDUPLICATION,
        INTENT_EXISTENCE_CHECK, INTENT_JOIN, INTENT_SORTING, INTENT_GROUPING,
        INTENT_PAGINATION, INTENT_RANGE_LOOKUP, INTENT_POINT_LOOKUP, INTENT_FILTERING,
        INTENT_SUBQUERY, INTENT_FULL_SCAN,
    ]
    primary = next((i for i in priority if i in intents), intents[0] if intents else INTENT_FULL_SCAN)

    # Structured filters.
    filters: List[Dict[str, Any]] = []
    for pred in list(query.where.equalities) + list(query.where.ranges):
        filters.append({
            'table': pred.table,
            'column': pred.column,
            'operator': pred.operator,
            'value': pred.value,
            'kind': 'equality' if pred in query.where.equalities else 'range',
        })
    for cond in query.where.in_conditions:
        filters.append({
            'table': cond.table,
            'column': cond.column,
            'operator': 'IN',
            'value': cond.value,
            'kind': 'in' + ('_subquery' if cond.is_subquery else ''),
        })
    for cond in query.where.like_conditions:
        filters.append({
            'table': cond.table,
            'column': cond.column,
            'operator': 'LIKE',
            'value': cond.value,
            'kind': 'like' + ('_leading_wildcard' if cond.leading_wildcard else ''),
        })

    # Structured joins.
    joins: List[Dict[str, Any]] = []
    for j in query.joins:
        joins.append({
            'type': j.type,
            'table': j.table,
            'left_column': j.left_column,
            'right_column': j.right_column,
            'missing_condition': j.missing_condition,
            'expression_on_join_col': j.expression_on_join_col,
        })

    pagination = None
    if query.limit_offset.limit is not None:
        pagination = {
            'limit': query.limit_offset.limit,
            'offset': query.limit_offset.offset or 0,
            'order_by_with_limit': query.limit_offset.order_by_with_limit,
            'large_offset': query.limit_offset.large_offset,
        }

    return OptimizationIntent(
        intents=intents,
        primary_intent=primary,
        top_n=top_n,
        filters=filters,
        joins=joins,
        groupings=list(query.group_by.columns),
        aggregates=list(query.select.aggregates),
        pagination=pagination,
    )


# ---------------------------------------------------------------------------
# 2. Cost-flow model
# ---------------------------------------------------------------------------

def _map_node_to_stage(node: PlanNodeMetrics) -> Optional[str]:
    """Map an EXPLAIN node type onto a pipeline stage name."""
    t = node.node_type
    # Scan family (Bitmap Index Scan before generic Index Scan check).
    if 'Bitmap Index Scan' in t or 'Bitmap Heap Scan' in t:
        return 'SCAN'
    if 'Seq Scan' in t or 'Index Only Scan' in t or 'Index Scan' in t:
        return 'SCAN'
    if 'CTE Scan' in t or 'Subquery Scan' in t or t.endswith('Scan'):
        return 'SCAN'
    if 'Sort' in t:
        return 'SORT'
    if 'Aggregate' in t:
        return 'AGG'
    if 'Join' in t:
        return 'JOIN'
    if t == 'Unique' or t == 'HashAggregate' or t == 'Group' or 'Distinct' in t:
        # Unique vs aggregate are ambiguous; treat Unique/Distinct as DISTINCT.
        if t == 'Unique' or 'Distinct' in t:
            return 'DISTINCT'
        return 'AGG'
    if t == 'Limit':
        return 'LIMIT'
    if t == 'Memoize':
        return 'AGG'
    # Materialize / Gather / Output are pass-through cost holders.
    return None


def _iter_plan_nodes(node: Optional[PlanNodeMetrics]):
    """Depth-first walk of the plan node tree."""
    yield node
    for child in (node.children or []):
        yield from _iter_plan_nodes(child)


def build_cost_flow(
    query: QueryStructure,
    analysis: Optional[PlanAnalysis] = None,
    schema: Optional[Dict[str, Any]] = None,
) -> CostFlow:
    """
    Build the row-flow pipeline (SCAN->FILTER->JOIN->AGG->SORT->DISTINCT->
    LIMIT->RESULT).

    When a real plan exists the flow is derived from the plan node tree
    (source='plan'). Otherwise a heuristic funnel is estimated from structure
    with source='heuristic' and LOW confidence — never asserted as fact.
    """
    if analysis is not None and analysis.root is not None:
        return _cost_flow_from_plan(analysis)
    return _cost_flow_heuristic(query)


def _cost_flow_from_plan(analysis: PlanAnalysis) -> CostFlow:
    """Accumulate per-stage rows-in/out and cost from the EXPLAIN node tree."""
    # sums per stage
    stage_cost: Dict[str, float] = {}
    stage_rows_out: Dict[str, float] = {}
    stage_rows_in: Dict[str, float] = {}
    stage_detail: Dict[str, List[str]] = {}

    def walk(node: PlanNodeMetrics, parent_stage: Optional[str]):
        stage = _map_node_to_stage(node)
        # Pass-through nodes (Materialize/Gather): skip cost attribution but
        # still recurse so children flow to their correct stage.
        child_rows = sum(_plan_rows(c) for c in (node.children or []))
        if stage is not None:
            stage_cost[stage] = stage_cost.get(stage, 0.0) + node.total_cost
            stage_rows_out[stage] = stage_rows_out.get(stage, 0.0) + node.plan_rows
            # rows entering this stage = sum of direct children rows (they are
            # produced before this node consumes them).
            stage_rows_in[stage] = stage_rows_in.get(stage, 0.0) + child_rows
            detail = _node_detail(node)
            if detail:
                stage_detail.setdefault(stage, []).append(detail)
        for child in (node.children or []):
            walk(child, stage)

    if analysis.root is not None:
        walk(analysis.root, None)

    total_cost = sum(stage_cost.values()) or (
        analysis.total_cost if analysis.total_cost else 0.0
    )

    stages: List[CostFlowStage] = []
    for name in STAGES:
        if name in stage_cost or name in stage_rows_out:
            stages.append(CostFlowStage(
                name=name,
                rows_in=stage_rows_in.get(name, 0.0),
                rows_out=stage_rows_out.get(name, 0.0),
                cost=stage_cost.get(name, 0.0),
                detail='; '.join(stage_detail.get(name, []))[:200],
                source='plan',
            ))

    boundary = _expensive_boundary(stages, total_cost)
    return CostFlow(stages=stages, expensive_work_boundary=boundary,
                    total_cost=total_cost, source='plan')


def _plan_rows(node: PlanNodeMetrics) -> float:
    return float(node.plan_rows or 0.0)


def _node_detail(node: PlanNodeMetrics) -> str:
    parts = [node.node_type]
    if getattr(node, 'relation_name', None):
        parts.append(node.relation_name)
    if getattr(node, 'index_name', None):
        parts.append('index:' + node.index_name)
    if getattr(node, 'filter', None):
        parts.append('filter:' + str(node.filter)[:80])
    return ' '.join(parts)


def _expensive_boundary(stages: List[CostFlowStage], total_cost: float) -> Optional[Dict[str, Any]]:
    """Identify the stage doing the dominant work (largest cost * row funnel)."""
    if not stages:
        return None
    # Score = cost plus a factor for rows transformed at that stage. A stage is
    # "expensive" when high cost coincides with many rows flowing through it.
    best = None
    for s in stages:
        rows_factor = (s.rows_in + s.rows_out) / max((s.rows_in + s.rows_out) or 1.0, 1.0)
        score = s.cost * (1.0 + 0.5 * min(rows_factor, 10.0))
        if best is None or score > best[0]:
            best = (score, s)
    if best is None:
        return None
    _, stage = best
    idx = STAGE_ORDER.get(stage.name, 99)
    next_stage = stages[idx + 1].name if idx + 1 < len(stages) else 'RESULT'
    cost_share = (stage.cost / total_cost) if total_cost else 0.0

    if stage.cost <= 0 and stage.rows_in == 0:
        return None  # nothing worth flagging

    reason = (
        f"Most work happens at the {stage.name} stage (cost share "
        f"{cost_share:.0%}); it transforms {stage.rows_in:.0f}->{stage.rows_out:.0f} rows "
        f"and feeds the {next_stage} stage."
    )
    return {
        'stage': stage.name,
        'cost_share': round(cost_share, 3),
        'rows_in': round(stage.rows_in, 2),
        'rows_out': round(stage.rows_out, 2),
        'next_stage': next_stage,
        'reason': reason,
    }


def _cost_flow_heuristic(query: QueryStructure) -> CostFlow:
    """Heuristic funnel when no EXPLAIN plan exists. Relative units, LOW confidence."""
    n_tables = max(len(query.tables), 1)
    base = 1000.0  # relative units per base table
    rows = base * n_tables
    cost = 1.0
    stages: List[CostFlowStage] = []

    # SCAN
    scan_cost = n_tables * 10.0
    stages.append(CostFlowStage('SCAN', rows, rows, scan_cost,
                                detail=f'{n_tables} table(s) scanned', source='heuristic'))
    cost += scan_cost

    # FILTER (softly reduce; a selective equality cuts hard)
    n_equality = len(query.where.equalities)
    n_range = len(query.where.ranges)
    if n_equality or n_range:
        sel = 1.0
        for _ in range(n_equality):
            sel *= SEL_EQUALITY
        for _ in range(n_range):
            sel *= SEL_RANGE
        rows_out = max(rows * sel, 1.0)
        filter_cost = (n_equality * 2.0) + (n_range * 3.0)
        stages.append(CostFlowStage('FILTER', rows, rows_out, filter_cost,
                                    detail=f'{n_equality} equality, {n_range} range predicate(s)',
                                    source='heuristic'))
        rows = rows_out
        cost += filter_cost

    # JOIN (many-to-one ≈ keeps driving side; unknown relationships multiply modestly)
    joins = query.joins or []
    if joins and len(query.tables) > 1:
        join_cost = 10.0 * len(joins)
        join_factor = max(len(query.tables) - len(joins), 1)
        rows_out = max(rows * join_factor, rows)
        stages.append(CostFlowStage('JOIN', rows, rows_out, join_cost,
                                    detail=f'{len(joins)} join(s)', source='heuristic'))
        rows = rows_out
        cost += join_cost

    # AGG / GROUP BY
    if query.group_by.columns or query.select.aggregates:
        agg_cost = 5.0 + 1.5 * len(query.group_by.columns)
        rows_out = max(len(query.group_by.columns) or 1, rows * 0.2)
        stages.append(CostFlowStage('AGG', rows, rows_out, agg_cost,
                                    detail='group/aggregate', source='heuristic'))
        rows = rows_out
        cost += agg_cost

    # SORT
    if query.order_by.columns:
        sort_cost = 5.0 + 1.0 * len(query.order_by.columns)
        stages.append(CostFlowStage('SORT', rows, rows, sort_cost,
                                    detail='ORDER BY %s' % ', '.join(query.order_by.columns),
                                    source='heuristic'))
        cost += sort_cost

    # DISTINCT
    if query.distinct.distinct:
        stages.append(CostFlowStage('DISTINCT', rows, rows, 8.0,
                                    detail='DISTINCT', source='heuristic'))
        cost += 8.0

    # LIMIT
    if query.limit_offset.limit is not None:
        rows_out = min(rows, float(query.limit_offset.limit + (query.limit_offset.offset or 0)))
        stages.append(CostFlowStage('LIMIT', rows, rows_out, 0.5,
                                    detail=f'LIMIT {query.limit_offset.limit}', source='heuristic'))
        rows = rows_out

    stages.append(CostFlowStage('RESULT', rows, rows, 0.0, source='heuristic'))

    boundary = _expensive_boundary(stages, cost)
    return CostFlow(stages=stages, expensive_work_boundary=boundary,
                    total_cost=cost, source='heuristic')


# ---------------------------------------------------------------------------
# 3. Top-N reasoning
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 3. Top-N reasoning
# ---------------------------------------------------------------------------

def _resolve_ordering_table(
    query: QueryStructure, ordering_columns: List[str],
    schema: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Resolve which table an ORDER BY column (string, possibly qualified) lives on.

    ORDER BY columns are stored as plain strings (e.g. 'mark' or 'g.mark').
    Resolution order: qualified name/alias -> single-table query -> the schema
    (when exactly one schema-declared table has that column). Returns None when
    genuinely ambiguous — never guesses.
    """
    if not ordering_columns:
        return None
    first_col = str(ordering_columns[0]).strip().strip('"')
    parts = first_col.split('.')
    qual = parts[0] if len(parts) > 1 else None
    _col = parts[-1]
    tables = list(query.tables or [])
    aliases = query.aliases or {}
    if qual:
        # Qualified: alias -> real table, or a real table name directly.
        return aliases.get(qual) or (qual if qual in tables else None)
    if len(tables) == 1:
        return tables[0]
    # Unqualified with multiple tables: consult the schema (schema-honoring).
    schema = schema or {}
    if schema.get('tables'):
        candidates = []
        for tbl, tdef in schema.get('tables', {}).items():
            cols = tdef.get('columns', {}) if isinstance(tdef, dict) else {}
            if any(str(c).lower() == _col.lower() for c in cols):
                candidates.append(tbl)
        if len(candidates) == 1:
            return candidates[0]
    return None  # ambiguous: give up


def _ordering_covered_by_index(schema: Dict[str, Any], table: Optional[str], columns: List[str]) -> bool:
    """True when an existing index/PK could provide the (prefix) ordering column(s)."""
    if not schema or not columns:
        return False
    first_col = str(columns[0]).split('.')[-1].strip().lower()
    candidates = [table] if table else [t for t in schema.get('tables', {})]
    for tbl in candidates:
        pk = get_table_primary_key(schema, tbl)
        if any(c.lower() == first_col for c in pk):
            return True
        for idx in get_table_indexes(schema, tbl):
            idx_cols = [str(c).split('.')[-1].strip().lower() for c in idx.get('columns', [])]
            if idx_cols and idx_cols[0] == first_col:
                return True
    return False


def analyze_top_n(
    query: QueryStructure,
    schema: Optional[Dict[str, Any]] = None,
    analysis: Optional[PlanAnalysis] = None,
) -> TopNReasoning:
    """
    Dedicated Top-N reasoning (ORDER BY + LIMIT).

    Answers: ordering columns, existing/candidate indexes, whether PostgreSQL
    can stop early (index provides order -> Limit terminates without sorting the
    full input), selectivity, join side, composite index viability. Crucially it
    classifies the opportunity *from the plan* — a Sort node above the Limit is a
    HEAP_SORT (cheapen the sort); an already-ordered index scan under the Limit
    is ALREADY_EARLY_TERMINATING. It never assumes one or the other.
    """
    schema = schema or {}
    ordering_columns = list(query.order_by.columns)
    directions = list(query.order_by.directions or ['ASC'] * len(ordering_columns))
    limit = query.limit_offset.limit

    if not ordering_columns or limit is None:
        return TopNReasoning(outcome='NOT_TOP_N', ordering_columns=ordering_columns,
                             directions=directions, limit=limit)

    # Plan evidence: is there a Sort node anywhere, and is the Limit fed by an
    # ordered (index) input?
    has_sort = False
    has_index_scan = False
    has_limit = False
    if analysis is not None and analysis.root is not None:
        for n in _iter_plan_nodes(analysis.root):
            t = n.node_type or ''
            if 'Sort' in t:
                has_sort = True
            if 'Index Scan' in t or 'Index Only Scan' in t:
                has_index_scan = True
            if t == 'Limit':
                has_limit = True

    # 1-2) ordering columns + coverage
    order_table = _resolve_ordering_table(query, ordering_columns, schema) or (
        query.tables[0] if query.tables else None)
    covered = _ordering_covered_by_index(schema, order_table, ordering_columns)

    # 4) selectivity of the driving filter (equality on an earlier column of a
    # composite makes the ordering column eligible for a later position).
    driving_filters = [_f for _f in (query.where.equalities or [])]

    # Determine the join side the ordering lives on.

    # 3/6) early termination feasibility: an index on the ordering column(s)
    # (optionally preceded by equality columns on the same table) lets
    # PostgreSQL walk the index in order and stop at LIMIT.
    early_termination_possible = False
    if not covered and ordering_columns:
        # An ordering-only index is always constructible in principle; the
        # question is whether the driving table is joined before the order can
        # apply. When the order is on the joined (right) side, a composite index
        # is needed and early termination is *conditional*.
        early_termination_possible = True

    covering_index = None
    recommended_cols: List[str] = []
    if order_table and not covered:
        recommended_cols = [c.split('.')[-1].replace('"', '') for c in ordering_columns]
        covering_index = {
            'table': order_table,
            'columns': recommended_cols,
            'directions': directions,
            'purpose': PURPOSE_TOP_N_COVERING,
            'reason': (
                f"Composite index on ({', '.join(recommended_cols)}) matching "
                f"{'/'.join(directions)} ordering lets PostgreSQL walk the index "
                f"and stop after {limit} rows without sorting the full input."
            ),
            'conditions': (
                'Only sound when the ordering columns live on the driven side '
                'and any equality filters on the same table are leading columns.'
            ),
        }

    # Classification: compare plans, don;t assume.
    if analysis is not None and analysis.root is not None:
        if has_sort:
            outcome = 'HEAP_SORT_OPTIMIZATION'
        elif has_limit and (has_index_scan and not has_sort):
            outcome = 'ALREADY_EARLY_TERMINATING'
        else:
            outcome = 'EARLY_TERMINATION_AVAILABLE'
    else:
        outcome = 'INSUFFICIENT_EVIDENCE' if not covered else 'EARLY_TERMINATION_AVAILABLE'

    questions = {
        'ordering_columns': ordering_columns,
        'directions': directions,
        'existing_index_covering_ordering': covered,
        'early_termination_via_index': early_termination_possible,
        'driving_filter_selectivity': [
            {'table': p.table, 'column': p.column, 'operator': p.operator} for p in driving_filters[:5]
        ],
        'ordering_join_side': {
            'table': order_table,
            'note': 'order column belongs to the driven/right side if a join is present'
                    if (order_table and len(query.tables) > 1) else 'base/driving side',
        },
        'composite_index_viable': bool(recommended_cols),
        'composite_index_column_order': recommended_cols,
    }

    recommendation_lines = []
    if outcome == 'HEAP_SORT_OPTIMIZATION':
        recommendation_lines.append(
            "PostgreSQL is sorting the full filtered input before applying LIMIT "
            "(top-N heapsort). Prefer an index that provides the ordering so the "
            "Limit can stop early."
        )
    elif outcome == 'ALREADY_EARLY_TERMINATING':
        recommendation_lines.append(
            "The plan already reaches the Limit without a full sort "
            "(ordered input). No top-N restructure is needed."
        )
    elif outcome == 'EARLY_TERMINATION_AVAILABLE':
        recommendation_lines.append(
            "An ordering/covering index can let PostgreSQL stop early at the "
            "Limit instead of sorting all qualifying rows."
        )
    else:
        recommendation_lines.append(
            "Insufficient plan evidence to classify the top-N; a covering index "
            "is a safe, semantics-preserving option."
        )
    if covering_index:
        recommendation_lines.append(
            "Keep ORDER BY + LIMIT as-is; do NOT restructure into "
            "JOIN (SELECT ... ORDER BY ... LIMIT ...) as a subquery — that "
            "changes which rows are selected."
        )

    return TopNReasoning(
        outcome=outcome,
        ordering_columns=ordering_columns,
        directions=directions,
        limit=limit,
        indexed_ordering_available=covered,
        early_termination_possible=early_termination_possible,
        recommendation=' '.join(recommendation_lines),
        covering_index=covering_index,
        never_rewrite_as_subquery_limit=True,
        questions=questions,
    )


# ---------------------------------------------------------------------------
# 4. Early-termination / row-elimination
# ---------------------------------------------------------------------------

def analyze_early_termination(
    query: QueryStructure,
    cost_flow: CostFlow,
    top_n: TopNReasoning,
    analysis: Optional[PlanAnalysis] = None,
) -> EarlyTermination:
    """Answer the three 'can we ...' questions with evidence (or None)."""
    evidence = []

    # (a) Can rows be eliminated earlier?
    eliminate_earlier: Optional[bool] = None
    if query.where.date_extractions or query.where.function_on_column:
        eliminate_earlier = True
        evidence.append("non-sargable predicate(s) prevent row elimination before scan/filter.")
    elif query.where.or_groups and len(query.where.or_groups) > 1:
        eliminate_earlier = True
        evidence.append("OR groups reduce filter selectivity; consider UNION of separate lookups.")
    elif query.joins and not query.where.equalities:
        eliminate_earlier = None  # can;t tell without filter selectivity
    else:
        eliminate_earlier = False

    # (b) Can expensive work be avoided?
    avoid_expensive = False
    if top_n.outcome == 'HEAP_SORT_OPTIMIZATION' and (top_n.covering_index or top_n.indexed_ordering_available):
        avoid_expensive = True
        evidence.append(top_n.recommendation)
    elif query.order_by.columns and not query.limit_offset.limit:
        avoid_expensive = True
        evidence.append("ORDER BY without LIMIT sorts the full result; an index may avoid it.")
    if query.distinct.distinct and query.distinct.redundant_candidate:
        avoid_expensive = True
        evidence.append("DISTINCT may be redundant (PK projected).")

    # (c) Can PostgreSQL stop early?
    stop_early: Optional[bool] = None
    if top_n.outcome in ('HEAP_SORT_OPTIMIZATION', 'EARLY_TERMINATION_AVAILABLE'):
        stop_early = True
        evidence.append("An ordering index + LIMIT allows early termination.")
    elif top_n.outcome == 'ALREADY_EARLY_TERMINATING':
        stop_early = False
        evidence.append("The plan already terminates early.")

    return EarlyTermination(
        eliminate_rows_earlier=eliminate_earlier,
        avoid_expensive_work=avoid_expensive,
        stop_early=stop_early,
        evidence=' '.join(evidence) if evidence else '',
    )


# ---------------------------------------------------------------------------
# 5. Join order reasoning
# ---------------------------------------------------------------------------

def reason_join_order(
    query: QueryStructure,
    analysis: Optional[PlanAnalysis] = None,
    cost_flow: Optional[CostFlow] = None,
) -> JoinOrderReasoning:
    """
    Join-order reasoning without a hard-coded "Hash is always better than
    Nested Loop" rule. Uses plan evidence (which strategies were picked, join
    details, whether filters are pushed to base tables) plus structure.
    """
    evidence = []
    concerns = []
    sequence: List[str] = []

    if query.joins:
        sequence = [f"{j.type}->{j.table}" for j in query.joins]

    if analysis is not None and analysis.join_details:
        strategies = sorted({d.get('type', '') for d in analysis.join_details})
        evidence.append("Plan join strategies: %s" % ', '.join(strategies or ['none']))
        for d in analysis.join_details:
            details = []
            if d.get('join_type'):
                details.append(f"type={d['join_type']}")
            if d.get('cost') is not None:
                details.append(f"cost={d['cost']:.1f}")
            if d.get('rows') is not None:
                details.append(f"rows={d['rows']}")
            if d.get('inner_unique'):
                details.append('inner_unique')
            if details:
                evidence.append("  %s: %s" % (d.get('type'), ', '.join(details)))
        # Any filter intent on the joined tables suggests pushing filters first.
        if query.where.equalities and query.joins:
            concerns.append("Selective filters are present; ensure they are applied "
                            "before the join (to base tables), not after.")

    if not query.joins:
        return JoinOrderReasoning(
            recommendation='No joins present; join-order reasoning not applicable.',
            evidence='',
            join_sequence=[],
            strategy_concerns=[],
        )

    recommendation = (
        'Prefers the smallest, most-filtered input as the driving side; '
        'the actual strategy choice (hash vs nested loop vs merge) is left to '
        'the planner and validated by plan evidence, not asserted here.'
    )
    return JoinOrderReasoning(
        recommendation=recommendation,
        evidence=' '.join(evidence),
        join_sequence=sequence,
        strategy_concerns=concerns,
    )


# ---------------------------------------------------------------------------
# 6. Reusable optimization questions
# ---------------------------------------------------------------------------

def answer_optimization_questions(
    query: QueryStructure,
    intent: OptimizationIntent,
    cost_flow: CostFlow,
    top_n: TopNReasoning,
    schema: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Run the ~20 reusable optimization questions. Each returns
    {answer, evidence, drives}. Drives = which strategy candidate types this
    question supports. Generic — reads structure only.
    """
    schema = schema or {}
    answers: Dict[str, Any] = {}

    def q(qid: str, question: str, answer, evidence: str, drives: List[str]):
        answers[qid] = {
            'question': question,
            'answer': answer,
            'evidence': evidence,
            'drives': drives,
        }

    q('top_n_present', 'Is this a TOP-N (ORDER BY + LIMIT) query?',
      intent.primary_intent == INTENT_TOP_N,
      f"intents={intent.intents}" if intent.intents else 'no intents detected',
      [STRATEGY_INDEX] if intent.primary_intent == INTENT_TOP_N else [STRATEGY_NO_CHANGE])

    q('order_by_covered_by_index', 'Is the ORDER BY covered by an existing index?',
      top_n.indexed_ordering_available if top_n.ordering_columns else None,
      'index/PK provides leading ordering column' if top_n.indexed_ordering_available else
      'no covering index found' if top_n.ordering_columns else 'no ORDER BY',
      [STRATEGY_INDEX])

    q('early_termination_possible', 'Can PostgreSQL stop early (index provides order, LIMIT cuts)?',
      top_n.early_termination_possible if top_n.ordering_columns else None,
      top_n.recommendation,
      [STRATEGY_INDEX])

    q('filter_selective_before_join', 'Are filters selective and on the driving table?',
      bool(query.where.equalities),
      '%d equality predicate(s)' % len(query.where.equalities),
      [STRATEGY_INDEX, STRATEGY_SQL_REWRITE])

    q('non_sargable_predicate', 'Is any predicate non-sargable (function/arithmetic/wildcard)?',
      bool(query.where.function_on_column or query.where.arithmetic_on_column or query.where.leading_wildcards),
      ', '.join([f"{r['func']}({r['column']})" for r in query.where.function_on_column]
                + [str(r['column']) for r in query.where.leading_wildcards]),
      [STRATEGY_SQL_REWRITE])

    q('join_missing_index_or_condition', 'Does any join lack an index / condition?',
      any(j.missing_condition for j in query.joins),
      ('CROSS/condition-less join(s)' if any(j.missing_condition for j in query.joins)
       else 'join conditions present'),
      [STRATEGY_INDEX] if any(j.missing_condition for j in query.joins) else [STRATEGY_NO_CHANGE])

    q('distinct_redundant', 'Is DISTINCT provably redundant (PK projected, no multiplying join)?',
      bool(query.distinct.distinct and query.distinct.redundant_candidate),
      'PK projected without row-multiplying join' if query.distinct.redundant_candidate else
      'duplicates may exist',
      [STRATEGY_SQL_REWRITE])

    q('union_downgradeable', 'Is UNION safely downgradeable to UNION ALL?',
      query.set_ops.kind == 'UNION',
      'UNION dedup on the full set',
      [STRATEGY_SQL_REWRITE])

    q('large_offset', 'Is OFFSET large (keyset pagination candidate)?',
      bool(query.limit_offset.large_offset),
      f"OFFSET={query.limit_offset.offset}",
      [STRATEGY_SQL_REWRITE] if query.limit_offset.large_offset else [STRATEGY_NO_CHANGE])

    q('covering_index_removes_sort', 'Would a covering index eliminate the sort for LIMIT?',
      top_n.outcome == 'HEAP_SORT_OPTIMIZATION',
      'Sort node above Limit in plan; covering index can remove it',
      [STRATEGY_INDEX])

    q('aggregate_input_reduced', 'Is the aggregation input already reduced?',
      bool(query.where.equalities or query.where.ranges),
      'filters exist' if (query.where.equalities or query.where.ranges) else
      'NO filters before GROUP BY',
      [STRATEGY_INDEX])

    q('in_to_exists_safe', 'Is IN(subquery) safely convertible to EXISTS?',
      any(c.is_subquery for c in query.where.in_conditions),
      'IN subquery present; EXISTS safe only when NOT NULL (see opportunities)',
      [STRATEGY_SQL_REWRITE])

    q('full_scan_with_index_candidate', 'Is there a full scan a filter index would help?',
      cost_flow.source == 'plan' and any(
          s.name == 'SCAN' and s.rows_out > 1000 and s.detail for s in cost_flow.stages),
      cost_flow.expensive_work_boundary['reason'] if cost_flow.expensive_work_boundary else 'scan small',
      [STRATEGY_INDEX])

    q('excessive_projection', 'Does SELECT project unnecessary columns?',
      bool(query.select.has_star or query.select.column_count > 10),
      'SELECT *' if query.select.has_star else f'{query.select.column_count} columns',
      [STRATEGY_SQL_REWRITE])

    q('sort_without_limit', 'Is there a full sort without LIMIT (avoidable)?',
      bool(query.order_by.columns and not query.limit_offset.limit),
      'ORDER BY without LIMIT sorts full result',
      [STRATEGY_INDEX, STRATEGY_NO_CHANGE])

    q('statistics_reliable', 'Are statistics / row estimates reliable?',
      None if cost_flow.source == 'heuristic' else 'plan-based',
      'no plan evidence' if cost_flow.source == 'heuristic' else 'plan estimation used',
      [STRATEGY_STATISTICS])

    q('filtered_scan_indexed', 'Would an index on the filter column narrow the scan?',
      bool(intent.filters),
      'drives index recommendation for the most selective filters',
      [STRATEGY_INDEX] if intent.filters else [STRATEGY_NO_CHANGE])

    q('pagination_large', 'Is pagination deep (OFFSET-based)?',
      bool(intent.pagination and intent.pagination.get('large_offset')),
      f"offset={intent.pagination.get('offset')}" if intent.pagination else 'no pagination',
      [STRATEGY_SQL_REWRITE] if (intent.pagination and intent.pagination.get('large_offset')) else [STRATEGY_NO_CHANGE])

    q('exists_over_subquery', 'Is there an existence check that could be pushed down?',
      bool(query.where.exists_conditions),
      f"{len(query.where.exists_conditions)} EXISTS condition(s)",
      [STRATEGY_SQL_REWRITE])

    q('set_op_dedup', 'Does a set operation force dedup?',
      query.set_ops.kind in ('UNION', 'INTERSECT', 'EXCEPT'),
      f"set operation '{query.set_ops.kind}'",
      [STRATEGY_SQL_REWRITE])

    return answers


# ---------------------------------------------------------------------------
# 7. Strategy decision tree
# ---------------------------------------------------------------------------

def _best_index_recommendation_present(index_advice: List[Any]) -> bool:
    return bool(index_advice)


def decide_strategy(
    query: QueryStructure,
    intent: OptimizationIntent,
    cost_flow: CostFlow,
    top_n: TopNReasoning,
    early_term: EarlyTermination,
    index_recommendations: List[Any],
    statistics_recommendations: List[Any],
    baseline_plan: Optional[PlanAnalysis] = None,
    best_candidate: Optional[Dict[str, Any]] = None,
    has_plan_evidence: bool = True,
) -> StrategyDecision:
    """
    Final decision tree.

    Orders the result states so an absence of a rewrite is never conflated with
    an absence of optimization, and INSUFFICIENT_EVIDENCE never collapses into
    NO_SQL_CHANGE/ALREADY_OPTIMAL.
    """
    path: List[str] = []
    strategies: List[StrategyCandidate] = []

    # --- Case 1: a safe, cheaper rewrite candidate won.
    if best_candidate is not None and best_candidate.get('validation_passed', False):
        best_safety = (best_candidate.get('semantic_safety') or 'unknown').lower()
        best_dict = best_candidate
        is_baseline = _is_baseline_candidate(best_dict)
        cost_improved = (best_dict.get('cost_change_percent') is not None
                         and best_dict.get('cost_change_percent') < 0)
        if not is_baseline and best_safety != 'unsafe' and cost_improved:
            path.append('best non-baseline candidate is semantically safe and cheaper')
            imp_evidence = best_dict.get('improvement_evidence')
            why = ''
            if isinstance(imp_evidence, dict):
                why = imp_evidence.get('why', '')
            elif isinstance(imp_evidence, str):
                why = imp_evidence
            strategies.append(StrategyCandidate(
                type=STRATEGY_SQL_REWRITE,
                action=best_dict.get('sql', ''),
                rationale="Winning rewrite candidate lowers estimated cost "
                          "with safe semantics.",
                evidence=why,
                confidence=float(best_dict.get('confidence_score') or 0.7),
            ))
            # Also surface physical/statistics advice if still useful.
            if index_recommendations:
                strategies.append(_index_strategy(index_recommendations))
            if statistics_recommendations:
                strategies.append(_statistics_strategy(statistics_recommendations))
            return StrategyDecision(STATE_SQL_REWRITE, strategies, path)

    # --- Case 2: physical (index) optimization without a SQL rewrite.
    has_index_advice = bool(index_recommendations)
    index_warranted = has_index_advice or top_n.outcome in (
        'HEAP_SORT_OPTIMIZATION', 'EARLY_TERMINATION_AVAILABLE'
    )
    if index_warranted:
        path.append('no safe rewrite, but index/ordering advice exists')
        if has_index_advice:
            strategies.append(_index_strategy(index_recommendations))
        else:
            strategies.append(StrategyCandidate(
                type=STRATEGY_INDEX,
                action=top_n.covering_index and top_n.covering_index.get(
                    'table', '') or '',
                rationale="Top-N ordering can be provided by an index to stop "
                          "early at the LIMIT.",
                evidence=top_n.recommendation,
                confidence=0.6 if top_n.outcome != 'INSUFFICIENT_EVIDENCE' else 0.4,
            ))
        if not has_plan_evidence and not schema_or_intent_filters(intent):
            strategies.append(_no_change_candidate('insufficient plan evidence'))
        return StrategyDecision(
            STATE_PHYSICAL_OPTIMIZATION, strategies, path)

    # --- Case 3: statistics optimization only.
    if statistics_recommendations:
        path.append('no rewrite and no index fix; stale statistics/poor estimation')
        strategies.append(_statistics_strategy(statistics_recommendations))
        return StrategyDecision(STATE_STATISTICS_OPTIMIZATION, strategies, path)

    # --- Case 4: already optimal (evidence-backed).
    optimal_evidence = _looks_optimal(cost_flow, top_n, early_term, baseline_plan)
    if optimal_evidence:
        path.append('plan shows no dominant-cost boundary, no improvement lever')
        strategies.append(_no_change_candidate(
            'query already efficiently executed (evidence-backed)'))
        return StrategyDecision(STATE_NO_CHANGE, strategies, path)

    # --- Case 5: insufficient evidence.
    path.append('no rewrite won, no index/statistics advice, cannot confirm optimal')
    strategies.append(_no_change_candidate(
        'insufficient evidence to assert an optimization or claim already-optimal; '
        'no plan and/or schema', confidence=0.0))
    return StrategyDecision(STATE_INSUFFICIENT_EVIDENCE, strategies, path)


def _is_baseline_candidate(candidate: Dict[str, Any]) -> bool:
    desc = (candidate.get('description') or '')
    return 'baseline' in desc.lower()


def _index_strategy(recommendations: List[Any]) -> StrategyCandidate:
    recs = recommendations
    actions = []
    for r in recs:
        sql = getattr(r, 'sql', None) or (r.get('sql') if isinstance(r, dict) else None)
        table = getattr(r, 'table', None) or (r.get('table') if isinstance(r, dict) else None)
        if sql:
            actions.append(sql)
        elif table:
            actions.append(f'index on {table}')
    action = '; '.join(actions[:3]) or 'see index_recommendations'
    evidence = getattr(recs[0], 'evidence', '') if recs and not isinstance(recs[0], dict) else (
        recs[0].get('evidence', '') if recs and isinstance(recs[0], dict) else '')
    return StrategyCandidate(
        type=STRATEGY_INDEX,
        action=action,
        rationale='Index recommendation(s) reduce scan/sort/join work without '
                  'changing query semantics.',
        evidence=evidence,
        confidence=0.7,
    )


def _statistics_strategy(recommendations: List[Any]) -> StrategyCandidate:
    action = '; '.join([getattr(r, 'action', None) or r.get('action', '') for r in recommendations][:3])
    return StrategyCandidate(
        type=STRATEGY_STATISTICS,
        action=action,
        rationale='Refreshing statistics improves row estimates and plan choices.',
        confidence=0.6,
    )


def _no_change_candidate(rationale: str, confidence: float = 0.5) -> StrategyCandidate:
    return StrategyCandidate(
        type=STRATEGY_NO_CHANGE,
        action='',
        rationale=rationale,
        confidence=confidence,
    )


def schema_or_intent_filters(intent: OptimizationIntent) -> bool:
    """Whether any filters/intents indicate indexable work (soft signal)."""
    return bool(intent.filters or intent.joins or intent.top_n)


def _looks_optimal(
    cost_flow: CostFlow,
    top_n: TopNReasoning,
    early_term: EarlyTermination,
    baseline_plan: Optional[PlanAnalysis],
) -> bool:
    """Evidence that the query is already efficient. Conservative."""
    if cost_flow.source == 'heuristic':
        # No plan -> cannot claim optimal.
        return False
    boundary = cost_flow.expensive_work_boundary
    if boundary is None:
        return False
    # If the dominant stage is LIMIT/RESULT or very low cost share, the heavy
    # lifting is tiny.
    if boundary.get('stage') in ('LIMIT', 'RESULT'):
        return True
    if boundary.get('cost_share', 1.0) < 0.3:
        return True
    if baseline_plan is not None:
        # Plan already uses index scans and no expensive sort/seq scan on large rows.
        if baseline_plan.index_scans > 0 and baseline_plan.seq_scans == 0 and baseline_plan.sorts == 0:
            return True
        if baseline_plan.row_estimation_quality == 'GOOD' and baseline_plan.sorts == 0:
            return True
    return False


# ---------------------------------------------------------------------------
# 8. Plan comparison answering WHY
# ---------------------------------------------------------------------------

def compare_plans(
    original_analysis: Optional[PlanAnalysis],
    candidate_analysis: Optional[PlanAnalysis],
) -> PlanComparison:
    """
    Compare two plans and explain WHY one is better, stage by stage. Walks both
    node trees, mapping to cost-flow stages, and reports deltas in cost/rows/
    sorts/scans at each stage.
    """
    if original_analysis is None or candidate_analysis is None:
        return PlanComparison(net_effect='unavailable', why='one or both plans missing')

    def stage_totals(analysis: PlanAnalysis) -> Dict[str, Dict[str, float]]:
        out: Dict[str, Dict[str, float]] = {}

        def walk(node: PlanNodeMetrics):
            stage = _map_node_to_stage(node)
            t = node.node_type or ''
            if stage is not None:
                d = out.setdefault(stage, {'cost': 0.0, 'rows': 0.0, 'nodes': 0})
                d['cost'] += node.total_cost
                d['rows'] += node.plan_rows
                d['nodes'] += 1
            for child in (node.children or []):
                walk(child)

        if analysis.root is not None:
            walk(analysis.root)
        return out

    orig = stage_totals(original_analysis)
    cand = stage_totals(candidate_analysis)

    improvements: List[str] = []
    regressions: List[str] = []
    net_cost_orig = original_analysis.total_cost or 0.0
    net_cost_cand = candidate_analysis.total_cost or 0.0

    for stage in STAGES:
        o = orig.get(stage)
        c = cand.get(stage)
        if o is None and c is None:
            continue
        o_cost = o['cost'] if o else 0.0
        c_cost = c['cost'] if c else 0.0
        o_rows = o['rows'] if o else 0.0
        c_rows = c['rows'] if c else 0.0
        delta_cost = c_cost - o_cost
        if delta_cost < -0.5:
            improvements.append(
                f"{stage} cost {o_cost:.1f}->{c_cost:.1f} ({delta_cost:+.1f})")
        elif delta_cost > 0.5:
            regressions.append(
                f"{stage} cost {o_cost:.1f}->{c_cost:.1f} ({delta_cost:+.1f})")

    # Whole-plan metrics.
    if candidate_analysis.sorts < original_analysis.sorts:
        improvements.append(
            f"sorts {original_analysis.sorts}->{candidate_analysis.sorts}")
    if candidate_analysis.seq_scans < original_analysis.seq_scans:
        improvements.append(
            f"sequential scans {original_analysis.seq_scans}->{candidate_analysis.seq_scans}")
    if candidate_analysis.index_scans > original_analysis.index_scans:
        improvements.append(
            f"index scans {original_analysis.index_scans}->{candidate_analysis.index_scans}")
    if candidate_analysis.row_estimation_quality != original_analysis.row_estimation_quality:
        improvements.append(
            f"row estimation {original_analysis.row_estimation_quality}"
            f"->{candidate_analysis.row_estimation_quality}")

    net_effect = f"total cost {net_cost_orig:.1f} -> {net_cost_cand:.1f}"
    if net_cost_cand < net_cost_orig:
        net_effect += " (cheaper)"
    elif net_cost_cand > net_cost_orig:
        net_effect += " (more expensive)"
    else:
        net_effect += " (unchanged)"

    why = []
    if improvements:
        why.append('Improved: ' + '; '.join(improvements[:5]))
    if regressions:
        why.append('Regressed: ' + '; '.join(regressions[:5]))
    if not improvements and not regressions:
        why.append('No material per-stage cost difference observed.')
    else:
        # Always tie back to the query the user asked about.
        reason = net_effect + '. ' + ' '.join(why)
        return PlanComparison(
            improvements=improvements,
            regressions=regressions,
            net_effect=net_effect,
            why=reason,
        )

    return PlanComparison(
        improvements=improvements,
        regressions=regressions,
        net_effect=net_effect,
        why=net_effect + '. ' + ' '.join(why),
    )


# ---------------------------------------------------------------------------
# 9. Facade
# ---------------------------------------------------------------------------

class OptimizationReasoning:
    """
    Orchestrates the whole thinking engine for one query. Pure reasoning: reads
    structure/plan/schema and emits structured advice. Never mutates candidates.
    """

    def __init__(
        self,
        query_structure: QueryStructure,
        schema: Optional[Dict[str, Any]] = None,
        baseline_plan: Optional[PlanAnalysis] = None,
        baseline_explain: Optional[Dict[str, Any]] = None,
        run_analyze: bool = False,
        opportunities: Optional[List[Any]] = None,
    ):
        self.query = query_structure
        self.schema = schema or {}
        self.plan = baseline_plan
        self.explain = baseline_explain
        self.run_analyze = run_analyze
        self.opportunities = opportunities or []

        self.intent = analyze_query_intent(query_structure, self.schema, baseline_plan)
        self.cost_flow = build_cost_flow(query_structure, baseline_plan, self.schema)
        self.top_n = analyze_top_n(query_structure, self.schema, baseline_plan)
        self.early_term = analyze_early_termination(
            query_structure, self.cost_flow, self.top_n, baseline_plan)
        self.join_order = reason_join_order(query_structure, baseline_plan, self.cost_flow)
        self.questions = answer_optimization_questions(
            query_structure, self.intent, self.cost_flow, self.top_n, self.schema)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'intent': self.intent.to_dict(),
            'cost_flow': self.cost_flow.to_dict(),
            'top_n': self.top_n.to_dict(),
            'early_termination': self.early_term.to_dict(),
            'join_order': self.join_order.to_dict(),
            'questions': self.questions,
        }