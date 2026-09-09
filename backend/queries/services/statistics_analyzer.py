"""
Statistics Analyzer — inspects pg_stats / pg_class / pg_stat_user_tables via the
seed connection to detect stale statistics, poor row estimates, and recommend ANALYZE.

Only active when a seed_db_connection is provided. Never treats a bad estimate as
proof the SQL is bad — it is a statistics signal, not a SQL quality signal.
"""
import logging
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class StatisticsRecommendation:
    """An ANALYZE or statistics-related recommendation."""
    action: str  # e.g., 'ANALYZE table_name'
    reason: str
    table: Optional[str] = None
    confidence: float = 0.5
    evidence: str = ''

    def to_dict(self) -> Dict[str, Any]:
        return {
            'action': self.action,
            'reason': self.reason,
            'table': self.table,
            'confidence': self.confidence,
            'evidence': self.evidence,
        }


def analyze_statistics(
    sql: str,
    parsed: Optional[Any],
    seed_conn: Any,
    plan: Optional[Any] = None,
    explain_analyze: Optional[Dict[str, Any]] = None,
    target_table: Optional[str] = None,
    schema: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], List[StatisticsRecommendation]]:
    """
    Analyze table statistics and row estimation quality.

    Args:
        sql: Original query SQL.
        parsed: ParsedQuery (unused, kept for signature compatibility).
        seed_conn: Django database connection to seed DB.
        plan: PlanAnalysis from EXPLAIN (FORMAT JSON) without ANALYZE.
        explain_analyze: Parsed EXPLAIN ANALYZE JSON (if available).
        target_table: Optional specific table to check.
        schema: Optional user-provided schema. When present, statistics are only
            analyzed for tables declared in that schema (the user's structure is
            authoritative — tables outside it are never recommended).

    Returns:
        (opportunities, recommendations) where opportunities are dicts for
        the opportunity detector format, and recommendations are
        StatisticsRecommendation objects for the final response.
    """
    opportunities: List[Dict[str, Any]] = []
    recommendations: List[StatisticsRecommendation] = []

    if seed_conn is None:
        return opportunities, recommendations

    try:
        tables_to_check = _tables_from_query(sql, target_table, schema)
        with seed_conn.cursor() as cur:
            for table in tables_to_check:
                _check_table_stats(cur, table, plan, explain_analyze,
                                  opportunities, recommendations)
    except Exception:
        logger.warning('Statistics analysis failed', exc_info=True)

    return opportunities, recommendations


def _tables_from_query(sql: str, target_table: Optional[str],
                       schema: Optional[Dict[str, Any]] = None) -> List[str]:
    """Extract table names from the query (simple heuristic).

    When a schema is provided, the result is intersected with the schema's
    declared tables so we never recommend ANALYZE (or report statistics) for a
    table the user did not declare in their schema.
    """
    if target_table:
        return [target_table]
    import re
    # Very naive: look for FROM <table> and JOIN <table>
    tables = set()
    for m in re.finditer(r'\b(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_]*)', sql, re.IGNORECASE):
        tables.add(m.group(1).lower())
    if schema and schema.get('tables'):
        declared = {str(t).lower() for t in schema['tables'].keys()}
        tables = tables & declared
    return list(tables)


def _check_table_stats(
    cur: Any,
    table: str,
    plan: Optional[Any],
    explain_analyze: Optional[Dict[str, Any]],
    opportunities: List[Dict[str, Any]],
    recommendations: List[StatisticsRecommendation],
):
    """Check pg_class, pg_stats, pg_stat_user_tables for a single table."""
    # 1. pg_class: reltuples, relpages, last_vacuum/analyze not directly there.
    cur.execute("""
        SELECT reltuples, relpages, relhasindex
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relname = %s AND n.nspname = 'public'
        LIMIT 1
    """, [table])
    row = cur.fetchone()
    if not row:
        return
    reltuples, relpages, has_index = row

    # 2. pg_stat_user_tables: last_analyze, last_autoanalyze, n_live_tup, n_dead_tup
    cur.execute("""
        SELECT last_analyze, last_autoanalyze, n_live_tup, n_dead_tup
        FROM pg_stat_user_tables
        WHERE relname = %s
        LIMIT 1
    """, [table])
    stat_row = cur.fetchone()
    last_analyze = stat_row[0] if stat_row else None
    last_autoanalyze = stat_row[1] if stat_row else None
    n_live = stat_row[2] if stat_row else None
    n_dead = stat_row[3] if stat_row else None

    # 3. pg_stats: column-level stats (sampled)
    cur.execute("""
        SELECT attname, null_frac, n_distinct, most_common_vals, most_common_freqs
        FROM pg_stats
        WHERE tablename = %s AND schemaname = 'public'
    """, [table])
    pg_stats_rows = cur.fetchall()

    # --- Stale statistics detection ---
    from datetime import datetime, timedelta
    now = datetime.now()
    stale_threshold = timedelta(days=7)

    last_analyzed = last_analyze or last_autoanalyze
    if last_analyzed is None:
        opportunities.append({
            'type': 'STALE_STATISTICS',
            'severity': 'MEDIUM',
            'table': table,
            'columns': [],
            'evidence': f"Table {table} has never been analyzed (no pg_stat_user_tables entry).",
            'confidence': 0.8,
        })
        recommendations.append(StatisticsRecommendation(
            action=f'ANALYZE {table}',
            reason='Table has never been analyzed; planner has no statistics.',
            table=table,
            confidence=0.8,
            evidence='No last_analyze timestamp found.',
        ))
    elif now - last_analyzed.replace(tzinfo=None) > stale_threshold:
        opportunities.append({
            'type': 'STALE_STATISTICS',
            'severity': 'LOW',
            'table': table,
            'columns': [],
            'evidence': f"Table {table} last analyzed on {last_analyzed} (>{stale_threshold.days} days ago).",
            'confidence': 0.6,
        })
        recommendations.append(StatisticsRecommendation(
            action=f'ANALYZE {table}',
            reason=f'Statistics are stale (last analyzed {last_analyzed}).',
            table=table,
            confidence=0.6,
            evidence=f'Last analyze: {last_analyzed}.',
        ))

    # --- Row count mismatch (pg_class.reltuples vs actual live tuples) ---
    if n_live is not None and reltuples > 0:
        ratio = n_live / reltuples if reltuples else 0
        if ratio > 2.0 or ratio < 0.5:
            opportunities.append({
                'type': 'STALE_STATISTICS',
                'severity': 'MEDIUM',
                'table': table,
                'columns': [],
                'evidence': f"pg_class.reltuples ({reltuples:.0f}) differs from live tuples ({n_live:.0f}) by {ratio:.1f}x.",
                'confidence': 0.7,
            })
            recommendations.append(StatisticsRecommendation(
                action=f'ANALYZE {table}',
                reason=f'Planner row estimate ({reltuples:.0f}) vs actual live rows ({n_live:.0f}) mismatch.',
                table=table,
                confidence=0.7,
                evidence=f'reltuples={reltuples:.0f}, n_live_tup={n_live:.0f}.',
            ))

    # --- Dead tuple bloat ---
    if n_live is not None and n_dead is not None and n_live > 0:
        dead_ratio = n_dead / n_live
        if dead_ratio > 0.2:
            opportunities.append({
                'type': 'STALE_STATISTICS',
                'severity': 'LOW',
                'table': table,
                'columns': [],
                'evidence': f"Table {table} has {dead_ratio:.1%} dead tuples; consider VACUUM ANALYZE.",
                'confidence': 0.5,
            })

    # --- EXPLAIN ANALYZE row estimation error ---
    if explain_analyze is not None:
        _check_plan_estimates(cur, table, explain_analyze, opportunities, recommendations)


def _check_plan_estimates(
    cur: Any,
    table: str,
    explain_analyze: Dict[str, Any],
    opportunities: List[Dict[str, Any]],
    recommendations: List[StatisticsRecommendation],
):
    """Walk the EXPLAIN ANALYZE plan and find nodes scanning this table."""
    # Walk the plan tree to find nodes referencing this table.
    def walk(node):
        if not isinstance(node, dict):
            return
        # Check if this node scans our table
        rel_name = node.get('Relation Name') or node.get('Relation')
        if rel_name and rel_name.lower() == table.lower():
            actual = node.get('Actual Rows') or node.get('Actual Rows') or node.get('Actual Rows')
            planned = node.get('Plan Rows') or node.get('Plan Rows')
            if actual is not None and planned is not None and planned > 0:
                ratio = actual / planned
                if ratio > 5.0 or ratio < 0.2:
                    quality = 'SEVERE' if ratio > 10 or ratio < 0.1 else 'POOR'
                    opportunities.append({
                        'type': 'POOR_ROW_ESTIMATION',
                        'severity': quality,
                        'table': table,
                        'columns': [],
                        'evidence': f"Row estimate error for {table}: planned={planned:.0f}, actual={actual:.0f} ({ratio:.1f}x).",
                        'confidence': 0.8,
                    })
                    recommendations.append(StatisticsRecommendation(
                        action=f'ANALYZE {table}',
                        reason=f'Row estimation error {ratio:.1f}x; statistics may be stale or distribution changed.',
                        table=table,
                        confidence=0.8,
                        evidence=f'planned={planned:.0f}, actual={actual:.0f}.',
                    ))
        # Recurse
        for k in ('Plans', 'plan', 'children'):
            if k in node:
                for child in node[k]:
                    walk(child)

    walk(explain_analyze)