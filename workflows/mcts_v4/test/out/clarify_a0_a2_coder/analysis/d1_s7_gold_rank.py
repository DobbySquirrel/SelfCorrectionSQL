#!/usr/bin/env python3
"""D1: Gold cluster rank under R2 (total_visit) on 6 recall-only S7 qids."""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parents[6]
PAR = ROOT / "workflows/mcts_v4/test/out/clarify_a0_a2_coder/analysis/parallel_during_rerun"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(PAR))

import selector_replay as sr  # noqa: E402
from selector_replay import build_clusters, pick_r2, pick_r7, _high_reward_count  # noqa: E402

import _loaders as pld  # noqa: E402

SIX_QIDS = ["201", "263", "685", "1238", "1486", "1490"]
CALIB = ROOT / "workflows/mcts_v4/test/out/clarify_a0_a2_coder/v4_calib_s7_41_coder_rollouts8.json"
AUDIT = PAR / "recall_gap_analysis/s7_cluster_audit.json"
OUT_MD = Path(__file__).resolve().parent / "d1_s7_gold_rank.md"
OUT_JSON = Path(__file__).resolve().parent / "d1_s7_gold_rank.json"


def gold_sigs_in_pool(
    rss: List[dict], qid: str, gold_sqls: dict, qid_to_db: dict, cache: dict
) -> Set[str]:
    """Signatures of clusters that contain at least one SQL matching gold."""
    hits: Set[str] = set()
    for r in rss:
        for info in r.get("all_sql_variants") or []:
            sql = (info.get("sql") or "").strip()
            sig = info.get("result_signature") or ""
            if not sql or not sig:
                continue
            if sr.eval_hit1_sql(sql, qid, gold_sqls, qid_to_db, cache):
                hits.add(sig)
    return hits


def r2_ranked_clusters(clusters: Dict[str, sr.Cluster]) -> List[Tuple[int, str, sr.Cluster]]:
    ranked = sorted(clusters.items(), key=lambda x: (-x[1].total_visit, -x[1].total_count))
    return [(i + 1, sig, c) for i, (sig, c) in enumerate(ranked)]


def gold_rank(
    ranked: List[Tuple[int, str, sr.Cluster]], gold_sigs: Set[str]
) -> Tuple[Optional[int], List[int]]:
    ranks = [rank for rank, sig, _ in ranked if sig in gold_sigs]
    if not ranks:
        return None, []
    return min(ranks), ranks


def bucket_label(best_rank: Optional[int], n_clusters: int) -> str:
    if best_rank is None:
        return "no_gold_sig"
    if best_rank == 1:
        return "rank1_gold_missed_tiebreak"  # rare: gold top visit but wrong SQL picked
    if best_rank == 2:
        return "rank2_R7_candidate"
    if best_rank <= 5:
        return "rank3_5_need_new_signal"
    if best_rank >= n_clusters or best_rank >= 6:
        return "low_visit_tail"
    return "other"


def main():
    calib = json.loads(CALIB.read_text(encoding="utf-8"))
    prof = {
        str(p["qid"]): p
        for p in json.loads(AUDIT.read_text(encoding="utf-8"))["profiles"]
    }
    gold_sqls, qid_to_db = pld.load_gold_meta()
    cache: dict = {}

    rows = []
    rank2_count = 0
    rank35_count = 0
    tail_count = 0
    r7_hits = []

    lines = [
        "# D1 — S7 六题 gold cluster 在 R2 排序中的名次",
        "",
        f"Generated: {datetime.now().isoformat(timespec='seconds')}",
        "",
        "数据：`v4_calib_s7_41_coder_rollouts8.json`（calibrated 重跑，不重跑 MCTS）",
        "",
        "题集：recall✓ Hit@1✗ 的 6 题（不含已中的 1505）",
        "",
        "R2 排序键：`total_visit`（与 `pick_r2` 一致）。Gold cluster = 池内至少一条 SQL 与 gold 执行一致所属 signature。",
        "",
    ]

    for qid in SIX_QIDS:
        rec = calib.get(qid) or {}
        rss = rec.get("rollout_stats") or []
        clusters = build_clusters(rss)
        gold_sigs = gold_sigs_in_pool(rss, qid, gold_sqls, qid_to_db, cache)
        ranked = r2_ranked_clusters(clusters)
        best_rank, all_ranks = gold_rank(ranked, gold_sigs)
        label = bucket_label(best_rank, len(clusters))
        if label == "rank2_R7_candidate":
            rank2_count += 1
        elif label == "rank3_5_need_new_signal":
            rank35_count += 1
        elif label in ("low_visit_tail", "no_gold_sig"):
            tail_count += 1

        r2_sql = pick_r2(clusters)
        r7_sql = pick_r7(rss)
        r2_ok = sr.eval_hit1_sql(r2_sql, qid, gold_sqls, qid_to_db, cache) if r2_sql else False
        r7_ok = sr.eval_hit1_sql(r7_sql, qid, gold_sqls, qid_to_db, cache) if r7_sql else False
        if r7_ok:
            r7_hits.append(qid)

        hi = _high_reward_count(rss)
        case = prof.get(qid, {}).get("case", "?")

        lines += [
            f"## qid={qid} (case {case}, high_reward_rollouts={hi})",
            "",
            f"- **Gold rank (best)**: **{best_rank if best_rank else '—'}** / {len(clusters)} clusters"
            + (f" (all gold ranks: {all_ranks})" if len(all_ranks) > 1 else ""),
            f"- **Bucket**: `{label}`",
            f"- R2 pick hit gold: {r2_ok} | R7 pick hit gold: {r7_ok}",
            f"- R7 trigger (≥6 high-reward & ≥2 clusters): {hi >= 6 and len(clusters) >= 2}",
            "",
            "| R2 rank | sig | visit | size | max_r | gold? |",
            "|---:|---|---:|---:|---:|:---:|",
        ]
        for rank, sig, c in ranked:
            is_gold = "✓" if sig in gold_sigs else ""
            lines.append(
                f"| {rank} | `{sig[:12]}…` | {c.total_visit} | {c.total_count} | "
                f"{c.max_rollout_reward:.3f} | {is_gold} |"
            )
        lines.append("")

        rows.append(
            {
                "qid": qid,
                "case": case,
                "n_clusters": len(clusters),
                "gold_best_rank": best_rank,
                "gold_all_ranks": all_ranks,
                "bucket": label,
                "high_reward_rollouts": hi,
                "r2_hit": r2_ok,
                "r7_hit": r7_ok,
                "r7_would_trigger": hi >= 6 and len(clusters) >= 2,
            }
        )

    lines += [
        "## 汇总",
        "",
        "| Gold rank bucket | n | qids |",
        "|---|---:|---|",
    ]
    from collections import Counter

    bc = Counter(r["bucket"] for r in rows)
    for bucket, n in bc.most_common():
        qids = [r["qid"] for r in rows if r["bucket"] == bucket]
        lines.append(f"| `{bucket}` | {n} | {', '.join(qids)} |")

    lines += [
        "",
        f"- **rank 2（R7 候选）**: {rank2_count}/6",
        f"- **rank 3–5**: {rank35_count}/6",
        f"- **tail / 无 sig**: {tail_count}/6",
        "",
        f"- **R7 replay 在 6 题上 Hit@1**: {len(r7_hits)}/6 → {r7_hits or '—'}",
        "",
        "## D2 建议（基于上表）",
        "",
    ]
    if rank2_count >= 3:
        lines.append(
            "- **rank 2 居多** → 值得在「S7-detector + 池内有 gold」子集上试 **conditional R7**（勿全 498）。"
        )
    elif rank35_count >= 3:
        lines.append(
            "- **rank 3–5 / 散乱** → 简单 visit 重排不够；主菜转向 **clarification-as-constraint** 或 LLM judge。"
        )
    else:
        lines.append("- **混合** → conditional R7 小范围 A/B + 并行起草 constraint selector v0。")

    if r7_hits:
        lines.append(f"- 注：静态 R7 replay 已命中 {r7_hits}，与全 498 上 R7 -19 不矛盾（子集有效）。")
    else:
        lines.append("- 注：静态 R7 replay 在 6 题上 **0 额外命中** → conditional R7 预期有限，除非 rank 2 但 tiebreak/SQL 选错。")

    lines.append("")
    lines.append("不重跑 498；不扫 reward penalty。")

    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    OUT_JSON.write_text(json.dumps({"qids": SIX_QIDS, "rows": rows}, indent=2), encoding="utf-8")
    print(OUT_MD)
    print(f"rank2={rank2_count} rank35={rank35_count} r7_hits={r7_hits}")


if __name__ == "__main__":
    main()
