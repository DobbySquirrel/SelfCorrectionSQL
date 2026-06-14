#!/usr/bin/env python3
"""Build 30-q manifest: Alpha recall ahead of min2 (recall growth headroom)."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[7]
OUT = ROOT / "workflows/mcts_v4/test/out/cte_diverse"
PLAN = OUT / "analysis/colbind_v2_56q"


def recall(rec: dict) -> bool:
    return any(a.get("is_correct") for a in (rec.get("all_sqls_with_attributes") or []))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=30)
    ap.add_argument("-o", type=Path, default=PLAN / "qids_alpha_min2_recall_gap30_manifest.json")
    args = ap.parse_args()

    alpha_pq = json.loads((ROOT / "results/arcwise_eval_result.json").read_text())["per_question"]
    global_d = json.loads((OUT / "v4_colbind_v2_dual03_global_filter_498q_rollouts12.json").read_text())
    min2_d = json.loads((OUT / "v4_colbind_v2_dual03_min2sq_full498_rollouts12.json").read_text())

    scored: list[tuple[int, int, dict]] = []
    for q in sorted(global_d.keys(), key=int):
        qi = int(q)
        aq = alpha_pq.get(str(qi)) or {}
        alpha_any = bool(aq.get("any_ok"))
        alpha_h1 = bool(aq.get("hit1_ok"))
        g_rec = recall(global_d[q])
        m_rec = recall(min2_d[q])
        m_acc = bool((min2_d[q].get("stats") or {}).get("gold_match"))
        score = 0
        reasons = []
        if alpha_any and not m_rec:
            score += 100
            reasons.append("alpha_recall_min2_miss")
        if g_rec and not m_rec:
            score += 50
            reasons.append("global_recall_min2_miss")
        if alpha_h1 and not m_acc:
            score += 30
            reasons.append("alpha_hit1_min2_miss")
        if alpha_any and m_rec and not m_acc:
            score += 10
            reasons.append("alpha_ahead_selection")
        if score > 0:
            scored.append((score, qi, {"reasons": reasons, "alpha_any": alpha_any, "min2_recall": m_rec}))

    scored.sort(key=lambda x: (-x[0], x[1]))
    picked = scored[: args.n]
    qids = [qi for _, qi, _ in picked]

    payload = {
        "created": datetime.now(timezone.utc).isoformat(),
        "description": "30q cohort: Alpha/global recall ahead of min2 (headroom for recall patches)",
        "qids": qids,
        "meta": {str(qi): meta for _, qi, meta in picked},
        "counts": {
            "alpha_recall_min2_miss": sum(1 for _, qi, m in picked if "alpha_recall_min2_miss" in m["reasons"]),
            "global_recall_min2_miss": sum(1 for _, qi, m in picked if "global_recall_min2_miss" in m["reasons"]),
        },
    }
    args.o.parent.mkdir(parents=True, exist_ok=True)
    args.o.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {args.o} n={len(qids)}")
    print(json.dumps(payload["counts"], indent=2))


if __name__ == "__main__":
    main()
