#!/usr/bin/env python3
"""S7 (41 q) cluster distribution audit — read-only."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Set, Tuple

ROOT = Path(__file__).resolve().parents[8]
OUT_BASE = Path(__file__).resolve().parents[3]
OUT_DIR = Path(__file__).resolve().parent
TAX = OUT_DIR / "recall_lost_75_taxonomy.json"
FINAL = OUT_BASE / "v4_final_498q_coder_rollouts8.json"
EF2 = OUT_BASE / "qids_ef2_51.json"
EF2_RERUN = OUT_BASE / "v4_ef2_51_rerun_coder_rollouts8.json"


def load_merged() -> dict:
    fin = json.loads(FINAL.read_text(encoding="utf-8"))
    ef2 = {str(q) for q in json.loads(EF2.read_text()).get("qids", [])}
    if EF2_RERUN.exists():
        rerun = json.loads(EF2_RERUN.read_text(encoding="utf-8"))
        for q in ef2:
            fin[str(q)] = rerun[str(q)]
    return fin


def rollout_result_sigs(rec: dict) -> List[str]:
    """Per-rollout dominant result signature (selected path)."""
    sigs = []
    for r in rec.get("rollout_stats") or []:
        rb = r.get("result_buckets") or {}
        if rb:
            best = max(rb.items(), key=lambda kv: kv[1] if isinstance(kv[1], (int, float)) else 0)
            sigs.append(str(best[0])[:32])
        else:
            sigs.append("empty")
    return sigs


def depth1_sigs(rec: dict) -> Set[str]:
    sigs: Set[str] = set()
    for r in rec.get("rollout_stats") or []:
        nodes = r.get("cte_buckets_per_node") or []
        if not nodes:
            continue
        for b in nodes[0].get("buckets") or []:
            s = b.get("result_signature_v2") or b.get("result_signature") or ""
            if s:
                sigs.add(s[:32])
            elif b.get("cluster_id") is not None:
                sigs.add(f"cid_{b['cluster_id']}")
    return sigs


def cluster_profile(rec: dict) -> dict:
    rss = rec.get("rollout_stats") or []
    r_sigs = rollout_result_sigs(rec)
    uniq_result = len(set(r_sigs))
    d1 = depth1_sigs(rec)
    rewards = [float(r.get("reward", 0)) for r in rss]
    visits = [int(r.get("leaf_visit_count") or r.get("visit_counts", [0])[-1] if isinstance(r.get("visit_counts"), list) else 0) for r in rss]
    # global result buckets union across rollouts
    all_rb_sigs: Set[str] = set()
    for r in rss:
        for k in (r.get("result_buckets") or {}):
            all_rb_sigs.add(str(k)[:32])

    same_result_cluster = uniq_result == 1 and r_sigs[0] != "empty"
    high_reward = sum(1 for x in rewards if x >= 0.99)

    # Case typing
    if same_result_cluster and len(rss) >= 6:
        case = "A"
    elif uniq_result <= 3:
        case = "B"
    elif uniq_result >= 4:
        case = "C"
    else:
        case = "?"

    return {
        "n_rollouts": len(rss),
        "uniq_rollout_result_clusters": uniq_result,
        "uniq_union_result_buckets": len(all_rb_sigs),
        "depth1_distinct": len(d1),
        "high_reward_rollouts": high_reward,
        "rewards": rewards,
        "same_result_cluster": same_result_cluster,
        "case": case,
        "rollout_sigs": r_sigs,
    }


def main() -> None:
    tax = json.loads(TAX.read_text(encoding="utf-8"))
    s7 = [r for r in tax["rows"] if r["primary"] == "S7"]
    merged = load_merged()

    profiles = []
    case_cnt = Counter()
    for row in s7:
        qid = row["qid"]
        p = cluster_profile(merged[qid])
        p["qid"] = qid
        p["db_id"] = row["db_id"]
        p["d1_clusters_r1"] = row.get("d1_clusters")
        case_cnt[p["case"]] += 1
        profiles.append(p)

    # Refine: homogeneous wrong (A strict): same result + >=6 high reward
    strict_a = sum(
        1
        for p in profiles
        if p["same_result_cluster"] and p["high_reward_rollouts"] >= 6
    )
    one_cluster_8 = sum(1 for p in profiles if p["uniq_rollout_result_clusters"] == 1)
    le3_clusters = sum(1 for p in profiles if p["uniq_rollout_result_clusters"] <= 3)
    ge4_clusters = sum(1 for p in profiles if p["uniq_rollout_result_clusters"] >= 4)
    d1_le2 = sum(1 for p in profiles if p["depth1_distinct"] <= 2)

    ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    lines = [
        "# S7 Cluster Distribution Audit (41 q)",
        "",
        f"Generated: {ts}",
        "",
        "Population: recall-lost 75 中 **primary=S7**（8 rollout 中 ≥6 个 reward≥0.99 且 recall=False）。",
        "",
        "## 1. 情况 A/B/C 归类（按 rollout 终态 result cluster）",
        "",
        "| 情况 | 定义 | n | % |",
        "|---|---|---:|---:|",
        f"| **A** | 8 rollout **同一** result cluster（终态签名相同） | {one_cluster_8} | {100*one_cluster_8/41:.1f}% |",
        f"| **A′** | A 且 ≥6 rollout reward≥0.99（S7 规则核心） | {strict_a} | {100*strict_a/41:.1f}% |",
        f"| **B** | 2–3 个错 cluster（终态） | {sum(1 for p in profiles if 2 <= p['uniq_rollout_result_clusters'] <= 3)} | {100*sum(1 for p in profiles if 2 <= p['uniq_rollout_result_clusters'] <= 3)/41:.1f}% |",
        f"| **C** | ≥4 个终态 cluster（搜索分散但都错） | {ge4_clusters} | {100*ge4_clusters/41:.1f}% |",
        "",
        "## 2. Depth-1 CTE 多样性（与 R1 S2 规则同源）",
        "",
        f"| depth-1 distinct clusters ≤2 | {d1_le2}/41 ({100*d1_le2/41:.1f}%) |",
        f"| depth-1 distinct clusters =1 | {sum(1 for p in profiles if p['depth1_distinct']<=1)}/41 |",
        "",
        "解读：depth-1 同质化 ≠ 终态同一 cluster；候选 4 应对的是 **终态 trapped in one wrong cluster**（A/A′）。",
        "",
        "## 3. 情况 D（recall-lost 池内）",
        "",
        "本 41 题 **oracle recall=False** → 任一路径 `selected_sql` 均未 gold match。",
        "**不存在**「有正确 cluster 仅 visit 低被 R2 忽略」——那是 selection-only（已在 recall✓ 池）。",
        "",
        "## 4. 候选 4 对症度",
        "",
    ]
    if one_cluster_8 >= 25:
        lines.append(f"- **{one_cluster_8}/41** 终态单 cluster → 候选 4（force rerun / 提 temperature）**最对症**。")
    if ge4_clusters >= 10:
        lines.append(f"- **{ge4_clusters}/41** 已 ≥4 cluster → 更像搜索已探索但全错，rerun 边际可能低（仍可能出新 signature）。")
    if d1_le2 >= 30:
        lines.append(f"- depth-1 ≤2 占 **{d1_le2}/41** → 与「局部最优/同质」叙事一致，支持同质检测触发 extra rollouts。")

    lines += [
        "",
        "## 5. 样例（A′ 单 cluster + 高 reward）",
        "",
    ]
    examples = [p for p in profiles if p["same_result_cluster"] and p["high_reward_rollouts"] >= 6][:8]
    for p in examples:
        lines.append(
            f"- q{p['qid']} ({p['db_id']}): rollout_clusters=1, d1={p['depth1_distinct']}, "
            f"high_r={p['high_reward_rollouts']}/8"
        )

    lines += [
        "",
        "## 6. 全量 qid × cluster 数",
        "",
        "| qid | db | rollout_clusters | union_buckets | d1 | high_r | case |",
        "|---:|---|---:|---:|---:|---:|---|",
    ]
    for p in sorted(profiles, key=lambda x: int(x["qid"])):
        lines.append(
            f"| {p['qid']} | {p['db_id']} | {p['uniq_rollout_result_clusters']} | "
            f"{p['uniq_union_result_buckets']} | {p['depth1_distinct']} | "
            f"{p['high_reward_rollouts']} | {p['case']} |"
        )

    out_md = OUT_DIR / "s7_cluster_audit.md"
    out_json = OUT_DIR / "s7_cluster_audit.json"
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    out_json.write_text(
        json.dumps(
            {
                "n_s7": 41,
                "one_cluster_8": one_cluster_8,
                "strict_a": strict_a,
                "le3_clusters": le3_clusters,
                "ge4_clusters": ge4_clusters,
                "d1_le2": d1_le2,
                "profiles": profiles,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(out_md)
    print(f"A(one cluster)={one_cluster_8} A'={strict_a} B(2-3)={le3_clusters-one_cluster_8} C(4+)={ge4_clusters} d1<=2={d1_le2}")


if __name__ == "__main__":
    main()
