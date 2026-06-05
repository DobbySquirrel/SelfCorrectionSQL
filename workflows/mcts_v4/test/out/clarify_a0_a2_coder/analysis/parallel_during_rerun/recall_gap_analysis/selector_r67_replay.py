#!/usr/bin/env python3
"""R6a + R7 selector replay on 498 merged + S7 subset + 30q sanity."""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

PAR = Path(__file__).resolve().parent.parent  # parallel_during_rerun
GAP = Path(__file__).resolve().parent
sys.path.insert(0, str(PAR))
import _loaders as pld  # noqa: E402
import selector_replay as sr  # noqa: E402

RULES4 = sr.RULES_R67
S7_JSON = GAP / "s7_cluster_audit.json"
RECALL_MAP = GAP / "recall_map_498_merged.json"
CACHE498 = PAR / "unified_quality_selector" / "selector_replay_498_merged_cache.json"
CACHE30 = PAR / "selector_replay_cache.json"


def _case_type(qid: str, profiles: Dict[str, dict]) -> str:
    p = profiles.get(qid, {})
    uc = p.get("uniq_rollout_result_clusters", 0)
    if uc == 1:
        return "A"
    if uc <= 3:
        return "B"
    return "C"


def _r2_hit_from_r0_cache(qid: str, r0_hit: bool, saved: set, hurt: set) -> bool:
    return (r0_hit and qid not in hurt) or (qid in saved)


def replay_with_refs(
    data: dict,
    qids: List[str],
    gold_sqls: dict,
    qid_to_db: dict,
    use_stored_r0: bool = False,
    r2_saved: Optional[set] = None,
    r2_hurt: Optional[set] = None,
) -> Dict[str, dict]:
    cache: dict = {}
    picks: Dict[str, Dict[str, str]] = {r: {} for r in RULES4}
    for qid in qids:
        rss = data[qid].get("rollout_stats") or []
        for rule in RULES4:
            picks[rule][qid] = sr.select_sql(rule, rss)

    hits: Dict[str, Dict[str, bool]] = {r: {} for r in RULES4}
    saved = r2_saved or set()
    hurt = r2_hurt or set()
    for qid in qids:
        if use_stored_r0:
            hits["R0_max_reward"][qid] = pld.hit1(data[qid])
        else:
            hits["R0_max_reward"][qid] = sr.eval_hit1_sql(
                picks["R0_max_reward"][qid], qid, gold_sqls, qid_to_db, cache
            )
        if r2_saved is not None and r2_hurt is not None:
            hits["R2_max_cluster_visit"][qid] = _r2_hit_from_r0_cache(
                qid, hits["R0_max_reward"][qid], saved, hurt
            )
        else:
            hits["R2_max_cluster_visit"][qid] = sr.eval_hit1_sql(
                picks["R2_max_cluster_visit"][qid], qid, gold_sqls, qid_to_db, cache
            )
        for rule in ("R6a_s7_fallback_r2", "R7_r2_second_cluster"):
            ns = pld.norm_sql(picks[rule][qid])
            if ns == pld.norm_sql(picks["R2_max_cluster_visit"][qid]):
                hits[rule][qid] = hits["R2_max_cluster_visit"][qid]
            elif rule == "R6a_s7_fallback_r2" and ns == pld.norm_sql(picks["R0_max_reward"][qid]):
                hits[rule][qid] = hits["R0_max_reward"][qid]
            else:
                hits[rule][qid] = sr.eval_hit1_sql(
                    picks[rule][qid], qid, gold_sqls, qid_to_db, cache
                )

    n = len(qids)
    out = {}
    for rule in RULES4:
        h = sum(1 for q in qids if hits[rule][q])
        saved_r0 = {q for q in qids if hits[rule][q] and not hits["R0_max_reward"][q]}
        hurt_r0 = {q for q in qids if hits["R0_max_reward"][q] and not hits[rule][q]}
        saved_r2 = {q for q in qids if hits[rule][q] and not hits["R2_max_cluster_visit"][q]}
        hurt_r2 = {q for q in qids if hits["R2_max_cluster_visit"][q] and not hits[rule][q]}
        out[rule] = {
            "hit1": h,
            "hit1_pct": 100 * h / n if n else 0,
            "saved_vs_r0": len(saved_r0),
            "hurt_vs_r0": len(hurt_r0),
            "net_vs_r0": len(saved_r0) - len(hurt_r0),
            "saved_vs_r2": len(saved_r2),
            "hurt_vs_r2": len(hurt_r2),
            "net_vs_r2": len(saved_r2) - len(hurt_r2),
            "saved_qids_vs_r0": sorted(saved_r0, key=int),
            "hurt_qids_vs_r0": sorted(hurt_r0, key=int),
            "saved_qids_vs_r2": sorted(saved_r2, key=int),
            "hurt_qids_vs_r2": sorted(hurt_r2, key=int),
        }
    return out


def render_498_md(res: dict) -> str:
    ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    r0, r2 = res["R0_max_reward"], res["R2_max_cluster_visit"]
    lines = [
        "# Selector R6a / R7 — 498 merged replay",
        "",
        f"Generated: {ts}",
        "",
        "Input: `v4_final_498q` + ef2 rerun overlay. Oracle-free picks; gold only in eval.",
        "",
        "| Rule | Hit@1 (498) | vs R0 | vs R2 | Saved vs R2 | Hurt vs R2 | Net vs R2 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for rule in RULES4:
        r = res[rule]
        lines.append(
            f"| {rule} | {r['hit1']}/498 ({r['hit1_pct']:.1f}%) | "
            f"{r['hit1']-r0['hit1']:+d} | {r['hit1']-r2['hit1']:+d} | "
            f"{r['saved_vs_r2']} | {r['hurt_vs_r2']} | {r['net_vs_r2']:+d} |"
        )
    lines += ["", "## Paired diff vs R2", ""]
    for rule in ("R6a_s7_fallback_r2", "R7_r2_second_cluster"):
        r = res[rule]
        lines.append(f"### {rule}")
        lines.append(f"- **saved** ({r['saved_vs_r2']}): `{r['saved_qids_vs_r2']}`")
        lines.append(f"- **hurt** ({r['hurt_vs_r2']}): `{r['hurt_qids_vs_r2']}`")
        lines.append("")
    for rule in ("R0_max_reward", "R2_max_cluster_visit"):
        r = res[rule]
        lines.append(f"### {rule} (reference)")
        lines.append(f"- saved vs R0: {r['saved_qids_vs_r0']} | hurt vs R0: {r['hurt_qids_vs_r0']}")
        lines.append("")

    r6, r7 = res["R6a_s7_fallback_r2"], res["R7_r2_second_cluster"]
    lines.append("## Stop / signals")
    if r7["net_vs_r2"] <= -3:
        lines.append(f"- 🛑 **R7 net vs R2 = {r7['net_vs_r2']} (≤ -3)** — R7 太激进")
    for rule, r in [("R6a", r6), ("R7", r7)]:
        if r["net_vs_r2"] >= 5:
            lines.append(f"- ✅ **{rule} net vs R2 = {r['net_vs_r2']} (≥ +5)** — selection 层仍有空间")
    lines.append("")
    best = max([("R6a", r6), ("R7", r7)], key=lambda x: x[1]["net_vs_r2"])
    lines.append("## 决策表")
    lines.append("")
    net = best[1]["net_vs_r2"]
    if net >= 5:
        rec = "落地 R6a/R7 到 sql_selector.py，**不**起 8 卡重跑"
    elif net >= 2:
        rec = "落地 + 8 卡可选跑 calibrated reward"
    elif net >= 0:
        rec = "selection saturate，考虑 reward 公式重跑"
    else:
        rec = "设计错，回看 S7 数据"
    lines.append(f"| 最佳 {best[0]} net vs R2 | **{net:+d}** | {rec} |")
    lines.append("")
    return "\n".join(lines) + "\n"


def render_s7_md(s7_qids: List[str], profiles: Dict[str, dict]) -> str:
    merged = pld.load_merged_498()
    gold, db = pld.load_gold_meta()
    res = replay_with_refs(merged, s7_qids, gold, db)

    ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    lines = [
        "# Selector R6a / R7 — S7 subset (41 q)",
        "",
        f"Generated: {ts}",
        "",
        "S7 qids from `recall_lost_75_taxonomy.json` (primary=S7). Cluster case A/B/C from `s7_cluster_audit.json`.",
        "",
        "| Rule | S7 Hit@1 | vs R2 saved | A (1 cluster) | B (2-3) | C (4+) | S7 hurt vs R2 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for rule in ("R6a_s7_fallback_r2", "R7_r2_second_cluster"):
        r = res[rule]
        saved = set(r["saved_qids_vs_r2"])
        hurt = set(r["hurt_qids_vs_r2"])
        a = sum(1 for q in saved if _case_type(q, profiles) == "A")
        b = sum(1 for q in saved if _case_type(q, profiles) == "B")
        c = sum(1 for q in saved if _case_type(q, profiles) == "C")
        lines.append(
            f"| {rule} | {r['hit1']}/41 | {r['saved_vs_r2']} | {a} | {b} | {c} | {r['hurt_vs_r2']} |"
        )
        if saved:
            lines.append(f"- saved qids: `{sorted(saved, key=int)}`")
        if hurt:
            lines.append(f"- hurt qids: `{sorted(hurt, key=int)}`")
    lines += [
        "",
        f"- R2 on S7 alone: **{res['R2_max_cluster_visit']['hit1']}/41** Hit@1",
        "",
    ]
    return "\n".join(lines) + "\n"


def render_30q_md(a0: dict, a3: dict) -> str:
    ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    lines = [
        "# R6a / R7 — 30q sanity",
        "",
        f"Generated: {ts}",
        "",
        "| Pool | R0 | R2 | R6a | R7 | R6a net vs R0 | R7 net vs R0 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for label, res in [("a0_30", a0), ("a3_30", a3)]:
        lines.append(
            f"| {label} | {res['R0_max_reward']['hit1']}/30 | {res['R2_max_cluster_visit']['hit1']}/30 | "
            f"{res['R6a_s7_fallback_r2']['hit1']}/30 | {res['R7_r2_second_cluster']['hit1']}/30 | "
            f"{res['R6a_s7_fallback_r2']['net_vs_r0']:+d} | {res['R7_r2_second_cluster']['net_vs_r0']:+d} |"
        )
    lines += [
        "",
        "期望对照: a0 R0≈20 R2≈22; a3 R0≈20 R2≈19.",
        "",
    ]
    for label, res in [("a0_30", a0), ("a3_30", a3)]:
        for rule in ("R6a_s7_fallback_r2", "R7_r2_second_cluster"):
            h = res[rule]["hit1"]
            if h <= 18:
                lines.append(f"- 🛑 **{label} {rule} Hit@1={h}/30 ≤ 18** — sanity 退化")
    lines.append("")
    return "\n".join(lines) + "\n"


def main() -> None:
    sys.path.insert(0, str(pld.ROOT))
    gold, db = pld.load_gold_meta()
    merged = pld.load_merged_498()
    q498 = sorted(merged.keys(), key=int)

    c498 = json.loads(CACHE498.read_text(encoding="utf-8"))["results_498"]
    r2s = set(c498["R2_max_cluster_visit"]["saved_qids"])
    r2h = set(c498["R2_max_cluster_visit"]["hurt_qids"])
    print("[498] replay (R0 stored, R2 from cache, eval R6a/R7 diffs only) ...", flush=True)
    res498 = replay_with_refs(merged, q498, gold, db, use_stored_r0=True, r2_saved=r2s, r2_hurt=r2h)
    (GAP / "selector_r67_498_merged.md").write_text(render_498_md(res498), encoding="utf-8")
    (GAP / "selector_r67_498_cache.json").write_text(json.dumps(res498, indent=2), encoding="utf-8")

    tax = json.loads((GAP / "recall_lost_75_taxonomy.json").read_text())
    s7_qids = sorted([r["qid"] for r in tax["rows"] if r["primary"] == "S7"], key=int)
    prof = {p["qid"]: p for p in json.loads(S7_JSON.read_text())["profiles"]}

    print(f"[S7] replay {len(s7_qids)} qids ...", flush=True)
    (GAP / "selector_r67_s7_breakdown.md").write_text(render_s7_md(s7_qids, prof), encoding="utf-8")

    print("[30q] a0 + a3 ...", flush=True)
    a0 = pld.load_json(pld.A0_8)
    a3 = pld.load_json(pld.A3_8)
    q30a = sorted(a0.keys(), key=int)
    q30b = sorted(a3.keys(), key=int)
    res_a0 = replay_with_refs(a0, q30a, gold, db)
    res_a3 = replay_with_refs(a3, q30b, gold, db)
    (GAP / "selector_r67_30q_sanity.md").write_text(render_30q_md(res_a0, res_a3), encoding="utf-8")

    print((GAP / "selector_r67_498_merged.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
