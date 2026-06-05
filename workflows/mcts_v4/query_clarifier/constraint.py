"""Constraint compilation and AST-based sql_satisfies checks."""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from workflows.mcts_v4.query_clarifier.config import (
    axis_hard_whitelist,
    clarify_mode,
    hard_confidence_threshold,
    soft_confidence_threshold,
)
from workflows.mcts_v4.query_clarifier.schemas import (
    ClarificationAnswer,
    ClarificationCandidate,
    ClarificationQuestion,
    ClusterSummary,
    CompiledConstraint,
    Predicate,
    SatResult,
)

logger = logging.getLogger(__name__)

try:
    import sqlglot
    from sqlglot import exp

    HAS_SQLGLOT = True
except ImportError:
    HAS_SQLGLOT = False

AGG_MAP = {
    "COUNT_STAR": ("COUNT", "*"),
    "COUNT_DISTINCT": ("COUNT", "DISTINCT"),
    "SUM": ("SUM", None),
    "AVG": ("AVG", None),
    "MIN": ("MIN", None),
    "MAX": ("MAX", None),
}


def _norm_ident(name: str) -> str:
    return (name or "").strip().lower().strip('"`[]')


def decide_level(answer: ClarificationAnswer, axis: str) -> str:
    if answer.abstain or answer.choice is None:
        return "none"
    mode = clarify_mode()
    hard_conf = hard_confidence_threshold()
    soft_conf = soft_confidence_threshold()
    if answer.confidence >= hard_conf:
        if axis in ("Reference", "Value"):
            return "soft"
        if axis not in axis_hard_whitelist():
            return "soft"
        if mode == "soft_only":
            return "soft"
        if mode == "log_only":
            return "hard"  # compiled level for simulation; enforcer respects log_only
        return "hard"
    if answer.confidence >= soft_conf:
        return "soft"
    return "none"


def _hint_to_predicates(hint: Dict[str, Any]) -> List[Predicate]:
    if not hint.get("column"):
        return []
    return [Predicate(column=str(hint["column"]), op=str(hint.get("op", "=")), value=hint.get("value"))]


def rep_sql_for_rank(top_clusters: List[ClusterSummary], rank: int) -> str:
    if rank <= 0:
        return ""
    for cl in top_clusters:
        if cl.rank == rank:
            return cl.representative_sql or ""
    if rank <= len(top_clusters):
        return top_clusters[rank - 1].representative_sql or ""
    return ""


def resolve_candidate_rank(chosen: ClarificationCandidate, cid_labels: List[str]) -> int:
    if chosen.maps_to_cluster_rank > 0:
        return chosen.maps_to_cluster_rank
    if chosen.cid in cid_labels:
        return cid_labels.index(chosen.cid) + 1
    return 0


def apply_self_check(cc: CompiledConstraint, rep_sql: str) -> CompiledConstraint:
    if cc.level == "none" or not (rep_sql or "").strip():
        return cc
    sat = sql_satisfies(rep_sql, cc)
    if not sat.satisfied:
        cc.self_check_failed = True
        cc.violated_fields = list(sat.violated_fields)
        cc.level = "none"
    return cc


def candidate_passes_self_check(
    candidate: ClarificationCandidate,
    axis: str,
    top_clusters: List[ClusterSummary],
) -> bool:
    """Pre-filter: candidate constraint_hint must be satisfied by its mapped rep_sql."""
    fake_cq = ClarificationQuestion(
        qid=0,
        axis=axis,  # type: ignore[arg-type]
        question="",
        candidates=[candidate],
    )
    fake_ans = ClarificationAnswer(qid=0, choice=candidate.cid, confidence=0.9, evidence="", abstain=False)
    cc = _compile_constraint_fields(fake_cq, fake_ans, candidate)
    if cc.level == "none":
        return True
    rank = resolve_candidate_rank(candidate, ["A", "B", "C", "D"])
    rep = rep_sql_for_rank(top_clusters, rank)
    if not rep:
        return False
    return sql_satisfies(rep, cc).satisfied


def _compile_constraint_fields(
    cq: ClarificationQuestion,
    ans: ClarificationAnswer,
    chosen: ClarificationCandidate,
) -> CompiledConstraint:
    cc = CompiledConstraint(qid=cq.qid, axis=cq.axis, source_answer=ans)
    hint = chosen.constraint_hint or {}
    cc.level = decide_level(ans, cq.axis)  # type: ignore[assignment]

    if cq.axis == "Measure":
        agg = str(hint.get("agg", "")).upper()
        col = hint.get("column")
        if agg == "COUNT_STAR":
            cc.required_agg = ("COUNT", "*")
        elif agg == "COUNT_DISTINCT":
            cc.required_agg = ("COUNT", _norm_ident(str(col)) if col else None)
        elif agg == "COUNT" and col:
            cc.required_agg = ("COUNT", _norm_ident(str(col)))
        elif agg in AGG_MAP:
            cc.required_agg = (agg, _norm_ident(str(col)) if col else None)
    elif cq.axis == "Ranking":
        order = str(hint.get("order", "")).upper()
        if order in ("ASC", "DESC"):
            cc.required_order = order
        lim = hint.get("limit")
        if lim is not None:
            try:
                lim_i = int(lim)
                if lim_i > 0:
                    cc.required_limit = lim_i
            except (TypeError, ValueError):
                pass
    elif cq.axis == "Output":
        cc.required_select_columns = {_norm_ident(c) for c in (hint.get("select_columns") or []) if c}
        cc.required_group_by = {_norm_ident(c) for c in (hint.get("group_by") or []) if c}
    elif cq.axis == "Value":
        cc.required_predicates = _hint_to_predicates(hint)
    elif cq.axis == "Reference":
        cc.required_tables = {_norm_ident(t) for t in (hint.get("required_tables") or []) if t}
        cc.banned_tables = {_norm_ident(t) for t in (hint.get("banned_tables") or []) if t}
        cc.required_columns = {_norm_ident(c) for c in (hint.get("required_columns") or []) if c}
    return cc


def compile_constraint(
    cq: ClarificationQuestion,
    ans: ClarificationAnswer,
    top_clusters: Optional[List[ClusterSummary]] = None,
) -> CompiledConstraint:
    cc = CompiledConstraint(qid=cq.qid, axis=cq.axis, source_answer=ans)
    if cq.axis == "None" or ans.abstain or not ans.choice:
        cc.level = "none"
        return cc
    chosen = next((c for c in cq.candidates if c.cid == ans.choice), None)
    if not chosen:
        cc.level = "none"
        return cc
    cc = _compile_constraint_fields(cq, ans, chosen)

    if top_clusters:
        rank = resolve_candidate_rank(chosen, ["A", "B", "C", "D"])
        cc.self_check_rep_rank = rank or None
        rep = rep_sql_for_rank(top_clusters, rank)
        cc = apply_self_check(cc, rep)
    return cc


def _parse_sql_ast(sql: str):
    if not HAS_SQLGLOT:
        return None
    try:
        return sqlglot.parse_one(sql, dialect="sqlite")
    except Exception:
        return None


def _collect_tables(node) -> Set[str]:
    tables: Set[str] = set()
    if not HAS_SQLGLOT or node is None:
        return tables
    for t in node.find_all(exp.Table):
        name = t.alias_or_name
        if name:
            tables.add(_norm_ident(name))
    return tables


def _collect_columns(node) -> Set[str]:
    cols: Set[str] = set()
    if not HAS_SQLGLOT or node is None:
        return cols
    for c in node.find_all(exp.Column):
        if c.name:
            cols.add(_norm_ident(c.name))
    return cols


def _collect_aggs(node) -> List[Tuple[str, Optional[str]]]:
    out: List[Tuple[str, Optional[str]]] = []
    if not HAS_SQLGLOT or node is None:
        return out
    for agg in node.find_all(exp.AggFunc):
        fn = type(agg).__name__.upper()
        if fn == "COUNT":
            inner = agg.this
            distinct_node = agg.find(exp.Distinct)
            is_distinct = distinct_node is not None
            if isinstance(inner, exp.Star) or str(inner) == "*":
                out.append(("COUNT", "*"))
            elif is_distinct:
                col_expr = inner
                if isinstance(col_expr, exp.Distinct):
                    col_expr = col_expr.expressions[0] if col_expr.expressions else col_expr
                col = _norm_ident(getattr(col_expr, "name", str(col_expr)))
                out.append(("COUNT", col))
            else:
                col = _norm_ident(getattr(inner, "name", str(inner)))
                out.append(("COUNT", col))
        else:
            inner = agg.this
            col = _norm_ident(getattr(inner, "name", str(inner))) if inner else None
            out.append((fn, col))
    return out


def _collect_order(node) -> Tuple[Optional[str], Optional[int]]:
    if not HAS_SQLGLOT or node is None:
        return None, None
    order = None
    orders = list(node.find_all(exp.Order))
    if orders:
        o = orders[-1]
        desc = any(getattr(e, "args", {}).get("desc") for e in o.expressions or [])
        order = "DESC" if desc else "ASC"
    limit = None
    limits = list(node.find_all(exp.Limit))
    if limits:
        lim = limits[-1]
        try:
            limit = int(str(lim.expression))
        except (TypeError, ValueError):
            pass
    return order, limit


def _collect_select_group(node) -> Tuple[Set[str], Set[str]]:
    sel: Set[str] = set()
    grp: Set[str] = set()
    if not HAS_SQLGLOT or node is None:
        return sel, grp
    for s in node.find_all(exp.Select):
        for e in s.expressions or []:
            if isinstance(e, exp.Column) and e.name:
                sel.add(_norm_ident(e.name))
            elif hasattr(e, "alias_or_name") and e.alias_or_name:
                sel.add(_norm_ident(e.alias_or_name))
        gb = s.args.get("group")
        if gb:
            for e in gb.expressions or []:
                if isinstance(e, exp.Column) and e.name:
                    grp.add(_norm_ident(e.name))
    return sel, grp


def _fallback_string_check(sql: str, cc: CompiledConstraint) -> SatResult:
    """Degraded check when sqlglot unavailable."""
    s = sql.lower()
    violated: List[str] = []
    if cc.required_agg:
        op, col = cc.required_agg
        if op == "COUNT" and col == "*":
            if "count(*)" not in s.replace(" ", ""):
                violated.append("required_agg")
        elif op == "COUNT" and col:
            if f"count({col})" not in s and f"count(distinct {col})" not in s:
                violated.append("required_agg")
    if cc.required_order and f"order by" in s:
        if cc.required_order == "DESC" and " desc" not in s:
            violated.append("required_order")
        if cc.required_order == "ASC" and " desc" in s:
            violated.append("required_order")
    if cc.required_limit is not None and f"limit {cc.required_limit}" not in s:
        violated.append("required_limit")
    for t in cc.required_tables:
        if t not in s:
            violated.append("required_tables")
    for t in cc.banned_tables:
        if t in s:
            violated.append("banned_tables")
    return SatResult(satisfied=not violated, violated_fields=violated)


def sql_satisfies(sql: str, cc: CompiledConstraint) -> SatResult:
    if cc.level == "none":
        return SatResult(satisfied=True)
    try:
        node = _parse_sql_ast(sql)
        if node is None:
            return _fallback_string_check(sql, cc)
        violated: List[str] = []
        if cc.required_agg:
            aggs = _collect_aggs(node)
            req_op, req_col = cc.required_agg
            if req_op == "COUNT" and req_col and req_col != "*":
                matched = any(a[0] == "COUNT" and a[1] == req_col for a in aggs)
            elif req_op == "COUNT" and req_col == "*":
                matched = ("COUNT", "*") in aggs
            else:
                matched = cc.required_agg in aggs or any(
                    a[0] == req_op and (req_col is None or a[1] == req_col) for a in aggs
                )
            if not matched:
                violated.append("required_agg")
        if cc.required_order:
            order, _ = _collect_order(node)
            if order and order != cc.required_order:
                violated.append("required_order")
        if cc.required_limit is not None:
            _, lim = _collect_order(node)
            if lim != cc.required_limit:
                violated.append("required_limit")
        if cc.required_select_columns:
            sel, _ = _collect_select_group(node)
            if not cc.required_select_columns.issubset(sel):
                violated.append("required_select_columns")
        if cc.required_group_by:
            _, grp = _collect_select_group(node)
            if not cc.required_group_by.issubset(grp):
                violated.append("required_group_by")
        if cc.required_tables:
            tabs = _collect_tables(node)
            if not cc.required_tables.issubset(tabs):
                violated.append("required_tables")
        if cc.banned_tables:
            tabs = _collect_tables(node)
            if cc.banned_tables & tabs:
                violated.append("banned_tables")
        if cc.required_columns:
            cols = _collect_columns(node)
            if not cc.required_columns.issubset(cols):
                violated.append("required_columns")
        return SatResult(satisfied=not violated, violated_fields=violated)
    except Exception as ex:
        logger.warning("sql_satisfies exception (treat as satisfied): %s", ex)
        return SatResult(satisfied=True, violated_fields=["parse_warning"])
