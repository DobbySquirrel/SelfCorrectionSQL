#!/usr/bin/env python3
"""Compare 30-question metrics vs 498-question reference (Task 3)."""

import argparse
import json
from collections import defaultdict
from pathlib import Path


def metrics(data: dict) -> dict:
    rollout_multi = 0
    rollout_total = 0
    q_any_multi = 0
    depths = []
    cte_depth_multi = {1: 0, 2: 0, 3: 0}
    n_q = 0

    for _qid, rec in data.items():
        if not isinstance(rec, dict):
            continue
        n_q += 1
        rollouts = rec.get("rollout_stats") or []
        has_multi = False
        ctes_by_d = defaultdict(set)
        for r in rollouts:
            rollout_total += 1
            if len(r.get("result_buckets") or {}) >= 2:
                rollout_multi += 1
                has_multi = True
            depths.append(len(r.get("cte_path") or []))
            for i, cte in enumerate(r.get("cte_path") or []):
                ctes_by_d[i].add(cte.strip())
        if rollouts and has_multi:
            q_any_multi += 1
        for d in (0, 1, 2):
            if len(ctes_by_d.get(d, set())) >= 2:
                cte_depth_multi[d + 1] += 1

    depths_s = sorted(depths) if depths else [0]
    median_depth = depths_s[len(depths_s) // 2] if depths_s else 0

    return {
        "n_questions": n_q,
        "q_any_rollout_multi_bucket_pct": 100.0 * q_any_multi / max(1, n_q),
        "final_sql_multi_bucket_rollout_pct": 100.0 * rollout_multi / max(1, rollout_total),
        "depth1_gte2_distinct_cte_pct": 100.0 * cte_depth_multi[1] / max(1, n_q),
        "depth2_gte2_distinct_cte_pct": 100.0 * cte_depth_multi[2] / max(1, n_q),
        "depth3_gte2_distinct_cte_pct": 100.0 * cte_depth_multi[3] / max(1, n_q),
        "median_cte_path_depth": median_depth,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input_30", type=Path, required=True)
    parser.add_argument("--input_498", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    m30 = metrics(json.loads(args.input_30.read_text(encoding="utf-8")))
    m498 = metrics(json.loads(args.input_498.read_text(encoding="utf-8")))

    checks = [
        ("q_any_rollout_multi_bucket_pct", 35.1, 10),
        ("depth1_gte2_distinct_cte_pct", 68.5, 15),
        ("depth2_gte2_distinct_cte_pct", 58.4, 15),
        ("final_sql_multi_bucket_rollout_pct", 9.85, 5),
    ]
    lines = ["# Sanity 30 vs 498\n", "| Metric | 30q | 498q ref | Δpp | Pass |", "|---|---:|---:|---:|:---:|"]
    all_pass = True
    for key, ref, tol in checks:
        v30, v498 = m30[key], m498[key]
        delta = abs(v30 - v498)
        ok = delta <= tol
        all_pass = all_pass and ok
        lines.append(f"| {key} | {v30:.1f}% | {v498:.1f}% | {delta:.1f} | {'OK' if ok else 'FAIL'} |")
    med = m30["median_cte_path_depth"]
    ok_med = med in (1, 2, 3)
    all_pass = all_pass and ok_med
    lines.append(f"| median_cte_path_depth | {med} | 2 | — | {'OK' if ok_med else 'FAIL'} |")
    lines.append(f"\n**Overall:** {'PASS' if all_pass else 'FAIL — stop'}\n")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines), encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
