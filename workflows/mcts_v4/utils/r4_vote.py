"""R4 majority vote helpers; downweight / exclude timeout-prone clusters."""

from __future__ import annotations

import os
from collections import Counter
from typing import Any, Dict, List, Optional, Set, Tuple

ENV_TIMEOUT_VOTE_MODE = "MCTS_R4_TIMEOUT_VOTE_MODE"
ENV_TIMEOUT_VOTE_WEIGHT = "MCTS_R4_TIMEOUT_VOTE_WEIGHT"
ENV_TIEBREAK = "MCTS_R4_TIEBREAK"
ENV_CLUSTER_VOTE_MODE = "MCTS_R4_VOTE_MODE"


def _norm_sql(sql: str) -> str:
    return " ".join((sql or "").split()).strip().lower()


def timeout_vote_mode() -> str:
    return os.environ.get(ENV_TIMEOUT_VOTE_MODE, "off").strip().lower()


def timeout_vote_weight() -> float:
    raw = os.environ.get(ENV_TIMEOUT_VOTE_WEIGHT, "0.5").strip()
    try:
        return float(raw)
    except ValueError:
        return 0.5


def tiebreak_mode() -> str:
    return os.environ.get(ENV_TIEBREAK, "rows").strip().lower()


def cluster_vote_mode() -> str:
    """mc: per-rollout vote only max-count bucket sigs; all_buckets: every sig in buckets +1."""
    return os.environ.get(ENV_CLUSTER_VOTE_MODE, "all_buckets").strip().lower()


def collect_r4_cluster_votes(rollout_stats_list: List[Dict[str, Any]]) -> Counter:
    votes: Counter = Counter()
    vm = cluster_vote_mode()
    for r in rollout_stats_list or []:
        rb = r.get("result_buckets") or {}
        if not rb:
            continue
        if vm == "all_buckets":
            for sig in rb:
                if sig:
                    votes[sig] += 1
            continue
        mc = max(rb.values())
        for sig, cnt in rb.items():
            if cnt == mc:
                votes[sig] += 1
    return votes


def timeout_sql_norms(rollout_stats_list: List[Dict[str, Any]]) -> Set[str]:
    bad: Set[str] = set()
    for r in rollout_stats_list or []:
        for v in r.get("all_sql_variants") or []:
            sql = _norm_sql(v.get("sql", ""))
            if not sql:
                continue
            err = v.get("error") or ""
            if v.get("valid") is False and "timeout" in err.lower():
                bad.add(sql)
    return bad


def timeout_signatures(rollout_stats_list: List[Dict[str, Any]]) -> Set[str]:
    bad: Set[str] = set()
    for r in rollout_stats_list or []:
        for v in r.get("all_sql_variants") or []:
            sig = (v.get("result_signature") or "").strip()
            if not sig:
                continue
            err = v.get("error") or ""
            if v.get("valid") is False and "timeout" in err.lower():
                bad.add(sig)
    return bad


def cluster_timeout_penalty(sig: str, clusters: Optional[dict], timeout_sqls: Set[str]) -> float:
    if not clusters or not timeout_sqls:
        return 0.0
    c = clusters.get(sig)
    if not c:
        return 0.0
    variants = getattr(c, "variants", None) or []
    if not variants:
        return 0.0
    bad = sum(1 for sql, _, _ in variants if _norm_sql(sql) in timeout_sqls)
    return bad / len(variants)


def collect_r4_votes(
    rollout_stats_list: List[Dict[str, Any]],
    *,
    clusters: Optional[dict] = None,
    mode: str | None = None,
    downweight: float | None = None,
) -> Tuple[Counter, Set[str]]:
    mode = (mode if mode is not None else timeout_vote_mode()).strip().lower()
    dw = downweight if downweight is not None else timeout_vote_weight()
    timeout_sqls = timeout_sql_norms(rollout_stats_list) if mode != "off" else set()
    timeout_sigs = timeout_signatures(rollout_stats_list) if mode != "off" else set()

    cluster_votes = collect_r4_cluster_votes(rollout_stats_list)
    votes: Counter = Counter()
    for sig, base_w in cluster_votes.items():
        penalty = cluster_timeout_penalty(sig, clusters, timeout_sqls)
        if mode == "exclude" and (sig in timeout_sigs or penalty >= 1.0):
            continue
        w = base_w
        if mode == "downweight":
            if sig in timeout_sigs:
                w = base_w * dw
            elif penalty > 0:
                w = base_w * (dw + (1.0 - dw) * (1.0 - penalty))
        votes[sig] += w
    return votes, timeout_sqls


def tiebreak_variants(
    variants: List[tuple],
    timeout_sqls: Optional[Set[str]] = None,
    *,
    mode: str | None = None,
) -> str:
    if not variants:
        return ""
    timeout_sqls = timeout_sqls or set()
    tb = (mode if mode is not None else tiebreak_mode()).strip().lower()

    from .execution_tiebreak import variants_have_row_collision

    if tb == "rows" and variants_have_row_collision(variants):
        def collision_key(item: tuple):
            sql, reward, rows = item
            is_timeout = 1 if _norm_sql(sql) in timeout_sqls else 0
            return (is_timeout, -float(reward or 0.0), len(sql or ""))

        best = min(variants, key=collision_key)
        return (best[0] or "").strip()

    def key(item: tuple):
        sql, reward, rows = item
        is_timeout = 1 if _norm_sql(sql) in timeout_sqls else 0
        if tb == "reward":
            return (is_timeout, -float(reward or 0.0), rows if rows else 0, len(sql or ""))
        if tb == "visit":
            return (is_timeout, rows if rows else 0, len(sql or ""))
        return (is_timeout, rows if rows else 0, len(sql or ""))

    best = min(variants, key=key)
    return (best[0] or "").strip()


def pick_r4_winner(
    rollout_stats_list: List[Dict[str, Any]],
    clusters: dict,
    *,
    timeout_mode: str = "off",
    tiebreak: str = "rows",
) -> str:
    votes, timeout_sqls = collect_r4_votes(
        rollout_stats_list, clusters=clusters, mode=timeout_mode
    )
    if not votes:
        return ""
    ranked = votes.most_common()
    top_v = ranked[0][1]
    tied = [sig for sig, v in ranked if v == top_v]
    if len(tied) == 1:
        c = clusters.get(tied[0])
        if not c:
            return ""
        return tiebreak_variants(c.variants, timeout_sqls, mode=tiebreak)
    best_r, best_sql = -1.0, ""
    for sig in tied:
        c = clusters.get(sig)
        if not c:
            continue
        if tb := tiebreak_variants(c.variants, timeout_sqls, mode=tiebreak):
            rw = max((v[1] for v in c.variants), default=0.0)
            if tiebreak == "visit":
                score = float(c.total_visit)
            elif tiebreak == "reward":
                score = rw
            else:
                score = rw
            if score > best_r or (score == best_r and len(tb) < len(best_sql or "z")):
                best_r = score
                best_sql = tb
    return best_sql
