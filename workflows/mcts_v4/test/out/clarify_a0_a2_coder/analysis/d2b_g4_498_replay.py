#!/usr/bin/env python3
"""D2b: G4 + conditional R7 replay on 498 merged cache (non-oracle guard, locked G4)."""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parents[6]
PAR = ROOT / "workflows/mcts_v4/test/out/clarify_a0_a2_coder/analysis/parallel_during_rerun"
GAP = PAR / "recall_gap_analysis"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(PAR))
sys.path.insert(0, str(GAP))

import _loaders as pld  # noqa: E402
import selector_replay as sr  # noqa: E402
from selector_replay import (  # noqa: E402
    build_clusters,
    pick_r2,
    pick_r7,
    _high_reward_count,
)
from recall_lost_taxonomy import (  # noqa: E402
    classify_one,
    gold_complexity,
    load_ppl_index,
)

OUT_MD = Path(__file__).resolve().parent / "d2b_g4_498_replay.md"
OUT_JSON = Path(__file__).resolve().parent / "d2b_g4_498_replay.json"
BUCKETS = ["S6", "S4", "S3", "S1", "S2", "S7", "S5", "S0"]


def norm_sql_struct(sql: str) -> str:
    s = (sql or "").lower()
    s = re.sub(r"'[^']*'", "'?'", s)
    s = re.sub(r"\b\d+\b", "?", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:500]


def top_clusters(rss: List[dict]) -> Tuple[sr.Cluster, sr.Cluster]:
    c = build_clusters(rss)
    if not c:
        return sr.Cluster(""), sr.Cluster("")
    ranked = sorted(c.items(), key=lambda x: (-x[1].total_visit, -x[1].total_count))
    c1 = ranked[0][1]
    c2 = ranked[1][1] if len(ranked) > 1 else sr.Cluster("")
    return c1, c2


def struct_count(cluster: sr.Cluster) -> int:
    keys = {norm_sql_struct(sql) for sql, _, _ in cluster.variants if sql}
    return len(keys)


def r7_triggers(rss: List[dict]) -> bool:
    return _high_reward_count(rss) >= 6 and len(build_clusters(rss)) >= 2


def g4_protects(rss: List[dict]) -> bool:
    """Locked G4: top1_struct_keys >= 6 AND visit_ratio < 2 → keep R2."""
    c1, c2 = top_clusters(rss)
    v1, v2 = c1.total_visit, c2.total_visit
    if v2 <= 0:
        return False
    ratio = v1 / v2
    return struct_count(c1) >= 6 and ratio < 2.0


def pick_g4_r7(rss: List[dict]) -> Tuple[str, bool, bool]:
    """Returns (sql, trigger, g4_protect)."""
    clusters = build_clusters(rss)
    if not r7_triggers(rss):
        return pick_r2(clusters), False, False
    if g4_protects(rss):
        return pick_r2(clusters), True, True
    return pick_r7(rss), True, False


def eval_direct(
    sql: str,
    qid: str,
    gold_sqls: dict,
    qid_to_db: dict,
    memo: Optional[dict] = None,
) -> bool:
    """Per-SQL compare_with_gold; memo keyed by (qid, exact stripped sql), NOT norm_sql."""
    from workflows.mcts_v1.test.test_mcts import build_db_connector, compare_with_gold

    s = (sql or "").strip()
    if not s:
        return False
    key = (qid, s)
    if memo is not None:
        if key in memo:
            return memo[key]
    gs = gold_sqls.get(qid, "")
    db = qid_to_db.get(qid, "")
    if not gs or not db:
        ok = False
    else:
        conn = build_db_connector(db)
        try:
            ok = bool(compare_with_gold(s, gs, conn))
        finally:
            conn.disconnect()
    if memo is not None:
        memo[key] = ok
    return ok


def main() -> None:
    data = pld.load_merged_498()
    gold_sqls, qid_to_db = pld.load_gold_meta()
    ppl = load_ppl_index()
    qids = sorted(data.keys(), key=int)
    n = len(qids)
    memo: dict = {}

    bucket_map: Dict[str, str] = {}
    for qid in qids:
        primary, _, _ = classify_one(data[qid], gold_sqls.get(qid, ""), ppl.get(qid))
        bucket_map[qid] = primary

    rows = []
    for i, qid in enumerate(qids):
        rss = (data.get(qid) or {}).get("rollout_stats") or []
        clusters = build_clusters(rss)
        r2_sql = pick_r2(clusters)
        g4_sql, trig, g4_prot = pick_g4_r7(rss)
        r2_ok = eval_direct(r2_sql, qid, gold_sqls, qid_to_db, memo)
        g4_ok = eval_direct(g4_sql, qid, gold_sqls, qid_to_db, memo)
        c1, c2 = top_clusters(rss)
        v2 = c2.total_visit
        ratio = (c1.total_visit / v2) if v2 > 0 else None

        rows.append(
            {
                "qid": qid,
                "bucket": bucket_map[qid],
                "trigger": trig,
                "g4_protect": g4_prot,
                "r2_hit": r2_ok,
                "g4_hit": g4_ok,
                "top1_struct": struct_count(c1) if c1.sig else 0,
                "visit_ratio": round(ratio, 2) if ratio is not None else None,
                "sql_changed": (g4_sql.strip() != r2_sql.strip()),
            }
        )
        if (i + 1) % 50 == 0:
            print(f"[D2b] eval {i+1}/{n}", flush=True)

    r2_hits = sum(1 for r in rows if r["r2_hit"])
    g4_hits = sum(1 for r in rows if r["g4_hit"])
    saved = {r["qid"] for r in rows if r["g4_hit"] and not r["r2_hit"]}
    hurt = {r["qid"] for r in rows if r["r2_hit"] and not r["g4_hit"]}
    net = len(saved) - len(hurt)

    # Quadrants: trigger × R2 hit
    A = [r for r in rows if r["trigger"] and r["r2_hit"]]
    B = [r for r in rows if r["trigger"] and not r["r2_hit"]]
    C = [r for r in rows if not r["trigger"] and r["r2_hit"]]
    D = [r for r in rows if not r["trigger"] and not r["r2_hit"]]

    A_hurt = [r for r in A if not r["g4_hit"]]
    B_saved = [r for r in B if r["g4_hit"]]
    A_g4_prot = [r for r in A if r["g4_protect"]]
    A_r7_path = [r for r in A if r["trigger"] and not r["g4_protect"]]

    trig_n = sum(1 for r in rows if r["trigger"])
    g4_prot_n = sum(1 for r in rows if r["g4_protect"])

    # Per-bucket stats
    bucket_stats = {}
    for b in BUCKETS:
        sub = [r for r in rows if r["bucket"] == b]
        if not sub:
            continue
        bn = len(sub)
        tr = sum(1 for r in sub if r["trigger"])
        r2h = sum(1 for r in sub if r["r2_hit"])
        sv = {r["qid"] for r in sub if r["g4_hit"] and not r["r2_hit"]}
        ht = {r["qid"] for r in sub if r["r2_hit"] and not r["g4_hit"]}
        r2h_sub = [r for r in sub if r["r2_hit"]]
        ht_r2 = {r["qid"] for r in r2h_sub if not r["g4_hit"]}
        bucket_stats[b] = {
            "n": bn,
            "trigger_n": tr,
            "trigger_pct": round(100 * tr / bn, 1),
            "r2_hit": r2h,
            "saved": sorted(sv, key=int),
            "hurt": sorted(ht, key=int),
            "net": len(sv) - len(ht),
            "hurt_among_r2_hit": len(ht_r2),
            "hurt_rate_r2_hit_pct": round(100 * len(ht_r2) / len(r2h_sub), 1) if r2h_sub else 0.0,
        }

    ts = datetime.now().isoformat(timespec="seconds")
    lines = [
        "# D2b — G4 + conditional R7 on 498 merged cache",
        "",
        f"Generated: {ts}",
        "",
        "数据：`v4_final_498q` + ef2 rerun overlay。**不重跑 MCTS**；selector post-hoc replay。",
        "",
        "**G4（锁定）**：`top1_struct_keys ≥ 6` 且 `top1_visit/top2_visit < 2` → 保 R2；",
        "否则在 `high_reward≥6 ∧ ≥2 clusters` 时走 R7。",
        "",
        "评估：逐题 `compare_with_gold`；memo 仅 `(qid, exact_sql)`，**不用** `norm_sql` cache。",
        "",
        "---",
        "",
        "## 1. 主指标 — 全 498 G4+R7 vs R2",
        "",
        "| Selector | Hit@1 | vs R2 saved | vs R2 hurt | Net Δ Hit@1 |",
        "|---|---:|---:|---:|---:|",
        f"| R2 replay | **{r2_hits}/{n}** ({100*r2_hits/n:.1f}%) | — | — | — |",
        f"| **G4 + conditional R7** | **{g4_hits}/{n}** ({100*g4_hits/n:.1f}%) | {len(saved)} | {len(hurt)} | **{net:+d}** |",
        "",
        f"- **saved** ({len(saved)}): `{', '.join(sorted(saved, key=int)[:30])}{'…' if len(saved)>30 else ''}`",
        f"- **hurt** ({len(hurt)}): `{', '.join(sorted(hurt, key=int)[:30])}{'…' if len(hurt)>30 else ''}`",
        "",
        "⚠️ 本表为 **全 498 最终答案**；勿用 S7 41 子集类推。",
        "",
        "---",
        "",
        "## 2. 四象限（trigger × R2 hit）",
        "",
        "```",
        "                R2 hit              R2 miss",
        f"trigger      A={len(A):3d}              B={len(B):3d}",
        f"no trigger   C={len(C):3d}              D={len(D):3d}",
        "```",
        "",
        "| 象限 | n | 关键子集 | 含义 |",
        "|---|---:|---|---|",
        f"| **A** trigger & R2✓ | {len(A)} | hurt **{len(A_hurt)}** | R2 已对；G4+R7 改错 = **净亏** |",
        f"| **B** trigger & R2✗ | {len(B)} | saved **{len(B_saved)}** | R2 错；G4+R7 改对 = **净赚** |",
        f"| C | {len(C)} | hurt {sum(1 for r in C if not r['g4_hit'])} | 未触发，应等同 R2 |",
        f"| D | {len(D)} | saved {sum(1 for r in D if r['g4_hit'])} | 未触发，共同失败 |",
        "",
        f"| 触发器激活 | **{trig_n}/{n}** ({100*trig_n/n:.1f}%) | G4 保护（触发内） | **{g4_prot_n}** |",
        f"| A 内 G4 保护 | {len(A_g4_prot)}/{len(A)} | A 内走 R7 路径 | {len(A_r7_path)} |",
        "",
        f"**A 误杀 − B 救回 = {len(A_hurt)} − {len(B_saved)} = {len(A_hurt) - len(B_saved):+d}**（四象限口径净差额）",
        f"**全局 saved − hurt = {net:+d}**（与 Hit@1 净变化一致）",
        "",
    ]

    if A_hurt:
        lines.append(f"A 误杀 qids: `{', '.join(r['qid'] for r in sorted(A_hurt, key=lambda x: int(x['qid'])))}`")
        lines.append("")
    if B_saved:
        lines.append(f"B 救回 qids: `{', '.join(r['qid'] for r in sorted(B_saved, key=lambda x: int(x['qid'])))}`")
        lines.append("")

    lines += [
        "---",
        "",
        "## 3. 按 S0–S7 桶（classify_one on 全 498）",
        "",
        "关心 **R2 本来对** 的桶：`hurt_among_r2_hit` / `hurt_rate_r2_hit_pct`。",
        "",
        "| Bucket | n | trigger% | R2 hit | saved | hurt | net | hurt@R2✓ | hurt%@R2✓ |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for b in BUCKETS:
        st = bucket_stats.get(b)
        if not st:
            continue
        lines.append(
            f"| **{b}** | {st['n']} | {st['trigger_pct']}% | {st['r2_hit']} | "
            f"{len(st['saved'])} | {len(st['hurt'])} | {st['net']:+d} | "
            f"{st['hurt_among_r2_hit']} | {st['hurt_rate_r2_hit_pct']}% |"
        )

    lines += [
        "",
        "---",
        "",
        "## 4. 决策（仅基于全 498 replay）",
        "",
    ]
    if net > 0 and len(A_hurt) <= len(B_saved):
        lines.append(f"- ✅ 全 498 净 **+{net}** Hit@1；四象限 A 误杀 {len(A_hurt)} ≤ B 救回 {len(B_saved)}。")
    elif net >= 0:
        lines.append(f"- ⚠️ 全 498 净 **{net:+d}**；A 误杀 {len(A_hurt)} 需对照桶分布是否集中在 S7/S5。")
    else:
        lines.append(
            f"- 🛑 全 498 净 **{net:+d}**；G4 不足以抵消 R7 路径误杀（A={len(A_hurt)} vs B={len(B_saved)}）。"
        )
    lines.append("- 不重跑 498 MCTS；本结果为 selector 层最终答案。")
    lines.append("- G4 规则已锁定，本报告无 mid-run guard 试错。")
    lines.append("")

    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    payload = {
        "n": n,
        "r2_hit1": r2_hits,
        "g4_hit1": g4_hits,
        "saved_vs_r2": sorted(saved, key=int),
        "hurt_vs_r2": sorted(hurt, key=int),
        "net_vs_r2": net,
        "trigger_n": trig_n,
        "g4_protect_n": g4_prot_n,
        "quadrants": {
            "A_trigger_r2_hit": len(A),
            "B_trigger_r2_miss": len(B),
            "C_no_trigger_r2_hit": len(C),
            "D_no_trigger_r2_miss": len(D),
            "A_hurt": [r["qid"] for r in A_hurt],
            "B_saved": [r["qid"] for r in B_saved],
        },
        "bucket_stats": bucket_stats,
        "rows": rows,
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(OUT_MD)
    print(
        f"R2={r2_hits} G4+R7={g4_hits} net={net:+d} "
        f"trigger={trig_n} A_hurt={len(A_hurt)} B_saved={len(B_saved)}"
    )


if __name__ == "__main__":
    main()
