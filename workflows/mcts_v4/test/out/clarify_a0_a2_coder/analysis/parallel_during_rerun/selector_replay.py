#!/usr/bin/env python3
"""Oracle-free selector replay on frozen rollout_stats JSON."""
from __future__ import annotations

import io
import sys
from collections import Counter
from contextlib import redirect_stdout
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

RULES = [
    "R0_max_reward",
    "R1_max_cluster_size",
    "R2_max_cluster_visit",
    "R3_reward_x_size",
    "R4_majority_then_reward",
    "R5_max_cluster_then_visit",
    "R6a_s7_fallback_r2",
    "R7_r2_second_cluster",
]

RULES_R67 = ["R0_max_reward", "R2_max_cluster_visit", "R6a_s7_fallback_r2", "R7_r2_second_cluster"]


@dataclass
class Cluster:
    sig: str
    total_count: int = 0
    total_visit: int = 0
    variants: List[Tuple[str, float, int]] = field(default_factory=list)

    @property
    def max_rollout_reward(self) -> float:
        return max((v[1] for v in self.variants), default=0.0)


def _tiebreak_pick(variants: List[Tuple[str, float, int]]) -> str:
    from workflows.mcts_v4.utils.execution_tiebreak import tiebreak_pick_variants

    return tiebreak_pick_variants(variants)


def build_clusters(rss: List[dict]) -> Dict[str, Cluster]:
    clusters: Dict[str, Cluster] = {}
    for r in rss:
        rb = r.get("result_buckets") or {}
        rw = float(r.get("reward", 0.0))
        leaf_v = int(r.get("leaf_visit_count") or 0)
        if not leaf_v and r.get("visit_counts"):
            leaf_v = int((r.get("visit_counts") or [0])[-1] or 0)
        for sig, cnt in rb.items():
            if not sig:
                continue
            c = clusters.setdefault(sig, Cluster(sig=sig))
            c.total_count += int(cnt)
            c.total_visit += leaf_v
        for v in r.get("all_sql_variants") or []:
            sig = v.get("result_signature") or ""
            if not sig:
                continue
            c = clusters.setdefault(sig, Cluster(sig=sig))
            rows = int(v.get("result_row_count") or 0) if v.get("valid") else 0
            c.variants.append((v.get("sql", ""), rw, rows))
    return clusters


def pick_r0(rss: List[dict]) -> str:
    try:
        from workflows.mcts_v1.utils.sql_selector import SQLSelector

        with redirect_stdout(io.StringIO()):
            return SQLSelector.select_by_highest_reward(rss)
    except Exception:
        valid = [r for r in rss if r.get("result_buckets")]
        if not valid:
            fb = [r for r in rss if (r.get("selected_sql") or "").strip()]
            if not fb:
                return ""
            mr = max(float(r.get("reward", 0)) for r in fb)
            return (next(r for r in fb if abs(float(r.get("reward", 0)) - mr) < 1e-6).get("selected_sql") or "").strip()
        mr = max(float(r.get("reward", 0)) for r in valid)
        top = [r for r in valid if abs(float(r.get("reward", 0)) - mr) < 1e-6]
        clusters = build_clusters(top)
        if not clusters:
            return ""
        best_sig = max(top[0]["result_buckets"], key=top[0]["result_buckets"].get)
        return _tiebreak_pick(clusters.get(best_sig, Cluster(best_sig)).variants)


def pick_r1(clusters: Dict[str, Cluster]) -> str:
    if not clusters:
        return ""
    best_sig = max(clusters, key=lambda s: clusters[s].total_count)
    return _tiebreak_pick(clusters[best_sig].variants)


def pick_r2(clusters: Dict[str, Cluster]) -> str:
    if not clusters:
        return ""
    best_sig = max(clusters, key=lambda s: clusters[s].total_visit)
    return _tiebreak_pick(clusters[best_sig].variants)


def pick_r3(clusters: Dict[str, Cluster]) -> str:
    if not clusters:
        return ""
    best_sig = max(
        clusters,
        key=lambda s: clusters[s].max_rollout_reward * max(1, clusters[s].total_count),
    )
    return _tiebreak_pick(clusters[best_sig].variants)


def pick_r4(rss: List[dict], clusters: Dict[str, Cluster]) -> str:
    votes: Counter = Counter()
    for r in rss:
        rb = r.get("result_buckets") or {}
        if not rb:
            continue
        mc = max(rb.values())
        for sig, c in rb.items():
            if c == mc:
                votes[sig] += 1
    if not votes:
        return pick_r0(rss)
    top_v = votes.most_common(1)[0][1]
    tied = [s for s, v in votes.items() if v == top_v]
    if len(tied) == 1:
        return _tiebreak_pick(clusters[tied[0]].variants)
    best_r, best_sql = -1.0, ""
    for sig in tied:
        c = clusters.get(sig)
        if not c:
            continue
        if c.max_rollout_reward > best_r:
            best_r = c.max_rollout_reward
            best_sql = _tiebreak_pick(c.variants)
    return best_sql


def pick_r5(clusters: Dict[str, Cluster]) -> str:
    if not clusters:
        return ""
    best_sig = max(clusters, key=lambda s: clusters[s].total_count)
    return _tiebreak_pick(sorted(clusters[best_sig].variants, key=lambda x: -x[1]))


def _high_reward_count(rss: List[dict]) -> int:
    return sum(1 for r in rss if float(r.get("reward", 0)) >= 0.99)


def pick_r6a(rss: List[dict]) -> str:
    """S7-detector: high reward consensus → fall back to R2; else R0."""
    clusters = build_clusters(rss)
    if _high_reward_count(rss) >= 6:
        return pick_r2(clusters)
    return pick_r0(rss)


def pick_r7(rss: List[dict]) -> str:
    """R2 baseline + S7-aware: ≥6 high-reward → pick 2nd cluster by visit."""
    clusters = build_clusters(rss)
    if _high_reward_count(rss) >= 6 and len(clusters) >= 2:
        ranked = sorted(clusters.items(), key=lambda x: -x[1].total_visit)
        return _tiebreak_pick(ranked[1][1].variants)
    return pick_r2(clusters)


def select_sql(rule: str, rss: List[dict]) -> str:
    clusters = build_clusters(rss)
    if rule == "R0_max_reward":
        return pick_r0(rss)
    if rule == "R1_max_cluster_size":
        return pick_r1(clusters)
    if rule == "R2_max_cluster_visit":
        return pick_r2(clusters)
    if rule == "R3_reward_x_size":
        return pick_r3(clusters)
    if rule == "R4_majority_then_reward":
        return pick_r4(rss, clusters)
    if rule == "R5_max_cluster_then_visit":
        return pick_r5(clusters)
    if rule == "R6a_s7_fallback_r2":
        return pick_r6a(rss)
    if rule == "R7_r2_second_cluster":
        return pick_r7(rss)
    return ""


def eval_hit1_sql(sql: str, qid: str, gold_sqls: dict, qid_to_db: dict, cache: dict) -> bool:
    import _loaders as pld

    if not sql.strip():
        return False
    key = (qid, pld.norm_sql(sql))
    if key in cache:
        return cache[key]
    try:
        from workflows.mcts_v1.test.test_mcts import build_db_connector, compare_with_gold

        db = qid_to_db.get(qid, "")
        gs = gold_sqls.get(qid, "")
        if not db or not gs:
            cache[key] = False
            return False
        conn = build_db_connector(db)
        try:
            ok = compare_with_gold(sql, gs, conn)
        finally:
            conn.disconnect()
        cache[key] = ok
        return ok
    except Exception:
        cache[key] = False
        return False


def replay_dataset(
    data: dict,
    qids: List[str],
    gold_sqls: dict,
    qid_to_db: dict,
    recall_map: Optional[dict],
    rules: Optional[List[str]] = None,
    ref_rule: str = "R0_max_reward",
    alt_ref: Optional[str] = None,
) -> Dict[str, dict]:
    rules = rules or RULES
    cache: dict = {}
    per_rule = {r: {"hit": 0, "saved": set(), "hurt": set()} for r in rules}
    ref_hits: Dict[str, bool] = {}
    alt_hits: Dict[str, bool] = {}

    for qid in qids:
        rss = (data.get(qid) or {}).get("rollout_stats") or []
        hits = {rule: eval_hit1_sql(select_sql(rule, rss), qid, gold_sqls, qid_to_db, cache) for rule in rules}
        ref_hits[qid] = hits.get(ref_rule, False)
        if alt_ref:
            alt_hits[qid] = hits.get(alt_ref, False)
        for rule in rules:
            if hits[rule]:
                per_rule[rule]["hit"] += 1
            if alt_ref and rule not in (ref_rule, alt_ref):
                if hits[rule] and not alt_hits[qid]:
                    per_rule[rule]["saved"].add(qid)
                if alt_hits[qid] and not hits[rule]:
                    per_rule[rule]["hurt"].add(qid)
            elif rule != ref_rule:
                if hits[rule] and not ref_hits[qid]:
                    per_rule[rule]["saved"].add(qid)
                if ref_hits[qid] and not hits[rule]:
                    per_rule[rule]["hurt"].add(qid)

    n = len(qids)
    recall_n = sum(recall_map.get(q, False) for q in qids) if recall_map else None
    out = {}
    for rule in rules:
        pr = per_rule[rule]
        entry = {
            "hit1": pr["hit"],
            "hit1_pct": 100 * pr["hit"] / n if n else 0,
            "recall": recall_n,
            "saved_qids": sorted(pr["saved"], key=int),
            "hurt_qids": sorted(pr["hurt"], key=int),
        }
        if alt_ref and rule not in (ref_rule, alt_ref):
            entry["saved_vs_r2"] = len(pr["saved"])
            entry["hurt_vs_r2"] = len(pr["hurt"])
            entry["net_vs_r2"] = len(pr["saved"]) - len(pr["hurt"])
        else:
            entry["saved_vs_r0"] = len(pr["saved"])
            entry["hurt_vs_r0"] = len(pr["hurt"])
            entry["net_vs_r0"] = len(pr["saved"]) - len(pr["hurt"])
        out[rule] = entry
    return out
