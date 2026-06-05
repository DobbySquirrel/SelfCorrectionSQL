#!/usr/bin/env python3
"""D1.5: non-oracle R2 top1 cluster signals on S7 41 (static)."""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Set, Tuple

ROOT = Path(__file__).resolve().parents[6]
PAR = ROOT / "workflows/mcts_v4/test/out/clarify_a0_a2_coder/analysis/parallel_during_rerun"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(PAR))

import selector_replay as sr  # noqa: E402
from selector_replay import build_clusters, _high_reward_count  # noqa: E402

import _loaders as pld  # noqa: E402

CALIB = ROOT / "workflows/mcts_v4/test/out/clarify_a0_a2_coder/v4_calib_s7_41_coder_rollouts8.json"
D2_JSON = Path(__file__).resolve().parent / "d2_s7_conditional_r7.json"
OUT_MD = Path(__file__).resolve().parent / "d1_5_s7_top1_signals.md"
OUT_JSON = Path(__file__).resolve().parent / "d1_5_s7_top1_signals.json"

SAVED = {"201", "685", "1238", "1490"}
HURT = {"1505"}
RECALL7 = SAVED | HURT | {"263", "1486"}


def norm_sql_struct(sql: str) -> str:
    """Crude structure key: strip literals/whitespace for G3 proxy."""
    s = (sql or "").lower()
    s = re.sub(r"'[^']*'", "'?'", s)
    s = re.sub(r"\b\d+\b", "?", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:500]


def top_clusters(rss: List[dict]) -> Tuple[str, sr.Cluster, str, sr.Cluster]:
    c = build_clusters(rss)
    if not c:
        return "", sr.Cluster(""), "", sr.Cluster("")
    ranked = sorted(c.items(), key=lambda x: (-x[1].total_visit, -x[1].total_count))
    s1, c1 = ranked[0]
    s2, c2 = (ranked[1] if len(ranked) > 1 else ("", sr.Cluster("")))
    return s1, c1, s2, c2


def rollouts_touching_sig(rss: List[dict], sig: str) -> int:
    n = 0
    for r in rss:
        if sig in (r.get("result_buckets") or {}):
            n += 1
    return n


def struct_count(cluster: sr.Cluster) -> int:
    keys = {norm_sql_struct(sql) for sql, _, _ in cluster.variants if sql}
    return len(keys)


def label_qid(qid: str, d2: dict) -> str:
    if qid in HURT:
        return "hurt_1505"
    if qid in SAVED:
        return "saved_r7"
    if qid in {"263", "1486"}:
        return "recall_no_r7"
    row = next((r for r in d2.get("rows", []) if r["qid"] == qid), {})
    if row.get("trigger") and not row.get("gold_in_pool"):
        return "trigger_no_gold"
    if row.get("trigger"):
        return "trigger_other"
    return "no_trigger"


def main():
    calib = json.loads(CALIB.read_text(encoding="utf-8"))
    d2 = json.loads(D2_JSON.read_text(encoding="utf-8")) if D2_JSON.is_file() else {"rows": []}
    prof = {
        str(p["qid"]): p["case"]
        for p in json.loads(
            (PAR / "recall_gap_analysis/s7_cluster_audit.json").read_text(encoding="utf-8")
        )["profiles"]
    }
    s7 = sorted(prof.keys(), key=int)
    rows = []

    for qid in s7:
        rss = (calib.get(qid) or {}).get("rollout_stats") or []
        s1, c1, s2, c2 = top_clusters(rss)
        v1, v2 = c1.total_visit, c2.total_visit if s2 else 0
        ratio = (v1 / v2) if v2 > 0 else float("inf")
        n_roll = rollouts_touching_sig(rss, s1) if s1 else 0
        n_struct = struct_count(c1) if s1 else 0
        n_var = len(c1.variants) if s1 else 0
        trig = _high_reward_count(rss) >= 6 and len(build_clusters(rss)) >= 2

        rows.append(
            {
                "qid": qid,
                "case": prof[qid],
                "group": label_qid(qid, d2),
                "trigger": trig,
                "n_clusters": len(build_clusters(rss)),
                "top1_visit": v1,
                "top2_visit": v2,
                "visit_ratio": round(ratio, 2) if ratio != float("inf") else None,
                "top1_rollouts_touching": n_roll,
                "top1_sql_variants": n_var,
                "top1_struct_keys": n_struct,
                "top1_size": c1.total_count,
                "top1_max_r": round(c1.max_rollout_reward, 3),
            }
        )

    def tbl(qids: List[str], title: str, lines: List[str]):
        lines += [f"### {title}", ""]
        lines.append(
            "| qid | case | top1_visit | top2_visit | ratio | rollouts@top1 | "
            "variants | struct_keys | top1_size |"
        )
        lines.append("|---|:---|---:|---:|---:|---:|---:|---:|---:|")
        for r in rows:
            if r["qid"] not in qids:
                continue
            ratio_s = (
                f"{r['visit_ratio']:.1f}"
                if r["visit_ratio"] is not None
                else "∞"
            )
            lines.append(
                f"| {r['qid']} | {r['case']} | {r['top1_visit']} | {r['top2_visit']} | "
                f"{ratio_s} | {r['top1_rollouts_touching']} | {r['top1_sql_variants']} | "
                f"{r['top1_struct_keys']} | {r['top1_size']} |"
            )
        lines.append("")

    def stats(qids: List[str]) -> dict:
        sub = [r for r in rows if r["qid"] in qids]
        if not sub:
            return {}
        import statistics as st

        def col(k):
            return [r[k] for r in sub]

        return {
            "n": len(sub),
            "top1_visit": f"{min(col('top1_visit'))}-{max(col('top1_visit'))} med={st.median(col('top1_visit'))}",
            "visit_ratio": f"{min(r['visit_ratio'] or 999 for r in sub):.1f}-{max(r['visit_ratio'] or 999 for r in sub):.1f}",
            "rollouts@top1": f"{min(col('top1_rollouts_touching'))}-{max(col('top1_rollouts_touching'))}",
            "struct_keys": f"{min(col('top1_struct_keys'))}-{max(col('top1_struct_keys'))}",
        }

    groups = {
        "hurt_1505": [HURT],
        "saved_r7 (+4)": [SAVED],
        "recall_no_r7 (263,1486)": [{"263", "1486"}],
        "trigger_no_gold (21)": [{r["qid"] for r in rows if r["group"] == "trigger_no_gold"}],
    }

    lines = [
        "# D1.5 — S7 41: R2 top1 cluster 可观测信号（non-oracle）",
        "",
        f"Generated: {datetime.now().isoformat(timespec='seconds')}",
        "",
        "目的：为 **G1/G2/G3 guard** 提供静态证据，**不用 gold**。",
        "",
        "R2 top1 = `total_visit` 最大簇。",
        "",
        "- **rollouts@top1**：含该 sig 的 rollout 数（result_buckets）",
        "- **struct_keys**：top1 簇内 SQL 去字面/空白后的结构种类数（G3 代理）",
        "",
        "---",
        "",
        "## 分组摘要",
        "",
        "| 组 | n | top1_visit | visit ratio | rollouts@top1 | struct_keys |",
        "|---|---:|---|---|---|---|",
    ]
    for name, qset_list in groups.items():
        qids = list(qset_list[0])
        st = stats(qids)
        if st:
            lines.append(
                f"| {name} | {st['n']} | {st['top1_visit']} | {st['visit_ratio']} | "
                f"{st['rollouts@top1']} | {st['struct_keys']} |"
            )

    lines += ["", "---", ""]

    tbl(list(HURT), "hurt — 1505（R2✓ R7✗）", lines)
    tbl(sorted(SAVED, key=int), "saved — R7 救回 4 题", lines)
    tbl(["263", "1486"], "recall 在池但 R7 未中", lines)

    no_gold = sorted([r["qid"] for r in rows if r["group"] == "trigger_no_gold"], key=int)
    if len(no_gold) <= 12:
        tbl(no_gold, f"trigger 无 gold（{len(no_gold)}）", lines)
    else:
        lines += [f"### trigger 无 gold（{len(no_gold)}，仅摘要）", ""]
        st = stats(no_gold)
        lines.append(f"- visit med 见上表；qids: `{', '.join(no_gold[:8])}…`")
        lines.append("")

    # Guard feasibility
    lines += [
        "## Guard 可行性（静态）",
        "",
        "### G1: top1 `rollouts@top1` ≥ 6 → 保 R2",
        "",
    ]
    g1_1505 = rows[[r["qid"] for r in rows].index("1505")]["top1_rollouts_touching"]
    g1_saved = [r["top1_rollouts_touching"] for r in rows if r["qid"] in SAVED]
    g1_nogold = [r["top1_rollouts_touching"] for r in rows if r["group"] == "trigger_no_gold"]
    lines.append(f"- 1505: rollouts@top1 = **{g1_1505}**")
    lines.append(f"- saved 4: {g1_saved}")
    lines.append(f"- trigger_no_gold: min={min(g1_nogold) if g1_nogold else '—'} max={max(g1_nogold) if g1_nogold else '—'}")
    sep_g1 = g1_1505 >= 6 and all(x < 6 for x in g1_saved)
    lines.append(f"- **能否分开 1505 vs saved4（阈值6）**: {'❌ 否' if not sep_g1 else '✅ 可能'}")
    lines.append("")
    lines += [
        "### G2: top1/top2 visit ratio > θ → 保 R2",
        "",
    ]
    r1505 = next(r for r in rows if r["qid"] == "1505")
    lines.append(
        f"- 1505: ratio = {r1505['top1_visit']}/{r1505['top2_visit']} = "
        f"{r1505['visit_ratio']}"
    )
    for q in sorted(SAVED, key=int):
        r = next(x for x in rows if x["qid"] == q)
        lines.append(f"- {q}: ratio = {r['visit_ratio']} (top2 visit={r['top2_visit']})")
    lines.append("- **1490** ratio≈10 → **G2 会保 R2 且阻止 R7 救回**（你已预警）")
    lines.append("- **G2 不适合**作为统一 guard")
    lines.append("")
    lines += [
        "### G3: top1 `struct_keys` == 1 → 保 R2（簇内 SQL 结构一致）",
        "",
    ]
    for r in rows:
        if r["qid"] in HURT | SAVED | {"263", "1486"}:
            lines.append(
                f"- {r['qid']} ({r['group']}): struct_keys={r['top1_struct_keys']}, "
                f"variants={r['top1_sql_variants']}"
            )
    sk_hurt = r1505["top1_struct_keys"]
    sk_saved = [r["top1_struct_keys"] for r in rows if r["qid"] in SAVED]
    lines.append("")
    if sk_hurt == 1 and all(s > 1 for s in sk_saved):
        lines.append("✅ **G3 可能分开**：1505 top1 结构一致，saved 4  top1 多结构。")
    elif sk_hurt == 1 and any(s == 1 for s in sk_saved):
        lines.append("⚠️ **G3 部分重叠**：部分 saved 题 top1 也单一结构。")
    else:
        lines.append("❌ **G3 不能干净分开** 1505 vs saved4。")

    lines += [
        "",
        "## D1.5 结论 → 是否跑 D2b",
        "",
    ]
    # Decision logic
    if sk_hurt == 1 and all(s > 1 for s in sk_saved):
        lines.append(
            "1. **优先验证 G3**（non-oracle）：top1 簇内 `struct_keys==1` 时保 R2，否则在触发条件下 R7。"
        )
        lines.append("2. **D2b 可跑**：在 498 cache 上 replay **G3+R7**，不是 oracle guard。")
    else:
        lines.append(
            "1. **1505 与 saved4 在 G1/G2/G3 上未干净分离** → conditional R7 工程价值打折。"
        )
        lines.append("2. **暂缓 D2b 全 498**；优先 clarification-constraint 或其它 visit 外信号。")
    lines.append("3. 不重跑 MCTS；不做 75 题 trigger 重叠率（优先级低）。")
    lines.append("")

    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    OUT_JSON.write_text(json.dumps({"rows": rows}, indent=2), encoding="utf-8")
    print(OUT_MD)
    print(f"1505 rollouts@top1={g1_1505} struct={sk_hurt} saved_struct={sk_saved}")


if __name__ == "__main__":
    main()
