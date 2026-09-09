"""
Conservative SQL Rewrite Engine.

Generates new semantic-gated SQL candidates. Every rewrite is conservative:
if semantic equivalence cannot be proven from query structure + schema, no SQL
is produced (the opportunity detector emits an advisory instead).

Pre-existing optimizer rules are NOT duplicated here; this engine adds the new
rules (date function->range, arithmetic sargability, IN->EXISTS, DISTINCT
removal, UNION->UNION ALL when provably disjoint, CROSS JOIN with FK,
filter-before-join). All produced candidates carry ``rewrite_rules_applied``
names so ``SemanticValidator.validate_candidate`` gates them downstream.
"""
import re
import logging
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple

import sqlglot
from sqlglot import exp
from sqlglot.dialects.postgres import Postgres

from .sql_parser import ParsedQuery, get_parser
from .query_analyzer import (
    QueryStructure,
    get_column_type,
    column_is_not_null,
    find_relationship,
    get_table_primary_key,
)

logger = logging.getLogger(__name__)

# Functions that can be rewritten to a range predicate on a *date/timestamp*
# column (never timestamptz without an explicit, known session TZ).
REWRITABLE_DATE_FUNCS = {'YEAR', 'MONTH', 'DAY', 'DATE'}

# Column types safe for arithmetic sargability rewrite (unbounded precision ->
# no overflow semantics change in PostgreSQL). Integer types are excluded
# because addition on an out-of-range column value changes from "overflow error"
# to "no rows", i.e. semantics differ.
ARITHMETIC_SAFE_TYPES = {'numeric', 'decimal'}


class _PostgresNoAsGenerator(Postgres.Generator):
    """Postgres generator that renders table aliases without the ``AS`` keyword
    (``student s`` instead of ``student AS s``). Used only by the
    qualify-columns rewrite so its output matches the canonical form users write."""

    def table_sql(self, expression: exp.Table, sep: str = ' ') -> str:
        return super().table_sql(expression, sep=sep)


def _postgres_no_as_generate(ast: exp.Expression) -> str:
    """Render an AST to SQL using the no-``AS``-table-alias Postgres generator."""
    return _PostgresNoAsGenerator().generate(ast)


class RewriteEngine:
    """Generates conservative, gated SQL rewrite candidates."""

    def __init__(self, schema: Optional[Dict[str, Any]] = None):
        self.schema = schema or {}
        self.parser = get_parser()

    # --- Public interface ---------------------------------------------------

    def generate(
        self,
        original_sql: str,
        parsed: ParsedQuery,
        structure: Optional[QueryStructure] = None,
    ) -> List[Dict[str, Any]]:
        """
        Generate candidate rewrites.

        Returns a list of dicts: {sql, description, rewrite_rules_applied}.
        """
        if structure is None:
            structure = self._build_structure(parsed, original_sql)

        candidates: List[Dict[str, Any]] = []
        # Maintain a stable generation order.
        rule_generators = [
            ('date_function_to_range', self.rewrite_date_function,
             'Rewrote date-function predicate to a half-open range predicate'),
            ('arithmetic_sargability', self.rewrite_arithmetic_on_column,
             'Rewrote arithmetic on column to a sargable constant comparison'),
            ('rewrite_in_subquery', self.rewrite_in_to_exists,
             'Rewrote IN (subquery) to EXISTS for better performance'),
            ('remove_distinct', self.rewrite_remove_distinct,
             'Removed provably-unnecessary DISTINCT'),
            ('union_to_union_all', self.rewrite_union_to_union_all,
             'Rewrote provably-disjoint UNION to UNION ALL'),
            ('add_join_condition', self.rewrite_cross_join,
             'Replaced CROSS JOIN with FK-based INNER JOIN'),
            ('predicate_pushdown', self.rewrite_filter_before_join,
             'Pushed selective filter before INNER JOIN'),
            ('qualify_columns', self.rewrite_qualify_columns,
             'Added table aliases, qualified unqualified column references, resolved CURRENT_YEAR placeholder'),
        ]

        for rule_name, method, description in rule_generators:
            try:
                new_sql = method(original_sql, parsed, structure)
            except Exception:
                logger.warning('Rewrite rule %s failed', rule_name, exc_info=True)
                new_sql = None
            if new_sql and new_sql != original_sql:
                # Normalize whitespace/formatting difference check
                candidates.append({
                    'sql': new_sql,
                    'description': description,
                    'rewrite_rules_applied': [rule_name],
                })

        # Deduplicate identical SQL (guarded by a plain dict key).
        seen = set()
        uniq = []
        for c in candidates:
            key = c['sql'].strip().lower()
            if key not in seen:
                seen.add(key)
                uniq.append(c)
        return uniq

    # --- Date function -> range --------------------------------------------

    def rewrite_date_function(self, sql: str, parsed: ParsedQuery,
                              structure: QueryStructure) -> Optional[str]:
        """YEAR(col) = 2025 -> col >= '2025-01-01' AND col < '2026-01-01'."""
        if not structure.where.date_extractions:
            return None
        try:
            ast = sqlglot.parse_one(sql, dialect='postgres')
        except Exception:
            return None
        where = ast.find(exp.Where)
        if where is None:
            return None

        new_body = self._rewrite_date_tree(where.this, structure)
        if new_body is None:
            return None
        # Attach the replacement back into the WHERE clause.
        where.set('this', new_body)
        # Compare emitted SQL to detect a real change.
        new_sql = ast.sql(dialect='postgres')
        if new_sql.strip() == sql.strip():
            return None
        return new_sql

    def _rewrite_date_tree(self, node: exp.Expression,
                           structure: QueryStructure) -> Optional[exp.Expression]:
        """Recurse over the WHERE tree, returning a modified node or None."""
        if node is None:
            return None

        # Do NOT descend into NOT: NOT(YEAR(col)=2025) is NOT equivalent to
        # NOT(range predicate). Skip rewriting under NOT.
        if isinstance(node, exp.Not):
            return None if _node_has_date_func(node) else node

        if isinstance(node, exp.Paren):
            inner = self._rewrite_date_tree(node.this, structure)
            if inner is not None and inner is not node.this:
                node.set('this', inner)
                return node
            return None

        if isinstance(node, (exp.And, exp.Or)):
            left_new = self._rewrite_date_tree(node.left, structure)
            right_new = self._rewrite_date_tree(node.right, structure)
            if left_new is not None:
                node.set('this', left_new)
            if right_new is not None:
                node.set('expression', right_new)
            return node

        # Direct date-function comparison
        replacement = self._try_date_rewrite(node, structure)
        if replacement is not None:
            return replacement

        if _node_has_date_func(node):
            # A date extraction buried in an unsupported shape -> no rewrite
            return None
        return None

    def _try_date_rewrite(self, node: exp.Expression,
                          structure: QueryStructure) -> Optional[exp.Expression]:
        """Try rewriting a single comparison (EQ only) containing a date func."""
        if not isinstance(node, exp.EQ):
            return None
        spec, literal = _match_func_vs_literal(node.left, node.right)
        if spec is None or literal is None:
            spec, literal = _match_func_vs_literal(node.right, node.left)
        if spec is None:
            return None

        func_name, col = spec
        if func_name not in REWRITABLE_DATE_FUNCS:
            return None

        # Column type gate: only date/timestamp (never timestamptz). Resolve the
        # table for unqualified columns via the query structure.
        table = self._resolve_table(structure, col.table, col.name)
        if table is None:
            return None
        col_type = get_column_type(self.schema, table, col.name)
        if col_type is None:
            return None
        normalized_type = (col_type.split('(')[0]).strip().lower()
        if normalized_type not in ('date', 'timestamp', 'datetime'):
            return None

        try:
            if func_name == 'YEAR':
                year = int(literal)
                if year < 1900 or year > 3000:
                    return None
                lower = exp.GTE(
                    this=exp.column(col.name, table=table),
                    expression=exp.Literal.string(f'{year}-01-01'),
                )
                upper = exp.LT(
                    this=exp.column(col.name, table=table),
                    expression=exp.Literal.string(f'{year + 1}-01-01'),
                )
            elif func_name == 'MONTH':
                # Requires YEAR context from the WHERE clause. Look for an adjacent
                # YEAR(col) = <year> predicate in the same AND chain.
                year = None
                for de in structure.where.date_extractions:
                    if de.get('func') == 'YEAR' and de.get('column') == col.name:
                        try:
                            year = int(de.get('value'))
                        except (ValueError, TypeError):
                            return None
                        break
                if year is None:
                    return None  # Advisory only - no year context
                month = int(literal)
                if month < 1 or month > 12:
                    return None
                lower = exp.GTE(
                    this=exp.column(col.name, table=table),
                    expression=exp.Literal.string(f'{year}-{month:02d}-01'),
                )
                # Next month
                next_month = month + 1
                next_year = year
                if next_month > 12:
                    next_month = 1
                    next_year += 1
                upper = exp.LT(
                    this=exp.column(col.name, table=table),
                    expression=exp.Literal.string(f'{next_year}-{next_month:02d}-01'),
                )
            elif func_name == 'DAY':
                # Requires YEAR and MONTH context.
                year = None
                month = None
                for de in structure.where.date_extractions:
                    if de.get('column') == col.name:
                        if de.get('func') == 'YEAR':
                            try:
                                year = int(de.get('value'))
                            except (ValueError, TypeError):
                                return None
                        elif de.get('func') == 'MONTH':
                            try:
                                month = int(de.get('value'))
                            except (ValueError, TypeError):
                                return None
                if year is None or month is None:
                    return None  # Advisory only - need both year and month
                day = int(literal)
                if day < 1 or day > 31:
                    return None
                lower = exp.GTE(
                    this=exp.column(col.name, table=table),
                    expression=exp.Literal.string(f'{year}-{month:02d}-{day:02d}'),
                )
                # Next day
                try:
                    from datetime import date, timedelta
                    nxt = date(year, month, day) + timedelta(days=1)
                    next_lit = nxt.strftime('%Y-%m-%d')
                except Exception:
                    return None
                upper = exp.LT(
                    this=exp.column(col.name, table=table),
                    expression=exp.Literal.string(next_lit),
                )
            elif func_name == 'DATE':
                lit = str(literal).strip()
                if not re.match(r'^\d{4}-\d{2}-\d{2}$', lit):
                    return None
                lower = exp.GTE(
                    this=exp.column(col.name, table=table),
                    expression=exp.Literal.string(lit),
                )
                # DATE(col) = '2025-01-15' -> col >= '2025-01-15' AND col < '2025-01-16'
                y, m, d = lit.split('-')
                try:
                    from datetime import date, timedelta
                    nxt = date(int(y), int(m), int(d)) + timedelta(days=1)
                    next_lit = nxt.strftime('%Y-%m-%d')
                except Exception:
                    return None
                upper = exp.LT(
                    this=exp.column(col.name, table=table),
                    expression=exp.Literal.string(next_lit),
                )
            else:
                return None
        except (ValueError, TypeError):
            return None

        return exp.paren(exp.and_(lower, upper))

    def _resolve_table(self, structure: QueryStructure, col_table: Optional[str],
                       col_name: str) -> Optional[str]:
        """Resolve a column's table when unqualified. Returns the single query
        table, or dictates None when ambiguous."""
        tables = [t for t in structure.tables if t]
        if col_table:
            if col_table.lower() in [t.lower() for t in tables]:
                return col_table
            return col_table
        if len(tables) == 1:
            return tables[0]
        # Ambiguous: search all tables for a column of this name.
        matches = [t for t in tables if get_column_type(self.schema, t, col_name) is not None]
        return matches[0] if len(matches) == 1 else None

    # --- Arithmetic on column -> constant comparison ------------------------

    def rewrite_arithmetic_on_column(self, sql: str, parsed: ParsedQuery,
                                     structure: QueryStructure) -> Optional[str]:
        """col + 5000 = 100000 -> col = 95000 (numeric/decimal columns only)."""
        if not structure.where.arithmetic_on_column:
            return None
        try:
            ast = sqlglot.parse_one(sql, dialect='postgres')
        except Exception:
            return None
        where = ast.find(exp.Where)
        if where is None:
            return None

        changed, new_body = self._rewrite_arith_tree(where.this, structure)
        if not changed:
            return None
        where.set('this', new_body)
        new_sql = ast.sql(dialect='postgres')
        if new_sql.strip() == sql.strip():
            return None
        return new_sql

    def _rewrite_arith_tree(self, node: exp.Expression,
                            structure: QueryStructure) -> Tuple[bool, exp.Expression]:
        if node is None:
            return False, node
        if isinstance(node, exp.Not):
            return False, node
        if isinstance(node, exp.Paren):
            changed, inner = self._rewrite_arith_tree(node.this, structure)
            if changed:
                node.set('this', inner)
                return True, node
            return False, node
        if isinstance(node, (exp.And, exp.Or)):
            lc, ln = self._rewrite_arith_tree(node.left, structure)
            rc, rn = self._rewrite_arith_tree(node.right, structure)
            if lc:
                node.set('this', ln)
            if rc:
                node.set('expression', rn)
            return (lc or rc), node

        replacement = self._try_arithmetic_rewrite(node, structure)
        if replacement is not None:
            return True, replacement
        return False, node

    def _try_arithmetic_rewrite(self, node: exp.Expression,
                                structure: QueryStructure) -> Optional[exp.Expression]:
        """col [+|-] const <op> const  ->  col <op> new_const."""
        if not isinstance(node, (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE)):
            return None

        side_col, op, const, other = None, None, None, None
        for left, right in ((node.left, node.right), (node.right, node.left)):
            arith, col, arith_op, const_val = _match_arith_side(left)
            if arith and isinstance(right, exp.Literal) and right.is_number:
                side_col, op, const, other = col, arith_op, const_val, right.this
                break

        if side_col is None or op not in ('+', '-'):
            return None

        # Type gate: numeric/decimal only (overflow-safe).
        table = self._resolve_table(structure, side_col.table, side_col.name)
        if table is None:
            return None
        col_type = get_column_type(self.schema, table, side_col.name)
        if col_type is None:
            return None
        normalized_type = (col_type.split('(')[0]).strip().lower()
        if normalized_type not in ARITHMETIC_SAFE_TYPES:
            return None

        try:
            const_v = float(const)
            other_v = float(other)
        except (ValueError, TypeError):
            return None

        if op == '+':
            target = other_v - const_v
        else:  # '-'
            target = other_v + const_v

        # Round to avoid float noise for integer-looking values.
        if target == int(target):
            target = int(target)
        else:
            target = round(target, 10)

        new_right = exp.Literal.number(str(target))
        cls = type(node)
        table = self._resolve_table(structure, side_col.table, side_col.name)
        return cls(this=exp.column(side_col.name, table=table),
                   expression=new_right)

    # --- IN (subquery) -> EXISTS -------------------------------------------

    def rewrite_in_to_exists(self, sql: str, parsed: ParsedQuery,
                             structure: QueryStructure) -> Optional[str]:
        """WHERE col IN (SELECT c FROM t WHERE ...) -> WHERE EXISTS (SELECT 1 ...).

        Only when the outer compared column is schema-known NOT NULL / PK, so
        IN vs EXISTS NULL semantics differ safely.
        """
        if not any(c.is_subquery for c in structure.where.in_conditions):
            return None
        try:
            ast = sqlglot.parse_one(sql, dialect='postgres')
        except Exception:
            return None
        where = ast.find(exp.Where)
        if where is None:
            return None

        changed = self._rewrite_in_tree(where.this, structure)
        if not changed:
            return None
        new_sql = ast.sql(dialect='postgres')
        if new_sql.strip() == sql.strip():
            return None
        return new_sql

    def _rewrite_in_tree(self, node: exp.Expression,
                         structure: QueryStructure) -> bool:
        if node is None:
            return False
        if isinstance(node, exp.Not):
            return False  # NOT IN semantics are distinct; do not rewrite
        if isinstance(node, exp.Paren):
            changed = self._rewrite_in_tree(node.this, structure)
            return changed
        if isinstance(node, (exp.And, exp.Or)):
            lc = self._rewrite_in_tree(node.left, structure)
            rc = self._rewrite_in_tree(node.right, structure)
            return lc or rc

        if isinstance(node, exp.In):
            sub = self._build_exists(node, structure)
            if sub is not None:
                node.replace(sub)
                return True
        return False

    def _build_exists(self, in_node: exp.In,
                      structure: QueryStructure) -> Optional[exp.Expression]:
        """Build an EXISTS subquery replacing `col IN (SELECT ...)`."""
        outer_col = in_node.this
        if not isinstance(outer_col, exp.Column):
            return None
        query = in_node.args.get('query')
        if query is None:
            return None
        inner_select = query.this if isinstance(query, exp.Subquery) else query
        if not isinstance(inner_select, exp.Select):
            return None

        # Inner projected column must be a single bare column.
        exprs = list(inner_select.expressions or [])
        if len(exprs) != 1:
            return None
        inner_expr = exprs[0]
        if isinstance(inner_expr, exp.Alias):
            inner_expr = inner_expr.this
        if not isinstance(inner_expr, exp.Column):
            return None
        inner_col = f"{inner_expr.table or _inner_table(inner_select)}.{inner_expr.name}"

        # Outer compared column must be schema-NOT-NULL. Resolve the outer
        # column's table from the AST (the owning SELECT's FROM table), not from
        # structure.tables which may also contain subquery tables.
        owner = in_node.find_ancestor(exp.Select)
        outer_table = None
        if owner is not None:
            _t = owner.find(exp.Table)
            outer_table = _t.name if _t is not None else None
        if outer_table is None:
            # Fallback: unambiguous resolution from the structure.
            outer_table = self._resolve_table(structure, outer_col.table, outer_col.name)
        if outer_table is None:
            return None
        if not column_is_not_null(self.schema, outer_table, outer_col.name):
            return None

        inner_from = _inner_table(inner_select)
        inner_where = inner_select.args.get('where')
        # Qualify the outer column with its resolved table so the correlation
        # reference binds to the outer query, not the inner FROM table.
        outer_qual = f"{outer_table}.{outer_col.name}"

        # Correlate: inner_col = outer_col
        corr = f"{inner_col} = {outer_qual}"
        if inner_where is not None:
            iw_sql = inner_where.this.sql(dialect='postgres')
            corr = f"{corr} AND ({iw_sql})"

        inner_sql = f"SELECT 1 FROM {inner_from} WHERE {corr}"
        try:
            inner_ast = sqlglot.parse_one(inner_sql, dialect='postgres')
        except Exception:
            return None
        return exp.Exists(this=inner_ast)

    # --- Remove DISTINCT ----------------------------------------------------

    def rewrite_remove_distinct(self, sql: str, parsed: ParsedQuery,
                                structure: QueryStructure) -> Optional[str]:
        """Drop DISTINCT only when selected columns cover the PK and no
        row-multiplying join / set-op is present."""
        if not structure.distinct.distinct or not structure.distinct.redundant_candidate:
            return None
        if structure.set_ops.kind:
            return None
        # Row-multiplying joins (many-to-many) invalidate the proof.
        for j in structure.joins:
            if j.type != 'INNER':
                return None
        try:
            ast = sqlglot.parse_one(sql, dialect='postgres')
        except Exception:
            return None
        top_select = _top_select(ast)
        if top_select is None:
            return None
        distinct = top_select.args.get('distinct')
        if distinct is None or distinct.args.get('on') is not None:
            return None  # no DISTINCT, or DISTINCT ON (not removable)
        top_select.set('distinct', None)
        new_sql = ast.sql(dialect='postgres')
        if new_sql.strip() == sql.strip():
            return None
        return new_sql

    # --- UNION -> UNION ALL (provably disjoint only) -------------------------

    def rewrite_union_to_union_all(self, sql: str, parsed: ParsedQuery,
                                   structure: QueryStructure) -> Optional[str]:
        """Rewrite UNION -> UNION ALL only when branch WHERE predicates are
        provably disjoint on the same column."""
        if structure.set_ops.kind != 'UNION' or structure.set_ops.branch_count < 2:
            return None
        try:
            ast = sqlglot.parse_one(sql, dialect='postgres')
        except Exception:
            return None
        union = _top_union(ast)
        if union is None:
            return None
        if union.args.get('distinct') is False:
            return None  # already UNION ALL

        branches = _union_branches(union)
        if len(branches) < 2:
            return None
        if not _branches_disjoint(branches):
            return None

        union.set('distinct', False)
        new_sql = ast.sql(dialect='postgres')
        if new_sql.strip() == sql.strip():
            return None
        return new_sql

    # --- CROSS JOIN with FK -------------------------------------------------

    def rewrite_cross_join(self, sql: str, parsed: ParsedQuery,
                           structure: QueryStructure) -> Optional[str]:
        """Replace a CROSS JOIN with an INNER JOIN when an explicit FK
        relationship exists between the two tables."""
        cross_join = None
        for j in structure.joins:
            if j.type == 'CROSS':
                cross_join = j
                break
        if cross_join is None:
            return None
        if not structure.tables:
            return None
        base_table = structure.tables[0].lower()
        join_table = cross_join.table.lower()
        rel = find_relationship(self.schema, base_table, join_table)
        if rel is None:
            return None

        # Determine which column pair links the two tables.
        f_t, f_c = rel.get('from_table'), rel.get('from_column')
        t_t, t_c = rel.get('to_table'), rel.get('to_column')
        if str(f_t).lower() == base_table:
            base_col, join_col = f_c, t_c
        else:
            base_col, join_col = t_c, f_c

        try:
            ast = sqlglot.parse_one(sql, dialect='postgres')
        except Exception:
            return None
        target_join = None
        for j in ast.find_all(exp.Join):
            jt = j.this if isinstance(j.this, exp.Table) else None
            if jt and jt.name.lower() == join_table:
                target_join = j
                break
        if target_join is None:
            return None
        if target_join.args.get('on'):
            return None  # already has a join condition

        on = exp.EQ(
            this=exp.column(join_col, table=join_table),
            expression=exp.column(base_col, table=base_table),
        )
        target_join.set('kind', None)
        target_join.set('side', None)
        target_join.set('on', exp.paren(on))
        new_sql = ast.sql(dialect='postgres')
        if new_sql.strip() == sql.strip():
            return None
        return new_sql

    # --- Filter push before INNER JOIN --------------------------------------

    def rewrite_filter_before_join(self, sql: str, parsed: ParsedQuery,
                                   structure: QueryStructure) -> Optional[str]:
        """Move a selective filter on an INNER-joined table into the ON clause.

        Only for INNER JOINs where the filter references the joined table's
        column (semantics preserved). Candidate still goes through EXPLAIN.
        """
        if not structure.joins:
            return None
        inner_join = None
        for j in structure.joins:
            if j.type.upper().strip() == 'INNER':
                inner_join = j
                break
        if inner_join is None:
            return None

        joined = inner_join.table.lower()
        # A filter on the joined table (not on both tables / base)
        candidate_pred = None
        for pred in list(structure.where.equalities) + list(structure.where.ranges):
            table = (pred.table or '').lower()
            if table == joined:
                candidate_pred = pred
                break
        if candidate_pred is None:
            return None

        if candidate_pred.column is None or candidate_pred.value is None:
            return None

        try:
            ast = sqlglot.parse_one(sql, dialect='postgres')
        except Exception:
            return None
        where = ast.find(exp.Where)
        if where is None:
            return None

        # Build the filter conjunct and remove it from WHERE; append to ON.
        # candidate_pred.value may include a table prefix like 'COL:Eng'; strip it.
        value_str = str(candidate_pred.value)
        if ':' in value_str:
            value_str = value_str.split(':', 1)[1]
        # Remove surrounding quotes if present
        if value_str.startswith("'") and value_str.endswith("'"):
            value_str = value_str[1:-1]

        from sqlglot import exp as _exp
        col_expr = _exp.column(candidate_pred.column, table=candidate_pred.table or joined)
        op = candidate_pred.operator
        # Handle comparison operators
        if op in ('EQ', '='):
            filt = _exp.EQ(this=col_expr, expression=exp.Literal.string(value_str))
        elif op in ('GT', '>'):
            filt = _exp.GT(this=col_expr, expression=exp.Literal.string(value_str))
        elif op in ('GTE', '>='):
            filt = _exp.GTE(this=col_expr, expression=exp.Literal.string(value_str))
        elif op in ('LT', '<'):
            filt = _exp.LT(this=col_expr, expression=exp.Literal.string(value_str))
        elif op in ('LTE', '<='):
            filt = _exp.LTE(this=col_expr, expression=exp.Literal.string(value_str))
        else:
            filt = None
        if filt is None:
            return None

        # Find the target join and append filter to its ON.
        target_join = None
        for j in ast.find_all(exp.Join):
            jt = j.this if isinstance(j.this, exp.Table) else None
            if jt and jt.name.lower() == joined:
                target_join = j
                break
        if target_join is None or not target_join.args.get('on'):
            return None

        on_expr = target_join.args['on']
        if isinstance(on_expr, exp.Paren):
            on_expr = on_expr.this
        target_join.set('on', exp.paren(exp.and_(on_expr, filt)))

        # Remove the predicate from WHERE (semantics preserved: it is still applied)
        _remove_conjunct(where.this, candidate_pred.column, candidate_pred.table)
        new_sql = ast.sql(dialect='postgres')
        if new_sql.strip() == sql.strip():
            return None
        return new_sql

    # --- Table alias qualification + placeholder resolution -----------------

    def rewrite_qualify_columns(self, sql: str, parsed: ParsedQuery,
                                structure: QueryStructure) -> Optional[str]:
        """Add table aliases, qualify unqualified columns with the owning
        table's alias, and resolve ``CURRENT_YEAR`` placeholders to the current
        calendar year (e.g. ``SELECT name, mark ... WHERE year = 'CURRENT_YEAR'``
        -> ``SELECT s.name, g.mark ... WHERE g.year = '2026'``).

        Conservative: only top-level FROM/JOIN tables are touched (no set-ops,
        CTEs, or subqueries). Columns are qualified only when the schema maps
        them to exactly one in-scope table; ambiguous/unresolved columns are
        left alone. Already-qualified references (e.g. ``student.id``) are
        kept unchanged.
        """
        try:
            ast = sqlglot.parse_one(sql, dialect='postgres')
        except Exception:
            return None

        # Conservative scope: no set-ops, CTEs, or subqueries in FROM/JOIN.
        if not isinstance(ast, exp.Select):
            return None
        if ast.find(exp.CTE):
            return None

        tables_in_scope = []
        from_node = ast.args.get('from')
        if from_node is not None:
            if not isinstance(from_node.this, exp.Table):
                return None
            tables_in_scope.append(from_node.this)
        for join in (ast.args.get('joins') or []):
            if not isinstance(join.this, exp.Table):
                return None
            tables_in_scope.append(join.this)
        if not tables_in_scope:
            return None

        # Table -> alias map. Existing aliases are preserved (and collected so
        # generated ones never collide with them).
        table_to_alias = {}  # table.name.lower -> alias
        used = set()
        for t in tables_in_scope:
            name = t.name
            if t.alias:
                table_to_alias[name.lower()] = t.alias.lower()
                used.add(t.alias.lower())
        for t in tables_in_scope:
            name = t.name
            if name.lower() in table_to_alias:
                continue
            base = name[0].lower()
            candidate = base
            n = 1
            while candidate in used:
                candidate = f"{base}{n}"
                n += 1
            table_to_alias[name.lower()] = candidate
            used.add(candidate)

        # Apply aliases to unaliased AST table nodes.
        for t in tables_in_scope:
            if t.alias:
                continue
            alias = table_to_alias.get(t.name.lower())
            if alias:
                t.set('alias', exp.TableAlias(this=exp.to_identifier(alias)))

        qualified_any = False
        if len(tables_in_scope) > 1:
            for col in ast.find_all(exp.Column):
                if col.table:
                    continue
                owners = [t.name for t in tables_in_scope
                          if get_column_type(self.schema, t.name, col.name) is not None]
                if len(owners) == 1:
                    owner_alias = table_to_alias.get(owners[0].lower())
                    if owner_alias:
                        col.set('table', owner_alias)
                        qualified_any = True

        resolved_year = self._resolve_current_year(ast)

        if not qualified_any and not resolved_year:
            return None

        new_sql = _postgres_no_as_generate(ast)
        # sqlglot normalizes ``ORDER BY ... DESC`` to ``DESC NULLS FIRST`` (the
        # PostgreSQL default). If the source did not write NULLS explicitly,
        # drop the injected default so the output stays faithful to the input.
        if not re.search(r'\bNULLS\s+(FIRST|LAST)\b', sql, re.IGNORECASE):
            new_sql = re.sub(r'\s+NULLS\s+(FIRST|LAST)\b', '', new_sql,
                             flags=re.IGNORECASE)
        if new_sql.strip() == sql.strip():
            return None
        return new_sql

    def _resolve_current_year(self, ast: exp.Expression) -> bool:
        """Replace ``CURRENT_YEAR`` placeholders with the current calendar year.

        Handles the quoted string literal (``'CURRENT_YEAR'``) and a bare
        unqualified identifier (``CURRENT_YEAR``). Returns True if any
        replacement happened.
        """
        year = str(datetime.now().year)
        changed = False
        for lit in ast.find_all(exp.Literal):
            value = str(lit.this).strip()
            if value.upper() == 'CURRENT_YEAR':
                lit.set('this', year)
                changed = True
        # Materialize first — replacing nodes while iterating a live walk is unsafe.
        for col in list(ast.find_all(exp.Column)):
            if col.table:
                continue
            if col.name.upper() == 'CURRENT_YEAR':
                col.replace(exp.Literal.string(year))
                changed = True
        return changed

    # --- Helper -------------------------------------------------------------

    def _build_structure(self, parsed: ParsedQuery, sql: str) -> QueryStructure:
        from .query_analyzer import analyze_query_structure
        return analyze_query_structure(parsed, sql, self.schema)


# --- Module-level helpers ---------------------------------------------------

def _node_has_date_func(node: exp.Expression) -> bool:
    for f in node.find_all(exp.Func):
        if type(f).__name__.upper() in ('YEAR', 'MONTH', 'DAY', 'DATE', 'DATE_TRUNC', 'EXTRACT'):
            return True
    return False


def _match_func_vs_literal(left: exp.Expression, right: exp.Expression) -> Tuple[Optional[Tuple[str, exp.Column]], Any]:
    """If `left` is a date-extraction func on a bare column and `right` is a
    literal, return ((FUNC_NAME, column), literal_value)."""
    if isinstance(left, exp.Func) and isinstance(right, exp.Literal):
        col = _func_column(left)
        if col is not None:
            name = type(left).__name__.upper()
            if name in ('YEAR', 'MONTH', 'DAY', 'DATE'):
                return (name, col), right.this
            if name == 'EXTRACT':
                part = (left.args.get('part') or '')
                part = str(part).upper()
                if part in ('YEAR', 'MONTH', 'DAY', 'DATE'):
                    return (part, col), right.this
    return None, None


def _func_column(func: exp.Expression) -> Optional[exp.Column]:
    """Return the bare Column used as the function's direct argument."""
    this = func.args.get('this')
    if isinstance(this, exp.Column):
        return this
    for a in func.expressions or []:
        if isinstance(a, exp.Column):
            return a
    return None


def _match_arith_side(expr: exp.Expression) -> Tuple[bool, Optional[exp.Column], Optional[str], Any]:
    """If expr is `col [+|-] const`, return (True, col, op_symbol, const)."""
    if not isinstance(expr, (exp.Add, exp.Sub)):
        return False, None, None, None
    left, right = expr.left, expr.right
    if isinstance(left, exp.Column) and isinstance(right, exp.Literal) and right.is_number:
        op = '+' if isinstance(expr, exp.Add) else '-'
        return True, left, op, right.this
    if isinstance(right, exp.Column) and isinstance(left, exp.Literal) and left.is_number:
        op = '+' if isinstance(expr, exp.Add) else '-'
        return True, right, op, left.this
    return False, None, None, None


def _inner_table(select: exp.Select) -> str:
    table = select.find(exp.Table)
    return table.name if table else ''


def _top_select(ast: exp.Expression) -> Optional[exp.Select]:
    if isinstance(ast, exp.Select):
        return ast
    return ast.find(exp.Select)


def _top_union(ast: exp.Expression) -> Optional[exp.Union]:
    if isinstance(ast, exp.Union):
        return ast
    return ast.find(exp.Union)


def _union_branches(union: exp.Union) -> List[exp.Select]:
    out = []
    stack = [union]
    while stack:
        node = stack.pop()
        if isinstance(node, exp.Union):
            stack.append(node.args.get('this'))
            stack.append(node.args.get('expression'))
        elif isinstance(node, exp.Select):
            out.append(node)
    return out


def _branches_disjoint(branches: List[exp.Select]) -> bool:
    """Both branches have a WHERE equality on the same column with different
    literal values -> the result sets are provably disjoint."""
    if len(branches) != 2:
        return False
    vals0 = _branch_constraints(branches[0])
    vals1 = _branch_constraints(branches[1])
    shared = set(vals0.keys()) & set(vals1.keys())
    if not shared:
        return False
    for col in shared:
        v0 = vals0[col]
        v1 = vals1[col]
        try:
            if str(v0) != str(v1) and (v0 is None or v1 is None or float(v0) != float(v1)):
                return True
        except (ValueError, TypeError):
            if str(v0) != str(v1):
                return True
    return False


def _branch_constraints(select: exp.Select) -> Dict[str, Any]:
    """Collect `column = literal` constraints from a branch's WHERE."""
    result = {}
    where = select.args.get('where')
    if where is None:
        return result
    for eq in where.this.find_all(exp.EQ):
        left, right = eq.left, eq.right
        if isinstance(left, exp.Column) and isinstance(right, exp.Literal):
            result[left.name.lower()] = right.this
    return result


def _parse_value(value_sql: str) -> exp.Expression:
    """Parse a simple literal value string back into a literal/column expr."""
    try:
        if value_sql.startswith("'") and value_sql.endswith("'"):
            return exp.Literal.string(value_sql[1:-1])
        try:
            return exp.Literal.number(str(int(value_sql)))
        except (ValueError, TypeError):
            return exp.Literal.number(str(float(value_sql)))
    except (ValueError, TypeError):
        return exp.Literal.string(str(value_sql))


def _remove_conjunct(where_body: exp.Expression, col_name: str,
                     table_name: Optional[str]) -> bool:
    """Best-effort removal of a matching conjunct from an AND chain."""
    def matches(e: exp.Expression) -> bool:
        if isinstance(e, exp.Paren):
            return matches(e.this)
        if isinstance(e, exp.EQ) and isinstance(e.left, exp.Column):
            name_ok = e.left.name.lower() == col_name.lower()
            table_ok = True
            if table_name:
                table_ok = (e.left.table or '').lower() == table_name.lower()
            return name_ok and table_ok
        return False

    if isinstance(where_body, exp.And):
        if matches(where_body.left):
            cur = where_body
            parent = cur.parent
            repl = where_body.right
            if parent is not None:
                parent.replace(repl)
            return True
        if _remove_conjunct(where_body.right, col_name, table_name):
            return True
        if _remove_conjunct(where_body.left, col_name, table_name):
            return True
        return False
    return False