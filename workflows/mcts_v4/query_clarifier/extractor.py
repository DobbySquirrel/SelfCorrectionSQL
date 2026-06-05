"""Deterministic constraint_hint extraction from SQL via sqlglot AST."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    import sqlglot
    from sqlglot import exp

    HAS_SQLGLOT = True
except ImportError:
    HAS_SQLGLOT = False

_COMPARISONS = tuple(
    cls
    for cls in (
        getattr(exp, "EQ", None),
        getattr(exp, "NEQ", None),
        getattr(exp, "LT", None),
        getattr(exp, "GT", None),
        getattr(exp, "LTE", None),
        getattr(exp, "GTE", None),
        getattr(exp, "Like", None),
        getattr(exp, "In", None),
    )
    if cls is not None
)


def _norm(name: str) -> str:
    return (name or "").strip().lower().strip('"`[]')


def _parse(sql: str):
    if not HAS_SQLGLOT or not (sql or "").strip():
        return None
    try:
        return sqlglot.parse_one(sql, dialect="sqlite")
    except Exception:
        return None


def _col_name(node) -> str:
    if node is None:
        return ""
    if isinstance(node, exp.Column):
        parts = [_norm(node.name)]
        if node.table:
            parts.insert(0, _norm(node.table))
        return ".".join(p for p in parts if p)
    if hasattr(node, "name") and node.name:
        return _norm(node.name)
    if hasattr(node, "alias_or_name") and node.alias_or_name:
        return _norm(node.alias_or_name)
    return _norm(str(node))


def _measure_col(node) -> str:
    cn = _col_name(node)
    return cn.split(".")[-1] if cn else ""


def _table_name(t) -> str:
    if isinstance(t.this, exp.Identifier):
        return _norm(t.this.name)
    if getattr(t, "name", None):
        return _norm(t.name)
    return _norm(t.alias_or_name)


def _select_group_names(node) -> Tuple[List[str], List[str]]:
    select_cols: List[str] = []
    group_cols: List[str] = []
    if node is None:
        return select_cols, group_cols
    for sel in node.find_all(exp.Select):
        for e in sel.expressions or []:
            if isinstance(e, exp.Star):
                continue
            if isinstance(e, exp.Column) and e.name:
                select_cols.append(_norm(e.name))
            elif hasattr(e, "alias_or_name") and e.alias_or_name:
                select_cols.append(_norm(e.alias_or_name))
        gb = sel.args.get("group")
        if gb:
            for e in gb.expressions or []:
                if isinstance(e, exp.Column) and e.name:
                    group_cols.append(_norm(e.name))
                else:
                    cn = _col_name(e)
                    if cn:
                        group_cols.append(_norm(cn.split(".")[-1]))
    return select_cols, group_cols


def _outer_order(node):
    orders = list(node.find_all(exp.Order))
    return orders[-1] if orders else None


def _outer_limit(node):
    limits = list(node.find_all(exp.Limit))
    return limits[-1] if limits else None


def _first_agg(node) -> Optional[Tuple[str, Optional[str], bool]]:
    if node is None:
        return None
    for agg in node.find_all(exp.AggFunc):
        fn = type(agg).__name__.upper()
        distinct = agg.find(exp.Distinct) is not None
        inner = agg.this
        if isinstance(inner, exp.Distinct) and inner.expressions:
            inner = inner.expressions[0]
            distinct = True
        if fn == "COUNT":
            if isinstance(inner, exp.Star) or str(inner) == "*":
                return ("COUNT_STAR", None, False)
            col = _measure_col(inner)
            if distinct:
                return ("COUNT_DISTINCT", col, True)
            return ("COUNT", col, False)
        col = _measure_col(inner) if inner else None
        return (fn, col or None, distinct)
    return None


def extract_measure(sql: str) -> Optional[Dict[str, Any]]:
    node = _parse(sql)
    if node is None:
        return None
    agg = _first_agg(node)
    if not agg:
        return None
    fn, col, _ = agg
    hint: Dict[str, Any] = {"agg": fn}
    if col and fn not in ("COUNT_STAR",):
        hint["column"] = col
    return hint


def extract_ranking(sql: str) -> Optional[Dict[str, Any]]:
    node = _parse(sql)
    if node is None:
        return None
    order = None
    key = None
    o = _outer_order(node)
    if o and o.expressions:
        e = o.expressions[0]
        key = _col_name(e.this if hasattr(e, "this") else e)
        order = "DESC" if e.args.get("desc") else "ASC"
    limit = None
    lim = _outer_limit(node)
    if lim is not None:
        try:
            limit = int(str(lim.expression))
        except (TypeError, ValueError):
            pass
    if order is None and limit is None:
        return None
    hint: Dict[str, Any] = {}
    if order:
        hint["order"] = order
    if limit is not None and limit > 0:
        hint["limit"] = limit
    if key:
        hint["key"] = key
    return hint if hint else None


def extract_output(sql: str) -> Optional[Dict[str, Any]]:
    node = _parse(sql)
    if node is None:
        return None
    select_cols, group_cols = _select_group_names(node)
    if not select_cols and not group_cols:
        return None
    hint: Dict[str, Any] = {}
    if select_cols:
        hint["select_columns"] = select_cols
    if group_cols:
        hint["group_by"] = group_cols
    return hint if hint else None


def extract_reference(sql: str) -> Optional[Dict[str, Any]]:
    node = _parse(sql)
    if node is None:
        return None
    tables: List[str] = []
    columns: List[str] = []
    for t in node.find_all(exp.Table):
        n = _table_name(t)
        if n:
            tables.append(n)
    for c in node.find_all(exp.Column):
        cn = _col_name(c)
        if cn:
            columns.append(cn.split(".")[-1])
    if not tables:
        return None
    return {"required_tables": sorted(set(tables)), "required_columns": sorted(set(columns))}


def _pred_op(node) -> str:
    if isinstance(node, (exp.EQ,)):
        return "="
    if isinstance(node, (exp.NEQ,)):
        return "!="
    if isinstance(node, exp.LT):
        return "<"
    if isinstance(node, exp.GT):
        return ">"
    if isinstance(node, exp.LTE):
        return "<="
    if isinstance(node, exp.GTE):
        return ">="
    if isinstance(node, exp.Like):
        return "LIKE"
    if isinstance(node, exp.In):
        return "IN"
    return "="


def extract_value(sql: str) -> Optional[Dict[str, Any]]:
    node = _parse(sql)
    if node is None:
        return None
    preds: List[Dict[str, Any]] = []
    for w in node.find_all(exp.Where):
        for pred in w.find_all(_COMPARISONS):
            col = _col_name(getattr(pred, "this", None))
            rhs = getattr(pred, "expression", None)
            if not col:
                continue
            val: Any = None
            if rhs is not None:
                if isinstance(rhs, exp.Literal):
                    val = rhs.this
                else:
                    val = str(rhs)
            preds.append({"column": col.split(".")[-1], "op": _pred_op(pred), "value": val})
        break
    if not preds:
        return None
    return preds[0]


def extract_constraint_hint(axis: str, sql: str) -> Optional[Dict[str, Any]]:
    axis = (axis or "").strip()
    if axis == "Measure":
        return extract_measure(sql)
    if axis == "Ranking":
        return extract_ranking(sql)
    if axis == "Output":
        return extract_output(sql)
    if axis == "Reference":
        return extract_reference(sql)
    if axis == "Value":
        return extract_value(sql)
    return None
