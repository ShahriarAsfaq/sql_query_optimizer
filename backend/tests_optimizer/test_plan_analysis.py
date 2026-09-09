"""
Test plan_analyzer with hand-built EXPLAIN (FORMAT JSON) plans — node type
counts, timing, buffers, parallelism, symmetric row-estimation error, quality
classification, and to_dict() backward compatibility.
"""
import os
import sys
import django

sys.path.insert(0, r'd:\AI projects\SQL Query Optimizer\backend')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'sql_optimizer.settings')
django.setup()

from queries.services.plan_analyzer import PlanAnalyzer, PlanNodeMetrics, PlanAnalysis

create_analyzer = PlanAnalyzer


def test_seq_scan_basics():
    """Seq Scan node yields scan counts, relation tracking, and plan timing."""
    plan_json = [{
        'Plan': {
            'Node Type': 'Seq Scan',
            'Relation Name': 'employees',
            'Alias': 'employees',
            'Startup Cost': 0.00,
            'Total Cost': 45.00,
            'Plan Rows': 1000,
            'Plan Width': 80,
        },
        'Planning Time': 0.213,
        'Execution Time': 12.732,
    }]
    analysis = create_analyzer().analyze(plan_json)

    assert analysis.seq_scans == 1
    assert analysis.total_cost == 45.00
    assert analysis.total_plan_rows == 1000
    assert analysis.tables_scanned == ['employees']
    assert analysis.seq_scan_tables == ['employees']
    assert analysis.planning_time == 0.213
    assert analysis.total_execution_time == 12.732
    print(f"Seq Scan basics: seq={analysis.seq_scans} cost={analysis.total_cost} "
          f"planning={analysis.planning_time} exec={analysis.total_execution_time}")
    print("[OK] Seq Scan basics")


def test_index_and_bitmap_scans():
    """Index, Index Only, and Bitmap scans are counted and tracked."""
    plan_json = [{
        'Plan': {
            'Node Type': 'Bitmap Heap Scan',
            'Relation Name': 'orders',
            'Total Cost': 40.0,
            'Plan Rows': 25,
            'Plan Width': 70,
            'Plans': [{
                'Node Type': 'Bitmap Index Scan',
                'Index Name': 'idx_orders_customer',
                'Total Cost': 1.5,
                'Plan Rows': 25,
                'Plans': [{
                    'Node Type': 'Seq Scan',
                    'Relation Name': 'orders',
                    'Total Cost': 20.0,
                }]
            }]
        }
    }]
    analysis = create_analyzer().analyze(plan_json)
    assert analysis.bitmap_heap_scans == 1
    assert analysis.bitmap_index_scans == 1
    assert 'idx_orders_customer' in analysis.indexes_used
    assert analysis.seq_scans == 1

    # Index Scan
    plan_json2 = [{
        'Plan': {
            'Node Type': 'Index Scan',
            'Relation Name': 'employees',
            'Index Name': 'employees_pkey',
            'Startup Cost': 0.29,
            'Total Cost': 8.30,
            'Plan Rows': 1,
        }
    }]
    analysis2 = create_analyzer().analyze(plan_json2)
    assert analysis2.index_scans == 1
    assert analysis2.index_only_scans == 0
    assert analysis2.index_scan_tables == ['employees']
    print(f"Index/Bitmap scans: bitmap_heap={analysis.bitmap_heap_scans} "
          f"bitmap_idx={analysis.bitmap_index_scans} index={analysis2.index_scans}")
    print("[OK] Index and Bitmap scans")


def test_join_types():
    """All three join strategies are detected and detailed."""
    plan_json = [{
        'Plan': {
            'Node Type': 'Hash Join',
            'Join Type': 'Inner',
            'Total Cost': 120.0,
            'Plan Rows': 90,
            'Join Filter': '(employees.department_id = departments.id)',
            'Plans': [
                {'Node Type': 'Seq Scan', 'Relation Name': 'employees', 'Total Cost': 45.0},
                {'Node Type': 'Seq Scan', 'Relation Name': 'departments', 'Total Cost': 30.0},
            ]
        }
    }]
    analysis = create_analyzer().analyze(plan_json)
    assert analysis.hash_joins == 1
    assert analysis.nested_loops == 0
    assert analysis.merge_joins == 0
    assert len(analysis.join_details) == 1
    assert analysis.join_details[0]['type'] == 'Hash Join'
    assert analysis.join_details[0]['join_type'] == 'Inner'
    print(f"Join types: hash={analysis.hash_joins} joins={analysis.join_details}")
    print("[OK] Join types")


def test_sort_detection():
    """Sort node increments sort count and captures sort details."""
    plan_json = [{
        'Plan': {
            'Node Type': 'Sort',
            'Sort Key': ['salary DESC'],
            'Sort Method': 'quicksort',
            'Sort Space Used': 1032,
            'Sort Space Type': 'Memory',
            'Total Cost': 250.0,
            'Plan Rows': 1000,
        }
    }]
    analysis = create_analyzer().analyze(plan_json)
    assert analysis.sorts == 1
    assert analysis.has_sort_in_plan is True
    assert len(analysis.sort_details) == 1
    assert analysis.sort_details[0]['sort_method'] == 'quicksort'
    assert analysis.sort_details[0]['sort_key'] == ['salary DESC']
    print(f"Sort detection: sorts={analysis.sorts} details={analysis.sort_details}")
    print("[OK] Sort detection")


def test_aggregate_detection():
    """HashAggregate and GroupAggregate nodes are counted."""
    plan_json = [{
        'Plan': {
            'Node Type': 'HashAggregate',
            'Group Key': ['department_id'],
            'Strategy': 'Hashed',
            'Total Cost': 80.0,
            'Plan Rows': 12,
            'Plans': [{'Node Type': 'Seq Scan', 'Relation Name': 'employees', 'Total Cost': 45.0}]
        }
    }]
    analysis = create_analyzer().analyze(plan_json)
    assert analysis.hash_aggregates == 1
    assert analysis.group_aggregates == 0
    print(f"Aggregate detection: hash_agg={analysis.hash_aggregates}")
    print("[OK] Aggregate detection")


def test_buffers_and_parallelism():
    """EXPLAIN ANALYZE BUFFERS aggregation + parallel worker counts."""
    plan_json = [{
        'Plan': {
            'Node Type': 'Gather',
            'Workers Planned': 2,
            'Workers Launched': 2,
            'Total Cost': 200.0,
            'Plan Rows': 1000,
            'Plans': [{
                'Node Type': 'Seq Scan',
                'Relation Name': 'orders',
                'Total Cost': 180.0,
                'Plan Rows': 1000,
                'Shared Hit Blocks': 100,
                'Shared Read Blocks': 40,
                'Shared Dirtied Blocks': 3,
                'Shared Written Blocks': 1,
                'Temp Read Blocks': 10,
                'Temp Written Blocks': 5,
            }]
        },
        'Planning Time': 0.5,
        'Execution Time': 250.0,
    }]
    analysis = create_analyzer().analyze(plan_json, explain_analyze=True)
    assert analysis.parallel_workers_planned == 2
    assert analysis.parallel_workers_launched == 2
    assert analysis.explain_analyze is True
    assert analysis.total_execution_time == 250.0
    # Buffers: hit + read accumulate to buffer_total
    assert analysis.shared_buffers.get('Shared Hit Blocks') == 100
    assert analysis.shared_buffers.get('Shared Read Blocks') == 40
    assert analysis.buffer_total == 140
    assert analysis.temp_buffers.get('read') == 10
    assert analysis.temp_buffers.get('written') == 5
    print(f"Buffers: total={analysis.buffer_total} "
          f"temp={analysis.temp_buffers} workers={analysis.parallel_workers_launched}")
    print("[OK] Buffers and parallelism")


def test_planning_execution_time_extraction():
    """Planning/Execution time from top-level EXPLAIN keys."""
    plan_json = [{
        'Plan': {'Node Type': 'Seq Scan', 'Relation Name': 't', 'Total Cost': 1.0},
        'Planning Time': 1.234,
        'Execution Time': 99.999,
    }]
    analysis = create_analyzer().analyze(plan_json)
    assert analysis.planning_time == 1.234
    assert analysis.total_execution_time == 99.999
    print(f"Timing: planning={analysis.planning_time} exec={analysis.total_execution_time}")
    print("[OK] Timing extraction")


def test_symmetric_error_quality_good():
    """Small row-estimation error classifies GOOD (< 2x)."""
    plan_json = [{
        'Plan': {
            'Node Type': 'Seq Scan',
            'Relation Name': 't',
            'Plan Rows': 100,
            'Plan Width': 10,
            'Total Cost': 5.0,
            'Actual Rows': 150,
            'Actual Loops': 1,
            'Actual Total Time': 1.0,
            'Actual Startup Time': 0.1,
        },
        'Planning Time': 0.1,
        'Execution Time': 2.5,
    }]
    analysis = create_analyzer().analyze(plan_json, explain_analyze=True)
    # symmetric error = max(150/100, 100/150) = 1.5 -> GOOD
    assert analysis.row_estimation_quality == 'GOOD'
    assert analysis.symmetric_avg_error == 1.5
    assert analysis.total_actual_rows == 150
    print(f"Sym error GOOD: avg={analysis.symmetric_avg_error} quality={analysis.row_estimation_quality}")
    print("[OK] Symmetric error GOOD")


def test_symmetric_error_quality_poor():
    """Large row-estimation error classifies as SEVERE (>= 10x)."""
    plan_json = [{
        'Plan': {
            'Node Type': 'Seq Scan',
            'Relation Name': 't',
            'Plan Rows': 100,
            'Total Cost': 5.0,
            'Actual Rows': 5000,
            'Actual Loops': 1,
        },
        'Execution Time': 3.0,
    }]
    analysis = create_analyzer().analyze(plan_json, explain_analyze=True)
    # symmetric error = max(5000/100, 100/5000) = 50 -> SEVERE
    assert analysis.row_estimation_quality == 'SEVERE'
    print(f"Sym error SEVERE: avg={analysis.symmetric_avg_error} quality={analysis.row_estimation_quality}")
    print("[OK] Symmetric error SEVERE")


def test_avg_estimation_error_uses_sum_divide_count():
    """Bug fix: avg estimation error must be SUM/COUNT, not MAX/COUNT."""
    plan_json = [{
        'Plan': {
            'Node Type': 'Sort',
            'Total Cost': 60.0,
            'Sort Key': ['x'],
            'Plans': [
                {
                    'Node Type': 'Seq Scan',
                    'Relation Name': 'a',
                    'Total Cost': 10.0,
                    'Plan Rows': 100,
                    'Actual Rows': 200,      # error = max(2, 0.5) = 2.0
                    'Actual Loops': 1,
                },
                {
                    'Node Type': 'Seq Scan',
                    'Relation Name': 'b',
                    'Total Cost': 20.0,
                    'Plan Rows': 100,
                    'Actual Rows': 800,      # error = max(8, 0.125) = 8.0
                    'Actual Loops': 1,
                },
            ]
        },
        'Execution Time': 4.0,
    }]
    analysis = create_analyzer().analyze(plan_json, explain_analyze=True)
    assert analysis.estimation_error_nodes == 2
    # SUM(2.0 + 8.0) / 2 = 5.0 (NOT max/count = 4.0)
    assert analysis.avg_estimation_error == 5.0
    assert analysis.symmetric_max_error == 8.0
    print(f"Avg error: avg={analysis.avg_estimation_error} max={analysis.symmetric_max_error}")
    print("[OK] Avg estimation error is SUM/COUNT")


def test_custom_thresholds():
    """Custom row-estimation thresholds reclassify quality."""
    plan_json = [{
        'Plan': {
            'Node Type': 'Seq Scan',
            'Relation Name': 't',
            'Total Cost': 5.0,
            'Plan Rows': 100,
            'Actual Rows': 300,      # error = 3.0
            'Actual Loops': 1,
        },
        'Execution Time': 2.0,
    }]
    # Default thresholds: 3.0 -> MODERATE (2 <= 3 < 5)
    analysis_default = create_analyzer().analyze(plan_json, explain_analyze=True)
    assert analysis_default.row_estimation_quality == 'MODERATE'

    # Custom thresholds {good: 5}: 3.0 < 5 -> GOOD
    analysis_custom = create_analyzer().analyze(
        plan_json, explain_analyze=True, thresholds={'good': 5.0, 'moderate': 10.0, 'poor': 100.0}
    )
    assert analysis_custom.row_estimation_quality == 'GOOD'
    print(f"Custom thresholds: default={analysis_default.row_estimation_quality} "
          f"custom={analysis_custom.row_estimation_quality}")
    print("[OK] Custom thresholds")


def test_invalid_explain_returns_empty():
    """Invalid EXPLAIN JSON should not crash and returns empty analysis."""
    analysis = create_analyzer().analyze([])
    assert analysis.total_cost == 0.0
    assert analysis.seq_scans == 0
    analysis_bad = create_analyzer().analyze([{'Plan': None}])
    assert analysis_bad.cost_source == 'none'
    print("[OK] Invalid EXPLAIN handled")


def test_to_dict_backward_compatible():
    """to_dict() keeps all existing keys and includes new additive ones."""
    plan_json = [{
        'Plan': {'Node Type': 'Seq Scan', 'Relation Name': 't', 'Total Cost': 5.0, 'Plan Rows': 10},
        'Planning Time': 0.5,
        'Execution Time': 3.0,
    }]
    analysis = create_analyzer().analyze(plan_json)
    d = analysis.to_dict()
    # Existing keys preserved
    for key in ('total_startup_cost', 'total_cost', 'total_plan_rows', 'node_counts',
                'cost_breakdown', 'row_estimation', 'scan_efficiency', 'join_details',
                'sort_details', 'subplan_details', 'cost_source', 'explain_analyze'):
        assert key in d, f"Missing existing key {key}"
    # New additive keys present
    for key in ('planning_time', 'total_execution_time', 'total_actual_rows', 'buffers',
                'temp_buffers', 'parallel_workers_planned', 'parallel_workers_launched',
                'has_sort_in_plan', 'buffer_total'):
        assert key in d, f"Missing new key {key}"
    assert d['planning_time'] == 0.5
    assert d['total_execution_time'] == 3.0
    assert d['buffer_total'] == 0
    print("[OK] to_dict backward compatible")


if __name__ == '__main__':
    print("=" * 60)
    print("Testing Plan Analyzer")
    print("=" * 60)

    test_seq_scan_basics()
    test_index_and_bitmap_scans()
    test_join_types()
    test_sort_detection()
    test_aggregate_detection()
    test_buffers_and_parallelism()
    test_planning_execution_time_extraction()
    test_symmetric_error_quality_good()
    test_symmetric_error_quality_poor()
    test_avg_estimation_error_uses_sum_divide_count()
    test_custom_thresholds()
    test_invalid_explain_returns_empty()
    test_to_dict_backward_compatible()

    print("\n" + "=" * 60)
    print("All plan analysis tests PASSED!")
    print("=" * 60)