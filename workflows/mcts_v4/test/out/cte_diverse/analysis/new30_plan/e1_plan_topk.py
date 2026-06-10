#!/usr/bin/env python3
"""Stage 3: top-k offline diagnostics for E0 vs E1."""

from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent))
import metrics as met  # noqa: E402

PLAN_DIR = Path(__file__).resolve().parent
OUT = Path(__file__).resolve().parents[2]


def load_merged(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    data = {}
    base = path.stem
    for i in range(4):
        p = path.parent / f"{base}_w{i}.json"
        if p.exists():
            data.update(json.loads(p.read_text(encoding="utf-8")))
    return data


def eval_e1_record(rec: Dict[str, Any]) -> Dict[str, Any]:
    """E1: union pool primary; also best-plan-only R3."""
    union_key = "union_rollout_stats"
    rss_union = rec.get(union_key) or rec.get("rollout_stats") or []
    fake = {**rec, "rollout_stats": rss_union}
    ev_union = met.eval_record(fake)

    # best plan: pick plan with highest mean rollout reward
    per_plan = rec.get("per_plan_rollout_stats") or {}
    best_pid = None
    best_rw = -1.0
    for pid, stats in per_plan.items():
        if not stats:
            continue
        mr = sum(float(s.get("reward") or 0) for s in stats) / len(stats)
        if mr > best_rw:
            best_rw = mr
            best_pid = pid
    ev_plan = ev_union
    if best_pid and per_plan.get(best_pid):
        fake2 = {**rec, "rollout_stats": per_plan[best_pid]}
        ev_plan = met.eval_record(fake2)

    return {
        **ev_union,
        "hit1_r3_union": ev_union["hit1_r3"],
        "hit1_r3_best_plan": ev_plan["hit1_r3"],
        "best_plan_id": best_pid,
        "plan_dedup_count": rec.get("plan_dedup_count"),
        "runtime_s": (rec.get("stats") or {}).get("timing", {}).get("total_s"),
    }


def delta(e1_val: int, e0_val: int) -> str:
    d = e1_val - e0_val
    return f"{d:+d}" if d else "0"


def main() -> None:
    manifest = json.loads((PLAN_DIR / "manifest.json").read_text(encoding="utf-8"))
    e0 = json.loads((PLAN_DIR / "e0_bprime_baseline.json").read_text(encoding="utf-8"))
    e1_path = OUT / "v4_plan_e1_new30_coder_rollouts12.json"
    e1 = load_merged(e1_path)
    if not e1:
        print(f"E1 not found at {e1_path} — run E1 first")
        return

    rows = []
    by_bucket_e1: Dict[str, List] = {}
    dedup_counts = []
    for row in manifest["questions"]:
        qid = row["qid"]
        bucket = row["bucket"]
        e0q = e0["per_question"][qid]
        if qid not in e1:
            continue
        e1q = eval_e1_record(e1[qid])
        dedup_counts.append(e1[qid].get("plan_dedup_count") or 0)
        r = {
            "qid": qid,
            "bucket": bucket,
            "e0": e0q,
            "e1": e1q,
            "d_recall": int(e1q["recall"]) - int(e0q["recall"]),
            "d_hit1": int(e1q["hit1_r3_union"]) - int(e0q["hit1_r3"]),
            "d_hit8": int(e1q["hit8"]) - int(e0q["hit8"]),
        }
        rows.append(r)
        by_bucket_e1.setdefault(bucket, []).append(r)

    def sum_metric(key, src="e1"):
        return sum(int(r[src][key.split("_")[-1] if False else key]) for r in rows)

    # fix aggregation
    def agg_rows(rs, prefix):
        n = len(rs)
        return {
            "n": n,
            "recall": sum(int(r[prefix]["recall"]) for r in rs),
            "hit1_r3": sum(int(r[prefix]["hit1_r3"] if prefix == "e0" else r[prefix]["hit1_r3_union"]) for r in rs),
            "hit8": sum(int(r[prefix]["hit8"]) for r in rs),
        }

    o0 = agg_rows(rows, "e0")
    o1 = agg_rows(rows, "e1")

    rank_dist_e0 = Counter(r["e0"]["gold_rank_bucket"] for r in rows)
    rank_dist_e1 = Counter(met.gold_rank_bucket(met.gold_cluster_rank(
        {**e1[r["qid"]], "rollout_stats": e1[r["qid"]].get("union_rollout_stats") or e1[r["qid"]].get("rollout_stats")}
    )) for r in rows)

    out = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "n": len(rows),
        "e0_overall": o0,
        "e1_overall": o1,
        "delta": {
            "recall": o1["recall"] - o0["recall"],
            "hit1_r3": o1["hit1_r3"] - o0["hit1_r3"],
            "hit8": o1["hit8"] - o0["hit8"],
        },
        "plan_dedup": {
            "mean": sum(dedup_counts) / len(dedup_counts) if dedup_counts else 0,
            "dist": dict(Counter(dedup_counts)),
        },
        "gold_rank_dist_e0": dict(rank_dist_e0),
        "gold_rank_dist_e1": dict(rank_dist_e1),
        "per_question": rows,
    }
    (PLAN_DIR / "e1_plan_topk.json").write_text(json.dumps(out, indent=2), encoding="utf-8")

    md = [
        "# E1 plan top-k vs E0",
        "",
        f"Generated: {out['generated']}",
        "",
        "## Overall",
        "",
        f"| | Recall | Hit@1 R3 | Hit@8 |",
        f"|---|---:|---:|---:|",
        f"| E0 B′ | {o0['recall']}/{o0['n']} | {o0['hit1_r3']}/{o0['n']} | {o0['hit8']}/{o0['n']} |",
        f"| E1 plan | {o1['recall']}/{o1['n']} | {o1['hit1_r3']}/{o1['n']} | {o1['hit8']}/{o1['n']} |",
        f"| Δ | {out['delta']['recall']:+d} | {out['delta']['hit1_r3']:+d} | {out['delta']['hit8']:+d} |",
        "",
        f"Plan dedup mean: {out['plan_dedup']['mean']:.2f} dist: {out['plan_dedup']['dist']}",
        "",
        "## By bucket (Δ Hit@1 / Δ Recall)",
        "",
    ]
    for b, rs in sorted(by_bucket_e1.items()):
        dr = sum(r["d_recall"] for r in rs)
        dh = sum(r["d_hit1"] for r in rs)
        md.append(f"- **{b}**: Δrecall={dr:+d}, Δhit@1={dh:+d} ({len(rs)}q)")
    (PLAN_DIR / "e1_plan_topk.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    # decision gateway
    dr, dh, dh8 = out["delta"]["recall"], out["delta"]["hit1_r3"], out["delta"]["hit8"]
    rt_e0 = e0["overall"]["mean_runtime_s"]
    rt_e1 = sum(r["e1"].get("runtime_s") or 0 for r in rows) / max(len(rows), 1)
    rt_ratio = rt_e1 / rt_e0 if rt_e0 else 0

    def gate(val, pass_th, marginal_th, higher_better=True):
        if higher_better:
            if val >= pass_th:
                return "PASS"
            if val >= marginal_th:
                return "MARGINAL"
            return "FAIL"
        if val <= pass_th:
            return "PASS"
        if val <= marginal_th:
            return "MARGINAL"
        return "FAIL"

    gates = {
        "recall": gate(dr, 3, 1),
        "hit1": gate(dh, 2, 1),
        "hit8": gate(dh8, 4, 2) if dh >= 0 else "FAIL",
        "runtime": gate(rt_ratio, 1.5, 2.0, higher_better=False),
    }
    fails = [k for k, v in gates.items() if v == "FAIL"]
    passes = [k for k, v in gates.items() if v == "PASS"]
    decision = "WAIT_USER" if passes and not fails else ("MARGINAL_STOP" if not fails else "FAIL_STOP")

    dmd = [
        "# Decision summary (new30 plan)",
        "",
        f"Generated: {out['generated']}",
        "",
        "| Dimension | Δ / ratio | Gate |",
        "|---|---|:---:|",
        f"| Recall | {dr:+d} | {gates['recall']} |",
        f"| Hit@1 R3 | {dh:+d} | {gates['hit1']} |",
        f"| Hit@8 | {dh8:+d} | {gates['hit8']} |",
        f"| Runtime ratio | {rt_ratio:.2f}x | {gates['runtime']} |",
        "",
        f"**Decision**: `{decision}`",
        "",
        "Bucket A recoveries (E0 miss → E1 recall):",
    ]
    a_rec = [r["qid"] for r in rows if r["bucket"] == "A_search_miss_recoverable"
             and not r["e0"]["recall"] and r["e1"]["recall"]]
    dmd.append(f"- {a_rec or 'none'}")
    (PLAN_DIR / "decision_summary.md").write_text("\n".join(dmd) + "\n", encoding="utf-8")
    print("Stage 3 OK", out["delta"], gates, decision)


if __name__ == "__main__":
    main()
