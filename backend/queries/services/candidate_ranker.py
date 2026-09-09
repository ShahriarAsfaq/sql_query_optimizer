"""
Candidate Ranker — multi-factor performance scoring, layered ranking, and
evidence-based confidence assessment.

No hard-coded join strategy bias (hash > nested loop). Actual execution time
(when available) dominates over estimated cost.
"""
import logging
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

from .plan_analyzer import PlanAnalysis, PlanNodeMetrics
from .index_advisor import IndexRecommendation
from .statistics_analyzer import StatisticsRecommendation

logger = logging.getLogger(__name__)


class ConfidenceLevel(Enum):
    HIGH = 'HIGH'
    MEDIUM = 'MEDIUM'
    LOW = 'LOW'


class EvidenceSource(Enum):
    EXPLAIN_ANALYZE = 'EXPLAIN_ANALYZE'   # actual execution time + rows
    EXPLAIN = 'EXPLAIN'                    # planner cost + estimated rows
    SCHEMA_STATS = 'SCHEMA_STATS'          # schema + pg_stats only
    HEURISTIC = 'HEURISTIC'                # structural heuristic only


@dataclass
class PerformanceScore:
    """Multi-factor performance score breakdown."""
    total: float
    cost_ratio: float           # estimated cost ratio (candidate / baseline)
    time_ratio: Optional[float]  # actual execution time ratio (candidate / baseline)
    row_est_quality: str        # GOOD/MODERATE/POOR/SEVERE
    scan_improvement: int       # seq scan count change
    index_improvement: int      # index scan count change
    sort_reduction: int         # sort count change
    join_improvement: int       # join strategy improvement
    buffer_reduction: float     # buffer usage ratio
    complexity_penalty: float   # transformation count penalty
    readability_bonus: float    # simpler SQL bonus


def compute_performance_score(
    original: Optional[PlanAnalysis],
    candidate: PlanAnalysis,
    original_time: Optional[float] = None,
    candidate_time: Optional[float] = None,
) -> PerformanceScore:
    """
    Compute a multi-factor performance score.

    Lower is better. Primary signal:
      - If both actual times available: time_ratio dominates
      - Else: cost_ratio dominates
    """
    # Cost ratio (candidate / original). < 1 means candidate cheaper.
    cost_ratio = 1.0
    if original is not None and original.total_cost > 0:
        cost_ratio = candidate.total_cost / original.total_cost

    # Time ratio (actual). Only used if both present.
    time_ratio = None
    if original_time is not None and candidate_time is not None and original_time > 0:
        time_ratio = candidate_time / original_time

    # Row estimation quality
    row_quality = candidate.row_estimation_quality or 'UNKNOWN'

    # Scan counts
    orig_seq = original.seq_scans if original else 0
    orig_idx = original.index_scans if original else 0
    orig_sorts = original.sorts if original else 0
    orig_nested = original.nested_loops if original else 0
    orig_hash = original.hash_joins if original else 0
    orig_merge = original.merge_joins if original else 0
    orig_buffer = original.buffer_total if original else 0

    scan_imp = candidate.seq_scans - orig_seq
    idx_imp = candidate.index_scans - orig_idx

    # Sorts
    sort_red = candidate.sorts - orig_sorts

    # Join strategy: prefer fewer nested loops when hash/merge available,
    # but don't hard-code bias. Score based on actual cost impact.
    join_imp = (candidate.nested_loops + candidate.hash_joins + candidate.merge_joins) - \
               (orig_nested + orig_hash + orig_merge)

    # Buffer reduction
    buf_ratio = 1.0
    if orig_buffer > 0:
        buf_ratio = candidate.buffer_total / orig_buffer

    # Complexity penalty (number of rewrite rules applied)
    # Passed via candidate.metadata if available
    complexity_penalty = 0.0

    # Readability bonus (simpler query structure)
    readability_bonus = 0.0

    # Composite score: primary factor is time or cost ratio
    if time_ratio is not None:
        total = time_ratio
    else:
        total = cost_ratio

    # Secondary factors (small adjustments)
    if row_quality == 'GOOD':
        total *= 0.98
    elif row_quality == 'POOR':
        total *= 1.05
    elif row_quality == 'SEVERE':
        total *= 1.10

    # Fewer seq scans is good
    if scan_imp < 0:
        total *= 0.97
    elif scan_imp > 0:
        total *= 1.03

    # More index scans is good
    if idx_imp > 0:
        total *= 0.98
    elif idx_imp < 0:
        total *= 1.02

    # Fewer sorts is good
    if sort_red < 0:
        total *= 0.98
    elif sort_red > 0:
        total *= 1.02

    # Buffer reduction
    if buf_ratio < 1.0:
        total *= 0.95 + 0.05 * buf_ratio
    elif buf_ratio > 1.0:
        total *= 1.0 + 0.05 * (buf_ratio - 1.0)

    total *= (1.0 + complexity_penalty - readability_bonus)

    return PerformanceScore(
        total=total,
        cost_ratio=cost_ratio,
        time_ratio=time_ratio,
        row_est_quality=row_quality,
        scan_improvement=-scan_imp,  # positive = improvement
        index_improvement=idx_imp,
        sort_reduction=-sort_red,
        join_improvement=-join_imp,
        buffer_reduction=1.0 - buf_ratio,
        complexity_penalty=complexity_penalty,
        readability_bonus=readability_bonus,
    )


def _plan_from_dict(d: Dict[str, Any]) -> Optional[PlanAnalysis]:
    """Rehydrate a PlanAnalysis from its to_dict() serialization (or None)."""
    if not isinstance(d, dict):
        return None
    root = PlanNodeMetrics(node_type='Unknown', total_cost=d.get('total_cost', 0.0))
    plan = PlanAnalysis(root=root)
    plan.total_startup_cost = d.get('total_startup_cost', 0.0)
    plan.total_cost = d.get('total_cost', 0.0)
    plan.total_plan_rows = d.get('total_plan_rows', 0.0)
    nc = d.get('node_counts', {}) or {}
    plan.seq_scans = nc.get('seq_scans', 0)
    plan.index_scans = nc.get('index_scans', 0)
    plan.index_only_scans = nc.get('index_only_scans', 0)
    plan.bitmap_heap_scans = nc.get('bitmap_heap_scans', 0)
    plan.bitmap_index_scans = nc.get('bitmap_index_scans', 0)
    plan.nested_loops = nc.get('nested_loops', 0)
    plan.hash_joins = nc.get('hash_joins', 0)
    plan.merge_joins = nc.get('merge_joins', 0)
    plan.sorts = nc.get('sorts', 0)
    plan.incremental_sorts = nc.get('incremental_sorts', 0)
    plan.hash_aggregates = nc.get('hash_aggregates', 0)
    plan.group_aggregates = nc.get('group_aggregates', 0)
    plan.materialize_nodes = nc.get('materialize_nodes', 0)
    plan.memoize_nodes = nc.get('memoize_nodes', 0)
    plan.limit_nodes = nc.get('limit_nodes', 0)
    plan.unique_nodes = nc.get('unique_nodes', 0)
    plan.subplans = nc.get('subplans', 0)
    plan.cte_scans = nc.get('cte_scans', 0)
    cb = d.get('cost_breakdown', {}) or {}
    plan.scan_cost = cb.get('scan', 0.0)
    plan.join_cost = cb.get('join', 0.0)
    plan.sort_cost = cb.get('sort', 0.0)
    plan.aggregate_cost = cb.get('aggregate', 0.0)
    plan.join_details = d.get('join_details', [])
    plan.sort_details = d.get('sort_details', [])
    plan.planning_time = d.get('planning_time')
    plan.total_execution_time = d.get('total_execution_time')
    plan.total_actual_rows = d.get('total_actual_rows', 0.0)
    plan.buffer_total = d.get('buffer_total', 0)
    plan.parallel_workers_planned = d.get('parallel_workers_planned', 0)
    plan.parallel_workers_launched = d.get('parallel_workers_launched', 0)
    plan.has_sort_in_plan = d.get('has_sort_in_plan', False)
    plan.cost_source = d.get('cost_source', 'postgresql_explain')
    plan.explain_analyze = d.get('explain_analyze', False)
    re_data = d.get('row_estimation', {}) or {}
    plan.row_estimation_quality = re_data.get('quality', 'GOOD')
    plan.symmetric_max_error = re_data.get('symmetric_max_error', 0.0)
    plan.symmetric_avg_error = re_data.get('symmetric_avg_error', 0.0)
    se = d.get('scan_efficiency', {}) or {}
    plan.tables_scanned = se.get('tables_scanned', [])
    plan.seq_scan_tables = se.get('seq_scan_tables', [])
    plan.index_scan_tables = se.get('index_scan_tables', [])
    plan.indexes_used = se.get('indexes_used', [])
    return plan


def rank_candidates(
    candidates: List[Dict[str, Any]],
    baseline_analysis: PlanAnalysis,
    baseline_time: Optional[float],
) -> List[Dict[str, Any]]:
    """
    Layered lexical ranking of candidates.

    Layer 1: Hard constraints — semantically invalid/UNSAFE go to bottom.
    Layer 2: Performance evidence — actual time (if reliable) then estimated cost.
    Layer 3: Plan quality — scan/index/sort/join/buffer/parallel.
    Layer 4: Evidence stability — ANALYZE > EXPLAIN > schema/stats > heuristic.
    Layer 5: Readability/complexity tie-breaker.
    """
    enriched = []
    for c in candidates:
        # Extract candidate's plan analysis (None, PlanAnalysis, or a serialized dict)
        plan = c.get('plan_analysis')
        if isinstance(plan, dict):
            plan = _plan_from_dict(plan)
            c['plan_analysis'] = plan
        if plan is None or not hasattr(plan, 'total_cost'):
            from .plan_analyzer import PlanAnalysis, PlanNodeMetrics, PlanNodeMetrics
            # Build minimal PlanAnalysis from plan_metrics if provided
            dummy_root = PlanNodeMetrics(node_type='Unknown')
            plan = PlanAnalysis(root=dummy_root)
            if isinstance(c.get('plan_metrics'), dict):
                pm = c['plan_metrics']
                plan.total_cost = pm.get('total_cost', 0.0)
                plan.total_execution_time = pm.get('total_execution_time') or 0.0
                plan.seq_scans = pm.get('seq_scans', 0)
                plan.index_scans = pm.get('index_scans', 0)
                plan.sorts = pm.get('sorts', 0)
                plan.nested_loops = pm.get('nested_loops', 0)
                plan.hash_joins = pm.get('hash_joins', 0)
                plan.merge_joins = pm.get('merge_joins', 0)
                plan.buffer_total = pm.get('buffer_total', 0)
                plan.total_actual_rows = pm.get('total_actual_rows', 0)
                plan.row_estimation_quality = pm.get('row_estimation_quality') or 'UNKNOWN'
            c['plan_analysis'] = plan

        # Execution time
        cand_time = c.get('actual_execution_time') or c.get('execution_time')
        if cand_time is None:
            pt = getattr(plan, 'total_execution_time', None) or 0
            cand_time = pt if pt > 0 else None

        # Semantic safety
        semantic_risk = c.get('semantic_risk', 'LOW')
        safety = c.get('safety_details', {})
        semantically_valid = safety.get('semantically_valid', True)

        # Evidence source
        evidence_quality = c.get('evidence_quality', 'HEURISTIC')

        # Performance score
        score_obj = compute_performance_score(
            baseline_analysis, plan, baseline_time, cand_time
        )
        c['_rank_score'] = score_obj.total
        c['_rank_score_obj'] = score_obj
        c['_semantically_valid'] = semantically_valid
        c['_evidence_source'] = evidence_quality
        c['_semantic_risk'] = semantic_risk
        enriched.append(c)

    # Layered sort key
    def sort_key(c):
        # Layer 1: semantic validity (False -> high value = bottom)
        layer1 = 0 if c['_semantically_valid'] else 1
        # Layer 1b: UNSAFE semantic risk -> demote
        if c['_semantic_risk'] == 'HIGH':
            layer1 += 0.5

        # Layer 2: performance
        layer2 = c['_rank_score']

        # Layer 3: plan quality (seq scans, sorts, etc.)
        plan = c.get('plan_analysis')
        layer3 = 0.0
        if plan:
            baseline_cost = baseline_analysis.total_cost if baseline_analysis else 0.0
            layer3 = (plan.seq_scans * 0.1 + plan.sorts * 0.05 +
                     (plan.total_cost / max(baseline_cost, 1.0)) * 0.01)

        # Layer 4: evidence quality (lower = more reliable)
        ev_order = {'EXPLAIN_ANALYZE': 0, 'EXPLAIN': 1, 'SCHEMA_STATS': 2, 'HEURISTIC': 3}
        layer4 = ev_order.get(c['_evidence_source'], 3)

        # Layer 5: complexity/readability (lower = simpler)
        rules = len(c.get('rewrite_rules_applied', []))
        layer5 = rules * 0.001

        return (layer1, layer2, layer3, layer4, layer5)

    enriched.sort(key=sort_key)

    # Clean up temp keys
    for c in enriched:
        c.pop('_rank_score', None)
        c.pop('_rank_score_obj', None)
        c.pop('_semantically_valid', None)
        c.pop('_evidence_source', None)
        c.pop('_semantic_risk', None)

    return enriched


def assess_confidence(
    candidate: Dict[str, Any],
    plan_analysis: Optional[PlanAnalysis],
    semantic_result: Optional[Dict[str, Any]] = None,
) -> Tuple[str, float]:
    """
    Evidence-based confidence assessment.

    Returns (confidence_level, confidence_score).
    """
    semantic_valid = True
    if semantic_result:
        semantic_valid = semantic_result.get('semantically_valid', True)

    if not semantic_valid:
        return ConfidenceLevel.LOW.value, 0.1

    # Evidence source from candidate
    evidence = candidate.get('evidence_quality', 'HEURISTIC')
    has_actual_time = (candidate.get('actual_execution_time') is not None and
                       candidate.get('actual_execution_time') > 0)

    # Check plan analysis quality
    plan_quality = 'NONE'
    if plan_analysis:
        exec_time = plan_analysis.total_execution_time
        if exec_time and exec_time > 0:
            plan_quality = 'EXPLAIN_ANALYZE'
        elif plan_analysis.total_cost and plan_analysis.total_cost > 0:
            plan_quality = 'EXPLAIN'
        else:
            plan_quality = 'SCHEMA_STATS'

    # Decision matrix
    if evidence == 'EXPLAIN_ANALYZE' and has_actual_time and semantic_valid:
        return ConfidenceLevel.HIGH.value, 0.95
    elif evidence == 'EXPLAIN' and plan_quality == 'EXPLAIN' and semantic_valid:
        return ConfidenceLevel.MEDIUM.value, 0.75
    elif evidence in ('EXPLAIN', 'SCHEMA_STATS') and semantic_valid:
        return ConfidenceLevel.MEDIUM.value, 0.65
    elif evidence == 'HEURISTIC' and semantic_valid:
        return ConfidenceLevel.LOW.value, 0.45
    else:
        return ConfidenceLevel.LOW.value, 0.35


def performance_improvement_pct(
    original: PlanAnalysis,
    candidate: PlanAnalysis,
    original_time: Optional[float] = None,
    candidate_time: Optional[float] = None,
) -> Optional[str]:
    """Compute percentage improvement string."""
    # Prefer actual time
    if original_time is not None and candidate_time is not None and original_time > 0:
        pct = (original_time - candidate_time) / original_time * 100
        return f'{pct:+.1f}%'
    # Fallback to planner cost
    if original.total_cost > 0:
        pct = (original.total_cost - candidate.total_cost) / original.total_cost * 100
        return f'{pct:+.1f}%'
    return None