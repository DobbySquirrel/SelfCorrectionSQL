#!/usr/bin/env python3
"""Shared offline metrics for new30 plan experiment (R2/R3/gold_match/top-k)."""

from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

MCTS_V4 = Path(__file__).resolve().parents[5]
PAR = MCTS_V4 / "test/out/clarify_a0_a2_coder/analysis/parallel_during_rerun"
sys.path.insert(0, str(MCTS_V4))
sys.path.insert(0, str(PAR))

from utils.sql_selector import SQLSelector  # noqa: E402
import selector_replay as sr  # noqa: E402
from selector_replay import build_clusters, _high_reward_count  # noqa: E402


def sql_correct_map(rec: Dict[str, Any]) -> Dict[str, bool]:
    return {
        (a.get("sql") or "").strip(): bool(a.get("is_correct"))
        for a in (rec.get("all_sqls_with_attributes") or [])
        if (a.get("sql") or "").strip()
    }


def has_recall(rec: Dict[str, Any]) -> bool:
    return any(sql_correct_map(rec).values())


def pick(rec: Dict[str, Any], strategy: str, rss_key: str = "rollout_stats") -> str:
    rss = rec.get(rss_key) or []
    with redirect_stdout(io.StringIO()):
        return (SQLSelector.select(rss, strategy=strategy) or "").strip()


def hit1(rec: Dict[str, Any], strategy: str = "R3", rss_key: str = "rollout_stats") -> bool:
    sm = sql_correct_map(rec)
    return bool(sm.get(pick(rec, strategy, rss_key)))


def gold_sigs(rec: Dict[str, Any], rss_key: str = "rollout_stats") -> set:
    sm = sql_correct_map(rec)
    out = set()
    for rs in rec.get(rss_key) or []:
        for v in rs.get("all_sql_variants") or []:
            sql = (v.get("sql") or "").strip()
            sig = v.get("result_signature") or ""
            if sql and sig and sm.get(sql):
                out.add(sig)
        sel = (rs.get("selected_sql") or "").strip()
        if sel and sm.get(sel):
            for v in rs.get("all_sql_variants") or []:
                if (v.get("sql") or "").strip() == sel:
                    sig = v.get("result_signature") or ""
                    if sig:
                        out.add(sig)
    return out


def r3_ranked_clusters(rec: Dict[str, Any], rss_key: str = "rollout_stats") -> List[Tuple[str, sr.Cluster]]:
    clusters = build_clusters(rec.get(rss_key) or [])
    return sorted(
        clusters.items(),
        key=lambda x: -(x[1].max_rollout_reward * max(1, x[1].total_count)),
    )


def gold_cluster_rank(rec: Dict[str, Any], rss_key: str = "rollout_stats") -> Optional[int]:
    gs = gold_sigs(rec, rss_key)
    if not gs:
        return None
    ranked = r3_ranked_clusters(rec, rss_key)
    for i, (sig, _) in enumerate(ranked, start=1):
        if sig in gs:
            return i
    return None


def hit_at_k(rec: Dict[str, Any], k: int, strategy: str = "R3", rss_key: str = "rollout_stats") -> bool:
    gs = gold_sigs(rec, rss_key)
    if not gs:
        return False
    if strategy == "R3":
        ranked = r3_ranked_clusters(rec, rss_key)
        top_sigs = {sig for sig, _ in ranked[:k]}
        return bool(gs & top_sigs)
    # R2: rank by visit
    clusters = build_clusters(rec.get(rss_key) or [])
    ranked = sorted(clusters.items(), key=lambda x: -x[1].total_visit)
    top_sigs = {sig for sig, _ in ranked[:k]}
    return bool(gs & top_sigs)


def gold_rank_bucket(rank: Optional[int]) -> str:
    if rank is None:
        return "not_in_pool"
    if rank == 1:
        return "1"
    if rank <= 3:
        return "2-3"
    if rank <= 8:
        return "4-8"
    return "9+"


def s7_like_trigger(rec: Dict[str, Any], rss_key: str = "rollout_stats") -> bool:
    rss = rec.get(rss_key) or []
    return _high_reward_count(rss) >= 6 and len(build_clusters(rss)) >= 2


def eval_record(rec: Dict[str, Any], rss_key: str = "rollout_stats") -> Dict[str, Any]:
    rec_ = dict(rec)
    if rss_key != "rollout_stats":
        rec_["rollout_stats"] = rec.get(rss_key) or []
    r = has_recall(rec_)
    h1 = hit1(rec_, "R3")
    h2 = hit1(rec_, "R2")
    gm = bool(rec_.get("stats", {}).get("gold_match"))
    if not gm:
        gm = hit1(rec_, "R3")  # fallback to pool label
    rank = gold_cluster_rank(rec_, "rollout_stats")
    return {
        "recall": r,
        "hit1_r3": h1,
        "hit1_r2": h2,
        "gold_match": gm,
        "hit3": hit_at_k(rec_, 3),
        "hit5": hit_at_k(rec_, 5),
        "hit8": hit_at_k(rec_, 8),
        "gold_cluster_rank": rank,
        "gold_rank_bucket": gold_rank_bucket(rank),
        "selection_efficiency": (h1 / r) if r else 0.0,
        "s7_like": s7_like_trigger(rec_),
    }
