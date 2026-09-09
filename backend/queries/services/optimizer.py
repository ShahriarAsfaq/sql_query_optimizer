"""
Optimizer Service - generates optimized query candidates and scores them.
"""
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, asdict, field
import logging
import re
import copy
from datetime import datetime

from .sql_parser import get_parser, ParsedQuery, SQLParserService
from .validator import ValidationService, ValidationIssue, ValidationSeverity
from .intent import IntentService, StructuredIntent, IntentCategory
from ..services.llm_client import LLMClient, MockLLMClient
from .calcite_client import CalciteClient, CalciteOptimizeResult
from .plan_analyzer import PlanAnalyzer, PlanAnalysis, PlanNodeMetrics, create_plan_analyzer
from .semantic_validator import SemanticValidator, SemanticSafety, SemanticCheckResult
from .query_analyzer import analyze_query_structure, QueryStructure
from .opportunity_detector import detect_optimization_opportunities, opportunity_list_to_dicts, OptimizationOpportunity
from .rewrite_engine import RewriteEngine
from .index_advisor import recommend_indexes, detect_redundant_indexes, IndexRecommendation, index_advice_to_dicts
from .statistics_analyzer import analyze_statistics, StatisticsRecommendation
from .candidate_ranker import (
    compute_performance_score, rank_candidates, assess_confidence,
    performance_improvement_pct, ConfidenceLevel, EvidenceSource, PerformanceScore
)
from .cost_model import HeuristicCostModel, estimate_cost_from_structure
from .optimization_reasoning import (
    OptimizationReasoning, compare_plans, decide_strategy,
)
from ml_services.ml_service import get_ml_service

logger = logging.getLogger(__name__)


@dataclass
class CandidateQuery:
    """A candidate query with metadata."""
    sql: str
    description: str
    cost: Optional[float] = None
    complexity_score: Optional[float] = None
    validation_passed: bool = False
    validation_errors: List[str] = None

    # New fields for performance-driven ranking
    startup_cost: Optional[float] = None
    plan_rows: Optional[float] = None
    plan_width: Optional[int] = None
    plan_analysis: Optional[PlanAnalysis] = None
    cost_source: str = "heuristic"
    semantic_safety: str = "unknown"
    semantic_details: List[Dict[str, Any]] = field(default_factory=list)
    optimization_reasons: List[str] = field(default_factory=list)
    confidence: str = "LOW"
    performance_score: Optional[float] = None
    cost_change_percent: Optional[float] = None
    rewrite_rules_applied: List[str] = field(default_factory=list)

    # --- Phase 9 additive fields ---
    semantic_risk: str = "LOW"              # LOW | MEDIUM | HIGH
    plan_metrics: Optional[Dict[str, Any]] = None
    optimization_opportunities: List[Dict[str, Any]] = field(default_factory=list)
    index_recommendations: List[Dict[str, Any]] = field(default_factory=list)
    statistics_recommendations: List[Dict[str, Any]] = field(default_factory=list)
    evidence_quality: str = "HEURISTIC"     # EXPLAIN_ANALYZE | EXPLAIN | SCHEMA_STATS | HEURISTIC
    actual_execution_time: Optional[float] = None
    planning_time: Optional[float] = None
    execution_time: Optional[float] = None
    row_estimation_quality: Optional[str] = None
    performance_improvement: Optional[str] = None
    evidence_source: str = "HEURISTIC"
    confidence_level: str = "LOW"
    confidence_score: float = 0.0

    def __post_init__(self):
        if self.validation_errors is None:
            self.validation_errors = []
        if self.semantic_details is None:
            self.semantic_details = []
        if self.optimization_reasons is None:
            self.optimization_reasons = []
        if self.rewrite_rules_applied is None:
            self.rewrite_rules_applied = []
        if self.optimization_opportunities is None:
            self.optimization_opportunities = []
        if self.index_recommendations is None:
            self.index_recommendations = []
        if self.statistics_recommendations is None:
            self.statistics_recommendations = []

    def to_dict(self) -> Dict[str, Any]:
        result = {
            'sql': self.sql,
            'description': self.description,
            'cost': self.cost,
            'startup_cost': self.startup_cost,
            'plan_rows': self.plan_rows,
            'plan_width': self.plan_width,
            'complexity_score': self.complexity_score,
            'validation_passed': self.validation_passed,
            'validation_errors': self.validation_errors,
            'cost_source': self.cost_source,
            'semantic_safety': self.semantic_safety,
            'semantic_details': self.semantic_details,
            'optimization_reasons': self.optimization_reasons,
            'confidence': self.confidence,
            'performance_score': self.performance_score,
            'cost_change_percent': self.cost_change_percent,
            'rewrite_rules_applied': self.rewrite_rules_applied,
            # New additive fields (backward compatible)
            'semantic_risk': self.semantic_risk,
            'plan_metrics': self.plan_metrics,
            'optimization_opportunities': [
                o.to_dict() if hasattr(o, 'to_dict') else o
                for o in (self.optimization_opportunities or [])
            ],
            'index_recommendations': self.index_recommendations,
            'statistics_recommendations': self.statistics_recommendations,
            'evidence_quality': self.evidence_quality,
            'actual_execution_time': self.actual_execution_time,
            'planning_time': self.planning_time,
            'execution_time': self.execution_time,
            'row_estimation_quality': self.row_estimation_quality,
            'performance_improvement': self.performance_improvement,
            'evidence_source': self.evidence_source,
            'confidence_level': self.confidence_level,
            'confidence_score': self.confidence_score,
        }
        if self.plan_analysis:
            result['plan_analysis'] = self.plan_analysis.to_dict()
        return result


class OptimizerService:
    """
    Service for query optimization: generating candidates, scoring, and ranking.
    Supports both rule-based (built-in) and Calcite-based optimization.
    """

    def __init__(
        self,
        schema: Optional[Dict[str, Any]] = None,
        seed_db_connection=None,
        llm_client=None,
        use_mock_llm: bool = False,
        use_calcite: bool = True,
        enable_actual_execution: bool = False,
        row_estimation_thresholds: Optional[Dict[str, float]] = None,
        optimization_limits: Optional[Dict[str, Any]] = None,
    ):
        """
        Initialize optimizer service.

        Args:
            schema: Database schema for validation
            seed_db_connection: Database connection for EXPLAIN (psycopg2 connection)
            llm_client: LLM client for intent extraction
            use_mock_llm: Use mock LLM for testing
            use_calcite: Whether to use Calcite server for optimization
            enable_actual_execution: If True, run EXPLAIN ANALYZE for SELECT/WITH queries.
                                     NEVER runs on DML. Default False.
            row_estimation_thresholds: Custom thresholds for row estimation quality
                                       (GOOD < x, MODERATE x-y, POOR y-z, SEVERE > z).
            optimization_limits: Limits for candidate generation (max_candidates, etc.).
        """
        self.schema = schema or {}
        self.seed_db = seed_db_connection
        self.parser = get_parser()
        self.validator = ValidationService(self.schema) if self.schema else None
        self.intent_service = IntentService(llm_client or (MockLLMClient() if use_mock_llm else None))

        # Calcite integration
        self.use_calcite = use_calcite
        self.calcite_client = CalciteClient() if use_calcite else None

        # Plan analyzer for EXPLAIN-based performance scoring
        self.plan_analyzer = create_plan_analyzer(thresholds=row_estimation_thresholds)

        # Semantic validator for rewrite safety checking
        self.semantic_validator = SemanticValidator(self.schema)

        # New modules
        self.rewrite_engine = RewriteEngine(self.schema)
        self.cost_model = HeuristicCostModel(self.schema)
        self.index_advisor_thresholds = row_estimation_thresholds or {}
        self.optimization_limits = optimization_limits or {}

        # EXPLAIN ANALYZE safety flag
        self.enable_actual_execution = enable_actual_execution

    def optimize(self, sql: str) -> Dict[str, Any]:
        """
        Optimize a SQL query by generating and ranking candidates.

        Args:
            sql: Original SQL query

        Returns:
            Dict with original query, cost, and ranked candidates
        """
        # Try Calcite first if enabled
        if self.use_calcite and self.calcite_client and self.calcite_client.is_available():
            try:
                return self._optimize_with_calcite(sql)
            except Exception as e:
                logger.warning(f"Calcite optimization failed, falling back to built-in: {e}")

        # Fallback to built-in optimization
        return self._optimize_builtin(sql)

    def _optimize_with_calcite(self, sql: str) -> Dict[str, Any]:
        """Optimize using Calcite server."""
        calcite_result = self.calcite_client.optimize(
            sql=sql,
            schema=self.schema if self.schema else None,
            dialect="postgresql",
            explain=True
        )

        if not calcite_result.valid:
            raise Exception(calcite_result.error or "Calcite optimization failed")

        # Parse original SQL for validation
        original_parsed = self.parser.parse(calcite_result.original_sql)

        # Calcite returns a logical plan representation, not SQL
        # Use original SQL for validation, but include Calcite's optimized plan
        validation_passed = original_parsed.is_valid
        validation_errors = [e.message for e in original_parsed.errors] if not validation_passed else []

        # Compute complexity score from original parsed query
        complexity_score = self._compute_complexity_score(original_parsed, sql)

        # Reasoning layer (additive) — intents + heuristic cost flow + strategy.
        # No baseline plan exists on the Calcite path, so evidence is heuristic.
        query_structure = analyze_query_structure(original_parsed, sql, self.schema)
        reasoning = OptimizationReasoning(
            query_structure, self.schema, None, None, False, [])
        has_calcite_rules = bool(calcite_result.rules_applied)
        calcite_strategy = {
            'result_state': 'SQL_REWRITE' if has_calcite_rules else 'NO_CHANGE_REQUIRED',
            'strategy_candidates': [
                {'type': 'SQL_REWRITE',
                 'action': ', '.join(calcite_result.rules_applied[:5]),
                 'rationale': 'Calcite applied optimizer rules to the query.',
                 'evidence': calcite_result.optimized_sql or '',
                 'confidence': 0.65} if has_calcite_rules else
                {'type': 'NO_SQL_CHANGE',
                 'action': '',
                 'rationale': 'Calcite applied no rules; query left unchanged.',
                 'evidence': '',
                 'confidence': 0.5},
            ],
            'decision_path': ['calcite rules applied' if has_calcite_rules
                              else 'no calcite rules applied'],
        }

        # The optimized_sql from Calcite is a logical plan - we present it as the optimization result
        # but keep the original SQL as the actual query
        candidate = {
            'sql': sql,  # Keep original SQL as the executable query
            'description': 'Calcite optimized query (logical plan shown)',
            'cost': calcite_result.optimized_cost,
            'complexity_score': complexity_score,
            'validation_passed': validation_passed,
            'validation_errors': validation_errors,
            'calcite_optimized_plan': calcite_result.optimized_sql,  # Store the logical plan
            'calcite_rules_applied': calcite_result.rules_applied,
            'calcite_explain_plan': calcite_result.explain_plan,
            # New fields for normalized response shape
            'semantic_risk': 'LOW',
            'evidence_quality': 'CALCITE',
            'evidence_source': 'CALCITE',
            'confidence_level': 'MEDIUM',
            'confidence_score': 0.65,
            'performance_improvement': None,
            'optimization_opportunities': [],
            'index_recommendations': [],
            'statistics_recommendations': [],
            'plan_metrics': None,
            'actual_execution_time': None,
            'planning_time': None,
            'execution_time': None,
            'row_estimation_quality': None,
        }

        return {
            'original_sql': calcite_result.original_sql,
            'original_cost': calcite_result.original_cost,
            'candidates': [candidate],
            'best_candidate': candidate,
            # New top-level fields for normalized shape
            'sql_optimizations': [{
                'sql': sql,
                'description': candidate['description'],
                'estimated_cost': calcite_result.optimized_cost,
                'performance_score': None,
                'cost_change_percent': None,
                'rewrite_rules_applied': calcite_result.rules_applied,
                'semantic_risk': 'LOW',
                'evidence_quality': 'CALCITE',
                'confidence_level': 'MEDIUM',
            }],
            'index_recommendations': [],
            'statistics_recommendations': [],
            'warnings': [],
            'opportunities': [],
            'evidence_source': 'CALCITE',
            'heuristic_used': False,
            # --- Thinking engine (additive, heuristic evidence) ---
            'query_intent': reasoning.intent.to_dict(),
            'cost_flow': reasoning.cost_flow.to_dict(),
            'reasoning': reasoning.to_dict(),
            'optimization_strategy': calcite_strategy,
            'plan_comparison': None,
        }

    def _optimize_builtin(self, sql: str, enable_actual_execution: Optional[bool] = None) -> Dict[str, Any]:
        """
        New evidence-driven optimization pipeline.

        Pipeline:
        1. Parse & validate original query
        2. Analyze query structure (QueryStructure)
        3. Baseline EXPLAIN (optionally ANALYZE behind flag) -> PlanAnalysis
        4. Detect optimization opportunities + statistics analysis
        5. Generate candidates (existing rules + RewriteEngine new rules)
        6. Per-candidate pipeline (validation, EXPLAIN, analysis) + attach opportunities + index/statistics recs + evidence_quality + semantic_risk
        7. Index recommendations + redundant index detection
        8. Candidate ranking + confidence assessment
        9. Enriched response
        """
        # Determine if we should run EXPLAIN ANALYZE
        run_analyze = self.enable_actual_execution if enable_actual_execution is None else enable_actual_execution

        # Step 1: Parse original query
        original_parsed = self.parser.parse(sql)
        if not original_parsed.is_valid:
            return {
                'original_sql': sql,
                'original_cost': None,
                'candidates': [],
                'best_candidate': None,
                'error': 'Original query is invalid'
            }

        # Step 2: Deep query structure analysis
        query_structure = analyze_query_structure(original_parsed, sql, self.schema)

        # Step 3: Baseline EXPLAIN (and optionally ANALYZE for SELECT/WITH only)
        original_explain = self._get_explain_plan(sql, analyze=run_analyze)
        original_analysis = self.plan_analyzer.analyze(original_explain) if original_explain else None
        original_cost = original_analysis.total_cost if original_analysis else None

        # Step 4: Detect optimization opportunities + statistics analysis
        baseline_opportunities = detect_optimization_opportunities(query_structure, original_analysis, self.schema)
        stats_opportunities, stats_recommendations = [], []
        if self.seed_db:
            stats_opportunities, stats_recommendations = analyze_statistics(
                sql, original_parsed, self.seed_db,
                plan=original_analysis,
                explain_analyze=original_explain if run_analyze else None,
                schema=self.schema,
            )
        all_opportunities = baseline_opportunities + stats_opportunities

        # Step 4b: Reasoning layer (thinking engine) — additive intent/cost-flow/
        # top-N/strategy reasoning built from structure + plan + schema. Pure
        # read-only: never mutates candidates or ranking.
        reasoning = OptimizationReasoning(
            query_structure, self.schema, original_analysis, original_explain,
            run_analyze, all_opportunities
        )

        # Step 5: Generate candidates (existing rules + new RewriteEngine rules)
        candidates = self._generate_candidates(original_parsed, sql)

        # Add RewriteEngine candidates
        rewrite_candidates = self._generate_rewrite_engine_candidates(query_structure, sql, original_parsed)
        candidates.extend(rewrite_candidates)

        # Process each candidate through the full pipeline
        scored_candidates = []
        for candidate in candidates:
            scored = self._process_candidate_pipeline(
                candidate, original_parsed, original_analysis, sql,
                query_structure, all_opportunities
            )
            if scored:
                scored_candidates.append(scored)

        # Also include original query as a baseline candidate (for comparison)
        original_candidate = self._create_baseline_candidate(sql, original_parsed, original_analysis, original_explain)
        scored_candidates.insert(0, original_candidate)

        # Step 7: Index recommendations (schema + query driven) + redundant index detection
        index_recommendations = recommend_indexes(query_structure, self.schema, original_analysis)
        redundant_indexes = detect_redundant_indexes(self.schema)

        # Step 8: Rank candidates using candidate_ranker (layered ranking + evidence-based confidence)
        ranked = rank_candidates(
            [c.to_dict() for c in scored_candidates],
            original_analysis,
            original_analysis.total_execution_time if original_analysis else None
        )

        # Convert back to CandidateQuery objects with enriched data
        enriched_candidates = self._enrich_candidates(ranked, scored_candidates,
                                                       query_structure, all_opportunities,
                                                       index_recommendations, redundant_indexes,
                                                       stats_recommendations,
                                                       original_analysis,
                                                       original_analysis.total_execution_time if original_analysis else None)

        # Get best candidate (excluding original baseline from "best" if it's the only one)
        best = enriched_candidates[0] if enriched_candidates else None

        # Correctness completion: if the original carries an unresolved
        # CURRENT_YEAR placeholder and a validated qualify_columns candidate
        # resolves it, promote that candidate as the recommended output. The
        # raw query cannot return the intended rows, and EXPLAIN ties (aliases
        # don't change cost), so the performance ranker alone keeps baseline.
        # The top_better guard never masks a genuinely faster candidate.
        resolution_warning = None
        if re.search(r'CURRENT_YEAR', sql, re.IGNORECASE):
            resolved = [c for c in enriched_candidates
                        if c.validation_passed
                        and 'qualify_columns' in (c.rewrite_rules_applied or [])
                        and not re.search(r'CURRENT_YEAR', c.sql, re.IGNORECASE)]
            top = enriched_candidates[0] if enriched_candidates else None
            top_better = (top is not None
                          and resolved
                          and top is not resolved[0]
                          and (top.performance_score or 0) > (resolved[0].performance_score or 1.0) + 0.001)
            if resolved and not top_better:
                best = resolved[0]
                resolution_warning = (
                    "Original query contains an unresolved CURRENT_YEAR placeholder; "
                    f"resolved it to '{datetime.now().year}' in the recommended query."
                )

        # Reasoning outputs: plan comparison (WHY best beats baseline) and the
        # final strategy decision tree. Both additive; computed only from
        # already-available data.
        plan_comparison = None
        if best is not None and best.plan_analysis and original_analysis:
            plan_comparison = compare_plans(original_analysis, best.plan_analysis)

        best_dict = best.to_dict() if best else None
        if best_dict is not None and plan_comparison is not None:
            best_dict['improvement_evidence'] = plan_comparison.to_dict()

        strategy = None
        if enriched_candidates:
            winning = best.to_dict() if best else None
            strategy = decide_strategy(
                query_structure, reasoning.intent, reasoning.cost_flow,
                reasoning.top_n, reasoning.early_term,
                list(index_recommendations) + list(redundant_indexes),
                list(stats_recommendations),
                baseline_plan=original_analysis,
                best_candidate=winning,
                has_plan_evidence=original_analysis is not None,
            )

        warnings = self._generate_warnings(enriched_candidates, all_opportunities)
        if resolution_warning:
            warnings.append(resolution_warning)

        return {
            'original_sql': sql,
            'original_cost': original_cost,
            'original_plan_analysis': original_analysis.to_dict() if original_analysis else None,
            'candidates': [c.to_dict() for c in enriched_candidates],
            'best_candidate': best_dict,
            # New top-level fields
            'sql_optimizations': self._extract_sql_optimizations(enriched_candidates),
            'index_recommendations': index_advice_to_dicts(index_recommendations + redundant_indexes),
            'statistics_recommendations': [r.to_dict() for r in stats_recommendations],
            'warnings': self._generate_warnings(enriched_candidates, all_opportunities),
            'opportunities': opportunity_list_to_dicts(all_opportunities),
            'evidence_source': 'EXPLAIN_ANALYZE' if run_analyze and original_analysis and original_analysis.total_execution_time > 0 else 'EXPLAIN',
            'heuristic_used': original_analysis is None,
            # --- Thinking engine (additive) ---
            'query_intent': reasoning.intent.to_dict(),
            'cost_flow': reasoning.cost_flow.to_dict(),
            'reasoning': reasoning.to_dict(),
            'optimization_strategy': strategy.to_dict() if strategy else None,
            'plan_comparison': plan_comparison.to_dict() if plan_comparison else None,
        }

    def _process_candidate_pipeline(
        self,
        candidate: CandidateQuery,
        original_parsed: ParsedQuery,
        original_analysis: Optional[PlanAnalysis],
        original_sql: str
    ) -> Optional[CandidateQuery]:
        """
        Process a candidate through the full validation and scoring pipeline.

        Steps:
        1. Parse candidate SQL
        2. Schema validation (hard constraint - fail fast)
        3. Semantic validation (hard constraint for UNSAFE rewrites)
        4. EXPLAIN plan extraction
        5. Plan analysis
        6. Performance scoring
        7. Confidence assessment
        """
        # Step 1: Parse candidate
        candidate_parsed = self.parser.parse(candidate.sql)
        if not candidate_parsed.is_valid:
            candidate.validation_passed = False
            candidate.validation_errors = [e.message for e in candidate_parsed.errors]
            return candidate

        # Step 2: Schema validation (hard constraint)
        if self.validator:
            validation = self.validator.validate(candidate_parsed)
            candidate.validation_passed = validation['is_valid']
            candidate.validation_errors = [
                i['message'] for i in validation['issues']
                if i['severity'] == 'error'
            ]
        else:
            candidate.validation_passed = candidate_parsed.is_valid
            candidate.validation_errors = [e.message for e in candidate_parsed.errors] if not candidate_parsed.is_valid else []

        if not candidate.validation_passed:
            return candidate

        # Step 3: Semantic validation (hard constraint for UNSAFE)
        rewrite_rules_applied = getattr(candidate, 'rewrite_rules_applied', [])
        semantic_result = self.semantic_validator.validate_candidate(
            original_sql, candidate.sql,
            original_parsed, candidate_parsed,
            rewrite_rules_applied
        )

        candidate.semantic_safety = semantic_result['semantic_safety']
        candidate.semantic_details = semantic_result['safety_details']

        # HARD CONSTRAINT: Reject UNSAFE rewrites
        if not semantic_result['semantically_valid']:
            candidate.validation_passed = False
            candidate.validation_errors.append(f"Semantic validation failed: {semantic_result['semantic_safety']}")
            return candidate

        # Step 4: EXPLAIN plan extraction
        candidate_explain = self._get_explain_plan(candidate.sql)

        # Step 5: Plan analysis
        if candidate_explain:
            candidate.plan_analysis = self.plan_analyzer.analyze(candidate_explain)
            candidate.cost = candidate.plan_analysis.total_cost
            candidate.startup_cost = candidate.plan_analysis.total_startup_cost
            candidate.plan_rows = candidate.plan_analysis.total_plan_rows
            candidate.cost_source = "postgresql_explain"
        else:
            # Fallback to heuristic cost
            candidate.cost = self._estimate_cost_from_structure(candidate.sql)
            candidate.cost_source = "heuristic"

        # Step 6: Performance scoring
        if original_analysis and candidate.plan_analysis:
            candidate.performance_score = self._calculate_performance_score(
                original_analysis, candidate.plan_analysis
            )
            candidate.cost_change_percent = self._calculate_cost_change_percent(
                original_analysis, candidate.plan_analysis
            )
        else:
            candidate.performance_score = None
            candidate.cost_change_percent = None

        # Step 7: Complexity score (secondary signal)
        candidate.complexity_score = self._compute_complexity_score(candidate_parsed, candidate.sql)

        # Step 8: Generate optimization reasons
        candidate.optimization_reasons = self._generate_optimization_reasons(
            candidate, original_analysis, rewrite_rules_applied
        )

        # Step 9: Confidence assessment
        candidate.confidence = self._assess_confidence(
            candidate, original_analysis, semantic_result
        )

        return candidate

    def _calculate_performance_score(self, original: PlanAnalysis, candidate: PlanAnalysis) -> float:
        """Calculate relative performance score (candidate vs original).

        Score > 1.0 means candidate is faster (lower cost).
        Score < 1.0 means candidate is slower (higher cost).
        """
        if original.total_cost <= 0:
            return 1.0

        # Primary metric: total cost ratio
        cost_ratio = original.total_cost / candidate.total_cost

        # Adjust for row estimation quality
        estimation_penalty = 1.0
        if candidate.estimation_error_nodes > 0:
            # Penalize plans with high estimation errors (unreliable plans)
            avg_error = candidate.avg_estimation_error if candidate.estimation_error_nodes > 0 else 1.0
            # If avg error > 2x or < 0.5x, apply penalty
            if avg_error > 2.0 or avg_error < 0.5:
                estimation_penalty = 0.8

        # Adjust for scan efficiency
        scan_bonus = 1.0
        total_scans = candidate.seq_scans + candidate.index_scans + candidate.index_only_scans + candidate.bitmap_heap_scans
        if total_scans > 0:
            index_ratio = (candidate.index_scans + candidate.index_only_scans + candidate.bitmap_heap_scans) / total_scans
            if index_ratio > 0.7:  # Mostly index scans
                scan_bonus = 1.1
            elif index_ratio < 0.3:  # Mostly seq scans
                scan_bonus = 0.9

        score = cost_ratio * estimation_penalty * scan_bonus

        # Cap to reasonable range
        return max(0.1, min(10.0, round(score, 3)))

    def _calculate_cost_change_percent(self, original: PlanAnalysis, candidate: PlanAnalysis) -> float:
        """Calculate percentage cost change (negative = improvement)."""
        if original.total_cost <= 0:
            return 0.0
        return round(((candidate.total_cost - original.total_cost) / original.total_cost) * 100, 2)

    def _generate_optimization_reasons(
        self,
        candidate: CandidateQuery,
        original_analysis: Optional[PlanAnalysis],
        rewrite_rules_applied: List[str]
    ) -> List[str]:
        """Generate human-readable reasons for the optimization."""
        reasons = []

        if not original_analysis or not candidate.plan_analysis:
            reasons.append("Cost estimated heuristically (no EXPLAIN available)")
            return reasons

        orig = original_analysis
        cand = candidate.plan_analysis

        # Cost improvement
        if candidate.cost_change_percent is not None:
            if candidate.cost_change_percent < -5:
                reasons.append(f"Estimated cost reduced by {abs(candidate.cost_change_percent):.1f}%")
            elif candidate.cost_change_percent > 5:
                reasons.append(f"Estimated cost increased by {candidate.cost_change_percent:.1f}%")
            else:
                reasons.append("Estimated cost similar to original")

        # Scan type improvements
        if cand.seq_scans < orig.seq_scans:
            reasons.append(f"Reduced sequential scans: {orig.seq_scans} -> {cand.seq_scans}")
        if cand.index_scans + cand.index_only_scans > orig.index_scans + orig.index_only_scans:
            reasons.append(f"Increased index scans: {orig.index_scans + orig.index_only_scans} -> {cand.index_scans + cand.index_only_scans}")

        # Join improvements
        if cand.nested_loops < orig.nested_loops and (cand.hash_joins > orig.hash_joins or cand.merge_joins > orig.merge_joins):
            reasons.append("Improved join strategy (fewer nested loops, more hash/merge joins)")

        # Sort improvements
        if cand.sorts < orig.sorts:
            reasons.append(f"Reduced sort operations: {orig.sorts} -> {cand.sorts}")
        if cand.incremental_sorts > orig.incremental_sorts:
            reasons.append("Uses incremental sort (memory efficient)")

        # Subquery/CTE improvements
        if cand.subplans < orig.subplans:
            reasons.append(f"Reduced subplans: {orig.subplans} -> {cand.subplans}")
        if cand.cte_scans > orig.cte_scans:
            reasons.append("Uses CTE scans (potentially better materialization)")

        # Row estimation quality
        if cand.estimation_error_nodes < orig.estimation_error_nodes:
            reasons.append("Improved row estimation accuracy")

        # Rewrite rules applied
        for rule in rewrite_rules_applied:
            rule_desc = self._get_rule_description(rule)
            if rule_desc:
                reasons.append(f"Applied: {rule_desc}")

        if not reasons:
            reasons.append("No significant performance difference detected")

        return reasons

    def _get_rule_description(self, rule_name: str) -> Optional[str]:
        """Get human-readable description of a rewrite rule."""
        rule_map = {
            'expand_select_star': 'SELECT * expansion',
            'add_limit_to_order_by': 'LIMIT added to ORDER BY',
            'rewrite_non_sargable': 'Non-sargable predicate rewrite (e.g., LOWER() to ILIKE)',
            'rewrite_correlated_subquery': 'Correlated subquery to JOIN/window function',
            'add_join_condition': 'Suggested JOIN condition',
            'rewrite_in_subquery': 'IN subquery to EXISTS/JOIN',
            'push_down_predicates': 'Predicate pushdown into CTEs',
            'convert_to_cte': 'Nested subquery to CTE',
            'reorder_joins': 'JOIN reordering',
            'predicate_pushdown': 'Predicate pushdown',
            'remove_distinct': 'DISTINCT removal (proven safe)',
            'union_to_union_all': 'UNION to UNION ALL (UNSAFE - not applied)',
            'outer_join_to_inner_join': 'OUTER to INNER JOIN (UNSAFE - not applied)',
        }
        return rule_map.get(rule_name)

    def _assess_confidence(
        self,
        candidate: CandidateQuery,
        original_analysis: Optional[PlanAnalysis],
        semantic_result: Dict[str, Any]
    ) -> str:
        """Assess confidence level in the optimization recommendation."""
        # Base confidence on multiple factors
        confidence_factors = []

        # Factor 1: EXPLAIN availability
        if candidate.cost_source == "postgresql_explain":
            confidence_factors.append("high")
        else:
            confidence_factors.append("low")

        # Factor 2: Semantic safety
        if candidate.semantic_safety == "safe":
            confidence_factors.append("high")
        elif candidate.semantic_safety == "conditionally_safe":
            confidence_factors.append("medium")
        else:
            confidence_factors.append("low")

        # Factor 3: Plan analysis quality (if available)
        if candidate.plan_analysis:
            if candidate.plan_analysis.estimation_error_nodes == 0:
                confidence_factors.append("high")
            elif candidate.plan_analysis.max_estimation_error < 2.0:
                confidence_factors.append("medium")
            else:
                confidence_factors.append("low")

        # Factor 4: Performance improvement magnitude
        if candidate.performance_score is not None:
            if candidate.performance_score > 1.2:
                confidence_factors.append("high")
            elif candidate.performance_score > 1.0:
                confidence_factors.append("medium")
            else:
                confidence_factors.append("low")

        # Aggregate
        high_count = confidence_factors.count("high")
        low_count = confidence_factors.count("low")

        if high_count >= 2 and low_count == 0:
            return "HIGH"
        elif low_count >= 2:
            return "LOW"
        else:
            return "MEDIUM"

    def generate_from_intent(self, structured_intent: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Generate candidate queries from structured intent.

        Args:
            structured_intent: StructuredIntent dictionary

        Returns:
            List of candidate query dictionaries
        """
        candidates = []

        # Convert dict to StructuredIntent-like object
        intent = self._dict_to_intent(structured_intent)

        # Generate base query
        base_query = self._build_query_from_intent(intent, self.schema)
        if base_query:
            candidates.append(CandidateQuery(
                sql=base_query,
                description="Primary query matching intent"
            ))

        # Generate variations
        variations = self._generate_intent_variations(intent)
        for var_sql, var_desc in variations:
            candidates.append(CandidateQuery(
                sql=var_sql,
                description=var_desc
            ))

        # Process each candidate through the new pipeline
        scored = []
        for candidate in candidates:
            parsed = self.parser.parse(candidate.sql)
            # Use the new pipeline with original_parsed as the first candidate's parsed (baseline)
            if parsed.is_valid:
                # Get EXPLAIN for this candidate
                explain = self._get_explain_plan(candidate.sql)
                analysis = self.plan_analyzer.analyze(explain) if explain else None
                candidate.cost = analysis.total_cost if analysis else self._estimate_cost_from_structure(candidate.sql)
                candidate.startup_cost = analysis.total_startup_cost if analysis else None
                candidate.plan_rows = analysis.total_plan_rows if analysis else None
                candidate.plan_analysis = analysis
                candidate.cost_source = "postgresql_explain" if analysis else "heuristic"
                candidate.complexity_score = self._compute_complexity_score(parsed, candidate.sql)
                candidate.validation_passed = True
                candidate.semantic_safety = "safe"
                candidate.semantic_details = []
                candidate.optimization_reasons = ["Intent-based generation"]
                candidate.confidence = "HIGH" if analysis else "MEDIUM"
                if analysis:
                    candidate.performance_score = 1.0  # No baseline for intent-generated
                    candidate.cost_change_percent = 0.0
                scored.append(candidate)

        # Rank using layered ranking (no original baseline for intent-generated)
        ranked = self._rank_candidates_layered(scored, None, None)

        return [c.to_dict() for c in ranked]

    def _generate_candidates(self, parsed: ParsedQuery, original_sql: str) -> List[CandidateQuery]:
        """Generate optimization candidates for a parsed query with rewrite rule tracking.

        Each candidate tracks which rewrite rules were applied for semantic validation.
        """
        candidates = []

        # 1. Replace SELECT * with explicit columns (CONDITIONALLY_SAFE)
        if any(c.name == '*' for c in parsed.columns):
            candidate_sql = self._expand_star(original_sql, parsed)
            if candidate_sql != original_sql:
                candidates.append(CandidateQuery(
                    sql=candidate_sql,
                    description="Expanded SELECT * to explicit columns",
                    rewrite_rules_applied=['expand_select_star']
                ))

        # 2. Rewrite non-sargable predicates (CONDITIONALLY_SAFE)
        candidate_sql = self._rewrite_non_sargable(original_sql, parsed)
        if candidate_sql != original_sql:
            candidates.append(CandidateQuery(
                sql=candidate_sql,
                description="Rewrote non-sargable predicates for index usage (e.g., LOWER() to ILIKE)",
                rewrite_rules_applied=['rewrite_non_sargable']
            ))

        # 3. Convert correlated subquery to JOIN/window function (CONDITIONALLY_SAFE)
        if parsed.subqueries:
            candidate_sql = self._rewrite_correlated_subquery(original_sql, parsed)
            if candidate_sql != original_sql:
                candidates.append(CandidateQuery(
                    sql=candidate_sql,
                    description="Rewrote correlated subquery as JOIN with window function",
                    rewrite_rules_applied=['rewrite_correlated_subquery']
                ))

        # 4. Add missing JOIN condition hint (SAFE - just adds condition)
        for join in parsed.joins:
            if join.type != 'CROSS' and not join.condition:
                candidate_sql = self._suggest_join_condition(original_sql, join)
                if candidate_sql != original_sql:
                    candidates.append(CandidateQuery(
                        sql=candidate_sql,
                        description=f"Added suggested JOIN condition for {join.table.name}",
                        rewrite_rules_applied=['add_join_condition']
                    ))

        # 5. Rewrite IN subquery to EXISTS or JOIN (CONDITIONALLY_SAFE)
        candidate_sql = self._rewrite_in_subquery(original_sql, parsed)
        if candidate_sql != original_sql:
            candidates.append(CandidateQuery(
                sql=candidate_sql,
                description="Rewrote IN subquery to EXISTS/JOIN for better performance",
                rewrite_rules_applied=['rewrite_in_subquery']
            ))

        # 6. Push down predicates (if CTEs present) (CONDITIONALLY_SAFE)
        if parsed.cte_names:
            candidate_sql = self._push_down_predicates(original_sql, parsed)
            if candidate_sql != original_sql:
                candidates.append(CandidateQuery(
                    sql=candidate_sql,
                    description="Pushed predicates into CTEs for early filtering",
                    rewrite_rules_applied=['push_down_predicates']
                ))

        # 7. Use CTE instead of nested subquery (SAFE)
        if parsed.subqueries and not parsed.cte_names:
            candidate_sql = self._convert_to_cte(original_sql, parsed)
            if candidate_sql != original_sql:
                candidates.append(CandidateQuery(
                    sql=candidate_sql,
                    description="Converted nested subquery to CTE for readability",
                    rewrite_rules_applied=['convert_to_cte']
                ))

        # 8. Add LIMIT to ORDER BY (UNSAFE - NOT applied automatically, only suggested)
        # This is deliberately NOT generated as an automatic candidate
        # It would change query semantics (fewer rows returned)

        # 9. JOIN reordering (SAFE - PostgreSQL planner handles this)
        # Not needed as a candidate since planner handles it

        # 10. Predicate pushdown (CONDITIONALLY_SAFE)
        # Can be applied more broadly than just CTEs
        candidate_sql = self._push_down_predicates(original_sql, parsed)
        if candidate_sql != original_sql and not parsed.cte_names:
            candidates.append(CandidateQuery(
                sql=candidate_sql,
                description="Pushed predicates closer to table scans",
                rewrite_rules_applied=['predicate_pushdown']
            ))

        return candidates

    def _generate_intent_variations(self, intent: StructuredIntent) -> List[Tuple[str, str]]:
        """Generate query variations for a given intent."""
        variations = []

        # Variation 1: Different ORDER BY direction
        if intent.order_by and intent.limit:
            for ob in intent.order_by:
                new_dir = 'ASC' if ob['direction'] == 'DESC' else 'DESC'
                var_intent = copy.deepcopy(intent)
                var_intent.order_by = [{'column': ob['column'], 'direction': new_dir}]
                var_sql = self._build_query_from_intent(var_intent, self.schema)
                if var_sql:
                    variations.append((var_sql, f"Same query with {new_dir} order"))

        # Variation 2: With/without date filter
        if intent.time_range and intent.filters:
            var_intent = copy.deepcopy(intent)
            # Handle time_range being either a dict or a string
            time_range_col = None
            if isinstance(intent.time_range, dict):
                time_range_col = intent.time_range.get('column')
            elif isinstance(intent.time_range, str):
                # For string time_range like "this year", we need to find the matching filter
                # Look for a filter that matches the time range column (e.g., "year")
                for f in intent.filters:
                    if f.get('column', '').lower() in ('year', 'date', 'created_at', 'updated_at', 'timestamp'):
                        time_range_col = f.get('column')
                        break
            if time_range_col:
                var_intent.filters = [f for f in var_intent.filters
                                      if f.get('column') != time_range_col]
                # Also clear time_range so it doesn't get re-added in _build_query_from_intent
                var_intent.time_range = None
                var_sql = self._build_query_from_intent(var_intent, self.schema)
                if var_sql:
                    variations.append((var_sql, "Without date filter"))

        # Variation 3: Different limit
        if intent.limit:
            for alt_limit in [5, 20, 50]:
                if alt_limit != intent.limit:
                    var_intent = copy.deepcopy(intent)
                    var_intent.limit = alt_limit
                    var_sql = self._build_query_from_intent(var_intent, self.schema)
                    if var_sql:
                        variations.append((var_sql, f"Limit {alt_limit} instead of {intent.limit}"))

        return variations

    def _build_query_from_intent(self, intent: StructuredIntent, schema: Optional[Dict[str, Any]] = None) -> Optional[str]:
        """Build a SQL query from structured intent."""
        if not intent.entity:
            return None

        parts = []

        # SELECT clause
        select_parts = []

        # Use explicit columns if provided
        if intent.select_columns:
            # Map user-friendly column names to actual schema columns
            mapped_columns = self._map_select_columns(intent.select_columns, intent, schema)
            select_parts.extend(mapped_columns)
        # Handle TOP_N with aggregate - need aggregate in SELECT and ORDER BY it
        elif intent.category == IntentCategory.TOP_N and intent.operation in ('COUNT', 'SUM', 'AVG', 'MIN', 'MAX') and intent.metric and intent.group_by:
            # SELECT group_by, AGG(metric) FROM ... GROUP BY group_by ORDER BY AGG(metric) DESC LIMIT N
            select_parts.extend(intent.group_by)
            agg_func = intent.operation
            select_parts.append(f"{agg_func}({intent.metric}) AS {agg_func.lower()}_{intent.metric}")
        elif intent.operation in ('COUNT', 'SUM', 'AVG', 'MIN', 'MAX') and intent.metric:
            select_parts.append(f"{intent.operation}({intent.metric})")
        elif intent.metric:
            select_parts.append(intent.metric)
        else:
            select_parts.append("*")

        if intent.group_by and not (intent.category == IntentCategory.TOP_N and intent.operation in ('COUNT', 'SUM', 'AVG', 'MIN', 'MAX') and intent.metric):
            select_parts.extend(intent.group_by)

        parts.append(f"SELECT {', '.join(select_parts)}")

        # FROM clause
        parts.append(f"FROM {intent.entity}")

        # JOINs (if entity suggests joins needed based on schema relationships)
        join_clauses = self._build_join_clauses(intent, schema)
        if join_clauses:
            parts.extend(join_clauses)

        # WHERE clause
        where_conditions = []
        for f in intent.filters:
            col = f.get('column', '')
            op = f.get('operator', '=')
            val = f.get('value')
            if isinstance(val, str):
                val = f"'{val}'"
            where_conditions.append(f"{col} {op} {val}")

        if intent.time_range:
            tr = intent.time_range
            if isinstance(tr, dict):
                col = tr.get('column', 'created_at')
                if tr.get('start'):
                    where_conditions.append(f"{col} >= '{tr['start']}'")
                if tr.get('end'):
                    where_conditions.append(f"{col} <= '{tr['end']}'")

        if where_conditions:
            parts.append(f"WHERE {' AND '.join(where_conditions)}")

        # GROUP BY
        if intent.group_by:
            parts.append(f"GROUP BY {', '.join(intent.group_by)}")

        # ORDER BY
        if intent.order_by:
            order_parts = [f"{o['column']} {o['direction']}" for o in intent.order_by]
            parts.append(f"ORDER BY {', '.join(order_parts)}")
        elif intent.category == IntentCategory.TOP_N and intent.operation in ('COUNT', 'SUM', 'AVG', 'MIN', 'MAX') and intent.metric:
            # Default ORDER BY for TOP_N with aggregate
            agg_func = intent.operation
            # Determine direction from keywords in raw text
            direction = 'DESC'
            if any(kw in intent.raw_text.lower() for kw in ['lowest', 'smallest', 'least', 'bottom']):
                direction = 'ASC'
            parts.append(f"ORDER BY {agg_func.lower()}_{intent.metric} {direction}")

        # LIMIT
        if intent.limit:
            parts.append(f"LIMIT {intent.limit}")

        return " ".join(parts)

    def _map_select_columns(self, select_columns: List[str], intent: StructuredIntent, schema: Optional[Dict[str, Any]]) -> List[str]:
        """Map user-friendly column names to actual schema column names with table prefixes."""
        if not schema or not schema.get('tables'):
            return select_columns

        # Build column -> table mapping, handling collisions by storing list of tables
        col_to_tables = {}
        for table_name, table_def in schema.get('tables', {}).items():
            for col in table_def.get('columns', {}).keys():
                col_lower = col.lower()
                if col_lower not in col_to_tables:
                    col_to_tables[col_lower] = []
                col_to_tables[col_lower].append((table_name, col))

        mapped = []
        entity = intent.entity
        join_tables = intent.join_tables or []

        for user_col in select_columns:
            user_col_lower = user_col.lower().strip()

            # Try exact match first
            if user_col_lower in col_to_tables:
                tables_with_col = col_to_tables[user_col_lower]

                # If only one table has this column, use it
                if len(tables_with_col) == 1:
                    table_name, actual_col = tables_with_col[0]
                    if table_name == entity:
                        mapped.append(actual_col)
                    else:
                        mapped.append(f"{table_name}.{actual_col}")
                else:
                    # Multiple tables have this column - prefer entity table, then join_tables
                    # Check if entity has this column
                    entity_match = [t for t in tables_with_col if t[0] == entity]
                    if entity_match:
                        table_name, actual_col = entity_match[0]
                        mapped.append(actual_col)
                    else:
                        # Check join_tables in order
                        found = False
                        for jt in join_tables:
                            jt_match = [t for t in tables_with_col if t[0] == jt]
                            if jt_match:
                                table_name, actual_col = jt_match[0]
                                mapped.append(f"{table_name}.{actual_col}")
                                found = True
                                break
                        if not found:
                            # Fallback to first table
                            table_name, actual_col = tables_with_col[0]
                            if table_name == entity:
                                mapped.append(actual_col)
                            else:
                                mapped.append(f"{table_name}.{actual_col}")
            else:
                # Try fuzzy match (singular/plural, prefix)
                found = False
                for col_lower, tables_with_col in col_to_tables.items():
                    if user_col_lower == col_lower.rstrip('s') or user_col_lower + 's' == col_lower:
                        # Prefer entity table
                        entity_match = [t for t in tables_with_col if t[0] == entity]
                        if entity_match:
                            table_name, actual_col = entity_match[0]
                            mapped.append(actual_col)
                        else:
                            # Check join_tables
                            for jt in join_tables:
                                jt_match = [t for t in tables_with_col if t[0] == jt]
                                if jt_match:
                                    table_name, actual_col = jt_match[0]
                                    mapped.append(f"{table_name}.{actual_col}")
                                    found = True
                                    break
                            if not found and tables_with_col:
                                table_name, actual_col = tables_with_col[0]
                                if table_name == entity:
                                    mapped.append(actual_col)
                                else:
                                    mapped.append(f"{table_name}.{actual_col}")
                        found = True
                        break
                if not found:
                    # Fallback: assume it's from the main entity
                    mapped.append(user_col)

        return mapped

    def _get_explain_cost(self, sql: str) -> Optional[float]:
        """Get estimated cost from PostgreSQL EXPLAIN."""
        if self.seed_db:
            try:
                with self.seed_db.cursor() as cursor:
                    cursor.execute(f"EXPLAIN (FORMAT JSON) {sql}")
                    result = cursor.fetchone()
                    if result and result[0]:
                        plan = result[0][0]['Plan']
                        return float(plan.get('Total Cost', 0))
            except Exception as e:
                logger.warning(f"EXPLAIN failed for query: {e}")

        # Fallback: estimate cost based on query structure (mock implementation)
        return self._estimate_cost_from_structure(sql)

    def _estimate_cost_from_structure(self, sql: str) -> float:
        """Estimate query cost from structure when EXPLAIN is not available.

        This is a simplified cost model based on:
        - Table scans (seq scan cost ~ 1.0 per 1000 rows estimated)
        - Index scans (index scan cost ~ 0.1 per row)
        - Joins (nested loop ~ 10, hash join ~ 5, merge join ~ 3)
        - Aggregations (group by ~ 5, sort ~ 3)
        - Subqueries (correlated ~ 50, uncorrelated ~ 10)
        """
        sql_upper = sql.upper()
        cost = 0.0

        # Base cost for SELECT
        cost += 1.0

        # Table scans
        from_count = sql_upper.count(' FROM ') + sql_upper.count(' JOIN ')
        cost += from_count * 10.0

        # Sequential scan penalty (no WHERE or no indexable condition)
        where_pos = sql_upper.find(' WHERE ')
        if where_pos == -1 and ' LIMIT ' not in sql_upper:
            cost += 20.0  # Full table scan

        # Indexable conditions
        if where_pos != -1:
            where_clause = sql_upper[where_pos:]
            # Check for indexable patterns
            if any(op in where_clause for op in ['= ', '> ', '< ', '>= ', '<= ', ' IN ', ' LIKE ']):
                cost += 2.0  # Index scan
            else:
                cost += 10.0  # Filter after scan

        # Joins
        join_types = [' INNER JOIN ', ' LEFT JOIN ', ' RIGHT JOIN ', ' JOIN ']
        for jt in join_types:
            cost += sql_upper.count(jt) * 15.0

        # Aggregations
        if ' GROUP BY ' in sql_upper:
            cost += 10.0
        if any(agg in sql_upper for agg in [' COUNT(', ' SUM(', ' AVG(', ' MIN(', ' MAX(']):
            cost += 5.0

        # Sorting
        if ' ORDER BY ' in sql_upper:
            cost += 5.0

        # Subqueries
        subquery_count = sql_upper.count('SELECT') - 1
        if subquery_count > 0:
            cost += subquery_count * 20.0

        # LIMIT reduces cost
        import re
        limit_match = re.search(r'LIMIT\s+(\d+)', sql_upper)
        if limit_match:
            limit_val = int(limit_match.group(1))
            if limit_val < 100:
                cost *= 0.5  # Limit reduces work

        # SELECT * penalty
        if 'SELECT *' in sql_upper:
            cost += 5.0

        return round(cost, 2)

    def _compute_complexity_score(self, parsed: ParsedQuery, sql: str = "") -> float:
        """Compute structural complexity score (lower is better).
        Uses ML model if available, falls back to rule-based.
        """
        # Try ML-based scoring first
        try:
            ml_service = get_ml_service()
            if ml_service.complexity_scorer.is_available():
                # Use natural language from SQL or generate a simple description
                text = sql  # Could be enhanced to use intent text
                return ml_service.score_complexity(text, {
                    'tables': [{'name': t.name} for t in parsed.tables],
                    'joins': [{'table': j.table.name} for j in parsed.joins],
                    'group_by': parsed.group_by,
                    'aggregations': parsed.aggregations,
                    'subqueries': parsed.subqueries,
                    'where_conditions': [{'column': c.column, 'operator': c.operator} for c in parsed.where_conditions],
                })
        except Exception as e:
            logger.warning(f"ML complexity scoring failed, falling back to rule-based: {e}")

        # Fallback to rule-based scoring
        score = 0.0

        # Base cost
        score += len(parsed.tables) * 1.0
        score += len(parsed.joins) * 2.0
        score += len(parsed.where_conditions) * 0.5
        score += len(parsed.group_by) * 1.0
        score += len(parsed.aggregations) * 1.5
        score += len(parsed.window_functions) * 2.0
        score += len(parsed.subqueries) * 3.0
        score += len(parsed.cte_names) * 1.0

        # Penalty for non-sargable predicates
        for cond in parsed.where_conditions:
            if self._is_non_sargable(cond):
                score += 2.0

        # Penalty for SELECT *
        if any(c.name == '*' for c in parsed.columns):
            score += 1.0

        # Penalty for missing LIMIT with ORDER BY
        if parsed.order_by and not parsed.limit:
            score += 1.5

        # Penalty for CROSS JOIN
        for join in parsed.joins:
            if join.type == 'CROSS':
                score += 3.0

        return round(score, 2)

    def _is_non_sargable(self, cond) -> bool:
        """Check if a WHERE condition is non-sargable."""
        non_sargable_funcs = {'LOWER', 'UPPER', 'TRIM', 'SUBSTR', 'SUBSTRING',
                              'YEAR', 'MONTH', 'DAY', 'DATE_TRUNC', 'TO_CHAR',
                              'CAST', 'CONVERT', 'COALESCE', 'NVL', 'ISNULL'}

        val_str = str(cond.value).upper()
        col_upper = cond.column.upper()

        for func in non_sargable_funcs:
            if f"{func}({col_upper}" in val_str or f"{func}( {col_upper}" in val_str:
                return True

        return False

    def _rank_candidates_layered(
        self,
        candidates: List[CandidateQuery],
        original_parsed: Optional[ParsedQuery],
        original_analysis: Optional[PlanAnalysis]
    ) -> List[CandidateQuery]:
        """
        Layered ranking model for candidate queries.

        Ranking layers (in order of priority):
        1. HARD CONSTRAINTS: Must pass schema validation AND semantic safety (not UNSAFE)
        2. PRIMARY PERFORMANCE: Relative cost from EXPLAIN (candidate_cost / baseline_cost)
        3. SECONDARY SIGNALS: Row estimation quality, scan efficiency, join strategy quality
        4. STABILITY: Deterministic plans, no volatile functions
        5. READABILITY (TIE-BREAKER): Complexity score, structural simplicity
        """
        def layered_rank_key(c: CandidateQuery) -> Tuple:
            # LAYER 1: Hard constraints - INVALID or UNSAFE go to bottom
            if not c.validation_passed:
                return (5, float('inf'), float('inf'), float('inf'), float('inf'), float('inf'))
            if c.semantic_safety == "unsafe":
                return (4, float('inf'), float('inf'), float('inf'), float('inf'), float('inf'))

            # LAYER 2: Primary Performance - relative cost ratio
            # Lower ratio = better (candidate cheaper than baseline)
            if c.performance_score is not None and c.performance_score > 0:
                # Invert so higher score = better (sort ascending with negative)
                perf_rank = -c.performance_score
            elif c.cost is not None and original_analysis and original_analysis.total_cost > 0:
                # Fallback: direct cost ratio
                cost_ratio = c.cost / original_analysis.total_cost
                perf_rank = cost_ratio  # Lower is better
            else:
                perf_rank = float('inf')

            # LAYER 3: Secondary Signals
            # 3a: Row estimation quality (fewer errors = better)
            est_error_rank = 0.0
            if c.plan_analysis:
                if c.plan_analysis.estimation_error_nodes > 0:
                    # Average error ratio - closer to 1.0 is better
                    avg_err = c.plan_analysis.avg_estimation_error
                    if avg_err > 0:
                        # Deviation from 1.0
                        est_error_rank = abs(1.0 - avg_err)
                    else:
                        est_error_rank = 1.0
                else:
                    est_error_rank = 0.0  # Perfect estimation
            else:
                est_error_rank = 1.0  # Unknown

            # 3b: Scan efficiency (index scans vs seq scans)
            scan_efficiency_rank = 0.0
            if c.plan_analysis:
                total_scans = (c.plan_analysis.seq_scans +
                               c.plan_analysis.index_scans +
                               c.plan_analysis.index_only_scans +
                               c.plan_analysis.bitmap_heap_scans)
                if total_scans > 0:
                    index_ratio = (c.plan_analysis.index_scans +
                                   c.plan_analysis.index_only_scans +
                                   c.plan_analysis.bitmap_heap_scans) / total_scans
                    # Higher index ratio = better (lower rank)
                    scan_efficiency_rank = 1.0 - index_ratio
                else:
                    scan_efficiency_rank = 0.5
            else:
                scan_efficiency_rank = 0.5

            # 3c: Join strategy quality (hash/merge > nested loop)
            join_quality_rank = 0.0
            if c.plan_analysis:
                total_joins = (c.plan_analysis.nested_loops +
                               c.plan_analysis.hash_joins +
                               c.plan_analysis.merge_joins)
                if total_joins > 0:
                    good_join_ratio = (c.plan_analysis.hash_joins +
                                       c.plan_analysis.merge_joins) / total_joins
                    join_quality_rank = 1.0 - good_join_ratio
                else:
                    join_quality_rank = 0.0
            else:
                join_quality_rank = 0.0

            secondary_rank = (est_error_rank + scan_efficiency_rank + join_quality_rank) / 3.0

            # LAYER 4: Stability
            # Prefer candidates with EXPLAIN ANALYZE data, deterministic plans
            stability_rank = 0.0
            if c.plan_analysis:
                if c.plan_analysis.explain_analyze:
                    stability_rank = 0.0  # Best - actual metrics available
                elif c.cost_source == "postgresql_explain":
                    stability_rank = 0.2  # Good - planner estimates
                else:
                    stability_rank = 0.5  # Heuristic - less reliable
            else:
                stability_rank = 1.0  # No plan analysis

            # LAYER 5: Readability / Complexity (tie-breaker)
            # Lower complexity = better (simpler queries are preferred)
            complexity_rank = 0.0
            if c.complexity_score is not None:
                complexity_rank = c.complexity_score / 100.0  # Normalize

            # Return tuple for lexicographic sorting (lower = better)
            return (
                0,  # Layer 1: passed hard constraints
                perf_rank,
                secondary_rank,
                stability_rank,
                complexity_rank,
            )

        return sorted(candidates, key=layered_rank_key)

    # --- Query rewrite helpers ---

    def _add_limit(self, sql: str, limit: int) -> str:
        """Add LIMIT clause to query."""
        # Simple approach: append LIMIT before semicolon
        sql = sql.rstrip().rstrip(';')
        if not re.search(r'\bLIMIT\s+\d+', sql, re.IGNORECASE):
            return f"{sql} LIMIT {limit}"
        return sql

    def _expand_star(self, sql: str, parsed: ParsedQuery) -> str:
        """Replace SELECT * with explicit column list."""
        if not parsed.tables:
            return sql

        # Get columns from schema for the first table
        table_name = parsed.tables[0].name.lower()
        if self.schema and table_name in self.schema.get('tables', {}):
            columns = list(self.schema['tables'][table_name].get('columns', {}).keys())
            if columns:
                col_list = ', '.join(columns)
                return re.sub(r'\bSELECT\s+\*', f'SELECT {col_list}', sql, flags=re.IGNORECASE)

        return sql

    def _rewrite_non_sargable(self, sql: str, parsed: ParsedQuery) -> str:
        """Rewrite non-sargable predicates."""
        # Pattern: WHERE LOWER(col) = 'value' -> WHERE col ILIKE 'value'
        rewritten = sql

        # Simple rewrite: LOWER(col) = 'val' -> col ILIKE 'val'
        rewritten = re.sub(
            r'\bLOWER\s*\(\s*(\w+)\s*\)\s*=\s*\'([^\']+)\'',
            r"\1 ILIKE '\2'",
            rewritten,
            flags=re.IGNORECASE
        )

        # UPPER(col) = 'VAL' -> col ILIKE 'val'
        rewritten = re.sub(
            r'\bUPPER\s*\(\s*(\w+)\s*\)\s*=\s*\'([^\']+)\'',
            r"\1 ILIKE '\2'",
            rewritten,
            flags=re.IGNORECASE
        )

        return rewritten

    def _rewrite_correlated_subquery(self, sql: str, parsed: ParsedQuery) -> str:
        """Attempt to rewrite correlated subquery as window function."""
        # This is a simplified heuristic - real implementation would be more complex
        # Look for patterns like: SELECT ... WHERE col IN (SELECT ... FROM ... WHERE outer.col = inner.col)
        # Replace with window function approach

        # For now, return original - would need full query rewrite engine
        return sql

    def _suggest_join_condition(self, sql: str, join: 'JoinInfo') -> str:
        """Suggest a JOIN condition based on foreign keys."""
        if not self.schema:
            return sql

        # Look for foreign key relationships
        for rel in self.schema.get('relationships', []):
            if (rel.get('from_table', '').lower() == join.table.name.lower() or
                rel.get('to_table', '').lower() == join.table.name.lower()):

                from_col = rel.get('from_column')
                to_col = rel.get('to_column')
                if from_col and to_col:
                    # Try to add ON condition
                    condition = f"{rel['from_table']}.{from_col} = {rel['to_table']}.{to_col}"
                    # This is a simplified approach - real implementation would parse and modify AST
                    pass

        return sql

    def _rewrite_in_subquery(self, sql: str, parsed: ParsedQuery) -> str:
        """Rewrite IN (SELECT ...) to EXISTS or JOIN."""
        # Pattern: WHERE col IN (SELECT col FROM table WHERE ...)
        # -> WHERE EXISTS (SELECT 1 FROM table WHERE table.col = outer.col AND ...)

        rewritten = re.sub(
            r'\b(\w+)\s+IN\s*\(\s*SELECT\s+(\w+)\s+FROM\s+(\w+)\s+WHERE\s+(.+?)\s*\)',
            r'EXISTS (SELECT 1 FROM \3 WHERE \3.\2 = \1 AND \4)',
            sql,
            flags=re.IGNORECASE | re.DOTALL
        )

        return rewritten

    def _push_down_predicates(self, sql: str, parsed: ParsedQuery) -> str:
        """Push WHERE predicates into CTEs."""
        # Simplified - would need full query rewrite
        return sql

    def _convert_to_cte(self, sql: str, parsed: ParsedQuery) -> str:
        """Convert nested subquery to CTE."""
        # Simplified - would need full query rewrite
        return sql

    def _build_join_clauses(self, intent: StructuredIntent, schema: Dict[str, Any]) -> List[str]:
        """
        Build JOIN clauses based on schema relationships and intent.

        If the intent involves multiple tables or a metric from a different table,
        add the necessary JOINs.
        """
        join_clauses = []

        if not schema or not schema.get('relationships'):
            return join_clauses

        entity = intent.entity
        if not entity or entity not in schema.get('tables', {}):
            return join_clauses

        # Get all columns from the entity table
        entity_cols = set(schema['tables'][entity].get('columns', {}).keys())

        # Check if metric is in a different table
        metric_table = None
        if intent.metric:
            for table_name, table_def in schema.get('tables', {}).items():
                if intent.metric in table_def.get('columns', {}):
                    metric_table = table_name
                    break

        # Check if group_by columns are in different tables
        group_by_tables = set()
        if intent.group_by:
            for gb in intent.group_by:
                for table_name, table_def in schema.get('tables', {}).items():
                    if gb in table_def.get('columns', {}):
                        group_by_tables.add(table_name)
                        break

        # Collect all tables needed
        needed_tables = {entity}
        if metric_table:
            needed_tables.add(metric_table)
        needed_tables.update(group_by_tables)

        # Also add join_tables from intent
        if intent.join_tables:
            needed_tables.update(intent.join_tables)

        # If we need tables beyond the entity, build JOINs using relationships
        if len(needed_tables) > 1:
            # Build a path from entity to each needed table
            relationships = schema.get('relationships', [])

            # Build adjacency list
            adj = {}
            self_relations = []  # Track self-referential relationships
            for rel in relationships:
                from_t = rel.get('from_table')
                to_t = rel.get('to_table')
                if from_t and to_t:
                    if from_t not in adj:
                        adj[from_t] = []
                    if to_t not in adj:
                        adj[to_t] = []
                    adj[from_t].append((to_t, rel))
                    adj[to_t].append((from_t, rel))
                    # Track self-referential relationships
                    if from_t == to_t:
                        self_relations.append(rel)

            # Find path from entity to each needed table using BFS
            for target in needed_tables:
                # Handle self-joins (e.g., employees_self for manager hierarchy)
                if target == entity + '_self':
                    # Find self-referential relationship for this entity
                    for rel in self_relations:
                        if rel.get('from_table') == entity:
                            from_c = rel.get('from_column')
                            to_c = rel.get('to_column')
                            # Self-join with alias
                            join_clauses.append(
                                f"JOIN {entity} mgr ON {entity}.{from_c} = mgr.{to_c}"
                            )
                    continue

                if target == entity:
                    continue

                # BFS to find path
                queue = [(entity, [])]
                visited = {entity}

                while queue:
                    current, path = queue.pop(0)
                    if current == target:
                        # Found path - build JOIN clauses
                        for step in path:
                            rel = step[1]
                            from_t = rel.get('from_table')
                            to_t = rel.get('to_table')
                            from_c = rel.get('from_column')
                            to_c = rel.get('to_column')

                            # Determine join type - use LEFT JOIN for tables in join_tables (user wants all from main)
                            join_type = "JOIN"
                            if to_t in intent.join_tables or from_t in intent.join_tables:
                                # If this table is explicitly requested by user as a join table, use LEFT JOIN
                                # to preserve all rows from the main entity
                                join_type = "LEFT JOIN"

                            # Determine join direction
                            if step[0] == from_t:
                                # from_t -> to_t
                                join_clauses.append(
                                    f"{join_type} {to_t} ON {from_t}.{from_c} = {to_t}.{to_c}"
                                )
                            else:
                                # to_t -> from_t
                                join_clauses.append(
                                    f"{join_type} {from_t} ON {to_t}.{to_c} = {from_t}.{from_c}"
                                )
                        break

                    for neighbor, rel in adj.get(current, []):
                        if neighbor not in visited:
                            visited.add(neighbor)
                            queue.append((neighbor, path + [(current, rel)]))

        return join_clauses

    def _dict_to_intent(self, d: Dict[str, Any]) -> StructuredIntent:
        """Convert dict to StructuredIntent."""
        # Handle case where category is a list (LLM might return multiple)
        category = d.get('category', 'RETRIEVE')
        if isinstance(category, list):
            # Prefer TOP_N > JOIN > AGGREGATE > others
            priority = ['TOP_N', 'JOIN', 'AGGREGATE', 'FILTER', 'GROUP', 'SORT', 'TREND', 'RANKING', 'DUPLICATE_DETECTION', 'COMPARISON', 'RETRIEVE', 'UNKNOWN']
            for cat in priority:
                if cat in category:
                    category = cat
                    break
            else:
                category = category[0]  # fallback to first

        return StructuredIntent(
            category=IntentCategory(category),
            operation=d.get('operation', 'SELECT'),
            entity=d.get('entity', ''),
            metric=d.get('metric'),
            filters=d.get('filters', []),
            time_range=d.get('time_range'),
            group_by=d.get('group_by', []),
            order_by=d.get('order_by', []),
            limit=d.get('limit'),
            ranking=d.get('ranking'),
            comparison=d.get('comparison'),
            join_tables=d.get('join_tables', []),
            select_columns=d.get('select_columns', []),
            raw_text=d.get('raw_text', ''),
        )

    # --- New helper methods for evidence-driven pipeline ---

    def _get_explain_plan(self, sql: str, analyze: bool = False) -> Optional[Dict[str, Any]]:
        """Get full EXPLAIN plan from PostgreSQL, optionally with ANALYZE.

        Safety: Only runs ANALYZE for SELECT/WITH queries (read-only).
        """
        if not self.seed_db:
            return None

        # Safety check: only run EXPLAIN ANALYZE on SELECT/WITH
        sql_upper = sql.strip().upper()
        if analyze:
            if not (sql_upper.startswith('SELECT') or sql_upper.startswith('WITH')):
                logger.warning(f"EXPLAIN ANALYZE requested but query is not SELECT/WITH: {sql[:100]}")
                analyze = False

        try:
            with self.seed_db.cursor() as cursor:
                if analyze:
                    cursor.execute(f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {sql}")
                else:
                    cursor.execute(f"EXPLAIN (FORMAT JSON) {sql}")
                result = cursor.fetchone()
                if result and result[0]:
                    return result[0]
        except Exception as e:
            logger.warning(f"EXPLAIN {'ANALYZE' if analyze else ''} failed: {e}")
        return None

    def _generate_rewrite_engine_candidates(self, query_structure: QueryStructure, original_sql: str, original_parsed: ParsedQuery) -> List[CandidateQuery]:
        """Generate candidates using the new RewriteEngine rules."""
        candidates = []

        # 1. Date function rewrites (YEAR/MONTH/DAY/DATE -> half-open ranges)
        date_rewrite = self.rewrite_engine.rewrite_date_function(original_sql, original_parsed, query_structure)
        if date_rewrite and date_rewrite != original_sql:
            candidates.append(CandidateQuery(
                sql=date_rewrite,
                description="Rewrote date function predicate to sargable half-open range",
                rewrite_rules_applied=['rewrite_date_function_predicate']
            ))

        # 2. Arithmetic on column rewrites (col + const = val -> col = val - const)
        arith_rewrite = self.rewrite_engine.rewrite_arithmetic_on_column(original_sql, original_parsed, query_structure)
        if arith_rewrite and arith_rewrite != original_sql:
            candidates.append(CandidateQuery(
                sql=arith_rewrite,
                description="Rewrote arithmetic on column to sargable form",
                rewrite_rules_applied=['rewrite_arithmetic_on_column']
            ))

        # 3. IN to EXISTS rewrite (with NOT NULL gate)
        in_exists_rewrite = self.rewrite_engine.rewrite_in_to_exists(original_sql, original_parsed, query_structure)
        if in_exists_rewrite and in_exists_rewrite != original_sql:
            candidates.append(CandidateQuery(
                sql=in_exists_rewrite,
                description="Rewrote IN subquery to EXISTS (NOT NULL verified)",
                rewrite_rules_applied=['rewrite_in_to_exists']
            ))

        # 4. DISTINCT removal (PK gate)
        distinct_rewrite = self.rewrite_engine.rewrite_remove_distinct(original_sql, original_parsed, query_structure)
        if distinct_rewrite and distinct_rewrite != original_sql:
            candidates.append(CandidateQuery(
                sql=distinct_rewrite,
                description="Removed redundant DISTINCT (primary key projected)",
                rewrite_rules_applied=['rewrite_remove_distinct']
            ))

        # 5. UNION to UNION ALL (advisory - requires provable disjointness)
        union_all_rewrite = self.rewrite_engine.rewrite_union_to_union_all(original_sql, original_parsed, query_structure)
        if union_all_rewrite and union_all_rewrite != original_sql:
            candidates.append(CandidateQuery(
                sql=union_all_rewrite,
                description="Rewrote UNION to UNION ALL (branches provably disjoint)",
                rewrite_rules_applied=['rewrite_union_to_union_all']
            ))

        # 6. CROSS JOIN with FK
        cross_join_rewrite = self.rewrite_engine.rewrite_cross_join(original_sql, original_parsed, query_structure)
        if cross_join_rewrite and cross_join_rewrite != original_sql:
            candidates.append(CandidateQuery(
                sql=cross_join_rewrite,
                description="Added FK-based join condition to CROSS JOIN",
                rewrite_rules_applied=['rewrite_cross_join']
            ))

        # 7. Filter pushdown before join
        filter_push_rewrite = self.rewrite_engine.rewrite_filter_before_join(original_sql, original_parsed, query_structure)
        if filter_push_rewrite and filter_push_rewrite != original_sql:
            candidates.append(CandidateQuery(
                sql=filter_push_rewrite,
                description="Pushed selective filter before join",
                rewrite_rules_applied=['rewrite_filter_before_join']
            ))

        # 8. Table alias qualification + CURRENT_YEAR placeholder resolution
        qualify_rewrite = self.rewrite_engine.rewrite_qualify_columns(original_sql, original_parsed, query_structure)
        if qualify_rewrite and qualify_rewrite != original_sql:
            candidates.append(CandidateQuery(
                sql=qualify_rewrite,
                description="Qualified unqualified columns with table aliases and resolved CURRENT_YEAR placeholder",
                rewrite_rules_applied=['qualify_columns']
            ))

        return candidates

    def _process_candidate_pipeline(
        self,
        candidate: CandidateQuery,
        original_parsed: ParsedQuery,
        original_analysis: Optional[PlanAnalysis],
        original_sql: str,
        query_structure: QueryStructure,
        all_opportunities: List[Dict[str, Any]]
    ) -> Optional[CandidateQuery]:
        """
        Process a candidate through the full validation and scoring pipeline.

        Steps:
        1. Parse candidate SQL
        2. Schema validation (hard constraint - fail fast)
        3. Semantic validation (hard constraint for UNSAFE rewrites)
        4. EXPLAIN plan extraction
        5. Plan analysis
        6. Performance scoring
        7. Confidence assessment (evidence-based)
        8. Attach opportunities, index/statistics recs, evidence_quality, semantic_risk
        """
        # Step 1: Parse candidate
        candidate_parsed = self.parser.parse(candidate.sql)
        if not candidate_parsed.is_valid:
            candidate.validation_passed = False
            candidate.validation_errors = [e.message for e in candidate_parsed.errors]
            return candidate

        # Step 2: Schema validation (hard constraint)
        if self.validator:
            validation = self.validator.validate(candidate_parsed)
            candidate.validation_passed = validation['is_valid']
            candidate.validation_errors = [
                i['message'] for i in validation['issues']
                if i['severity'] == 'error'
            ]
        else:
            candidate.validation_passed = candidate_parsed.is_valid
            candidate.validation_errors = [e.message for e in candidate_parsed.errors] if not candidate_parsed.is_valid else []

        if not candidate.validation_passed:
            return candidate

        # Step 3: Semantic validation (hard constraint for UNSAFE)
        rewrite_rules_applied = getattr(candidate, 'rewrite_rules_applied', [])
        semantic_result = self.semantic_validator.validate_candidate(
            original_sql, candidate.sql,
            original_parsed, candidate_parsed,
            rewrite_rules_applied
        )

        candidate.semantic_safety = semantic_result['semantic_safety']
        candidate.semantic_details = semantic_result['safety_details']
        candidate.semantic_risk = semantic_result.get('semantic_risk', 'LOW')

        # HARD CONSTRAINT: Reject UNSAFE rewrites
        if not semantic_result['semantically_valid']:
            candidate.validation_passed = False
            candidate.validation_errors.append(f"Semantic validation failed: {semantic_result['semantic_safety']}")
            return candidate

        # Step 4: EXPLAIN plan extraction
        candidate_explain = self._get_explain_plan(candidate.sql)

        # Step 5: Plan analysis
        if candidate_explain:
            candidate.plan_analysis = self.plan_analyzer.analyze(candidate_explain)
            candidate.cost = candidate.plan_analysis.total_cost
            candidate.startup_cost = candidate.plan_analysis.total_startup_cost
            candidate.plan_rows = candidate.plan_analysis.total_plan_rows
            candidate.cost_source = "postgresql_explain"
            candidate.evidence_quality = "EXPLAIN"
        else:
            # Fallback to heuristic cost
            candidate.cost = self._estimate_cost_from_structure(candidate.sql)
            candidate.cost_source = "heuristic"
            candidate.evidence_quality = "HEURISTIC"

        # Step 6: Performance scoring using candidate_ranker
        if original_analysis and candidate.plan_analysis:
            score_obj = compute_performance_score(
                original_analysis, candidate.plan_analysis,
                original_analysis.total_execution_time,
                candidate.plan_analysis.total_execution_time
            )
            candidate.performance_score = score_obj.total
            candidate.cost_change_percent = self._calculate_cost_change_percent(
                original_analysis, candidate.plan_analysis
            )
            candidate.performance_improvement = performance_improvement_pct(
                original_analysis, candidate.plan_analysis,
                original_analysis.total_execution_time,
                candidate.plan_analysis.total_execution_time
            )
            # Capture plan metrics
            candidate.plan_metrics = {
                'total_cost': candidate.plan_analysis.total_cost,
                'seq_scans': candidate.plan_analysis.seq_scans,
                'index_scans': candidate.plan_analysis.index_scans,
                'sorts': candidate.plan_analysis.sorts,
                'hash_joins': candidate.plan_analysis.hash_joins,
                'merge_joins': candidate.plan_analysis.merge_joins,
                'nested_loops': candidate.plan_analysis.nested_loops,
                'actual_rows': candidate.plan_analysis.total_actual_rows,
                'estimation_error': candidate.plan_analysis.symmetric_avg_error,
                'row_estimation_quality': candidate.plan_analysis.row_estimation_quality,
                'buffer_total': candidate.plan_analysis.buffer_total,
            }
            candidate.actual_execution_time = candidate.plan_analysis.total_execution_time
            candidate.planning_time = candidate.plan_analysis.planning_time
            candidate.execution_time = candidate.plan_analysis.total_execution_time
            candidate.row_estimation_quality = candidate.plan_analysis.row_estimation_quality
        else:
            candidate.performance_score = None
            candidate.cost_change_percent = None
            candidate.performance_improvement = None
            candidate.evidence_quality = "HEURISTIC"

        # Step 7: Complexity score (secondary signal)
        candidate.complexity_score = self._compute_complexity_score(candidate_parsed, candidate.sql)

        # Step 8: Generate optimization reasons
        candidate.optimization_reasons = self._generate_optimization_reasons(
            candidate, original_analysis, rewrite_rules_applied
        )

        # Step 9: Attach opportunities relevant to this candidate
        candidate.optimization_opportunities = all_opportunities

        # Step 10: Index and statistics recommendations (per-candidate)
        candidate.index_recommendations = []
        candidate.statistics_recommendations = []

        # Step 11: Evidence-based confidence assessment
        level, score = assess_confidence(
            candidate.to_dict(), candidate.plan_analysis, semantic_result
        )
        candidate.confidence_level = level
        candidate.confidence_score = score
        candidate.evidence_source = candidate.evidence_quality

        return candidate

    def _create_baseline_candidate(
        self,
        sql: str,
        original_parsed: ParsedQuery,
        original_analysis: Optional[PlanAnalysis],
        original_explain: Optional[Dict[str, Any]]
    ) -> CandidateQuery:
        """Create the baseline candidate (original query) with all enriched fields."""
        original_candidate = CandidateQuery(
            sql=sql,
            description="Original query (baseline)",
            cost=original_analysis.total_cost if original_analysis else None,
            startup_cost=original_analysis.total_startup_cost if original_analysis else None,
            plan_rows=original_analysis.total_plan_rows if original_analysis else None,
            plan_analysis=original_analysis,
            cost_source="postgresql_explain" if original_analysis else "heuristic",
            semantic_safety="safe",
            semantic_details=[],
            optimization_reasons=["Baseline query"],
            confidence="HIGH",
            performance_score=1.0,
            cost_change_percent=0.0,
            semantic_risk="LOW",
            evidence_quality="EXPLAIN_ANALYZE" if (original_explain and original_analysis and original_analysis.total_execution_time and original_analysis.total_execution_time > 0) else "EXPLAIN" if original_analysis else "HEURISTIC",
        )
        # Parse and validate original
        original_candidate.validation_passed = original_parsed.is_valid
        original_candidate.validation_errors = [e.message for e in original_parsed.errors] if not original_parsed.is_valid else []
        original_candidate.complexity_score = self._compute_complexity_score(original_parsed, sql)

        if original_analysis:
            original_candidate.plan_metrics = {
                'total_cost': original_analysis.total_cost,
                'seq_scans': original_analysis.seq_scans,
                'index_scans': original_analysis.index_scans,
                'sorts': original_analysis.sorts,
                'hash_joins': original_analysis.hash_joins,
                'merge_joins': original_analysis.merge_joins,
                'nested_loops': original_analysis.nested_loops,
                'actual_rows': original_analysis.total_actual_rows,
                'estimation_error': original_analysis.symmetric_avg_error,
                'row_estimation_quality': original_analysis.row_estimation_quality,
                'buffer_total': original_analysis.buffer_total,
            }
            original_candidate.actual_execution_time = original_analysis.total_execution_time
            original_candidate.planning_time = original_analysis.planning_time
            original_candidate.execution_time = original_analysis.total_execution_time
            original_candidate.row_estimation_quality = original_analysis.row_estimation_quality
            original_candidate.evidence_source = original_candidate.evidence_quality

        return original_candidate

    def _enrich_candidates(
        self,
        ranked_dicts: List[Dict[str, Any]],
        scored_candidates: List[CandidateQuery],
        query_structure: QueryStructure,
        all_opportunities: List[Dict[str, Any]],
        index_recommendations: List[IndexRecommendation],
        redundant_indexes: List[IndexRecommendation],
        stats_recommendations: List[StatisticsRecommendation],
        original_analysis: Optional[PlanAnalysis],
        original_time: Optional[float]
    ) -> List[CandidateQuery]:
        """Enrich ranked candidate dicts back into CandidateQuery objects with full metadata."""
        # Build a lookup from SQL to the original CandidateQuery object
        sql_to_candidate = {c.sql: c for c in scored_candidates}

        enriched = []
        for rd in ranked_dicts:
            sql = rd.get('sql', '')
            candidate = sql_to_candidate.get(sql)

            if candidate:
                # Update with ranking results
                candidate.performance_score = rd.get('performance_score', candidate.performance_score)
                candidate.confidence_level = rd.get('confidence_level', candidate.confidence_level)
                candidate.confidence_score = rd.get('confidence_score', candidate.confidence_score)
                candidate.evidence_source = rd.get('evidence_source', candidate.evidence_source)
                # Attach global index/statistics recommendations
                candidate.index_recommendations = [r.to_dict() for r in index_recommendations]
                candidate.statistics_recommendations = [r.to_dict() for r in stats_recommendations]
                candidate.optimization_opportunities = all_opportunities
                enriched.append(candidate)
            else:
                # Fallback: create from dict
                from .candidate_ranker import ConfidenceLevel
                c = CandidateQuery(
                    sql=sql,
                    description=rd.get('description', ''),
                    cost=rd.get('cost'),
                    performance_score=rd.get('performance_score'),
                    cost_change_percent=rd.get('cost_change_percent'),
                    plan_analysis=rd.get('plan_analysis'),
                    cost_source=rd.get('cost_source', 'heuristic'),
                    semantic_safety=rd.get('semantic_safety', 'unknown'),
                    semantic_details=rd.get('semantic_details', []),
                    optimization_reasons=rd.get('optimization_reasons', []),
                    confidence=rd.get('confidence', 'LOW'),
                    semantic_risk=rd.get('semantic_risk', 'LOW'),
                    evidence_quality=rd.get('evidence_quality', 'HEURISTIC'),
                    actual_execution_time=rd.get('actual_execution_time'),
                    planning_time=rd.get('planning_time'),
                    execution_time=rd.get('execution_time'),
                    row_estimation_quality=rd.get('row_estimation_quality'),
                    performance_improvement=rd.get('performance_improvement'),
                    evidence_source=rd.get('evidence_source', 'HEURISTIC'),
                    confidence_level=rd.get('confidence_level', 'LOW'),
                    confidence_score=rd.get('confidence_score', 0.0),
                    rewrite_rules_applied=rd.get('rewrite_rules_applied', []),
                )
                c.optimization_opportunities = all_opportunities
                c.index_recommendations = [r.to_dict() for r in index_recommendations]
                c.statistics_recommendations = [r.to_dict() for r in stats_recommendations]
                enriched.append(c)

        return enriched

    def _extract_sql_optimizations(self, candidates: List[CandidateQuery]) -> List[Dict[str, Any]]:
        """Extract SQL optimization candidates for top-level response."""
        optimizations = []
        for c in candidates:
            if c.sql and c.description and c != candidates[0]:  # Skip baseline
                optimizations.append({
                    'sql': c.sql,
                    'description': c.description,
                    'estimated_cost': c.cost,
                    'performance_score': c.performance_score,
                    'cost_change_percent': c.cost_change_percent,
                    'rewrite_rules_applied': c.rewrite_rules_applied,
                    'semantic_risk': c.semantic_risk,
                    'evidence_quality': c.evidence_quality,
                    'confidence_level': c.confidence_level,
                })
        return optimizations

    def _generate_warnings(self, candidates: List[CandidateQuery], opportunities: List[Any]) -> List[str]:
        """Generate warnings from candidates and opportunities."""
        warnings = []

        # Check for UNSAFE candidates that were rejected
        for c in candidates:
            if not c.validation_passed and c.semantic_safety == 'unsafe':
                warnings.append(f"Rejected unsafe rewrite: {c.description}")
            elif c.semantic_risk == 'HIGH':
                warnings.append(f"High semantic risk: {c.description}")

        # Check for advisory opportunities
        for opp in opportunities:
            severity = getattr(opp, 'severity', None)
            evidence = getattr(opp, 'evidence', '')
            if severity == 'LOW' and 'advisory' in evidence.lower():
                warnings.append(f"Advisory: {evidence}")

        return warnings

    def optimize(self, sql: str, enable_actual_execution: Optional[bool] = None) -> Dict[str, Any]:
        """
        Optimize a SQL query by generating and ranking candidates.

        Args:
            sql: Original SQL query
            enable_actual_execution: Override instance setting for EXPLAIN ANALYZE

        Returns:
            Dict with original query, cost, and ranked candidates
        """
        # Try Calcite first if enabled
        if self.use_calcite and self.calcite_client and self.calcite_client.is_available():
            try:
                return self._optimize_with_calcite(sql)
            except Exception as e:
                logger.warning(f"Calcite optimization failed, falling back to built-in: {e}")

        # Fallback to built-in optimization
        return self._optimize_builtin(sql, enable_actual_execution)