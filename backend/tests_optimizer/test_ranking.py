"""
Test candidate_ranker — multi-factor scoring, layered ranking, evidence-based
confidence. Cheaper SQL wins; actual-time beats estimated cost; semantically
unsafe candidates are demoted; evidence quality breaks ties; heuristic evidence
yields LOW confidence.
"""
import os
import sys
import django

sys.path.insert(0, r'd:\AI projects\SQL Query Optimizer\backend')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'sql_optimizer.settings')
django.setup()

from queries.services.candidate_ranker import (
    compute_performance_score,
    rank_candidates,
    assess_confidence,
    performance_improvement_pct,
    ConfidenceLevel,
)
from queries.services.plan_analyzer import PlanAnalysis, PlanNodeMetrics


def make_plan(total_cost, seq_scans=0, index_scans=0, sorts=0,
              exec_time=0.0, row_quality='GOOD', nested=0, hash_joins=0,
              merge_joins=0, buffer_total=0):
    root = PlanNodeMetrics(node_type='Result', total_cost=total_cost, plan_rows=1)
    plan = PlanAnalysis(root=root)
    plan.total_cost = total_cost
    plan.seq_scans = seq_scans
    plan.index_scans = index_scans
    plan.sorts = sorts
    plan.total_execution_time = exec_time
    plan.row_estimation_quality = row_quality
    plan.nested_loops = nested
    plan.hash_joins = hash_joins
    plan.merge_joins = merge_joins
    plan.buffer_total = buffer_total
    return plan


def candidate(sql, plan, evidence='EXPLAIN', actual_time=None,
              semantic_risk='LOW', valid=True, rules=None):
    return {
        'sql': sql,
        'plan_analysis': plan,
        'actual_execution_time': actual_time,
        'semantic_risk': semantic_risk,
        'safety_details': {'semantically_valid': valid},
        'evidence_quality': evidence,
        'rewrite_rules_applied': rules or ['rewrite_x'],
    }


def test_compute_performance_score_cheaper_wins():
    """Lower cost ratio yields a lower (better) total score."""
    baseline = make_plan(total_cost=100)
    cheap = make_plan(total_cost=50)      # ratio 0.5
    costly = make_plan(total_cost=200)    # ratio 2.0

    s_cheap = compute_performance_score(baseline, cheap)
    s_costly = compute_performance_score(baseline, costly)
    assert s_cheap.total < s_costly.total
    assert s_cheap.cost_ratio == 0.5
    assert s_costly.cost_ratio == 2.0
    print(f"Score: cheap={s_cheap.total:.4f} costly={s_costly.total:.4f}")
    print("[OK] Cheaper candidate scores better")


def test_compute_performance_score_time_dominates():
    """Actual-time ratio dominates when both times are present."""
    baseline = make_plan(total_cost=100)
    plan_a = make_plan(total_cost=90, exec_time=12.0)   # cheaper estimate, slower actual
    plan_b = make_plan(total_cost=300, exec_time=2.0)    # pricier estimate, faster actual

    s_a = compute_performance_score(baseline, plan_a, 10.0, 12.0)  # time ratio 1.2
    s_b = compute_performance_score(baseline, plan_b, 10.0, 2.0)   # time ratio 0.2
    assert s_a.time_ratio == 1.2
    assert s_b.time_ratio == 0.2
    assert s_b.total < s_a.total, "Faster actual time should win despite higher estimate"
    print(f"Time dominates: A={s_a.total:.4f} B={s_b.total:.4f}")
    print("[OK] Actual-time dominates estimated cost")


def test_score_secondary_factors():
    """GOOD row estimation and fewer seq scans nudge the score down."""
    baseline = make_plan(total_cost=100, seq_scans=2)
    good = make_plan(total_cost=50, seq_scans=0, row_quality='GOOD')
    poor = make_plan(total_cost=50, seq_scans=2, row_quality='SEVERE')

    s_good = compute_performance_score(baseline, good)
    s_poor = compute_performance_score(baseline, poor)
    assert s_good.total < s_poor.total
    assert s_good.index_improvement == 0
    print(f"Secondary factors: good={s_good.total:.4f} poor={s_poor.total:.4f}")
    print("[OK] Secondary factors applied")


def test_rank_candidates_cheaper_sql_wins():
    """Layered ranking puts the cheapest valid rewrite first."""
    baseline = make_plan(total_cost=100)
    slow = candidate('expensive', make_plan(total_cost=90))
    fast = candidate('cheap', make_plan(total_cost=40))
    ranked = rank_candidates([slow, fast], baseline, None)
    assert ranked[0]['sql'] == 'cheap'
    print(f"Ranked order: {[c['sql'] for c in ranked]}")
    print("[OK] Cheaper SQL ranks first")


def test_rank_candidates_actual_time_beats_estimate():
    """A candidate with faster actual time outranks one with lower estimated cost."""
    baseline = make_plan(total_cost=100)
    # Lower estimated cost but slower actual time
    cheap_est = candidate('cheap_estimate', make_plan(total_cost=80),
                          evidence='EXPLAIN_ANALYZE', actual_time=12.0)
    # Higher estimated cost but faster actual time
    fast_actual = candidate('fast_actual', make_plan(total_cost=200),
                            evidence='EXPLAIN_ANALYZE', actual_time=4.0)
    ranked = rank_candidates([cheap_est, fast_actual], baseline, 10.0)
    assert ranked[0]['sql'] == 'fast_actual', "Actual time should dominate estimate"
    print(f"Ranked order: {[c['sql'] for c in ranked]}")
    print("[OK] Actual-time ranking beats estimate")


def test_rank_candidates_semantic_unsafe_demoted():
    """Semantically unsafe candidate ranks last even if fastest."""
    baseline = make_plan(total_cost=100)
    unsafe_fast = candidate('UNSAFE', make_plan(total_cost=20),
                            semantic_risk='HIGH', valid=False)
    safe_slow = candidate('safe', make_plan(total_cost=120), valid=True)
    ranked = rank_candidates([unsafe_fast, safe_slow], baseline, None)
    assert ranked[0]['sql'] == 'safe'
    assert ranked[-1]['sql'] == 'UNSAFE'
    print(f"Ranked order: {[c['sql'] for c in ranked]}")
    print("[OK] Semantically unsafe candidate demoted")


def test_rank_candidates_evidence_tiebreak():
    """Same performance: EXPLAIN_ANALYZE evidence ranks above HEURISTIC."""
    baseline = make_plan(total_cost=100)
    plan_a = make_plan(total_cost=50)
    plan_b = make_plan(total_cost=50)
    ev_analyze = candidate('analyze_evidence', plan_a, evidence='EXPLAIN_ANALYZE')
    ev_heuristic = candidate('heuristic_evidence', plan_b, evidence='HEURISTIC',
                             actual_time=None)
    ranked = rank_candidates([ev_heuristic, ev_analyze], baseline, None)
    assert ranked[0]['sql'] == 'analyze_evidence'
    print(f"Ranked order: {[c['sql'] for c in ranked]}")
    print("[OK] Evidence quality breaks tie")


def test_assess_confidence_matrix():
    """Evidence-based confidence: HIGH/MEDIUM/LOW by evidence source."""
    high_plan = make_plan(total_cost=50, exec_time=3.0)
    med_plan = make_plan(total_cost=50)
    low_plan = make_plan(total_cost=50)

    # HIGH: EXPLAIN_ANALYZE + actual time present
    level, score = assess_confidence(
        candidate('q', high_plan, evidence='EXPLAIN_ANALYZE', actual_time=3.0),
        high_plan,
        {'semantically_valid': True},
    )
    assert level == ConfidenceLevel.HIGH.value and score > 0.9

    # MEDIUM: EXPLAIN without actual time
    level, score = assess_confidence(
        candidate('q', med_plan, evidence='EXPLAIN'),
        med_plan,
        {'semantically_valid': True},
    )
    assert level == ConfidenceLevel.MEDIUM.value and 0.5 < score < 0.9

    # LOW: heuristic-only
    level, score = assess_confidence(
        candidate('q', low_plan, evidence='HEURISTIC'),
        low_plan,
        {'semantically_valid': True},
    )
    assert level == ConfidenceLevel.LOW.value and score <= 0.5

    print(f"Confidence: HIGH={assess_confidence(candidate('q', high_plan, evidence='EXPLAIN_ANALYZE', actual_time=3.0), high_plan, {'semantically_valid': True})}")
    print("[OK] Confidence matrix")


def test_assess_confidence_semantic_invalid():
    """Semantically invalid candidate gets minimum confidence."""
    plan = make_plan(total_cost=50)
    level, score = assess_confidence(
        candidate('q', plan, evidence='EXPLAIN_ANALYZE', actual_time=3.0),
        plan,
        {'semantically_valid': False},
    )
    assert level == ConfidenceLevel.LOW.value
    assert score == 0.1
    print(f"Semantic-invalid confidence: {level}/{score}")
    print("[OK] Semantic-invalid gets LOW confidence")


def test_performance_improvement_pct():
    """Improvement percentage uses actual time when available, else cost."""
    baseline = make_plan(total_cost=100)
    cand = make_plan(total_cost=60)
    # Actual-time based
    pct = performance_improvement_pct(baseline, cand, original_time=10.0, candidate_time=4.0)
    assert pct == '+60.0%'
    # Cost-based fallback
    pct_cost = performance_improvement_pct(baseline, cand)
    assert pct_cost == '+40.0%'
    # No cost data -> None
    zero_baseline = make_plan(total_cost=0)
    assert performance_improvement_pct(zero_baseline, zero_baseline) is None
    print(f"Improvement: time={pct} cost={pct_cost}")
    print("[OK] Improvement percentage")


if __name__ == '__main__':
    print("=" * 60)
    print("Testing Candidate Ranker")
    print("=" * 60)

    test_compute_performance_score_cheaper_wins()
    test_compute_performance_score_time_dominates()
    test_score_secondary_factors()
    test_rank_candidates_cheaper_sql_wins()
    test_rank_candidates_actual_time_beats_estimate()
    test_rank_candidates_semantic_unsafe_demoted()
    test_rank_candidates_evidence_tiebreak()
    test_assess_confidence_matrix()
    test_assess_confidence_semantic_invalid()
    test_performance_improvement_pct()

    print("\n" + "=" * 60)
    print("All ranking tests PASSED!")
    print("=" * 60)