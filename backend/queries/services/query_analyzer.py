"""
Deep SQL Query Structure Analyzer.

Re-parses raw SQL with sqlglot to extract a rich, boolean-structure-preserving
representation of the query (the existing ``ParsedQuery.where_conditions`` flattens
AND/OR and loses function-on-column / date-extraction predicates). This module is
the foundation for opportunity detection, rewrite decisions, and heuristic cost.

All other optimizer modules import the shared schema helpers and the
``row_estimation_error`` helper from here.
"""
import logging
import sqlglot
from sqlglot import exp
from typing import Dict, List, Any, Optional, Set, Tuple
from dataclasses import dataclass, field

from .sql_parser import ParsedQuery

logger = logging.getLogger(__name__)

# Functions that operate directly on a column and are non-sargable.
FUNCTION_ON_COLUMN_FUNCS = {
    'LOWER', 'UPPER', 'TRIM', 'BTRIM', 'LTRIM', 'RTRIM', 'SUBSTR', 'SUBSTRING',
    'CAST', 'CONVERT', 'COALESCE', 'NVL', 'ISNULL', 'YEAR', 'MONTH', 'DAY',
    'DATE', 'DATE_TRUNC', 'TO_CHAR', 'TO_DATE', 'EXTRACT', 'ABS', 'LENGTH',
    'CHAR_LENGTH', 'LEFT', 'RIGHT', 'REPLACE', 'CONCAT',
}

# Date/time extraction functions that can be rewritten to range predicates.
DATE_EXTRACTION_FUNCS = {'YEAR', 'MONTH', 'DAY', 'DATE', 'DATE_TRUNC'}

# Arithmetic operators invertible on numeric columns (no overflow-guarantee issue
# for the pure rewrite to a constant, which the caller verifies separately).
INVERTIBLE_ARITHMETIC = {'+', '-', '*'}

# Operators for comparison.
COMPARISON_OPS = {
    'EQ', 'NEQ', 'LT', 'LTE', 'GT', 'GTE',
}

# --- Shared schema helpers -------------------------------------------------


def _table_defs(schema: Dict[str, Any]) -> Dict[str, Any]:
    """Return {lower_name: table_def} for the schema, tolerating missing tables."""
    if not schema:
        return {}
    return {
        str(k).lower(): v for k, v in schema.get('tables', {}).items()
    }


def get_column_type(schema: Dict[str, Any], table: str, column: str) -> Optional[str]:
    """Return a column's type (lowercased string) or None."""
    tables = _table_defs(schema)
    tdef = tables.get(str(table).lower())
    if not tdef:
        return None
    col_def = tdef.get('columns', {}).get(str(column).lower())
    if col_def is None:
        # Fall back to case-sensitive lookup
        for k, v in tdef.get('columns', {}).items():
            if str(k).lower() == str(column).lower():
                col_def = v
                break
    if isinstance(col_def, dict):
        return str(col_def.get('type', '')).lower() or None
    if isinstance(col_def, str):
        return col_def.lower()
    return None


def column_is_not_null(schema: Dict[str, Any], table: str, column: str) -> bool:
    """Return True if the schema marks the column NOT NULL (or it is a PK)."""
    tables = _table_defs(schema)
    tdef = tables.get(str(table).lower())
    if not tdef:
        return False
    col_def = None
    for k, v in tdef.get('columns', {}).items():
        if str(k).lower() == str(column).lower():
            col_def = v
            break
    if col_def is not None:
        if isinstance(col_def, dict):
            if col_def.get('primary_key'):
                return True
            if col_def.get('not_null'):
                return True
            nullable = col_def.get('nullable')
            if nullable is False:
                return True
    pk = tdef.get('primary_key') or []
    if str(column).lower() in [str(p).lower() for p in pk]:
        return True
    return False


def get_table_primary_key(schema: Dict[str, Any], table: str) -> List[str]:
    """Return the primary key columns (lowercased) for a table, if declared."""
    tables = _table_defs(schema)
    tdef = tables.get(str(table).lower())
    if not tdef:
        return []
    pk = tdef.get('primary_key') or []
    # Some schemas mark PK via column-level primary_key flags
    if not pk:
        for k, v in tdef.get('columns', {}).items():
            if isinstance(v, dict) and v.get('primary_key'):
                pk.append(str(k).lower())
    return [str(p).lower() for p in pk]


def get_table_indexes(schema: Dict[str, Any], table: str) -> List[Dict[str, Any]]:
    """Return declared indexes for a table as a list of {name, columns, unique}."""
    tables = _table_defs(schema)
    tdef = tables.get(str(table).lower())
    if not tdef:
        return []
    indexes = tdef.get('indexes') or []
    result = []
    for idx in indexes:
        if isinstance(idx, dict):
            cols = idx.get('columns') or []
            result.append({
                'name': idx.get('name'),
                'columns': [str(c).lower() for c in cols],
                'unique': bool(idx.get('unique', False)),
            })
        elif isinstance(idx, str):
            # tolerate "index: col1, col2" style string entries
            result.append({
                'name': None,
                'columns': [c.strip().lower() for c in idx.split(',') if c.strip()],
                'unique': False,
            })
    return result


def find_relationship(schema: Dict[str, Any], table_a: str, table_b: str) -> Optional[Dict[str, Any]]:
    """Return a relationship dict linking table_a and table_b (either direction), if any."""
    a = str(table_a).lower()
    b = str(table_b).lower()
    for rel in (schema or {}).get('relationships', []):
        f = str(rel.get('from_table', '')).lower()
        t = str(rel.get('to_table', '')).lower()
        if (f == a and t == b) or (f == b and t == a):
            return rel
    return None


def row_estimation_error(actual: Any, estimated: Any) -> float:
    """
    Symmetric row-estimation error: max(actual/estimated, estimated/actual).

    Values near 1.0 indicate an accurate estimate. Returns 0.0 when both are
    zero/absent. Handles zero denominators conservatively.
    """
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


def estimation_quality_class(error: float, thresholds: Optional[Dict[str, float]] = None) -> str:
    """
    Classify a symmetric row-estimation error into GOOD/MODERATE/POOR/SEVERE.

    thresholds (optional): {'good': <2, 'moderate': <5, 'poor': <10}.
    """
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


# --- Data structures -------------------------------------------------------


@dataclass
class SelectStructure:
    """Analysis of the SELECT projection."""
    has_star: bool = False
    column_count: int = 0
    duplicate_expressions: List[str] = field(default_factory=list)
    expression_columns: List[str] = field(default_factory=list)
    function_columns: List[str] = field(default_factory=list)
    aggregates: List[str] = field(default_factory=list)
    window_functions: List[str] = field(default_factory=list)


@dataclass
class PredicateInfo:
    """A single predicate occurrence, possibly with non-sargable detail."""
    column: str
    operator: str
    value: Any = None
    table: Optional[str] = None
    is_subquery: bool = False
    leading_wildcard: bool = False


@dataclass
class WhereStructure:
    """Boolean-structure-preserving representation of the WHERE clause."""
    equalities: List[PredicateInfo] = field(default_factory=list)
    ranges: List[PredicateInfo] = field(default_factory=list)
    like_conditions: List[PredicateInfo] = field(default_factory=list)
    or_groups: List[List[str]] = field(default_factory=list)
    and_count: int = 0
    in_conditions: List[PredicateInfo] = field(default_factory=list)
    exists_conditions: List[str] = field(default_factory=list)
    is_null_conditions: List[PredicateInfo] = field(default_factory=list)
    is_not_null_conditions: List[PredicateInfo] = field(default_factory=list)
    function_on_column: List[Dict[str, Any]] = field(default_factory=list)
    arithmetic_on_column: List[Dict[str, Any]] = field(default_factory=list)
    date_extractions: List[Dict[str, Any]] = field(default_factory=list)
    leading_wildcards: List[PredicateInfo] = field(default_factory=list)


@dataclass
class JoinStructure:
    """A single JOIN with column-level detail."""
    type: str = 'INNER'
    table: str = ''
    condition: Optional[str] = None
    left_column: Optional[str] = None
    right_column: Optional[str] = None
    expression_on_join_col: bool = False
    missing_condition: bool = False


@dataclass
class OrderByStructure:
    """ORDER BY analysis."""
    columns: List[str] = field(default_factory=list)
    directions: List[str] = field(default_factory=list)
    has_expression: bool = False
    has_limit: bool = False


@dataclass
class GroupByStructure:
    """GROUP BY analysis."""
    columns: List[str] = field(default_factory=list)
    has_having: bool = False
    aggregation_type: str = 'none'  # none | simple | hashed


@dataclass
class DistinctStructure:
    """DISTINCT analysis."""
    distinct: bool = False
    distinct_on: List[str] = field(default_factory=list)
    redundant_candidate: bool = False


@dataclass
class SetOpStructure:
    """SET operation analysis (UNION/INTERSECT/EXCEPT)."""
    kind: Optional[str] = None
    branch_count: int = 0
    branches: List[str] = field(default_factory=list)


@dataclass
class LimitOffsetStructure:
    """LIMIT/OFFSET analysis."""
    limit: Optional[int] = None
    offset: Optional[int] = None
    order_by_with_limit: bool = False
    large_offset: bool = False


@dataclass
class QueryStructure:
    """Complete structural analysis of a SQL query."""
    operation_type: str = 'SELECT'
    raw_sql: str = ''
    tables: List[str] = field(default_factory=list)
    aliases: Dict[str, str] = field(default_factory=dict)
    select: SelectStructure = field(default_factory=SelectStructure)
    where: WhereStructure = field(default_factory=WhereStructure)
    joins: List[JoinStructure] = field(default_factory=list)
    order_by: OrderByStructure = field(default_factory=OrderByStructure)
    group_by: GroupByStructure = field(default_factory=GroupByStructure)
    distinct: DistinctStructure = field(default_factory=DistinctStructure)
    set_ops: SetOpStructure = field(default_factory=SetOpStructure)
    limit_offset: LimitOffsetStructure = field(default_factory=LimitOffsetStructure)
    subqueries: List[str] = field(default_factory=list)
    cte_names: List[str] = field(default_factory=list)
    is_valid: bool = True

    def to_dict(self) -> Dict[str, Any]:
        """Serializable representation (used for debugging/UI)."""
        return {
            'operation_type': self.operation_type,
            'tables': self.tables,
            'aliases': self.aliases,
            'select': {
                'has_star': self.select.has_star,
                'column_count': self.select.column_count,
                'duplicate_expressions': self.select.duplicate_expressions,
                'expression_columns': self.select.expression_columns,
                'function_columns': self.select.function_columns,
                'aggregates': self.select.aggregates,
                'window_functions': self.select.window_functions,
            },
            'where': {
                'equalities': [p.__dict__ for p in self.where.equalities],
                'ranges': [p.__dict__ for p in self.where.ranges],
                'like_conditions': [p.__dict__ for p in self.where.like_conditions],
                'or_groups': self.where.or_groups,
                'and_count': self.where.and_count,
                'in_conditions': [p.__dict__ for p in self.where.in_conditions],
                'exists_conditions': self.where.exists_conditions,
                'is_null_conditions': [p.__dict__ for p in self.where.is_null_conditions],
                'is_not_null_conditions': [p.__dict__ for p in self.where.is_not_null_conditions],
                'function_on_column': self.where.function_on_column,
                'arithmetic_on_column': self.where.arithmetic_on_column,
                'date_extractions': self.where.date_extractions,
                'leading_wildcards': [p.__dict__ for p in self.where.leading_wildcards],
            },
            'joins': [
                {
                    'type': j.type, 'table': j.table, 'condition': j.condition,
                    'left_column': j.left_column, 'right_column': j.right_column,
                    'expression_on_join_col': j.expression_on_join_col,
                    'missing_condition': j.missing_condition,
                } for j in self.joins
            ],
            'order_by': {
                'columns': self.order_by.columns,
                'directions': self.order_by.directions,
                'has_expression': self.order_by.has_expression,
                'has_limit': self.order_by.has_limit,
            },
            'group_by': {
                'columns': self.group_by.columns,
                'has_having': self.group_by.has_having,
                'aggregation_type': self.group_by.aggregation_type,
            },
            'distinct': {
                'distinct': self.distinct.distinct,
                'distinct_on': self.distinct.distinct_on,
                'redundant_candidate': self.distinct.redundant_candidate,
            },
            'set_ops': {
                'kind': self.set_ops.kind,
                'branch_count': self.set_ops.branch_count,
                'branches': self.set_ops.branches,
            },
            'limit_offset': {
                'limit': self.limit_offset.limit,
                'offset': self.limit_offset.offset,
                'order_by_with_limit': self.limit_offset.order_by_with_limit,
                'large_offset': self.limit_offset.large_offset,
            },
            'subqueries': self.subqueries,
            'cte_names': self.cte_names,
            'is_valid': self.is_valid,
        }


# --- Analyzer --------------------------------------------------------------


def analyze_query_structure(
    parsed: ParsedQuery,
    raw_sql: str = '',
    schema: Optional[Dict[str, Any]] = None,
    large_offset_threshold: int = 1000,
) -> QueryStructure:
    """
    Build a ``QueryStructure`` from a parsed query (and the raw SQL).

    Re-parses ``raw_sql`` (falling back to ``parsed.raw_sql``) with sqlglot to
    preserve boolean structure that ``ParsedQuery`` flattens.
    """
    sql = raw_sql or parsed.raw_sql
    structure = QueryStructure(
        operation_type=parsed.operation_type,
        raw_sql=sql,
        tables=[t.name for t in parsed.tables],
        aliases={t.alias.lower(): t.name.lower() for t in parsed.tables if t.alias},
        subqueries=list(parsed.subqueries),
        cte_names=list(parsed.cte_names),
        is_valid=parsed.is_valid,
    )

    if not parsed.is_valid:
        return structure

    try:
        ast = sqlglot.parse_one(sql, dialect='postgres')
    except Exception:
        logger.warning('query_analyzer: could not re-parse SQL', exc_info=True)
        return structure

    # Determine select node and set-op root
    select_node = None
    set_op_root = None
    if isinstance(ast, exp.Select):
        select_node = ast
    elif isinstance(ast, (exp.Union, exp.Intersect, exp.Except)):
        set_op_root = ast
        select_node = ast.find(exp.Select)
    elif isinstance(ast, exp.With):
        # WITH ... SELECT / WITH ... UNION
        set_op_root = ast.this if isinstance(ast.this, (exp.Union, exp.Intersect, exp.Except)) else None
        select_node = ast.find(exp.Select)

    if select_node is not None:
        structure.select = _analyze_select(select_node)

    if set_op_root is not None or isinstance(ast, (exp.Union, exp.Intersect, exp.Except)):
        structure.set_ops = _analyze_set_ops(ast if set_op_root is None else set_op_root)

    # WHERE
    where_node = _find_where(ast)
    if where_node is not None:
        structure.where = _analyze_where(where_node, structure)

    # JOINs
    structure.joins = _analyze_joins(ast, structure)

    # ORDER BY
    structure.order_by = _analyze_order_by(ast)

    # GROUP BY / HAVING
    structure.group_by = _analyze_group_by(ast)

    # DISTINCT / DISTINCT ON
    structure.distinct = _analyze_distinct(select_node, structure, schema)

    # LIMIT / OFFSET
    structure.limit_offset = _analyze_limit_offset(ast, large_offset_threshold)

    return structure


def _find_where(ast: exp.Expression) -> Optional[exp.Expression]:
    """Return the first WHERE clause expression (predicate body)."""
    where = ast.find(exp.Where)
    if where is None:
        return None
    return where.this


def _is_bare_column(expr: exp.Expression) -> bool:
    return isinstance(expr, exp.Column)


def _analyze_select(select_node: exp.Expression) -> SelectStructure:
    sel = SelectStructure()
    expressions = list(getattr(select_node, 'expressions', []) or [])
    seen = set()
    for e in expressions:
        if isinstance(e, exp.Star):
            sel.has_star = True
            continue
        if isinstance(e, exp.Alias):
            e = e.this
        sql_str = e.sql(dialect='postgres')
        if sql_str in seen:
            sel.duplicate_expressions.append(sql_str)
        seen.add(sql_str)

        # Aggregates / window functions in projection
        for agg in e.find_all((exp.Count, exp.Sum, exp.Avg, exp.Min, exp.Max,
                               exp.CountIf, exp.Stddev, exp.Variance)):
            sel.aggregates.append(agg.sql(dialect='postgres'))
        for w in e.find_all(exp.Window):
            sel.window_functions.append(w.sql(dialect='postgres'))

        # Function/expression columns (non-simple projections)
        is_simple = isinstance(e, exp.Column) and not e.find(exp.Func)
        if not is_simple:
            sel.expression_columns.append(sql_str)
        if e.find(exp.Func):
            sel.function_columns.append(sql_str)

    sel.column_count = len([e for e in expressions if not isinstance(e, exp.Star)])
    return sel


def _analyze_where(where_node: exp.Expression, structure: QueryStructure) -> WhereStructure:
    wh = WhereStructure()

    # Walk predicates collecting per-class info.
    _classify_predicates(where_node, wh, structure)

    # Non-sargable detection across the whole clause.
    _detect_non_sargable(where_node, wh)

    # AND/OR structure
    _collect_boolean_structure(where_node, wh)

    return wh


def _classify_predicates(expr: exp.Expression, wh: WhereStructure, structure: QueryStructure):
    """Classify top-level AND-separated predicates by boolean class."""
    # Handle OR specially (collect as a group, but still descend for classes)
    if isinstance(expr, exp.Or):
        return

    if isinstance(expr, exp.And):
        _classify_predicates(expr.left, wh, structure)
        _classify_predicates(expr.right, wh, structure)
        return

    if isinstance(expr, exp.Paren):
        _classify_predicates(expr.this, wh, structure)
        return

    if isinstance(expr, exp.Not):
        _classify_predicates(expr.this, wh, structure)
        return

    # ---- comparisons ----
    if isinstance(expr, (exp.EQ, exp.NEQ)):
        pi = _build_predicate(expr)
        if pi:
            wh.equalities.append(pi)
        return
    if isinstance(expr, (exp.LT, exp.LTE, exp.GT, exp.GTE, exp.Between)):
        pi = _build_predicate(expr)
        if pi:
            wh.ranges.append(pi)
        return
    if isinstance(expr, (exp.Like, exp.ILike)):
        pi = _build_predicate(expr)
        if pi:
            wh.like_conditions.append(pi)
            if pi.leading_wildcard:
                wh.leading_wildcards.append(pi)
        return
    if isinstance(expr, exp.In):
        pi = _build_in_predicate(expr, structure)
        if pi:
            wh.in_conditions.append(pi)
        return
    if isinstance(expr, exp.Exists):
        wh.exists_conditions.append(expr.sql(dialect='postgres'))
        return
    if isinstance(expr, exp.Is):
        pi = _build_predicate(expr)
        if pi:
            if pi.operator == 'IS NULL':
                wh.is_null_conditions.append(pi)
            elif pi.operator == 'IS NOT NULL':
                wh.is_not_null_conditions.append(pi)


def _build_predicate(expr: exp.Expression) -> Optional[PredicateInfo]:
    """Build a PredicateInfo for a simple binary/range/like/is comparison."""
    if isinstance(expr, exp.Between):
        col = expr.this
        if not _is_bare_column(col):
            return None
        return PredicateInfo(
            column=col.name, table=col.table or None,
            operator='BETWEEN', value=f"{expr.args.get('low')} AND {expr.args.get('high')}",
        )

    left, right = getattr(expr, 'left', None), getattr(expr, 'right', None)

    if isinstance(expr, (exp.Like, exp.ILike)):
        col = expr.this
        if not _is_bare_column(col):
            return None
        op = 'ILIKE' if isinstance(expr, exp.ILike) else 'LIKE'
        value = None
        pattern_expr = expr.expression
        if isinstance(pattern_expr, exp.Literal):
            value = pattern_expr.this
        leading = False
        if isinstance(value, str) and value:
            leading = value[0] in ('%', '_')
        return PredicateInfo(
            column=col.name, table=col.table or None, operator=op,
            value=value, leading_wildcard=leading,
        )

    if isinstance(expr, exp.Is):
        col = left if _is_bare_column(left) else None
        if col is None and _is_bare_column(right):
            col = right
        if col is None:
            return None
        is_null = isinstance(right, exp.Null) or (isinstance(expr, exp.Is) and isinstance(expr.expression, exp.Null))
        # sqlglot represents IS NULL as Is(this=col, expression=Null) or IsNull()
        op = 'IS NULL' if is_null else 'IS NOT NULL'
        return PredicateInfo(column=col.name, table=col.table or None, operator=op, value=None)

    if _is_bare_column(left) and not _is_function(right):
        return PredicateInfo(
            column=left.name, table=left.table or None,
            operator=expr.__class__.__name__.upper(),
            value=_extract_literal(right),
        )
    if _is_bare_column(right) and not _is_function(left):
        return PredicateInfo(
            column=right.name, table=right.table or None,
            operator=_reverse_op(expr.__class__.__name__.upper()),
            value=_extract_literal(left),
        )
    return None


def _is_function(expr: Any) -> bool:
    return isinstance(expr, exp.Func) or isinstance(expr, exp.Binary)


def _extract_literal(expr: Any) -> Any:
    if isinstance(expr, exp.Literal):
        return expr.this
    if isinstance(expr, exp.Paren):
        return _extract_literal(expr.this)
    if isinstance(expr, exp.Column):
        return f"COL:{expr.name}"
    if isinstance(expr, exp.Null):
        return None
    return expr.sql(dialect='postgres')


def _reverse_op(op: str) -> str:
    rev = {'LT': 'GT', 'LTE': 'GTE', 'GT': 'LT', 'GTE': 'LTE', 'EQ': 'EQ', 'NEQ': 'NEQ'}
    return rev.get(op, op)


def _build_in_predicate(expr: exp.In, structure: QueryStructure) -> Optional[PredicateInfo]:
    col = expr.this
    if not _is_bare_column(col):
        return None
    is_subquery = expr.args.get('query') is not None or bool(expr.find(exp.Select))
    return PredicateInfo(
        column=col.name, table=col.table or None, operator='IN',
        value=expr.sql(dialect='postgres'), is_subquery=is_subquery,
    )


def _detect_non_sargable(expr: exp.Expression, wh: WhereStructure):
    """Detect function-on-column and arithmetic-on-column predicates."""
    # Function-on-column: any func whose arg is a bare column.
    for func in expr.find_all(exp.Func):
        name = func.__class__.__name__.upper()
        if name in ('ANONYMOUS', 'FUNC'):
            name = str(getattr(func, 'name', '') or '').upper()
        if name not in FUNCTION_ON_COLUMN_FUNCS:
            continue
        for arg in func.args.get('this') and [func.args.get('this')] or func.expressions or []:
            if isinstance(arg, exp.Column):
                rec = {'column': arg.name, 'table': arg.table or None,
                       'func': name, 'arg': arg.sql(dialect='postgres')}
                wh.function_on_column.append(rec)
                if name in DATE_EXTRACTION_FUNCS:
                    # Try to find the literal value if this func is in a comparison
                    value = _extract_comparison_value(func, expr)
                    if value is not None:
                        rec['value'] = value
                    wh.date_extractions.append(rec)
                break

    # Arithmetic-on-column: binary +/-/* where one operand is a bare column.
    for binary in expr.find_all(exp.Binary):
        op = binary.__class__.__name__
        if op not in ('Add', 'Sub', 'Mul', 'Div'):
            continue
        left, right = binary.left, binary.right
        if _is_bare_column(left) and _is_numeric_literal(right):
            wh.arithmetic_on_column.append({
                'column': left.name, 'table': left.table or None,
                'op': _arith_symbol(op), 'value': right.this,
            })
        elif _is_bare_column(right) and _is_numeric_literal(left):
            wh.arithmetic_on_column.append({
                'column': right.name, 'table': right.table or None,
                'op': _arith_symbol(op), 'value': left.this,
            })


def _arith_symbol(op: str) -> str:
    return {'Add': '+', 'Sub': '-', 'Mul': '*', 'Div': '/'}.get(op, op)


def _is_numeric_literal(expr: Any) -> bool:
    return isinstance(expr, exp.Literal) and expr.is_number


def _extract_comparison_value(func: exp.Expression, root: exp.Expression) -> Any:
    """Find the literal value that this func is being compared to."""
    # Walk up to find the parent comparison
    parent = func.find_ancestor((exp.EQ, exp.NEQ, exp.LT, exp.LTE, exp.GT, exp.GTE))
    if parent:
        if parent.left is func:
            return parent.right.this if isinstance(parent.right, exp.Literal) else None
        elif parent.right is func:
            return parent.left.this if isinstance(parent.left, exp.Literal) else None
    return None


def _collect_boolean_structure(expr: exp.Expression, wh: WhereStructure):
    """Count AND conjuncts and collect OR groups."""
    def count_and(e):
        if isinstance(e, exp.And):
            return count_and(e.left) + count_and(e.right)
        return 1

    wh.and_count = count_and(expr)

    def collect_or_groups(e):
        if isinstance(e, exp.Or):
            collect_or_groups(e.left)
            collect_or_groups(e.right)
            return
        if isinstance(e, exp.And):
            collect_or_groups(e.left)
            collect_or_groups(e.right)
            return
        if isinstance(e, exp.Paren):
            collect_or_groups(e.this)
            return
        wh.or_groups.append([e.sql(dialect='postgres')])

    collect_or_groups(expr)


def _analyze_joins(ast: exp.Expression, structure: QueryStructure) -> List[JoinStructure]:
    joins = []
    for join in ast.find_all(exp.Join):
        kind = (join.kind or 'INNER').upper() if join.kind else 'INNER'
        side = (join.side or '').upper()
        if side:
            kind = side
        table = join.this
        table_name = table.name if isinstance(table, exp.Table) else str(table)

        js = JoinStructure(type=kind, table=table_name)
        on = join.args.get('on')
        if on:
            js.condition = on.sql(dialect='postgres')
            lcol, rcol = _extract_join_columns(on)
            js.left_column, js.right_column = lcol, rcol
            js.expression_on_join_col = _join_has_expression(on)
        else:
            js.missing_condition = (kind != 'CROSS')
        joins.append(js)
    return joins


def _extract_join_columns(on: exp.Expression) -> Tuple[Optional[str], Optional[str]]:
    """Extract (left_column, right_column) from a simple equality join condition."""
    if isinstance(on, exp.Paren):
        on = on.this
    if isinstance(on, exp.EQ):
        left, right = on.left, on.right
        if _is_bare_column(left) and _is_bare_column(right):
            return left.name, right.name
    return None, None


def _join_has_expression(on: exp.Expression) -> bool:
    if isinstance(on, exp.Paren):
        on = on.this
    if isinstance(on, exp.EQ):
        if _is_bare_column(on.left) and _is_bare_column(on.right):
            return False
        return True
    return True


def _analyze_order_by(ast: exp.Expression) -> OrderByStructure:
    ob = OrderByStructure()
    order = ast.find(exp.Order)
    if order is None:
        return ob
    for e in order.expressions:
        desc = False
        expr = e
        if isinstance(expr, exp.Ordered):
            desc = bool(expr.args.get('desc', False))
            expr = expr.this
        ob.columns.append(expr.sql(dialect='postgres'))
        ob.directions.append('DESC' if desc else 'ASC')
        if not _is_bare_column(expr):
            ob.has_expression = True
    limit = ast.find(exp.Limit)
    ob.has_limit = limit is not None
    return ob


def _analyze_group_by(ast: exp.Expression) -> GroupByStructure:
    gb = GroupByStructure()
    group = ast.find(exp.Group)
    if group is not None:
        gb.columns = [e.sql(dialect='postgres') for e in group.expressions]
        gb.aggregation_type = 'hashed'
    having = ast.find(exp.Having)
    gb.has_having = having is not None
    # Aggregate type from plan node (HashAggregate vs GroupAggregate) is
    # determined later from the EXPLAIN plan; here we only mark that aggregation exists.
    return gb


def _analyze_distinct(select_node: exp.Expression, structure: QueryStructure,
                      schema: Optional[Dict[str, Any]]) -> DistinctStructure:
    ds = DistinctStructure()
    if select_node is None:
        return ds
    # DISTINCT ON
    distinct_on = select_node.args.get('distinct')
    if distinct_on is not None:
        # exp.Distinct has .expressions, exp.DistinctOn too
        on_cols = getattr(distinct_on, 'expressions', None)
        if on_cols:
            ds.distinct_on = [c.sql(dialect='postgres') for c in on_cols]
        ds.distinct = True
    # Plain DISTINCT: Select has is_distinct flag or 'distinct' key set
    if select_node.args.get('distinct') is not None or getattr(select_node, 'is_distinct', False):
        ds.distinct = True

    if ds.distinct and structure.tables:
        # Redundant-candidate check: single table whose selected columns include
        # the primary key (with no joins that could multiply rows).
        table = structure.tables[0]
        pk = get_table_primary_key(schema or {}, table)
        if len(structure.joins) == 0 and not structure.set_ops.kind and pk:
            selected = [c for c in structure.select.expression_columns] or \
                       [c for c in [c for c in _plain_selected(select_node)]]
            selected_lower = {str(c).split('.')[-1].lower() for c in selected}
            if any(str(p).lower() in selected_lower for p in pk):
                ds.redundant_candidate = True
    return ds


def _plain_selected(select_node: exp.Expression) -> List[str]:
    result = []
    for e in select_node.expressions or []:
        if isinstance(e, exp.Star):
            continue
        if isinstance(e, exp.Alias):
            e = e.this
        if _is_bare_column(e):
            result.append(e.name)
    return result


def _analyze_set_ops(ast: exp.Expression) -> SetOpStructure:
    so = SetOpStructure()
    if isinstance(ast, exp.Union):
        so.kind = 'UNION ALL' if ast.args.get('distinct') is False else 'UNION'
    elif isinstance(ast, exp.Intersect):
        so.kind = 'INTERSECT'
    elif isinstance(ast, exp.Except):
        so.kind = 'EXCEPT'
    else:
        return so

    # Count branches
    stack = [ast]
    branches = []
    while stack:
        node = stack.pop()
        if isinstance(node, (exp.Union, exp.Intersect, exp.Except)):
            stack.append(node.args.get('this'))
            stack.append(node.args.get('expression'))
        else:
            branches.append(node.sql(dialect='postgres'))
    so.branches = branches
    so.branch_count = len(branches)
    return so


def _analyze_limit_offset(ast: exp.Expression, large_offset_threshold: int) -> LimitOffsetStructure:
    lo = LimitOffsetStructure()
    limit = ast.find(exp.Limit)
    if limit is not None:
        try:
            lo.limit = int(limit.expression.this)
        except (ValueError, AttributeError):
            lo.limit = None
    offset = ast.find(exp.Offset)
    if offset is not None:
        try:
            lo.offset = int(offset.expression.this)
        except (ValueError, AttributeError):
            lo.offset = None
    order = ast.find(exp.Order)
    lo.order_by_with_limit = order is not None and lo.limit is not None
    if lo.offset is not None:
        lo.large_offset = lo.offset >= large_offset_threshold
    return lo
