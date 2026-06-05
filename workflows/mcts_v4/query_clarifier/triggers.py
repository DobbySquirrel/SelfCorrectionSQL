"""Trigger logic and node_stats aggregation for AutoClarify."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

from workflows.mcts_v4.query_clarifier.config import (
    clarify_enabled,
    high_reward_threshold,
    max_clarify_per_run,
)
from workflows.mcts_v4.query_clarifier.schemas import ClusterSummary, NodeStats
from workflows.mcts_v4.utils.sql_selector import SQLSelector

_run_clarify_count = 0


def reset_run_clarify_count() -> None:
    global _run_clarify_count
    _run_clarify_count = 0


def get_run_clarify_count() -> int:
    return _run_clarify_count


def increment_run_clarify_count() -> None:
    global _run_clarify_count
    _run_clarify_count += 1


def run_budget_exhausted() -> bool:
    return _run_clarify_count >= max_clarify_per_run()


def norm_sql_struct(sql: str) -> str:
    s = (sql or "").lower()
    s = re.sub(r"'[^']*'", "'?'", s)
    s = re.sub(r"\b\d+\b", "?", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:500]


def build_node_stats(
    rollout_stats_list: List[Dict[str, Any]],
    *,
    exhaustion_triggered: bool = False,
) -> NodeStats:
    threshold = high_reward_threshold()
    high = sum(1 for r in rollout_stats_list if float(r.get("reward", 0)) >= threshold)
    clusters = SQLSelector._build_clusters(rollout_stats_list)
    ranked = sorted(clusters.values(), key=lambda c: (-c.total_visit, -c.total_count))
    top1 = ranked[0].total_visit if ranked else 0
    top2 = ranked[1].total_visit if len(ranked) > 1 else 0
    return NodeStats(
        high_reward_rollouts=high,
        n_nonempty_result_buckets=len(clusters),
        top1_visit=top1,
        top2_visit=top2,
        exhaustion_triggered=exhaustion_triggered,
    )


def build_cluster_summaries(rollout_stats_list: List[Dict[str, Any]], top_k: int = 3) -> List[ClusterSummary]:
    clusters = SQLSelector._build_clusters(rollout_stats_list)
    ranked: List[Tuple[str, Any]] = sorted(
        clusters.items(),
        key=lambda x: (-x[1].total_visit, -x[1].max_rollout_reward),
    )[:top_k]
    out: List[ClusterSummary] = []
    for rank, (sig, cl) in enumerate(ranked, start=1):
        rep_sql = ""
        best_reward = -1.0
        for sql, rw, _ in cl.variants:
            if rw > best_reward and sql:
                best_reward = rw
                rep_sql = sql
        if not rep_sql and cl.variants:
            rep_sql = cl.variants[0][0]
        out.append(
            ClusterSummary(
                rank=rank,
                visit=cl.total_visit,
                reward=cl.max_rollout_reward,
                representative_sql=rep_sql,
                result_signature=sig,
                structure_signature=norm_sql_struct(rep_sql),
            )
        )
    return out


def should_clarify(node_stats: NodeStats) -> Tuple[bool, str]:
    if not clarify_enabled():
        return False, "disabled"
    if run_budget_exhausted():
        return False, "run_budget_exhausted"
    hr = node_stats.high_reward_rollouts
    nc = node_stats.n_nonempty_result_buckets
    if hr >= 6 and nc >= 2:
        return True, "high_reward>=6 && clusters>=2"
    if node_stats.exhaustion_triggered and nc >= 2:
        return True, "exhaustion && clusters>=2"
    return False, "no_trigger"
