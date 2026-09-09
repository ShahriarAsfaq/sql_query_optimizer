"""
PostgreSQL EXPLAIN Plan Analyzer - extracts performance-relevant features from EXPLAIN plans.

This module provides deep analysis of PostgreSQL query plans to drive optimization decisions.
"""
import logging
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class ScanType(Enum):
    """PostgreSQL scan types."""
    SEQ_SCAN = "Seq Scan"
    INDEX_SCAN = "Index Scan"
    INDEX_ONLY_SCAN = "Index Only Scan"
    BITMAP_HEAP_SCAN = "Bitmap Heap Scan"
    BITMAP_INDEX_SCAN = "Bitmap Index Scan"
    TID_SCAN = "Tid Scan"
    SUBQUERY_SCAN = "Subquery Scan"
    FUNCTION_SCAN = "Function Scan"
    VALUES_SCAN = "Values Scan"
    CTE_SCAN = "CTE Scan"
    NAMEDTUPLESTORE_SCAN = "Named Tuplestore Scan"
    WORKTABLE_SCAN = "WorkTable Scan"
    FOREIGN_SCAN = "Foreign Scan"
    CUSTOM_SCAN = "Custom Scan"


class JoinType(Enum):
    """PostgreSQL join types."""
    NESTED_LOOP = "Nested Loop"
    HASH_JOIN = "Hash Join"
    MERGE_JOIN = "Merge Join"


class PlanNodeType(Enum):
    """Other plan node types."""
    SORT = "Sort"
    INCREMENTAL_SORT = "Incremental Sort"
    HASH_AGGREGATE = "HashAggregate"
    GROUP_AGGREGATE = "GroupAggregate"
    MATERIALIZE = "Materialize"
    MEMOIZE = "Memoize"
    LIMIT = "Limit"
    UNIQUE = "Unique"
    SETOP = "SetOp"
    APPEND = "Append"
    MERGE_APPEND = "Merge Append"
    RECURSIVE_UNION = "Recursive Union"
    BITMAP_AND = "BitmapAnd"
    BITMAP_OR = "BitmapOr"
    RESULT = "Result"
    PROJECT_SET = "ProjectSet"
    MODIFY_TABLE = "ModifyTable"
    LOCK_ROWS = "LockRows"
    GATHER = "Gather"
    GATHER_MERGE = "Gather Merge"


@dataclass
class PlanNodeMetrics:
    """Metrics extracted from a single plan node."""
    node_type: str
    startup_cost: float = 0.0
    total_cost: float = 0.0
    plan_rows: float = 0.0
    plan_width: int = 0

    # Actual metrics (from EXPLAIN ANALYZE)
    actual_rows: Optional[float] = None
    actual_total_time: Optional[float] = None
    actual_startup_time: Optional[float] = None
    actual_loops: Optional[int] = None

    # Scan-specific
    relation_name: Optional[str] = None
    alias: Optional[str] = None
    index_name: Optional[str] = None
    index_cond: Optional[str] = None
    filter: Optional[str] = None
    rows_removed_by_filter: Optional[float] = None

    # Join-specific
    join_type: Optional[str] = None
    join_filter: Optional[str] = None
    inner_unique: Optional[bool] = None

    # Sort-specific
    sort_key: Optional[List[str]] = None
    sort_method: Optional[str] = None
    sort_space_used: Optional[int] = None
    sort_space_type: Optional[str] = None

    # Aggregate-specific
    group_key: Optional[List[str]] = None
    strategy: Optional[str] = None
    partial_mode: Optional[str] = None

    # SubPlan info
    subplan_name: Optional[str] = None

    # Parallelism (PostgreSQL emits these on the Gather / Gather Merge node)
    workers_planned: Optional[int] = None
    workers_launched: Optional[int] = None

    # Buffers (when available)
    shared_hit_blocks: Optional[int] = None
    shared_read_blocks: Optional[int] = None
    shared_dirtied_blocks: Optional[int] = None
    shared_written_blocks: Optional[int] = None
    local_hit_blocks: Optional[int] = None
    local_read_blocks: Optional[int] = None
    local_dirtied_blocks: Optional[int] = None
    local_written_blocks: Optional[int] = None
    temp_read_blocks: Optional[int] = None
    temp_written_blocks: Optional[int] = None

    # Children
    children: List['PlanNodeMetrics'] = field(default_factory=list)

    # Derived metrics
    estimated_rows_per_loop: float = 0.0
    actual_rows_per_loop: float = 0.0
    estimation_error_ratio: Optional[float] = None
    estimation_error_symmetric: Optional[float] = None

    def __post_init__(self):
        if self.plan_rows > 0:
            self.estimated_rows_per_loop = self.plan_rows
        if self.actual_rows is not None and self.actual_loops and self.actual_loops > 0:
            self.actual_rows_per_loop = self.actual_rows / self.actual_loops
        if self.actual_rows is not None and self.plan_rows > 0:
            self.estimation_error_ratio = self.actual_rows / self.plan_rows
            self.estimation_error_symmetric = self._symmetric_error(
                self.actual_rows, self.plan_rows
            )

    @staticmethod
    def _symmetric_error(actual, estimated) -> float:
        """max(actual/estimated, estimated/actual) with safe zero handling."""
        try:
            actual = float(actual)
            estimated = float(estimated)
        except (TypeError, ValueError):
            return 0.0
        if actual <= 0 and estimated <= 0:
            return 0.0
        if estimated <= 0:
            return 1000.0 if actual > 0 else 0.0
        if actual <= 0:
            return estimated
        return max(actual / estimated, estimated / actual)

    def recompute_row_metrics(self):
        """Recompute derived row metrics after actual metrics are assigned.

        ``__post_init__`` runs before ``_parse_plan_node`` assigns ``actual_rows``,
        so derived row metrics must be refreshed once the node is fully populated.
        """
        self.estimated_rows_per_loop = 0.0
        self.actual_rows_per_loop = 0.0
        self.estimation_error_ratio = None
        self.estimation_error_symmetric = None
        if self.plan_rows and self.plan_rows > 0:
            self.estimated_rows_per_loop = self.plan_rows
        if self.actual_rows is not None and self.actual_loops and self.actual_loops > 0:
            self.actual_rows_per_loop = self.actual_rows / self.actual_loops
        if self.actual_rows is not None and self.plan_rows and self.plan_rows > 0:
            self.estimation_error_ratio = self.actual_rows / self.plan_rows
            self.estimation_error_symmetric = self._symmetric_error(
                self.actual_rows, self.plan_rows
            )


@dataclass
class PlanAnalysis:
    """Complete analysis of a query plan."""
    root: PlanNodeMetrics

    # Aggregate metrics
    total_startup_cost: float = 0.0
    total_cost: float = 0.0
    total_plan_rows: float = 0.0

    # Node counts by type
    seq_scans: int = 0
    index_scans: int = 0
    index_only_scans: int = 0
    bitmap_heap_scans: int = 0
    bitmap_index_scans: int = 0
    nested_loops: int = 0
    hash_joins: int = 0
    merge_joins: int = 0
    sorts: int = 0
    incremental_sorts: int = 0
    hash_aggregates: int = 0
    group_aggregates: int = 0
    materialize_nodes: int = 0
    memoize_nodes: int = 0
    limit_nodes: int = 0
    unique_nodes: int = 0
    subplans: int = 0
    cte_scans: int = 0

    # Cost breakdown
    scan_cost: float = 0.0
    join_cost: float = 0.0
    sort_cost: float = 0.0
    aggregate_cost: float = 0.0
    materialize_cost: float = 0.0
    other_cost: float = 0.0

    # Row estimation quality
    max_estimation_error: float = 0.0
    avg_estimation_error: float = 0.0
    estimation_error_nodes: int = 0

    # Scan efficiency
    tables_scanned: List[str] = field(default_factory=list)
    indexes_used: List[str] = field(default_factory=list)
    seq_scan_tables: List[str] = field(default_factory=list)
    index_scan_tables: List[str] = field(default_factory=list)

    # Join efficiency
    join_details: List[Dict[str, Any]] = field(default_factory=list)

    # Sort efficiency
    sort_details: List[Dict[str, Any]] = field(default_factory=list)

    # SubPlan details
    subplan_details: List[Dict[str, Any]] = field(default_factory=list)

    # Cost source
    cost_source: str = "postgresql_explain"
    explain_analyze: bool = False

    # Enhanced metrics (additive - appended to to_dict, existing keys unchanged)
    planning_time: Optional[float] = None          # Planning Time (ms)
    total_execution_time: Optional[float] = None   # Execution Time (ms)
    total_actual_rows: float = 0.0                 # Sum of actual rows across nodes
    shared_buffers: Dict[str, int] = field(default_factory=dict)   # hit/read/dirtied/written totals
    temp_buffers: Dict[str, int] = field(default_factory=dict)     # read/written block totals
    parallel_workers_planned: int = 0
    parallel_workers_launched: int = 0
    has_sort_in_plan: bool = False
    buffer_total: int = 0                          # total blocks touched (shared hit+read)
    symmetric_max_error: float = 0.0
    symmetric_avg_error: float = 0.0
    row_estimation_quality: str = "GOOD"
    row_estimation_thresholds: Optional[Dict[str, float]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            'total_startup_cost': self.total_startup_cost,
            'total_cost': self.total_cost,
            'total_plan_rows': self.total_plan_rows,
            'node_counts': {
                'seq_scans': self.seq_scans,
                'index_scans': self.index_scans,
                'index_only_scans': self.index_only_scans,
                'bitmap_heap_scans': self.bitmap_heap_scans,
                'bitmap_index_scans': self.bitmap_index_scans,
                'nested_loops': self.nested_loops,
                'hash_joins': self.hash_joins,
                'merge_joins': self.merge_joins,
                'sorts': self.sorts,
                'incremental_sorts': self.incremental_sorts,
                'hash_aggregates': self.hash_aggregates,
                'group_aggregates': self.group_aggregates,
                'materialize_nodes': self.materialize_nodes,
                'memoize_nodes': self.memoize_nodes,
                'limit_nodes': self.limit_nodes,
                'unique_nodes': self.unique_nodes,
                'subplans': self.subplans,
                'cte_scans': self.cte_scans,
            },
            'cost_breakdown': {
                'scan': self.scan_cost,
                'join': self.join_cost,
                'sort': self.sort_cost,
                'aggregate': self.aggregate_cost,
                'materialize': self.materialize_cost,
                'other': self.other_cost,
            },
            'row_estimation': {
                'max_error': self.max_estimation_error,
                'avg_error': self.avg_estimation_error,
                'nodes_with_error': self.estimation_error_nodes,
                'symmetric_max_error': self.symmetric_max_error,
                'symmetric_avg_error': self.symmetric_avg_error,
                'quality': self.row_estimation_quality,
            },
            'scan_efficiency': {
                'tables_scanned': self.tables_scanned,
                'indexes_used': self.indexes_used,
                'seq_scan_tables': self.seq_scan_tables,
                'index_scan_tables': self.index_scan_tables,
            },
            'join_details': self.join_details,
            'sort_details': self.sort_details,
            'subplan_details': self.subplan_details,
            'cost_source': self.cost_source,
            'explain_analyze': self.explain_analyze,
            # Enhanced metrics (additive)
            'planning_time': self.planning_time,
            'total_execution_time': self.total_execution_time,
            'total_actual_rows': self.total_actual_rows,
            'buffers': self.shared_buffers,
            'temp_buffers': self.temp_buffers,
            'buffer_total': self.buffer_total,
            'parallel_workers_planned': self.parallel_workers_planned,
            'parallel_workers_launched': self.parallel_workers_launched,
            'has_sort_in_plan': self.has_sort_in_plan,
        }


class PlanAnalyzer:
    """
    Analyzes PostgreSQL EXPLAIN (FORMAT JSON) output to extract performance features.
    """

    def __init__(self, thresholds: Optional[Dict[str, float]] = None):
        self._node_stack: List[PlanNodeMetrics] = []
        self.thresholds = thresholds

    def analyze(self, explain_json: List[Dict[str, Any]], explain_analyze: bool = False,
                thresholds: Optional[Dict[str, float]] = None) -> PlanAnalysis:
        """
        Analyze a PostgreSQL EXPLAIN plan.

        Args:
            explain_json: Parsed JSON from EXPLAIN (FORMAT JSON)
            explain_analyze: Whether ANALYZE was run (actual metrics available)
            thresholds: Optional row-estimation thresholds {good, moderate, poor}

        Returns:
            PlanAnalysis with extracted metrics
        """
        if not explain_json or not isinstance(explain_json, list):
            logger.warning("Invalid EXPLAIN JSON format")
            return self._empty_analysis()

        # PostgreSQL EXPLAIN JSON wraps the plan in a list with a 'Plan' key
        plan_data = explain_json[0] if explain_json else {}
        if not isinstance(plan_data, dict) or not isinstance(plan_data.get('Plan'), dict):
            logger.warning("No 'Plan' key in EXPLAIN output")
            return self._empty_analysis()

        root_node = self._parse_plan_node(plan_data['Plan'], depth=0)

        analysis = PlanAnalysis(
            root=root_node,
            explain_analyze=explain_analyze,
        )
        if thresholds is not None:
            analysis.row_estimation_thresholds = thresholds

        # Extract top-level timing / buffer / parallelism info
        self._extract_top_level(plan_data, analysis)

        # Aggregate metrics from the tree
        self._aggregate_metrics(root_node, analysis)

        # Classify row-estimation quality from symmetric errors
        analysis.row_estimation_quality = self._classify_row_estimation(
            analysis.symmetric_avg_error,
            analysis.row_estimation_thresholds or self.thresholds,
        )

        return analysis

    def _extract_top_level(self, plan_data: Dict[str, Any], analysis: PlanAnalysis):
        """Extract top-level EXPLAIN keys: timing, parallelism.

        Buffer blocks live per-node in EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) and
        are aggregated during the tree walk (see ``_walk_tree``). A few tools also
        emit a top-level ``Buffers`` dict, which we accept defensively.
        """
        if 'Planning Time' in plan_data:
            analysis.planning_time = plan_data.get('Planning Time')
        if 'Execution Time' in plan_data:
            analysis.total_execution_time = plan_data.get('Execution Time')

        # Defensive: accept an explicit top-level 'Buffers' block if present.
        buffers_block = plan_data.get('Buffers')
        if isinstance(buffers_block, dict):
            for key, val in buffers_block.items():
                if isinstance(val, (int, float)):
                    analysis.shared_buffers[str(key)] = int(val)

        # Parallelism
        analysis.parallel_workers_planned = plan_data.get('Workers Planned', 0) or 0
        analysis.parallel_workers_launched = plan_data.get('Workers Launched', 0) or 0

    def _empty_analysis(self) -> PlanAnalysis:
        """Return an empty analysis for error cases."""
        return PlanAnalysis(
            root=PlanNodeMetrics(node_type="Unknown"),
            cost_source="none",
        )

    def _parse_plan_node(self, node: Dict[str, Any], depth: int = 0) -> PlanNodeMetrics:
        """Parse a single plan node recursively."""
        metrics = PlanNodeMetrics(
            node_type=node.get('Node Type', 'Unknown'),
            startup_cost=node.get('Startup Cost', 0.0),
            total_cost=node.get('Total Cost', 0.0),
            plan_rows=node.get('Plan Rows', 0.0),
            plan_width=node.get('Plan Width', 0),
        )

        # Actual metrics (from EXPLAIN ANALYZE)
        if 'Actual Rows' in node:
            metrics.actual_rows = node.get('Actual Rows')
        if 'Actual Total Time' in node:
            metrics.actual_total_time = node.get('Actual Total Time')
        if 'Actual Startup Time' in node:
            metrics.actual_startup_time = node.get('Actual Startup Time')
        if 'Actual Loops' in node:
            metrics.actual_loops = node.get('Actual Loops')

        # Scan-specific fields
        metrics.relation_name = node.get('Relation Name')
        metrics.alias = node.get('Alias')
        metrics.index_name = node.get('Index Name')
        metrics.index_cond = node.get('Index Cond')
        metrics.filter = node.get('Filter')
        metrics.rows_removed_by_filter = node.get('Rows Removed by Filter')

        # Join-specific fields
        metrics.join_type = node.get('Join Type')
        metrics.join_filter = node.get('Join Filter')
        metrics.inner_unique = node.get('Inner Unique')

        # Sort-specific fields
        metrics.sort_key = node.get('Sort Key')
        metrics.sort_method = node.get('Sort Method')
        metrics.sort_space_used = node.get('Sort Space Used')
        metrics.sort_space_type = node.get('Sort Space Type')

        # Aggregate-specific fields
        metrics.group_key = node.get('Group Key')
        metrics.strategy = node.get('Strategy')
        metrics.partial_mode = node.get('Partial Mode')

        # SubPlan
        metrics.subplan_name = node.get('Subplan Name')

        # Parallelism (on the Gather node in FORMAT JSON)
        metrics.workers_planned = node.get('Workers Planned')
        metrics.workers_launched = node.get('Workers Launched')

        # Buffer info (when available)
        metrics.shared_hit_blocks = node.get('Shared Hit Blocks')
        metrics.shared_read_blocks = node.get('Shared Read Blocks')
        metrics.shared_dirtied_blocks = node.get('Shared Dirtied Blocks')
        metrics.shared_written_blocks = node.get('Shared Written Blocks')
        metrics.local_hit_blocks = node.get('Local Hit Blocks')
        metrics.local_read_blocks = node.get('Local Read Blocks')
        metrics.local_dirtied_blocks = node.get('Local Dirtied Blocks')
        metrics.local_written_blocks = node.get('Local Written Blocks')
        metrics.temp_read_blocks = node.get('Temp Read Blocks')
        metrics.temp_written_blocks = node.get('Temp Written Blocks')

        # Parse children
        for key in ['Plans', 'Outer Plan', 'Inner Plan', 'Subplans']:
            if key in node:
                child_list = node[key]
                if isinstance(child_list, list):
                    for child in child_list:
                        metrics.children.append(self._parse_plan_node(child, depth + 1))
                elif isinstance(child_list, dict):
                    metrics.children.append(self._parse_plan_node(child_list, depth + 1))

        # Refresh derived row metrics now that actual metrics are populated
        # (__post_init__ ran before actual_rows was assigned).
        metrics.recompute_row_metrics()

        return metrics

    def _aggregate_metrics(self, node: PlanNodeMetrics, analysis: PlanAnalysis):
        """Aggregate metrics across the entire plan tree."""
        # Update totals from root
        analysis.total_startup_cost = node.startup_cost
        analysis.total_cost = node.total_cost
        analysis.total_plan_rows = node.plan_rows

        # Walk the tree and collect metrics
        self._symmetric_error_sum = 0.0
        self._walk_tree(node, analysis)

        # Calculate average estimation error = SUM / COUNT (fix: was MAX / COUNT)
        if analysis.estimation_error_nodes > 0:
            analysis.avg_estimation_error = (
                self._symmetric_error_sum / analysis.estimation_error_nodes
            )
            analysis.symmetric_avg_error = analysis.avg_estimation_error

    def _walk_tree(self, node: PlanNodeMetrics, analysis: PlanAnalysis):
        """Recursively walk the plan tree and collect metrics."""
        node_type = node.node_type

        # Classify node type and collect metrics
        if 'Seq Scan' in node_type:
            analysis.seq_scans += 1
            if node.relation_name:
                analysis.tables_scanned.append(node.relation_name)
                analysis.seq_scan_tables.append(node.relation_name)
            analysis.scan_cost += node.total_cost

        elif 'Index Only Scan' in node_type:
            analysis.index_only_scans += 1
            if node.relation_name:
                analysis.tables_scanned.append(node.relation_name)
                analysis.index_scan_tables.append(node.relation_name)
            if node.index_name:
                analysis.indexes_used.append(node.index_name)
            analysis.scan_cost += node.total_cost

        elif 'Bitmap Index Scan' in node_type:
            # Must precede the generic 'Index Scan' check — 'Bitmap Index Scan'
            # contains 'Index Scan' as a substring.
            analysis.bitmap_index_scans += 1
            if node.index_name:
                analysis.indexes_used.append(node.index_name)
            analysis.scan_cost += node.total_cost

        elif 'Bitmap Heap Scan' in node_type:
            analysis.bitmap_heap_scans += 1
            if node.relation_name:
                analysis.tables_scanned.append(node.relation_name)
            analysis.scan_cost += node.total_cost

        elif 'Index Scan' in node_type:
            analysis.index_scans += 1
            if node.relation_name:
                analysis.tables_scanned.append(node.relation_name)
                analysis.index_scan_tables.append(node.relation_name)
            if node.index_name:
                analysis.indexes_used.append(node.index_name)
            analysis.scan_cost += node.total_cost

        elif node_type == 'Nested Loop':
            analysis.nested_loops += 1
            analysis.join_cost += node.total_cost
            if node.join_type:
                analysis.join_details.append({
                    'type': 'Nested Loop',
                    'join_type': node.join_type,
                    'cost': node.total_cost,
                    'rows': node.plan_rows,
                    'join_filter': node.join_filter,
                    'inner_unique': node.inner_unique,
                })

        elif node_type == 'Hash Join':
            analysis.hash_joins += 1
            analysis.join_cost += node.total_cost
            if node.join_type:
                analysis.join_details.append({
                    'type': 'Hash Join',
                    'join_type': node.join_type,
                    'cost': node.total_cost,
                    'rows': node.plan_rows,
                    'join_filter': node.join_filter,
                    'hash_cond': node.filter,  # Hash condition often in Filter
                    'inner_unique': node.inner_unique,
                })

        elif node_type == 'Merge Join':
            analysis.merge_joins += 1
            analysis.join_cost += node.total_cost
            if node.join_type:
                analysis.join_details.append({
                    'type': 'Merge Join',
                    'join_type': node.join_type,
                    'cost': node.total_cost,
                    'rows': node.plan_rows,
                    'join_filter': node.join_filter,
                    'merge_cond': node.filter,
                    'inner_unique': node.inner_unique,
                })

        elif node_type == 'Sort':
            analysis.sorts += 1
            analysis.sort_cost += node.total_cost
            analysis.sort_details.append({
                'cost': node.total_cost,
                'rows': node.plan_rows,
                'sort_key': node.sort_key,
                'sort_method': node.sort_method,
                'sort_space_used': node.sort_space_used,
                'sort_space_type': node.sort_space_type,
            })

        elif node_type == 'Incremental Sort':
            analysis.incremental_sorts += 1
            analysis.sort_cost += node.total_cost
            analysis.sort_details.append({
                'cost': node.total_cost,
                'rows': node.plan_rows,
                'sort_key': node.sort_key,
                'sort_method': 'Incremental',
                'sort_space_used': node.sort_space_used,
            })

        elif node_type == 'HashAggregate':
            analysis.hash_aggregates += 1
            analysis.aggregate_cost += node.total_cost

        elif node_type == 'GroupAggregate':
            analysis.group_aggregates += 1
            analysis.aggregate_cost += node.total_cost

        elif node_type == 'Materialize':
            analysis.materialize_nodes += 1
            analysis.materialize_cost += node.total_cost

        elif node_type == 'Memoize':
            analysis.memoize_nodes += 1
            analysis.materialize_cost += node.total_cost

        elif node_type == 'Limit':
            analysis.limit_nodes += 1

        elif node_type == 'Unique':
            analysis.unique_nodes += 1

        elif node_type == 'CTE Scan':
            analysis.cte_scans += 1
            analysis.scan_cost += node.total_cost

        elif 'SubPlan' in node_type or 'Subplan' in node_type:
            analysis.subplans += 1
            analysis.subplan_details.append({
                'name': node.subplan_name,
                'cost': node.total_cost,
                'rows': node.plan_rows,
            })
        else:
            analysis.other_cost += node.total_cost

        # Track actual rows
        if node.actual_rows is not None:
            analysis.total_actual_rows += node.actual_rows

        # Parallelism (max across the tree; some workers may only be planned)
        if node.workers_planned is not None:
            analysis.parallel_workers_planned = max(
                analysis.parallel_workers_planned, int(node.workers_planned or 0)
            )
        if node.workers_launched is not None:
            analysis.parallel_workers_launched = max(
                analysis.parallel_workers_launched, int(node.workers_launched or 0)
            )

        # Track sort presence
        if node_type in ('Sort', 'Incremental Sort'):
            analysis.has_sort_in_plan = True

        # Accumulate per-node buffer usage (EXPLAIN ANALYZE BUFFERS)
        self._accumulate_buffers(node, analysis)

        # Track row estimation quality (symmetric error)
        if node.estimation_error_symmetric is not None:
            analysis.estimation_error_nodes += 1
            err = node.estimation_error_symmetric
            self._symmetric_error_sum += err
            analysis.symmetric_max_error = max(analysis.symmetric_max_error, err)
            # Legacy ratio-based max preserved for backward compatibility
            if node.estimation_error_ratio is not None:
                analysis.max_estimation_error = max(
                    analysis.max_estimation_error, abs(node.estimation_error_ratio)
                )

        # Recurse into children
        for child in node.children:
            self._walk_tree(child, analysis)

    @staticmethod
    def _accumulate_buffers(node: 'PlanNodeMetrics', analysis: PlanAnalysis):
        """Add this node's buffer blocks to the analysis-wide totals."""
        s_keys = (
            ('shared_hit_blocks', 'Shared Hit Blocks'),
            ('shared_read_blocks', 'Shared Read Blocks'),
            ('shared_dirtied_blocks', 'Shared Dirtied Blocks'),
            ('shared_written_blocks', 'Shared Written Blocks'),
            ('local_hit_blocks', 'Local Hit Blocks'),
            ('local_read_blocks', 'Local Read Blocks'),
        )
        for attr, label in s_keys:
            val = getattr(node, attr, None)
            if val is not None:
                analysis.shared_buffers[label] = (
                    analysis.shared_buffers.get(label, 0) + val
                )
        analysis.buffer_total = (
            analysis.shared_buffers.get('Shared Hit Blocks', 0)
            + analysis.shared_buffers.get('Shared Read Blocks', 0)
        )

        temp_read = getattr(node, 'temp_read_blocks', None)
        temp_written = getattr(node, 'temp_written_blocks', None)
        if temp_read is not None or temp_written is not None:
            t_r = analysis.temp_buffers.get('read', 0)
            t_w = analysis.temp_buffers.get('written', 0)
            if temp_read is not None:
                t_r += temp_read
            if temp_written is not None:
                t_w += temp_written
            analysis.temp_buffers = {'read': t_r, 'written': t_w}


    @staticmethod
    def _classify_row_estimation(error: float,
                                 thresholds: Optional[Dict[str, float]]) -> str:
        """Classify an average symmetric error into GOOD/MODERATE/POOR/SEVERE."""
        th = thresholds or {}
        good = th.get('good', 2.0)
        moderate = th.get('moderate', 5.0)
        poor = th.get('poor', 10.0)
        if error < good:
            return 'GOOD'
        if error < moderate:
            return 'MODERATE'
        if error < poor:
            return 'POOR'
        return 'SEVERE'


def create_plan_analyzer(thresholds: Optional[Dict[str, float]] = None) -> PlanAnalyzer:
    """Factory function to create a PlanAnalyzer instance."""
    return PlanAnalyzer(thresholds=thresholds)