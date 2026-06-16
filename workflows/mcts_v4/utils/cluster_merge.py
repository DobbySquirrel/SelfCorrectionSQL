"""Jaccard merge helpers for CTE / final SQL clustering."""

from __future__ import annotations

import os
from collections import Counter
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

from .mcts_helpers import MCTSUtils

RowSet = FrozenSet[tuple]


def cte_jaccard_enabled() -> bool:
    return os.environ.get("MCTS_CTE_JACCARD_MERGE", "0").strip().lower() in ("1", "true", "yes", "on")


def final_jaccard_enabled() -> bool:
    return os.environ.get("MCTS_FINAL_JACCARD_MERGE", "0").strip().lower() in ("1", "true", "yes", "on")


def jaccard_threshold(env_key: str = "MCTS_CTE_JACCARD_THRESHOLD", default: float = 0.85) -> float:
    raw = os.environ.get(env_key, str(default)).strip()
    try:
        return float(raw)
    except ValueError:
        return default


def rowset_from_execution(result: Optional[Dict[str, Any]]) -> Optional[RowSet]:
    if not result or not result.get("valid"):
        return None
    rows, _cols = MCTSUtils.execution_result_to_rows_columns(result)
    tuples: List[tuple] = []
    for row in rows:
        if isinstance(row, dict):
            tuples.append(tuple(row.values()))
        else:
            tuples.append(tuple(row))
    return frozenset(tuples)


def jaccard(a: Optional[RowSet], b: Optional[RowSet]) -> float:
    if a is None or b is None:
        return 0.0
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _union_find(n: int, edges: List[Tuple[int, int]]) -> List[int]:
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for i, j in edges:
        union(i, j)
    return [find(i) for i in range(n)]


def merge_cte_buckets(buckets: Dict[str, dict], threshold: float) -> Dict[str, dict]:
    """Merge CTE probe buckets whose row-sets have Jaccard >= threshold."""
    skip = {"<END>", "empty_result"}
    keys = [
        k
        for k in buckets
        if k not in skip and not str(k).startswith("invalid_")
    ]
    if len(keys) < 2:
        return buckets

    rowsets = [rowset_from_execution(buckets[k].get("execution_result")) for k in keys]
    edges: List[Tuple[int, int]] = []
    for i in range(len(keys)):
        if rowsets[i] is None:
            continue
        for j in range(i + 1, len(keys)):
            if rowsets[j] is None:
                continue
            if jaccard(rowsets[i], rowsets[j]) >= threshold:
                edges.append((i, j))
    if not edges:
        return buckets

    roots = _union_find(len(keys), edges)
    groups: Dict[int, List[str]] = {}
    for i, r in enumerate(roots):
        groups.setdefault(r, []).append(keys[i])

    out = dict(buckets)
    merged_groups = 0
    for members in groups.values():
        if len(members) < 2:
            continue
        merged_groups += 1
        rep_key = max(members, key=lambda k: int(out[k].get("count", 0)))
        rep = out[rep_key]
        for k in members:
            if k == rep_key:
                continue
            other = out.pop(k)
            rep["count"] = int(rep.get("count", 0)) + int(other.get("count", 0))
            rep["variants"] = list(rep.get("variants") or []) + list(other.get("variants") or [])
            if len((other.get("cte") or "")) < len((rep.get("cte") or "")):
                rep["cte"] = other.get("cte")
                rep["execution_result"] = other.get("execution_result")

    if merged_groups:
        print(
            f"[CTE Jaccard] merged {merged_groups} bucket group(s) at tau>={threshold:.2f} "
            f"→ {len(out)} buckets"
        )
    return out


def remap_rollout_signatures_by_jaccard(
    rollout_stats_list: List[Dict[str, Any]],
    sql_to_super: Dict[str, str],
) -> List[Dict[str, Any]]:
    """Return deep-copied rollouts with result_signature remapped to super-cluster ids."""
    from copy import deepcopy

    out: List[Dict[str, Any]] = []
    for rs in rollout_stats_list or []:
        rs2 = deepcopy(rs)
        rb: Counter = Counter()
        vs2 = []
        for v in rs.get("all_sql_variants") or []:
            v2 = dict(v)
            sql = (v.get("sql") or "").strip()
            if sql and v.get("valid"):
                nk = " ".join(sql.split()).strip().lower()
                sup = sql_to_super.get(nk)
                if sup:
                    v2["result_signature"] = sup
                    rb[sup] += 1
            vs2.append(v2)
        rs2["all_sql_variants"] = vs2
        rs2["result_buckets"] = dict(rb) if rb else {}
        out.append(rs2)
    return out
