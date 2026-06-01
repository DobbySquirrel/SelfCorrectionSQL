#!/usr/bin/env python3
"""A2a: single-expansion sibling bucket distribution from cte_buckets_per_node."""

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path


def iter_expansions(data: dict):
    for qid, rec in data.items():
        for r in rec.get("rollout_stats") or []:
            for step in r.get("cte_buckets_per_node") or []:
                if not isinstance(step, dict):
                    continue
                buckets = step.get("buckets") or []
                yield qid, step.get("depth", 0), step.get("expansion_step_id", ""), buckets


def bucket_count_by_hash(buckets, key: str) -> int:
    seen = set()
    for b in buckets:
        sig = b.get(key) or ""
        seen.add(sig)
    return len(seen)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    data = json.loads(args.input.read_text(encoding="utf-8"))
    hist_legacy = Counter()
    hist_v2 = Counter()
    by_depth_legacy = defaultdict(lambda: {"total": 0, "multi": 0})
    by_depth_v2 = defaultdict(lambda: {"total": 0, "multi": 0})
    per_q_multi = Counter()

    for qid, depth, _eid, buckets in iter_expansions(data):
        if not buckets:
            continue
        nl = bucket_count_by_hash(buckets, "result_signature_legacy")
        nv = bucket_count_by_hash(buckets, "result_signature_v2")
        hist_legacy[nl] += 1
        hist_v2[nv] += 1
        by_depth_legacy[depth]["total"] += 1
        by_depth_v2[depth]["total"] += 1
        if nl >= 2:
            by_depth_legacy[depth]["multi"] += 1
            per_q_multi[qid] += 1
        if nv >= 2:
            by_depth_v2[depth]["multi"] += 1

    lines = ["# A2a Expansion bucket stats\n", "## len(buckets) histogram (legacy distinct sig per step)"]
    total = sum(hist_legacy.values())
    for k in sorted(hist_legacy):
        lines.append(f"- {k} buckets: {hist_legacy[k]} ({100*hist_legacy[k]/max(1,total):.1f}%)")
    lines.append("\n## len(buckets) histogram (v2 distinct sig per step)")
    total_v = sum(hist_v2.values())
    for k in sorted(hist_v2):
        lines.append(f"- {k} buckets: {hist_v2[k]} ({100*hist_v2[k]/max(1,total_v):.1f}%)")

    lines.append("\n## P(len>=2) by depth (legacy)")
    for d in sorted(by_depth_legacy):
        t = by_depth_legacy[d]["total"]
        m = by_depth_legacy[d]["multi"]
        lines.append(f"- depth={d}: {m}/{t} = {100*m/max(1,t):.1f}%")
    lines.append("\n## P(len>=2) by depth (v2)")
    for d in sorted(by_depth_v2):
        t = by_depth_v2[d]["total"]
        m = by_depth_v2[d]["multi"]
        lines.append(f"- depth={d}: {m}/{t} = {100*m/max(1,t):.1f}%")

    nq = len(data)
    avg_multi = sum(per_q_multi.values()) / max(1, nq)
    lines.append(f"\n## Per-question: mean multi-bucket expansion steps (legacy) = {avg_multi:.2f}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {args.output} ({total} expansion steps)")


if __name__ == "__main__":
    main()
