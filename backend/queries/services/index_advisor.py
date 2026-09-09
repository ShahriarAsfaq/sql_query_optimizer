"""
Index Advisor — produces CREATE INDEX recommendations and detects redundant indexes.

Recommendations are advisory only: never auto-create or auto-drop. All output is
tagged as INDEX_RECOMMENDATION (create) or INDEX_ADVISORY (redundant detection).
"""
import logging
from typing import Dict, List, Any, Optional, Tuple, Set
from dataclasses import dataclass, field

from .query_analyzer import (
    QueryStructure,
    get_column_type,
    get_table_indexes,
    get_table_primary_key,
    _table_defs,
)

logger = logging.getLogger(__name__)


@dataclass
class IndexRecommendation:
    """A recommended index or advisory."""
    type: str  # INDEX_RECOMMENDATION | INDEX_ADVISORY
    sql: str
    table: str
    columns: List[str]
    reason: str
    confidence: float
    evidence: str
    # Optional structured purpose: WHERE_EQUALITY | RANGE_LOOKUP | JOIN |
    # GROUP_BY | ORDER_BY | TOP_N_COVERING | COMPOSITE. Additive — omitted from
    # to_dict() when unset so existing consumers are unaffected.
    purpose: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        result = {
            'type': self.type,
            'sql': self.sql,
            'table': self.table,
            'columns': self.columns,
            'reason': self.reason,
            'confidence': self.confidence,
            'evidence': self.evidence,
        }
        if self.purpose:
            result['purpose'] = self.purpose
        return result


def recommend_indexes(
    query: QueryStructure,
    schema: Dict[str, Any],
    baseline_plan: Optional[Any] = None,
) -> List[IndexRecommendation]:
    """
    Generate index recommendations from query structure.

    Args:
        query: Deep query structure from analyze_query_structure.
        schema: Database schema with table columns, PKs, and existing indexes.
        baseline_plan: Optional PlanAnalysis to check for existing index scans.

    Returns:
        List of IndexRecommendation (type=INDEX_RECOMMENDATION).
    """
    tables = _table_defs(schema)
    recs: List[IndexRecommendation] = []
    existing_idx = _existing_indexes_by_table(tables)

    # Resolve table for unqualified columns: if single table in query, use it.
    single_table = query.tables[0] if len(query.tables) == 1 else None

    # Columns that would benefit from an index, collected from query structure.
    # Priority order: equality -> range -> join -> group by -> order by.
    equality_cols: List[Tuple[str, str]] = []  # (table, column)
    range_cols: List[Tuple[str, str]] = []
    join_cols: List[Tuple[str, str]] = []
    group_cols: List[Tuple[str, str]] = []
    order_cols: List[Tuple[str, str]] = []

    def resolve_table(t: Optional[str]) -> Optional[str]:
        return t if t else single_table

    # WHERE equality predicates
    for pred in query.where.equalities:
        tbl = resolve_table(pred.table)
        if tbl and pred.column:
            equality_cols.append((tbl, pred.column))

    # WHERE range predicates
    for pred in query.where.ranges:
        tbl = resolve_table(pred.table)
        if tbl and pred.column:
            range_cols.append((tbl, pred.column))

    # JOIN conditions — qualify columns to their real table. The raw JoinStructure
    # keeps only bare left/right column names (e.g. `d.id = e.department_id`
    # -> left='id', right='department_id', table='departments') with no table
    # qualifiers, so we resolve each side against the schema: whichever side the
    # joined table actually has is its join key; the *other* side is the probe
    # (FK) column on the driving table — the one worth indexing.
    for j in query.joins:
        if not j.table:
            continue
        tbl_col, other_col, other_table = _qualify_join_columns(j, tables)
        if tbl_col is None and other_col is None:
            continue
        # Prefer indexing a non-PK probe side (e.g. employees.department_id).
        # The joined table's own key is usually its PK — already indexed.
        if other_table and other_col and not _covered_by_pk_or_index(
                tables, other_table, other_col, existing_idx):
            join_cols.append((other_table, other_col))
        elif tbl_col and not _covered_by_pk_or_index(
                tables, j.table, tbl_col, existing_idx):
            join_cols.append((j.table, tbl_col))

    # GROUP BY (columns are plain strings, possibly qualified as tbl.col)
    for col in query.group_by.columns:
        qual_table, qual_col = _split_qualified(col)
        tbl = resolve_table(qual_table or None)
        if tbl and qual_col:
            group_cols.append((tbl, qual_col))

    # ORDER BY
    for col in query.order_by.columns:
        qual_table, qual_col = _split_qualified(col)
        tbl = resolve_table(qual_table or None)
        if tbl and qual_col:
            order_cols.append((tbl, qual_col))

    # Build candidate column lists per table, preserving priority.
    candidates: Dict[str, List[str]] = {}  # table -> ordered list of columns

    def add_candidate(table: str, col: str, priority: int):
        if table not in tables:
            return
        if table not in candidates:
            candidates[table] = []
        if col not in candidates[table]:
            candidates[table].insert(min(priority, len(candidates[table])), col)

    for t, c in equality_cols:
        add_candidate(t, c, 0)
    for t, c in range_cols:
        add_candidate(t, c, 1)
    for t, c in join_cols:
        add_candidate(t, c, 2)
    for t, c in group_cols:
        add_candidate(t, c, 3)
    for t, c in order_cols:
        add_candidate(t, c, 4)

    # Generate a recommendation per table.
    for table, cols in candidates.items():
        if not cols:
            continue
        # Skip if an equivalent or prefix index already exists.
        if _covered_by_existing(table, cols, existing_idx):
            continue
        # Skip if table has a PK on the first column (PK implies index).
        pk = get_table_primary_key(tables, table)
        if pk and pk[0].lower() == cols[0].lower():
            continue
        # Skip columns with very low selectivity (e.g., boolean).
        if _is_low_selectivity(tables, table, cols[0]):
            continue

        # Build CREATE INDEX SQL.
        quoted_cols = ', '.join(f'"{c}"' for c in cols)
        sql = f'CREATE INDEX ON "{table}" ({quoted_cols});'

        # Evidence and confidence.
        where_cols = [c for t, c in equality_cols if t == table]
        range_c = [c for t, c in range_cols if t == table]
        join_c = [c for t, c in join_cols if t == table]
        group_c = [c for t, c in group_cols if t == table]
        order_c = [c for t, c in order_cols if t == table]
        parts = []
        if where_cols:
            parts.append(f'WHERE equality on {", ".join(where_cols)}')
        if range_c:
            parts.append(f'WHERE range on {", ".join(range_c)}')
        if join_c:
            parts.append(f'JOIN on {", ".join(join_c)}')
        if group_c:
            parts.append(f'GROUP BY on {", ".join(group_c)}')
        if order_c:
            parts.append(f'ORDER BY on {", ".join(order_c)}')
        evidence = f"Query filters/joins on {table} via: {'; '.join(parts)}."
        reason = f"Index would support {'/'.join(parts)}."

        # Confidence higher when we have equality predicates, lower for pure ORDER BY.
        conf = 0.7
        if where_cols:
            conf = 0.85
        elif range_c:
            conf = 0.75
        elif join_c:
            conf = 0.7

        # Structured purpose (additive): most-specific origin wins. TOP_N_COVERING
        # when the index includes the ORDER BY columns and the query has a LIMIT.
        purpose = 'COMPOSITE' if len(cols) > 1 else None
        if where_cols:
            purpose = 'WHERE_EQUALITY'
        elif range_c:
            purpose = 'RANGE_LOOKUP'
        elif join_c:
            purpose = 'JOIN'
        elif group_c:
            purpose = 'GROUP_BY'
        elif order_c:
            if query.limit_offset.limit:
                purpose = 'TOP_N_COVERING'
            else:
                purpose = 'ORDER_BY'

        recs.append(IndexRecommendation(
            type='INDEX_RECOMMENDATION',
            sql=sql,
            table=table,
            columns=cols,
            reason=reason,
            confidence=conf,
            evidence=evidence,
            purpose=purpose,
        ))

    return recs


def detect_redundant_indexes(schema: Dict[str, Any]) -> List[IndexRecommendation]:
    """
    Detect duplicate or prefix-redundant indexes.

    Returns advisories (INDEX_ADVISORY) — never auto-drop.
    """
    tables = _table_defs(schema)
    advisories: List[IndexRecommendation] = []

    for table_name, tdef in tables.items():
        idxs = tdef.get('indexes', [])
        if len(idxs) < 2:
            continue

        # Normalize: list of (columns_tuple, unique, name).
        norm = []
        for idx in idxs:
            cols = tuple(c.lower() for c in idx.get('columns', []))
            if cols:
                norm.append((cols, idx.get('unique', False), idx.get('name')))

        # Check each pair for redundancy.
        for i, (cols_i, uniq_i, name_i) in enumerate(norm):
            for j, (cols_j, uniq_j, name_j) in enumerate(norm):
                if i >= j:
                    continue
                # Exact duplicate: same columns, same order, same uniqueness.
                if cols_i == cols_j:
                    advisories.append(IndexRecommendation(
                        type='INDEX_ADVISORY',
                        sql=f'-- DUPLICATE INDEX: {name_i} and {name_j} on {table_name} have identical columns ({", ".join(cols_i)}). Consider dropping one.',
                        table=table_name,
                        columns=list(cols_i),
                        reason='Duplicate index detected.',
                        confidence=0.95,
                        evidence=f'Indexes {name_i} and {name_j} both cover ({", ".join(cols_i)}).',
                    ))
                # Prefix redundancy: i is a prefix of j and both non-unique
                # (or i is unique and j is not used for FK).
                elif len(cols_i) < len(cols_j) and cols_i == cols_j[:len(cols_i)]:
                    # If both non-unique, i makes j redundant for prefix scans.
                    if not uniq_i and not uniq_j:
                        advisories.append(IndexRecommendation(
                            type='INDEX_ADVISORY',
                            sql=f'-- PREFIX REDUNDANT: {name_j} on {table_name} ({", ".join(cols_j)}) has prefix {name_i} ({", ".join(cols_i)}). Consider dropping {name_j}.',
                            table=table_name,
                            columns=list(cols_j),
                            reason='Prefix-redundant index.',
                            confidence=0.7,
                            evidence=f'{name_j} covers ({", ".join(cols_j)}) which has prefix ({", ".join(cols_i)}) already indexed by {name_i}.',
                        ))

    return advisories


def index_advice_to_dicts(recs: List[IndexRecommendation]) -> List[Dict[str, Any]]:
    """Serialize recommendations/advisories."""
    return [r.to_dict() for r in recs]


def _split_qualified(col: str) -> Tuple[Optional[str], str]:
    """Split 'tbl.col' into (table, column). Unqualified -> (None, col)."""
    parts = str(col).split('.')
    if len(parts) == 2 and parts[0] and parts[1]:
        return parts[0].strip().strip('"'), parts[1].strip().strip('"')
    return None, str(col).strip().strip('"')


def _table_has_column(tables: Dict[str, Any], table: str, column: str) -> bool:
    """True when a schema-declared table has the (case-insensitive) column."""
    tdef = tables.get(str(table).lower())
    if not tdef:
        return False
    cols = tdef.get('columns', {}) if isinstance(tdef, dict) else {}
    return any(str(c).lower() == str(column).lower() for c in cols)


def _qualify_join_columns(
    j,
    tables: Dict[str, Any],
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Resolve a join's bare columns to (tbl_col, other_col, other_table).

    For `d.id = e.department_id` (table='departments', left='id', right=
    'department_id'): departments owns 'id', so tbl_col='id'; the other side
    belongs to whatever table declares it — here employees -> other_col=
    'department_id' (if unique in the schema). Returns (None, None, None) when
    the pairing cannot be decided from the schema.
    """
    tbl, left, right = j.table, j.left_column, j.right_column
    if not tbl or not left or not right:
        return None, None, None
    if _table_has_column(tables, tbl, left):
        tbl_col, other_col = left, right
    elif _table_has_column(tables, tbl, right):
        tbl_col, other_col = right, left
    else:
        return None, None, None  # schema doesn't know the joined table's cols
    # Find the owning table for the other column (schema-honoring, unambiguous).
    owners = [t for t in tables if _table_has_column(tables, t, other_col)]
    other_table = owners[0] if len(owners) == 1 else None
    if other_table and other_table.lower() == str(tbl).lower():
        other_table = None  # same table — self-join; skip the extra candidate
    return tbl_col, other_col, other_table


def _covered_by_pk_or_index(
    tables: Dict[str, Any],
    table: str,
    column: str,
    existing: Dict[str, List[Tuple[str, ...]]],
) -> bool:
    """True when a PK or existing index already covers the column as a prefix."""
    if _covered_by_existing(table, [column], existing):
        return True
    pk = get_table_primary_key(tables, table)
    return any(str(c).lower() == str(column).lower() for c in pk)


# --- Helpers ---------------------------------------------------------------

def _existing_indexes_by_table(tables: Dict[str, Any]) -> Dict[str, List[Tuple[str, ...]]]:
    """Return {table: [tuple_of_columns]} for existing indexes."""
    out = {}
    for tname, tdef in tables.items():
        out[tname] = []
        for idx in tdef.get('indexes', []):
            cols = tuple(c.lower() for c in idx.get('columns', []))
            if cols:
                out[tname].append(cols)
    return out


def _covered_by_existing(
    table: str,
    cols: List[str],
    existing: Dict[str, List[Tuple[str, ...]]],
) -> bool:
    """True if an existing index covers all `cols` as a prefix (order matters)."""
    col_seq = tuple(c.lower() for c in cols)
    for existing_cols in existing.get(table, []):
        if len(existing_cols) >= len(col_seq):
            if existing_cols[:len(col_seq)] == col_seq:
                return True
    return False


def _is_low_selectivity(tables: Dict[str, Any], table: str, column: str) -> bool:
    """Heuristic: boolean/enum-like columns with very low n_distinct."""
    tdef = tables.get(table, {})
    col_def = tdef.get('columns', {}).get(column.lower())
    if isinstance(col_def, dict):
        col_type = str(col_def.get('type', '')).lower()
        if col_type in ('boolean', 'bool'):
            return True
        # n_distinct in pg_stats negative means approx unique; positive small value
        # means low cardinality. Not available here; we just avoid boolean.
    return False