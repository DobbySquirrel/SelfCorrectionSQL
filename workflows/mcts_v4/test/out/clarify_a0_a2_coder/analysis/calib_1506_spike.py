#!/usr/bin/env python3
"""5-min spike: why qid 1506 hurt under calibrated reward (vs a3 R2)."""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "workflows/mcts_v4/test/out/clarify_a0_a2_coder/analysis/parallel_during_rerun"))

import selector_replay as sr  # noqa: E402
from workflows.mcts_v4.utils.mcts_helpers import MCTSUtils  # noqa: E402

QID = "1506"
OUT = Path(__file__).resolve().parent
A3 = OUT.parent / "v4_a3_30q_coder_rollouts8.json"
CALIB = OUT.parent / "v4_calib_30q_coder_rollouts8.json"


def _clusters_md(rss: list, title: str) -> list[str]:
    lines = [f"### {title}", ""]
    clusters = sr.build_clusters(rss)
    lines.append("| sig (12) | total_count | total_visit | max_rollout_r | variants |")
    lines.append("|---|---:|---:|---:|---:|")
    for sig, c in sorted(clusters.items(), key=lambda x: -x[1].total_visit):
        lines.append(
            f"| `{sig[:12]}…` | {c.total_count} | {c.total_visit} | "
            f"{c.max_rollout_reward:.4f} | {len(c.variants)} |"
        )
    lines.append("")
    lines.append("**Per-rollout**")
    lines.append("")
    lines.append("| rid | reward | leaf_visit | #buckets | bucket counts | R2 pick sig |")
    lines.append("|---:|---:|---:|---:|---|---|")
    for r in rss:
        rb = r.get("result_buckets") or {}
        n_b = len(rb)
        legacy = MCTSUtils.calculate_consistency_reward_legacy(rb, 8)
        cal = MCTSUtils.calculate_consistency_reward_calibrated(rb, 8)
        top = max(rb.values()) if rb else 0
        lines.append(
            f"| {r.get('rollout_id','?')} | {r.get('reward',0):.4f} | "
            f"{r.get('leaf_visit_count',0)} | {n_b} | top={top}/8 "
            f"legacy_r={legacy:.4f} cal_r={cal:.4f} |"
        )
    lines.append("")
    r2_sql = sr.pick_r2(clusters)
    r0_sql = sr.pick_r0(rss)
    lines.append(f"- R2 SQL (first 120 chars): `{r2_sql[:120]}…`")
    lines.append(f"- R0 SQL (first 120 chars): `{r0_sql[:120]}…`")
    lines.append("")
    return lines


def main():
    a3 = json.loads(A3.read_text(encoding="utf-8"))
    cal = json.loads(CALIB.read_text(encoding="utf-8"))
    ra, rc = a3.get(QID, {}), cal.get(QID, {})
    rss_a = ra.get("rollout_stats") or []
    rss_c = rc.get("rollout_stats") or []

    lines = [
        "# Calibrated hurt spike — qid 1506",
        "",
        f"Generated: {datetime.now().isoformat(timespec='seconds')}",
        "",
        "Stage 1 hurt vs **a3 R2** (v2 hash, same config as calibrated).",
        "",
        f"- a3 `optimal_sql` / R2 replay: `{sr.pick_r2(sr.build_clusters(rss_a))[:80]}…`",
        f"- calib `optimal_sql`: `{(rc.get('optimal_sql') or '')[:80]}…`",
        "",
    ]
    lines += _clusters_md(rss_a, "a3 R2 baseline (frozen rollout_stats)")
    lines += _clusters_md(rss_c, "calibrated run (new search + rewards)")

    # Hypothesis tag
    hurt = True
    a3_all_1 = all(abs(float(r.get("reward", 0)) - 1.0) < 1e-3 for r in rss_a if r.get("result_buckets"))
    cal_multi_low = any(
        len(r.get("result_buckets") or {}) >= 2
        and float(r.get("reward", 0)) < 0.99
        for r in rss_c
    )
    lines += ["## Interpretation (5 min)", ""]
    if a3_all_1 and cal_multi_low:
        lines.append(
            "- a3: multi-bucket rollouts still **reward=1.0** (legacy formula). "
            "Calib: same diversity → **reward < 1** → UCB/backprop favors other branches."
        )
        lines.append(
            "- Likely mechanism: **calibration lowered consistency signal**, not R2 selector alone."
        )
    else:
        lines.append("- Mixed pattern — inspect per-rollout table above.")
    lines.append("")
    lines.append("Does not block Stage 2 (S7 recall-lost pool).")

    out = OUT / "calib_1506_dump.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
