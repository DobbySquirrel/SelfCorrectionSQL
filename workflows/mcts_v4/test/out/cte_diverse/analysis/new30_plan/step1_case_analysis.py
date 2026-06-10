#!/usr/bin/env python3
"""Step 1: D hurt + B saved cluster case analysis (E0 vs E1, no GPU)."""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import metrics as met  # noqa: E402

OUT = Path(__file__).resolve().parents[2]
PLAN = Path(__file__).resolve().parent
E0_CACHE = OUT / "v4_diverse_b2_n3_sv5_498q_coder_rollouts12"
E1 = OUT / "v4_plan_e1_new30_coder_rollouts12.json"

D_HURT = ["901", "948"]
B_SAVED = ["424", "758", "915", "1029", "1235"]


def load_b2(qid: str) -> dict:
    for i in range(4):
        p = OUT / f"v4_diverse_b2_n3_sv5_498q_coder_rollouts12_w{i}.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        if qid in data:
            return data[qid]
    return {}


def cluster_table(rec: dict, rss_key: str = "rollout_stats") -> List[dict]:
    rss = rec.get(rss_key) or rec.get("rollout_stats") or []
    gs = met.gold_sigs({**rec, "rollout_stats": rss})
    ranked = met.r3_ranked_clusters({**rec, "rollout_stats": rss})
    rows = []
    for rank, (sig, cl) in enumerate(ranked[:12], start=1):
        rows.append({
            "rank": rank,
            "sig": sig[:16],
            "visit": cl.total_visit,
            "count": cl.total_count,
            "max_reward": round(cl.max_rollout_reward, 3),
            "r3_score": round(cl.max_rollout_reward * max(1, cl.total_count), 3),
            "has_gold": sig in gs,
            "n_variants": len(cl.variants),
        })
    return rows


def per_plan_gold(rec: dict) -> Dict[str, dict]:
    rss = rec.get("rollout_stats") or []
    by_plan: Dict[str, list] = defaultdict(list)
    for rs in rss:
        by_plan[rs.get("plan_id") or "?"].append(rs)
    out = {}
    for pid, stats in sorted(by_plan.items()):
        fake = {**rec, "rollout_stats": stats}
        gs = met.gold_sigs(fake)
        ranked = met.r3_ranked_clusters(fake)
        gold_rank = None
        for i, (sig, _) in enumerate(ranked, start=1):
            if sig in gs:
                gold_rank = i
                break
        out[pid] = {
            "n_rollouts": len(stats),
            "recall": bool(gs),
            "gold_rank_r3": gold_rank,
            "top1_has_gold": bool(ranked and ranked[0][0] in gs),
            "top1_visit": ranked[0][1].total_visit if ranked else 0,
            "gold_visit": next(
                (cl.total_visit for sig, cl in ranked if sig in gs), 0
            ),
        }
    return out


def analyze_qid(qid: str, e1: dict) -> dict:
    e0 = load_b2(qid)
    e1r = e1[qid]
    e0_ev = met.eval_record(e0)
    e1_ev = met.eval_record({**e1r, "rollout_stats": e1r.get("rollout_stats") or []})

    saved_by_plans = []
    pp = met.per_plan_gold if hasattr(met, "per_plan_gold") else per_plan_gold
    plan_info = per_plan_gold(e1r)
    for pid, info in plan_info.items():
        if info["recall"]:
            saved_by_plans.append(pid)

    mechanism = "unknown"
    if e1_ev["recall"] and not e0_ev["recall"]:
        mechanism = "search_gain"
    elif e1_ev["hit1_r3"] and not e0_ev["hit1_r3"]:
        if e0_ev["recall"]:
            if len(saved_by_plans) >= 2:
                mechanism = "support_aggregation_multi_plan"
            elif len(saved_by_plans) == 1:
                mechanism = "structural_single_plan"
            else:
                mechanism = "selector_rank_shift"
    elif e0_ev["recall"] and not e1_ev["recall"]:
        if e0_ev["gold_cluster_rank"] == 1 and (e1_ev["gold_cluster_rank"] or 99) > 1:
            mechanism = "consensus_dilution"
        else:
            mechanism = "gold_lost_from_pool"

    return {
        "qid": qid,
        "e0": e0_ev,
        "e1": e1_ev,
        "e0_clusters": cluster_table(e0),
        "e1_clusters_union": cluster_table(e1r),
        "e1_per_plan": plan_info,
        "plans_with_gold_recall": saved_by_plans,
        "mechanism_hypothesis": mechanism,
    }


def main() -> None:
    e1 = json.loads(E1.read_text(encoding="utf-8"))
    cases = {}
    for qid in D_HURT + B_SAVED:
        if qid in e1:
            cases[qid] = analyze_qid(qid, e1)

    out_json = PLAN / "step1_case_analysis.json"
    out_json.write_text(json.dumps(cases, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        "# Step 1: D hurt + B saved case analysis",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
    ]
    for label, qids in [("D hurt", D_HURT), ("B saved (Hit@1)", B_SAVED)]:
        lines.append(f"## {label}")
        lines.append("")
        for qid in qids:
            c = cases.get(qid)
            if not c:
                continue
            lines.append(f"### qid={qid} — {c['mechanism_hypothesis']}")
            lines.append("")
            lines.append(
                f"- E0: recall={c['e0']['recall']} hit_r3={c['e0']['hit1_r3']} "
                f"gold_rank={c['e0']['gold_cluster_rank']}"
            )
            lines.append(
                f"- E1: recall={c['e1']['recall']} hit_r3={c['e1']['hit1_r3']} "
                f"gold_rank={c['e1']['gold_cluster_rank']}"
            )
            lines.append(f"- Plans with gold in pool: {c['plans_with_gold_recall']}")
            lines.append("")
            lines.append("**E0 top clusters (R3 rank)**")
            lines.append("")
            lines.append("| rank | sig | visit | count | r3_score | gold? |")
            lines.append("|---:|---|---:|---:|---:|:---:|")
            for r in c["e0_clusters"][:6]:
                lines.append(
                    f"| {r['rank']} | {r['sig']} | {r['visit']} | {r['count']} | "
                    f"{r['r3_score']} | {int(r['has_gold'])} |"
                )
            lines.append("")
            lines.append("**E1 union top clusters**")
            lines.append("")
            lines.append("| rank | sig | visit | count | r3_score | gold? |")
            lines.append("|---:|---|---:|---:|---:|:---:|")
            for r in c["e1_clusters_union"][:6]:
                lines.append(
                    f"| {r['rank']} | {r['sig']} | {r['visit']} | {r['count']} | "
                    f"{r['r3_score']} | {int(r['has_gold'])} |"
                )
            lines.append("")
            lines.append("**E1 per-plan gold**")
            lines.append("")
            for pid, info in c["e1_per_plan"].items():
                lines.append(
                    f"- `{pid}`: recall={info['recall']} gold_rank={info['gold_rank_r3']} "
                    f"top1_gold={info['top1_has_gold']} gold_visit={info['gold_visit']}"
                )
            lines.append("")

    (PLAN / "step1_case_analysis.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {PLAN / 'step1_case_analysis.md'}")


if __name__ == "__main__":
    main()
