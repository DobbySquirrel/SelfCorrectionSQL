#!/usr/bin/env python3
"""D2a: trigger audit + R2 vs bare conditional R7 on calibrated S7 41q."""
from __future__ import annotations

import json
import sys
from collections import Counter
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

CALIB = ROOT / "workflows/mcts_v4/test/out/clarify_a0_a2_coder/v4_calib_s7_41_coder_rollouts8.json"
AUDIT = PAR / "recall_gap_analysis/s7_cluster_audit.json"
OUT_MD = Path(__file__).resolve().parent / "d2_s7_conditional_r7.md"
OUT_JSON = Path(__file__).resolve().parent / "d2_s7_conditional_r7.json"


def recall_pool(data: dict, qid: str, gold_sqls, qid_to_db, cache) -> bool:
    rec = data.get(qid) or {}
    if any(s.get("is_correct") for s in rec.get("all_sqls_with_attributes") or []):
        return True
    for r in rec.get("rollout_stats") or []:
        for info in r.get("all_sql_variants") or []:
            s = (info.get("sql") or "").strip()
            if s and sr.eval_hit1_sql(s, qid, gold_sqls, qid_to_db, cache):
                return True
    return False


def gold_sigs(rss: List[dict], qid: str, gold_sqls, qid_to_db, cache) -> Set[str]:
    out: Set[str] = set()
    for r in rss:
        for info in r.get("all_sql_variants") or []:
            sql = (info.get("sql") or "").strip()
            sig = info.get("result_signature") or ""
            if sql and sig and sr.eval_hit1_sql(sql, qid, gold_sqls, qid_to_db, cache):
                out.add(sig)
    return out


def gold_best_rank(clusters: Dict[str, sr.Cluster], gold_sigs: Set[str]) -> Optional[int]:
    if not gold_sigs or not clusters:
        return None
    ranked = sorted(clusters.items(), key=lambda x: (-x[1].total_visit, -x[1].total_count))
    for i, (sig, _) in enumerate(ranked, start=1):
        if sig in gold_sigs:
            return i
    return None


def r7_triggers(rss: List[dict]) -> bool:
    return _high_reward_count(rss) >= 6 and len(build_clusters(rss)) >= 2


def pick_bare_r7(rss: List[dict]) -> Tuple[str, bool]:
    """Bare pick_r7 — no visit-ratio guard (per D2a spec)."""
    if r7_triggers(rss):
        return pick_r7(rss), True
    return pick_r2(build_clusters(rss)), False


def hit(sql: str, qid: str, gold_sqls, qid_to_db, cache) -> bool:
    return bool(sql.strip()) and sr.eval_hit1_sql(sql, qid, gold_sqls, qid_to_db, cache)


def main():
    calib = json.loads(CALIB.read_text(encoding="utf-8"))
    prof = {str(p["qid"]): p for p in json.loads(AUDIT.read_text(encoding="utf-8"))["profiles"]}
    s7_qids = sorted(prof.keys(), key=int)
    gold_sqls, qid_to_db = pld.load_gold_meta()
    cache: dict = {}

    trigger_qids: List[str] = []
    trigger_gold_in: List[str] = []
    trigger_gold_out: List[str] = []
    rows = []

    for qid in s7_qids:
        rss = (calib.get(qid) or {}).get("rollout_stats") or []
        clusters = build_clusters(rss)
        gsigs = gold_sigs(rss, qid, gold_sqls, qid_to_db, cache)
        g_rank = gold_best_rank(clusters, gsigs)
        in_pool = recall_pool(calib, qid, gold_sqls, qid_to_db, cache)
        trig = r7_triggers(rss)

        if trig:
            trigger_qids.append(qid)
            (trigger_gold_in if in_pool else trigger_gold_out).append(qid)

        r2_sql = pick_r2(clusters)
        r7_sql, used_r7 = pick_bare_r7(rss)
        r2_ok = hit(r2_sql, qid, gold_sqls, qid_to_db, cache)
        r7_ok = hit(r7_sql, qid, gold_sqls, qid_to_db, cache)

        if r7_ok and not r2_ok:
            net = "+"
        elif r2_ok and not r7_ok:
            net = "hurt"
        elif r2_ok and r7_ok:
            net = "="
        else:
            net = "—"

        rows.append(
            {
                "qid": qid,
                "case": prof[qid].get("case"),
                "trigger": trig,
                "gold_in_pool": in_pool,
                "gold_cluster_rank": g_rank,
                "high_reward_n": _high_reward_count(rss),
                "n_clusters": len(clusters),
                "r2_hit": r2_ok,
                "r7_hit": r7_ok,
                "net_change": net,
            }
        )

    n = len(s7_qids)
    r2_hits = {r["qid"] for r in rows if r["r2_hit"]}
    r7_hits = {r["qid"] for r in rows if r["r7_hit"]}
    both = r2_hits & r7_hits
    r2_only = r2_hits - r7_hits
    r7_only = r7_hits - r2_hits
    neither = set(s7_qids) - r2_hits - r7_hits

    lines = [
        "# D2a — S7 41q: trigger audit + R2 vs bare conditional R7",
        "",
        f"Generated: {datetime.now().isoformat(timespec='seconds')}",
        "",
        "数据：`v4_calib_s7_41_coder_rollouts8.json`。R7 = 裸 `pick_r7`（**无** visit-ratio guard）。",
        "",
        "⚠️ S7 41 为 recall-lost 偏置子集；本表看 **趋势与安全性**，不外推全 498。",
        "",
        "---",
        "",
        "## 1. 触发器选择性（先于此看 Hit@1）",
        "",
        "条件：`high_reward_rollouts ≥ 6` **且** `≥2` result clusters（与 `pick_r7` 门控一致）。",
        "",
        f"| 指标 | 值 |",
        f"|---|---:|",
        f"| S7 全集 | {n} |",
        f"| **触发 N** | **{len(trigger_qids)}** |",
        f"| 触发 & gold 在池 | {len(trigger_gold_in)} |",
        f"| 触发 & gold 不在池 | {len(trigger_gold_out)} |",
        f"| 未触发 & gold 在池 | {len([q for q in s7_qids if q not in trigger_qids and recall_pool(calib,q,gold_sqls,qid_to_db,cache)])} |",
        f"| Calibrated recall（池内有 gold） | {sum(1 for q in s7_qids if recall_pool(calib,q,gold_sqls,qid_to_db,cache))}/{n} |",
        "",
    ]

    if len(trigger_qids) <= 12:
        lines.append(f"触发 qids: `{', '.join(trigger_qids)}`")
    else:
        lines.append(f"触发 qids ({len(trigger_qids)}): `{', '.join(trigger_qids[:15])}…`")
    lines.append("")

    if len(trigger_qids) <= 10:
        interp = "N ≈ recall 救回规模 → **触发器较准**，副作用面小。"
    elif len(trigger_qids) >= 25:
        interp = "N 很大 → 触发器在 S7 **大量激活**，需重点看 R7 误杀格。"
    else:
        interp = "N 中等 → 以混淆矩阵 **误杀 ≤ 救回** 为准。"

    lines.append(f"**解读**：{interp}")
    lines.append("")
    lines.extend(
        [
            "---",
            "",
            "## 2. Hit@1 汇总",
            "",
            "| Selector | Hit@1 |",
            "|---|---:|",
            f"| R2 replay | **{len(r2_hits)}/{n}** |",
            f"| Bare conditional R7 | **{len(r7_hits)}/{n}** |",
            f"| Net vs R2 | **{len(r7_only) - len(r2_only):+d}** (saved {len(r7_only)}, hurt {len(r2_only)}) |",
            "",
            "---",
            "",
            "## 3. 混淆矩阵（41 题，R2 × R7）",
            "",
            "|  | R7 hit | R7 miss |",
            "|--|---:|---:|",
            f"| **R2 hit** | {len(both)} | {len(r2_only)} |",
            f"| **R2 miss** | {len(r7_only)} | {len(neither)} |",
            "",
            "| 格 | n | qids | 含义 |",
            "|---|---:|---|---|",
            f"| R2✓ R7✓ | {len(both)} | {sorted(both, key=int) or '—'} | 两规则都对 |",
            f"| R2✓ R7✗ | {len(r2_only)} | {sorted(r2_only, key=int) or '—'} | **R7 误杀** |",
            f"| R2✗ R7✓ | {len(r7_only)} | {sorted(r7_only, key=int) or '—'} | **R7 救回** |",
            f"| R2✗ R7✗ | {len(neither)} | （略，见下表） | 共同失败 |",
            "",
        ]
    )

    safe = len(r7_only) >= len(r2_only)
    lines.append(
        f"**安全性**：R7 误杀 {len(r2_only)} vs 救回 {len(r7_only)} → "
        + ("✅ 误杀 ≤ 救回，conditional 在 S7 子集有价值。" if safe else "🛑 误杀 > 救回，裸 R7 不安全。")
    )
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 4. 逐题表")
    lines.append("")
    lines.append(
        "| qid | case | trigger? | gold池? | gold_rank | R2_hit | R7_hit | net |"
    )
    lines.append("|---|:---|:---:|:---:|:---|:---:|:---:|:---:|")
    for r in rows:
        gr = str(r["gold_cluster_rank"]) if r["gold_cluster_rank"] else "—"
        lines.append(
            f"| {r['qid']} | {r['case']} | {'Y' if r['trigger'] else 'N'} | "
            f"{'Y' if r['gold_in_pool'] else 'N'} | {gr} | "
            f"{'✓' if r['r2_hit'] else ''} | {'✓' if r['r7_hit'] else ''} | {r['net_change']} |"
        )

    lines += [
        "",
        "---",
        "",
        "## 5. D2 结论（仅 S7 41）",
        "",
    ]
    if len(r2_only) == 0 and len(r7_only) >= 4:
        lines.append(
            f"- ✅ 裸 conditional R7：**0 误杀**，救回 {len(r7_only)} 题 → 与 D1「rank2 占 4/6」一致。"
        )
        lines.append("- 下一步：在 **498 的 S7 标签子集** 做同口径 replay（仍不重跑），**勿**全库裸 R7。")
    elif safe:
        lines.append(f"- ⚠️ 有净收益但误杀存在：saved={sorted(r7_only,key=int)}, hurt={sorted(r2_only,key=int)}。")
    else:
        lines.append("- 🛑 误杀过多，需 guard 或放弃 visit-based R7。")

    lines += [
        "- **263 / 1486**：gold rank 3–4，本实验不塞规则 → 走 clarification-constraint。",
        "- 不重跑 498；不扫 reward penalty。",
        "",
    ]

    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    OUT_JSON.write_text(
        json.dumps(
            {
                "trigger_n": len(trigger_qids),
                "trigger_qids": trigger_qids,
                "trigger_gold_in_pool": trigger_gold_in,
                "trigger_gold_not_in_pool": trigger_gold_out,
                "confusion": {
                    "both": sorted(both, key=int),
                    "r2_only_hurt": sorted(r2_only, key=int),
                    "r7_only_saved": sorted(r7_only, key=int),
                    "neither": sorted(neither, key=int),
                },
                "rows": rows,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(OUT_MD)
    print(
        f"trigger={len(trigger_qids)}/{n} R2={len(r2_hits)} R7={len(r7_hits)} "
        f"saved={len(r7_only)} hurt={len(r2_only)}"
    )


if __name__ == "__main__":
    main()
