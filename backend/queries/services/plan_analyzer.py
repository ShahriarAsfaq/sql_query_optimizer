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

    def __post_init__(self):
        if self.plan_rows > 0:
            self.estimated_rows_per_loop = self.plan_rows
        if self.actual_rows is not None and self.actual_loops and self.actual_loops > 0:
            self.actual_rows_per_loop = self.actual_rows / self.actual_loops
        if self.actual_rows is not None and self.plan_rows > 0:
            self.estimation_error_ratio = self.actual_rows / self.plan_rows


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
        }


class PlanAnalyzer:
    """
    Analyzes PostgreSQL EXPLAIN (FORMAT JSON) output to extract performance features.
    """

    def __init__(self):
        self._node_stack: List[PlanNodeMetrics] = []

    def analyze(self, explain_json: List[Dict[str, Any]], explain_analyze: bool = False) -> PlanAnalysis:
        """
        Analyze a PostgreSQL EXPLAIN plan.

        Args:
            explain_json: Parsed JSON from EXPLAIN (FORMAT JSON)
            explain_analyze: Whether ANALYZE was run (actual metrics available)

        Returns:
            PlanAnalysis with extracted metrics
        """
        if not explain_json or not isinstance(explain_json, list):
            logger.warning("Invalid EXPLAIN JSON format")
            return self._empty_analysis()

        # PostgreSQL EXPLAIN JSON wraps the plan in a list with a 'Plan' key
        plan_data = explain_json[0] if explain_json else {}
        if 'Plan' not in plan_data:
            logger.warning("No 'Plan' key in EXPLAIN output")
            return self._empty_analysis()

        root_node = self._parse_plan_node(plan_data['Plan'], depth=0)

        analysis = PlanAnalysis(
            root=root_node,
            explain_analyze=explain_analyze,
        )

        # Aggregate metrics from the tree
        self._aggregate_metrics(root_node, analysis)

        return analysis

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

        return metrics

    def _aggregate_metrics(self, node: PlanNodeMetrics, analysis: PlanAnalysis):
        """Aggregate metrics across the entire plan tree."""
        # Update totals from root
        analysis.total_startup_cost = node.startup_cost
        analysis.total_cost = node.total_cost
        analysis.total_plan_rows = node.plan_rows

        # Walk the tree and collect metrics
        self._walk_tree(node, analysis)

        # Calculate average estimation error
        if analysis.estimation_error_nodes > 0:
            analysis.avg_estimation_error = analysis.max_estimation_error / analysis.estimation_error_nodes

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

        elif 'Index Scan' in node_type:
            analysis.index_scans += 1
            if node.relation_name:
                analysis.tables_scanned.append(node.relation_name)
                analysis.index_scan_tables.append(node.relation_name)
            if node.index_name:
                analysis.indexes_used.append(node.index_name)
            analysis.scan_cost += node.total_cost

        elif 'Bitmap Heap Scan' in node_type:
            analysis.bitmap_heap_scans += 1
            if node.relation_name:
                analysis.tables_scanned.append(node.relation_name)
            analysis.scan_cost += node.total_cost

        elif 'Bitmap Index Scan' in node_type:
            analysis.bitmap_index_scans += 1
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

        # Track row estimation quality
        if node.estimation_error_ratio is not None:
            analysis.estimation_error_nodes += 1
            error_ratio = node.estimation_error_ratio
            analysis.max_estimation_error = max(analysis.max_estimation_error, error_ratio)

        # Recurse into children
        for child in node.children:
            self._walk_tree(child, analysis)


def create_plan_analyzer() -> PlanAnalyzer:
    """Factory function to create a PlanAnalyzer instance."""
    return PlanAnalyzer()